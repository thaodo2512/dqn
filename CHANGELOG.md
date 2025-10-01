# Changelog

All notable changes to this project will be documented in this file.

## [0.2.16] - 2025-10-01
### Changed
- Default training `TIMERANGE` now starts at `20240101` (Jan 1, 2024) in the training
  compose and docs, aligning with requested backtest start and avoiding 2023 spans.

## [0.2.17] - 2025-10-01
### Added
- Auto multi-core threading launcher: `scripts/launch_with_all_cores.py` detects
  available CPUs (respecting cgroups/affinity) and sets common thread env vars
  (OMP/MKL/OPENBLAS/NumExpr/Torch) before running training or dry-run.
- Docker image now includes `scripts/` and both compose services run via the
  launcher (`--mode train` / `--mode trade`).
### Docs
- Updated README and training docs to mention the new launcher and core auto-detect.

## [0.2.18] - 2025-10-01
### Added
- CPU-only docker-compose variants:
  - `docker/docker-compose.train.cpu.yml` for training/backtesting
  - `docker/docker-compose.cpu.yml` for dry-run trading
  Both force `device: cpu` while the launcher auto-configures thread env vars to
  utilize all available CPU cores.
### Changed
- Launcher now honors `FORCE_CPU=1` or `FREQAI_DEVICE=cpu`, generating a small
  `user_data/cpu-device.json` override and setting `CUDA_VISIBLE_DEVICES=""`.

## [0.2.19] - 2025-10-01
### Added
- x86/general CPU support:
  - `docker/Dockerfile.cpu` (Python 3.11 slim, TA-Lib, Freqtrade 2025.6, SB3, PyTorch CPU)
  - `docker/docker-compose.train.cpu.x86.yml` (training/backtesting)
  - `docker/docker-compose.cpu.x86.yml` (dry-run trading)
### Docs
- Updated `README_RL.md` and `docs/freqai-training.md` with x86 CPU-only commands.

## [0.2.20] - 2025-10-01
### Added
- RL reward overhaul in `MyFiveActionEnv`:
  - Reward = delta PnL when in position; 0 when flat
  - Apply fees only on entry/exit; sliding-window churn and per-trade drawdown penalties
  - Reward component debug logging behind `reward_kwargs.debug_log` / `reward_debug`
  - ATR feature added; `startup_candle_count` increased to 200
- Multi-container trainer: `scripts/train_pairs.py`
  - Bounded parallel per-pair containers; auto-detect CPUs → threads/concurrency
  - Per-container overlays for CPU device, identifier, single-pair whitelist
  - `--reward-debug` flag to enable detailed reward logs
  - `--id-prefix` / `--id-suffix` to version identifiers; `--fresh` to disable restore
  - Prints detected CPUs, chosen threads/concurrency, and pair list
### Changed
- Trainer now forces single-pair runs by overlaying `exchange.pair_whitelist`
- Trainer falls back to `user_data/config.json` when default `user_config/config.json` is missing
### Fixed
- x86 Dockerfile uses `libopenblas-dev` (ATLAS removed in Debian trixie)
- Trainer writes overlay JSON on host (avoids fragile echo quoting) and mounts a fallback `.overlays/` dir if `user_data/` is not writable
- Trainer passes container-visible paths for debug/restore overlays; auto-unique identifier when `--fresh` without suffix
- Removed deprecated `train_one_pair()`; use `launch_one_pair()` only

## [0.2.6] - 2025-09-30
### Fixed
- Set a safer default `TIMERANGE` in the training compose to `20240215-20250930` to
  guarantee FreqAI multi-timeframe warmup coverage with data that starts at
  `2024-01-01`, preventing `all training data dropped due to NaNs`.
- Updated documentation to reflect the new default timerange.
### Changed
- Made `tools/download_data.sh` robust across Freqtrade versions when listing data:
  avoid timeframe filters for `list-data` and prefer `--show-timerange` (singular)
  when available; otherwise fall back to a plain listing.

## [0.2.7] - 2025-09-30
### Added
- Jetson GPU support baked into the Docker image: install `nvidia-jetpack` and NVIDIA
  CUDA-enabled PyTorch wheels for JetPack 6.2.1 (`torch==2.3.0+nv24.04`,
  `torchvision==0.18.0+nv24.04`).
### Changed
- Compose services now explicitly request GPU with `gpus: all` in addition to the
  NVIDIA runtime and device reservations.
- Set Stable-Baselines3 `device: cuda` in `user_data/config.json` so training uses GPU
  when available on Jetson.
- Updated docs to include GPU verification commands and clarifications.

## [0.2.8] - 2025-09-30
### Fixed
- Correct NVIDIA PyTorch index URL for JetPack 6.2: use
  `--extra-index-url https://developer.download.nvidia.com/compute/redist/jp/v62` and
  avoid over-pinning `+nv` suffixes to improve compatibility with available wheels.
