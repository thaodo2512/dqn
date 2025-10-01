#!/usr/bin/env bash
set -euo pipefail

# End-to-end GCP training orchestrator
# - Creates a VM, installs Docker, copies this repo, runs training via docker compose,
#   fetches artifacts to local, optionally uploads to GCS, and optionally deletes the VM.
#
# Prereqs: gcloud CLI (auth'd), and this script run from the repo root.
#
# Config (override via env):
: "${PROJECT_ID:=}"
: "${ZONE:=asia-south1-c}"
: "${INSTANCE_NAME:=dqn-train-$(date +%Y%m%d-%H%M%S)}"
: "${MACHINE_TYPE:=c4d-standard-16}"
: "${DISK_SIZE_GB:=200}"
: "${DISK_TYPE:=pd-ssd}"
: "${VISIBLE_CORE_COUNT:=}"
: "${SERVICE_ACCOUNT:=}"           # empty = use default compute SA
: "${SCOPES:=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write}"
: "${IMAGE_FAMILY:=ubuntu-2204-lts}"
: "${IMAGE_PROJECT:=ubuntu-os-cloud}"

# Training params
: "${THREADS:=2}"
: "${CONCURRENCY:=8}"
: "${TIMERANGE:=20240101-20250930}"
: "${ID_PREFIX:=}"
: "${ID_SUFFIX:=}"
: "${FRESH:=0}"

# Paths
REPO_LOCAL=${REPO_LOCAL:-$(pwd)}
REPO_NAME=${REPO_NAME:-$(basename "$REPO_LOCAL")}
OUTPUT_LOCAL_DIR=${OUTPUT_LOCAL_DIR:-$(pwd)/gcp-output/${INSTANCE_NAME}}
: "${GCS_BUCKET:=}"  # Optional: gs://bucket/path (no trailing slash)

if [[ -z "$PROJECT_ID" ]]; then
  echo "PROJECT_ID is required" >&2; exit 2
fi

echo "[gcp_e2e] Creating VM: ${INSTANCE_NAME} in ${ZONE} (project ${PROJECT_ID})"
CREATE_ARGS=(
  compute instances create "$INSTANCE_NAME"
  --project "$PROJECT_ID"
  --zone "$ZONE"
  --machine-type "$MACHINE_TYPE"
  --network-interface=network-tier=PREMIUM,subnet=default
  --maintenance-policy=MIGRATE
  --provisioning-model=STANDARD
  --scopes "$SCOPES"
  --image-family "$IMAGE_FAMILY" --image-project "$IMAGE_PROJECT"
  --boot-disk-size=${DISK_SIZE_GB} --boot-disk-type=${DISK_TYPE} --boot-disk-auto-delete
  --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
)
[[ -n "$SERVICE_ACCOUNT" ]] && CREATE_ARGS+=(--service-account "$SERVICE_ACCOUNT")
[[ -n "$VISIBLE_CORE_COUNT" ]] && CREATE_ARGS+=(--visible-core-count "$VISIBLE_CORE_COUNT")

if ! gcloud "${CREATE_ARGS[@]}"; then
  echo "[gcp_e2e] VM creation failed" >&2; exit 1
fi

echo "[gcp_e2e] Waiting for VM to reach RUNNING ..."
for _ in {1..180}; do
  STATUS=$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format='value(status)' || true)
  [[ "$STATUS" == "RUNNING" ]] && break
  sleep 5
done
[[ "$STATUS" == "RUNNING" ]] || { echo "[gcp_e2e] VM did not become RUNNING" >&2; exit 1; }

echo "[gcp_e2e] Installing Docker ..."
INSTALL_CMD='sudo apt-get update -y && sudo apt-get install -y ca-certificates curl gnupg && \
  sudo install -m 0755 -d /etc/apt/keyrings && \
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null && \
  sudo chmod a+r /etc/apt/keyrings/docker.asc && \
  . /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null && \
  sudo apt-get update -y && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin python3 && \
  sudo usermod -aG docker $USER && sudo systemctl enable --now docker'

gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --command "$INSTALL_CMD"

echo "[gcp_e2e] Copying repo to VM ..."
gcloud compute scp --recurse "$REPO_LOCAL" "$INSTANCE_NAME":~/. --zone="$ZONE" --project="$PROJECT_ID"

echo "[gcp_e2e] Running training on VM ..."
REMOTE_RUN=(
  "cd ~/${REPO_NAME} && \n"
  "bash scripts/gcp_vm_run.sh --threads ${THREADS} --concurrency ${CONCURRENCY} --timerange ${TIMERANGE}"
)
[[ -n "$ID_PREFIX" ]] && REMOTE_RUN+=(" --id-prefix $(printf %q "$ID_PREFIX")")
[[ -n "$ID_SUFFIX" ]] && REMOTE_RUN+=(" --id-suffix $(printf %q "$ID_SUFFIX")")
[[ "$FRESH" == "1" ]] && REMOTE_RUN+=(" --fresh")

gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --command "${REMOTE_RUN[*]}"

echo "[gcp_e2e] Fetching artifacts to ${OUTPUT_LOCAL_DIR} ..."
mkdir -p "$OUTPUT_LOCAL_DIR"
gcloud compute scp --recurse "$INSTANCE_NAME":~/${REPO_NAME}/output/. "$OUTPUT_LOCAL_DIR" --zone="$ZONE" --project="$PROJECT_ID" || echo "[gcp_e2e] No artifacts to fetch"

if [[ -n "${GCS_BUCKET}" ]]; then
  echo "[gcp_e2e] Uploading artifacts to ${GCS_BUCKET}/${INSTANCE_NAME} ..."
  if command -v gsutil >/dev/null 2>&1; then
    gsutil -m rsync -r "$OUTPUT_LOCAL_DIR" "${GCS_BUCKET}/${INSTANCE_NAME}"
  else
    echo "[gcp_e2e] gsutil not found; skipping upload" >&2
  fi
fi

if [[ "${CLEANUP:-true}" == "true" ]]; then
  echo "[gcp_e2e] Deleting VM ${INSTANCE_NAME} ..."
  gcloud compute instances delete "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --quiet || true
fi

echo "[gcp_e2e] Done. Local artifacts: ${OUTPUT_LOCAL_DIR}"
