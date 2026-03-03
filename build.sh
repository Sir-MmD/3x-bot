#!/usr/bin/env bash
set -e

# Build a fully static 3x-bot binary (no dependencies, not even glibc).
# Automatically installs all required build tools and Python packages.

PYTHON_BUILD_VERSION="3.12.11"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }

# ── Detect distro & package manager ──────────────────────────────────

detect_pkg_manager() {
    if [[ ! -f /etc/os-release ]]; then
        error "Cannot detect distro: /etc/os-release not found."
    fi

    # shellcheck source=/dev/null
    . /etc/os-release

    for id in ${ID} ${ID_LIKE:-}; do
        case "$id" in
            debian|ubuntu|mint|pop) PKG_MANAGER="apt";    return ;;
            arch|manjaro|endeavouros) PKG_MANAGER="pacman"; return ;;
            fedora)                   PKG_MANAGER="dnf";    return ;;
            centos|rhel|rocky|alma)   PKG_MANAGER="yum";    return ;;
        esac
    done

    error "Unsupported distro: ${ID} (ID_LIKE: ${ID_LIKE:-none})"
}

# ── Install system build dependencies ────────────────────────────────

install_build_deps() {
    info "Installing system build dependencies..."

    case "$PKG_MANAGER" in
        apt)
            sudo apt update -qq
            sudo apt install -y -qq build-essential libssl-dev zlib1g-dev \
                libncurses-dev libffi-dev libsqlite3-dev libreadline-dev libbz2-dev \
                patchelf wget >/dev/null
            ;;
        pacman)
            sudo pacman -Sy --noconfirm --needed base-devel openssl zlib ncurses \
                libffi sqlite readline bzip2 patchelf wget >/dev/null
            ;;
        dnf)
            sudo dnf install -y -q gcc make openssl-devel zlib-devel \
                ncurses-devel libffi-devel sqlite-devel readline-devel bzip2-devel \
                patchelf wget
            ;;
        yum)
            sudo yum install -y -q gcc make openssl-devel zlib-devel \
                ncurses-devel libffi-devel sqlite-devel readline-devel bzip2-devel \
                patchelf wget
            ;;
    esac
}

# ── Ensure Python 3.12 ──────────────────────────────────────────────

ensure_python312() {
    if command -v python3.12 &>/dev/null; then
        info "Python 3.12 found: $(python3.12 --version)"
        PYTHON=python3.12
        return
    fi

    info "Python 3.12 not found. Trying package manager..."

    case "$PKG_MANAGER" in
        apt)    sudo apt install -y -qq python3.12 python3.12-venv 2>/dev/null || true ;;
        pacman) sudo pacman -Sy --noconfirm python312 2>/dev/null || true ;;
        dnf)    sudo dnf install -y -q python3.12 2>/dev/null || true ;;
        yum)    sudo yum install -y -q python3.12 2>/dev/null || true ;;
    esac

    if command -v python3.12 &>/dev/null; then
        info "Python 3.12 installed from packages."
        PYTHON=python3.12
        return
    fi

    info "Package not available. Building Python ${PYTHON_BUILD_VERSION} from source..."

    local build_dir="/tmp/python-build-$$"
    mkdir -p "$build_dir" && cd "$build_dir"

    wget -q "https://www.python.org/ftp/python/${PYTHON_BUILD_VERSION}/Python-${PYTHON_BUILD_VERSION}.tgz"
    tar xzf "Python-${PYTHON_BUILD_VERSION}.tgz"
    cd "Python-${PYTHON_BUILD_VERSION}"

    info "Configuring..."
    ./configure --enable-optimizations --prefix=/usr/local >/dev/null 2>&1

    info "Building (this may take a few minutes)..."
    make -j"$(nproc)" >/dev/null 2>&1

    info "Installing..."
    sudo make altinstall >/dev/null 2>&1

    cd /
    rm -rf "$build_dir"

    command -v python3.12 &>/dev/null || error "Failed to install Python 3.12."
    info "Python 3.12 built and installed: $(python3.12 --version)"
    PYTHON=python3.12
}

# ── Setup venv & pip packages ────────────────────────────────────────

setup_venv() {
    if [[ ! -d "venv" ]]; then
        info "Creating virtual environment..."
        $PYTHON -m venv venv
    fi

    # shellcheck source=/dev/null
    source venv/bin/activate

    info "Installing Python packages..."
    pip install -q "setuptools<82"
    pip install -q -r requirements.txt
    pip install -q scons pyinstaller "patchelf==0.14.5.0"
    pip install -q --no-build-isolation staticx
}

# ── Strip RUNPATH from Python .so files ──────────────────────────────

strip_runpaths() {
    info "Stripping RUNPATH from Python shared libraries..."

    local python_lib
    python_lib=$(python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")

    find "$python_lib" -name "*.so*" -exec sh -c '
        rpath=$(patchelf --print-rpath "$1" 2>/dev/null)
        [ -n "$rpath" ] && patchelf --remove-rpath "$1"
    ' _ {} \; 2>/dev/null || true
}

# ── Build pipeline ───────────────────────────────────────────────────

build() {
    info "[1/4] Building with PyInstaller..."
    pyinstaller 3x-bot.spec --noconfirm 2>&1 | tail -1

    info "[2/4] Patching RUNPATH in cached libraries..."
    for lib in ~/.cache/pyinstaller/bincache*/**.so*; do
        rpath=$(patchelf --print-rpath "$lib" 2>/dev/null) || true
        if [[ -n "$rpath" ]]; then
            patchelf --remove-rpath "$lib"
        fi
    done 2>/dev/null || true

    info "[3/4] Rebuilding with patched libraries..."
    rm -rf build/3x-bot dist/3x-bot
    pyinstaller 3x-bot.spec --noconfirm 2>&1 | tail -1

    info "[4/4] Creating static binary..."
    staticx dist/3x-bot dist/3x-bot-static 2>/dev/null

    local size
    size=$(du -h dist/3x-bot-static | cut -f1)
    echo
    info "Done! dist/3x-bot-static (${size}, fully static)"
}

# ── Main ─────────────────────────────────────────────────────────────

cd "$(dirname "$0")"

detect_pkg_manager
install_build_deps
ensure_python312
setup_venv
strip_runpaths
build
