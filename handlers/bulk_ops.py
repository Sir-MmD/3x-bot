import asyncio
import json
import time

from telethon import events, Button

from config import get_panel, st, clear, bot, visible_panels, visible_inbounds
from db import log_activity
from helpers import auth, reply
from i18n import t


async def _bulk_op_execute(event, uid: int):
    s = st(uid)
    clients = s.get("bo_clients", [])
    op = s["bo_op"]
    action = s["bo_action"]
    value = s["bo_value"]
    panel_name = s["bo_pid"]
    p = get_panel(panel_name)

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

    # Group clients by inbound_id so updates within the same inbound
    # run sequentially (3x-ui stores all clients as a JSON array inside
    # the inbound settings — concurrent writes to the same inbound
    # cause a read-modify-write race that silently drops changes).
    by_inbound: dict[int, list] = {}
    for client, iid, cid, proto in clients:
        by_inbound.setdefault(iid, []).append((client, iid, cid, proto))

    async def process_inbound_group(group):
        nonlocal success, failed, skipped, done_count
        for client, inbound_id, client_id, protocol in group:
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
        process_inbound_group(group) for group in by_inbound.values()
    ])

    try:
        await progress_msg.delete()
    except Exception:
        pass

    log_activity(uid, "bulk_op", json.dumps({
        "panel": panel_name, "op": op, "action": action, "value": value,
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
        t("sr_panel", uid, panel=panel_name),
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
                    [Button.inline(t("btn_back", uid), f"boa:{s['bo_action']}".encode())],
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


def _inbound_selector_buttons(uid, panel_name, inbounds, selected):
    """Build buttons for the inbound multi-select screen."""
    btns = []
    for ib in inbounds:
        iid = ib["id"]
        icon = "\u2705" if iid in selected else "\u2b1c"
        label = f"{icon} {ib['remark']} | {ib['port']}"
        btns.append([Button.inline(label, f"boi:{panel_name}:{iid}".encode())])
    btns.append([
        Button.inline(t("btn_select_all", uid), f"boia:{panel_name}".encode()),
        Button.inline(t("btn_deselect_all", uid), f"boid:{panel_name}".encode()),
    ])
    btns.append([Button.inline(t("btn_continue", uid), f"boc:{panel_name}".encode())])
    btns.append([Button.inline(t("btn_back", uid), f"pm:{panel_name}".encode())])
    return btns


def register(bot):
    @bot.on(events.CallbackQuery(pattern=rb"^bo:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_start(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        if panel_name not in visible_panels(uid):
            return
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        inbounds = visible_inbounds(uid, panel_name, inbounds)
        s = st(uid)
        s["bo_pid"] = panel_name
        s["bo_inbounds"] = inbounds
        selected = set()
        s["bo_selected"] = selected
        btns = _inbound_selector_buttons(uid, panel_name, inbounds, selected)
        await reply(
            event,
            t("bo_select_inbounds", uid, selected=0, total=len(inbounds)),
            buttons=btns,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^boi:(.+):(\d+)$"))
    @auth("bulk")
    async def cb_bulk_toggle_inbound(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        s = st(uid)
        selected = s.get("bo_selected", set())
        if iid in selected:
            selected.discard(iid)
        else:
            selected.add(iid)
        s["bo_selected"] = selected
        inbounds = s.get("bo_inbounds", [])
        btns = _inbound_selector_buttons(uid, panel_name, inbounds, selected)
        await reply(
            event,
            t("bo_select_inbounds", uid, selected=len(selected), total=len(inbounds)),
            buttons=btns,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^boia:(.+)$"))
    @auth("bulk")
    async def cb_bulk_select_all(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        s = st(uid)
        inbounds = s.get("bo_inbounds", [])
        selected = {ib["id"] for ib in inbounds}
        s["bo_selected"] = selected
        btns = _inbound_selector_buttons(uid, panel_name, inbounds, selected)
        await reply(
            event,
            t("bo_select_inbounds", uid, selected=len(selected), total=len(inbounds)),
            buttons=btns,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^boid:(.+)$"))
    @auth("bulk")
    async def cb_bulk_deselect_all(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        s = st(uid)
        inbounds = s.get("bo_inbounds", [])
        selected = set()
        s["bo_selected"] = selected
        btns = _inbound_selector_buttons(uid, panel_name, inbounds, selected)
        await reply(
            event,
            t("bo_select_inbounds", uid, selected=0, total=len(inbounds)),
            buttons=btns,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^boc:(.+)$"))
    @auth("bulk")
    async def cb_bulk_continue(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        s = st(uid)
        selected = s.get("bo_selected", set())
        if not selected:
            await event.answer(t("bo_no_inbound_selected", uid), alert=True)
            return
        await reply(
            event,
            t("bo_filter_title", uid),
            buttons=[
                [Button.inline(t("btn_only_enabled", uid), b"bof:en")],
                [Button.inline(t("btn_only_disabled", uid), b"bof:dis")],
                [Button.inline(t("btn_all_accounts", uid), b"bof:all")],
                [Button.inline(t("btn_back", uid), f"bo:{panel_name}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bof:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_filter(event):
        filt = event.pattern_match.group(1).decode()
        uid = event.sender_id
        s = st(uid)
        s["bo_filter"] = filt
        panel_name = s["bo_pid"]
        p = get_panel(panel_name)
        selected = s.get("bo_selected", set())

        inbounds = await p.list_inbounds()
        collected = []
        for ib in inbounds:
            if ib["id"] not in selected:
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
                collected.append((client, ib["id"], client_id, protocol))

        s["bo_clients"] = collected
        await _show_filter_result(event, uid, filt, len(collected))

    async def _show_filter_result(event, uid, filt, count):
        s = st(uid)
        panel_name = s["bo_pid"]
        filter_key = {"en": "filter_enabled", "dis": "filter_disabled", "all": "filter_all"}[filt]
        filter_label = t(filter_key, uid)
        await reply(
            event,
            t("bo_filter_result", uid, filter=filter_label, count=count),
            buttons=[
                [Button.inline(t("btn_days", uid), b"bot:d"), Button.inline(t("btn_traffic", uid), b"bot:t")],
                [Button.inline(t("btn_back", uid), f"boc:{panel_name}".encode())],
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
        await reply(
            event,
            t("bo_type_title", uid, type=label),
            buttons=[
                [Button.inline(t("btn_add", uid), b"boa:add"), Button.inline(t("btn_subtract", uid), b"boa:sub")],
                [Button.inline(t("btn_back", uid), f"bof:{filt}".encode())],
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
            buttons=[[Button.inline(t("btn_back", uid), f"bot:{op}".encode())]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bosa:([yn])$"))
    @auth("bulk")
    async def cb_bulk_op_sau(event):
        uid = event.sender_id
        choice = event.pattern_match.group(1).decode()
        s = st(uid)
        s["bo_sau"] = choice == "y"
        await _bulk_op_execute(event, uid)
