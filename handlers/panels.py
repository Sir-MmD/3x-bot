import json
from urllib.parse import urlparse

from telethon import events, Button

from config import (
    st, clear, panels, get_panel, register_panel, unregister_panel,
    sub_urls, reload_panels, is_owner, has_perm, stop_panel_auto_backup,
)
from db import (
    get_db_panel, add_db_panel, remove_db_panel,
    update_db_panel_field, rename_db_panel, swap_panel_order,
    rename_panel_in_admins, remove_panel_from_admins,
    rename_panel_in_settings, remove_panel_from_settings,
    log_activity,
)
from helpers import auth, reply, answer
from i18n import t
from panel import PanelClient
from .owner import _require_owner, _back_btn, _NAME_RE


# ── Panel List ──────────────────────────────────────────────────────────────

async def _show_panel_list(event, uid: int):
    btns = []
    names = list(panels)
    for i, name in enumerate(names):
        row = [Button.inline(f"🖥 {name}", f"op:mp:{name}".encode())]
        if i > 0:
            row.append(Button.inline("⬆️", f"op:pmup:{name}".encode()))
        if i < len(names) - 1:
            row.append(Button.inline("⬇️", f"op:pmdn:{name}".encode()))
        btns.append(row)
    btns.append([Button.inline(t("btn_add_panel", uid), b"op:ap")])
    btns.append([Button.inline(t("btn_back", uid), b"op"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
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
    eff_url = edits.get("url", pd.url) if has_edits else pd.url
    eff_user = edits.get("user", pd.username) if has_edits else pd.username
    eff_proxy = edits.get("proxy", pd.proxy) if has_edits else pd.proxy
    eff_sub = edits.get("sub", pd.sub_url) if has_edits else pd.sub_url
    eff_2fa = edits.get("2fa", pd.secret_token) if has_edits else pd.secret_token

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
    if has_edits and "2fa" in edits:
        lines.append(t("op_panel_2fa_changed", uid))
    if eff_proxy:
        lines.append(t("op_panel_proxy_set", uid, proxy=eff_proxy))
    else:
        lines.append(t("op_panel_proxy_none", uid))
    if eff_sub:
        lines.append(t("op_panel_sub_set", uid, sub=eff_sub))
    else:
        lines.append(t("op_panel_sub_none", uid))
    if eff_2fa:
        lines.append(t("op_panel_2fa_set", uid))
    else:
        lines.append(t("op_panel_2fa_none", uid))

    n = name
    btns = [
        [Button.inline(t("btn_edit_name", uid), f"op:pe:name:{n}".encode()),
         Button.inline(t("btn_edit_url", uid), f"op:pe:url:{n}".encode())],
        [Button.inline(t("btn_edit_user", uid), f"op:pe:user:{n}".encode()),
         Button.inline(t("btn_edit_pass", uid), f"op:pe:pass:{n}".encode())],
        [Button.inline(t("btn_edit_proxy", uid), f"op:pe:proxy:{n}".encode()),
         Button.inline(t("btn_edit_sub", uid), f"op:pe:sub:{n}".encode())],
        [Button.inline(t("btn_edit_2fa", uid), f"op:pe:2fa:{n}".encode())],
    ]
    if has_edits:
        btns.append([Button.inline(t("btn_confirm_test", uid), b"op:pet")])
        btns.append([Button.inline(t("btn_discard", uid), f"op:ped:{n}".encode())])
    else:
        row = [Button.inline(t("btn_test_connection", uid), f"op:ptc:{n}".encode())]
        if is_owner(uid):
            row.append(Button.inline(t("btn_remove_panel", uid), f"op:rp:{n}".encode()))
        btns.append(row)
    back = s.get("op_pd_back", b"op:panels" if is_owner(uid) else f"mp:{n}".encode())
    btns.append([Button.inline(t("btn_back", uid), back),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, "\n".join(lines), buttons=btns)


async def _show_manage_panel(event, uid, panel_name, back_data):
    """Render the manage panel menu (edit / stop xray / restart xray)."""
    btns = [
        [Button.inline(t("btn_edit_panel", uid), f"op:pd:{panel_name}".encode())],
        [Button.inline(t("btn_stop_xray", uid), f"mp:sx:{panel_name}".encode()),
         Button.inline(t("btn_restart_xray", uid), f"mp:rx:{panel_name}".encode())],
        [Button.inline(t("btn_back", uid), back_data),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await reply(event, t("manage_panel_title", uid, panel=panel_name), buttons=btns)


# ── Proxy Helpers ───────────────────────────────────────────────────────────

async def _show_proxy_type_picker(event, uid, flow: str):
    """Show proxy type picker. flow = 'ap' (add panel) or 'pe' (panel edit)."""
    btns = [
        [Button.inline("HTTP", f"op:{flow}pt:http".encode()),
         Button.inline("SOCKS5", f"op:{flow}pt:socks5".encode())],
        [Button.inline("SOCKS4", f"op:{flow}pt:socks4".encode()),
         Button.inline(t("btn_no_proxy", uid), f"op:{flow}pt:none".encode())],
    ]
    if flow == "ap":
        btns += _back_btn(uid, b"op:panels")
    else:
        s = st(uid)
        name = s.get("op_pe_panel", "")
        btns += _back_btn(uid, f"op:pd:{name}".encode())
    await reply(event, t("op_proxy_pick_type", uid), buttons=btns)


async def _show_proxy_sub_prompt(event, uid, s):
    """Show subscription URL prompt after proxy is set."""
    s["state"] = "op_ap_sub"
    await event.respond(t("op_add_panel_prompt_sub", uid),
                        buttons=[
                            [Button.inline(t("btn_skip", uid), b"op:apsks")],
                            [Button.inline(t("btn_back", uid), b"op:panels"),
                             Button.inline(t("btn_main_menu", uid), b"m")],
                        ])


async def _show_2fa_prompt(event, uid, s):
    """Show 2FA secret token prompt after sub URL."""
    s["state"] = "op_ap_2fa"
    await event.respond(t("op_add_panel_prompt_2fa", uid),
                        buttons=[
                            [Button.inline(t("btn_skip", uid), b"op:apsk2")],
                            [Button.inline(t("btn_back", uid), b"op:panels"),
                             Button.inline(t("btn_main_menu", uid), b"m")],
                        ])


def _assemble_proxy_url(s):
    """Assemble proxy URL from state fields."""
    ptype = s.get("op_proxy_type", "http")
    addr = s.get("op_proxy_addr", "")
    port = s.get("op_proxy_port", "")
    user = s.get("op_proxy_user", "")
    pw = s.get("op_proxy_pass_val", "")
    if user and pw:
        return f"{ptype}://{user}:{pw}@{addr}:{port}"
    return f"{ptype}://{addr}:{port}"


async def _finish_proxy_step(event, uid, s, flow, proxy_url):
    """Store assembled proxy and proceed to next step."""
    if flow == "ap":
        s["op_ap_data"]["proxy"] = proxy_url
        await _show_proxy_sub_prompt(event, uid, s)
    else:  # pe
        s.setdefault("op_pe_edits", {})["proxy"] = proxy_url
        s["state"] = ""
        name = s.get("op_pe_panel", "")
        await _show_panel_detail(event, uid, name)


async def _finalize_add_panel(event, uid, s):
    """Test connection and save new panel."""
    data = s["op_ap_data"]

    await event.respond(t("op_add_panel_testing", uid))
    pc = PanelClient(data["url"], data["username"], data["password"],
                     name=data["name"], proxy=data["proxy"],
                     secret_token=data.get("secret_token", ""))
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
                 data["proxy"], data.get("sub_url", ""), uid, data.get("secret_token", ""))
    register_panel(data["name"], data["url"], data["username"], data["password"],
                   data["proxy"], data.get("sub_url", ""), data.get("secret_token", ""))
    log_activity(uid, "add_panel", json.dumps({"name": data["name"]}))
    clear(uid)
    await event.respond(
        t("op_add_panel_success", uid, name=data["name"]),
        buttons=_back_btn(uid, b"op:panels"),
    )


# ── Text Input Handlers ────────────────────────────────────────────────────

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
    s["state"] = None
    await _show_proxy_type_picker(event, uid, "ap")
    return True


async def _handle_proxy_step_input(event, uid, s):
    """Handle text inputs for the step-by-step proxy setup."""
    state = s["state"]
    flow = s.get("op_proxy_flow", "ap")  # "ap" or "pe"
    text = event.text.strip()

    if state == "op_proxy_addr":
        s["state"] = None
        if not text:
            s["state"] = "op_proxy_addr"
            await event.respond(t("op_proxy_addr_invalid", uid))
            return True
        s["op_proxy_addr"] = text
        s["state"] = "op_proxy_port"
        back_data = f"op:{flow}pt:{s.get('op_proxy_type', 'http')}".encode()
        await event.respond(t("op_proxy_enter_port", uid),
                            buttons=_back_btn(uid, back_data))
        return True

    if state == "op_proxy_port":
        s["state"] = None
        try:
            port = int(text)
            if port <= 0 or port > 65535:
                raise ValueError
        except ValueError:
            s["state"] = "op_proxy_port"
            await event.respond(t("op_proxy_port_invalid", uid))
            return True
        s["op_proxy_port"] = port
        # Ask about auth
        btns = [
            [Button.inline(t("btn_proxy_with_auth", uid), f"op:{flow}pa:y".encode()),
             Button.inline(t("btn_proxy_no_auth", uid), f"op:{flow}pa:n".encode())],
        ] + _back_btn(uid, f"op:{flow}pt:{s.get('op_proxy_type', 'http')}".encode())
        await reply(event, t("op_proxy_pick_auth", uid), buttons=btns)
        return True

    if state == "op_proxy_user":
        s["state"] = None
        s["op_proxy_user"] = text
        s["state"] = "op_proxy_pass"
        await event.respond(t("op_proxy_enter_pass", uid),
                            buttons=_back_btn(uid, f"op:{flow}pa:y".encode()))
        return True

    if state == "op_proxy_pass":
        s["state"] = None
        s["op_proxy_pass_val"] = text
        proxy_url = _assemble_proxy_url(s)
        await _finish_proxy_step(event, uid, s, flow, proxy_url)
        return True

    return False


async def _handle_add_panel_sub(event, uid, s):
    text = event.text.strip()
    s["op_ap_data"]["sub_url"] = "" if text == "-" else text.rstrip("/")
    await _show_2fa_prompt(event, uid, s)
    return True


async def _handle_add_panel_2fa(event, uid, s):
    text = event.text.strip()
    s["op_ap_data"]["secret_token"] = "" if text == "-" else text
    s["state"] = None
    await _finalize_add_panel(event, uid, s)
    return True


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
    elif field == "sub":
        edits["sub"] = "" if text == "-" else text.rstrip("/")
    elif field == "2fa":
        edits["2fa"] = "" if text == "-" else text
    else:
        return False

    s["state"] = ""  # stop text input
    await _show_panel_detail(event, uid, name)
    return True


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):

    @bot.on(events.CallbackQuery(data=b"op:panels"))
    @auth
    @_require_owner
    async def cb_panel_list(event):
        uid = event.sender_id
        clear(uid)
        await _show_panel_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pmup:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_panel_move_up(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        names = list(panels)
        idx = names.index(name) if name in names else -1
        if idx > 0:
            swap_panel_order(names[idx - 1], name)
            reload_panels()
        await _show_panel_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pmdn:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_panel_move_down(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        names = list(panels)
        idx = names.index(name) if name in names else -1
        if 0 <= idx < len(names) - 1:
            swap_panel_order(name, names[idx + 1])
            reload_panels()
        await _show_panel_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pd:([^:]+)$"))
    @auth("manage_panel")
    async def cb_panel_detail(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        s = st(uid)
        # Set back destination if not already set by manage panel flow
        if "op_pd_back" not in s:
            s["op_pd_back"] = b"op:panels"
        await _show_panel_detail(event, uid, name)

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
        stop_panel_auto_backup(name)
        remove_db_panel(name)
        remove_panel_from_admins(name)
        remove_panel_from_settings(name)
        log_activity(uid, "remove_panel", json.dumps({"name": name}))
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

    # ── Proxy Step-by-Step (Add Panel: ap, Edit Panel: pe) ────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:(ap|pe)pt:(\w+)$"))
    @auth("manage_panel")
    async def cb_proxy_type(event):
        uid = event.sender_id
        flow = event.pattern_match.group(1).decode()  # "ap" or "pe"
        ptype = event.pattern_match.group(2).decode()
        s = st(uid)
        s["op_proxy_flow"] = flow
        if ptype == "picker":
            # Back to type picker
            s["state"] = None
            s["op_proxy_flow"] = flow
            await _show_proxy_type_picker(event, uid, flow)
            return
        if ptype == "none":
            # No proxy
            if flow == "ap":
                s["op_ap_data"]["proxy"] = ""
                await _show_proxy_sub_prompt(event, uid, s)
            else:
                s.setdefault("op_pe_edits", {})["proxy"] = ""
                s["state"] = ""
                name = s.get("op_pe_panel", "")
                await _show_panel_detail(event, uid, name)
            return
        s["op_proxy_type"] = ptype
        s["state"] = "op_proxy_addr"
        await reply(event, t("op_proxy_enter_addr", uid),
                    buttons=_back_btn(uid, f"op:{flow}pt:picker".encode()))

    @bot.on(events.CallbackQuery(pattern=rb"^op:(ap|pe)pa:([yn])$"))
    @auth("manage_panel")
    async def cb_proxy_auth(event):
        uid = event.sender_id
        flow = event.pattern_match.group(1).decode()
        choice = event.pattern_match.group(2).decode()
        s = st(uid)
        s["op_proxy_flow"] = flow
        if choice == "n":
            proxy_url = _assemble_proxy_url(s)
            await _finish_proxy_step(event, uid, s, flow, proxy_url)
        else:
            s["state"] = "op_proxy_user"
            await reply(event, t("op_proxy_enter_user", uid),
                        buttons=_back_btn(uid, f"op:{flow}pt:{s.get('op_proxy_type', 'http')}".encode()))

    @bot.on(events.CallbackQuery(data=b"op:apsks"))
    @auth
    @_require_owner
    async def cb_skip_sub(event):
        uid = event.sender_id
        s = st(uid)
        if "op_ap_data" not in s:
            return
        s["op_ap_data"]["sub_url"] = ""
        await _show_2fa_prompt(event, uid, s)

    @bot.on(events.CallbackQuery(data=b"op:apsk2"))
    @auth
    @_require_owner
    async def cb_skip_2fa(event):
        uid = event.sender_id
        s = st(uid)
        if "op_ap_data" not in s:
            return
        s["op_ap_data"]["secret_token"] = ""
        s["state"] = None
        await _finalize_add_panel(event, uid, s)

    # ── Edit Panel ─────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:pe:([\w]+):([A-Za-z0-9_-]+)$"))
    @auth("manage_panel")
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
            "2fa": "op_pe_prompt_2fa",
        }
        prompt_key = prompts.get(field)
        if not prompt_key:
            return
        s = st(uid)
        # Preserve existing edits if editing the same panel
        if s.get("op_pe_panel") != name:
            s["op_pe_edits"] = {}
        s.setdefault("op_pe_edits", {})
        s["op_pe_panel"] = name
        s["op_pe_field"] = field
        if field == "proxy":
            s["op_proxy_flow"] = "pe"
            await _show_proxy_type_picker(event, uid, "pe")
            return
        s["state"] = "op_pe"
        await reply(event, t(prompt_key, uid),
                    buttons=_back_btn(uid, f"op:pd:{name}".encode()))

    @bot.on(events.CallbackQuery(data=b"op:pet"))
    @auth("manage_panel")
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
        eff_url = edits.get("url", pd.url)
        eff_user = edits.get("user", pd.username)
        eff_pass = edits.get("pass", pd.password)
        eff_proxy = edits.get("proxy", pd.proxy)
        eff_2fa = edits.get("2fa", pd.secret_token)

        # Test connection if any connectivity field changed
        conn_changed = any(k in edits for k in ("url", "user", "pass", "proxy", "2fa"))
        if conn_changed:
            pc = PanelClient(eff_url, eff_user, eff_pass,
                             name=new_name, proxy=eff_proxy, secret_token=eff_2fa)
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
            rename_panel_in_admins(name, new_name)
            rename_panel_in_settings(name, new_name)
            target = new_name

        # 3. Update changed fields
        field_map = {"url": "url", "user": "username", "pass": "password",
                     "proxy": "proxy", "sub": "sub_url", "2fa": "secret_token"}
        for short, db_col in field_map.items():
            if short in edits:
                update_db_panel_field(target, db_col, edits[short])

        # 4. Re-register from DB
        pd_new = get_db_panel(target)
        if pd_new:
            register_panel(pd_new.name, pd_new.url, pd_new.username,
                           pd_new.password, pd_new.proxy, pd_new.sub_url,
                           pd_new.secret_token)

        log_activity(uid, "edit_panel", json.dumps({"name": target, "fields": list(edits.keys())}))
        clear(uid)
        await reply(event, t("op_pe_success", uid, name=target),
                    buttons=_back_btn(uid, f"op:pd:{target}".encode()))

    @bot.on(events.CallbackQuery(pattern=rb"^op:ped:([^:]+)$"))
    @auth("manage_panel")
    async def cb_panel_edit_discard(event):
        """Discard all pending edits."""
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        clear(uid)
        await _show_panel_detail(event, uid, name)

    # ── Test Connection ────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:ptc:([^:]+)$"))
    @auth("manage_panel")
    async def cb_test_connection(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        if name not in panels:
            return
        try:
            await panels[name].login()
            await answer(event, t("op_test_connection_ok", uid), alert=True)
        except Exception as e:
            await answer(event, t("op_test_connection_fail", uid, error=str(e)), alert=True)

    # ── Manage Panel (from panel sub-menu or owner panel list) ────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:mp:(.+)$"))
    @auth
    @_require_owner
    async def cb_owner_manage_panel(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        if panel_name not in panels:
            return
        s = st(uid)
        s["op_pd_back"] = f"op:mp:{panel_name}".encode()
        await _show_manage_panel(event, uid, panel_name, b"op:panels")

    @bot.on(events.CallbackQuery(pattern=rb"^mp:sx:(.+)$"))
    @auth("manage_panel")
    async def cb_stop_xray(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        p = get_panel(panel_name)
        if not p:
            return
        try:
            await p.stop_xray()
        except RuntimeError as e:
            await answer(event, str(e), alert=True)
            return
        log_activity(uid, "stop_xray", json.dumps({"panel": panel_name}))
        await answer(event, t("xray_stopped", uid), alert=True)

    @bot.on(events.CallbackQuery(pattern=rb"^mp:rx:(.+)$"))
    @auth("manage_panel")
    async def cb_restart_xray(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        p = get_panel(panel_name)
        if not p:
            return
        try:
            await p.restart_xray()
        except RuntimeError as e:
            await answer(event, str(e), alert=True)
            return
        log_activity(uid, "restart_xray", json.dumps({"panel": panel_name}))
        await answer(event, t("xray_restarted", uid), alert=True)

    @bot.on(events.CallbackQuery(pattern=rb"^mp:(.+)$"))
    @auth("manage_panel")
    async def cb_manage_panel(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        if panel_name not in panels:
            return
        s = st(uid)
        s["op_pd_back"] = f"mp:{panel_name}".encode()
        await _show_manage_panel(event, uid, panel_name, f"pm:{panel_name}".encode())
