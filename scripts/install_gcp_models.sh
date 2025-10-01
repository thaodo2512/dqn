#!/usr/bin/env bash
set -euo pipefail

# Install trained models from a GCP e2e run into this repo (and optionally to a Jetson).
#
# By default, selects the newest folder under gcp-output/ and copies its
# freqaimodels/ into user_data/freqaimodels/.
#
# Usage examples:
#   scripts/install_gcp_models.sh
#   scripts/install_gcp_models.sh --source gcp-output/instance-20251001-103405
#   scripts/install_gcp_models.sh --clean
#   scripts/install_gcp_models.sh --jetson-dest user@jetson:/home/jetson/dqn-repo
#   scripts/install_gcp_models.sh --identifier my-dqn-sol
#
# Options:
#   --source DIR        Source directory (gcp-output/<instance>); default: latest under gcp-output/
#   --dest DIR          Destination models dir; default: user_data/freqaimodels
#   --clean             Remove destination before install
#   --from-tar          Prefer extracting freqaimodels.tgz over copying freqaimodels/
#   --jetson-dest STR   Rsync installed models to Jetson repo (e.g., user@host:/path/to/repo)
#   --identifier STR    Optionally set user_data/config.json freqai.identifier to STR

ROOT_DIR=$(cd "$(dirname "$0")"/.. && pwd)
cd "$ROOT_DIR"

SRC_DIR=""
DEST_DIR="user_data/freqaimodels"
CLEAN=0
FROM_TAR=0
JETSON_DEST=""
SET_IDENTIFIER=""

usage() {
  sed -n '1,200p' "$0" | sed -n '1,20p'
}

latest_output_dir() {
  ls -1dt gcp-output/*/ 2>/dev/null | head -n1 | sed 's:/*$::'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SRC_DIR="$2"; shift 2 ;;
    --dest) DEST_DIR="$2"; shift 2 ;;
    --clean) CLEAN=1; shift ;;
    --from-tar) FROM_TAR=1; shift ;;
    --jetson-dest) JETSON_DEST="$2"; shift 2 ;;
    --identifier) SET_IDENTIFIER="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$SRC_DIR" ]]; then
  SRC_DIR=$(latest_output_dir || true)
  if [[ -z "$SRC_DIR" ]]; then
    echo "No gcp-output/* directory found. Use --source to specify." >&2
    exit 2
  fi
fi

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Source directory not found: $SRC_DIR" >&2; exit 2
fi

echo "[install] Source: $SRC_DIR"
echo "[install] Destination: $DEST_DIR"

MODELS_DIR="$SRC_DIR/freqaimodels"
MODELS_TGZ="$SRC_DIR/freqaimodels.tgz"

if [[ "$CLEAN" == "1" ]]; then
  echo "[install] Cleaning destination: $DEST_DIR"
  rm -rf "$DEST_DIR"
fi

mkdir -p "$(dirname "$DEST_DIR")"

if [[ "$FROM_TAR" == "1" ]] && [[ -f "$MODELS_TGZ" ]]; then
  echo "[install] Extracting from tarball: $MODELS_TGZ"
  tar -xzf "$MODELS_TGZ" -C "$(dirname "$DEST_DIR")"
elif [[ -d "$MODELS_DIR" ]]; then
  echo "[install] Copying models directory: $MODELS_DIR -> $DEST_DIR"
  mkdir -p "$DEST_DIR"
  rsync -a "$MODELS_DIR/" "$DEST_DIR/"
elif [[ -f "$MODELS_TGZ" ]]; then
  echo "[install] Extracting from tarball: $MODELS_TGZ"
  tar -xzf "$MODELS_TGZ" -C "$(dirname "$DEST_DIR")"
else
  echo "No models found in $SRC_DIR (missing freqaimodels/ and freqaimodels.tgz)" >&2
  exit 2
fi

if [[ -n "$SET_IDENTIFIER" ]]; then
  echo "[install] Setting freqai.identifier to: $SET_IDENTIFIER"
  python3 - "$SET_IDENTIFIER" <<'PY'
import json,sys
ident=sys.argv[1]
path='user_data/config.json'
with open(path,'r',encoding='utf-8') as fh:
    cfg=json.load(fh)
cfg.setdefault('freqai',{})['identifier']=ident
with open(path,'w',encoding='utf-8') as fh:
    json.dump(cfg,fh,indent=2)
    fh.write('\n')
print('updated',path)
PY
fi

if [[ -n "$JETSON_DEST" ]]; then
  REMOTE_MODELS_DIR="$JETSON_DEST/user_data/freqaimodels/"
  echo "[install] Syncing to Jetson: $REMOTE_MODELS_DIR"
  rsync -av "$DEST_DIR/" "$REMOTE_MODELS_DIR"
fi

echo "[install] Done. Installed models at: $DEST_DIR"

