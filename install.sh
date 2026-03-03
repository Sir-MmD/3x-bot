#!/usr/bin/env bash
set -e

# --- Constants ---
INSTALL_DIR="$HOME/3x-bot"
BIN_NAME="3x-bot"
SERVICE_NAME="3x-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO="Sir-MmD/3x-bot"
CONFIG_FILE="${INSTALL_DIR}/config.toml"
VERSION_FILE="${INSTALL_DIR}/.version"
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
    if [[ ! -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
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

get_installed_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        cat "$VERSION_FILE"
    else
        echo "-"
    fi
}

get_latest_version() {
    local url="https://github.com/${REPO}/releases/latest"
    local redirect

    if command -v curl &>/dev/null; then
        redirect=$(curl -sI "$url" 2>/dev/null | grep -i '^location:' | tr -d '\r')
    elif command -v wget &>/dev/null; then
        redirect=$(wget --spider -S "$url" 2>&1 | grep -i '^\s*location:' | tail -1 | tr -d '\r')
    fi

    if [[ -n "$redirect" ]]; then
        echo "${redirect##*/}"
    else
        echo "-"
    fi
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64)  echo "amd64" ;;
        aarch64|arm64) echo "arm64" ;;
        *)
            error "Unsupported architecture: $(uname -m)"
            exit 1
            ;;
    esac
}

