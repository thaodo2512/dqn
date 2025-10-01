#!/usr/bin/env bash
set -euo pipefail

# Remote VM runner: builds training image, runs multi-pair training, and packages artifacts.
#
# Options (env or flags):
#   --threads N        Threads per container for BLAS/NumExpr/Torch (default: 2)
#   --concurrency N    Parallel containers (default: 8)
#   --timerange STR    Backtest timerange (default: 20240101-20250930)
#   --id-prefix STR    Optional freqai.identifier prefix
#   --id-suffix STR    Optional freqai.identifier suffix
#   --fresh            Disable restore_best_model for this run
#
# Outputs:
#   - output/freqaimodels/ (copied from user_data/freqaimodels)
#   - output/freqaimodels.tgz (tarball of models directory)

THREADS=${THREADS:-2}
CONCURRENCY=${CONCURRENCY:-8}
TIMERANGE=${TIMERANGE:-20240101-20250930}
ID_PREFIX=${ID_PREFIX:-}
ID_SUFFIX=${ID_SUFFIX:-}
FRESH=${FRESH:-0}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --threads) THREADS="$2"; shift 2;;
    --concurrency) CONCURRENCY="$2"; shift 2;;
    --timerange) TIMERANGE="$2"; shift 2;;
    --id-prefix) ID_PREFIX="$2"; shift 2;;
    --id-suffix) ID_SUFFIX="$2"; shift 2;;
    --fresh) FRESH=1; shift;;
    *) echo "Unknown arg: $1"; exit 2;;
  esac
done

echo "[gcp_vm_run] Threads=${THREADS} Concurrency=${CONCURRENCY} Timerange=${TIMERANGE}"

# Build the CPU training image upfront to avoid N parallel builds.
echo "[gcp_vm_run] Building training image ..."
docker compose -f docker/docker-compose.train.cpu.x86.yml build

echo "[gcp_vm_run] Starting bounded-parallel training ..."
PY_ARGS=(
  --threads "${THREADS}"
  --concurrency "${CONCURRENCY}"
  --timerange "${TIMERANGE}"
)
[[ -n "${ID_PREFIX}" ]] && PY_ARGS+=(--id-prefix "${ID_PREFIX}")
[[ -n "${ID_SUFFIX}" ]] && PY_ARGS+=(--id-suffix "${ID_SUFFIX}")
[[ "${FRESH}" == "1" ]] && PY_ARGS+=(--fresh)

python3 scripts/train_pairs.py "${PY_ARGS[@]}"

echo "[gcp_vm_run] Packaging artifacts ..."
mkdir -p output
rm -f output/freqaimodels.tgz || true
tar -C user_data -czf output/freqaimodels.tgz freqaimodels || echo "[gcp_vm_run] No models to tar"
rm -rf output/freqaimodels
if [[ -d user_data/freqaimodels ]]; then
  cp -r user_data/freqaimodels output/
fi
echo "[gcp_vm_run] Done. Artifacts under: $(pwd)/output"

