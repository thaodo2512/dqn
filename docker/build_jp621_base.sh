#!/usr/bin/env bash
set -euo pipefail

# This script assembles a JetPack 6.2.1 (L4T 36.3.0) rootfs and imports it
# into Docker as jetson/jp6.2.1-ml:latest. It mirrors the manual steps
# documented under docs/jetpack-6.2.1-container.md.

WORKDIR=${WORKDIR:-$HOME/jp621-build}
TAG=${TAG:-jetson/jp6.2.1-ml:latest}
JETSON_LINUX_ARCHIVE=${JETSON_LINUX_ARCHIVE:-jetson_linux_r36.3.0_aarch64.tbz2}
SAMPLE_ROOTFS_ARCHIVE=${SAMPLE_ROOTFS_ARCHIVE:-tegra_linux_sample-root-filesystem_r36.3.0_aarch64.tbz2}
JETPACK_BASE_URL=${JETPACK_BASE_URL:-https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/release}

mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "[1/6] Downloading Jetson Linux BSP and sample rootfs ..."
for archive in "$JETSON_LINUX_ARCHIVE" "$SAMPLE_ROOTFS_ARCHIVE"; do
  if [[ ! -f $archive ]]; then
    echo "  Fetching $archive"
    if ! wget "${JETPACK_BASE_URL}/${archive}"; then
      if [[ $archive == jetson_linux_* ]]; then
        echo "  Primary URL failed, attempting capitalized filename"
        ALT_ARCHIVE=${archive^}
        if ! wget "${JETPACK_BASE_URL}/${ALT_ARCHIVE}"; then
          echo "  Alternate URL failed. Download the BSP manually and place it in $WORKDIR." >&2
          exit 1
        fi
      else
        echo "  Download failed. Fetch the archive manually and place it in $WORKDIR." >&2
        exit 1
      fi
    fi
  else
    echo "  Skipping download, found $archive"
  fi
done

if [[ ! -d Linux_for_Tegra ]]; then
  echo "[2/6] Extracting Jetson Linux BSP"
  tar xf "$JETSON_LINUX_ARCHIVE"
else
  echo "[2/6] Reusing existing Linux_for_Tegra directory"
fi

cd Linux_for_Tegra

if [[ ! -d rootfs/etc ]]; then
  echo "[3/6] Expanding sample rootfs (requires sudo)"
  sudo tar xf "../${SAMPLE_ROOTFS_ARCHIVE}" -C rootfs
else
  echo "[3/6] Sample rootfs already expanded"
fi

if [[ ! -f rootfs/etc/nv_tegra_release ]]; then
  echo "[4/7] Applying JetPack binaries (requires sudo)"
  if ! printf 'yes\n' | sudo ./apply_binaries.sh; then
    echo "  Failed to apply JetPack binaries. Manually run './apply_binaries.sh' to inspect the error." >&2
    exit 1
  fi
else
  echo "[4/7] JetPack binaries already applied"
fi

if [[ ! -d rootfs/home/jetson ]]; then
  echo "[5/7] Creating default Jetson user (requires sudo)"
  sudo ./tools/l4t_create_default_user.sh -u jetson -p jetson -n JetsonUser --accept-license
else
  echo "[5/7] Default user already present"
fi

if [[ ! -f rootfs/etc/apt/sources.list.d/nvidia-l4t.list ]]; then
  echo "[6/7] Adding NVIDIA apt repositories"
  sudo tee rootfs/etc/apt/sources.list.d/nvidia-l4t.list >/dev/null <<'APT'
# JetPack 6.2.1 / L4T 36.3 repositories
deb https://repo.download.nvidia.com/jetson/common r36.3 main
deb https://repo.download.nvidia.com/jetson/t234 r36.3 main
APT
  wget -qO - https://repo.download.nvidia.com/jetson/jetson-ota-public.asc | \
    sudo tee rootfs/etc/apt/trusted.gpg.d/jetson-ota-public.asc >/dev/null
else
  echo "[6/7] NVIDIA apt repositories already configured"
fi

echo "[7/7] Importing rootfs into Docker as ${TAG}"
TMP_TAR=$(mktemp --tmpdir jetson-jp621-rootfs-XXXX.tar)
trap 'rm -f "$TMP_TAR"' EXIT

tar --numeric-owner -C rootfs -cf "$TMP_TAR" .
cat "$TMP_TAR" | docker import - "$TAG"

echo "\nDone. Verify with: docker run --rm -it --runtime nvidia ${TAG} bash"
