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
#   --no-install          Skip local model install after fetching artifacts
#   --use-iap             SSH via IAP tunnel instead of external IP
#   --apt-timeout SECS    Max seconds to wait for apt/dpkg to be idle before forcing (default: 600)
#   --force-apt           After timeout, stop apt services and proceed (dangerous but pragmatic)
#   --debug               Stream verbose SSH and enable remote set -x for easier debugging
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
USE_IAP=${USE_IAP:-0}
APT_TIMEOUT=${APT_TIMEOUT:-600}
FORCE_APT=${FORCE_APT:-0}
DEBUG=${DEBUG:-0}
INSTALL_LOCAL=${INSTALL_LOCAL:-1}

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
    --use-iap) USE_IAP=1; shift;;
    --apt-timeout) APT_TIMEOUT="$2"; shift 2;;
    --force-apt) FORCE_APT=1; shift;;
    --debug) DEBUG=1; shift;;
    --no-install) INSTALL_LOCAL=0; shift;;
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

# Wait for SSH service to become reachable; newly booted images may refuse connections briefly.
echo "[onepair] Waiting for SSH to be ready ..."
IAP_OPT=""
if [[ "$USE_IAP" == "1" ]]; then IAP_OPT="--tunnel-through-iap"; fi
SSH_READY=0
SSH_VERBOSE_ARG=""
if [[ "$DEBUG" == "1" ]]; then SSH_VERBOSE_ARG="-- -v"; fi
SSH_BASE_OPTS="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
for i in {1..30}; do
  if gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" $IAP_OPT \
      --command "echo ready" -- $SSH_BASE_OPTS ${DEBUG:+-v}; then
    SSH_READY=1; break
  fi
  echo "  attempt $i/30: ssh not ready yet; sleeping 5s ..."
  sleep 5
done
if [[ "$SSH_READY" != "1" ]]; then
  echo "[onepair] SSH still not reachable. Try troubleshooting:" >&2
  echo "  gcloud compute ssh $INSTANCE_NAME --project=$PROJECT_ID --zone=$ZONE --troubleshoot ${USE_IAP:+--tunnel-through-iap}" >&2
  exit 1
fi

echo "[onepair] Installing Docker on the VM ..."
INSTALL_DOCKER='set -euo pipefail; \
  # Ensure cloud-init completed to avoid racing its apt jobs
  if command -v cloud-init >/dev/null 2>&1; then \
    echo "[vm] cloud-init status:"; sudo cloud-init status || true; \
    sudo cloud-init status --wait || true; \
  fi; \
  wait_apt() { \
    for i in $(seq 1 __APT_ITERS__); do \
      if \
        sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
        sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
        sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || \
        (command -v systemctl >/dev/null 2>&1 && ( \
          systemctl is-active --quiet apt-daily.service || \
          systemctl is-active --quiet apt-daily-upgrade.service || \
          systemctl is-active --quiet unattended-upgrades.service \
        )); then \
        echo "[vm] apt/dpkg busy; retry $i/__APT_ITERS__"; \
        if [ $((i % 10)) -eq 1 ]; then \
          echo "[vm] active units:"; systemctl list-units --type=service | grep -E "apt-daily|unattended" || true; \
          echo "[vm] processes:"; ps -C apt-get,dpkg -o pid,cmd --no-headers || true; \
        fi; \
        sleep 5; \
      else \
        sudo dpkg --configure -a || true; return 0; \
      fi; \
    done; \
    echo "[vm] apt/dpkg remained busy after wait window" >&2; \
    return 2; \
  }; \
  export DEBIAN_FRONTEND=noninteractive; \
  if [ "__DEBUG__" = "1" ]; then set -x; fi; \
  if ! wait_apt; then \
    if [ "__FORCE_APT__" = "1" ]; then \
      echo "[vm] forcing apt: stopping apt-daily/unattended services"; \
      sudo systemctl stop apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer unattended-upgrades.service || true; \
      sleep 3; \
      echo "[vm] attempting to kill lingering apt/dpkg"; \
      sudo pkill -9 apt-get || true; sudo pkill -9 dpkg || true; \
      sudo rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock || true; \
      sudo dpkg --configure -a || true; \
    else \
      echo "[vm] apt busy and --force-apt not set; aborting" >&2; exit 1; \
    fi; \
  fi; \
  sudo apt-get -o DPkg::Lock::Timeout=600 -y update; \
  sudo apt-get -o DPkg::Lock::Timeout=600 -y install ca-certificates curl gnupg; \
  sudo install -m 0755 -d /etc/apt/keyrings; \
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null; \
  sudo chmod a+r /etc/apt/keyrings/docker.asc; \
  . /etc/os-release; echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null; \
  sudo apt-get -o DPkg::Lock::Timeout=600 -y update; \
  sudo apt-get -o DPkg::Lock::Timeout=600 -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin python3; \
  sudo usermod -aG docker $USER; sudo systemctl enable --now docker'
