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

# Expand start backwards to include warmup buffer before TIMERANGE start.
# Default warmup 45 days; override via WARMUP_DAYS.
: "${WARMUP_DAYS:=45}"
if [[ -n "${TIMERANGE:-}" && "$TIMERANGE" == *-* ]]; then
  TR_START="${TIMERANGE%%-*}"
  if [[ "$TR_START" =~ ^[0-9]{8}$ ]]; then
    AUTO_START="$(date -u -d "${TR_START} - ${WARMUP_DAYS} days" +%Y%m%d)"
    # Use the earlier of DOWNLOAD_START and AUTO_START
    if [[ "$AUTO_START" < "$DOWNLOAD_START" ]]; then
      echo "[download-data] Extending start back by ${WARMUP_DAYS}d: ${DOWNLOAD_START} -> ${AUTO_START}" >&2
      DOWNLOAD_START="$AUTO_START"
    fi
  fi
fi

echo "[download-data] Timerange: ${DOWNLOAD_START}-${DOWNLOAD_END}" >&2

# Build a pairs-file. By default include whitelist + correlated pairs so FreqAI
# has all required OHLCV for feature generation. Overrides:
#  - SINGLE_PAIR: if set, restrict downloads to this one pair
#  - PAIRS_FILE_OVERRIDE: if path set and exists, use that JSON file directly
if [[ -n "${PAIRS_FILE_OVERRIDE:-}" && -f "${PAIRS_FILE_OVERRIDE}" ]]; then
  PAIRS_FILE="${PAIRS_FILE_OVERRIDE}"
  echo "[download-data] Using override pairs file: ${PAIRS_FILE}" >&2
else
  PAIRS_FILE=$(mktemp)
  if [[ -n "${SINGLE_PAIR:-}" ]]; then
    echo "[download-data] SINGLE_PAIR set → restricting downloads to: ${SINGLE_PAIR}" >&2
    python3 - "$SINGLE_PAIR" >"$PAIRS_FILE" <<'PY'
import json,sys
pair=sys.argv[1]
print(json.dumps([pair]))
PY
  else
    python3 - "$FT_CONFIG" >"$PAIRS_FILE" <<'PY'
import json,sys
cfg_path=sys.argv[1]
with open(cfg_path,'r',encoding='utf-8') as fh:
    cfg=json.load(fh)
wl=cfg.get('exchange',{}).get('pair_whitelist',[])
corr=cfg.get('freqai',{}).get('feature_parameters',{}).get('include_corr_pairlist',[])
pairs=sorted(set((wl or []) + (corr or [])))
print(json.dumps(pairs))
PY
  fi
fi

echo "[download-data] Pairs:" >&2
python3 - "$PAIRS_FILE" <<'PY'
import json,sys
pairs=json.load(open(sys.argv[1],'r',encoding='utf-8'))
print("\n".join(pairs))
PY

freqtrade download-data \
  --trading-mode futures \
  --config "${FT_CONFIG}" \
  --timeframes ${DOWNLOAD_TIMEFRAMES} \
  --timerange "${DOWNLOAD_START}-${DOWNLOAD_END}" \
  --prepend \
  --pairs-file "$PAIRS_FILE"

rm -f "$PAIRS_FILE"

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
