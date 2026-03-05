import asyncio
import io
import json
import time

from telethon import events, Button

from config import get_panel, st, clear, bot, visible_panels, visible_inbounds, server_addrs, sub_urls
from db import log_activity
from helpers import auth, reply, answer, format_bytes, format_expiry, make_qr
from i18n import t
from panel import build_client_link, SUPPORTED_PROTOCOLS
from pdf_export import generate_account_pdf


def _back_data(uid: int) -> bytes:
    """Return the appropriate back callback data based on bulk ops source."""
    s = st(uid)
    source = s.get("bo_source", "main")
    if source == "main":
        return b"bom_start"
    return f"pm:{source}".encode()


async def _bulk_op_execute(event, uid: int):
    s = st(uid)
    clients = s.get("bo_clients", [])
    op = s["bo_op"]
    action = s["bo_action"]
    value = s["bo_value"]

    if not clients:
        await reply(
            event,
            t("bo_no_accounts", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"m")]],
        )
        clear(uid)
        return

    progress_msg = await bot.send_message(
        event.chat_id,
        t("bo_processing", uid, done=0, total=len(clients)),
    )

    success = 0
    failed = 0
    skipped = 0
    done_count = 0
    total = len(clients)
    counters_lock = asyncio.Lock()

    def apply_change(client):
        """Apply the operation to a client dict copy. Returns (updated, skip)."""
        updated = dict(client)
        if op == "d":
            cur = updated.get("expiryTime", 0)
            add_ms = value * 86_400_000
            if action == "add":
                if cur == 0:
                    return updated, True
                sau = s.get("bo_sau", False)
                if sau:
                    if cur < 0:
                        updated["expiryTime"] = cur - add_ms
                    else:
                        updated["expiryTime"] = -add_ms
                else:
                    if cur < 0:
                        now_ms = int(time.time() * 1000)
                        updated["expiryTime"] = now_ms + abs(cur) + add_ms
                    else:
                        updated["expiryTime"] = cur + add_ms
            else:  # subtract
                if cur == 0:
                    return updated, True
                if cur < 0:
                    new_val = cur + add_ms
                    if new_val >= 0:
                        new_val = -86_400_000
                    updated["expiryTime"] = new_val
                else:
                    now_ms = int(time.time() * 1000)
                    new_val = cur - add_ms
                    if new_val <= now_ms:
                        new_val = now_ms + 86_400_000
                    updated["expiryTime"] = new_val
        else:  # traffic
            cur = updated.get("totalGB", 0)
            delta_bytes = int(value * 1024**3)
            if action == "add":
                if cur == 0:
                    return updated, True
                updated["totalGB"] = cur + delta_bytes
            else:  # subtract
                if cur == 0:
                    return updated, True
                new_val = cur - delta_bytes
                if new_val <= 0:
                    new_val = 1
                updated["totalGB"] = new_val
        return updated, False

    # Group clients by (panel_name, inbound_id) so updates within the same
    # inbound run sequentially (3x-ui read-modify-write race).
    by_group: dict[tuple[str, int], list] = {}
    for client, iid, cid, proto, pname in clients:
        by_group.setdefault((pname, iid), []).append((client, iid, cid, proto, pname))

    async def process_inbound_group(group):
        nonlocal success, failed, skipped, done_count
        panel_name = group[0][4]
        p = get_panel(panel_name)
        for client, inbound_id, client_id, protocol, _pn in group:
            updated, skip = apply_change(client)
            if skip:
                async with counters_lock:
                    skipped += 1
                    done_count += 1
                    current = done_count
            else:
                try:
                    await p.update_client(client_id, inbound_id, updated)
                    async with counters_lock:
                        success += 1
                        done_count += 1
                        current = done_count
                except Exception:
                    async with counters_lock:
                        failed += 1
                        done_count += 1
                        current = done_count
            if current % 10 == 0 or current == total:
                try:
                    await progress_msg.edit(t("bo_processing", uid, done=current, total=total))
                except Exception:
                    pass

    await asyncio.gather(*[
        process_inbound_group(group) for group in by_group.values()
    ])

    try:
        await progress_msg.delete()
    except Exception:
        pass

    # Determine involved panels for logging/display
    involved_panels = sorted({pname for _, _, _, _, pname in clients})
    panels_str = ", ".join(involved_panels)

    log_activity(uid, "bulk_op", json.dumps({
        "panels": involved_panels, "op": op, "action": action, "value": value,
        "success": success, "failed": failed, "skipped": skipped,
    }))

    action_label = t("action_added", uid) if action == "add" else t("action_subtracted", uid)
    if op == "d":
        value_str = t("days_unit", uid, value=value)
    else:
        value_str = t("gb_unit", uid, value=value)

    lines = [
        t("bo_complete", uid),
        "",
        t("bo_operation", uid, action=action_label, value=value_str),
        t("sr_panel", uid, panel=panels_str),
        "",
        t("bo_success", uid, count=success),
        t("bo_failed", uid, count=failed),
        t("bo_skipped", uid, count=skipped),
    ]
    await bot.send_message(
        event.chat_id,
        "\n".join(lines),
        buttons=[[Button.inline(t("btn_back", uid), b"m")]],
        parse_mode="md",
    )
    clear(uid)


