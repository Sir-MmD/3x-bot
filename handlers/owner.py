import io
import re
import zipfile
from datetime import datetime
from urllib.parse import urlparse

from telethon import events, Button

import config as _config_mod
from config import (
    st, clear, user_perms, is_owner, _count_owners,
    ALL_PERMS, owner_id, panels, register_panel, unregister_panel,
    sub_urls, bot, DATA_DIR, _CONFIG_PATH, load_db_panels,
)
from db import (
    get_db_admins, add_db_admin, remove_db_admin,
    update_db_admin_perms, update_db_admin_owner,
    get_db_panel, add_db_panel, remove_db_panel,
    update_db_panel_field, rename_db_panel,
    get_setting, set_setting,
    _lang_cache, _DB_PATH,
)
import db as _db_mod
from helpers import auth, reply
from i18n import t
from panel import PanelClient

_PERM_LIST = sorted(ALL_PERMS)
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _require_owner(func):
    async def wrapper(event):
        if not is_owner(event.sender_id):
            return
        return await func(event)
    return wrapper


# ── Helpers ──────────────────────────────────────────────────────────────────

def _all_admins() -> dict[int, dict]:
    """Config owner + DB admins merged into a single dict."""
    result: dict[int, dict] = {}
    result[owner_id] = {
        "perms": ALL_PERMS,
        "raw_perms": {"*"},
        "is_owner": True,
        "source": "config",
    }
    for uid, (perms, db_is_owner) in get_db_admins().items():
        if uid == owner_id:
            continue
        result[uid] = {
            "perms": ALL_PERMS if "*" in perms else perms,
            "raw_perms": perms,
            "is_owner": db_is_owner,
            "source": "db",
        }
    return result


def _format_perms(perms: set[str]) -> str:
    if "*" in perms or perms >= ALL_PERMS:
        return "`*` (all)"
    if not perms:
        return "none"
    return ", ".join(f"`{p}`" for p in sorted(perms))


def _back_btn(uid: int, data: bytes):
    """Shortcut for a single back-button row."""
    return [[Button.inline(t("btn_back", uid), data)]]


# ── Owner Panel Main ────────────────────────────────────────────────────────

async def _show_owner_panel(event, uid: int):
    btns = [
        [Button.inline(t("btn_manage_admins", uid), b"op:admins")],
        [Button.inline(t("btn_manage_panels", uid), b"op:panels")],
        [Button.inline(t("btn_settings", uid), b"op:set")],
        [Button.inline(t("btn_backup", uid), b"op:bk"),
         Button.inline(t("btn_restore", uid), b"op:rs")],
        [Button.inline(t("btn_restart", uid), b"op:restart")],
        [Button.inline(t("btn_back", uid), b"m")],
    ]
    await reply(event, t("op_title", uid), buttons=btns)


# ── Admin List ──────────────────────────────────────────────────────────────

async def _show_admin_list(event, uid: int):
    all_adm = _all_admins()
    btns = []
    for aid, info in sorted(all_adm.items()):
        icon = "👑" if info["is_owner"] else "👤"
        lock = " 🔒" if info["source"] == "config" else ""
        btns.append([Button.inline(f"{icon} {aid}{lock}", f"op:ad:{aid}".encode())])
    btns.append([Button.inline(t("btn_add_admin", uid), b"op:aa")])
    btns.append([Button.inline(t("btn_back", uid), b"op")])
    await reply(event, t("op_admins_title", uid), buttons=btns)


# ── Admin Detail ────────────────────────────────────────────────────────────

