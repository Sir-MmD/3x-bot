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
            apt update && apt install -y python3 python3-pip python3-venv git
            ;;
        pacman)
            pacman -Sy --noconfirm python python-pip git
            ;;
        dnf)
            dnf install -y python3 python3-pip git
            ;;
        yum)
            yum install -y python3 python3-pip git
            ;;
    esac
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

# --- Actions ---

do_install() {
    if [[ -d "$INSTALL_DIR" ]]; then
        error "Already installed at ${INSTALL_DIR}. Use 'update' to update."
        return 1
    fi

    detect_distro
    install_deps

    info "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"

    info "Creating virtual environment..."
    python3 -m venv "${INSTALL_DIR}/venv"

    info "Installing Python dependencies..."
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

    if [[ ! -f "${INSTALL_DIR}/config.toml" ]]; then
        info "Copying config.toml.example → config.toml"
        cp "${INSTALL_DIR}/config.toml.example" "${INSTALL_DIR}/config.toml"
    else
        warn "config.toml already exists, skipping"
    fi

    write_service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    info "Installation complete!"
    info "Edit ${INSTALL_DIR}/config.toml, then run: systemctl start ${SERVICE_NAME}"
}

do_update() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        error "Not installed. Run 'install' first."
        return 1
    fi

    info "Stopping ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME"

    info "Pulling latest changes..."
    git -C "$INSTALL_DIR" pull

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
