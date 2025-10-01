#!/usr/bin/env bash
set -euo pipefail

# Jetson inference runner using saved FreqAI RL models.
#
# Modes:
#   backtest  - run backtesting using saved models only (no training)
#   trade     - run dry-run trading using saved models; uses infer-only overlay
#
# Examples:
#   ./scripts/run_inference_jetson.sh backtest --timerange 20240801-20240901
#   ./scripts/run_inference_jetson.sh trade --detach
#
# Notes:
# - Ensure user_data/freqaimodels/ contains the trained model matching
#   user_data/config.json -> freqai.identifier.
# - The overlay user_data/infer-only.json disables training loops.

MODE=${1:-}
shift || true

TIMERANGE=${TIMERANGE:-20240101-20250930}
DETACH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timerange) TIMERANGE="$2"; shift 2 ;;
    --detach|-d) DETACH=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$MODE" ]]; then
  echo "Usage: $0 <backtest|trade> [--timerange YYYYMMDD-YYYYMMDD] [--detach]" >&2
  exit 2
fi

case "$MODE" in
  backtest)
    # Use training compose service but run a one-off backtest with live models.
    exec docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train \
      freqtrade backtesting \
        --config user_data/config.json \
        --config user_data/infer-only.json \
        --strategy-path user_data/strategies --strategy MyRLStrategy \
        --freqaimodel ReinforcementLearner --freqai-backtest-live-models \
        --timerange "$TIMERANGE" -vv
    ;;
  trade)
    if [[ "$DETACH" == "1" ]]; then
      # Background service with Web/API; container stays up.
      exec docker compose -f docker/docker-compose.jetson.yml up -d
    else
      # One-off foreground trade invoking infer-only overlay.
      exec docker compose -f docker/docker-compose.jetson.yml run --rm --service-ports freqai \
        bash -lc 'freqtrade trade --config user_data/config.json --config user_data/infer-only.json --strategy-path user_data/strategies --strategy MyRLStrategy --dry-run'
    fi
    ;;
  *)
    echo "Unknown mode: $MODE (expected backtest|trade)" >&2
    exit 2
    ;;
esac

