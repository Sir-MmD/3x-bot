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

## Data Storage

All bot data is stored in `~/3x-bot/`:

| File | Contents |
|------|----------|
| `3x-bot` | Bot binary |
| `config.toml` | API credentials, bot token, owner ID |
| `3x-bot.db` | SQLite database (admins, panels, settings, user preferences) |
| `bot.session` | Telethon session file |
