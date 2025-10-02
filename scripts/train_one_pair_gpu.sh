#!/usr/bin/env bash
set -euo pipefail

# Simple wrapper to run training (RL backtesting) on GPU for a single pair
# using the L4 compose stack, with TIMERANGE aligned to the last N×30 days.
#
# Usage:
#   scripts/train_one_pair_gpu.sh --pair BTC/USDT:USDT
#   scripts/train_one_pair_gpu.sh --pair ETH/USDT:USDT --latest-blocks 22
#   scripts/train_one_pair_gpu.sh --pair BTC/USDT:USDT --timerange 20240101-20250930
#
# Options:
#   --pair SYMBOL            Required. e.g., BTC/USDT:USDT
#   --latest-blocks N        Use last N×30 days ending today (UTC). Default: 22
#   --timerange STR          Explicit YYYYMMDD-YYYYMMDD; overrides --latest-blocks
#   --compose-file PATH      Compose file (default: docker/docker-compose.train.gpu.l4.yml)
#   --service NAME           Service name (default: freqai-train-gpu-l4)

PAIR=""
LATEST_BLOCKS=${LATEST_BLOCKS:-22}
TIMERANGE=${TIMERANGE:-}
COMPOSE_FILE=${COMPOSE_FILE:-docker/docker-compose.train.gpu.l4.yml}
SERVICE=${SERVICE:-freqai-train-gpu-l4}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pair) PAIR="$2"; shift 2;;
    --latest-blocks) LATEST_BLOCKS="$2"; shift 2;;
    --timerange) TIMERANGE="$2"; shift 2;;
    --compose-file) COMPOSE_FILE="$2"; shift 2;;
    --service) SERVICE="$2"; shift 2;;
    -h|--help)
      sed -n '1,80p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ -z "$PAIR" ]]; then
  echo "--pair is required (e.g., --pair BTC/USDT:USDT)" >&2
  exit 2
fi

# If no explicit TIMERANGE, compute last N×30 days ending today (UTC)
if [[ -z "$TIMERANGE" ]]; then
  END_UTC=$(date -u +%Y%m%d)
  DAYS=$(( LATEST_BLOCKS * 30 ))
  START_UTC=$(date -u -d "${END_UTC} - ${DAYS} days" +%Y%m%d)
  TIMERANGE="${START_UTC}-${END_UTC}"
fi

echo "[train_one_pair_gpu] Compose: ${COMPOSE_FILE}  Service: ${SERVICE}"
echo "[train_one_pair_gpu] Pair: ${PAIR}  Timerange: ${TIMERANGE}"

exec docker compose -f "$COMPOSE_FILE" run --rm \
  -e "TIMERANGE=${TIMERANGE}" \
  -e "SINGLE_PAIR=${PAIR}" \
  "$SERVICE"

