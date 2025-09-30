#!/usr/bin/env bash
set -euo pipefail

# Ensure historical data coverage across all configured timeframes before training/backtesting.
# Defaults aim to cover FreqAI warmup (multi-timeframe) plus training window.

# Start date for downloads (inclusive). Override via env DOWNLOAD_START=YYYYMMDD.
: "${DOWNLOAD_START:=20231001}"

# End date for downloads (inclusive). If not set, derive from TIMERANGE end or use today.
if [[ -z "${DOWNLOAD_END:-}" ]]; then
  if [[ -n "${TIMERANGE:-}" && "$TIMERANGE" == *-* ]]; then
    DOWNLOAD_END="${TIMERANGE#*-}"
  else
    DOWNLOAD_END="$(date +%Y%m%d)"
  fi
fi

# Timeframes to download. Override via env DOWNLOAD_TIMEFRAMES.
: "${DOWNLOAD_TIMEFRAMES:=5m 15m 1h}"

# Config path inside container / workspace
: "${FT_CONFIG:=user_data/config.json}"

echo "[download-data] Timeframes: ${DOWNLOAD_TIMEFRAMES}" >&2
echo "[download-data] Timerange: ${DOWNLOAD_START}-${DOWNLOAD_END}" >&2

freqtrade download-data \
  --trading-mode futures \
  --config "${FT_CONFIG}" \
  --timeframes ${DOWNLOAD_TIMEFRAMES} \
  --timerange "${DOWNLOAD_START}-${DOWNLOAD_END}"

echo "[download-data] Available data after download:" >&2
# Prefer showing timerange if the CLI supports it; don't pass timeframe filters here.
if freqtrade list-data --help 2>/dev/null | grep -q -- "--show-timerange"; then
  freqtrade list-data \
    --trading-mode futures \
    --config "${FT_CONFIG}" \
    --show-timerange || true
else
  freqtrade list-data \
    --trading-mode futures \
    --config "${FT_CONFIG}" || true
fi

echo "[download-data] Done." >&2
