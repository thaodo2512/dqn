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


def launch_one_pair(
    compose: Path,
    service: str,
    host_cfg: Path,
    pair: str,
    threads: int,
    timerange: str,
    reward_debug: bool,
    id_prefix: str,
    id_suffix: str,
    fresh: bool,
    overlay_base: Path,
) -> int:
    cfg_dir = host_cfg.parent.resolve()
    cfg_base = host_cfg.name
    sname = safe_name(pair)
    ident = f"{id_prefix}dqn-{sname}{id_suffix}"

    # Create overlay configs on host (mounted into container via compose)
    ov_host = overlay_base
    ov_host.mkdir(exist_ok=True, parents=True)

    cpu_dev_path = ov_host / "cpu-device.json"
    if not cpu_dev_path.exists():
        cpu_dev_path.write_text(json.dumps({
            "freqai": {"rl_config": {"hyperparams": {"device": "cpu"}}}
        }))

    id_path = ov_host / f"id-{sname}.json"
    id_path.write_text(json.dumps({"freqai": {"identifier": ident}}))

    pair_cfg_path = ov_host / f"pairs-{sname}.json"
    pair_cfg_path.write_text(json.dumps({"exchange": {"pair_whitelist": [pair]}}))

    debug_cfg_opt = ""
    if reward_debug:
        dbg_path = ov_host / f"reward-debug-{sname}.json"
        dbg_path.write_text(json.dumps({
            "freqai": {"log_level": "DEBUG", "rl_config": {"reward_kwargs": {"debug_log": True}}}
        }))
        debug_cfg_opt = f" --config {dbg_path}"

    restore_cfg_opt = ""
    if fresh:
        rst_path = ov_host / f"restore-false-{sname}.json"
        rst_path.write_text(json.dumps({"freqai": {"restore_best_model": False}}))
        restore_cfg_opt = f" --config {rst_path}"

    # Container-visible overlay directory
    use_user_data_mount = (ov_host.resolve() == Path("user_data").resolve())
    ov_container = "/freqtrade/user_data" if use_user_data_mount else "/freqtrade/overlays"

    inner = (
        "mkdir -p user_data/logs && "
        + "freqtrade backtesting "
        + f"--config /freqtrade/user_config/{shlex.quote(cfg_base)} "
        + f"--config {ov_container}/cpu-device.json --config {ov_container}/id-{sname}.json --config {ov_container}/pairs-{sname}.json{debug_cfg_opt}{restore_cfg_opt} "
        + "--strategy-path user_data/strategies --strategy MyRLStrategy "
        + "--freqaimodel ReinforcementLearner "
        + f"-p {shlex.quote(pair)} "
        + f"--timerange {shlex.quote(timerange)} -vv "
        + f"--logfile user_data/logs/train-{sname}.log"
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
    ]
    # Mount overlays when not using user_data as base
    if not use_user_data_mount:
        cmd.extend(["-v", f"{str(ov_host.resolve())}:/freqtrade/overlays:ro"])
    cmd.extend([
        service,
        "bash",
        "-lc",
        inner,
    ])
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
        help="Max containers in parallel (default: auto from available CPUs)",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Threads per container for BLAS/NumExpr/Torch (default: auto)",
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
    p.add_argument(
        "--reward-debug",
        action="store_true",
        help="Enable detailed reward component logging and set FreqAI log level to DEBUG",
    )
    p.add_argument(
        "--id-prefix",
        default="",
        help="Optional prefix for freqai.identifier (default: empty)",
    )
    p.add_argument(
        "--id-suffix",
        default="",
        help="Optional suffix for freqai.identifier (default: empty)",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help="Train from scratch by disabling restore_best_model for this run",
    )
    return p.parse_args(list(argv))

def _parse_cpuset(cpuset: str) -> int:
    total = 0
    for part in (cpuset or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start = int(a); end = int(b)
            except ValueError:
                continue
            if end >= start:
                total += (end - start + 1)
        else:
            try:
                int(part)
                total += 1
            except ValueError:
                continue
    return total


def detect_logical_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except Exception:
        pass
    # cgroup v1
    try:
        with open("/sys/fs/cgroup/cpuset/cpuset.cpus", "r", encoding="utf-8") as fh:
            n = _parse_cpuset(fh.read().strip())
            if n > 0:
                return n
    except Exception:
        pass
    # cgroup v2
    for path in ("/sys/fs/cgroup/cpuset.cpus", "/sys/fs/cgroup/cpuset.cpus.effective"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                n = _parse_cpuset(fh.read().strip())
                if n > 0:
                    return n
        except Exception:
            continue
    return max(1, os.cpu_count() or 1)


def choose_threads(cpus: int) -> int:
    if cpus <= 4:
        return 1
    if cpus <= 8:
        return 2
    if cpus <= 24:
        return 4
    return 6


def compute_default_concurrency(threads: int, cpus: int | None = None) -> int:
    cores = int(cpus or detect_logical_cpus())
    k = max(1, cores // max(1, threads))
    return max(1, min(k, 16))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    compose = Path(args.compose_file)
    if not compose.exists():
        print(f"compose file not found: {compose}", file=sys.stderr)
        return 2

    # Resolve config path: prefer user-provided, otherwise fall back to user_data/config.json
    cfg_in = Path(args.config).expanduser()
    if cfg_in.exists():
        host_cfg = cfg_in
    else:
        # Fallback only when using the default missing path
        fallback = Path("user_data/config.json")
        if args.config == "user_config/config.json" and fallback.exists():
            print(f"[train_pairs] Falling back to {fallback} (default config not found)")
            host_cfg = fallback
        else:
            print(f"config not found: {cfg_in}", file=sys.stderr)
            return 2

    pairs = args.pairs or read_pairs_from_config(host_cfg)
    if not pairs:
        print("no pairs to train", file=sys.stderr)
        return 2

    cpus = detect_logical_cpus()
    threads = args.threads or choose_threads(cpus)
    conc = args.concurrency or compute_default_concurrency(threads, cpus)
    print(f"[train_pairs] Detected CPUs={cpus} -> threads/container={threads}, concurrency={conc}")
    print(f"[train_pairs] Total pairs: {len(pairs)}")
    print("[train_pairs] Pairs:")
    for p in pairs:
        print(f"  - {p}")

    # Prefetch OHLCV data using the external config
    print("[train_pairs] Prefetching historical data ...")
    rc = prefetch_data(compose, args.service, host_cfg)
    if rc != 0:
        print(f"[train_pairs] Prefetch failed with code {rc}", file=sys.stderr)
        return rc

    # Choose overlay base dir: prefer user_data, but if not writable, fall back to .overlays
    overlay_base = Path("user_data")
    try:
        overlay_base.mkdir(exist_ok=True)
        test_path = overlay_base / ".writetest"
        test_path.write_text("ok")
        test_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        overlay_base = Path(".overlays")
        overlay_base.mkdir(exist_ok=True)
        print(f"[train_pairs] user_data not writable; using {overlay_base} for overlays")

    # Fan out containers with bounded parallelism
    results: List[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = {
            ex.submit(
                launch_one_pair,
                compose,
                args.service,
                host_cfg,
                pair,
                threads,
                args.timerange,
                bool(args.reward_debug),
                str(args.id_prefix or ""),
                str(args.id_suffix or ""),
                bool(args.fresh),
                overlay_base,
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