async def _show_admin_detail(event, uid: int, target_uid: int):
    if target_uid == owner_id:
        lines = [
            t("op_admin_detail_title", uid),
            t("op_admin_uid", uid, id=target_uid),
            t("op_admin_is_owner", uid),
            t("op_admin_perms", uid, perms="`*` (all)"),
            t("op_config_owner_notice", uid),
        ]
        btns = [[Button.inline(t("btn_back", uid), b"op:admins")]]
        await reply(event, "\n".join(lines), buttons=btns)
        return

    db_admins = get_db_admins()
    if target_uid not in db_admins:
        await _show_admin_list(event, uid)
        return

    raw, db_is_owner = db_admins[target_uid]
    lines = [
        t("op_admin_detail_title", uid),
        t("op_admin_uid", uid, id=target_uid),
        t("op_admin_is_owner", uid) if db_is_owner else t("op_admin_is_admin", uid),
        t("op_admin_perms", uid, perms=_format_perms(raw)),
    ]
    text = "\n".join(lines)

    has_star = "*" in raw
    btns = []
    for p in _PERM_LIST:
        on = has_star or p in raw
        label = t("op_perm_on", uid, perm=p) if on else t("op_perm_off", uid, perm=p)
        btns.append([Button.inline(label, f"op:tp:{target_uid}:{p}".encode())])
    btns.append([Button.inline(t("btn_toggle_owner", uid), f"op:tow:{target_uid}".encode())])
    if target_uid != uid:
        btns.append([Button.inline(t("btn_remove_admin", uid), f"op:ra:{target_uid}".encode())])
    btns.append([Button.inline(t("btn_back", uid), b"op:admins")])
    await reply(event, text, buttons=btns)


# ── Panel List ──────────────────────────────────────────────────────────────

async def _show_panel_list(event, uid: int):
    btns = []
    for name in panels:
        btns.append([Button.inline(f"🖥 {name}", f"op:pd:{name}".encode())])
    btns.append([Button.inline(t("btn_add_panel", uid), b"op:ap")])
    btns.append([Button.inline(t("btn_back", uid), b"op")])
    await reply(event, t("op_panels_title", uid), buttons=btns)


# ── Panel Detail ────────────────────────────────────────────────────────────

async def _show_panel_detail(event, uid: int, name: str):
    if name not in panels:
        await _show_panel_list(event, uid)
        return
    pd = get_db_panel(name)
    if not pd:
        await _show_panel_list(event, uid)
        return

    s = st(uid)
    edits = s.get("op_pe_edits", {})
    has_edits = bool(edits) and s.get("op_pe_panel") == name

    # Effective values (pending overrides current)
    eff_name = edits.get("name", name) if has_edits else name
    eff_url = edits.get("url", pd["url"]) if has_edits else pd["url"]
    eff_user = edits.get("user", pd["username"]) if has_edits else pd["username"]
    eff_proxy = edits.get("proxy", pd.get("proxy", "")) if has_edits else pd.get("proxy", "")
    eff_sub = edits.get("sub", pd.get("sub_url", "")) if has_edits else pd.get("sub_url", "")

    lines = [t("op_panel_detail_title", uid)]
    if has_edits:
        lines.append(t("op_pe_unsaved", uid))
    lines.extend([
        t("op_panel_name", uid, name=eff_name),
        t("op_panel_url", uid, url=eff_url),
        t("op_panel_user", uid, user=eff_user),
    ])
    if has_edits and "pass" in edits:
        lines.append(t("op_panel_pass_changed", uid))
    if eff_proxy:
        lines.append(t("op_panel_proxy_set", uid, proxy=eff_proxy))
    else:
        lines.append(t("op_panel_proxy_none", uid))
    if eff_sub:
        lines.append(t("op_panel_sub_set", uid, sub=eff_sub))
    else:
        lines.append(t("op_panel_sub_none", uid))

    n = name
    btns = [
        [Button.inline(t("btn_edit_name", uid), f"op:pe:name:{n}".encode()),
         Button.inline(t("btn_edit_url", uid), f"op:pe:url:{n}".encode())],
        [Button.inline(t("btn_edit_user", uid), f"op:pe:user:{n}".encode()),
         Button.inline(t("btn_edit_pass", uid), f"op:pe:pass:{n}".encode())],
        [Button.inline(t("btn_edit_proxy", uid), f"op:pe:proxy:{n}".encode()),
         Button.inline(t("btn_edit_sub", uid), f"op:pe:sub:{n}".encode())],
    ]
    if has_edits:
        btns.append([Button.inline(t("btn_confirm_test", uid), b"op:pet")])
        btns.append([Button.inline(t("btn_discard", uid), f"op:ped:{n}".encode())])
    else:
        btns.append([Button.inline(t("btn_remove_panel", uid), f"op:rp:{n}".encode())])
    btns.append([Button.inline(t("btn_back", uid), b"op:panels")])
    await reply(event, "\n".join(lines), buttons=btns)


