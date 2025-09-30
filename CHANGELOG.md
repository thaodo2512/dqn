# Changelog

All notable changes to this project will be documented in this file.

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
