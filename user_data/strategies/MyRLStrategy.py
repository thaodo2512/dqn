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
    """Custom RL environment inheriting from Base5ActionRLEnv."""

    def calculate_reward(self, action: int) -> float:
        # Pull reward parameters from config
        rw = self.config["freqai"]["rl_config"]["reward_kwargs"]
        fee_rate = rw.get("fee_rate", 0.0007)
        churn_penalty = rw.get("churn_penalty", 0.01)
        drawdown_factor = rw.get("drawdown_factor", 0.05)
        reward_clip = rw.get("reward_clip", 5.0)

        trade_profit = self.current_trade.get("profit_ratio", 0.0) if self.current_trade else 0.0
        reward = trade_profit - (fee_rate * 2.0)

        # Simple churn example (customize with your churn_window_steps if desired)
        if getattr(self, "trade_count_in_window", 0) > 5:
            reward -= churn_penalty

        # Drawdown penalty example
        if getattr(self, "drawdown", 0.0) > 0.05:
            reward -= float(drawdown_factor) * float(self.drawdown)

        if reward > reward_clip:
            reward = reward_clip
        elif reward < -reward_clip:
            reward = -reward_clip
        return float(reward)


class MyRLStrategy(FreqaiStrategy):
    """RL-driven FreqAI strategy using Stable-Baselines3 DQN."""

    timeframe = "5m"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 160

    # Feature engineering over multiple timeframes/periods
    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: Dict, **kwargs
    ) -> DataFrame:
        # Example: RSI over a period using pandas-ta
        dataframe[f"%-rsi_{period}"] = ta.rsi(dataframe["close"], length=period)
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