# ── Settings ─────────────────────────────────────────────────────────────────

async def _show_settings(event, uid: int):
    pub = get_setting("public_mode") == "1"
    pub_perms = get_setting("public_permissions")
    fj = get_setting("force_join")

    lines = [t("op_settings_title", uid)]
    if pub:
        lines.append(t("op_public_mode_on", uid))
        pp = set(pub_perms.split(",")) if pub_perms else set()
        pp.discard("")
        lines.append(t("op_public_perms_label", uid, perms=_format_perms(pp)))
    else:
        lines.append(t("op_public_mode_off", uid))
    if fj:
        lines.append(t("op_force_join_label", uid, channels=fj))
    else:
        lines.append(t("op_force_join_none", uid))

    btns = [
        [Button.inline(t("btn_toggle_public", uid), b"op:tpm")],
    ]
    if pub:
        btns.append([Button.inline(t("btn_edit_public_perms", uid), b"op:epp")])
    btns.append([Button.inline(t("btn_edit_force_join", uid), b"op:efj")])
    btns.append([Button.inline(t("btn_back", uid), b"op")])
    await reply(event, "\n".join(lines), buttons=btns)


# ── Public Perms Picker ─────────────────────────────────────────────────────

async def _show_public_perm_picker(event, uid: int):
    s = st(uid)
    selected = s.get("op_pp_perms", set())
    has_star = "*" in selected

    btns = []
    for p in _PERM_LIST:
        on = has_star or p in selected
        label = t("op_perm_on", uid, perm=p) if on else t("op_perm_off", uid, perm=p)
        btns.append([Button.inline(label, f"op:epp:{p}".encode())])
    if has_star:
        btns.append([Button.inline(t("btn_deselect_all", uid), b"op:epp:*")])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), b"op:epp:*")])
    btns.append([Button.inline(t("btn_confirm_add", uid), b"op:eppc")])
    btns.append([Button.inline(t("btn_back", uid), b"op:set")])
    await reply(event, t("op_edit_public_perms_title", uid), buttons=btns)


# ── Add Admin Perm Picker ───────────────────────────────────────────────────

async def _show_perm_picker(event, uid: int):
    s = st(uid)
    target_uid = s.get("op_aa_target")
    selected = s.get("op_aa_perms", set())
    has_star = "*" in selected

    btns = []
    for p in _PERM_LIST:
        on = has_star or p in selected
        label = t("op_perm_on", uid, perm=p) if on else t("op_perm_off", uid, perm=p)
        btns.append([Button.inline(label, f"op:aap:{p}".encode())])
    if has_star:
        btns.append([Button.inline(t("btn_deselect_all", uid), b"op:aap:*")])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), b"op:aap:*")])
    btns.append([Button.inline(t("btn_confirm_add", uid), b"op:aac")])
    btns.append([Button.inline(t("btn_back", uid), b"op:admins")])
    await reply(event, t("op_add_admin_pick_perms", uid, id=target_uid), buttons=btns)


# ── Text Input Handler ──────────────────────────────────────────────────────

async def handle_owner_input(event) -> bool:
    uid = event.sender_id
    s = st(uid)
    state = s.get("state", "")

    if state == "op_aa_uid":
        return await _handle_add_admin_uid(event, uid, s)
    if state == "op_ap_name":
        return await _handle_add_panel_name(event, uid, s)
    if state == "op_ap_url":
        return await _handle_add_panel_url(event, uid, s)
    if state == "op_ap_user":
        return await _handle_add_panel_user(event, uid, s)
    if state == "op_ap_pass":
        return await _handle_add_panel_pass(event, uid, s)
    if state == "op_ap_proxy":
        return await _handle_add_panel_proxy(event, uid, s)
    if state == "op_ap_sub":
        return await _handle_add_panel_sub(event, uid, s)
    if state == "op_pe":
        return await _handle_panel_edit(event, uid, s)
    if state == "op_fj":
        return await _handle_force_join_input(event, uid, s)
    return False


