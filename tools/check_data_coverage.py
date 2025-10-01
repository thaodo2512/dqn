#!/usr/bin/env python3
"""Verify OHLCV data coverage for FreqAI backtesting/training.

Checks that all required pairs/timeframes have OHLCV starting early enough to
cover a requested timerange start minus a warmup buffer (days).

Usage:
  python tools/check_data_coverage.py \
    --config user_data/config.json \
    --timerange 20240101-20250930 \
    --timeframes 5m 15m 1h \
    --warmup-days 45

Exit status is non-zero when coverage is insufficient.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


LINE_RE = re.compile(
    r"^(?P<pair>[^,]+),\s*(?P<trading>[^,]+),\s*(?P<tf>[^,]+),\s*data starts at (?P<start>\d{4}-\d{2}-\d{2} [^,]+)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="user_data/config.json")
    p.add_argument("--timerange", required=True, help="YYYYMMDD-YYYYMMDD or YYYYMMDD-")
    p.add_argument("--timeframes", nargs="+", default=["5m", "15m", "1h"])
    p.add_argument("--warmup-days", type=int, default=int(os.environ.get("WARMUP_DAYS", 45)))
    return p.parse_args()


def read_pairs(cfg_path: str) -> List[str]:
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    wl = cfg.get("exchange", {}).get("pair_whitelist", [])
    corr = cfg.get("freqai", {}).get("feature_parameters", {}).get("include_corr_pairlist", [])
    return sorted(set((wl or []) + (corr or [])))


def build_pairs_file(pairs: Iterable[str]) -> str:
    import tempfile

    fd, path = tempfile.mkstemp(prefix="pairs_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(list(pairs), fh)
    return path


def timerange_start_str(tr: str) -> str:
    if "-" not in tr:
        return tr
    return tr.split("-", 1)[0]


def yyyymmdd_to_dt(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y%m%d").replace(tzinfo=dt.timezone.utc)


def run_list_data(config: str, pairs_file: str, timeframes: List[str]) -> str:
    cmd = [
        "freqtrade",
        "list-data",
        "--trading-mode",
        "futures",
        "--config",
        config,
        "--pairs-file",
        pairs_file,
        "--timeframes",
        *timeframes,
        "--show-timerange",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.stdout


def parse_starts(output: str) -> Dict[Tuple[str, str], dt.datetime]:
    starts: Dict[Tuple[str, str], dt.datetime] = {}
    for line in output.splitlines():
        m = LINE_RE.search(line.strip())
        if not m:
            continue
        pair = m.group("pair").strip()
        tf = m.group("tf").strip()
        start_str = m.group("start").split(" ")[0]  # YYYY-MM-DD
        try:
            d = dt.datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            starts[(pair, tf)] = d
        except Exception:
            continue
    return starts


def main() -> int:
    args = parse_args()
    pairs = read_pairs(args.config)
    if not pairs:
        print("No pairs found in config whitelist/correlated.", file=sys.stderr)
        return 2
    pairs_file = build_pairs_file(pairs)
    try:
        out = run_list_data(args.config, pairs_file, args.timeframes)
        starts = parse_starts(out)
    finally:
        try:
            os.remove(pairs_file)
        except Exception:
            pass

    tr_start = yyyymmdd_to_dt(timerange_start_str(args.timerange))
    required_min = tr_start - dt.timedelta(days=args.warmup_days)

    missing: List[Tuple[str, str, str]] = []
    for pair in pairs:
        for tf in args.timeframes:
            key = (pair, tf)
            if key not in starts:
                missing.append((pair, tf, "no-data"))
                continue
            if starts[key] > required_min:
                missing.append((pair, tf, starts[key].strftime("%Y-%m-%d")))

    print("Data coverage check")
    print(f"  Timerange start: {tr_start.date()}  Warmup days: {args.warmup_days}")
    print(f"  Required min start: {required_min.date()}")
    if not missing:
        print("OK: All pairs/timeframes start early enough.")
        return 0
    print("INSUFFICIENT COVERAGE for the following:")
    for pair, tf, got in missing:
        print(f"  - {pair} {tf}: starts at {got}")
    print("Hint: increase DOWNLOAD_START or WARMUP_DAYS, or set an earlier TIMERANGE start.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

