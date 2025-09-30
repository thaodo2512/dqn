# FreqAI DQN Strategy (5m, Binance USDT-M)

## Files
- `user_data/strategies/MyRLStrategy.py` — RL strategy with engineered derivative features and custom reward env.
- `user_data/config.json` — FreqAI configuration enabling Stable-Baselines3 DQN.
- `tools/pair_discovery.py` — USDT-M whitelist discovery with volume/open-interest filters.
- `docker/` — Jetson-friendly Dockerfile and compose stack.

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "freqtrade[all]" "stable-baselines3[extra]" ccxt pandas numpy
freqtrade create-userdir --userdir user_data
```

## Data
```bash
freqtrade download-data \
  --timeframe 5m \
  --timerange -180d \
  --trading-mode futures \
  --config user_data/config.json
```

Optional: fetch all configured timeframes in one go (5m, 15m, 1h):
```bash
freqtrade download-data \
  --timeframes 5m 15m 1h \
  --timerange -180d \
  --trading-mode futures \
  --config user_data/config.json
```

Docker training defaults to Option A (auto-download with warmup cushion). When using
`docker-compose.train.jetson.yml`, data for `5m 15m 1h` is downloaded for
`20231001-<TIMERANGE end>` automatically via `tools/download_data.sh` before the
backtest runs. Override with `DOWNLOAD_START` / `DOWNLOAD_TIMEFRAMES`.

## Pair Discovery
```bash
python tools/pair_discovery.py \
  --min-quote-vol 2000000 \
  --min-oi 1000000 \
  --top 100 \
  --out user_data/whitelist_usdtm.txt
```
Paste the output into `exchange.pair_whitelist` in `user_data/config.json`.

## Training & Backtesting
```bash
freqtrade backtesting \
  --strategy MyRLStrategy \
  --config user_data/config.json \
  --timeframe 5m \
  --timerange -30d \
  --freqai-backtest-live-models
```
FreqAI retrains each cycle (lookback 96, train window 30 days) and stores RL models under `user_data/freqai`. TensorBoard logs are enabled.

## Dry-Run / Live
```bash
freqtrade trade --config user_data/config.json --strategy MyRLStrategy --dry-run
```
For single-pair manual inference, set `"pair_whitelist": ["BTC/USDT:USDT"]` in the config and rerun the command.

## Docker on Jetson Orin Nano
```bash
cd docker
docker compose -f docker-compose.jetson.yml up --build -d
```
Requires the NVIDIA Container Toolkit; the image auto-detects GPU vs CPU PyTorch. Jetson images ship Python 3.8, so the Dockerfile pins `freqtrade[all]==2023.8` — the newest release compatible with that runtime, compiles TA-Lib from source, and installs `pandas-ta` `0.3.14b` from the GitHub source archive because PyPI wheels now target Python ≥3.12.

GPU usage
- The Jetson image installs JetPack runtime and CUDA-enabled PyTorch; compose requests GPU with `gpus: all`.
- Stable-Baselines3 is configured to `device: cuda` in `user_data/config.json`.
- Verify inside container:
  `docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"`

## Action Mapping & Reward
- `0`: hold
- `1`: enter long → `enter_long = 1`
- `2`: exit long → `exit_long = 1`
- `3`: enter short → `enter_short = 1`
- `4`: exit short → `exit_short = 1`

Reward per step:
`reward = (risk_adjusted_return / (1 + α·volatility)^β) - fee - turnover - drawdown*λ - churn + sentiment_bonus`.
- Fees: 0.0007 per side (configurable).
- Turnover: 0.1 when entering/exiting.
- Drawdown: 0.05 × incremental drawdown from equity peak.
- Churn: 0.01 if reversing within a short window (default 6 candles).
- Sentiment bonus: weighted OI change and taker buy ratio aligned with position.
Weights are configurable via `freqai.rl_config.reward_kwargs` in `user_data/config.json`.

## Feature Set
- OHLCV, simple/log returns, rolling volatility, RSI(14), EMA(20/50) deltas, spread proxy, close z-score.
- Open interest level/z-score/% change (via Binance USDT-M open interest history).
- Taker buy/sell volumes, buy ratio, rolling ratio (per 5m taker data).
- All features cast to `float32` and aligned to OHLCV timestamps (lookback 96).

## Safety
Trading perpetual futures carries liquidation and funding risk. Test thoroughly, use isolated leverage, and monitor exchange/fees alignment before real deployment.