print_banner() {
    local status os_name installed latest
    status=$(get_status)
    os_name=$(get_os_name)
    installed=$(get_installed_version)
    latest=$(get_latest_version)

    local status_color="$RED"
    if [[ "$status" == "Running" ]]; then
        status_color="$GREEN"
    elif [[ "$status" == "Stopped" ]]; then
        status_color="$YELLOW"
    fi

    local version_color="$GREEN"
    if [[ "$installed" != "$latest" && "$installed" != "-" && "$latest" != "-" ]]; then
        version_color="$YELLOW"
    fi

    echo
    print_separator
    local title="3X-BOT Management Script"
    local pad=$(( (WIDTH - ${#title}) / 2 ))
    printf "%${pad}s" ""
    echo -e "${BOLD}${title}${NC}"
    print_separator
    echo -e " OS       : ${CYAN}${os_name}${NC}"
    echo -e " Status   : ${status_color}${status}${NC}"
    echo -e " Installed: ${version_color}${installed}${NC}"
    echo -e " Latest   : ${GREEN}${latest}${NC}"
    print_separator
}

# --- Core Functions ---

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root."
        exit 1
    fi
}

download_binary() {
    local arch latest
    arch=$(detect_arch)
    latest=$(get_latest_version)

    if [[ "$latest" == "-" ]]; then
        error "Could not determine latest version."
        exit 1
    fi

    local url="https://github.com/${REPO}/releases/download/${latest}/${BIN_NAME}-linux-${arch}"

    info "Downloading ${BIN_NAME} ${latest} (${arch})..."
    if command -v curl &>/dev/null; then
        curl -fSL -o "${INSTALL_DIR}/${BIN_NAME}" "$url"
    elif command -v wget &>/dev/null; then
        wget -qO "${INSTALL_DIR}/${BIN_NAME}" "$url"
    else
        error "curl or wget is required."
        exit 1
    fi

    chmod +x "${INSTALL_DIR}/${BIN_NAME}"
    echo "$latest" > "$VERSION_FILE"
}

write_service() {
    info "Writing systemd unit to ${SERVICE_FILE}..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=3x-ui Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/${BIN_NAME}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

# --- Configuration ---

configure_bot() {
    local api_id api_hash token owner proxy

    echo
    print_separator
    echo -e " ${BOLD}Bot Configuration${NC}"
    print_separator
    echo

    # If editing existing config, show current values
    if [[ -f "$CONFIG_FILE" ]]; then
        echo -e " ${CYAN}Leave empty to keep current value.${NC}"
        echo

        local cur_api_id cur_api_hash cur_token cur_owner cur_proxy
        cur_api_id=$(grep -oP '^api_id\s*=\s*\K\S+' "$CONFIG_FILE" 2>/dev/null || echo "")
        cur_api_hash=$(grep -oP '^api_hash\s*=\s*"\K[^"]+' "$CONFIG_FILE" 2>/dev/null || echo "")
        cur_token=$(grep -oP '^token\s*=\s*"\K[^"]+' "$CONFIG_FILE" 2>/dev/null || echo "")
        cur_owner=$(grep -oP '^owner\s*=\s*\K\S+' "$CONFIG_FILE" 2>/dev/null || echo "")
        cur_proxy=$(grep -oP '^proxy\s*=\s*"\K[^"]+' "$CONFIG_FILE" 2>/dev/null || echo "")

        read -rp " API ID [${cur_api_id}]: " api_id < /dev/tty
        read -rp " API Hash [${cur_api_hash}]: " api_hash < /dev/tty
        read -rp " Bot Token [${cur_token}]: " token < /dev/tty
        read -rp " Owner ID [${cur_owner}]: " owner < /dev/tty
        read -rp " Proxy [${cur_proxy:-none}]: " proxy < /dev/tty

        api_id="${api_id:-$cur_api_id}"
        api_hash="${api_hash:-$cur_api_hash}"
        token="${token:-$cur_token}"
        owner="${owner:-$cur_owner}"
        proxy="${proxy:-$cur_proxy}"
    else
        read -rp " API ID: " api_id < /dev/tty
        read -rp " API Hash: " api_hash < /dev/tty
        read -rp " Bot Token: " token < /dev/tty
        read -rp " Owner ID: " owner < /dev/tty
        read -rp " Proxy (leave empty to skip): " proxy < /dev/tty
    fi

    if [[ -z "$api_id" || -z "$api_hash" || -z "$token" || -z "$owner" ]]; then
        error "All fields except proxy are required."
        return 1
    fi

    mkdir -p "$INSTALL_DIR"

    cat > "$CONFIG_FILE" <<EOF
api_id = ${api_id}
api_hash = "${api_hash}"
token = "${token}"
owner = ${owner}
EOF

    if [[ -n "$proxy" ]]; then
        echo "proxy = \"${proxy}\"" >> "$CONFIG_FILE"
    fi

    info "Config saved to ${CONFIG_FILE}"
}

# --- Actions ---

do_install() {
    if [[ -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Already installed at ${INSTALL_DIR}/. Use 'update' to update."
        return 1
    fi

    mkdir -p "$INSTALL_DIR"
    download_binary
    configure_bot

    write_service
    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"

    info "Installation complete!"
    info "${SERVICE_NAME} is now running."
}

do_update() {
    if [[ ! -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Not installed. Run 'install' first."
        return 1
    fi

    local installed latest
    installed=$(get_installed_version)
    latest=$(get_latest_version)

    if [[ "$installed" == "$latest" ]]; then
        info "Already up to date (${installed})."
        return 0
    fi

    info "Updating ${installed} -> ${latest}..."
    systemctl stop "$SERVICE_NAME" || true

    download_binary

    systemctl start "$SERVICE_NAME"

    info "Update complete!"
}

do_config() {
    if [[ ! -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Not installed. Run 'install' first."
        return 1
    fi

    configure_bot || return 1

    echo
    local restart
    read -rp " Restart service to apply? [Y/n]: " restart < /dev/tty
    restart="${restart:-y}"

    if [[ "$restart" =~ ^[Yy]$ ]]; then
        systemctl restart "$SERVICE_NAME"
        info "Service restarted."
    fi
}

do_uninstall() {
    if [[ ! -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Not installed. Nothing to uninstall."
        return 1
    fi

    info "Stopping and disabling ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME" || true
    systemctl disable "$SERVICE_NAME" || true

    info "Removing systemd unit..."
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload

    info "Removing ${INSTALL_DIR}/..."
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
        echo -e "  ${GREEN}3.${NC} Configure"
        echo -e "  ${GREEN}4.${NC} Uninstall"
        print_separator
        echo -e "  ${GREEN}0.${NC} Exit"
        print_separator
        echo

        local choice
        read -rp " Choose [0-4]: " choice < /dev/tty

        echo
        case "$choice" in
            1) do_install   || true ;;
            2) do_update    || true ;;
            3) do_config    || true ;;
            4) do_uninstall || true ;;
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
    config)    do_config    ;;
    uninstall) do_uninstall ;;
    "")        show_menu    ;;
    *)
        error "Unknown command: ${1}"
        echo "Usage: $0 {install|update|config|uninstall}"
        exit 1
        ;;
esac
