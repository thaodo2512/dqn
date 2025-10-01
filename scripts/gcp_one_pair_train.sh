#!/usr/bin/env bash
set -euo pipefail

# One‑pair GCP training: creates a small VM, runs training for a single pair,
# fetches artifacts locally. Leaves the original e2e scripts untouched.
#
# Defaults mirror the VM command you provided. You can override most values via
# flags or env without editing the script.
#
# Usage examples:
#   scripts/gcp_one_pair_train.sh --pair BTC/USDT:USDT
#   scripts/gcp_one_pair_train.sh --pair ETH/USDT:USDT --timerange 20240101-20240930 \
#       --instance-name my-onepair-$(date +%Y%m%d-%H%M) --cleanup
#
# Required:
#   --pair SYMBOL         Pair symbol like "BTC/USDT:USDT"
#
# Optional (sane defaults prefilled from your VM command):
#   --project ID          GCP project (default: valiant-epsilon-472304-r9)
#   --zone ZONE           Zone (default: asia-south1-c)
#   --instance-name NAME  VM name (default: onepair-YYYYMMDD-HHMMSS at runtime)
#   --timerange STR       Backtest timerange (default: 20240101-20250930)
#   --threads N           Threads per container (default: 1)
#   --fresh               Disable model restore for this run
#   --id-prefix STR       Identifier prefix (default: onepair-)
#   --id-suffix STR       Identifier suffix (default: empty)
#   --cleanup             Delete the VM at the end (default: keep)
#
# Outputs:
#   gcp-output/<instance-name>/freqaimodels(.tgz), logs(.tgz) fetched locally.

usage() {
  sed -n '1,120p' "$0" | sed -n '1,60p'
}

PAIR=""
PROJECT_ID=${PROJECT_ID:-valiant-epsilon-472304-r9}
ZONE=${ZONE:-asia-south1-c}
# Default to a timestamped instance name so each run is unique
INSTANCE_NAME=${INSTANCE_NAME:-onepair-$(date +%Y%m%d-%H%M%S)}
TIMERANGE=${TIMERANGE:-20240101-20250930}
THREADS=${THREADS:-1}
FRESH=${FRESH:-0}
ID_PREFIX=${ID_PREFIX:-onepair-}
ID_SUFFIX=${ID_SUFFIX:-}
CLEANUP=${CLEANUP:-0}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pair) PAIR="$2"; shift 2;;
    --project) PROJECT_ID="$2"; shift 2;;
    --zone) ZONE="$2"; shift 2;;
    --instance-name) INSTANCE_NAME="$2"; shift 2;;
    --timerange) TIMERANGE="$2"; shift 2;;
    --threads) THREADS="$2"; shift 2;;
    --fresh) FRESH=1; shift;;
    --id-prefix) ID_PREFIX="$2"; shift 2;;
    --id-suffix) ID_SUFFIX="$2"; shift 2;;
    --cleanup) CLEANUP=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$PAIR" ]]; then
  echo "--pair is required (e.g., --pair BTC/USDT:USDT)" >&2
  usage; exit 2
fi

echo "[onepair] Project=${PROJECT_ID} Zone=${ZONE} Instance=${INSTANCE_NAME} Pair=${PAIR}"

# Create the VM if it doesn't exist already
if gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "[onepair] Instance already exists: ${INSTANCE_NAME} (skipping create)"
else
  echo "[onepair] Creating VM ${INSTANCE_NAME} ..."
  gcloud compute instances create "$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --zone="$ZONE" \
    --machine-type=c4d-standard-2 \
    --network-interface=network-tier=PREMIUM,nic-type=GVNIC,stack-type=IPV4_ONLY,subnet=default \
    --metadata=enable-osconfig=TRUE \
    --maintenance-policy=MIGRATE \
    --provisioning-model=STANDARD \
    --service-account=137846157442-compute@developer.gserviceaccount.com \
    --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append \
    --min-cpu-platform="AMD Turin" \
    --tags=http-server,https-server \
    --create-disk=auto-delete=yes,boot=yes,device-name="${INSTANCE_NAME}",image=projects/ubuntu-os-cloud/global/images/ubuntu-minimal-2204-jammy-v20250930,mode=rw,provisioned-iops=3300,provisioned-throughput=215,size=50,type=hyperdisk-balanced \
    --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --labels=goog-ops-agent-policy=v2-x86-template-1-4-0,goog-ec-src=vm_add-gcloud \
    --reservation-affinity=any \
    --threads-per-core=1 \
    --visible-core-count=1

  # Create ops-agents policy matching your template (ignore if it already exists)
  echo "agentsRule:
  packageState: installed
  version: latest
