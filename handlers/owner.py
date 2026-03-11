import re

from telethon import events, Button

from config import (
    st, clear, is_owner, ALL_PERMS, owner_id, panels,
    VERSION,
)
from db import get_db_admins, get_setting, get_all_user_profiles
from helpers import auth, reply
from i18n import t


_PERM_LIST = sorted(ALL_PERMS)
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _require_owner(func):
    async def wrapper(event):
        if not is_owner(event.sender_id):
            return
        return await func(event)
    return wrapper


# ── Shared Helpers ──────────────────────────────────────────────────────────

def _all_admins() -> dict[int, dict]:
    """Config owner + DB admins merged into a single dict."""
    result: dict[int, dict] = {}
    result[owner_id] = {
        "perms": ALL_PERMS,
        "raw_perms": {"*"},
        "is_owner": True,
        "source": "config",
        "panels": {"*"},
        "inbounds": {},
    }
    for uid, (perms, db_is_owner, admin_panels, admin_inbounds) in get_db_admins().items():
        if uid == owner_id:
            continue
        result[uid] = {
            "perms": ALL_PERMS if "*" in perms else perms,
            "raw_perms": perms,
            "is_owner": db_is_owner,
            "source": "db",
            "panels": admin_panels,
            "inbounds": admin_inbounds,
        }
    return result


def _format_perms(perms: set[str]) -> str:
    if "*" in perms or perms >= ALL_PERMS:
        return "`*` (all)"
    if not perms:
        return "none"
    return ", ".join(f"`{p}`" for p in sorted(perms))


def _format_panels(panel_set: set[str]) -> str:
    if "*" in panel_set:
        return "`*` (all)"
    if not panel_set:
        return "none"
    return ", ".join(f"`{p}`" for p in sorted(panel_set))


def _format_inbounds(ib_map: dict[str, set[int] | None]) -> str:
    if not ib_map:
        return "`*` (all)"
    parts = []
    for panel in sorted(ib_map):
        ids = ib_map[panel]
        if ids is None:
            parts.append(f"`{panel}`: all")
        else:
            parts.append(f"`{panel}`: {','.join(str(i) for i in sorted(ids))}")
    return "\n".join(parts)


def _back_btn(uid: int, data: bytes):
    """Shortcut for a back-button row with main menu."""
    if data == b"m":
        return [[Button.inline(t("btn_back", uid), data)]]
    return [[Button.inline(t("btn_back", uid), data),
             Button.inline(t("btn_main_menu", uid), b"m")]]


def _format_interval(seconds: int) -> str:
    """Format seconds into a human-readable interval string."""
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


# ── Toggle Helpers ──────────────────────────────────────────────────────────

def _toggle_perm_set(selected: set[str], perm: str) -> set[str]:
    """Toggle a perm in a set, handling * expansion/collapse."""
    if perm == "*":
        if "*" in selected:
            selected.clear()
        else:
            selected.clear()
            selected.add("*")
    else:
        if "*" in selected:
            selected.clear()
            selected.update(ALL_PERMS)
            selected.discard(perm)
        elif perm in selected:
            selected.discard(perm)
        else:
            selected.add(perm)
            if selected >= ALL_PERMS:
                selected.clear()
                selected.add("*")
    return selected


def _toggle_panel_set(selected: set[str], name: str) -> set[str]:
    """Toggle a panel in a set, handling * expansion/collapse."""
    all_names = set(panels)
    if name == "*":
        if "*" in selected:
            selected.clear()
        else:
            selected.clear()
            selected.add("*")
    else:
        if "*" in selected:
            selected.clear()
            selected.update(all_names)
            selected.discard(name)
        elif name in selected:
            selected.discard(name)
        else:
            selected.add(name)
            if all_names and selected >= all_names:
                selected.clear()
                selected.add("*")
    return selected


# ── Owner Panel Main ────────────────────────────────────────────────────────

async def _show_owner_panel(event, uid: int):
    btns = [
        [Button.inline(t("btn_manage_admins", uid), b"op:admins")],
        [Button.inline(t("btn_manage_panels", uid), b"op:panels")],
        [Button.inline(t("btn_settings", uid), b"op:set")],
        [Button.inline(t("btn_list_users", uid), b"op:ul")],
        [Button.inline(t("btn_backup_restore", uid), b"op:br")],
        [Button.inline(t("btn_restart", uid), b"op:restart")],
        [Button.inline(t("btn_back", uid), b"m")],
    ]
    title = t("op_title", uid) + "\n" + t("op_version", uid, version=VERSION)
    await reply(event, title, buttons=btns)


# ── Input Dispatcher ────────────────────────────────────────────────────────

