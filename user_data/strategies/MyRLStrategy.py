"""FreqAI reinforcement-learning strategy using Stable-Baselines3 DQN for Binance USDT-M 5m data."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import ClassVar, Dict, Optional

import ccxt
import numpy as np
import pandas as pd
from pandas import DataFrame, Series

from freqtrade.freqai.strategy.freqai_strategy import FreqaiStrategy
from freqtrade.freqai.RL.Base5ActionRLEnv import Actions, Base5ActionRLEnv, Positions

logger = logging.getLogger(__name__)

LOOKBACK_CANDLES = 96
CHURN_WINDOW_STEPS = 6
FEE_RATE = 0.0007
TURNOVER_PENALTY = 0.1
CHURN_PENALTY = 0.01
DRAWDOWN_FACTOR = 0.05
FLOAT_EPS = 1e-9


@dataclass
class MarketMetrics:
    """Container holding derivative metrics aligned to OHLCV timestamps."""

    oi: DataFrame
    taker: DataFrame


class MyFiveActionEnv(Base5ActionRLEnv):
    """Custom reward environment aligning actions with risk-aware incentives."""

    def reset_env(
        self,
        df: DataFrame,
        prices: DataFrame,
        window_size: int,
        reward_kwargs: dict,
        starting_point: bool = True,
    ) -> None:
        super().reset_env(df, prices, window_size, reward_kwargs, starting_point)
        # Cache reward configuration (with safe defaults) for runtime control via config.json
        cfg = reward_kwargs or {}
        self._reward_cfg = cfg
        self._equity: float = 1.0
        self._equity_peak: float = 1.0
        self._prev_drawdown: float = 0.0
        self._last_direction: int = 0
        self._last_direction_tick: int = -1
        self._prev_action: int = Actions.Neutral.value
        # Tunable weights and penalties
        self._fee_rate: float = float(cfg.get("fee_rate", FEE_RATE))
        self._turnover_penalty: float = float(cfg.get("turnover_penalty", TURNOVER_PENALTY))
        self._churn_penalty: float = float(cfg.get("churn_penalty", CHURN_PENALTY))
        self._drawdown_factor: float = float(cfg.get("drawdown_factor", DRAWDOWN_FACTOR))
        self._churn_window: int = int(cfg.get("churn_window_steps", CHURN_WINDOW_STEPS))
        self._vol_alpha: float = float(cfg.get("vol_alpha", 10.0))
        self._vol_beta: float = float(cfg.get("vol_beta", 1.0))
        self._w_taker: float = float(cfg.get("taker_weight", 0.6))
        self._w_oi: float = float(cfg.get("oi_weight", 0.4))
        self._reward_clip: float = float(cfg.get("reward_clip", 5.0))
        self._max_trade_duration: int = int(cfg.get("max_trade_duration_candles", 0))

    def calculate_reward(self, action: int) -> float:  # noqa: PLR0915
        if not self._is_valid(action):
            self.tensorboard_log("invalid_action")
            self._prev_action = action
            return -TURNOVER_PENALTY

        current_tick = self._current_tick
        price_now = float(self.prices.iloc[current_tick].open)
        price_prev = float(self.prices.iloc[current_tick - 1].open) if current_tick > 0 else price_now
        raw_return = 0.0
        if price_prev > 0:
            raw_return = (price_now - price_prev) / price_prev

        position_multiplier = 0.0
        if self._position == Positions.Long:
            position_multiplier = 1.0
        elif self._position == Positions.Short:
            position_multiplier = -1.0

        step_return = position_multiplier * raw_return

        row_idx = min(current_tick, len(self.signal_features) - 1)
        row = self.signal_features.iloc[row_idx]
        vol_feature = float(row.get("%-volatility_14", 0.0))
        denom = abs(vol_feature) + FLOAT_EPS
        risk_adjusted_return = step_return / denom if step_return else 0.0
        # Additional volatility damping to reduce choppy-period over-rewarding
        vol_damp = 1.0 / pow(1.0 + self._vol_alpha * max(vol_feature, 0.0), self._vol_beta)
        risk_adjusted_return *= vol_damp
        risk_adjusted_return = float(np.clip(risk_adjusted_return, -5.0, 5.0))

        self._equity = float(max(FLOAT_EPS, self._equity * (1.0 + step_return)))
        if self._equity > self._equity_peak:
            self._equity_peak = self._equity
        drawdown = 0.0
        if self._equity_peak > 0:
            drawdown = (self._equity_peak - self._equity) / self._equity_peak
        incremental_drawdown = max(0.0, drawdown - self._prev_drawdown)
        self._prev_drawdown = drawdown
        drawdown_penalty = incremental_drawdown * self._drawdown_factor

        fee_penalty = 0.0
        if action in (
            Actions.Long_enter.value,
            Actions.Short_enter.value,
            Actions.Long_exit.value,
            Actions.Short_exit.value,
        ):
            fee_penalty = self._fee_rate

        turnover_penalty = 0.0
        if action in (
            Actions.Long_enter.value,
            Actions.Short_enter.value,
            Actions.Long_exit.value,
            Actions.Short_exit.value,
        ):
            turnover_penalty = self._turnover_penalty

        churn_penalty = 0.0
        if action in (Actions.Long_enter.value, Actions.Short_enter.value):
            direction = 1 if action == Actions.Long_enter.value else -1
            if self._last_direction and direction != self._last_direction:
                if self._last_direction_tick >= 0 and (
                    current_tick - self._last_direction_tick
                ) <= self._churn_window:
                    churn_penalty = self._churn_penalty
            self._last_direction = direction
            self._last_direction_tick = current_tick
        elif action in (Actions.Long_exit.value, Actions.Short_exit.value):
            self._last_direction_tick = current_tick

        # Sentiment bonus from OI / taker flow alignment
        taker_ratio = float(row.get("%-taker_buy_ratio", 0.5))  # [0,1], 0.5 neutral
        oi_change = float(row.get("%-oi_pct_change", 0.0))
        sentiment_bonus = 0.0
        if self._position == Positions.Long:
            pos_bonus = self._w_taker * max(taker_ratio - 0.5, 0.0) + self._w_oi * max(oi_change, 0.0)
            sentiment_bonus = float(np.clip(pos_bonus, 0.0, 1.0))
        elif self._position == Positions.Short:
            pos_bonus = self._w_taker * max(0.5 - taker_ratio, 0.0) + self._w_oi * max(-oi_change, 0.0)
            sentiment_bonus = float(np.clip(pos_bonus, 0.0, 1.0))

        reward = (
            risk_adjusted_return
            - fee_penalty
            - turnover_penalty
            - drawdown_penalty
            - churn_penalty
            + sentiment_bonus
        )

        # Safeguards: penalize excessive trade duration and drawdown breaches
        max_trade_duration = self._max_trade_duration
        if max_trade_duration > 0 and self.get_trade_duration() > max_trade_duration:
            reward -= 1.0
        if drawdown > float(self.max_drawdown):
            reward -= 2.0

        self._prev_action = action
        return float(np.clip(reward, -self._reward_clip, self._reward_clip))


class MyRLStrategy(FreqaiStrategy):
    """RL-driven FreqAI strategy using Stable-Baselines3 DQN on Binance USDT-M futures."""

    timeframe = "5m"
    process_only_new_candles = True
    can_short = True
    startup_candle_count = LOOKBACK_CANDLES + 64

    minimal_roi = {"0": 10}
    stoploss = -0.99
    use_exit_signal = True

    _ccxt_exchange: ClassVar[Optional[ccxt.binanceusdm]] = None
    _metric_cache: ClassVar[Dict[str, tuple[float, MarketMetrics]]] = {}
    _cache_ttl: ClassVar[int] = 300

    @classmethod
    def get_env_cls(cls) -> type[Base5ActionRLEnv]:
        return MyFiveActionEnv

    @classmethod
    def _get_exchange(cls) -> ccxt.binanceusdm:
        if cls._ccxt_exchange is None:
            cls._ccxt_exchange = ccxt.binanceusdm({"enableRateLimit": True})
            try:
                cls._ccxt_exchange.load_markets()
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to load markets: %s", exc)
        return cls._ccxt_exchange

    @classmethod
    def _cache_key(cls, pair: str) -> str:
        return f"{pair}|{cls.timeframe}"

    @classmethod
    def _get_cached_metrics(cls, pair: str) -> Optional[MarketMetrics]:
        key = cls._cache_key(pair)
        cached = cls._metric_cache.get(key)
        if not cached:
            return None
        ts, metrics = cached
        if time.time() - ts > cls._cache_ttl:
            return None
        return metrics

    @classmethod
    def _store_metrics(cls, pair: str, metrics: MarketMetrics) -> None:
        cls._metric_cache[cls._cache_key(pair)] = (time.time(), metrics)

    @classmethod
    def _fetch_derivative_metrics(cls, pair: str, length: int) -> MarketMetrics:
        cached = cls._get_cached_metrics(pair)
        if cached is not None:
            return cached

        exchange = cls._get_exchange()
        market = exchange.market(pair)
        symbol_id = market.get("id", pair.replace("/", ""))
        limit = min(1000, max(length + 10, LOOKBACK_CANDLES + 50))

        oi_df = pd.DataFrame(
            {
                "timestamp": pd.Series(dtype=np.int64),
                "open_interest": pd.Series(dtype=np.float64),
            }
        )
        try:
            oi_history = exchange.fetch_open_interest_history(
                symbol_id, timeframe=cls.timeframe, limit=limit
            )
            if oi_history:
                oi_df = pd.DataFrame(oi_history)
                oi_df = oi_df.rename(columns={"openInterest": "open_interest"})
                oi_df = oi_df[["timestamp", "open_interest"]]
                oi_df.sort_values("timestamp", inplace=True)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to fetch open interest for %s: %s", pair, exc)

        taker_df = pd.DataFrame(
            {
                "timestamp": pd.Series(dtype=np.int64),
                "taker_buy": pd.Series(dtype=np.float64),
                "taker_sell": pd.Series(dtype=np.float64),
            }
        )
        taker_endpoint = None
        for attr in (
            "public_get_futures_data_takerlongshortratio",
            "public_get_fapi_v1_takerlongshortratio",
        ):
            if hasattr(exchange, attr):
                taker_endpoint = getattr(exchange, attr)
                break
        if taker_endpoint is not None:
            try:
                raw = taker_endpoint(
                    {
                        "symbol": symbol_id,
                        "interval": cls.timeframe,
                        "limit": limit,
                        "type": "Futures",
                    }
                )
                if raw:
                    taker_df = pd.DataFrame(raw)
                    taker_df = taker_df.rename(columns={"buyVol": "taker_buy", "sellVol": "taker_sell"})
                    keep_cols = [c for c in ("timestamp", "taker_buy", "taker_sell") if c in taker_df.columns]
                    taker_df = taker_df[keep_cols]
                    taker_df.sort_values("timestamp", inplace=True)
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to fetch taker volumes for %s: %s", pair, exc)

        metrics = MarketMetrics(oi=oi_df, taker=taker_df)
        cls._store_metrics(pair, metrics)
        return metrics

    @staticmethod
    def _zscore(series: Series, window: int) -> Series:
        rolling_mean = series.rolling(window).mean()
        rolling_std = series.rolling(window).std().replace(0, np.nan)
        z = (series - rolling_mean) / (rolling_std + FLOAT_EPS)
        return z.fillna(0.0)

    @staticmethod
    def _compute_rsi(close: Series, length: int = 14) -> Series:
        delta = close.diff().fillna(0.0)
        gain = delta.clip(lower=0.0).ewm(alpha=1 / length, adjust=False).mean()
        loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / length, adjust=False).mean()
        rs = gain / (loss + FLOAT_EPS)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)

    def feature_engineering_standard(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        if dataframe.empty:
            return dataframe

        df = dataframe.copy()
        df.sort_values("date", inplace=True)
        df["return_1"] = df["close"].pct_change().fillna(0.0)
        df["log_return_1"] = np.log(df["close"].replace(0, np.nan)).diff().replace([np.inf, -np.inf], 0.0).fillna(0.0)
        df["volatility_14"] = df["return_1"].rolling(14).std().fillna(method="bfill").fillna(0.0)
        df["rsi_14"] = self._compute_rsi(df["close"], 14)
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_20_delta"] = (df["ema_20"] / df["close"]) - 1.0
        df["ema_50_delta"] = (df["ema_50"] / df["close"]) - 1.0
        df["spread_proxy"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
        df["zscore_close"] = self._zscore(df["close"], LOOKBACK_CANDLES)

        metrics = self._fetch_derivative_metrics(metadata["pair"], len(df))
        base = pd.DataFrame({"timestamp": (df["date"].astype("int64") // 1_000_000).astype(np.int64)})

        combined = base.sort_values("timestamp").copy()
        if not metrics.oi.empty:
            oi_df = metrics.oi.drop_duplicates("timestamp")
            combined = pd.merge_asof(
                combined,
                oi_df.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )
        else:
            combined["open_interest"] = 0.0

        if not metrics.taker.empty:
            taker_df = metrics.taker.drop_duplicates("timestamp")
            combined = pd.merge_asof(
                combined,
                taker_df.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )
        else:
            combined["taker_buy"] = 0.0
            combined["taker_sell"] = 0.0

        combined.fillna(method="ffill", inplace=True)
        combined.fillna(0.0, inplace=True)

        df["open_interest"] = combined["open_interest"].clip(lower=0.0)
        df["oi_pct_change"] = df["open_interest"].pct_change().replace([np.inf, -np.inf], 0.0).fillna(0.0)
        df["oi_zscore"] = self._zscore(df["open_interest"], LOOKBACK_CANDLES)

        df["taker_buy_vol"] = combined["taker_buy"].clip(lower=0.0)
        df["taker_sell_vol"] = combined["taker_sell"].clip(lower=0.0)
        total_taker = (df["taker_buy_vol"] + df["taker_sell_vol"]).replace(0.0, np.nan)
        df["taker_buy_ratio"] = (df["taker_buy_vol"] / total_taker).fillna(0.5)
        df["taker_buy_ratio_roll"] = df["taker_buy_ratio"].rolling(12).mean().fillna(method="bfill").fillna(0.5)

        df["spread_proxy"] = df["spread_proxy"].replace([np.inf, -np.inf], 0.0).fillna(0.0)

        feature_map = {
            "%-return_1": df["return_1"],
            "%-log_return_1": df["log_return_1"],
            "%-volatility_14": df["volatility_14"],
            "%-rsi_14": df["rsi_14"] / 100.0,
            "%-ema_20_delta": df["ema_20_delta"],
            "%-ema_50_delta": df["ema_50_delta"],
            "%-spread_proxy": df["spread_proxy"],
            "%-zscore_close": df["zscore_close"],
            "%-oi_level": df["open_interest"] * 1e-9,
            "%-oi_zscore": df["oi_zscore"],
            "%-oi_pct_change": df["oi_pct_change"],
            "%-taker_buy_vol": df["taker_buy_vol"] * 1e-8,
            "%-taker_sell_vol": df["taker_sell_vol"] * 1e-8,
            "%-taker_buy_ratio": df["taker_buy_ratio"],
            "%-taker_buy_ratio_roll": df["taker_buy_ratio_roll"],
        }
        for col, series in feature_map.items():
            dataframe[col] = series.astype(np.float32)

        cleanup_cols = [
            "return_1",
            "log_return_1",
            "volatility_14",
            "rsi_14",
            "ema_20",
            "ema_50",
            "ema_20_delta",
            "ema_50_delta",
            "spread_proxy",
            "zscore_close",
            "open_interest",
            "oi_pct_change",
            "oi_zscore",
            "taker_buy_vol",
            "taker_sell_vol",
            "taker_buy_ratio",
            "taker_buy_ratio_roll",
        ]
        dataframe.drop(columns=[c for c in cleanup_cols if c in dataframe.columns], inplace=True, errors="ignore")

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    @staticmethod
    def _extract_actions(dataframe: DataFrame) -> Series:
        for column in ("rl_action", "predict", "rl_agent_action"):
            if column in dataframe.columns:
                return dataframe[column].fillna(0).astype(int)
        return pd.Series(data=0, index=dataframe.index, dtype=int)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        actions = self._extract_actions(dataframe)
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0

        dataframe.loc[actions == Actions.Long_enter.value, "enter_long"] = 1
        dataframe.loc[actions == Actions.Short_enter.value, "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        actions = self._extract_actions(dataframe)
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0

        dataframe.loc[actions == Actions.Long_exit.value, "exit_long"] = 1
        dataframe.loc[actions == Actions.Short_exit.value, "exit_short"] = 1
        return dataframe


__all__ = ["MyRLStrategy", "MyFiveActionEnv"]