instanceFilter:
  inclusionLabels:
  - labels:
      goog-ops-agent-policy: v2-x86-template-1-4-0
" > /tmp/ops-agents-config.yaml
  POLICY_NAME="goog-ops-agent-v2-x86-template-1-4-0-${ZONE}"
  gcloud compute instances ops-agents policies create "$POLICY_NAME" \
    --project="$PROJECT_ID" --zone="$ZONE" --file=/tmp/ops-agents-config.yaml || true
fi

echo "[onepair] Waiting for VM to be RUNNING ..."
for _ in {1..180}; do
  STATUS=$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format='value(status)' || true)
  [[ "$STATUS" == "RUNNING" ]] && break
  sleep 5
done
[[ "$STATUS" == "RUNNING" ]] || { echo "[onepair] VM did not become RUNNING" >&2; exit 1; }

echo "[onepair] Installing Docker on the VM ..."
INSTALL_DOCKER='sudo apt-get update -y && sudo apt-get install -y ca-certificates curl gnupg && \
  sudo install -m 0755 -d /etc/apt/keyrings && \
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null && \
  sudo chmod a+r /etc/apt/keyrings/docker.asc && \
  . /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null && \
  sudo apt-get update -y && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin python3 && \
  sudo usermod -aG docker $USER && sudo systemctl enable --now docker'
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --command "$INSTALL_DOCKER"

ROOT_LOCAL=$(pwd)
REPO_NAME=$(basename "$ROOT_LOCAL")

echo "[onepair] Copying repo to VM ..."
gcloud compute scp --recurse "$ROOT_LOCAL" "$INSTANCE_NAME":~/. --zone="$ZONE" --project="$PROJECT_ID"

echo "[onepair] Building CPU training image and running single-pair training ..."
REMOTE_RUN=(
  "set -euo pipefail; \n"
  "cd ~/${REPO_NAME} && \n"
  "docker compose -f docker/docker-compose.train.cpu.x86.yml build && \n"
  "python3 scripts/train_pairs.py --threads ${THREADS} --concurrency 1 --timerange ${TIMERANGE} --pairs $(printf %q "$PAIR")"
)
if [[ -n "$ID_PREFIX" ]]; then
  REMOTE_RUN+=(" --id-prefix $(printf %q "$ID_PREFIX")")
fi
if [[ -n "$ID_SUFFIX" ]]; then
  REMOTE_RUN+=(" --id-suffix $(printf %q "$ID_SUFFIX")")
fi
if [[ "$FRESH" == "1" ]]; then
  REMOTE_RUN+=(" --fresh")
fi
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --command "${REMOTE_RUN[*]}"

echo "[onepair] Packaging artifacts on VM ..."
PACK_CMD=(
  "set -euo pipefail; \n"
  "cd ~/${REPO_NAME} && \n"
  "mkdir -p output && \n"
  "rm -f output/freqaimodels.tgz || true && \n"
  "tar -C user_data -czf output/freqaimodels.tgz freqaimodels || true && \n"
  "rm -rf output/freqaimodels && \n"
  "if [[ -d user_data/freqaimodels ]]; then cp -r user_data/freqaimodels output/; fi && \n"
  "rm -f output/logs.tgz || true && \n"
  "tar -C user_data -czf output/logs.tgz logs || true && \n"
  "rm -rf output/logs && \n"
  "if [[ -d user_data/logs ]]; then cp -r user_data/logs output/; fi && \n"
  "echo '[onepair] Artifacts ready under: ' $(pwd)/output"
)
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --command "${PACK_CMD[*]}"

LOCAL_OUT_DIR="gcp-output/${INSTANCE_NAME}"
mkdir -p "$LOCAL_OUT_DIR"
echo "[onepair] Fetching artifacts to ${LOCAL_OUT_DIR} ..."
gcloud compute scp --recurse "$INSTANCE_NAME":~/${REPO_NAME}/output/. "$LOCAL_OUT_DIR" --zone="$ZONE" --project="$PROJECT_ID" || echo "[onepair] No artifacts to fetch"

if [[ "$CLEANUP" == "1" ]]; then
  echo "[onepair] Deleting VM ${INSTANCE_NAME} ..."
  gcloud compute instances delete "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --quiet || true
fi

echo "[onepair] Done. Artifacts: ${LOCAL_OUT_DIR}"
