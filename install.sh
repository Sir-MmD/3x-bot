#!/usr/bin/env bash
set -e

# --- Constants ---
INSTALL_DIR="$HOME/3x-bot"
BIN_NAME="3x-bot"
SERVICE_NAME="3x-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO="Sir-MmD/3x-bot"
CONFIG_FILE="${INSTALL_DIR}/config.toml"
WIDTH=40

# --- State ---
INCLUDE_PRERELEASE=false

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
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
    if [[ -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        local ver
        ver=$("${INSTALL_DIR}/${BIN_NAME}" --version 2>/dev/null | grep -oP 'v\K\S+' || echo "")
        if [[ -n "$ver" ]]; then
            echo "v${ver}"
        else
            echo "-"
        fi
    else
        echo "-"
    fi
}

# Fetch the latest stable release tag via redirect (no JSON parsing needed)
get_stable_version() {
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

# Fetch the newest release tag (including pre-releases) via GitHub API
get_prerelease_version() {
    local json
    if command -v curl &>/dev/null; then
        json=$(curl -s "https://api.github.com/repos/${REPO}/releases?per_page=1" 2>/dev/null)
    elif command -v wget &>/dev/null; then
        json=$(wget -qO- "https://api.github.com/repos/${REPO}/releases?per_page=1" 2>/dev/null)
    fi

    if [[ -n "$json" ]]; then
        local tag
        tag=$(echo "$json" | grep -m1 '"tag_name"' | sed 's/.*"tag_name" *: *"\([^"]*\)".*/\1/')
        [[ -n "$tag" ]] && echo "$tag" || echo "-"
    else
        echo "-"
    fi
}

# Return the target version based on INCLUDE_PRERELEASE flag
get_target_version() {
    if $INCLUDE_PRERELEASE; then
        get_prerelease_version
    else
        get_stable_version
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
    local status os_name installed target
    status=$(get_status)
    os_name=$(get_os_name)
    installed=$(get_installed_version)
    target=$(get_target_version)

    local status_color="$RED"
    if [[ "$status" == "Running" ]]; then
        status_color="$GREEN"
    elif [[ "$status" == "Stopped" ]]; then
        status_color="$YELLOW"
    fi

    local version_color="$GREEN"
    if [[ "$installed" != "$target" && "$installed" != "-" && "$target" != "-" ]]; then
        version_color="$YELLOW"
    fi

    local target_label="Latest"
    if $INCLUDE_PRERELEASE; then
        target_label="Latest ${DIM}(pre)${NC}"
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
    echo -e " ${target_label}: ${GREEN}${target}${NC}"
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
    local arch target
    arch=$(detect_arch)
    target=$(get_target_version)

    if [[ "$target" == "-" ]]; then
        error "Could not determine target version."
        if ! $INCLUDE_PRERELEASE; then
            warn "No stable release found. Try enabling pre-release (option 5)."
        fi
        exit 1
    fi

    local url="https://github.com/${REPO}/releases/download/${target}/${BIN_NAME}-linux-${arch}"

    info "Downloading ${BIN_NAME} ${target} (${arch})..."
    echo
    if command -v curl &>/dev/null; then
        curl -fL --progress-bar -o "${INSTALL_DIR}/${BIN_NAME}" "$url"
    elif command -v wget &>/dev/null; then
        wget --show-progress -qO "${INSTALL_DIR}/${BIN_NAME}" "$url"
    else
        error "curl or wget is required."
        exit 1
    fi
    echo

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

    write_service
    systemctl daemon-reload

    # Run the binary once for interactive config setup (it exits after creating config.toml)
    info "Starting initial configuration..."
    "${INSTALL_DIR}/${BIN_NAME}" || true

    systemctl enable --now "$SERVICE_NAME"

    info "Installation complete!"
    info "${SERVICE_NAME} is now running."
}

do_update() {
    if [[ ! -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Not installed. Run 'install' first."
        return 1
    fi

    local installed target
    installed=$(get_installed_version)
    target=$(get_target_version)

    if [[ "$installed" == "$target" ]]; then
        info "Already up to date (${installed})."
        return 0
    fi

    info "Updating ${installed} -> ${target}..."
    systemctl stop "$SERVICE_NAME" || true

    download_binary

    systemctl start "$SERVICE_NAME"

    # Verify the update
    local new_version
    new_version=$(get_installed_version)
    info "Update complete! Now running ${new_version}."
}

do_config() {
    if [[ ! -f "${INSTALL_DIR}/${BIN_NAME}" ]]; then
        error "Not installed. Run 'install' first."
        return 1
    fi

    # Remove existing config so the binary triggers interactive setup
    if [[ -f "$CONFIG_FILE" ]]; then
        local backup="${CONFIG_FILE}.bak"
        cp "$CONFIG_FILE" "$backup"
        info "Backed up current config to ${backup}"
        rm -f "$CONFIG_FILE"
    fi

    systemctl stop "$SERVICE_NAME" || true
    info "Starting configuration..."
    "${INSTALL_DIR}/${BIN_NAME}" || true
    systemctl start "$SERVICE_NAME"
    info "Service restarted with new config."
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

toggle_prerelease() {
    if $INCLUDE_PRERELEASE; then
        INCLUDE_PRERELEASE=false
        info "Pre-release channel disabled."
    else
        INCLUDE_PRERELEASE=true
        info "Pre-release channel enabled."
    fi
}

# --- Menu ---

show_menu() {
    while true; do
        clear
        print_banner
        echo

        local pre_label="OFF"
        if $INCLUDE_PRERELEASE; then
            pre_label="${GREEN}ON${NC}"
        fi

        echo -e "  ${GREEN}1.${NC} Install"
        echo -e "  ${GREEN}2.${NC} Update"
        echo -e "  ${GREEN}3.${NC} Configure"
        echo -e "  ${GREEN}4.${NC} Uninstall"
        echo -e "  ${GREEN}5.${NC} Pre-release [${pre_label}]"
        print_separator
        echo -e "  ${GREEN}0.${NC} Exit"
        print_separator
        echo

        local choice
        read -rp " Choose [0-5]: " choice < /dev/tty

        echo
        case "$choice" in
            1) do_install        || true ;;
            2) do_update         || true ;;
            3) do_config         || true ;;
            4) do_uninstall      || true ;;
            5) toggle_prerelease || true ;;
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
