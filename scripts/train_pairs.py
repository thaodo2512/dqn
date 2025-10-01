#!/usr/bin/env python3
"""
Launch bounded-parallel training (backtesting with FreqAI RL) — one container per pair —
then remove containers while preserving models/logs (bind-mounted in user_data/).

Pairs are read from a host config JSON (default: user_config/config.json). That config
is bind-mounted read-only into the container and passed to Freqtrade, so the training
respects your external whitelist and settings. Artifacts are written to user_data/.

Examples (run from repo root):
  # Use defaults (auto concurrency ~= cores/threads)
  python scripts/train_pairs.py

  # Explicit config and concurrency
  python scripts/train_pairs.py --config /abs/path/user_config/config.json --concurrency 8 --threads 4

Requirements:
  - Docker and Docker Compose V2 available on the host
  - x86 CPU compose: docker/docker-compose.train.cpu.x86.yml
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List


DEFAULT_COMPOSE = "docker/docker-compose.train.cpu.x86.yml"
DEFAULT_SERVICE = "freqai-train-cpu-x86"


def read_pairs_from_config(config_path: Path) -> List[str]:
    with config_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    wl = cfg.get("exchange", {}).get("pair_whitelist", []) or []
    if not isinstance(wl, list) or not wl:
        raise ValueError(f"No pairs found in {config_path} under exchange.pair_whitelist")
    return [str(p) for p in wl]


def safe_name(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def shell(cmd: List[str]) -> int:
    return subprocess.call(cmd)


def prefetch_data(compose: Path, service: str, host_cfg: Path) -> int:
    cfg_dir = host_cfg.parent.resolve()
    cfg_base = host_cfg.name
    # Run download script using the external config (bind-mounted read-only)
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose),
        "run",
        "--rm",
        "-e",
        f"FT_CONFIG=/freqtrade/user_config/{cfg_base}",
        "-v",
        f"{cfg_dir}:/freqtrade/user_config:ro",
        service,
        "bash",
        "-lc",
        "bash tools/download_data.sh",
    ]
    return shell(cmd)


def train_one_pair(
    compose: Path,
    service: str,
    host_cfg: Path,
    pair: str,
    threads: int,
    timerange: str,
) -> int:
    cfg_dir = host_cfg.parent.resolve()
    cfg_base = host_cfg.name
    sname = safe_name(pair)
    # Build the in-container command: ensure logs dir, write CPU device override and identifier, run backtesting
    inner = (
        "mkdir -p user_data/logs && "
        f"printf '{{\"freqai\":{{\"rl_config\":{{\"hyperparams\":{{\"device\":\"cpu\"}}}}}}}}' > user_data/cpu-device.json && "
        f"printf '{{\"freqai\":{{\"identifier\":\"dqn-{sname}\"}}}}' > user_data/id-{sname}.json && "
        "freqtrade backtesting "
        f"--config /freqtrade/user_config/{shlex.quote(cfg_base)} "
        f"--config user_data/cpu-device.json --config user_data/id-{sname}.json "
        "--strategy-path user_data/strategies --strategy MyRLStrategy "
        "--freqaimodel ReinforcementLearner "
        f"-p {shlex.quote(pair)} "
        f"--timerange {shlex.quote(timerange)} -vv "
        f"--logfile user_data/logs/train-{sname}.log"
    )

    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose),
        "run",
        "--rm",
        "-e",
        f"OMP_NUM_THREADS={threads}",
        "-e",
        f"OPENBLAS_NUM_THREADS={threads}",
        "-e",
        f"MKL_NUM_THREADS={threads}",
        "-e",
        f"NUMEXPR_MAX_THREADS={threads}",
        "-e",
        f"TORCH_NUM_THREADS={threads}",
        "-v",
        f"{cfg_dir}:/freqtrade/user_config:ro",
        service,
        "bash",
        "-lc",
        inner,
    ]
    return shell(cmd)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        default="user_config/config.json",
        help="Host path to config.json containing exchange.pair_whitelist (default: user_config/config.json)",
    )
    p.add_argument(
        "--compose-file",
        default=DEFAULT_COMPOSE,
        help=f"docker compose file to use (default: {DEFAULT_COMPOSE})",
    )
    p.add_argument(
        "--service",
        default=DEFAULT_SERVICE,
        help=f"compose service name (default: {DEFAULT_SERVICE})",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="Max containers in parallel (default: auto ~= cores/threads)",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Threads per container for BLAS/NumExpr/Torch (default: 4)",
    )
    p.add_argument(
        "--timerange",
        default=os.environ.get("TIMERANGE", "20240101-20250930"),
        help="Backtest timerange (default: env TIMERANGE or 20240101-20250930)",
    )
    p.add_argument(
        "--pairs",
        nargs="*",
        help="Optional explicit list of pairs; overrides config whitelist",
    )
    return p.parse_args(list(argv))


def compute_default_concurrency(threads: int) -> int:
    cores = os.cpu_count() or 1
    k = max(1, cores // max(1, threads))
    # Be conservative; cap to 16 by default
    return max(1, min(k, 16))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    compose = Path(args.compose_file)
    if not compose.exists():
        print(f"compose file not found: {compose}", file=sys.stderr)
        return 2

    host_cfg = Path(args.config)
    if not host_cfg.exists():
        print(f"config not found: {host_cfg}", file=sys.stderr)
        return 2

    pairs = args.pairs or read_pairs_from_config(host_cfg)
    if not pairs:
        print("no pairs to train", file=sys.stderr)
        return 2

    conc = args.concurrency or compute_default_concurrency(args.threads)
    print(f"[train_pairs] Using concurrency={conc}, threads/container={args.threads}")
    print(f"[train_pairs] Total pairs: {len(pairs)}")

    # Prefetch OHLCV data using the external config
    print("[train_pairs] Prefetching historical data ...")
    rc = prefetch_data(compose, args.service, host_cfg)
    if rc != 0:
        print(f"[train_pairs] Prefetch failed with code {rc}", file=sys.stderr)
        return rc

    # Fan out containers with bounded parallelism
    results: List[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = {
            ex.submit(
                train_one_pair,
                compose,
                args.service,
                host_cfg,
                pair,
                args.threads,
                args.timerange,
            ): pair
            for pair in pairs
        }
        for fut in as_completed(futs):
            pair = futs[fut]
            try:
                code = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[train_pairs] {pair}: exception: {exc}", file=sys.stderr)
                code = 99
            results.append((pair, code))
            status = "OK" if code == 0 else f"FAIL({code})"
            print(f"[train_pairs] {pair}: {status}")

    failures = [p for p, c in results if c != 0]
    if failures:
        print("[train_pairs] Failures:")
        for p in failures:
            print(f"  - {p}")
        return 1
    print("[train_pairs] All jobs completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

