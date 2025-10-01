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

The service now invokes a small launcher that auto-detects CPU cores and
configures thread env vars (OMP/MKL/OPENBLAS/NumExpr/Torch) before running the
workflow:
```bash
python scripts/launch_with_all_cores.py --mode train
```
Internally, this script calls `tools/download_data.sh` and then `freqtrade
backtesting` with the configured `TIMERANGE`.

## CPU-only variants

CPU-only (x86/general CPU):
```bash
# Training/backtesting
docker compose -f docker/docker-compose.train.cpu.x86.yml run --rm freqai-train-cpu-x86

# Dry-run trading
docker compose -f docker/docker-compose.cpu.x86.yml up --build -d
```
These use a Python 3.11 slim base and install CPU-only PyTorch; the launcher
auto-detects CPU cores and sets thread env vars.

CPU-only (Jetson/ARM64):
```bash
# Training/backtesting
docker compose -f docker/docker-compose.train.cpu.yml run --rm freqai-train-cpu

# Dry-run trading
docker compose -f docker/docker-compose.cpu.yml up --build -d
```
These reuse the Jetson Dockerfile but force CPU; useful when running on Jetson
without GPU access.

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

## Cloud → Jetson Workflow (GCP)
Train on an x86 VM in Google Cloud, fetch artifacts locally (or to GCS), and run inference on Jetson.

### Local Orchestrator
Run the end‑to‑end helper from the repo root; it creates a VM, installs Docker, copies the repo, runs training, fetches artifacts, optionally uploads to GCS, and cleans up.

```bash
# Minimal (uses Ubuntu 22.04 LTS image family)
PROJECT_ID="<your-project>" ZONE="asia-south1-c" \
  ./scripts/gcp_e2e_train.sh

# Tuned (16 vCPU VM → threads=2, concurrency=8), with artifact upload to GCS
PROJECT_ID="<your-project>" ZONE="asia-south1-c" \
  THREADS=2 CONCURRENCY=8 TIMERANGE=20240101-20250930 \
  GCS_BUCKET="gs://your-bucket/dqn-jobs" CLEANUP=true \
  ./scripts/gcp_e2e_train.sh
```

Defaults target `c4d-standard-16` and produce local artifacts under
`gcp-output/<instance-name>/` (e.g., `freqaimodels.tgz`, `freqaimodels/`). If `GCS_BUCKET`
is set, the same directory is uploaded to your bucket.

### Remote Training Runner
The orchestrator invokes `scripts/gcp_vm_run.sh` on the VM to build the training image,
run bounded‑parallel training via `scripts/train_pairs.py`, and package artifacts under
`output/` in the repo (includes `freqaimodels/`, `freqaimodels.tgz`, and logs as
`logs/` and `logs.tgz`).

Key knobs (defaults for a 16 vCPU VM):
- `--threads 2` — per‑container BLAS/NumExpr/Torch threads
- `--concurrency 8` — parallel containers (2 × 8 = 16 CPU threads)
- `--timerange` — backtest timerange

You can SSH to the VM and re‑run it manually if needed:
```bash
bash scripts/gcp_vm_run.sh --threads 2 --concurrency 8 --timerange 20240101-20250930
```

### Transfer to Jetson
- Copy models to Jetson: `rsync -av gcp-output/<instance>/freqaimodels/ /path/to/jetson/repo/user_data/freqaimodels/`
- Ensure `user_data/config.json` has `freqai.identifier` matching the model you want to serve and `restore_best_model: true`.

## Jetson Inference with Saved Models
Run inference on Jetson without retraining using the provided overlay and helper script.

- Overlay: `user_data/infer-only.json` sets `train_cycles: 0`, `total_timesteps: 0`.
- Helper: `scripts/run_inference_jetson.sh` wraps compose commands.

Examples
```bash
# Backtest using saved models only (no training)
./scripts/run_inference_jetson.sh backtest --timerange 20240801-20240901

# Dry-run trading (foreground)
./scripts/run_inference_jetson.sh trade

# Background service (Web/API on 8080)
./scripts/run_inference_jetson.sh trade --detach
```

Tips
- Keep `freqtrade` and `stable-baselines3` versions aligned between training VM and Jetson (build both images from this repo).
- For backtests on Jetson, ensure OHLCV coverage with `tools/download_data.sh` or the training compose prefetch.
- To force CPU on Jetson, set `FORCE_CPU=1` (compose env) or override device to `cpu`.

Troubleshooting
- If a backtest starts training, confirm `user_data/infer-only.json` is applied and the `freqai.identifier` matches a model directory under `user_data/freqaimodels/`.
- If plots are empty, regenerate reports via the reports compose or `scripts/report_latest.py` after a backtest.
