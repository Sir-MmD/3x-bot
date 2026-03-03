#!/usr/bin/env bash
set -e

# --- Constants ---
INSTALL_DIR="$HOME/3x-bot"
BIN_NAME="3x-bot"
SERVICE_NAME="3x-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO="Sir-MmD/3x-bot"
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

download_binary() {
    local arch
    arch=$(detect_arch)

    local url="https://github.com/${REPO}/releases/latest/download/${BIN_NAME}-linux-${arch}"

    info "Downloading ${BIN_NAME} (${arch})..."
    if command -v curl &>/dev/null; then
        curl -fSL -o "${INSTALL_DIR}/${BIN_NAME}" "$url"
    elif command -v wget &>/dev/null; then
        wget -qO "${INSTALL_DIR}/${BIN_NAME}" "$url"
    else
        error "curl or wget is required."
        exit 1
    fi

    chmod +x "${INSTALL_DIR}/${BIN_NAME}"
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

# --- Actions ---

do_install() {
    if [[ -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Already installed at ${INSTALL_DIR}/. Use 'update' to update."
        return 1
    fi

    mkdir -p "$INSTALL_DIR"
    download_binary

    info "Running initial setup..."
    "${INSTALL_DIR}/${BIN_NAME}" < /dev/tty || true

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

    info "Stopping ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME" || true

    download_binary

    info "Starting ${SERVICE_NAME}..."
    systemctl start "$SERVICE_NAME"

    info "Update complete!"
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
