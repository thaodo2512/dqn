# Repository Guidelines

## Project Structure & Module Organization
Use this repository to keep Deep Q-Network research assets organized and reproducible. Place runtime code inside `src/dqn/`, grouping logic into subpackages such as `agents/` (policy classes), `networks/` (model definitions), `buffers/` (experience replay), and `envs/` (wrappers). Mirror that structure in `tests/` for parity. Store configuration files in `configs/` with YAML defaults like `configs/cartpole.yaml`. Check experiment notebooks or reports into `docs/` when they explain methodology; generated artefacts, checkpoints, and tensorboard logs belong in `runs/` or `artifacts/` and should remain git-ignored.

## Build, Test, and Development Commands
Create an isolated environment before installing dependencies: `python -m venv .venv && source .venv/bin/activate`. Install requirements with `pip install -r requirements.txt` and keep the file sorted. Run `ruff check src tests` and `black src tests` to enforce formatting, followed by `mypy src` for type safety. Execute unit and integration tests with `pytest` (e.g., `pytest -m "not slow"`). Training scripts under `scripts/` should be runnable via `python scripts/train.py --config configs/cartpole.yaml` and accept `--seed` for repeatability.

## Jetson Orin Nano Compatibility
Assume contributors work on JetPack 5.x (Ubuntu 20.04 aarch64). When suggesting package installs, prefer commands verified on Jetson—for example, use `sudo apt-get install python3-opencv` or `pip install --extra-index-url https://developer.download.nvidia.com/compute/redist/jp/v51 torch==2.1.0` rather than x86-only mirrors. Check that binaries ship `linux_aarch64` wheels or fall back to source builds with documented flags, and call out GPU dependencies that require NVIDIA CUDA libraries bundled with JetPack.

## Coding Style & Naming Conventions
Target Python 3.11 and rely on type hints across the codebase. Modules and functions use `snake_case`, classes use `PascalCase`, and constants use `SCREAMING_SNAKE_CASE`. Keep public APIs documented with doctrings and include inline comments only when business logic is not obvious. Limit lines to 100 characters so `black` and `ruff` stay aligned. Prefer descriptive config filenames such as `double-dqn.yaml`, and prefix experiment folders with `YYYYMMDD_short-description`.

## Testing Guidelines
Pytest is the canonical test runner; mirror each module with a `tests/test_<module>.py` file. Collect shared fixtures in `tests/conftest.py` and seed `numpy`, `random`, and `torch` inside fixtures for determinism. Mark long-running rollouts with `@pytest.mark.slow` and exclude them from CI by default. Aim for at least 80% statement coverage on `src/dqn`, and accompany new agents with smoke tests that execute a truncated training loop to validate loss trends.

## Commit & Pull Request Guidelines
The current history is sparse, so adopt Conventional Commits (e.g., `feat(replay): add prioritized buffer`). Reference related issues within the body (`Fixes #42`) and describe the motivation, approach, and evaluation evidence. Every PR must include the latest `pytest` output and, when training changes occur, a short table or screenshot summarizing reward curves. Keep changes focused (<500 LOC) and request a reviewer familiar with the touched subsystem before merging. Before opening a PR, add a dated entry to `CHANGELOG.md` describing the change set; no commit that impacts code or docs ships without an accompanying changelog note.
