import asyncio
import json
import time

from telethon import events, Button

from config import get_panel, st, clear, bot
from helpers import auth, reply


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
            "⚠️ No accounts to process.",
            buttons=[[Button.inline("◀️ Back", b"m")]],
        )
        clear(uid)
        return

    progress_msg = await bot.send_message(
        event.chat_id,
        f"⏳ Processing 0/{len(clients)}...",
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
                    await progress_msg.edit(f"⏳ Processing {current}/{total}...")
                except Exception:
                    pass

    await asyncio.gather(*[
        process_inbound_group(group) for group in by_inbound.values()
    ])

    try:
        await progress_msg.delete()
    except Exception:
        pass

    op_label = "Days" if op == "d" else "Traffic"
    action_label = "Added" if action == "add" else "Subtracted"
    if op == "d":
        value_str = f"{value} day(s)"
    else:
        value_str = f"{value} GB"

    lines = [
        f"⚡ **Bulk Operation Complete**",
        "",
        f"Operation: {action_label} {value_str}",
        f"Panel: {panel_name}",
        "",
        f"✅ Success: {success}",
        f"❌ Failed: {failed}",
        f"⏭ Skipped (unlimited): {skipped}",
    ]
    await bot.send_message(
        event.chat_id,
        "\n".join(lines),
        buttons=[[Button.inline("◀️ Back", b"m")]],
        parse_mode="md",
    )
    clear(uid)


async def handle_bulk_op_input(event):
    """Handle bo_input text input. Returns True if handled."""
    s = st(event.sender_id)
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
            await event.respond("⚠️ Invalid number. Enter days:")
            return True
        if days <= 0:
            s["state"] = "bo_input"
            await event.respond("⚠️ Must be positive. Enter days:")
            return True
        s["bo_value"] = days
        if s.get("bo_action") == "add":
            await event.respond(
                "⏱ Start timer after first use?",
                buttons=[
                    [Button.inline("✅ Yes", b"bosa:y"), Button.inline("❌ No", b"bosa:n")],
                    [Button.inline("◀️ Back", f"bo:{s['bo_pid']}".encode())],
                ],
            )
        else:
            await _bulk_op_execute(event, event.sender_id)
    else:
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "bo_input"
            await event.respond("⚠️ Invalid number. Enter GB:")
            return True
        if gb <= 0:
            s["state"] = "bo_input"
            await event.respond("⚠️ Must be positive. Enter GB:")
            return True
        s["bo_value"] = gb
        await _bulk_op_execute(event, event.sender_id)
    return True


def register(bot):
    @bot.on(events.CallbackQuery(pattern=rb"^bo:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_start(event):
        panel_name = event.pattern_match.group(1).decode()
        s = st(event.sender_id)
        s["bo_pid"] = panel_name
        await reply(
            event,
            "⚡ **Bulk Operation**\nFilter accounts:",
            buttons=[
                [Button.inline("✅ Only Enabled", b"bof:en")],
                [Button.inline("🔴 Only Disabled", b"bof:dis")],
                [Button.inline("📋 All Accounts", b"bof:all")],
                [Button.inline("◀️ Back", f"il:{panel_name}".encode())],
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

        inbounds = await p.list_inbounds()
        collected = []
        for ib in inbounds:
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
        filter_label = {"en": "Enabled", "dis": "Disabled", "all": "All"}[filt]
        await reply(
            event,
            f"⚡ **Bulk Operation**\n"
            f"Filter: {filter_label}\n"
            f"Found **{len(collected)}** account(s)\n\n"
            "Choose operation:",
            buttons=[
                [Button.inline("⏳ Days", b"bot:d"), Button.inline("📊 Traffic", b"bot:t")],
                [Button.inline("◀️ Back", f"bo:{panel_name}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bot:([dt])$"))
    @auth("bulk")
    async def cb_bulk_op_type(event):
        op = event.pattern_match.group(1).decode()
        s = st(event.sender_id)
        s["bo_op"] = op
        label = "Days" if op == "d" else "Traffic"
        await reply(
            event,
            f"⚡ **Bulk Operation — {label}**\nChoose action:",
            buttons=[
                [Button.inline("➕ Add", b"boa:add"), Button.inline("➖ Subtract", b"boa:sub")],
                [Button.inline("◀️ Back", f"bo:{s['bo_pid']}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^boa:(.+)$"))
    @auth("bulk")
    async def cb_bulk_op_action(event):
        action = event.pattern_match.group(1).decode()
        s = st(event.sender_id)
        s["bo_action"] = action
        s["state"] = "bo_input"
        op = s["bo_op"]
        if op == "d":
            verb = "add" if action == "add" else "subtract"
            prompt = f"⚡ Enter number of days to {verb}:"
        else:
            verb = "add" if action == "add" else "subtract"
            prompt = f"⚡ Enter GB to {verb}:"
        await reply(
            event,
            prompt,
            buttons=[[Button.inline("◀️ Back", f"bo:{s['bo_pid']}".encode())]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bosa:([yn])$"))
    @auth("bulk")
    async def cb_bulk_op_sau(event):
        choice = event.pattern_match.group(1).decode()
        s = st(event.sender_id)
        s["bo_sau"] = choice == "y"
        await _bulk_op_execute(event, event.sender_id)
