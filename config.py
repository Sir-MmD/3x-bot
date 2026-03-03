import tomllib
from pathlib import Path
from urllib.parse import urlparse

import socks
from telethon import TelegramClient

from panel import PanelClient

# ── Config ───────────────────────────────────────────────────────────────────

cfg = tomllib.loads(Path("config.toml").read_text())
bot_cfg = cfg["bot"]

# ── Permissions ───────────────────────────────────────────────────────────

ALL_PERMS = {"search", "create", "modify", "toggle", "remove", "bulk", "pdf"}

admins: dict[int, set[str]] = {}
for a in cfg.get("admins", []):
    admins[a["id"]] = set(a.get("permissions", []))

public_mode: bool = bot_cfg.get("public", False)
public_perms: set[str] = set(bot_cfg.get("public_permissions", []))
force_join: list[str] = bot_cfg.get("force_join", [])


def user_perms(uid: int) -> set[str]:
    """Return resolved permission set for a user."""
    if uid in admins:
        p = admins[uid]
        return ALL_PERMS if "*" in p else p
    if public_mode:
        return ALL_PERMS if "*" in public_perms else public_perms
    return set()


def has_perm(uid: int, perm: str) -> bool:
    """Check if a user has a specific permission."""
    return perm in user_perms(uid)


panels: dict[str, PanelClient] = {}
server_addrs: dict[str, str] = {}
sub_urls: dict[str, str | None] = {}

for pcfg in cfg["panels"]:
    name = pcfg["name"]
    panels[name] = PanelClient(pcfg["url"], pcfg["username"], pcfg["password"], name=name, proxy=pcfg.get("proxy", ""))
    server_addrs[name] = urlparse(pcfg["url"]).hostname
    sub_urls[name] = pcfg.get("sub_url", "").rstrip("/") or None

_proxy_types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}

def _parse_proxy(url: str):
    """Parse a proxy URL into a PySocks tuple for Telethon."""
    p = urlparse(url)
    scheme = p.scheme.lower()
    if scheme not in _proxy_types:
        raise ValueError(f"Unsupported proxy type: {scheme} (use socks5, socks4, or http)")
    return (_proxy_types[scheme], p.hostname, p.port, True, p.username, p.password)

_bot_proxy_url = bot_cfg.get("proxy", "")
_bot_proxy = _parse_proxy(_bot_proxy_url) if _bot_proxy_url else None

bot = TelegramClient("bot", bot_cfg["api_id"], bot_cfg["api_hash"], proxy=_bot_proxy)


def get_panel(name: str) -> PanelClient:
    return panels[name]


# ── State ────────────────────────────────────────────────────────────────────

states: dict[int, dict] = {}


def st(uid: int) -> dict:
    return states.setdefault(uid, {})


def clear(uid: int):
    states.pop(uid, None)
