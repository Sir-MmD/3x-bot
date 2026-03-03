# 3xbot

Telegram bot for managing [3x-ui](https://github.com/MHSanaei/3x-ui) panel accounts. Supports multiple panels, client creation (single & bulk), traffic/duration management, and PDF export with QR codes.

## Features

- **Multi-panel support** — manage multiple 3x-ui panels from one bot
- **Search** — find clients by email across all panels
- **Create accounts** — single or bulk (up to 100), with flexible naming schemes
- **Manage clients** — enable/disable, modify traffic & duration, reset usage, remove
- **Client list** — paginated view of all clients per inbound with stats
- **Bulk operations** — add/subtract days or traffic with multi-select inbound filter
- **PDF export** — account details with QR codes and subscription links
- **Protocol support** — VLESS, VMess, Trojan, Shadowsocks
- **Per-admin permissions** — restrict each admin to specific operations, panels, and inbounds
- **Public mode** — optionally open the bot to everyone with configurable permissions
- **Force join** — require users to join specific channels before using the bot
- **Multi-language** — English, Persian, Russian with per-user selection and RTL PDF support
- **Backup & Restore** — download/upload config + database as ZIP
- **Proxy support** — HTTP/SOCKS proxy for both Telegram and panel connections

## Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Sir-MmD/3x-bot/main/install.sh)
```

Downloads a pre-built static binary (amd64/arm64), runs initial setup, and creates a systemd service. Requires root.

The same script handles **install**, **update**, and **uninstall**.

## Configuration

On first run, the bot prompts for the required configuration:

| Field | Description |
|-------|-------------|
| `api_id` / `api_hash` | Telegram API credentials from [my.telegram.org](https://my.telegram.org) |
| `token` | Bot token from [@BotFather](https://t.me/BotFather) |
| `owner` | Your Telegram user ID (gets full access) |
| `proxy` | Optional proxy for Telegram connection (`socks5://`, `socks4://`, `http://`) |

Everything else (admins, panels, public mode, force-join, permissions) is managed through the **Owner Panel** in the bot UI and stored in the database.

## Permissions

Each admin gets a list of permissions. Use `*` to grant all.

| Permission | Covers |
|-----------|--------|
| `search` | Search user, view details |
| `create` | Create account (single & bulk) |
| `modify` | Modify traffic & duration |
| `toggle` | Enable/disable accounts |
| `remove` | Remove accounts |
| `bulk` | Bulk operations, reset traffic, delete depleted |
| `pdf` | PDF export |

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
| `3x-bot.db` | SQLite database (admins, panels, settings, user preferences) |
| `bot.session` | Telethon session file |