- Updated docs to match the correct JetPack index path.

## [0.2.5] - 2025-09-30
### Changed
- Adopted Option A as default: training and trade compose services now run a
  pre-download step (`tools/download_data.sh`) to fetch historical data across
  `5m 15m 1h` before execution, ensuring FreqAI warmup coverage and avoiding NaN
  drops. Default download timerange is `DOWNLOAD_START=20231001` to the end of
  `TIMERANGE` (or today) and can be overridden via environment variables.
- Updated `docker/docker-compose.train.jetson.yml` and `docker/docker-compose.jetson.yml`
  to invoke the pre-download step.
- Documented the new default behavior and overrides in `docs/freqai-training.md` and
  `README_RL.md`.

## [0.2.4] - 2025-09-30
### Changed
- Retargeted the Jetson Docker build to a JetPack 6.2.1 compatible base image, pinned
  pandas-ta `v0.3.14b0` and `freqtrade[all]==2025.6` for Python 3.10 support.
- Documented the workflow for building/importing the JetPack 6.2.1 base image used by
  `docker/Dockerfile.jetson`.
- Added `docker/build_jp621_base.sh` to automate importing the JetPack 6.2.1 rootfs as
  the default Docker base image.
- Parameterized `docker-compose.jetson.yml` to default builds against the JP 6.2.1 base
  image.
- Cleaned duplicate NVIDIA apt sources during Docker builds to avoid Jetson base update
  warnings.
- Removed invalid `<SOC>` entries from the default NVIDIA apt source list prior to
  running package installs in the Jetson Docker image.
- Purged system `python3-sympy`/`python3-mpmath` before pip installs to resolve
  interpreter conflicts during the container build.
- Configured the Jetson docker-compose service to run with the NVIDIA runtime so GPU
  hooks work with `docker compose`.
- Added a dedicated training compose file and documentation for running FreqAI RL
  backtests inside the Jetson container.
- Updated compose commands to exec form to avoid shell parsing issues and removed
  deprecated config keys (`forcebuy_enable`, `protections`).
- Training compose service now supplies a default `--timerange` argument via compose
  variable substitution (override with `TIMERANGE`) and docs explain how to customize it.
- Expanded FreqAI configuration (timeframes, correlated pairs, identifiers, data split
  params) and supplied placeholder API server credentials to satisfy 2025.6 schema
  validation.
- Added placeholder Telegram credentials so schema validation passes when Telegram is
  disabled.
- Futures config cleanup: removed invalid `position_mode` and `pairlist_update_interval`,
  set order-book pricing with `price_side: same`, and fixed stake/dry-run wallet for
  realistic backtests.
- Added top-level `stoploss: -0.10` to align with Freqtrade 2025.6 configuration schema
  and provide a sensible default.
- Added missing `freqai.rl_config.model_reward_parameters` (rr/profit_aim) to satisfy
  ReinforcementLearner requirements during backtesting/training.
 - Switched exchange id to `binanceusdm` for futures backtesting compatibility with
  Freqtrade 2025.6.
 - Added explicit `margin_mode: cross` and `position_mode: oneway` to default futures
  configuration to satisfy Binance USD‑M futures requirements.
- Updated `order_types` schema to the new entry/exit/force/emergency keys required by
  Freqtrade 2025.x.

## [0.2.3] - 2024-07-19
### Fixed
- Updated Jetson Docker image to install `freqtrade[all]==2023.8` (Python 3.8 compatible), compile TA-Lib from source with refreshed `config.guess/config.sub` scripts, and pull `pandas-ta` 0.3.14b from the GitHub source archive for Python 3.8 support.

## [0.2.2] - 2024-07-19
### Changed
- Redesigned DQN reward: volatility damping and OI/taker sentiment bonus integrated into `MyFiveActionEnv.calculate_reward()`; weights now configurable via `freqai.rl_config.reward_kwargs`.

## [0.2.1] - 2024-07-19
### Fixed
- Updated Jetson container base image to the published `nvcr.io/nvidia/l4t-ml:r35.2.1-py3` tag and removed the legacy compose version field to unblock Docker builds.

## [0.2.0] - 2024-07-19
### Added
- Implemented `MyRLStrategy` with custom DQN reward environment and derivatives-aware feature set for FreqAI.
- Added FreqAI configuration, Jetson-ready Docker assets, pair discovery utility, and project README for RL workflows.

## [0.1.2] - 2024-07-19
### Added
- Documented Jetson Orin Nano compatibility requirements for package installation guidance.

## [0.1.1] - 2024-07-19
### Added
- Documented the policy that every change must include a `CHANGELOG.md` entry.

## [0.1.0] - 2024-07-19
### Added
- Initial repository structure guidelines in `AGENTS.md`.
- Established changelog following Keep a Changelog format.
