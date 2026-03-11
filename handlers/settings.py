import json
import re

from telethon import events, Button
from telethon.tl.functions.messages import CheckChatInviteRequest

from config import st, clear, ALL_PERMS, panels, get_panel, bot
from db import (
    get_setting, set_setting, _parse_inbounds_json, _serialize_inbounds,
    _parse_json_set, _serialize_set, get_plans, get_test_account,
    log_activity,
)
from helpers import auth, reply, answer
from i18n import t
from .owner import (
    _require_owner, _back_btn, _format_perms, _format_panels,
    _format_inbounds, _PERM_LIST, _toggle_perm_set, _toggle_panel_set,
)

_INVITE_RE = re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)(.+)")


# ── Settings ─────────────────────────────────────────────────────────────────

async def _show_settings(event, uid: int):
    pub = get_setting("public_mode") == "1"
    pub_perms_raw = get_setting("public_permissions")
    pub_panels_raw = get_setting("public_panels", '["*"]')
    pub_inbounds_raw = get_setting("public_inbounds", "{}")
    fj = get_setting("force_join")

    lines = [t("op_settings_title", uid)]
    lines.append(t("op_public_mode_on", uid) if pub else t("op_public_mode_off", uid))
    pp = _parse_json_set(pub_perms_raw)
    lines.append(t("op_public_perms_label", uid, perms=_format_perms(pp)))
    pub_pset = _parse_json_set(pub_panels_raw) or {"*"}
    lines.append(t("op_public_panels_label", uid, panels=_format_panels(pub_pset)))
    pub_ib = _parse_inbounds_json(pub_inbounds_raw)
    lines.append(t("op_public_inbounds_label", uid, inbounds=_format_inbounds(pub_ib)))
    if fj:
        lines.append(t("op_force_join_label", uid, channels=fj))
    else:
        lines.append(t("op_force_join_none", uid))

    rl = get_setting("search_rate_limit")
    if rl:
        lines.append(t("op_rate_limit_label", uid, value=rl))
    else:
        lines.append(t("op_rate_limit_none", uid))

    caption = get_setting("simple_search_caption")
    if caption:
        lines.append(t("op_simple_caption_label", uid, caption=caption))
    else:
        lines.append(t("op_simple_caption_none", uid))

    plan_count = len(get_plans())
    if plan_count:
        lines.append(t("op_plans_count", uid, count=plan_count))
    else:
        lines.append(t("op_plans_none", uid))

    ta = get_test_account()
    if ta:
        ta_days = ta.get("days", 0)
        ta_traffic = ta.get("traffic", 0)
        if ta.get("sau"):
            lines.append(t("op_test_account_label_sau", uid, days=ta_days, traffic=ta_traffic))
        else:
            lines.append(t("op_test_account_label", uid, days=ta_days, traffic=ta_traffic))
    else:
        lines.append(t("op_test_account_none", uid))

    btns = [
        [Button.inline(t("btn_toggle_public", uid), b"op:tpm")],
        [Button.inline(t("btn_edit_public_perms", uid), b"op:epp")],
        [Button.inline(t("btn_edit_public_panels", uid), b"op:eppp")],
        [Button.inline(t("btn_edit_public_inbounds", uid), b"op:eppi")],
        [Button.inline(t("btn_edit_force_join", uid), b"op:fj")],
    ]
    btns.append([Button.inline(t("btn_edit_rate_limit", uid), b"op:erl")])
    btns.append([Button.inline(t("btn_edit_simple_caption", uid), b"op:esc")])
    btns.append([Button.inline(t("btn_edit_plans", uid), b"op:pl")])
    btns.append([Button.inline(t("btn_edit_test_account", uid), b"op:eta")])
    btns.append([Button.inline(t("btn_back", uid), b"op"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
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
    btns.append([Button.inline(t("btn_back", uid), b"op:set"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_edit_public_perms_title", uid), buttons=btns)


# ── Public Panel Picker ───────────────────────────────────────────────────

async def _show_public_panel_picker(event, uid: int):
    s = st(uid)
    selected = s.get("op_ppp_panels", {"*"})
    has_star = "*" in selected

    btns = []
    for name in sorted(panels):
        on = has_star or name in selected
        label = t("op_perm_on", uid, perm=name) if on else t("op_perm_off", uid, perm=name)
        btns.append([Button.inline(label, f"op:eppp:{name}".encode())])
    if has_star:
        btns.append([Button.inline(t("btn_deselect_all", uid), b"op:eppp:*")])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), b"op:eppp:*")])
    btns.append([Button.inline(t("btn_confirm_add", uid), b"op:epppc")])
    btns.append([Button.inline(t("btn_back", uid), b"op:set"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_edit_public_panels_title", uid), buttons=btns)


# ── Public Inbound Editor Helpers ─────────────────────────────────────────

async def _show_public_inbound_panel_list(event, uid: int):
    """Show panels for public inbound editing."""
    pub_panels_raw = get_setting("public_panels", '["*"]')
    pub_pset = _parse_json_set(pub_panels_raw) or {"*"}
    pub_inbounds = _parse_inbounds_json(get_setting("public_inbounds", "{}"))
    panel_names = sorted(panels) if "*" in pub_pset else sorted(pub_pset & set(panels))
    btns = []
    for name in panel_names:
        ib_ids = pub_inbounds.get(name)
        suffix = " [restricted]" if ib_ids is not None else ""
        btns.append([Button.inline(f"🖥 {name}{suffix}", f"op:eppip:{name}".encode())])
    btns.append([Button.inline(t("btn_back", uid), b"op:set"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_edit_public_inbounds_panel_list", uid), buttons=btns)


async def _show_public_inbound_picker(event, uid: int, panel_name: str):
    """Show inbound toggle list for public inbound editing."""
    s = st(uid)
    selected = s.get("op_epi_selected", set())
    select_all = s.get("op_epi_all", False)

    p = get_panel(panel_name)
    inbound_list = await p.list_inbounds()
    s["op_epi_inbound_list"] = inbound_list

    btns = []
    for ib in inbound_list:
        iid = ib["id"]
        on = select_all or iid in selected
        desc = f"{ib['remark']} | {ib['port']}"
        label = t("op_perm_on", uid, perm=desc) if on else t("op_perm_off", uid, perm=desc)
        btns.append([Button.inline(label, f"op:eppit:{panel_name}:{iid}".encode())])
    if select_all:
        btns.append([Button.inline(t("btn_deselect_all", uid), f"op:eppis:{panel_name}".encode())])
    else:
        btns.append([Button.inline(t("btn_select_all", uid), f"op:eppis:{panel_name}".encode())])
    btns.append([Button.inline(t("btn_confirm_add", uid), f"op:eppic:{panel_name}".encode())])
    btns.append([Button.inline(t("btn_back", uid), b"op:eppi"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_edit_public_inbounds_title", uid, panel=panel_name), buttons=btns)


# ── Force Join Editor ──────────────────────────────────────────────────────

def _get_fj_channels() -> list[str]:
    """Get force join channels as a list."""
    raw = get_setting("force_join") or ""
    return [ch.strip() for ch in raw.split(",") if ch.strip()]


def _set_fj_channels(channels: list[str]):
    """Save force join channels."""
    set_setting("force_join", ",".join(channels))


async def _show_force_join_editor(event, uid: int):
    """Show the interactive force join channel manager."""
    channels = _get_fj_channels()
    if channels:
        lines = [t("op_fj_title", uid), ""]
        for i, ch in enumerate(channels):
            if _INVITE_RE.match(ch):
                lines.append(f"{i + 1}. {t('op_fj_entry_private', uid, link=ch)}")
            else:
                lines.append(f"{i + 1}. {t('op_fj_entry_public', uid, channel=ch)}")
    else:
        lines = [t("op_fj_title", uid), "", t("op_fj_empty", uid)]

    btns = [[Button.inline(t("btn_add_channel", uid), b"op:fja")]]
    if channels:
        # Remove buttons (up to 8 channels shown as individual buttons)
        rm_row = []
        for i in range(len(channels)):
            rm_row.append(Button.inline(f"\U0001f5d1 #{i + 1}", f"op:fjr:{i}".encode()))
            if len(rm_row) == 4:
                btns.append(rm_row)
                rm_row = []
        if rm_row:
            btns.append(rm_row)
        btns.append([Button.inline(t("btn_clear_all", uid), b"op:fjca")])
    btns.append([Button.inline(t("btn_back", uid), b"op:set"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, "\n".join(lines), buttons=btns)


async def _handle_force_join_input(event, uid, s):
    """Handle text input for adding a force join channel."""
    s["state"] = None
    text = event.text.strip()

    # Normalize and validate
    m = _INVITE_RE.match(text)
    if m:
        # Private channel invite link
        invite_hash = m.group(1)
        try:
            await bot(CheckChatInviteRequest(invite_hash))
        except Exception:
            await event.respond(
                t("op_fj_not_found", uid),
                buttons=_back_btn(uid, b"op:fj"),
            )
            return True
        # Normalize to https://t.me/+HASH
        entry = f"https://t.me/+{invite_hash}"
    elif text.startswith("https://") or text.startswith("http://") or "t.me/" in text:
        # Looks like a link but not a valid invite
        await event.respond(
            t("op_fj_invalid_format", uid),
            buttons=_back_btn(uid, b"op:fj"),
        )
        return True
    else:
        # Public channel — prepend @ if missing
        username = text.lstrip("@").strip()
        if not username:
            await event.respond(
                t("op_fj_invalid", uid),
                buttons=_back_btn(uid, b"op:fj"),
            )
            return True
        try:
            await bot.get_entity(f"@{username}")
        except Exception:
            await event.respond(
                t("op_fj_not_found", uid),
                buttons=_back_btn(uid, b"op:fj"),
            )
            return True
        entry = f"@{username}"

    channels = _get_fj_channels()
    channels.append(entry)
    _set_fj_channels(channels)
    log_activity(uid, "add_force_join", json.dumps({"channel": entry}))
    await event.respond(t("op_fj_added", uid, channel=entry))
    # Show the editor again by creating a fake-ish callback
    clear(uid)
    await _show_force_join_editor(event, uid)
    return True


# ── Text Input Handlers ────────────────────────────────────────────────────


async def _handle_simple_caption_input(event, uid, s):
    s["state"] = None
    text = event.message.raw_text.strip()
    if text == "-":
        set_setting("simple_search_caption", "")
        log_activity(uid, "edit_simple_caption", json.dumps({"action": "cleared"}))
        await event.respond(
            t("op_simple_caption_cleared", uid),
            buttons=_back_btn(uid, b"op:set"),
        )
    else:
        set_setting("simple_search_caption", text)
        log_activity(uid, "edit_simple_caption", json.dumps({"caption": text[:50]}))
        await event.respond(
            t("op_simple_caption_saved", uid),
            buttons=_back_btn(uid, b"op:set"),
        )
    return True


async def _handle_rl_count_custom(event, uid, s):
    s["state"] = None
    try:
        count = int(event.text.strip())
        if count <= 0:
            raise ValueError
    except ValueError:
        s["state"] = "op_rl_count_custom"
        await event.respond(t("op_rl_invalid_number", uid))
        return True
    s["op_rl_count"] = count
    # Show window picker
    btns = [
        [Button.inline("30s", b"op:erlw:30"), Button.inline("60s", b"op:erlw:60"),
         Button.inline("120s", b"op:erlw:120")],
        [Button.inline(t("btn_custom", uid), b"op:erlwc")],
    ] + _back_btn(uid, b"op:erls")
    await event.respond(t("op_rl_pick_window", uid), buttons=btns)
    return True


async def _handle_rl_window_custom(event, uid, s):
    s["state"] = None
    try:
        window = int(event.text.strip())
        if window <= 0:
            raise ValueError
    except ValueError:
        s["state"] = "op_rl_window_custom"
        await event.respond(t("op_rl_invalid_number", uid))
        return True
    count = s.get("op_rl_count", 5)
    set_setting("search_rate_limit", f"{count},{window}")
    log_activity(uid, "edit_rate_limit", json.dumps({"value": f"{count},{window}"}))
    clear(uid)
    await event.respond(
        t("op_rate_limit_saved", uid),
        buttons=_back_btn(uid, b"op:set"),
    )
    return True


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):
    from . import plans, test_account
    plans.register(bot)
    test_account.register(bot)

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
        uid = event.sender_id
        current = get_setting("public_mode") == "1"
        new_val = "0" if current else "1"
        set_setting("public_mode", new_val)
        log_activity(uid, "toggle_public_mode", json.dumps({"enabled": new_val == "1"}))
        await _show_settings(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:epp"))
    @auth
    @_require_owner
    async def cb_edit_public_perms(event):
        uid = event.sender_id
        s = st(uid)
        pp = get_setting("public_permissions")
        current = _parse_json_set(pp)
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
        set_setting("public_permissions", _serialize_set(selected))
        log_activity(uid, "edit_public_perms", json.dumps({"perms": sorted(selected)}))
        clear(uid)
        await reply(event, t("op_public_perms_saved", uid),
                    buttons=_back_btn(uid, b"op:set"))

    # ── Public Panel Picker ─────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:eppp"))
    @auth
    @_require_owner
    async def cb_edit_public_panels(event):
        uid = event.sender_id
        s = st(uid)
        pp = get_setting("public_panels", '["*"]')
        current = _parse_json_set(pp) or {"*"}
        all_names = set(panels)
        if all_names and current >= all_names:
            current = {"*"}
        s["op_ppp_panels"] = current
        await _show_public_panel_picker(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eppp:(.+)$"))
    @auth
    @_require_owner
    async def cb_toggle_public_panel(event):
        uid = event.sender_id
        s = st(uid)
        name = event.pattern_match.group(1).decode()
        selected = s.get("op_ppp_panels", {"*"})
        _toggle_panel_set(selected, name)
        s["op_ppp_panels"] = selected
        await _show_public_panel_picker(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:epppc"))
    @auth
    @_require_owner
    async def cb_confirm_public_panels(event):
        uid = event.sender_id
        s = st(uid)
        selected = s.get("op_ppp_panels", {"*"})
        if not selected:
            selected = {"*"}
        set_setting("public_panels", _serialize_set(selected))
        log_activity(uid, "edit_public_panels", json.dumps({"panels": sorted(selected)}))
        clear(uid)
        await reply(event, t("op_public_panels_saved", uid),
                    buttons=_back_btn(uid, b"op:set"))

    # ── Public Inbound Picker ──────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:eppi"))
    @auth
    @_require_owner
    async def cb_edit_public_inbounds(event):
        uid = event.sender_id
        clear(uid)
        await _show_public_inbound_panel_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eppip:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_edit_public_inbound_panel(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        if panel_name not in panels:
            return
        pub_inbounds = _parse_inbounds_json(get_setting("public_inbounds", "{}"))
        s = st(uid)
        existing = pub_inbounds.get(panel_name)
        if existing is None:
            s["op_epi_all"] = True
            s["op_epi_selected"] = set()
        else:
            s["op_epi_all"] = False
            s["op_epi_selected"] = set(existing)
        await _show_public_inbound_picker(event, uid, panel_name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eppit:([^:]+):(\d+)$"))
    @auth
    @_require_owner
    async def cb_toggle_public_inbound(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        s = st(uid)
        if s.get("op_epi_all"):
            inbound_list = s.get("op_epi_inbound_list", [])
            s["op_epi_selected"] = {ib["id"] for ib in inbound_list} - {iid}
            s["op_epi_all"] = False
        else:
            selected = s.get("op_epi_selected", set())
            if iid in selected:
                selected.discard(iid)
            else:
                selected.add(iid)
            inbound_list = s.get("op_epi_inbound_list", [])
            if inbound_list and selected == {ib["id"] for ib in inbound_list}:
                s["op_epi_all"] = True
                s["op_epi_selected"] = set()
            else:
                s["op_epi_selected"] = selected
        await _show_public_inbound_picker(event, uid, panel_name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eppis:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_toggle_all_public_inbounds(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        s = st(uid)
        if s.get("op_epi_all"):
            s["op_epi_all"] = False
            s["op_epi_selected"] = set()
        else:
            s["op_epi_all"] = True
            s["op_epi_selected"] = set()
        await _show_public_inbound_picker(event, uid, panel_name)

    @bot.on(events.CallbackQuery(pattern=rb"^op:eppic:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_confirm_public_inbounds(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        s = st(uid)
        pub_inbounds = _parse_inbounds_json(get_setting("public_inbounds", "{}"))
        if s.get("op_epi_all"):
            pub_inbounds.pop(panel_name, None)
        else:
            selected = s.get("op_epi_selected", set())
            if selected:
                pub_inbounds[panel_name] = selected
            else:
                pub_inbounds.pop(panel_name, None)
        set_setting("public_inbounds", _serialize_inbounds(pub_inbounds))
        log_activity(uid, "edit_public_inbounds", json.dumps({"panel": panel_name}))
        s.pop("op_epi_all", None)
        s.pop("op_epi_selected", None)
        s.pop("op_epi_inbound_list", None)
        await answer(event, t("op_public_inbounds_saved", uid))
        await _show_public_inbound_panel_list(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:fj"))
    @auth
    @_require_owner
    async def cb_force_join_editor(event):
        uid = event.sender_id
        clear(uid)
        await _show_force_join_editor(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:fja"))
    @auth
    @_require_owner
    async def cb_force_join_add(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_fj"
        await reply(event, t("op_fj_enter_channel", uid),
                    buttons=_back_btn(uid, b"op:fj"))

    @bot.on(events.CallbackQuery(pattern=rb"^op:fjr:(\d+)$"))
    @auth
    @_require_owner
    async def cb_force_join_remove(event):
        uid = event.sender_id
        idx = int(event.pattern_match.group(1))
        channels = _get_fj_channels()
        if 0 <= idx < len(channels):
            removed = channels.pop(idx)
            _set_fj_channels(channels)
            log_activity(uid, "remove_force_join", json.dumps({"channel": removed}))
            await answer(event, t("op_fj_removed", uid, channel=removed))
        await _show_force_join_editor(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:fjca"))
    @auth
    @_require_owner
    async def cb_force_join_clear_confirm(event):
        uid = event.sender_id
        await reply(
            event,
            t("op_fj_confirm_clear", uid),
            buttons=[
                [Button.inline(t("btn_yes", uid), b"op:fjcac")],
                [Button.inline(t("btn_cancel", uid), b"op:fj")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"op:fjcac"))
    @auth
    @_require_owner
    async def cb_force_join_clear_execute(event):
        uid = event.sender_id
        _set_fj_channels([])
        log_activity(uid, "clear_force_join", "{}")
        await answer(event, t("op_fj_cleared", uid))
        await _show_force_join_editor(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:esc"))
    @auth
    @_require_owner
    async def cb_edit_simple_caption(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_esc"
        current = get_setting("simple_search_caption")
        text = t("op_simple_caption_prompt", uid)
        if current:
            text += "\n\n" + t("op_simple_caption_current", uid, caption=current)
        await reply(event, text, buttons=_back_btn(uid, b"op:set"))

    # ── Rate Limit ─────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:erl"))
    @auth
    @_require_owner
    async def cb_edit_rate_limit(event):
        uid = event.sender_id
        rl = get_setting("search_rate_limit")
        if rl:
            status = t("op_rl_current", uid, value=rl)
        else:
            status = t("op_rl_current_off", uid)
        text = t("op_rate_limit_info", uid) + "\n\n" + status
        btns = [
            [Button.inline(t("btn_set_limit", uid), b"op:erls"),
             Button.inline(t("btn_disable", uid), b"op:erld")],
        ] + _back_btn(uid, b"op:set")
        await reply(event, text, buttons=btns)

    @bot.on(events.CallbackQuery(data=b"op:erld"))
    @auth
    @_require_owner
    async def cb_rate_limit_disable(event):
        uid = event.sender_id
        set_setting("search_rate_limit", "")
        log_activity(uid, "edit_rate_limit", json.dumps({"value": "disabled"}))
        await answer(event, t("op_rate_limit_saved", uid))
        await _show_settings(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:erls"))
    @auth
    @_require_owner
    async def cb_rate_limit_set(event):
        uid = event.sender_id
        s = st(uid)
        btns = [
            [Button.inline("3", b"op:erlc:3"), Button.inline("5", b"op:erlc:5"),
             Button.inline("10", b"op:erlc:10")],
            [Button.inline(t("btn_custom", uid), b"op:erlcc")],
        ] + _back_btn(uid, b"op:erl")
        await reply(event, t("op_rl_pick_count", uid), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:erlc:(\d+)$"))
    @auth
    @_require_owner
    async def cb_rate_limit_count(event):
        uid = event.sender_id
        count = int(event.pattern_match.group(1))
        s = st(uid)
        s["op_rl_count"] = count
        btns = [
            [Button.inline("30s", b"op:erlw:30"), Button.inline("60s", b"op:erlw:60"),
             Button.inline("120s", b"op:erlw:120")],
            [Button.inline(t("btn_custom", uid), b"op:erlwc")],
        ] + _back_btn(uid, b"op:erls")
        await reply(event, t("op_rl_pick_window", uid), buttons=btns)

    @bot.on(events.CallbackQuery(data=b"op:erlcc"))
    @auth
    @_require_owner
    async def cb_rate_limit_count_custom(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_rl_count_custom"
        await reply(event, t("op_rl_enter_count", uid),
                    buttons=_back_btn(uid, b"op:erls"))

    @bot.on(events.CallbackQuery(pattern=rb"^op:erlw:(\d+)$"))
    @auth
    @_require_owner
    async def cb_rate_limit_window(event):
        uid = event.sender_id
        s = st(uid)
        count = s.get("op_rl_count", 5)
        window = int(event.pattern_match.group(1))
        set_setting("search_rate_limit", f"{count},{window}")
        log_activity(uid, "edit_rate_limit", json.dumps({"value": f"{count},{window}"}))
        clear(uid)
        await answer(event, t("op_rate_limit_saved", uid))
        await _show_settings(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:erlwc"))
    @auth
    @_require_owner
    async def cb_rate_limit_window_custom(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_rl_window_custom"
        await reply(event, t("op_rl_enter_window", uid),
                    buttons=_back_btn(uid, b"op:erls"))
