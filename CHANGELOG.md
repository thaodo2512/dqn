# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2024-07-19
### Added
- Implemented `MyRLStrategy` with custom DQN reward environment and derivatives-aware feature set for FreqAI.
- Added FreqAI configuration, Jetson-ready Docker assets, pair discovery utility, and project README for RL workflows.

## [0.2.1] - 2024-07-19
### Fixed
- Updated Jetson container base image to the published `nvcr.io/nvidia/l4t-ml:r35.2.1-py3` tag and removed the legacy compose version field to unblock Docker builds.

## [0.2.2] - 2024-07-19
### Changed
- Redesigned DQN reward: volatility damping and OI/taker sentiment bonus integrated into `MyFiveActionEnv.calculate_reward()`; weights now configurable via `freqai.rl_config.reward_kwargs`.

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
