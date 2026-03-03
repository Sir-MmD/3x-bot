#!/usr/bin/env bash
set -e

# --- Constants ---
INSTALL_DIR="/opt/3x-bot"
SERVICE_NAME="3x-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO_URL="https://github.com/Sir-MmD/3x-bot.git"
WIDTH=40

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# --- Helpers ---

info()  { echo -e " ${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e " ${YELLOW}[WARN]${NC} $*"; }
error() { echo -e " ${RED}[ERR]${NC}  $*" >&2; }

print_separator() {
    printf '%0.s─' $(seq 1 "$WIDTH")
    echo
}

get_status() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        echo "Not Installed"
    elif systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "Running"
    else
        echo "Stopped"
    fi
}

get_os_name() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        echo "${PRETTY_NAME:-${ID} ${VERSION_ID:-}}"
    else
        echo "Unknown"
    fi
}

print_banner() {
    local status os_name
    status=$(get_status)
    os_name=$(get_os_name)

    local status_color="$RED"
    if [[ "$status" == "Running" ]]; then
        status_color="$GREEN"
    elif [[ "$status" == "Stopped" ]]; then
        status_color="$YELLOW"
    fi

    echo
    print_separator
    local title="3X-BOT Management Script"
    local pad=$(( (WIDTH - ${#title}) / 2 ))
    printf "%${pad}s" ""
    echo -e "${BOLD}${title}${NC}"
    print_separator
    echo -e " OS      : ${CYAN}${os_name}${NC}"
    echo -e " Status  : ${status_color}${status}${NC}"
    print_separator
}

# --- Core Functions ---

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root."
        exit 1
    fi
}

detect_distro() {
    if [[ ! -f /etc/os-release ]]; then
        error "Cannot detect distro: /etc/os-release not found."
        exit 1
    fi

    # shellcheck source=/dev/null
    . /etc/os-release

    local ids="${ID} ${ID_LIKE:-}"

    for id in $ids; do
        case "$id" in
            debian|ubuntu|mint|pop)
                PKG_MANAGER="apt"
                return
                ;;
            arch|manjaro|endeavouros)
                PKG_MANAGER="pacman"
                return
                ;;
            fedora)
                PKG_MANAGER="dnf"
                return
                ;;
            centos|rhel|rocky|alma)
                PKG_MANAGER="yum"
                return
                ;;
        esac
    done

    error "Unsupported distro: ${ID} (ID_LIKE: ${ID_LIKE:-none})"
    exit 1
}

install_deps() {
    info "Detected package manager: ${PKG_MANAGER}"
    info "Installing system dependencies..."

    case "$PKG_MANAGER" in
        apt)
            apt update && apt install -y python3 python3-pip python3-venv git wget
            ;;
        pacman)
            pacman -Sy --noconfirm python python-pip git wget
            ;;
        dnf)
            dnf install -y python3 python3-pip git wget
            ;;
        yum)
            yum install -y python3 python3-pip git wget
            ;;
    esac
}

# Telethon 1.42 is incompatible with Python 3.13+ (asyncio StreamReader
# raises RuntimeError on concurrent reads).  We need exactly Python 3.12.
PYTHON_BUILD_VERSION="3.12.11"

