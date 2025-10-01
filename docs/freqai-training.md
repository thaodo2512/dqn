# FreqAI Training Container

Use the dedicated compose file to launch a GPU-enabled container that runs
Freqtrade backtesting with FreqAI enabled. The service reuses the same
JetPack 6.2.1 image and mounts the project `user_data/` and `tools/` folders, so
artifacts (models, logs, plots) are written back to the host.

## Usage
Build and run the training container (defaults to `--timerange 20240101-20250930`):
```bash
docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train
```

By default, the service first downloads historical data for all required timeframes
before backtesting (Option A). It runs `tools/download_data.sh`, which fetches:
- Timeframes: `5m 15m 1h` (override with `DOWNLOAD_TIMEFRAMES`)
- Timerange: `${DOWNLOAD_START}-${TIMERANGE end}`; defaults are `20231001` to the end
  portion of `TIMERANGE` (falling back to today if not set).
- Pairs: union of `exchange.pair_whitelist` and `freqai.feature_parameters.include_corr_pairlist`
  to ensure correlated features have OHLCV coverage.

Override defaults, for example:
```bash
DOWNLOAD_START=20230101 DOWNLOAD_TIMEFRAMES="5m 15m 1h 4h" \
  docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train
```

## GPU on Jetson
The image now installs JetPack runtime libraries and NVIDIA's CUDA-enabled PyTorch
for JetPack 6.2.1. Compose requests the GPU (`gpus: all`) and SB3 is configured to use
`device: cuda` by default. Verify inside the container:
```bash
docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train \
  python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"
```
If this prints `True` for CUDA availability, training runs on GPU. If not, ensure your
base image was built with NVIDIA apt sources (see docs) and your host has the NVIDIA
Container Toolkit configured.

Debugging tips
- The training compose runs with increased verbosity (`-vv`) and writes a logfile to
  `user_data/logs/train-debug.log`. You can tail it while the job runs:
  `tail -f user_data/logs/train-debug.log`.
- To quickly validate strategy import independent of backtesting, run:
  ```bash
  docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train \
    freqtrade test-strategy --config user_data/config.json \
    --strategy-path user_data/strategies --strategy MyRLStrategy \
    --freqaimodel ReinforcementLearner -vv
  ```

The default command executes:
```bash
tools/download_data.sh && \
freqtrade backtesting \
  --config user_data/config.json \
  --strategy MyRLStrategy \
  --freqaimodel ReinforcementLearner \
  --timerange ${TIMERANGE-20240101-20250930}
```

## Customizing the training run
Set `TIMERANGE` before invoking the service (default is `20240101-20250930`, provided via
compose variable substitution). To retrain on a different span:
```bash
TIMERANGE=20240401-20250930 docker compose -f docker/docker-compose.train.jetson.yml \
  run --rm freqai-train
```

To add further CLI flags, override the command while reusing the environment:
```bash
TIMERANGE=20240401-20250930 docker compose -f docker/docker-compose.train.jetson.yml \
  run --rm freqai-train \
  freqtrade backtesting --config user_data/config.json --strategy MyRLStrategy \
  --freqaimodel ReinforcementLearner \
  --timerange ${TIMERANGE-20240101-20250930} --max-open-trades 3
```

All outputs (trained models, TensorBoard logs, etc.) appear under
`user_data/freqaimodels/` and `user_data/logs/` on the host.

## Verify data coverage
After downloads, you can verify that all pairs/timeframes start early enough to cover
your training `TIMERANGE` plus a warmup buffer:
```bash
python tools/check_data_coverage.py \
  --config user_data/config.json \
  --timerange ${TIMERANGE-20240101-20250930} \
  --timeframes 5m 15m 1h \
  --warmup-days ${WARMUP_DAYS-45}
```
The checker uses `freqtrade list-data --show-timerange` under the hood and exits non‑zero
if coverage is insufficient, listing the pairs/timeframes that need earlier data.
