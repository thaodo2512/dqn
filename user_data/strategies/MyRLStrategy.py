import logging
from typing import Any, Dict

import pandas as pd
import pandas_ta as ta
from pandas import DataFrame

# Resolve FreqAI base strategy across versions
try:
    from freqtrade.freqai.strategy.freqai_strategy import FreqaiStrategy
except Exception:
    try:
        from freqtrade.freqai.freqai_strategy import FreqaiStrategy  # type: ignore
    except Exception:
        from freqtrade.strategy import IStrategy as FreqaiStrategy  # type: ignore

# Resolve RL env path across versions
try:
    from freqtrade.freqai.RL.Base5ActionRLEnv import Actions, Base5ActionRLEnv, Positions
except Exception:  # pragma: no cover
    from freqtrade.freqai.rl.Base5ActionRLEnv import Actions, Base5ActionRLEnv, Positions  # type: ignore

logger = logging.getLogger(__name__)


class MyFiveActionEnv(Base5ActionRLEnv):
    """Custom RL environment inheriting from Base5ActionRLEnv.

    Changes vs. base:
    - Reward uses delta PnL during open trades (no per-step fees).
    - Fees applied only on entries/exits.
    - Simple churn window (entry frequency) and per‑trade drawdown penalty.
    """

    # Gym/Gymnasium compatible reset signature varies across versions; accept pass‑through.
    def reset(self, *args, **kwargs):  # type: ignore[override]
        obs = super().reset(*args, **kwargs)
        self._step_idx = 0
        self._prev_position = getattr(self, "_position", Positions.Neutral)
        self._prev_trade_profit = 0.0
        self._trade_peak_profit = 0.0
        self.drawdown = 0.0
        self.trade_entries = []  # list[int] of step indices when entries occurred
        self.trade_count_in_window = 0
        return obs

    def step(self, action: int):  # type: ignore[override]
        # Capture position prior to applying the action
        self._prev_position = getattr(self, "_position", Positions.Neutral)
        self._step_idx = int(getattr(self, "_step_idx", 0)) + 1
        return super().step(action)

    def calculate_reward(self, action: int) -> float:  # noqa: C901
        # Pull reward parameters from config
        rw = self.config["freqai"]["rl_config"]["reward_kwargs"]
        fee_rate = float(rw.get("fee_rate", 0.0007))
        churn_penalty = float(rw.get("churn_penalty", 0.01))
        drawdown_factor = float(rw.get("drawdown_factor", 0.05))
        reward_clip = float(rw.get("reward_clip", 5.0))
        churn_window = int(rw.get("churn_window_steps", 50))

        # Profit ratio from current open trade (unrealized PnL proxy)
        trade_profit = (
            float(self.current_trade.get("profit_ratio", 0.0)) if self.current_trade else 0.0
        )

        # Base reward: delta PnL only when in a position; neutral yields 0
        in_position = getattr(self, "_position", Positions.Neutral) in (
            Positions.Long,
            Positions.Short,
        )
        prev_profit = float(getattr(self, "_prev_trade_profit", 0.0))
        reward = (trade_profit - prev_profit) if in_position else 0.0

        # Apply fees only on transitions based on the position BEFORE the action
        is_entry = action in (Actions.Long_enter.value, Actions.Short_enter.value)
        is_exit = action in (Actions.Long_exit.value, Actions.Short_exit.value)
        prev_pos = getattr(self, "_prev_position", Positions.Neutral)

        if prev_pos == Positions.Neutral and is_entry:
            reward -= fee_rate  # entry fee once
            # Track churn: record this entry at current step, prune outside window
            step_idx = int(getattr(self, "_step_idx", 0))
            entries = list(getattr(self, "trade_entries", []))
            entries.append(step_idx)
            min_step = max(0, step_idx - churn_window)
            entries = [t for t in entries if t >= min_step]
            self.trade_entries = entries
            self.trade_count_in_window = len(entries)
        elif prev_pos in (Positions.Long, Positions.Short) and is_exit:
            reward -= fee_rate  # exit fee once

        # Drawdown penalty within a trade: penalize distance from peak
        if in_position:
            peak = float(getattr(self, "_trade_peak_profit", 0.0))
            peak = max(peak, trade_profit)
            dd = max(0.0, peak - trade_profit)
            self._trade_peak_profit = peak
            self.drawdown = dd
            if dd > 0.05:
                reward -= (drawdown_factor * dd)
        else:
            # Reset per-trade trackers when flat
            self._trade_peak_profit = 0.0
            self.drawdown = 0.0
            self._prev_trade_profit = 0.0

        # Churn penalty when too many entries in the window
        if int(getattr(self, "trade_count_in_window", 0)) > 5:
            reward -= churn_penalty

        # Optional invalid action penalty if helper exists
        try:
            if hasattr(self, "_is_valid") and not self._is_valid(action):  # type: ignore[attr-defined]
                reward = min(reward, -2.0)
        except Exception:
            pass

        # Clip the final reward
        if reward > reward_clip:
            reward = reward_clip
        elif reward < -reward_clip:
            reward = -reward_clip

        # Persist previous profit for delta on next step
        if in_position:
            self._prev_trade_profit = trade_profit

        return float(reward)


class MyRLStrategy(FreqaiStrategy):
    """RL-driven FreqAI strategy using Stable-Baselines3 DQN."""

    timeframe = "5m"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 200

    # Feature engineering over multiple timeframes/periods
    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: Dict, **kwargs
    ) -> DataFrame:
        # Example features: RSI and ATR over a given period using pandas-ta
        dataframe[f"%-rsi_{period}"] = ta.rsi(dataframe["close"], length=period)
        dataframe[f"%-atr_{period}"] = ta.atr(
            high=dataframe["high"], low=dataframe["low"], close=dataframe["close"], length=period
        )
        return dataframe

    # Minimal standard features for RL observations
    def feature_engineering_standard(self, dataframe: DataFrame, **kwargs) -> DataFrame:
        dataframe["%-raw_close"] = dataframe["close"]
        dataframe["%-raw_open"] = dataframe["open"]
        dataframe["%-raw_high"] = dataframe["high"]
        dataframe["%-raw_low"] = dataframe["low"]
        return dataframe

    # Targets are not required for RL; keep a neutral placeholder
    def set_freqai_targets(self, dataframe: DataFrame, **kwargs) -> DataFrame:
        dataframe["&-action"] = 0
        return dataframe

    # Let FreqAI populate features/predictions
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Let FreqAI orchestrate feature generation / predictions
        return self.freqai.start(dataframe, metadata, self)

    # Map predicted actions to entry signals
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        if "&-action" not in df.columns:
            return df
        enter_long = (df.get("do_predict", 1) == 1) & (df["&-action"] == 1)
        enter_short = (df.get("do_predict", 1) == 1) & (df["&-action"] == 3)
        df.loc[enter_long, ["enter_long", "enter_tag"]] = (1, "long")
        df.loc[enter_short, ["enter_short", "enter_tag"]] = (1, "short")
        return df

    # Map predicted actions to exit signals
    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        if "&-action" not in df.columns:
            return df
        exit_long = (df.get("do_predict", 1) == 1) & (df["&-action"] == 2)
        exit_short = (df.get("do_predict", 1) == 1) & (df["&-action"] == 4)
        df.loc[exit_long, "exit_long"] = 1
        df.loc[exit_short, "exit_short"] = 1
        return df


__all__ = ["MyRLStrategy", "MyFiveActionEnv"]
