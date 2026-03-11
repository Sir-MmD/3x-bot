import json

from telethon import events, Button

from config import (
    st, clear, ALL_PERMS, owner_id, panels, get_panel, bot,
)
from db import (
    get_db_admins, add_db_admin, remove_db_admin,
    update_db_admin_perms, update_db_admin_owner,
    update_db_admin_panels, update_db_admin_inbounds,
    log_activity, get_user_profile, upsert_user_profile,
)
from config import _count_owners, is_owner
from helpers import auth, reply, answer, get_display_name
from i18n import t
from .owner import (
    _require_owner, _back_btn, _all_admins, _format_perms,
    _format_panels, _format_inbounds, _PERM_LIST, _toggle_perm_set,
    _toggle_panel_set,
)


# ── Admin List ──────────────────────────────────────────────────────────────

async def _show_admin_list(event, uid: int):
    all_adm = _all_admins()
    btns = []
    for aid, info in sorted(all_adm.items()):
        icon = "👑" if info["is_owner"] else "👤"
        lock = " 🔒" if info["source"] == "config" else ""
        name = get_display_name(aid)
        if name == str(aid):
            # No cached profile — try fetching entity as fallback
            try:
                entity = await bot.get_entity(aid)
                first = getattr(entity, "first_name", "") or ""
                last = getattr(entity, "last_name", "") or ""
                uname = getattr(entity, "username", "") or ""
                if first or last:
                    upsert_user_profile(aid, first, last, uname, "", "")
                    name = first
                    if last:
                        name += " " + last
            except Exception:
                pass
        label = f"{icon} {name} ({aid}){lock}"
        if len(label) > 60:
            avail = 60 - len(f"{icon}  ({aid}){lock}") - 1
            label = f"{icon} {name[:avail]}… ({aid}){lock}"
        btns.append([Button.inline(label, f"op:ad:{aid}".encode())])
    btns.append([Button.inline(t("btn_add_admin", uid), b"op:aa")])
    btns.append([Button.inline(t("btn_back", uid), b"op"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_admins_title", uid), buttons=btns)


# ── Admin Detail ────────────────────────────────────────────────────────────

async def _show_admin_detail(event, uid: int, target_uid: int):
    if target_uid == owner_id:
        lines = [
            t("op_admin_detail_title", uid),
            t("op_admin_uid", uid, id=target_uid),
            t("op_admin_is_owner", uid),
            t("op_admin_perms", uid, perms="`*` (all)"),
            t("op_admin_panels", uid, panels="`*` (all)"),
            t("op_admin_inbounds", uid, inbounds="`*` (all)"),
            t("op_config_owner_notice", uid),
        ]
        btns = [[Button.inline(t("btn_back", uid), b"op:admins"),
                 Button.inline(t("btn_main_menu", uid), b"m")]]
        await reply(event, "\n".join(lines), buttons=btns)
        return

    db_admins = get_db_admins()
    if target_uid not in db_admins:
        await _show_admin_list(event, uid)
        return

    raw, db_is_owner, admin_panels, admin_inbounds = db_admins[target_uid]
    prof = get_user_profile(target_uid)
    lines = [
        t("op_admin_detail_title", uid),
        t("op_admin_uid", uid, id=target_uid),
    ]
    if prof:
        name = prof["first_name"]
        if prof["last_name"]:
            name += " " + prof["last_name"]
        if name.strip():
            lines.append(t("op_admin_name", uid, name=name))
        if prof["username"]:
            lines.append(t("op_admin_username", uid, username=prof["username"]))
    lines.extend([
        t("op_admin_is_owner", uid) if db_is_owner else t("op_admin_is_admin", uid),
        t("op_admin_perms", uid, perms=_format_perms(raw)),
        t("op_admin_panels", uid, panels=_format_panels(admin_panels)),
        t("op_admin_inbounds", uid, inbounds=_format_inbounds(admin_inbounds)),
    ])
    text = "\n".join(lines)

    has_star = "*" in raw
    btns = []
    for p in _PERM_LIST:
        on = has_star or p in raw
        label = t("op_perm_on", uid, perm=p) if on else t("op_perm_off", uid, perm=p)
        btns.append([Button.inline(label, f"op:tp:{target_uid}:{p}".encode())])
    btns.append([Button.inline(t("btn_edit_panels", uid), f"op:ep:{target_uid}".encode())])
    btns.append([Button.inline(t("btn_edit_inbounds", uid), f"op:ei:{target_uid}".encode())])
    btns.append([Button.inline(t("btn_toggle_owner", uid), f"op:tow:{target_uid}".encode())])
    if target_uid != uid:
        btns.append([Button.inline(t("btn_remove_admin", uid), f"op:ra:{target_uid}".encode())])
    btns.append([Button.inline(t("btn_back", uid), b"op:admins"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, text, buttons=btns)


# ── Perm Picker (Add Admin) ────────────────────────────────────────────────

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
    btns.append([Button.inline(t("btn_back", uid), b"op:admins"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_add_admin_pick_perms", uid, id=target_uid), buttons=btns)


# ── Admin Panel Picker ─────────────────────────────────────────────────────

async def _show_admin_panel_picker(event, uid: int, target_uid: int, selected: set[str]):
    has_star = "*" in selected
    btns = []
    for name in sorted(panels):
        on = has_star or name in selected
        label = t("op_perm_on", uid, perm=name) if on else t("op_perm_off", uid, perm=name)
        btns.append([Button.inline(label, f"op:epa:{target_uid}:{name}".encode())])
    if has_star:
        btns.append([Button.inline(t("btn_deselect_all", uid), f"op:epa:{target_uid}:*".encode())])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), f"op:epa:{target_uid}:*".encode())])
    btns.append([Button.inline(t("btn_confirm_add", uid), f"op:epac:{target_uid}".encode())])
    btns.append([Button.inline(t("btn_back", uid), f"op:ad:{target_uid}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    name = get_display_name(target_uid)
    await reply(event, t("op_edit_admin_panels_title", uid, id=target_uid, name=name), buttons=btns)


# ── Add Admin Panel Picker ─────────────────────────────────────────────────

async def _show_add_admin_panel_picker(event, uid: int):
    s = st(uid)
    target_uid = s.get("op_aa_target")
    selected = s.get("op_aa_panels", {"*"})
    has_star = "*" in selected

    btns = []
    for name in sorted(panels):
        on = has_star or name in selected
        label = t("op_perm_on", uid, perm=name) if on else t("op_perm_off", uid, perm=name)
        btns.append([Button.inline(label, f"op:aapn:{name}".encode())])
    if has_star:
        btns.append([Button.inline(t("btn_deselect_all", uid), b"op:aapn:*")])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), b"op:aapn:*")])
    btns.append([Button.inline(t("btn_confirm_add", uid), b"op:aapnc")])
    btns.append([Button.inline(t("btn_back", uid), b"op:admins"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_add_admin_pick_panels", uid, id=target_uid), buttons=btns)


# ── Admin Inbound Editor Helpers ───────────────────────────────────────────

async def _show_admin_inbound_panel_list(event, uid: int, target_uid: int):
    """Show panels the admin has access to — click one to edit its inbounds."""
    db_admins = get_db_admins()
    if target_uid not in db_admins:
        await _show_admin_list(event, uid)
        return
    _, _, admin_panels, admin_inbounds = db_admins[target_uid]
    panel_names = sorted(panels) if "*" in admin_panels else sorted(admin_panels & set(panels))
    btns = []
    for name in panel_names:
        ib_ids = admin_inbounds.get(name)
        suffix = " [restricted]" if ib_ids is not None else ""
        btns.append([Button.inline(f"🖥 {name}{suffix}", f"op:eip:{target_uid}:{name}".encode())])
    btns.append([Button.inline(t("btn_back", uid), f"op:ad:{target_uid}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    name = get_display_name(target_uid)
    await reply(event, t("op_edit_inbounds_panel_list", uid, id=target_uid, name=name), buttons=btns)


async def _show_admin_inbound_picker(event, uid: int, target_uid: int, panel_name: str):
    """Show inbound toggle list for a specific panel."""
    s = st(uid)
    selected = s.get("op_ei_selected", set())
    select_all = s.get("op_ei_all", False)

    p = get_panel(panel_name)
    inbound_list = await p.list_inbounds()
    s["op_ei_inbound_list"] = inbound_list  # cache for redraws

    btns = []
    for ib in inbound_list:
        iid = ib["id"]
        on = select_all or iid in selected
        desc = f"{ib['remark']} | {ib['port']}"
        label = t("op_perm_on", uid, perm=desc) if on else t("op_perm_off", uid, perm=desc)
        btns.append([Button.inline(label, f"op:eipt:{target_uid}:{panel_name}:{iid}".encode())])
    if select_all:
        btns.append([Button.inline(t("btn_deselect_all", uid), f"op:eips:{target_uid}:{panel_name}".encode())])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), f"op:eips:{target_uid}:{panel_name}".encode())])
    btns.append([Button.inline(t("btn_confirm_add", uid), f"op:eipc:{target_uid}:{panel_name}".encode())])
    btns.append([Button.inline(t("btn_back", uid), f"op:ei:{target_uid}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    name = get_display_name(target_uid)
    await reply(event, t("op_edit_inbounds_title", uid, id=target_uid, name=name, panel=panel_name), buttons=btns)


# ── Text Input Handlers ────────────────────────────────────────────────────

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


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):

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
        current_perms, _, _panels, _ib = db_admins[target_uid]
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
        log_activity(uid, "toggle_perm", json.dumps({"target": target_uid, "perm": perm}))
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
        _, current_owner, _panels, _ib = db_admins[target_uid]
        if current_owner and _count_owners() <= 1:
            await answer(event, t("op_cannot_demote_last_owner", uid), alert=True)
            return
        new_owner = not current_owner
        update_db_admin_owner(target_uid, new_owner)
        log_activity(uid, "toggle_owner", json.dumps({"target": target_uid, "is_owner": new_owner}))
        await _show_admin_detail(event, uid, target_uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:ra:(\d+)$"))
    @auth
    @_require_owner
    async def cb_confirm_remove_admin(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        if target_uid == uid:
            await answer(event, t("op_cannot_remove_self", uid), alert=True)
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
        log_activity(uid, "remove_admin", json.dumps({"target": target_uid}))
        await reply(event, t("op_admin_removed", uid, id=target_uid),
                    buttons=_back_btn(uid, b"op:admins"))

    # ── Edit Admin Panels ───────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:ep:(\d+)$"))
    @auth
    @_require_owner
    async def cb_edit_admin_panels(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        if target_uid == owner_id:
            return
        db_admins = get_db_admins()
        if target_uid not in db_admins:
            return
        _, _, current_panels, _ = db_admins[target_uid]
        s = st(uid)
        s["op_ep_panels"] = set(current_panels)
        await _show_admin_panel_picker(event, uid, target_uid, s["op_ep_panels"])

    @bot.on(events.CallbackQuery(pattern=rb"^op:epa:(\d+):(.+)$"))
    @auth
    @_require_owner
    async def cb_toggle_admin_panel(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        name = event.pattern_match.group(2).decode()
        s = st(uid)
        selected = s.get("op_ep_panels", {"*"})
        _toggle_panel_set(selected, name)
        s["op_ep_panels"] = selected
        await _show_admin_panel_picker(event, uid, target_uid, selected)

    @bot.on(events.CallbackQuery(pattern=rb"^op:epac:(\d+)$"))
    @auth
    @_require_owner
    async def cb_confirm_admin_panels(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        s = st(uid)
        selected = s.get("op_ep_panels", {"*"})
        if not selected:
            selected = {"*"}
        update_db_admin_panels(target_uid, selected)
        log_activity(uid, "edit_admin_panels", json.dumps({"target": target_uid, "panels": sorted(selected)}))
        s.pop("op_ep_panels", None)
        await _show_admin_detail(event, uid, target_uid)

    # ── Edit Admin Inbounds ────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:ei:(\d+)$"))
    @auth
    @_require_owner
    async def cb_edit_admin_inbounds(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        if target_uid == owner_id:
            return
        clear(uid)
        await _show_admin_inbound_panel_list(event, uid, target_uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eip:(\d+):([^:]+)$"))
    @auth
    @_require_owner
    async def cb_edit_admin_inbound_panel(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        panel_name = event.pattern_match.group(2).decode()
        if target_uid == owner_id or panel_name not in panels:
            return
        db_admins = get_db_admins()
        if target_uid not in db_admins:
            return
        _, _, _, admin_inbounds = db_admins[target_uid]
        s = st(uid)
        existing = admin_inbounds.get(panel_name)
        if existing is None:
            # Currently all — start with select-all mode
            s["op_ei_all"] = True
            s["op_ei_selected"] = set()
        else:
            s["op_ei_all"] = False
            s["op_ei_selected"] = set(existing)
        await _show_admin_inbound_picker(event, uid, target_uid, panel_name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eipt:(\d+):([^:]+):(\d+)$"))
    @auth
    @_require_owner
    async def cb_toggle_admin_inbound(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        panel_name = event.pattern_match.group(2).decode()
        iid = int(event.pattern_match.group(3))
        s = st(uid)
        if s.get("op_ei_all"):
            # Expand to all IDs minus toggled one
            inbound_list = s.get("op_ei_inbound_list", [])
            s["op_ei_selected"] = {ib["id"] for ib in inbound_list} - {iid}
            s["op_ei_all"] = False
        else:
            selected = s.get("op_ei_selected", set())
            if iid in selected:
                selected.discard(iid)
            else:
                selected.add(iid)
            # Check if all are selected → collapse to select-all
            inbound_list = s.get("op_ei_inbound_list", [])
            if inbound_list and selected == {ib["id"] for ib in inbound_list}:
                s["op_ei_all"] = True
                s["op_ei_selected"] = set()
            else:
                s["op_ei_selected"] = selected
        await _show_admin_inbound_picker(event, uid, target_uid, panel_name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eips:(\d+):([^:]+)$"))
    @auth
    @_require_owner
    async def cb_toggle_all_admin_inbounds(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        panel_name = event.pattern_match.group(2).decode()
        s = st(uid)
        if s.get("op_ei_all"):
            s["op_ei_all"] = False
            s["op_ei_selected"] = set()
        else:
            s["op_ei_all"] = True
            s["op_ei_selected"] = set()
        await _show_admin_inbound_picker(event, uid, target_uid, panel_name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eipc:(\d+):([^:]+)$"))
    @auth
    @_require_owner
    async def cb_confirm_admin_inbounds(event):
        uid = event.sender_id
        target_uid = int(event.pattern_match.group(1))
        panel_name = event.pattern_match.group(2).decode()
        s = st(uid)
        db_admins = get_db_admins()
        if target_uid not in db_admins:
            return
        _, _, _, current_inbounds = db_admins[target_uid]
        new_inbounds = dict(current_inbounds)
        if s.get("op_ei_all"):
            # All selected = no restriction for this panel
            new_inbounds.pop(panel_name, None)
        else:
            selected = s.get("op_ei_selected", set())
            if selected:
                new_inbounds[panel_name] = selected
            else:
                # Empty selection = no restriction (remove entry)
                new_inbounds.pop(panel_name, None)
        update_db_admin_inbounds(target_uid, new_inbounds)
        log_activity(uid, "edit_admin_inbounds", json.dumps({"target": target_uid, "panel": panel_name}))
        s.pop("op_ei_all", None)
        s.pop("op_ei_selected", None)
        s.pop("op_ei_inbound_list", None)
        await answer(event, t("op_inbounds_saved", uid))
        await _show_admin_inbound_panel_list(event, uid, target_uid)

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
        # Move to panel picker step
        s["op_aa_panels"] = {"*"}
        await _show_add_admin_panel_picker(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:aapn:(.+)$"))
    @auth
    @_require_owner
    async def cb_add_admin_toggle_panel(event):
        uid = event.sender_id
        s = st(uid)
        name = event.pattern_match.group(1).decode()
        selected = s.get("op_aa_panels", {"*"})
        _toggle_panel_set(selected, name)
        s["op_aa_panels"] = selected
        await _show_add_admin_panel_picker(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:aapnc"))
    @auth
    @_require_owner
    async def cb_confirm_add_admin_panels(event):
        uid = event.sender_id
        s = st(uid)
        target_uid = s.get("op_aa_target")
        if target_uid is None:
            return
        selected_perms = s.get("op_aa_perms", set())
        selected_panels = s.get("op_aa_panels", {"*"})
        add_db_admin(target_uid, selected_perms, False, uid, admin_panels=selected_panels)
        log_activity(uid, "add_admin", json.dumps({"target": target_uid, "perms": sorted(selected_perms), "panels": sorted(selected_panels)}))
        clear(uid)
        await reply(event, t("op_admin_added", uid, id=target_uid),
                    buttons=_back_btn(uid, b"op:admins"))
