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
`20231001-<TIMERANGE end>` automatically. The container now launches via
`scripts/launch_with_all_cores.py`, which auto-detects available CPU cores and
configures thread env vars (OMP/MKL/OPENBLAS/etc.) so numerical libs use all
cores during feature engineering and training. Override with `DOWNLOAD_START` /
`DOWNLOAD_TIMEFRAMES`.

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

CPU-only compose (x86/general CPU)
- Training (backtesting + RL, CPU only):
  `docker compose -f docker/docker-compose.train.cpu.x86.yml run --rm freqai-train-cpu-x86`
- Dry-run trading (CPU only):
  `docker compose -f docker/docker-compose.cpu.x86.yml up --build -d`

CPU-only compose (Jetson/ARM64)
- Training (backtesting + RL):
  `docker compose -f docker/docker-compose.train.cpu.yml run --rm freqai-train-cpu`
- Dry-run trading:
  `docker compose -f docker/docker-compose.cpu.yml up --build -d`

These CPU variants force SB3 `device: cpu` and the launcher auto-detects all
available cores, setting thread env vars for NumPy/BLAS/NumExpr/Torch to fully
utilize the CPU.

## HTML Reports & Web UI
Generate interactive HTML reports from backtest results or browse via the Web UI.

x86 CPU (reports + Web UI)
- Generate HTML reports (saves to `user_data/plot/`):
  `docker compose -f docker/docker-compose.reports.cpu.x86.yml run --rm freqai-reports-cpu-x86`
- Start Web UI at http://localhost:8080:
  `docker compose -f docker/docker-compose.reports.cpu.x86.yml up -d freqai-webui-cpu-x86`
- Optional overrides:
  - `RESULTS_JSON=user_data/backtest_results/<file>.json`
  - `PAIR=BTC/USDT:USDT`
  - `TIMERANGE=20240101-20250930`
  - `FT_CONFIG=user_data/config.json`

Jetson (reports + Web UI)
- Generate HTML reports:
  `docker compose -f docker/docker-compose.reports.jetson.yml run --rm freqai-reports-jetson`
- Start Web UI at http://localhost:8080:
  `docker compose -f docker/docker-compose.reports.jetson.yml up -d freqai-webui-jetson`

Trade Web UI (inference-only)
- Jetson (GPU):
  `docker compose -f docker/docker-compose.trade-ui.jetson.yml up -d`
- x86 CPU:
  `docker compose -f docker/docker-compose.trade-ui.cpu.x86.yml up -d`
These start dry-run trading with the API server enabled (port 8080) and the
inference-only overlay applied. Ensure models are installed and
`user_data/config.json` has the correct `freqai.identifier`.

Notes
- Make sure you have a backtest results JSON. If not, rerun backtesting with:
  `freqtrade backtesting --config user_data/config.json --strategy-path user_data/strategies --strategy MyRLStrategy --freqaimodel ReinforcementLearner --export-filename user_data/backtest_results/latest.json`
- The Web UI reads credentials from `user_data/config.json` (API server section). The
  `webserver` command runs regardless of the `enabled` flag.

## GCP End-to-End Training
Train on a Google Cloud x86 VM, then copy models back for Jetson inference.

Prerequisites
- `gcloud` CLI authenticated (`gcloud auth login`) and a project selected.
- Run from the repo root.

Create VM, train, fetch artifacts (local), optional upload to GCS
- Minimal:
  `PROJECT_ID="<your-project>" ZONE="asia-south1-c" ./scripts/gcp_e2e_train.sh`
- Tuned threads/concurrency and upload to GCS:
  `PROJECT_ID="<your-project>" ZONE="asia-south1-c" THREADS=2 CONCURRENCY=8 TIMERANGE=20240101-20250930 GCS_BUCKET="gs://your-bucket/dqn-jobs" CLEANUP=true ./scripts/gcp_e2e_train.sh`

Environment options
- `MACHINE_TYPE` (default `c4d-standard-16`), `DISK_SIZE_GB` (default `200`), `DISK_TYPE` (default `pd-ssd`)
- `THREADS`, `CONCURRENCY`, `TIMERANGE`, `ID_PREFIX`, `ID_SUFFIX`, `FRESH`
- `GCS_BUCKET` (optional `gs://bucket/prefix`) to upload artifacts
- `CLEANUP=true|false` to delete the VM after run (default true)

Artifacts
- Saved to `gcp-output/<instance-name>/` locally (e.g., `freqaimodels.tgz`, `freqaimodels/`).
- If `GCS_BUCKET` is set, the same folder uploads to `gs://.../<instance-name>/`.

Jetson inference
- Install latest models into this repo: `scripts/install_gcp_models.sh`
- Install from a specific run and set identifier: `scripts/install_gcp_models.sh --source gcp-output/<instance-name> --identifier <your-id>`
- Sync to Jetson automatically: `scripts/install_gcp_models.sh --jetson-dest user@jetson:/path/to/repo --identifier <your-id>`
- Ensure `user_data/config.json` `freqai.identifier` points to the trained model (the installer can set this).
- Start dry-run/live on Jetson: `docker compose -f docker/docker-compose.jetson.yml up -d`

## Jetson Inference (Saved Models)
Run inference locally on Jetson using models trained in the cloud.

Quick start
- Backtest with saved models (no training):
  `./scripts/run_inference_jetson.sh backtest --timerange 20240801-20240901`
- Dry-run trading with saved models (foreground):
  `./scripts/run_inference_jetson.sh trade`
- Background service (Web/API):
  `./scripts/run_inference_jetson.sh trade --detach`

Requirements
- `user_data/freqaimodels/` present and `user_data/config.json` `freqai.identifier` set to the desired model.
- Overlay `user_data/infer-only.json` (provided) disables RL training loops during inference.

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