async def handle_bulk_op_manual(event):
    """Handle bo_manual text input (manual email entry). Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    if s.get("state") != "bo_manual":
        return False

    s["state"] = None
    bo_panels = s.get("bo_panels", set())

    emails = [line.strip() for line in event.text.strip().splitlines() if line.strip()]
    if not emails:
        s["state"] = "bo_manual"
        await event.respond(t("bo_manual_empty", uid))
        return True

    collected = []
    not_found = []
    for email in emails:
        found = False
        for panel_name in sorted(bo_panels):
            p = get_panel(panel_name)
            inbounds = await p.list_inbounds()
            inbounds = visible_inbounds(uid, panel_name, inbounds)
            for ib in inbounds:
                protocol = ib["protocol"]
                settings = json.loads(ib.get("settings", "{}"))
                for client in settings.get("clients", []):
                    if client.get("email", "").lower() == email.lower():
                        client_id = p.get_client_id(client, protocol)
                        collected.append((client, ib["id"], client_id, protocol, panel_name))
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if not found:
            not_found.append(email)

    s["bo_clients"] = collected
    s["bo_filter"] = "manual"
    await _show_manual_result(event, uid, len(collected), not_found)
    return True


async def _show_manual_result(event, uid, found_count, not_found):
    s = st(uid)
    lines = [t("bo_manual_result", uid, found=found_count, not_found=len(not_found))]
    if not_found and len(not_found) <= 10:
        lines.append("\n".join(f"  `{e}`" for e in not_found))
    btns = [
        [Button.inline(t("btn_days", uid), b"bot:d"), Button.inline(t("btn_traffic", uid), b"bot:t")],
        [Button.inline(t("btn_enable_all", uid), b"bot:en"), Button.inline(t("btn_disable_all", uid), b"bot:dis")],
        [Button.inline(t("btn_remove_all", uid), b"bot:rm")],
        [Button.inline(t("btn_export", uid), b"boe")],
        [Button.inline(t("btn_back", uid), _back_data(uid)),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await reply(event, "\n".join(lines), buttons=btns)


async def handle_bulk_op_input(event):
    """Handle bo_input text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state != "bo_input":
        return False

    s["state"] = None
    bo_op = s.get("bo_op")
    if bo_op == "d":
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "bo_input"
            await event.respond(t("bo_days_invalid", uid))
            return True
        if days <= 0:
            s["state"] = "bo_input"
            await event.respond(t("bo_days_positive", uid))
            return True
        s["bo_value"] = days
        if s.get("bo_action") == "add":
            await event.respond(
                t("start_after_use_prompt", uid),
                buttons=[
                    [Button.inline(t("btn_yes", uid), b"bosa:y"), Button.inline(t("btn_no", uid), b"bosa:n")],
                    [Button.inline(t("btn_back", uid), f"boa:{s['bo_action']}".encode()),
                     Button.inline(t("btn_main_menu", uid), b"m")],
                ],
            )
        else:
            await _bulk_op_execute(event, uid)
    else:
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "bo_input"
            await event.respond(t("bo_gb_invalid", uid))
            return True
        if gb <= 0:
            s["state"] = "bo_input"
            await event.respond(t("bo_gb_positive", uid))
            return True
        s["bo_value"] = gb
        await _bulk_op_execute(event, uid)
    return True


