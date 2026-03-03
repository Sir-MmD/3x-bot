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

## Quick Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/Sir-MmD/3x-bot/main/install.sh)
```

This launches an interactive menu to install, update, or uninstall the bot. The installer will guide you through configuration. Requires root.

## Manual Setup

```bash
pip install -r requirements.txt
```

Copy and edit the config file:

```bash
cp config.toml.example config.toml
```

```toml
[bot]
api_id = 123456
api_hash = "your_api_hash"
token = "your_bot_token"
# proxy = "socks5://127.0.0.1:1080"  # optional
# public = true                       # allow everyone to use the bot
# public_permissions = ["search", "pdf"]
# force_join = ["@channel1", "@channel2"]

[[admins]]
id = 123456789
permissions = ["*"]  # all permissions

# [[admins]]
# id = 987654321
# permissions = ["search", "create", "pdf"]

[[panels]]
name = "Panel1"
url = "https://panel1.example.com:9092/path"
username = "admin"
password = "secret"
# sub_url = "https://sub.example.com/sub"  # optional
# proxy = "socks5://127.0.0.1:1080"  # optional, proxy for panel connection
```

Run:

```bash
python bot.py
```

## Configuration

| Field | Description |
|-------|-------------|
| `api_id` / `api_hash` | Telegram API credentials from [my.telegram.org](https://my.telegram.org) |
| `token` | Bot token from [@BotFather](https://t.me/BotFather) |
| `proxy` (bot) | Optional proxy for Telegram connection (`socks5://`, `socks4://`, `http://`) |
| `public` | Set `true` to allow everyone to use the bot (default `false`) |
| `public_permissions` | Permissions granted to all users in public mode |
| `force_join` | List of channels users must join before using the bot |
| `[[admins]]` `id` | Telegram user ID of an admin |
| `[[admins]]` `permissions` | List of permission strings (or `["*"]` for all) |
| `name` | Panel nickname displayed in the bot UI and PDFs |
| `url` | 3x-ui panel URL including base path |
| `sub_url` | Optional subscription server URL |
| `proxy` (panel) | Optional proxy for panel API connection (`socks5://`, `http://`) |

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

The bot supports **English**, **Persian (فارسی)**, and **Russian (Русский)**. Each user picks their language on first interaction, and the preference is stored in a local SQLite database (`users.db`).

- Language picker is shown before the force-join check on first use
- Users can change their language anytime via the **🌐 Language** button in the main menu
- All bot messages, button labels, and PDF labels are translated
- Persian PDFs use RTL text shaping (via `uharfbuzz`) and the Vazirmatn font
- Russian PDFs use the NotoSans font for Cyrillic support

To add a new language, create `translations/<code>.toml` with all the same keys as `en.toml`, and add the language to `LANGUAGES` in `i18n.py`.

## Project Structure

```
bot.py              Entry point — registers handlers and runs the bot
config.py           Config loading, bot instance, panels, state management
db.py               SQLite user settings (language preference)
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
├── owner.py        Owner panel: manage admins, panels, settings
└── router.py       Text input dispatcher (routes by state prefix)
```

## Requirements

- **Python 3.12** (not 3.13+ — Telethon is incompatible with Python 3.13's asyncio changes)
- A running [3x-ui](https://github.com/MHSanaei/3x-ui) panel with API access

The install script automatically installs Python 3.12 (from package manager or builds from source).
