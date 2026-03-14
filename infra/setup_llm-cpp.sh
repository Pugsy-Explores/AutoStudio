#!/usr/bin/env bash
set -euo pipefail

########################################

# CONFIG

########################################

PREFIX="/opt/llama"
SRC_DIR="$PREFIX/src"
BIN_DIR="$PREFIX/bin"

########################################

# LOG

########################################

log() {
echo "[`date '+%H:%M:%S'`] $1"
}

########################################

# INSTALL PACKAGE IF MISSING

########################################

ensure_pkg() {

pkg="$1"

if dpkg -s "$pkg" >/dev/null 2>&1; then
log "$pkg already installed"
else
log "Installing $pkg"
apt-get update
apt-get install -y "$pkg"
fi
}

########################################

# INSTALL BUILD DEPENDENCIES

########################################

install_build_tools() {

ensure_pkg build-essential
ensure_pkg cmake
ensure_pkg git
ensure_pkg pkg-config
ensure_pkg curl
ensure_pkg wget
}

########################################

# DETECT CUDA VERSION

########################################

detect_cuda_version() {

if ! command -v nvcc >/dev/null 2>&1; then
log "CUDA compiler not found"
return 1
fi

CUDA_VERSION=$(nvcc --version | grep release | sed 's/.*release //' | cut -d',' -f1)

CUDA_MAJOR=$(echo "$CUDA_VERSION" | cut -d'.' -f1)

log "Detected CUDA version: $CUDA_VERSION"

echo "$CUDA_MAJOR"
}

########################################

# ADD NVIDIA REPO IF NEEDED

########################################

ensure_cuda_repo() {

if ! apt-cache policy | grep -q developer.download.nvidia.com; then

```
log "Adding NVIDIA CUDA repository"

wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb

dpkg -i cuda-keyring_1.1-1_all.deb

apt-get update
```

else
log "CUDA repo already configured"
fi
}

########################################

# ENSURE CUBLAS DEV LIBS

########################################

ensure_cublas() {

if ldconfig -p | grep -q libcublas; then
log "cuBLAS already installed"
return
fi

CUDA_MAJOR=$(detect_cuda_version)

ensure_cuda_repo

log "Installing cuBLAS for CUDA $CUDA_MAJOR"

apt-get install -y libcublas-${CUDA_MAJOR}-0 libcublas-dev || 
apt-get install -y cuda-libraries-dev
}

########################################

# BUILD LLAMA.CPP

########################################

build_llamacpp() {

mkdir -p "$SRC_DIR"
mkdir -p "$BIN_DIR"

cd "$SRC_DIR"

if [ ! -d "llama.cpp" ]; then
log "Cloning llama.cpp"
git clone https://github.com/ggml-org/llama.cpp.git
fi

cd llama.cpp

log "Building llama.cpp"

rm -rf build
mkdir build
cd build

cmake .. -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release

cmake --build . -j$(nproc)

cp bin/llama-server "$BIN_DIR/"
chmod +x "$BIN_DIR/llama-server"

log "llama-server installed at $BIN_DIR/llama-server"
}

########################################

# MAIN

########################################

log "Installing build tools"
install_build_tools

log "Ensuring cuBLAS libraries"
ensure_cublas

log "Building llama.cpp"
build_llamacpp

echo ""
echo "----------------------------------------"
echo "llama.cpp server ready"
echo ""
echo "Binary:"
echo "$BIN_DIR/llama-server"
echo ""
echo "Example:"
echo "$BIN_DIR/llama-server -m model.gguf -ngl 999 -c 32768 --port 8080"
echo "----------------------------------------"
