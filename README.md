# 3xbot

Telegram bot for managing [3x-ui](https://github.com/MHSanaei/3x-ui) panel accounts. Supports multiple panels, client creation (single & bulk), traffic/duration management, and PDF export with QR codes.

## Features

- **Multi-panel support** — manage multiple 3x-ui panels from one bot
- **Search** — find clients by email across all panels (shows panel picker if found on multiple)
- **Create accounts** — single or bulk (up to 100), with flexible naming schemes
- **Manage clients** — enable/disable, modify traffic & duration, reset usage, remove
- **Bulk operations** — add/subtract days or traffic across all accounts in a panel, filtered by enabled/disabled/all
- **PDF export** — account details with QR codes and subscription links
- **Proxy support** — HTTP/SOCKS proxy for both Telegram and panel connections
- **Protocol support** — VLESS, VMess, Trojan, Shadowsocks

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
allowed_users = [123456789]
# proxy = "socks5://127.0.0.1:1080"  # optional, proxy for Telegram connection

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
| `allowed_users` | List of Telegram user IDs authorized to use the bot |
| `name` | Panel nickname displayed in the bot UI and PDFs |
| `url` | 3x-ui panel URL including base path |
| `sub_url` | Optional subscription server URL |
| `proxy` (bot) | Optional proxy for Telegram connection (`socks5://`, `socks4://`, `http://`) |
| `proxy` (panel) | Optional proxy for panel API connection (`socks5://`, `http://`) |

## Requirements

- Python 3.12+
- A running [3x-ui](https://github.com/MHSanaei/3x-ui) panel with API access
