import tomllib
from pathlib import Path
from urllib.parse import urlparse

import socks
from telethon import TelegramClient

from panel import PanelClient

# ── Config ───────────────────────────────────────────────────────────────────

cfg = tomllib.loads(Path("config.toml").read_text())

ALL_PERMS = {"search", "create", "modify", "toggle", "remove", "bulk", "pdf"}

owner_id: int = cfg["owner"]


# ── Permissions ──────────────────────────────────────────────────────────────

def is_owner(uid: int) -> bool:
    if uid == owner_id:
        return True
    from db import get_db_admins
    return get_db_admins().get(uid, (set(), False))[1]


def _count_owners() -> int:
    from db import get_db_admins
    count = 1  # config owner always counts
    for _uid, (_perms, _is_owner) in get_db_admins().items():
        if _is_owner and _uid != owner_id:
            count += 1
    return count


def user_perms(uid: int) -> set[str]:
    """Return resolved permission set for a user."""
    if is_owner(uid):
        return ALL_PERMS | {"owner"}
    from db import get_db_admins, get_setting
    db_admins = get_db_admins()
    if uid in db_admins:
        p = db_admins[uid][0]
        return ALL_PERMS if "*" in p else p
    if get_setting("public_mode") == "1":
        pp = get_setting("public_permissions")
        perms = set(pp.split(",")) if pp else set()
        perms.discard("")
        return ALL_PERMS if "*" in perms else perms
    return set()


def has_perm(uid: int, perm: str) -> bool:
    """Check if a user has a specific permission."""
    return perm in user_perms(uid)


def get_force_join() -> list[str]:
    from db import get_setting
    val = get_setting("force_join")
    return [ch.strip() for ch in val.split(",") if ch.strip()]


# ── Panels ───────────────────────────────────────────────────────────────────

panels: dict[str, PanelClient] = {}
server_addrs: dict[str, str] = {}
sub_urls: dict[str, str | None] = {}


def get_panel(name: str) -> PanelClient:
    return panels[name]


def register_panel(name: str, url: str, username: str, password: str,
                   proxy: str = "", sub_url: str = ""):
    """Register a panel at runtime."""
    panels[name] = PanelClient(url, username, password, name=name, proxy=proxy)
    server_addrs[name] = urlparse(url).hostname
    sub_urls[name] = sub_url.rstrip("/") or None


def unregister_panel(name: str) -> PanelClient | None:
    """Remove a panel from runtime. Returns the PanelClient for cleanup."""
    client = panels.pop(name, None)
    server_addrs.pop(name, None)
    sub_urls.pop(name, None)
    return client


def load_db_panels():
    """Load DB panels into runtime (called at startup)."""
    from db import get_db_panels
    for p in get_db_panels():
        if p["name"] in panels:
            continue
        register_panel(
            p["name"], p["url"], p["username"], p["password"],
            p.get("proxy", ""), p.get("sub_url", ""),
        )


# ── Bot ──────────────────────────────────────────────────────────────────────

_proxy_types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}


def _parse_proxy(url: str):
    """Parse a proxy URL into a PySocks tuple for Telethon."""
    p = urlparse(url)
    scheme = p.scheme.lower()
    if scheme not in _proxy_types:
        raise ValueError(f"Unsupported proxy type: {scheme} (use socks5, socks4, or http)")
    return (_proxy_types[scheme], p.hostname, p.port, True, p.username, p.password)


_bot_proxy_url = cfg.get("proxy", "")
_bot_proxy = _parse_proxy(_bot_proxy_url) if _bot_proxy_url else None

bot = TelegramClient("bot", cfg["api_id"], cfg["api_hash"], proxy=_bot_proxy)


# ── State ────────────────────────────────────────────────────────────────────

states: dict[int, dict] = {}


def st(uid: int) -> dict:
    return states.setdefault(uid, {})


def clear(uid: int):
    states.pop(uid, None)