async def handle_owner_restore(event) -> bool:
    """Handle uploaded ZIP file for restore (state=op_rs)."""
    uid = event.sender_id
    s = st(uid)
    if s.get("state") != "op_rs":
        return False
    if not is_owner(uid):
        return False

    doc = event.message.document
    if not doc:
        return False

    name = event.message.file.name or ""
    if not name.lower().endswith(".zip"):
        await event.respond(t("restore_invalid_zip", uid),
                            buttons=_back_btn(uid, b"op"))
        return True

    buf = io.BytesIO()
    await bot.download_media(event.message, buf)
    buf.seek(0)

    try:
        zf = zipfile.ZipFile(buf)
    except zipfile.BadZipFile:
        await event.respond(t("restore_invalid_zip", uid),
                            buttons=_back_btn(uid, b"op"))
        return True

    names = zf.namelist()
    if "config.toml" not in names or "3x-bot.db" not in names:
        zf.close()
        await event.respond(t("restore_missing_files", uid),
                            buttons=_back_btn(uid, b"op"))
        return True

    # Extract files to DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zf.extract("config.toml", DATA_DIR)
    zf.extract("3x-bot.db", DATA_DIR)
    zf.close()

    # Invalidate all caches
    _db_mod._admins_cache = None
    _db_mod._panels_cache = None
    _db_mod._settings_cache = None
    _db_mod._lang_cache.clear()

    # Reload panels
    load_db_panels()

    clear(uid)
    await event.respond(
        t("restore_success", uid) + "\n" + t("restore_restart_note", uid),
        buttons=_back_btn(uid, b"op"),
    )
    return True


async def _handle_add_admin_uid(event, uid, s):
    text = event.text.strip()
    try:
        target_uid = int(text)
    except ValueError:
        await event.respond(t("op_add_admin_invalid_uid", uid),
                            buttons=_back_btn(uid, b"op:admins"))
        return True
    all_adm = _all_admins()
    if target_uid in all_adm:
        await event.respond(t("op_add_admin_already_exists", uid),
                            buttons=_back_btn(uid, b"op:admins"))
        return True
    s["op_aa_target"] = target_uid
    s["op_aa_perms"] = set()
    s["state"] = ""
    await _show_perm_picker(event, uid)
    return True


async def _handle_add_panel_name(event, uid, s):
    name = event.text.strip()
    if not _NAME_RE.match(name):
        await event.respond(t("op_add_panel_name_invalid", uid),
                            buttons=_back_btn(uid, b"op:panels"))
        return True
    if name in panels:
        await event.respond(t("op_add_panel_name_taken", uid),
                            buttons=_back_btn(uid, b"op:panels"))
        return True
    s["op_ap_data"] = {"name": name}
    s["state"] = "op_ap_url"
    await event.respond(t("op_add_panel_prompt_url", uid),
                        buttons=_back_btn(uid, b"op:panels"))
    return True


async def _handle_add_panel_url(event, uid, s):
    url = event.text.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        await event.respond(t("op_add_panel_invalid_url", uid),
                            buttons=_back_btn(uid, b"op:panels"))
        return True
    s["op_ap_data"]["url"] = url
    s["state"] = "op_ap_user"
    await event.respond(t("op_add_panel_prompt_user", uid),
                        buttons=_back_btn(uid, b"op:panels"))
    return True


async def _handle_add_panel_user(event, uid, s):
    s["op_ap_data"]["username"] = event.text.strip()
    s["state"] = "op_ap_pass"
    await event.respond(t("op_add_panel_prompt_pass", uid),
                        buttons=_back_btn(uid, b"op:panels"))
    return True


async def _handle_add_panel_pass(event, uid, s):
    s["op_ap_data"]["password"] = event.text.strip()
    s["state"] = "op_ap_proxy"
    await event.respond(t("op_add_panel_prompt_proxy", uid),
                        buttons=[
                            [Button.inline(t("btn_skip", uid), b"op:apskp")],
                            [Button.inline(t("btn_back", uid), b"op:panels")],
                        ])
    return True


async def _handle_add_panel_proxy(event, uid, s):
    text = event.text.strip()
    s["op_ap_data"]["proxy"] = "" if text == "-" else text
    s["state"] = "op_ap_sub"
    await event.respond(t("op_add_panel_prompt_sub", uid),
                        buttons=[
                            [Button.inline(t("btn_skip", uid), b"op:apsks")],
                            [Button.inline(t("btn_back", uid), b"op:panels")],
                        ])
    return True