# Bake iteration count and force flag into the remote command
ITERS=$(( APT_TIMEOUT / 5 ))
[[ $ITERS -lt 1 ]] && ITERS=1
REMOTE_INSTALL=${INSTALL_DOCKER/__APT_ITERS__/$ITERS}
REMOTE_INSTALL=${REMOTE_INSTALL/__FORCE_APT__/$FORCE_APT}
REMOTE_INSTALL=${REMOTE_INSTALL/__DEBUG__/$DEBUG}
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" $IAP_OPT --command "$REMOTE_INSTALL" ${DEBUG:+-- -v}

ROOT_LOCAL=$(pwd)
REPO_NAME=$(basename "$ROOT_LOCAL")

echo "[onepair] Packaging repo and copying tarball to VM ..."
# Create a tar.gz of the current repo, excluding VCS and common large/temp dirs
TMP_TAR=$(mktemp -p "${TMPDIR:-/tmp}" "repo_${REPO_NAME}_XXXXXXXX.tar.gz")
tar -C "$ROOT_LOCAL" \
    --exclude-vcs --exclude='.git' --exclude='gcp-output' --exclude='runs' --exclude='artifacts' \
    --exclude='__pycache__' --exclude='*.pyc' \
    -czf "$TMP_TAR" .
gcloud compute scp "$TMP_TAR" "$INSTANCE_NAME":~/"${REPO_NAME}.tar.gz" --zone="$ZONE" --project="$PROJECT_ID" ${USE_IAP:+--tunnel-through-iap}
rm -f "$TMP_TAR"

echo "[onepair] Extracting repo on VM ..."
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" $IAP_OPT \
  --command "rm -rf ~/${REPO_NAME} && mkdir -p ~/${REPO_NAME} && tar -xzf ~/${REPO_NAME}.tar.gz -C ~/${REPO_NAME} && rm -f ~/${REPO_NAME}.tar.gz"

echo "[onepair] Building CPU training image and running single-pair training ..."
PAIR_ESC=$(printf %q "$PAIR")
REMOTE_CMD="set -euo pipefail; cd ~/${REPO_NAME} && docker compose -f docker/docker-compose.train.cpu.x86.yml build && python3 scripts/train_pairs.py --threads ${THREADS} --concurrency 1 --timerange ${TIMERANGE} --pairs ${PAIR_ESC}"
if [[ -n "$ID_PREFIX" ]]; then
  IDP_ESC=$(printf %q "$ID_PREFIX"); REMOTE_CMD+=" --id-prefix ${IDP_ESC}"
fi
if [[ -n "$ID_SUFFIX" ]]; then
  IDS_ESC=$(printf %q "$ID_SUFFIX"); REMOTE_CMD+=" --id-suffix ${IDS_ESC}"
fi
if [[ "$FRESH" == "1" ]]; then
  REMOTE_CMD+=" --fresh"
fi
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" $IAP_OPT --command "$REMOTE_CMD" ${DEBUG:+-- -v}

echo "[onepair] Packaging artifacts on VM ..."
PACK_CMD="set -euo pipefail; cd ~/${REPO_NAME} && mkdir -p output; rm -f output/freqaimodels.tgz || true; tar -C user_data -czf output/freqaimodels.tgz freqaimodels || true; rm -rf output/freqaimodels; if [[ -d user_data/freqaimodels ]]; then cp -r user_data/freqaimodels output/; fi; rm -f output/logs.tgz || true; tar -C user_data -czf output/logs.tgz logs || true; rm -rf output/logs; if [[ -d user_data/logs ]]; then cp -r user_data/logs output/; fi; echo '[onepair] Artifacts ready under: ' $(pwd)/output"
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" $IAP_OPT --command "$PACK_CMD" ${DEBUG:+-- -v}

LOCAL_OUT_DIR="gcp-output/${INSTANCE_NAME}"
mkdir -p "$LOCAL_OUT_DIR"
echo "[onepair] Fetching artifacts to ${LOCAL_OUT_DIR} ..."
gcloud compute scp --recurse "$INSTANCE_NAME":~/${REPO_NAME}/output/. "$LOCAL_OUT_DIR" --zone="$ZONE" --project="$PROJECT_ID" ${USE_IAP:+--tunnel-through-iap} || echo "[onepair] No artifacts to fetch"

# Optionally install models locally and set the identifier in user_data/config.json
if [[ "$INSTALL_LOCAL" == "1" ]]; then
  # Compute the same identifier scheme used by train_pairs.py
  SNAME=$(printf '%s' "$PAIR" | sed 's/[\/:]/_/g')
  IDENT="${ID_PREFIX}dqn-${SNAME}${ID_SUFFIX}"
  if [[ -d "$LOCAL_OUT_DIR/freqaimodels" || -f "$LOCAL_OUT_DIR/freqaimodels.tgz" ]]; then
    echo "[onepair] Installing models locally and setting identifier: ${IDENT}"
    bash scripts/install_gcp_models.sh --source "$LOCAL_OUT_DIR" --identifier "$IDENT"
  else
    echo "[onepair] No models found under ${LOCAL_OUT_DIR} to install" >&2
  fi
fi

if [[ "$CLEANUP" == "1" ]]; then
  echo "[onepair] Deleting VM ${INSTANCE_NAME} ..."
  gcloud compute instances delete "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --quiet || true
fi

echo "[onepair] Done. Artifacts: ${LOCAL_OUT_DIR}"