def _inbound_selector_buttons(uid, inbounds_with_panel, selected):
    """Build buttons for the inbound multi-select screen.

    inbounds_with_panel: list of (panel_name, inbound_dict)
    selected: set of (panel_name, iid)
    """
    s = st(uid)
    multi_panel = len(s.get("bo_panels", set())) > 1
    btns = []
    for panel_name, ib in inbounds_with_panel:
        iid = ib["id"]
        supported = ib.get("protocol", "") in SUPPORTED_PROTOCOLS
        prefix = f"[{panel_name}] " if multi_panel else ""
        if not supported:
            label = f"\u26a0\ufe0f {prefix}{ib['remark']} | {ib['port']}"
        elif (panel_name, iid) in selected:
            label = f"\u2705 {prefix}{ib['remark']} | {ib['port']}"
        else:
            label = f"\u2b1c {prefix}{ib['remark']} | {ib['port']}"
        btns.append([Button.inline(label, f"boi:{panel_name}:{iid}".encode())])
    btns.append([
        Button.inline(t("btn_select_all", uid), b"boia"),
        Button.inline(t("btn_deselect_all", uid), b"boid"),
    ])
    btns.append([Button.inline(t("btn_continue", uid), b"boic")])
    btns.append([Button.inline(t("btn_enter_manually", uid), b"bom")])
    btns.append([Button.inline(t("btn_back", uid), _back_data(uid)),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    return btns


async def _show_inbound_selector(event, uid):
    """Show the inbound multi-select screen."""
    s = st(uid)
    inbounds = s.get("bo_inbounds", [])
    selected = s.get("bo_selected", set())
    btns = _inbound_selector_buttons(uid, inbounds, selected)
    total = len(inbounds)
    sel_count = len(selected)
    await reply(
        event,
        t("bo_select_inbounds", uid, selected=sel_count, total=total),
        buttons=btns,
    )


async def _load_inbounds_for_panels(uid, panel_names):
    """Fetch and filter inbounds for all selected panels. Returns list of (panel_name, inbound)."""
    result = []
    for pname in sorted(panel_names):
        p = get_panel(pname)
        ibs = await p.list_inbounds()
        ibs = visible_inbounds(uid, pname, ibs)
        for ib in ibs:
            result.append((pname, ib))
    return result


def register(bot):

    # ── Main menu entry: panel multi-select ──────────────────────────

    @bot.on(events.CallbackQuery(data=b"bom_start"))
    @auth("bulk")
    async def cb_bulk_main_start(event):
        uid = event.sender_id
        clear(uid)
        s = st(uid)
        vpanels = visible_panels(uid)
        if not vpanels:
            return
        if len(vpanels) == 1:
            # Skip panel selector, go straight to inbound selector
            pname = next(iter(vpanels))
            s["bo_source"] = "main"
            s["bo_panels"] = {pname}
            s["bo_inbounds"] = await _load_inbounds_for_panels(uid, {pname})
            s["bo_selected"] = set()
            await _show_inbound_selector(event, uid)
            return
        s["bo_source"] = "main"
        s["bo_panels"] = set()
        btns = []
        for name in sorted(vpanels):
            label = f"\u2b1c {name}"
            btns.append([Button.inline(label, f"bop:{name}".encode())])
        btns.append([
            Button.inline(t("btn_select_all", uid), b"bopa"),
            Button.inline(t("btn_deselect_all", uid), b"bopd"),
        ])
        btns.append([Button.inline(t("btn_continue", uid), b"bopc")])
        btns.append([Button.inline(t("btn_back", uid), b"m")])
        await reply(event, t("bo_select_panels", uid, selected=0, total=len(vpanels)), buttons=btns)

    async def _show_panel_selector(event, uid):
        s = st(uid)
        vpanels = visible_panels(uid)
        selected = s.get("bo_panels", set())
        btns = []
        for name in sorted(vpanels):
            if name in selected:
                label = f"\u2705 {name}"
            else:
                label = f"\u2b1c {name}"
            btns.append([Button.inline(label, f"bop:{name}".encode())])
        btns.append([
            Button.inline(t("btn_select_all", uid), b"bopa"),
            Button.inline(t("btn_deselect_all", uid), b"bopd"),
        ])
        btns.append([Button.inline(t("btn_continue", uid), b"bopc")])
        btns.append([Button.inline(t("btn_back", uid), b"m")])
        await reply(event, t("bo_select_panels", uid, selected=len(selected), total=len(vpanels)), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^bop:(.+)$"))
    @auth("bulk")
    async def cb_bulk_toggle_panel(event):
        uid = event.sender_id
        name = event.pattern_match.group(1).decode()
        s = st(uid)
        selected = s.get("bo_panels", set())
        if name in selected:
            selected.discard(name)
        else:
            selected.add(name)
        s["bo_panels"] = selected
        await _show_panel_selector(event, uid)

    @bot.on(events.CallbackQuery(data=b"bopa"))
    @auth("bulk")
    async def cb_bulk_select_all_panels(event):
        uid = event.sender_id
        s = st(uid)
        s["bo_panels"] = set(visible_panels(uid))
        await _show_panel_selector(event, uid)

    @bot.on(events.CallbackQuery(data=b"bopd"))
    @auth("bulk")
    async def cb_bulk_deselect_all_panels(event):
        uid = event.sender_id
        s = st(uid)
        s["bo_panels"] = set()
        await _show_panel_selector(event, uid)

    @bot.on(events.CallbackQuery(data=b"bopc"))
    @auth("bulk")
    async def cb_bulk_panels_continue(event):
        uid = event.sender_id
        s = st(uid)
        selected = s.get("bo_panels", set())
        if not selected:
            await answer(event, t("bo_no_panel_selected", uid), alert=True)
            return
        s["bo_inbounds"] = await _load_inbounds_for_panels(uid, selected)
        s["bo_selected"] = set()
        await _show_inbound_selector(event, uid)

    # ── Per-panel shortcut (from panel sub-menu) ─────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^bo:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_start(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        if panel_name not in visible_panels(uid):
            return
        s = st(uid)
        s["bo_source"] = panel_name
        s["bo_panels"] = {panel_name}
        s["bo_inbounds"] = await _load_inbounds_for_panels(uid, {panel_name})
        s["bo_selected"] = set()
        await _show_inbound_selector(event, uid)

    # ── Inbound multi-select ─────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^boi:(.+):(\d+)$"))
    @auth("bulk")
    async def cb_bulk_toggle_inbound(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        s = st(uid)
        inbounds = s.get("bo_inbounds", [])
        # Block unsupported protocols
        ib = next((i for pn, i in inbounds if pn == panel_name and i["id"] == iid), None)
        if ib and ib.get("protocol", "") not in SUPPORTED_PROTOCOLS:
            await answer(event, t("unsupported_protocol_short", uid, protocol=ib["protocol"]), alert=True)
            return
        selected = s.get("bo_selected", set())
        key = (panel_name, iid)
        if key in selected:
            selected.discard(key)
        else:
            selected.add(key)
        s["bo_selected"] = selected
        await _show_inbound_selector(event, uid)

    @bot.on(events.CallbackQuery(data=b"boia"))
    @auth("bulk")
    async def cb_bulk_select_all(event):
        uid = event.sender_id
        s = st(uid)
        inbounds = s.get("bo_inbounds", [])
        selected = {(pn, ib["id"]) for pn, ib in inbounds if ib.get("protocol", "") in SUPPORTED_PROTOCOLS}
        s["bo_selected"] = selected
        await _show_inbound_selector(event, uid)

    @bot.on(events.CallbackQuery(data=b"boid"))
    @auth("bulk")
    async def cb_bulk_deselect_all(event):
        uid = event.sender_id
        s = st(uid)
        s["bo_selected"] = set()
        await _show_inbound_selector(event, uid)

    @bot.on(events.CallbackQuery(data=b"boic"))
    @auth("bulk")
    async def cb_bulk_continue(event):
        uid = event.sender_id
        s = st(uid)
        selected = s.get("bo_selected", set())
        if not selected:
            await answer(event, t("bo_no_inbound_selected", uid), alert=True)
            return
        await reply(
            event,
            t("bo_filter_title", uid),
            buttons=[
                [Button.inline(t("btn_only_enabled", uid), b"bof:en")],
                [Button.inline(t("btn_only_disabled", uid), b"bof:dis")],
                [Button.inline(t("btn_all_accounts", uid), b"bof:all")],
                [Button.inline(t("btn_back", uid), _back_data(uid)),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bof:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_filter(event):
        filt = event.pattern_match.group(1).decode()
        uid = event.sender_id
        s = st(uid)
        s["bo_filter"] = filt
        selected = s.get("bo_selected", set())

        collected = []
        for panel_name, iid in selected:
            p = get_panel(panel_name)
            inbounds = await p.list_inbounds()
            ib = next((i for i in inbounds if i["id"] == iid), None)
            if not ib:
                continue
            protocol = ib["protocol"]
            settings = json.loads(ib.get("settings", "{}"))
            for client in settings.get("clients", []):
                enabled = client.get("enable", True)
                if filt == "en" and not enabled:
                    continue
                if filt == "dis" and enabled:
                    continue
                client_id = p.get_client_id(client, protocol)
                collected.append((client, ib["id"], client_id, protocol, panel_name))

        s["bo_clients"] = collected
        await _show_filter_result(event, uid, filt, len(collected))

    async def _show_filter_result(event, uid, filt, count):
        filter_key = {"en": "filter_enabled", "dis": "filter_disabled", "all": "filter_all"}[filt]
        filter_label = t(filter_key, uid)
        await reply(
            event,
            t("bo_filter_result", uid, filter=filter_label, count=count),
            buttons=[
                [Button.inline(t("btn_days", uid), b"bot:d"), Button.inline(t("btn_traffic", uid), b"bot:t")],
                [Button.inline(t("btn_enable_all", uid), b"bot:en"), Button.inline(t("btn_disable_all", uid), b"bot:dis")],
                [Button.inline(t("btn_remove_all", uid), b"bot:rm")],
                [Button.inline(t("btn_export", uid), b"boe")],
                [Button.inline(t("btn_back", uid), _back_data(uid)),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bot:([dt])$"))
    @auth("bulk")
    async def cb_bulk_op_type(event):
        uid = event.sender_id
        op = event.pattern_match.group(1).decode()
        s = st(uid)
        s["bo_op"] = op
        label = t("op_days", uid) if op == "d" else t("op_traffic", uid)
        filt = s.get("bo_filter", "all")
        if filt == "manual":
            back_data = _back_data(uid)
        else:
            back_data = f"bof:{filt}".encode()
        await reply(
            event,
            t("bo_type_title", uid, type=label),
            buttons=[
                [Button.inline(t("btn_add", uid), b"boa:add"), Button.inline(t("btn_subtract", uid), b"boa:sub")],
                [Button.inline(t("btn_back", uid), back_data),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^boa:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_action(event):
        uid = event.sender_id
        action = event.pattern_match.group(1).decode()
        s = st(uid)
        s["bo_action"] = action
        s["state"] = "bo_input"
        op = s["bo_op"]
        if op == "d":
            verb = t("verb_add", uid) if action == "add" else t("verb_subtract", uid)
            prompt = t("bo_days_prompt", uid, verb=verb)
        else:
            verb = t("verb_add", uid) if action == "add" else t("verb_subtract", uid)
            prompt = t("bo_traffic_prompt", uid, verb=verb)
        await reply(
            event,
            prompt,
            buttons=[[Button.inline(t("btn_back", uid), f"bot:{op}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bosa:([yn])$"))
    @auth("bulk")
    async def cb_bulk_op_sau(event):
        uid = event.sender_id
        choice = event.pattern_match.group(1).decode()
        s = st(uid)
        s["bo_sau"] = choice == "y"
        await _bulk_op_execute(event, uid)

    # ── Bulk Enable / Disable / Remove ─────────────────────────────────

    async def _bulk_action_execute(event, uid, action):
        """Execute bulk enable, disable, or remove on collected clients."""
        s = st(uid)
        clients = s.get("bo_clients", [])
        if not clients:
            await reply(
                event,
                t("bo_no_accounts", uid),
                buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            )
            clear(uid)
            return

        progress_msg = await bot.send_message(
            event.chat_id,
            t("bo_processing", uid, done=0, total=len(clients)),
        )

        success = 0
        failed = 0
        done_count = 0
        total = len(clients)
        counters_lock = asyncio.Lock()

        by_group: dict[tuple[str, int], list] = {}
        for client, iid, cid, proto, pname in clients:
            by_group.setdefault((pname, iid), []).append((client, iid, cid, proto, pname))

        async def process_group(group):
            nonlocal success, failed, done_count
            panel_name = group[0][4]
            p = get_panel(panel_name)
            for client, inbound_id, client_id, protocol, _pn in group:
                try:
                    if action == "remove":
                        await p.delete_client(inbound_id, client_id)
                    else:
                        client["enable"] = action == "enable"
                        await p.update_client(client_id, inbound_id, client)
                    async with counters_lock:
                        success += 1
                        done_count += 1
                        current = done_count
                except Exception:
                    async with counters_lock:
                        failed += 1
                        done_count += 1
                        current = done_count
                if current % 10 == 0 or current == total:
                    try:
                        await progress_msg.edit(t("bo_processing", uid, done=current, total=total))
                    except Exception:
                        pass

        await asyncio.gather(*[process_group(group) for group in by_group.values()])

        try:
            await progress_msg.delete()
        except Exception:
            pass

        involved_panels = sorted({pname for _, _, _, _, pname in clients})
        panels_str = ", ".join(involved_panels)

        log_activity(uid, f"bulk_{action}", json.dumps({
            "panels": involved_panels, "success": success, "failed": failed,
        }))

        title_key = {"enable": "bo_enable_success", "disable": "bo_disable_success", "remove": "bo_remove_success"}[action]
        lines = [
            t(title_key, uid),
            "",
            t("sr_panel", uid, panel=panels_str),
            "",
            t("bo_success", uid, count=success),
            t("bo_failed", uid, count=failed),
        ]
        await bot.send_message(
            event.chat_id,
            "\n".join(lines),
            buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            parse_mode="md",
        )
        clear(uid)

    @bot.on(events.CallbackQuery(data=b"bot:en"))
    @auth("bulk")
    async def cb_bulk_enable(event):
        await _bulk_action_execute(event, event.sender_id, "enable")

    @bot.on(events.CallbackQuery(data=b"bot:dis"))
    @auth("bulk")
    async def cb_bulk_disable(event):
        await _bulk_action_execute(event, event.sender_id, "disable")

    @bot.on(events.CallbackQuery(data=b"bot:rm"))
    @auth("bulk")
    async def cb_bulk_remove_confirm(event):
        uid = event.sender_id
        s = st(uid)
        clients = s.get("bo_clients", [])
        if not clients:
            return
        await reply(
            event,
            t("bo_confirm_remove", uid, count=len(clients)),
            buttons=[
                [Button.inline(t("btn_yes_remove_all", uid), b"bot:rmc"),
                 Button.inline(t("btn_cancel", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"bot:rmc"))
    @auth("bulk")
    async def cb_bulk_remove_execute(event):
        await _bulk_action_execute(event, event.sender_id, "remove")

    # ── Enter Manually ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"bom"))
    @auth("bulk")
    async def cb_bulk_manual(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "bo_manual"
        await reply(
            event,
            t("bo_manual_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), _back_data(uid)),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )

    # ── Export ──────────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"boe"))
    @auth("bulk")
    async def cb_bulk_export(event):
        uid = event.sender_id
        s = st(uid)
        if not s.get("bo_clients"):
            return
        btns = [
            [Button.inline(t("btn_pdf", uid), b"boef:pdf"),
             Button.inline(t("btn_txt", uid), b"boef:txt")],
            [Button.inline(t("btn_pdf_txt", uid), b"boef:both")],
            [Button.inline(t("btn_back", uid), _back_data(uid)),
             Button.inline(t("btn_main_menu", uid), b"m")],
        ]
        await reply(event, t("bo_export_title", uid), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^boef:(pdf|txt|both)$"))
    @auth("bulk")
    async def cb_bulk_export_format(event):
        uid = event.sender_id
        fmt = event.pattern_match.group(1).decode()
        s = st(uid)
        clients = s.get("bo_clients", [])
        if not clients:
            return

        await answer(event, t("bo_exporting", uid))

        # Build inbound maps per panel
        ib_maps: dict[str, dict[int, dict]] = {}
        involved_panels = {pname for _, _, _, _, pname in clients}
        for pname in involved_panels:
            p = get_panel(pname)
            ibs = await p.list_inbounds()
            ib_maps[pname] = {ib["id"]: ib for ib in ibs}

        unlim = t("unlimited", uid)
        accounts = []
        txt_lines = []
        for client, iid, cid, protocol, panel_name in clients:
            ib = ib_maps.get(panel_name, {}).get(iid)
            if not ib:
                continue
            email = client.get("email", "")
            addr = server_addrs.get(panel_name, "")
            if protocol in SUPPORTED_PROTOCOLS:
                proxy_link = build_client_link(client, ib, addr)
            else:
                proxy_link = ""
            sub_url = sub_urls.get(panel_name)
            sub_id = client.get("subId", "")
            sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None
            total = client.get("totalGB", 0)
            traffic_str = format_bytes(total) if total > 0 else unlim
            duration_str = format_expiry(client.get("expiryTime", 0), uid)
            qr_img = make_qr(proxy_link) if proxy_link else None

            accounts.append({
                "email": email,
                "proxy_link": proxy_link or "",
                "qr_image": qr_img,
                "traffic": traffic_str,
                "duration": duration_str,
                "sub_link": sub_link,
                "panel": panel_name,
            })

            # Build TXT block
            block = [f"Email: {email}"]
            block.append(f"Panel: {panel_name}")
            block.append(f"Traffic: {traffic_str}")
            block.append(f"Duration: {duration_str}")
            if proxy_link:
                block.append(f"Link: {proxy_link}")
            if sub_link:
                block.append(f"Subscription: {sub_link}")
            block.append("")
            txt_lines.extend(block)

        panels_str = ", ".join(sorted(involved_panels))

        if fmt in ("pdf", "both") and accounts:
            pdf_buf = generate_account_pdf(
                accounts,
                title=t("bo_export_pdf_title", uid, panel=panels_str),
                uid=uid,
            )
            await bot.send_file(event.chat_id, pdf_buf,
                                caption=t("bo_export_done", uid))

        if fmt in ("txt", "both") and txt_lines:
            txt_buf = io.BytesIO("\n".join(txt_lines).encode("utf-8"))
            txt_buf.name = "accounts-bulk.txt"
            await bot.send_file(event.chat_id, txt_buf,
                                caption=t("bo_export_done", uid))

        log_activity(uid, "bulk_export", json.dumps({
            "panels": sorted(involved_panels), "format": fmt, "count": len(accounts),
        }))
