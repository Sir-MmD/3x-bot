# 3xbot

Telegram bot for managing [3x-ui](https://github.com/MHSanaei/3x-ui) panel accounts. Supports multiple panels, client creation (single & bulk), traffic/duration management, and PDF export with QR codes.

## Features

- **Multi-panel support** — manage multiple 3x-ui panels from one bot
- **Search** — find clients by email across all panels (shows panel picker if found on multiple)
- **Create accounts** — single or bulk (up to 100), with flexible naming schemes
- **Manage clients** — enable/disable, modify traffic & duration, reset usage, remove
- **Panel sub-menu** — click a panel to access its inbound list and bulk operations
- **Client list** — click an inbound to see a paginated text list of all clients with email, status, traffic usage, and remaining duration
- **Per-inbound actions** — add client, bulk add, reset all traffic, and delete depleted clients directly from the client list
- **Bulk operations** — add/subtract days or traffic with multi-select inbound filter, then filtered by enabled/disabled/all
- **PDF export** — account details with QR codes and subscription links
- **Proxy support** — HTTP/SOCKS proxy for both Telegram and panel connections
- **Protocol support** — VLESS, VMess, Trojan, Shadowsocks
- **Per-admin permissions** — grant each admin specific operation permissions
- **Public mode** — optionally open the bot to everyone with configurable default permissions
- **Force join** — require users to join specific channels before using the bot
- **Multi-language** — English, Persian (فارسی), Russian (Русский) with per-user language selection; PDFs render in the user's chosen language with RTL support for Persian
- **Backup & Restore** — download a timestamped ZIP of config + database, restore by uploading it back
- **Restart** — restart the bot from the owner panel without touching the server

## Quick Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Sir-MmD/3x-bot/main/install.sh)
```

This launches an interactive menu to install, update, or uninstall the bot. The installer will guide you through configuration. Requires root.

## Manual Setup

```bash
pip install -r requirements.txt
python bot.py
```

On first run, the bot will prompt you for the required configuration (API ID, API Hash, Bot Token, Owner ID, and optional proxy). The config file is saved to `~/3x-bot/config.toml`. All data (config, database, session) is stored in `~/3x-bot/`.

You can also create the config file manually:

```toml
api_id = 123456
api_hash = "your_api_hash"
token = "your_bot_token"
owner = 123456789
# proxy = "socks5://127.0.0.1:1080"  # optional
```

Panels and admins are managed at runtime through the bot's **Owner Panel** — no need to edit config files.

## Configuration

The config file (`~/3x-bot/config.toml`) only needs 4 fields:

| Field | Description |
|-------|-------------|
| `api_id` / `api_hash` | Telegram API credentials from [my.telegram.org](https://my.telegram.org) |
| `token` | Bot token from [@BotFather](https://t.me/BotFather) |
| `owner` | Your Telegram user ID (gets full access) |
| `proxy` | Optional proxy for Telegram connection (`socks5://`, `socks4://`, `http://`) |

Everything else (admins, panels, public mode, force-join, permissions) is managed through the **Owner Panel** in the bot UI and stored in the database.

## Permissions

Each admin gets a list of permissions. Use `*` to grant all permissions.

| Permission | Covers |
|-----------|--------|
| `search` | Search user, view details |
| `create` | Create account (single & bulk), view panel/inbound/client list |
| `modify` | Modify traffic & duration |
| `toggle` | Enable/disable accounts |
| `remove` | Remove accounts |
| `bulk` | Bulk operations (add/subtract days/traffic), reset all traffic, delete depleted, view panel/inbound/client list |
| `pdf` | PDF export |
| `*` | All of the above |

Admins always bypass force-join checks. In public mode, non-admin users get `public_permissions` and must pass force-join (if configured).

## Language Support

The bot supports **English**, **Persian (فارسی)**, and **Russian (Русский)**. Each user picks their language on first interaction, and the preference is stored in the database.

- Language picker is shown before the force-join check on first use
- Users can change their language anytime via the **🌐 Language** button in the main menu
- All bot messages, button labels, and PDF labels are translated
- Persian PDFs use RTL text shaping (via `uharfbuzz`) and the Vazirmatn font
- Russian PDFs use the NotoSans font for Cyrillic support

To add a new language, create `translations/<code>.toml` with all the same keys as `en.toml`, and add the language to `LANGUAGES` in `i18n.py`.

## Project Structure

```
bot.py              Entry point — registers handlers and runs the bot
config.py           Config loading, interactive setup, bot instance, panels, state
db.py               SQLite database (users, admins, panels, settings)
i18n.py             Translation loader and t() lookup function
helpers.py          Formatting, QR, auth, reply, client dict builder
panel.py            3x-ui API client and proxy link generation
pdf_export.py       PDF generation with QR codes and RTL support
translations/       TOML translation files (en, fa, ru)
fonts/              Unicode TTF fonts for PDF rendering
handlers/
├── menu.py         /start, back-to-main, language picker
├── search.py       Search, enable/disable, remove, PDF export
├── modify.py       Modify traffic & duration
├── create.py       Single & bulk account creation
├── inbounds.py     Panel sub-menu, inbound list, client list, reset/delete actions
├── bulk_ops.py     Bulk operations with inbound multi-select
├── owner.py        Owner panel: admins, panels, settings, backup/restore, restart
└── router.py       Text/document input dispatcher (routes by state prefix)
```

## Data Storage

All bot data is stored in `~/3x-bot/`:

| File | Contents |
|------|----------|
| `config.toml` | API credentials, bot token, owner ID |
| `3x-bot.db` | SQLite database (admins, panels, settings, user preferences) |
| `bot.session` | Telethon session file |

On update, the install script automatically migrates data from old locations (`/opt/3x-bot/`, `/etc/3x-bot/`).

## Requirements

- **Python 3.12** (not 3.13+ — Telethon is incompatible with Python 3.13's asyncio changes)
- A running [3x-ui](https://github.com/MHSanaei/3x-ui) panel with API access

The install script automatically installs Python 3.12 (from package manager or builds from source).
