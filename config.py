import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

VERSION = "1.0.0"
AUTHOR = "Sir.MmD"

import socks
from telethon import TelegramClient

from panel import PanelClient

# ── Paths ────────────────────────────────────────────────────────────────────

DATA_DIR = Path.home() / "3x-bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)
_CONFIG_PATH = DATA_DIR / "config.toml"

# ── Config ───────────────────────────────────────────────────────────────────


def _validate_config(cfg: dict) -> bool:
    """Return True if config has the expected section structure."""
    bot_sec = cfg.get("bot", {})
    owner_sec = cfg.get("owner", {})
    return (
        isinstance(bot_sec, dict) and "api_id" in bot_sec
        and "api_hash" in bot_sec and "token" in bot_sec
        and isinstance(owner_sec, dict) and "id" in owner_sec
    )


def _read_config() -> dict:
    """Read and parse config.toml, or run interactive setup if missing/corrupt."""
    if _CONFIG_PATH.exists():
        try:
            cfg = tomllib.loads(_CONFIG_PATH.read_text())
            if _validate_config(cfg):
                return cfg
            print("\n[ERR] config.toml has old or invalid format.")
            print(f"      Path: {_CONFIG_PATH}")
            resp = input("      Create a new config? [y/N]: ").strip().lower()
            if resp != "y":
                sys.exit(1)
        except Exception as e:
            print(f"\n[ERR] config.toml is corrupt: {e}")
            print(f"      Path: {_CONFIG_PATH}")
            resp = input("      Create a new config? [y/N]: ").strip().lower()
            if resp != "y":
                sys.exit(1)

    # Interactive setup
    print("\n── 3x-bot Setup ──────────────────────────")
    print(f"Config will be saved to: {_CONFIG_PATH}\n")

    api_id = input("  Telegram API ID: ").strip()
    api_hash = input("  Telegram API Hash: ").strip()
    token = input("  Bot Token: ").strip()
    owner = input("  Owner Telegram User ID: ").strip()

    if not api_id or not api_hash or not token or not owner:
        print("\n[ERR] All fields except proxy are required.")
        sys.exit(1)

    try:
        int(api_id)
        int(owner)
    except ValueError:
        print("\n[ERR] API ID and Owner must be numbers.")
        sys.exit(1)

    print("\n  ── Proxy (optional, press Enter to skip) ──")
    proxy_type = input("  Proxy type (socks5/socks4/http) [skip]: ").strip().lower()

    proxy_section = ""
    if proxy_type in ("socks5", "socks4", "http"):
        proxy_addr = input("  Proxy address (IP/hostname): ").strip()
        proxy_port = input("  Proxy port: ").strip()
        proxy_user = input("  Proxy username (Enter to skip): ").strip()
        proxy_pass = input("  Proxy password (Enter to skip): ").strip()
        if not proxy_addr or not proxy_port:
            print("  [WARN] Proxy address/port required. Skipping proxy.")
        else:
            proxy_section = f"""
[proxy]
type = "{proxy_type}"
address = "{proxy_addr}"
port = {proxy_port}
user = "{proxy_user}"
pass = "{proxy_pass}"
"""

    content = f"""[bot]
api_id = {api_id}
api_hash = "{api_hash}"
token = "{token}"

[owner]
id = {owner}
{proxy_section}"""

    _CONFIG_PATH.write_text(content)
    print(f"\n[OK] Config saved to {_CONFIG_PATH}\n")

    return tomllib.loads(_CONFIG_PATH.read_text())


cfg = _read_config()

ALL_PERMS = {"search", "search_simple", "create", "modify", "toggle", "remove", "bulk", "pdf"}

owner_id: int = cfg["owner"]["id"]


# ── Permissions ──────────────────────────────────────────────────────────────

def is_owner(uid: int) -> bool:
    if uid == owner_id:
        return True
    from db import get_db_admins
    return get_db_admins().get(uid, (set(), False, {"*"}, {}))[1]


def _count_owners() -> int:
    from db import get_db_admins
    count = 1  # config owner always counts
    for _uid, (_perms, _is_owner, _panels, _inbounds) in get_db_admins().items():
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


def user_panels(uid: int) -> set[str] | None:
    """Return the set of panel names a user may access, or None for 'all'."""
    if is_owner(uid):
        return None  # owners see everything
    from db import get_db_admins, get_setting
    db_admins = get_db_admins()
    if uid in db_admins:
        ap = db_admins[uid][2]
        return None if "*" in ap else ap
    if get_setting("public_mode") == "1":
        pp = get_setting("public_panels", "*")
        pset = set(pp.split(",")) if pp else {"*"}
        pset.discard("")
        return None if "*" in pset else pset
    return set()


