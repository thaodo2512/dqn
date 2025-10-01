#!/usr/bin/env python3
"""
Launch Freqtrade training or trading while auto-configuring multi-core CPU usage.

This script detects the available logical CPU cores (respecting cgroup/affinity
constraints when possible) and sets common thread-related environment variables
so NumPy/SciPy/BLAS/NumExpr/PyTorch use all available cores. It then runs the
standard workflow commands for either training (backtesting with FreqAI RL) or
dry-run trading.

Usage examples (inside container or repo root):
  python scripts/launch_with_all_cores.py --mode train
  python scripts/launch_with_all_cores.py --mode trade

Respects existing TIMERANGE and other env vars as in docker-compose.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import Optional


def _parse_cpuset(cpuset: str) -> int:
    """Parse a Linux cpuset string like "0-3,6,8-9" into a count of CPUs."""
    count = 0
    for part in cpuset.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            try:
                start = int(start_s)
                end = int(end_s)
            except ValueError:
                continue
            if end >= start:
                count += (end - start + 1)
        else:
            try:
                int(part)
                count += 1
            except ValueError:
                continue
    return count


def detect_logical_cpus() -> int:
    """Detect the number of logical CPUs available to this process.

    Order of preference:
    - sched_getaffinity (Linux) for per-process CPU set
    - cgroup cpuset files (container limits)
    - os.cpu_count() fallback
    """
    # Per-process CPU affinity (best signal under Linux)
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except Exception:
        pass

    # cgroup v1 cpuset
    try:
        with open("/sys/fs/cgroup/cpuset/cpuset.cpus", "r", encoding="utf-8") as fh:
            cs = fh.read().strip()
            n = _parse_cpuset(cs)
            if n > 0:
                return n
    except Exception:
        pass

    # cgroup v2 cpuset
    try:
        with open("/sys/fs/cgroup/cpuset.cpus", "r", encoding="utf-8") as fh:
            cs = fh.read().strip()
            n = _parse_cpuset(cs)
            if n > 0:
                return n
    except Exception:
        pass

    # Fallback
    return max(1, os.cpu_count() or 1)


def set_thread_env_vars(threads: int) -> None:
    """Set common thread environment variables for numerical libs."""
    # Upper bound safeguard
    t = max(1, int(threads))
    os.environ.setdefault("OMP_NUM_THREADS", str(t))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(t))
    os.environ.setdefault("MKL_NUM_THREADS", str(t))
    os.environ.setdefault("BLIS_NUM_THREADS", str(t))
    os.environ.setdefault("NUMEXPR_MAX_THREADS", str(t))
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", str(t))  # no-op on Linux
    os.environ.setdefault("TORCH_NUM_THREADS", str(t))


def run_cmd(cmd: list[str]) -> int:
    proc = subprocess.run(cmd)
    return proc.returncode


def _maybe_cpu_override() -> list[str]:
    """Return extra --config args to force CPU if requested via env.

    Triggers when FREQAI_DEVICE=cpu or FORCE_CPU in {1,true,yes}.
    Creates a small override file under user_data/.
    """
    want_cpu = False
    dev = os.environ.get("FREQAI_DEVICE", "").lower().strip()
    if dev == "cpu":
        want_cpu = True
    if os.environ.get("FORCE_CPU", "").lower() in {"1", "true", "yes"}:
        want_cpu = True
    if not want_cpu:
        return []

    # Hide CUDA devices to force CPU kernels for libs that honor it
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

    override_path = os.path.join("user_data", "cpu-device.json")
    payload = (
        '{"freqai":{"rl_config":{"hyperparams":{"device":"cpu"}}}}\n'
    )
    try:
        with open(override_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception as exc:
        print(f"[launcher] Failed writing CPU override: {exc}")
        return []

    print("[launcher] Forcing SB3 device=cpu via user_data/cpu-device.json and CUDA_VISIBLE_DEVICES=\"\"")
    return ["--config", override_path]


def do_train() -> int:
    # Step 1: ensure data coverage
    rc = run_cmd(["bash", "tools/download_data.sh"])
    if rc != 0:
        return rc
    # Step 2: backtesting with RL training enabled
    timerange = os.environ.get("TIMERANGE", "20240101-20250930")
    cmd = [
        "freqtrade",
        "backtesting",
        "--config",
        "user_data/config.json",
        "--strategy",
        "MyRLStrategy",
        "--freqaimodel",
        "ReinforcementLearner",
        "--strategy-path",
        "user_data/strategies",
        "--timerange",
        timerange,
        "-vv",
        "--logfile",
        "user_data/logs/train-debug.log",
    ] + _maybe_cpu_override()
    return run_cmd(cmd)


def do_trade() -> int:
    # Optional prefetch to avoid NaNs on startup
    rc = run_cmd(["bash", "tools/download_data.sh"])
    if rc != 0:
        return rc
    cmd = [
        "freqtrade",
        "trade",
        "--config",
        "user_data/config.json",
        "--strategy",
        "MyRLStrategy",
        "--dry-run",
    ] + _maybe_cpu_override()
    return run_cmd(cmd)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["train", "trade"],
        default="train",
        help="Workflow: 'train' (backtesting + RL) or 'trade' (dry-run)",
    )
    args = parser.parse_args(argv)

    threads = detect_logical_cpus()
    set_thread_env_vars(threads)
    print(f"[launcher] Detected {threads} logical CPUs; configured thread env vars accordingly.")

    if args.mode == "trade":
        return do_trade()
    return do_train()


if __name__ == "__main__":
    raise SystemExit(main())
