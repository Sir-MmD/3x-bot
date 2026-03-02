# 3xbot

Telegram bot for managing [3x-ui](https://github.com/MHSanaei/3x-ui) panel accounts. Supports multiple panels, client creation (single & bulk), traffic/duration management, and PDF export with QR codes.

## Features

- **Multi-panel support** — manage multiple 3x-ui panels from one bot
- **Search** — find clients by email across all panels
- **Create accounts** — single or bulk (up to 100), with flexible naming schemes
- **Manage clients** — enable/disable, modify traffic & duration, reset usage, remove
- **PDF export** — account details with QR codes and subscription links
- **Protocol support** — VLESS, VMess, Trojan, Shadowsocks

## Setup

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

[[panels]]
name = "Panel1"
url = "https://panel1.example.com:9092/path"
username = "admin"
password = "secret"
# sub_url = "https://sub.example.com/sub"  # optional

[[panels]]
name = "Panel2"
url = "https://panel2.example.com:9092/path"
username = "admin"
password = "secret"
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

## Requirements

- Python 3.12+
- A running [3x-ui](https://github.com/MHSanaei/3x-ui) panel with API access
