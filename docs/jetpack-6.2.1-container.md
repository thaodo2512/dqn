# JetPack 6.2.1 Container Base Workflow

JetPack 6.2.1 (L4T 36.3) does not yet ship an `l4t-ml` container on NVIDIA NGC, so our
`docker/Dockerfile.jetson` expects a locally imported base image named
`jetson/jp6.2.1-ml:latest`. Run `docker/build_jp621_base.sh` to build that image with
default settings, or follow the manual steps below to customize the workflow.

```bash
# Build the default JetPack 6.2.1 base image into docker as jetson/jp6.2.1-ml:latest
docker/build_jp621_base.sh
```

> The script requests the Jetson Linux BSP archives directly from NVIDIA's developer
> portal. Ensure you have the proper credentials configured (interactive login or
> cached download token). It also invokes `sudo` during rootfs assembly and pipes `yes`
> to `apply_binaries.sh` to accept NVIDIA's license automatically.

## 1. Prerequisites
- Host running Ubuntu 20.04/22.04 with the NVIDIA Container Toolkit installed.
- `sdkmanager` or direct access to the Jetson Linux R36.3.0 packages via
  https://developer.nvidia.com/embedded/jetpack.
- Sufficient disk space (≈25 GB) and sudo access.

## 2. Download Jetson Linux BSP and Sample Rootfs (manual path)
```bash
mkdir -p ~/jp621 && cd ~/jp621
wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/release/jetson_linux_r36.3.0_aarch64.tbz2
wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v3.0/release/tegra_linux_sample-root-filesystem_r36.3.0_aarch64.tbz2
```
> URLs may require NVIDIA developer credentials; update them if NVIDIA revises the
> JetPack 6.2.1 download paths. If the lowercase filenames return 404, retry with the
> capitalized variant (e.g., `Jetson_Linux_R36.3.0_aarch64.tbz2`) or download manually
> from your NVIDIA account and place the archives in the build directory.

## 3. Assemble the Rootfs
```bash
tar xf jetson_linux_r36.3.0_aarch64.tbz2
cd Linux_for_Tegra
sudo tar xf ../tegra_linux_sample-root-filesystem_r36.3.0_aarch64.tbz2 -C rootfs
printf 'yes\n' | sudo ./apply_binaries.sh
sudo ./tools/l4t_create_default_user.sh -u jetson -p jetson -n JetsonUser --accept-license
```

## 4. Prime the Rootfs for Container Use
Add NVIDIA apt repositories so the container can install JetPack components on demand:
```bash
cat <<'APT' | sudo tee rootfs/etc/apt/sources.list.d/nvidia-l4t.list
# JetPack 6.2.1 / L4T 36.3 repositories
deb https://repo.download.nvidia.com/jetson/common r36.3 main
deb https://repo.download.nvidia.com/jetson/t234 r36.3 main
APT
wget -qO - https://repo.download.nvidia.com/jetson/jetson-ota-public.asc | \
  sudo tee rootfs/etc/apt/trusted.gpg.d/jetson-ota-public.asc
```

Optionally install core ML packages (PyTorch, TensorRT Python bindings, etc.) into the
rootfs using a chroot or by launching `qemu-user-static`. For most setups it is enough
to install them after importing the image (step 6).

## 5. Create the Docker Base Image
```bash
cd ~/jp621/Linux_for_Tegra/rootfs
sudo tar --numeric-owner -cf ../jetson-jp621-rootfs.tar .
cat ../jetson-jp621-rootfs.tar | docker import - jetson/jp6.2.1-ml:latest
```

## 6. Validate the Base
Launch a throwaway container and install CUDA/ML dependencies:
```bash
docker run --rm -it --runtime nvidia jetson/jp6.2.1-ml:latest bash -lc "\
  apt-get update && \
  apt-get install -y nvidia-jetpack python3-pip python3-dev && \
  python3 -m pip install --upgrade pip wheel && \
  pip install --extra-index-url https://developer.download.nvidia.com/compute/redist/jp/v62 \
    torch torchvision\
"
```
Adjust PyTorch versions per NVIDIA's release notes. After this step the tag
`jetson/jp6.2.1-ml:latest` contains the CUDA, cuDNN, TensorRT and PyTorch stack required
by our RL workloads.

## 7. Build the Project Image
From the repository root, build the updated Dockerfile:
```bash
docker build -f docker/Dockerfile.jetson -t freqai-jp621 .
```

If you maintain your own registry, push the intermediate base image (step 5) so others
can reuse it:
```bash
docker tag jetson/jp6.2.1-ml:latest registry.local/jetson/jp6.2.1-ml:latest
docker push registry.local/jetson/jp6.2.1-ml:latest
```

## Notes
- JetPack package versions change; verify the CUDA/cuDNN/TensorRT bundle exactly matches
  the JetPack 6.2.1 release you flashed on-device.
- When NVIDIA publishes an official `l4t-ml:r36.3.x` image, update `BASE_IMAGE` in
  `docker/Dockerfile.jetson` to point to it and drop the custom import step.
- The `jetson/jp6.2.1-ml:latest` name is just a convention; adjust it to fit your
  registry and CI pipeline.