async def handle_owner_input(event) -> bool:
    uid = event.sender_id
    s = st(uid)
    state = s.get("state", "")

    if state == "op_aa_uid":
        from .admins import _handle_add_admin_uid
        return await _handle_add_admin_uid(event, uid, s)
    if state == "op_ap_name":
        from .panels import _handle_add_panel_name
        return await _handle_add_panel_name(event, uid, s)
    if state == "op_ap_url":
        from .panels import _handle_add_panel_url
        return await _handle_add_panel_url(event, uid, s)
    if state == "op_ap_user":
        from .panels import _handle_add_panel_user
        return await _handle_add_panel_user(event, uid, s)
    if state == "op_ap_pass":
        from .panels import _handle_add_panel_pass
        return await _handle_add_panel_pass(event, uid, s)
    if state and state.startswith("op_proxy_"):
        from .panels import _handle_proxy_step_input
        return await _handle_proxy_step_input(event, uid, s)
    if state == "op_ap_sub":
        from .panels import _handle_add_panel_sub
        return await _handle_add_panel_sub(event, uid, s)
    if state == "op_pe":
        from .panels import _handle_panel_edit
        return await _handle_panel_edit(event, uid, s)
    if state == "op_fj":
        from .settings import _handle_force_join_input
        return await _handle_force_join_input(event, uid, s)
    if state == "op_esc":
        from .settings import _handle_simple_caption_input
        return await _handle_simple_caption_input(event, uid, s)
    if state == "op_rl_count_custom":
        from .settings import _handle_rl_count_custom
        return await _handle_rl_count_custom(event, uid, s)
    if state == "op_rl_window_custom":
        from .settings import _handle_rl_window_custom
        return await _handle_rl_window_custom(event, uid, s)
    if state == "op_ab_input":
        from .backup import _handle_auto_backup_input
        return await _handle_auto_backup_input(event, uid, s)
    if state == "op_pab_input":
        from .backup import _handle_panel_auto_backup_input
        return await _handle_panel_auto_backup_input(event, uid, s)
    return False


def handle_owner_restore(event):
    from .backup import handle_owner_restore as _restore
    return _restore(event)


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):
    from . import admins, panels, settings, backup

    @bot.on(events.CallbackQuery(data=b"op"))
    @auth
    @_require_owner
    async def cb_owner_panel(event):
        uid = event.sender_id
        clear(uid)
        await _show_owner_panel(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:ul(?::(\d+))?$"))
    @auth
    @_require_owner
    async def cb_user_list(event):
        uid = event.sender_id
        profiles = get_all_user_profiles()
        if not profiles:
            await reply(event, t("op_users_empty", uid),
                        buttons=[[Button.inline(t("btn_back", uid), b"op")]])
            return
        m = event.pattern_match
        page = int(m.group(1)) if m.group(1) else 0
        per_page = 15
        total_pages = (len(profiles) + per_page - 1) // per_page
        page = min(page, total_pages - 1)
        start = page * per_page
        page_profiles = profiles[start:start + per_page]

        lines = [t("op_users_title", uid, count=len(profiles))]
        if total_pages > 1:
            lines.append(t("op_users_page", uid, page=page + 1, total=total_pages))
        lines.append("")
        for user_id, first, last, username, phone, bio, first_seen in page_profiles:
            name = f"{first} {last}".strip() or "—"
            parts = [f"👤 **{name}**"]
            id_line = f"🆔 `{user_id}`"
            if username:
                id_line += f" · @{username}"
            parts.append(id_line)
            if phone:
                parts.append(f"📱 `{phone}`")
            if bio:
                truncated = (bio[:80] + "…") if len(bio) > 80 else bio
                parts.append(f"📝 {truncated}")
            if first_seen and first_seen > 0:
                from datetime import datetime, timezone
                local_dt = datetime.fromtimestamp(first_seen).astimezone()
                tz_name = local_dt.strftime("%Z") or local_dt.strftime("%z")
                fs_str = local_dt.strftime("%Y-%m-%d %H:%M") + f" ({tz_name})"
                parts.append(t("op_users_first_seen", uid, date=fs_str))
            lines.append("\n".join(parts))
            lines.append("")

        btns = []
        nav = []
        if page > 0:
            nav.append(Button.inline("◀️", f"op:ul:{page - 1}".encode()))
        if page < total_pages - 1:
            nav.append(Button.inline("▶️", f"op:ul:{page + 1}".encode()))
        if nav:
            btns.append(nav)
        btns.append([Button.inline(t("btn_back", uid), b"op"),
                     Button.inline(t("btn_main_menu", uid), b"m")])
        await reply(event, "\n".join(lines), buttons=btns)

    # Register sub-modules
    admins.register(bot)
    panels.register(bot)
    settings.register(bot)
    backup.register(bot)