async def _handle_add_panel_sub(event, uid, s):
    text = event.text.strip()
    sub_url = "" if text == "-" else text.rstrip("/")
    await _finalize_add_panel(event, uid, s, sub_url)
    return True


async def _finalize_add_panel(event, uid, s, sub_url=""):
    """Test connection and save new panel."""
    data = s["op_ap_data"]
    data["sub_url"] = sub_url

    await event.respond(t("op_add_panel_testing", uid))
    pc = PanelClient(data["url"], data["username"], data["password"],
                     name=data["name"], proxy=data["proxy"])
    try:
        await pc.login()
    except Exception as e:
        await pc.close()
        s["state"] = "op_ap_url"
        await event.respond(
            t("op_add_panel_test_failed", uid, error=str(e)),
            buttons=_back_btn(uid, b"op:panels"),
        )
        return
    await pc.close()

    add_db_panel(data["name"], data["url"], data["username"], data["password"],
                 data["proxy"], data["sub_url"], uid)
    register_panel(data["name"], data["url"], data["username"], data["password"],
                   data["proxy"], data["sub_url"])
    clear(uid)
    await event.respond(
        t("op_add_panel_success", uid, name=data["name"]),
        buttons=_back_btn(uid, b"op:panels"),
    )


async def _handle_force_join_input(event, uid, s):
    text = event.text.strip()
    if text == "-":
        set_setting("force_join", "")
    else:
        channels = ",".join(ch.strip() for ch in text.split(",") if ch.strip())
        set_setting("force_join", channels)
    clear(uid)
    await event.respond(
        t("op_force_join_saved", uid),
        buttons=_back_btn(uid, b"op:set"),
    )
    return True


# ── Panel Edit ──────────────────────────────────────────────────────────────

async def _handle_panel_edit(event, uid, s):
    """Handle text input for editing a panel field. Stores in pending edits."""
    text = event.text.strip()
    name = s.get("op_pe_panel")
    field = s.get("op_pe_field")
    if not name or not field:
        return False

    back = _back_btn(uid, f"op:pd:{name}".encode())
    edits = s.setdefault("op_pe_edits", {})

    if field == "name":
        if not _NAME_RE.match(text):
            await event.respond(t("op_add_panel_name_invalid", uid), buttons=back)
            return True
        if text != name and text in panels:
            await event.respond(t("op_add_panel_name_taken", uid), buttons=back)
            return True
        if text == name:
            edits.pop("name", None)
        else:
            edits["name"] = text
    elif field == "url":
        url = text.rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            await event.respond(t("op_add_panel_invalid_url", uid), buttons=back)
            return True
        edits["url"] = url
    elif field in ("user", "pass"):
        edits[field] = text
    elif field == "proxy":
        edits["proxy"] = "" if text == "-" else text
    elif field == "sub":
        edits["sub"] = "" if text == "-" else text.rstrip("/")
    else:
        return False

    s["state"] = ""  # stop text input
    await _show_panel_detail(event, uid, name)
    return True


