#!/usr/bin/env python3
"""
Generate HTML reports from the most recent backtest results and open them in a browser.

Behavior:
- Finds the newest JSON in `user_data/backtest_results/` by modification time.
- Runs `freqtrade plot-profit` (locally if available, otherwise via the x86 reports compose).
- Picks the newest HTML in `user_data/plot/` and attempts to open it.

Usage:
  python scripts/report_latest.py
  python scripts/report_latest.py --pair ETH/USDT:USDT --timerange 20240101-20250930
  python scripts/report_latest.py --use-docker  # force Docker-based report generation

Notes:
- For Docker path, this script uses `docker/docker-compose.reports.cpu.x86.yml`.
- HTML files are written to `user_data/plot/`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_latest_results(results_dir: Path) -> Optional[Path]:
    if not results_dir.exists():
        return None
    candidates = sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def find_latest_html(plot_dir: Path) -> Optional[Path]:
    if not plot_dir.exists():
        return None
    candidates = sorted(plot_dir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def run_local_plot(ft_config: Path, results_json: Path, pair: str, timerange: str) -> int:
    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    # Profit curve
    cmd1 = [
        "freqtrade",
        "plot-profit",
        "--config",
        str(ft_config),
        "--results",
        str(results_json),
    ]
    rc = subprocess.call(cmd1, env=env, cwd=str(repo_root()))
    if rc != 0:
        return rc
    # Per-pair (best-effort)
    cmd2 = [
        "freqtrade",
        "plot-dataframe",
        "--config",
        str(ft_config),
        "--strategy-path",
        "user_data/strategies",
        "--strategy",
        "MyRLStrategy",
        "-p",
        pair,
        "--timerange",
        timerange,
    ]
    subprocess.call(cmd2, env=env, cwd=str(repo_root()))
    return 0


def run_docker_plot(compose_file: Path, results_json_rel: str) -> int:
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "run",
        "--rm",
        "-e",
        f"RESULTS_JSON={results_json_rel}",
        "freqai-reports-cpu-x86",
    ]
    return subprocess.call(cmd, cwd=str(repo_root()))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default=os.environ.get("PAIR", "BTC/USDT:USDT"))
    parser.add_argument(
        "--timerange", default=os.environ.get("TIMERANGE", "20240101-20250930")
    )
    parser.add_argument("--use-docker", action="store_true", help="Force Docker compose path")
    args = parser.parse_args(argv)

    root = repo_root()
    results_dir = root / "user_data" / "backtest_results"
    plot_dir = root / "user_data" / "plot"
    ft_config = root / "user_data" / "config.json"

    latest = find_latest_results(results_dir)
    if not latest:
        print(f"No results JSON found in {results_dir}", file=sys.stderr)
        return 2

    print(f"[report_latest] Using results: {latest.relative_to(root)}")

    rc = 0
    use_docker = bool(args.use_docker)
    freqtrade_path = shutil.which("freqtrade")
    if not use_docker and freqtrade_path:
        rc = run_local_plot(ft_config, latest, args.pair, args.timerange)
    else:
        compose_file = root / "docker" / "docker-compose.reports.cpu.x86.yml"
        if not compose_file.exists():
            print(
                "Compose file not found and local freqtrade is unavailable. "
                "Install freqtrade or add the compose stack.",
                file=sys.stderr,
            )
            return 2
        # Container working_dir is /freqtrade and mounts ../user_data at /freqtrade/user_data
        rel_results = str(latest.relative_to(root))
        if not rel_results.startswith("user_data/"):
            print(
                f"Unexpected results path layout: {rel_results}. Expected under user_data/",
                file=sys.stderr,
            )
            return 2
        rc = run_docker_plot(compose_file, rel_results)
    if rc != 0:
        return rc

    html = find_latest_html(plot_dir)
    if not html:
        print("No HTML report found in user_data/plot/", file=sys.stderr)
        return 1

    print(f"[report_latest] Opening: {html}")
    try:
        webbrowser.open(html.resolve().as_uri())
    except Exception:
        print("Failed to open browser automatically. Open the HTML manually:", file=sys.stderr)
        print(str(html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