ensure_python312() {
    if command -v python3.12 &>/dev/null; then
        info "Python 3.12 found: $(python3.12 --version)"
        return
    fi

    info "Python 3.12 not found. Trying package manager..."

    case "$PKG_MANAGER" in
        apt)    apt install -y python3.12 python3.12-venv 2>/dev/null && return || true ;;
        pacman) pacman -Sy --noconfirm python312 2>/dev/null && return || true ;;
        dnf)    dnf install -y python3.12 2>/dev/null && return || true ;;
        yum)    yum install -y python3.12 2>/dev/null && return || true ;;
    esac

    if command -v python3.12 &>/dev/null; then
        info "Python 3.12 installed from packages."
        return
    fi

    info "Package not available. Building Python ${PYTHON_BUILD_VERSION} from source..."

    case "$PKG_MANAGER" in
        apt)
            apt install -y build-essential libssl-dev zlib1g-dev \
                libncurses-dev libffi-dev libsqlite3-dev libreadline-dev libbz2-dev
            ;;
        pacman)
            pacman -Sy --noconfirm base-devel openssl zlib ncurses libffi sqlite readline bzip2
            ;;
        dnf|yum)
            $PKG_MANAGER install -y gcc make openssl-devel zlib-devel \
                ncurses-devel libffi-devel sqlite-devel readline-devel bzip2-devel
            ;;
    esac

    local build_dir="/tmp/python-build-$$"
    mkdir -p "$build_dir"
    cd "$build_dir"

    info "Downloading Python ${PYTHON_BUILD_VERSION}..."
    wget -q "https://www.python.org/ftp/python/${PYTHON_BUILD_VERSION}/Python-${PYTHON_BUILD_VERSION}.tgz"
    tar xzf "Python-${PYTHON_BUILD_VERSION}.tgz"
    cd "Python-${PYTHON_BUILD_VERSION}"

    info "Configuring..."
    ./configure --enable-optimizations --prefix=/usr/local >/dev/null 2>&1

    info "Building (this may take a few minutes)..."
    make -j"$(nproc)" >/dev/null 2>&1

    info "Installing..."
    make altinstall >/dev/null 2>&1

    cd /
    rm -rf "$build_dir"

    if ! command -v python3.12 &>/dev/null; then
        error "Failed to install Python 3.12."
        exit 1
    fi

    info "Python 3.12 built and installed: $(python3.12 --version)"
}

write_service() {
    info "Writing systemd unit to ${SERVICE_FILE}..."
    cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=3x-ui Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/3x-bot
ExecStart=/opt/3x-bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

# --- Configuration ---

configure_bot() {
    local config_file="${INSTALL_DIR}/config.toml"

    echo
    print_separator
    echo -e " ${BOLD}Bot Configuration${NC}"
    print_separator
    echo

    local api_id api_hash token bot_proxy force_join

    read -rp " Telegram API ID: " api_id < /dev/tty
    read -rp " Telegram API Hash: " api_hash < /dev/tty
    read -rp " Bot Token: " token < /dev/tty
    read -rp " Bot Proxy (e.g. socks5://127.0.0.1:1080, leave empty to skip): " bot_proxy < /dev/tty
    read -rp " Force-join channels (comma-separated @handles, leave empty to skip): " force_join < /dev/tty

    cat > "$config_file" <<EOF
[bot]
api_id = ${api_id}
api_hash = "${api_hash}"
token = "${token}"
EOF

    if [[ -n "$bot_proxy" ]]; then
        echo "proxy = \"${bot_proxy}\"" >> "$config_file"
    fi

    if [[ -n "$force_join" ]]; then
        # Format as TOML array: "@ch1, @ch2" -> ["@ch1", "@ch2"]
        local fj_array
        fj_array=$(echo "$force_join" | tr -d ' ' | sed 's/,/", "/g')
        echo "force_join = [\"${fj_array}\"]" >> "$config_file"
    fi

    # ── Admins ──────────────────────────────────────────────────────
    local admin_num=1
    local add_more_admin="y"

    while [[ "$add_more_admin" =~ ^[Yy]$ ]]; do
        echo
        print_separator
        echo -e " ${BOLD}Admin #${admin_num}${NC}"
        print_separator
        echo

        local admin_id admin_perms

        read -rp " Telegram User ID: " admin_id < /dev/tty
        echo -e " ${CYAN}Available permissions: *, search, create, modify, toggle, remove, bulk, pdf${NC}"
        read -rp " Permissions (comma-separated, * for all): " admin_perms < /dev/tty

        # Format as TOML array: "search, create" -> ["search", "create"]
        local perms_array
        perms_array=$(echo "$admin_perms" | tr -d ' ' | sed 's/,/", "/g')

        cat >> "$config_file" <<EOF

[[admins]]
id = ${admin_id}
permissions = ["${perms_array}"]
EOF

        admin_num=$((admin_num + 1))

        echo
        read -rp " Add another admin? [y/N]: " add_more_admin < /dev/tty
        add_more_admin="${add_more_admin:-n}"
    done

    # ── Panels ──────────────────────────────────────────────────────
    local panel_num=1
    local add_more="y"

    while [[ "$add_more" =~ ^[Yy]$ ]]; do
        echo
        print_separator
        echo -e " ${BOLD}Panel #${panel_num}${NC}"
        print_separator
        echo

        local name url username password sub_url proxy

        read -rp " Panel Name: " name < /dev/tty
        read -rp " Panel URL (e.g. https://example.com:2053/path): " url < /dev/tty
        read -rp " Username: " username < /dev/tty
        read -rsp " Password: " password < /dev/tty
        echo
        read -rp " Subscription URL (leave empty to skip): " sub_url < /dev/tty
        read -rp " Proxy (e.g. socks5://127.0.0.1:1080, leave empty to skip): " proxy < /dev/tty

        cat >> "$config_file" <<EOF

[[panels]]
name = "${name}"
url = "${url}"
username = "${username}"
password = "${password}"
EOF

        if [[ -n "$sub_url" ]]; then
            echo "sub_url = \"${sub_url}\"" >> "$config_file"
        fi
        if [[ -n "$proxy" ]]; then
            echo "proxy = \"${proxy}\"" >> "$config_file"
        fi

        panel_num=$((panel_num + 1))

        echo
        read -rp " Add another panel? [y/N]: " add_more < /dev/tty
        add_more="${add_more:-n}"
    done

    info "Config saved to ${config_file}"
}

# --- Actions ---

do_install() {
    if [[ -d "$INSTALL_DIR" ]]; then
        error "Already installed at ${INSTALL_DIR}. Use 'update' to update."
        return 1
    fi

    detect_distro
    install_deps
    ensure_python312

    info "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"

    info "Creating virtual environment..."
    python3.12 -m venv "${INSTALL_DIR}/venv"

    info "Installing Python dependencies..."
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

    if [[ ! -f "${INSTALL_DIR}/config.toml" ]]; then
        configure_bot
    else
        warn "config.toml already exists, skipping"
    fi

    write_service
    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"

    info "Installation complete!"
    info "${SERVICE_NAME} is now running."
}

do_update() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        error "Not installed. Run 'install' first."
        return 1
    fi

    info "Stopping ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME"

    detect_distro
    ensure_python312

    info "Pulling latest changes..."
    git -C "$INSTALL_DIR" pull

    # Recreate venv if not using Python 3.12
    local venv_ver
    venv_ver=$("${INSTALL_DIR}/venv/bin/python" --version 2>/dev/null || echo "")
    if [[ "$venv_ver" != *"3.12"* ]]; then
        info "Recreating venv with Python 3.12..."
        rm -rf "${INSTALL_DIR}/venv"
        python3.12 -m venv "${INSTALL_DIR}/venv"
    fi

    info "Updating Python dependencies..."
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

    info "Starting ${SERVICE_NAME}..."
    systemctl start "$SERVICE_NAME"

    info "Update complete!"
}

