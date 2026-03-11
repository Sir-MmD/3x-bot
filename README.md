# 3xbot

Telegram bot for managing [3x-ui](https://github.com/MHSanaei/3x-ui) panel accounts. Supports multiple panels, client creation (single & bulk), traffic/duration management, and PDF export with QR codes.

## Features

- **Multi-panel support** — manage multiple 3x-ui panels from one bot
- **Search** — find clients by email across all panels
- **Create accounts** — single or bulk (up to 100), with flexible naming schemes; single create shows full account info, bulk sends TXT summary
- **Manage clients** — enable/disable, modify traffic & duration, reset usage, remove
- **Client list** — paginated view of all clients per inbound with detailed inbound info header (protocol, transport, security, user stats, traffic)
- **Bulk operations** — add/subtract days or traffic, enable/disable all, remove all — across multiple panels with multi-select panel and inbound filter
- **PDF & TXT export** — account details with QR codes and subscription links in PDF or plain text
- **TXT file import** — upload exported TXT files (or plain ID lists) to bulk create or bulk ops flows
- **Protocol support** — VLESS, VMess, Trojan, Shadowsocks
- **Per-admin permissions** — restrict each admin to specific operations, panels, and inbounds
- **Public mode** — optionally open the bot to everyone with configurable permissions
- **Force join** — require users to join channels (public or private) with interactive channel manager
- **Multi-language** — English, Persian, Russian with per-user selection and RTL PDF support
- **Info hints** — contextual ℹ️ help text on every interactive step
- **Account plans** — define reusable plan templates (name, traffic, days, start-after-use) for quick single/bulk account creation
- **Test account** — one-click test account creation with configurable naming, traffic, and duration presets
- **Re-create** — after creating accounts, re-create with the same parameters and a new email/batch
- **Activity logging** — audit trail of all user actions with structured columns (panel, inbound, email, lang) for analytics
- **Admin display names** — admin list shows Telegram names alongside user IDs
- **Backup & Restore** — Bot DB (config + database as ZIP) and Panel DB (per-panel x-ui.db backup/restore) with auto-backup scheduling
- **Manage panel** — edit panel settings, stop/restart Xray from the bot UI
- **Proxy support** — HTTP/SOCKS proxy for both Telegram and panel connections

## Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Sir-MmD/3x-bot/main/install.sh)
```

Downloads a pre-built static binary (amd64/arm64), runs initial setup, and creates a systemd service. Requires root.

The same script handles **install**, **update**, and **uninstall**.

## Configuration

On first run, the bot prompts for the required configuration and saves it to `config.toml`:

```toml
[bot]
api_id = 12345
api_hash = "your_api_hash"
token = "your_bot_token"

[owner]
id = 98765

[proxy]  # optional
type = "socks5"
address = "127.0.0.1"
port = 1080
user = ""
pass = ""
```

| Section | Field | Description |
|---------|-------|-------------|
| `[bot]` | `api_id` / `api_hash` | Telegram API credentials from [my.telegram.org](https://my.telegram.org) |
| `[bot]` | `token` | Bot token from [@BotFather](https://t.me/BotFather) |
| `[owner]` | `id` | Your Telegram user ID (gets full access) |
| `[proxy]` | `type`, `address`, `port`, `user`, `pass` | Optional proxy for Telegram connection (socks5, socks4, http) |

Everything else (admins, panels, public mode, force-join, permissions) is managed through the **Owner Panel** in the bot UI and stored in the database.

> **Upgrading from v0.5.x**: Old flat config.toml files are not compatible. The bot will prompt you to create a new config on startup.

> **v1.0.0**: UI now uses "Account ID" instead of "email". TXT file uploads supported in bulk create and bulk ops.
>
> **v1.1.0**: arm64 builds, `--version` flag, translated duration units, improved install script with version detection.
>
> **v2.0.0**: Database refactored with versioned migrations, dataclasses, JSON serialization, and normalized tables for plans and test accounts. Existing databases are automatically migrated — no manual steps needed. Handler modules split for maintainability (settings, plans, test account, bulk create).

## Permissions

Each admin gets a list of permissions. Use `*` to grant all.

| Permission | Covers |
|-----------|--------|
| `search` | Search user, view full details |
| `search_simple` | Search user, view only status/remaining traffic/time |
| `create` | Create account (single & bulk) |
| `modify` | Modify traffic & duration |
| `toggle` | Enable/disable accounts |
| `remove` | Remove accounts |
| `bulk` | Bulk operations, reset traffic, delete depleted |
| `pdf` | PDF export |
| `manage_panel` | Edit panel settings, stop/restart Xray |

Beyond permissions, you can restrict **which panels** and **which inbounds** each admin can see. The same restrictions are available for public mode users.

## Building from Source

### Automated

The build script installs all dependencies (compiling Python 3.12 from source if needed) and produces a fully static binary:

```bash
git clone https://github.com/Sir-MmD/3x-bot.git && cd 3x-bot
./build.sh
# Output: dist/3x-bot-static
```

### Manual

Requires Python 3.12 (not 3.13+ — Telethon is incompatible).

**1. Install build tools**

```bash
# Debian/Ubuntu
apt install build-essential patchelf libssl-dev zlib1g-dev \
    libncurses-dev libffi-dev libsqlite3-dev libreadline-dev libbz2-dev
```

**2. Create venv and install packages**

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install "setuptools<82" && pip install -r requirements.txt
pip install scons pyinstaller patchelf==0.14.5.0
pip install --no-build-isolation staticx
```

> `setuptools<82` is required because v82 removed `pkg_resources` (used by staticx).
> `patchelf==0.14.5.0` is pinned because v0.16+ causes [assertion errors](https://github.com/JonathonReinhart/staticx/issues/285) with staticx.
> `--no-build-isolation` is needed on arm64 where staticx has no prebuilt wheel.

**3. Strip RUNPATH from Python shared libraries**

```bash
PYTHON_LIB=$(python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
find "$PYTHON_LIB" -name "*.so*" -exec sh -c '
    rpath=$(patchelf --print-rpath "$1" 2>/dev/null)
    [ -n "$rpath" ] && patchelf --remove-rpath "$1"
' _ {} \; 2>/dev/null
```

This prevents staticx from rejecting the binary due to absolute RUNPATH entries in bundled `.so` files.

**4. Build with PyInstaller**

```bash
pyinstaller 3x-bot.spec --noconfirm
```

**5. Patch RUNPATH in PyInstaller's cached libraries and rebuild**

```bash
for lib in ~/.cache/pyinstaller/bincache*/**.so*; do
    rpath=$(patchelf --print-rpath "$lib" 2>/dev/null)
    [ -n "$rpath" ] && patchelf --remove-rpath "$lib"
done 2>/dev/null

rm -rf build/3x-bot dist/3x-bot
pyinstaller 3x-bot.spec --noconfirm
```

**6. Create static binary**

```bash
staticx dist/3x-bot dist/3x-bot-static
```

The output `dist/3x-bot-static` is a ~20MB fully static binary with no runtime dependencies.

## Data Storage

All bot data is stored in `~/3x-bot/`:

| File | Contents |
|------|----------|
| `3x-bot` | Bot binary |
| `config.toml` | API credentials, bot token, owner ID |
| `3x-bot.db` | SQLite database (admins, panels, settings, plans, user profiles, activity log) |
| `bot.session` | Telethon session file |