# ── Perm toggle helper ───────────────────────────────────────────────────────

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


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):

    @bot.on(events.CallbackQuery(data=b"op"))
    @auth
    @_require_owner
    async def cb_owner_panel(event):
        uid = event.sender_id
        clear(uid)
        await _show_owner_panel(event, uid)

    # ── Admin callbacks ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:admins"))
    @auth
    @_require_owner
    async def cb_admin_list(event):
        uid = event.sender_id
        clear(uid)
        await _show_admin_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:ad:(\d+)$"))
    @auth
    @_require_owner
    async def cb_admin_detail(event):
        target_uid = int(event.pattern_match.group(1))
        await _show_admin_detail(event, event.sender_id, target_uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:tp:(\d+):(\w+)$"))
    @auth
    @_require_owner
    async def cb_toggle_perm(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        perm = event.pattern_match.group(2).decode()
        if target_uid == owner_id:
            return
        db_admins = get_db_admins()
        if target_uid not in db_admins:
            return
        current_perms, _ = db_admins[target_uid]
        new_perms = set(current_perms)
        if "*" in new_perms:
            new_perms = set(ALL_PERMS)
        if perm in new_perms:
            new_perms.discard(perm)
        else:
            new_perms.add(perm)
        if new_perms >= ALL_PERMS:
            new_perms = {"*"}
        update_db_admin_perms(target_uid, new_perms)
        await _show_admin_detail(event, uid, target_uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:tow:(\d+)$"))
    @auth
    @_require_owner
    async def cb_toggle_owner(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        if target_uid == owner_id:
            return
        db_admins = get_db_admins()
        if target_uid not in db_admins:
            return
        _, current_owner = db_admins[target_uid]
        if current_owner and _count_owners() <= 1:
            await event.answer(t("op_cannot_demote_last_owner", uid), alert=True)
            return
        update_db_admin_owner(target_uid, not current_owner)
        await _show_admin_detail(event, uid, target_uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:ra:(\d+)$"))
    @auth
    @_require_owner
    async def cb_confirm_remove_admin(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        if target_uid == uid:
            await event.answer(t("op_cannot_remove_self", uid), alert=True)
            return
        if target_uid == owner_id:
            return
        btns = [
            [Button.inline(t("btn_yes_remove", uid), f"op:rac:{target_uid}".encode())],
            [Button.inline(t("btn_cancel", uid), f"op:ad:{target_uid}".encode())],
        ]
        await reply(event, t("op_confirm_remove_admin", uid, id=target_uid), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:rac:(\d+)$"))
    @auth
    @_require_owner
    async def cb_execute_remove_admin(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        if target_uid == uid or target_uid == owner_id:
            return
        remove_db_admin(target_uid)
        await reply(event, t("op_admin_removed", uid, id=target_uid),
                    buttons=_back_btn(uid, b"op:admins"))

    # ── Add Admin ───────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:aa"))
    @auth
    @_require_owner
    async def cb_add_admin(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_aa_uid"
        await reply(event, t("op_add_admin_prompt_uid", uid),
                    buttons=_back_btn(uid, b"op:admins"))

    @bot.on(events.CallbackQuery(pattern=rb"^op:aap:(.+)$"))
    @auth
    @_require_owner
    async def cb_add_admin_toggle_perm(event):
        uid = event.sender_id
        s = st(uid)
        perm = event.pattern_match.group(1).decode()
        selected = s.get("op_aa_perms", set())
        _toggle_perm_set(selected, perm)
        s["op_aa_perms"] = selected
        await _show_perm_picker(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:aac"))
    @auth
    @_require_owner
    async def cb_confirm_add_admin(event):
        uid = event.sender_id
        s = st(uid)
        target_uid = s.get("op_aa_target")
        if target_uid is None:
            return
        selected = s.get("op_aa_perms", set())
        add_db_admin(target_uid, selected, False, uid)
        clear(uid)
        await reply(event, t("op_admin_added", uid, id=target_uid),
                    buttons=_back_btn(uid, b"op:admins"))

    # ── Panel callbacks ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:panels"))
    @auth
    @_require_owner
    async def cb_panel_list(event):
        uid = event.sender_id
        clear(uid)
        await _show_panel_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pd:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_panel_detail(event):
        name = event.pattern_match.group(1).decode()
        await _show_panel_detail(event, event.sender_id, name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:rp:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_confirm_remove_panel(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        btns = [
            [Button.inline(t("btn_yes_remove", uid), f"op:rpc:{name}".encode())],
            [Button.inline(t("btn_cancel", uid), f"op:pd:{name}".encode())],
        ]
        await reply(event, t("op_confirm_remove_panel", uid, name=name), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:rpc:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_execute_remove_panel(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        pc = unregister_panel(name)
        if pc:
            try:
                await pc.close()
            except Exception:
                pass
        remove_db_panel(name)
        await reply(event, t("op_panel_removed", uid, name=name),
                    buttons=_back_btn(uid, b"op:panels"))

    # ── Add Panel ───────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:ap"))
    @auth
    @_require_owner
    async def cb_add_panel(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_ap_name"
        await reply(event, t("op_add_panel_prompt_name", uid),
                    buttons=_back_btn(uid, b"op:panels"))

    @bot.on(events.CallbackQuery(data=b"op:apskp"))
    @auth
    @_require_owner
    async def cb_skip_proxy(event):
        uid = event.sender_id
        s = st(uid)
        if "op_ap_data" not in s:
            return
        s["op_ap_data"]["proxy"] = ""
        s["state"] = "op_ap_sub"
        await reply(event, t("op_add_panel_prompt_sub", uid),
                    buttons=[
                        [Button.inline(t("btn_skip", uid), b"op:apsks")],
                        [Button.inline(t("btn_back", uid), b"op:panels")],
                    ])

    @bot.on(events.CallbackQuery(data=b"op:apsks"))
    @auth
    @_require_owner
    async def cb_skip_sub(event):
        uid = event.sender_id
        s = st(uid)
        if "op_ap_data" not in s:
            return
        await _finalize_add_panel(event, uid, s, sub_url="")

    # ── Edit Panel ─────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:pe:(\w+):([A-Za-z0-9_-]+)$"))
    @auth
    @_require_owner
    async def cb_panel_edit_start(event):
        uid = event.sender_id
        field = event.pattern_match.group(1).decode()
        name = event.pattern_match.group(2).decode()
        if name not in panels:
            await _show_panel_list(event, uid)
            return
        prompts = {
            "name": "op_pe_prompt_name",
            "url": "op_pe_prompt_url",
            "user": "op_pe_prompt_user",
            "pass": "op_pe_prompt_pass",
            "proxy": "op_pe_prompt_proxy",
            "sub": "op_pe_prompt_sub",
        }
        prompt_key = prompts.get(field)
        if not prompt_key:
            return
        s = st(uid)
        # Preserve existing edits if editing the same panel
        if s.get("op_pe_panel") != name:
            s["op_pe_edits"] = {}
        s.setdefault("op_pe_edits", {})
        s["state"] = "op_pe"
        s["op_pe_panel"] = name
        s["op_pe_field"] = field
        await reply(event, t(prompt_key, uid),
                    buttons=_back_btn(uid, f"op:pd:{name}".encode()))

    @bot.on(events.CallbackQuery(data=b"op:pet"))
    @auth
    @_require_owner
    async def cb_panel_edit_test(event):
        """Apply all pending edits — test connection first if needed."""
        uid = event.sender_id
        s = st(uid)
        name = s.get("op_pe_panel")
        edits = s.get("op_pe_edits", {})
        if not name or not edits:
            return

        pd = get_db_panel(name)
        if not pd:
            clear(uid)
            await _show_panel_list(event, uid)
            return

        new_name = edits.get("name", name)

        # Effective connectivity values
        eff_url = edits.get("url", pd["url"])
        eff_user = edits.get("user", pd["username"])
        eff_pass = edits.get("pass", pd["password"])
        eff_proxy = edits.get("proxy", pd.get("proxy", ""))

        # Test connection if any connectivity field changed
        conn_changed = any(k in edits for k in ("url", "user", "pass", "proxy"))
        if conn_changed:
            pc = PanelClient(eff_url, eff_user, eff_pass,
                             name=new_name, proxy=eff_proxy)
            try:
                await pc.login()
                await pc.close()
            except Exception as e:
                await pc.close()
                await reply(event,
                    t("op_add_panel_test_failed", uid, error=str(e)),
                    buttons=[
                        [Button.inline(t("btn_retry", uid),
                                       f"op:pd:{name}".encode())],
                        [Button.inline(t("btn_discard", uid),
                                       f"op:ped:{name}".encode())],
                    ])
                return

        # All good — apply changes
        # 1. Unregister old panel
        old_client = unregister_panel(name)
        if old_client:
            try:
                await old_client.close()
            except Exception:
                pass

        # 2. Rename if needed
        target = name
        if "name" in edits:
            rename_db_panel(name, new_name)
            target = new_name

        # 3. Update changed fields
        field_map = {"url": "url", "user": "username", "pass": "password",
                     "proxy": "proxy", "sub": "sub_url"}
        for short, db_col in field_map.items():
            if short in edits:
                update_db_panel_field(target, db_col, edits[short])

        # 4. Re-register from DB
        pd_new = get_db_panel(target)
        if pd_new:
            register_panel(pd_new["name"], pd_new["url"], pd_new["username"],
                           pd_new["password"], pd_new.get("proxy", ""),
                           pd_new.get("sub_url", ""))

        clear(uid)
        await reply(event, t("op_pe_success", uid, name=target),
                    buttons=_back_btn(uid, f"op:pd:{target}".encode()))

    @bot.on(events.CallbackQuery(pattern=rb"^op:ped:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_panel_edit_discard(event):
        """Discard all pending edits."""
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        clear(uid)
        await _show_panel_detail(event, uid, name)

    # ── Settings callbacks ──────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:set"))
    @auth
    @_require_owner
    async def cb_settings(event):
        uid = event.sender_id
        clear(uid)
        await _show_settings(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:tpm"))
    @auth
    @_require_owner
    async def cb_toggle_public_mode(event):
        current = get_setting("public_mode") == "1"
        set_setting("public_mode", "0" if current else "1")
        await _show_settings(event, event.sender_id)

    @bot.on(events.CallbackQuery(data=b"op:epp"))
    @auth
    @_require_owner
    async def cb_edit_public_perms(event):
        uid = event.sender_id
        s = st(uid)
        pp = get_setting("public_permissions")
        current = set(pp.split(",")) if pp else set()
        current.discard("")
        if current >= ALL_PERMS:
            current = {"*"}
        s["op_pp_perms"] = current
        await _show_public_perm_picker(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:epp:(.+)$"))
    @auth
    @_require_owner
    async def cb_toggle_public_perm(event):
        uid = event.sender_id
        s = st(uid)
        perm = event.pattern_match.group(1).decode()
        selected = s.get("op_pp_perms", set())
        _toggle_perm_set(selected, perm)
        s["op_pp_perms"] = selected
        await _show_public_perm_picker(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:eppc"))
    @auth
    @_require_owner
    async def cb_confirm_public_perms(event):
        uid = event.sender_id
        s = st(uid)
        selected = s.get("op_pp_perms", set())
        if "*" in selected:
            val = "*"
        else:
            val = ",".join(sorted(selected))
        set_setting("public_permissions", val)
        clear(uid)
        await reply(event, t("op_public_perms_saved", uid),
                    buttons=_back_btn(uid, b"op:set"))

    @bot.on(events.CallbackQuery(data=b"op:efj"))
    @auth
    @_require_owner
    async def cb_edit_force_join(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_fj"
        await reply(event, t("op_force_join_prompt", uid),
                    buttons=_back_btn(uid, b"op:set"))

    # ── Backup / Restore ──────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:bk"))
    @auth
    @_require_owner
    async def cb_backup(event):
        uid = event.sender_id
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d_%H-%M")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            cfg_path = _CONFIG_PATH
            db_path = _DB_PATH
            if cfg_path.exists():
                zf.write(cfg_path, "config.toml")
            if DATA_DIR.joinpath("3x-bot.db").exists():
                zf.write(db_path, "3x-bot.db")
        buf.seek(0)
        buf.name = f"3x-bot-backup-{stamp}.zip"
        caption = t("backup_caption", uid, date=now.strftime("%Y/%m/%d"), time=now.strftime("%H:%M"))
        await bot.send_file(event.chat_id, buf, caption=caption)

    @bot.on(events.CallbackQuery(data=b"op:rs"))
    @auth
    @_require_owner
    async def cb_restore_prompt(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_rs"
        await reply(event, t("restore_prompt", uid),
                    buttons=_back_btn(uid, b"op"))

    @bot.on(events.CallbackQuery(data=b"op:restart"))
    @auth
    @_require_owner
    async def cb_restart_confirm(event):
        uid = event.sender_id
        btns = [
            [Button.inline(t("btn_yes_restart", uid), b"op:restartc")],
            [Button.inline(t("btn_cancel", uid), b"op")],
        ]
        await reply(event, t("confirm_restart", uid), buttons=btns)

    @bot.on(events.CallbackQuery(data=b"op:restartc"))
    @auth
    @_require_owner
    async def cb_restart_execute(event):
        uid = event.sender_id
        await event.respond(t("restarting", uid))
        _config_mod.restart_requested = uid
        await bot.disconnect()