do_uninstall() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        error "Not installed. Nothing to uninstall."
        return 1
    fi

    info "Stopping and disabling ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME" || true
    systemctl disable "$SERVICE_NAME" || true

    info "Removing systemd unit..."
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload

    info "Removing ${INSTALL_DIR}..."
    rm -rf "$INSTALL_DIR"

    info "Uninstall complete!"
}

# --- Menu ---

show_menu() {
    while true; do
        clear
        print_banner
        echo
        echo -e "  ${GREEN}1.${NC} Install"
        echo -e "  ${GREEN}2.${NC} Update"
        echo -e "  ${GREEN}3.${NC} Uninstall"
        print_separator
        echo -e "  ${GREEN}0.${NC} Exit"
        print_separator
        echo

        local choice
        read -rp " Choose [0-3]: " choice < /dev/tty

        echo
        case "$choice" in
            1) do_install   || true ;;
            2) do_update    || true ;;
            3) do_uninstall || true ;;
            0) echo; exit 0 ;;
            *) warn "Invalid option: ${choice}" ;;
        esac

        echo
        read -rp " Press Enter to continue..." _ < /dev/tty
    done
}

# --- Main ---

check_root

case "${1:-}" in
    install)   do_install   ;;
    update)    do_update    ;;
    uninstall) do_uninstall ;;
    "")        show_menu    ;;
    *)
        error "Unknown command: ${1}"
        echo "Usage: $0 {install|update|uninstall}"
        exit 1
        ;;
esac