def user_inbounds(uid: int, panel_name: str) -> set[int] | None:
    """Return allowed inbound IDs for a user on a panel, or None for 'all'."""
    if is_owner(uid):
        return None
    from db import get_db_admins, get_setting, _parse_inbounds_json
    db_admins = get_db_admins()
    if uid in db_admins:
        ib_map = db_admins[uid][3]
        return ib_map.get(panel_name)  # None = all (panel not listed)
    if get_setting("public_mode") == "1":
        raw = get_setting("public_inbounds", "{}")
        ib_map = _parse_inbounds_json(raw)
        return ib_map.get(panel_name)  # None = all
    return None


def visible_inbounds(uid: int, panel_name: str, inbounds: list[dict]) -> list[dict]:
    """Filter inbound list by user's inbound access on a panel."""
    allowed = user_inbounds(uid, panel_name)
    if allowed is None:
        return inbounds
    return [ib for ib in inbounds if ib["id"] in allowed]


def visible_panels(uid: int) -> dict:
    """Return the panels dict filtered by user's panel access."""
    allowed = user_panels(uid)
    if allowed is None:
        return dict(panels)
    return {n: p for n, p in panels.items() if n in allowed}


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


def reload_panels():
    """Re-populate panels dict in DB sort order (dict preserves insertion order)."""
    from db import get_db_panels
    old = dict(panels)
    panels.clear()
    server_addrs.clear()
    sub_urls.clear()
    for p in get_db_panels():
        name = p["name"]
        if name in old:
            panels[name] = old[name]
            server_addrs[name] = urlparse(p["url"]).hostname
            sub_urls[name] = p.get("sub_url", "").rstrip("/") or None
        else:
            register_panel(
                name, p["url"], p["username"], p["password"],
                p.get("proxy", ""), p.get("sub_url", ""),
            )


# ── Bot ──────────────────────────────────────────────────────────────────────

_proxy_types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}


def _parse_proxy(proxy_cfg: dict):
    """Parse a proxy config dict into a PySocks tuple for Telethon."""
    scheme = proxy_cfg.get("type", "").lower()
    if scheme not in _proxy_types:
        raise ValueError(f"Unsupported proxy type: {scheme} (use socks5, socks4, or http)")
    addr = proxy_cfg.get("address", "")
    port = int(proxy_cfg.get("port", 0))
    user = proxy_cfg.get("user", "") or None
    pwd = proxy_cfg.get("pass", "") or None
    return (_proxy_types[scheme], addr, port, True, user, pwd)


_bot_proxy_cfg = cfg.get("proxy", {})
_bot_proxy = _parse_proxy(_bot_proxy_cfg) if _bot_proxy_cfg.get("type") else None

bot = TelegramClient(str(DATA_DIR / "bot"), cfg["bot"]["api_id"], cfg["bot"]["api_hash"], proxy=_bot_proxy)


# ── State ────────────────────────────────────────────────────────────────────

restart_requested: int | None = None  # set to uid to restart
states: dict[int, dict] = {}


def st(uid: int) -> dict:
    return states.setdefault(uid, {})


def clear(uid: int):
    states.pop(uid, None)


# ── Auto-Backup ─────────────────────────────────────────────────────────────

import asyncio

_auto_backup_task: asyncio.Task | None = None


def _get_owner_uids() -> list[int]:
    """Return all owner user IDs (config owner + DB owners)."""
    from db import get_db_admins
    uids = [owner_id]
    for uid, (_perms, _is_owner, _panels, _ib) in get_db_admins().items():
        if _is_owner and uid != owner_id:
            uids.append(uid)
    return uids


async def _auto_backup_loop(interval: int):
    """Background loop that sends backups to all owners every `interval` seconds."""
    import io
    import zipfile
    from datetime import datetime
    from i18n import t
    while True:
        await asyncio.sleep(interval)
        try:
            now = datetime.now()
            stamp = now.strftime("%Y-%m-%d_%H-%M")
            buf = io.BytesIO()
            db_path = DATA_DIR / "3x-bot.db"
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                if _CONFIG_PATH.exists():
                    zf.write(_CONFIG_PATH, "config.toml")
                if db_path.exists():
                    zf.write(db_path, "3x-bot.db")
            buf.seek(0)
            buf.name = f"3x-bot-backup-{stamp}.zip"
            caption = t("auto_backup_caption", 0,
                        date=now.strftime("%Y/%m/%d"),
                        time=now.strftime("%H:%M"))
            for uid in _get_owner_uids():
                try:
                    buf.seek(0)
                    await bot.send_file(uid, buf, caption=caption)
                except Exception:
                    pass
        except Exception:
            pass


def start_auto_backup(interval: int):
    """Start or restart the auto-backup background task."""
    global _auto_backup_task
    stop_auto_backup()
    _auto_backup_task = asyncio.ensure_future(_auto_backup_loop(interval))


def stop_auto_backup():
    """Cancel the auto-backup task if running."""
    global _auto_backup_task
    if _auto_backup_task is not None:
        _auto_backup_task.cancel()
        _auto_backup_task = None
