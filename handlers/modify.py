import time

from telethon import events, Button

from config import get_panel, st
from helpers import format_bytes, format_expiry, auth, reply
from handlers.search import show_search_result


async def handle_modify_traffic_input(event):
    """Handle mt_edit, mt_add, mt_sub text input. Returns True if handled."""
    s = st(event.sender_id)
    state = s.get("state")

    if state == "mt_edit":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_edit"
            await event.respond("⚠️ Invalid number. Enter traffic in GB (0 = unlimited):")
            return True
        new_bytes = int(gb * 1024**3) if gb > 0 else 0
        client = s["sr_client"]
        client["totalGB"] = new_bytes
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond("✅ Traffic limit updated.")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return True

    if state == "mt_add":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_add"
            await event.respond("⚠️ Invalid number. Enter GB to add:")
            return True
        if gb <= 0:
            s["state"] = "mt_add"
            await event.respond("⚠️ Must be positive. Enter GB to add:")
            return True
        client = s["sr_client"]
        if client.get("totalGB", 0) == 0:
            await event.respond("⚠️ Traffic is already unlimited.")
            await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return True
        client["totalGB"] += int(gb * 1024**3)
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond("✅ Traffic added.")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return True

    if state == "mt_sub":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_sub"
            await event.respond("⚠️ Invalid number. Enter GB to subtract:")
            return True
        if gb <= 0:
            s["state"] = "mt_sub"
            await event.respond("⚠️ Must be positive. Enter GB to subtract:")
            return True
        client = s["sr_client"]
        cur = client.get("totalGB", 0)
        if cur == 0:
            await event.respond("⚠️ Traffic is unlimited, nothing to subtract from.")
            await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return True
        sub_bytes = int(gb * 1024**3)
        client["totalGB"] = max(0, cur - sub_bytes)
        if client["totalGB"] == 0:
            client["totalGB"] = 1  # avoid setting to unlimited; use Edit Total for that
            await event.respond("⚠️ Result would be 0 (unlimited). Set to minimum instead.")
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(f"✅ Subtracted {gb} GB.")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return True

    return False


async def handle_modify_days_input(event):
    """Handle md_edit, md_add, md_sub text input. Returns True if handled."""
    s = st(event.sender_id)
    state = s.get("state")

    if state == "md_edit":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_edit"
            await event.respond("⚠️ Invalid number. Enter days (0 = unlimited):")
            return True
        if days == 0:
            s["sr_client"]["expiryTime"] = 0
            p = get_panel(s["sr_pid"])
            try:
                await p.update_client(s["sr_cid"], s["sr_iid"], s["sr_client"])
                await event.respond("✅ Duration set to unlimited.")
            except RuntimeError as e:
                await event.respond(f"⚠️ Error: {e}")
            await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        else:
            s["md_days"] = days
            s["state"] = "md_sau"
            await event.respond(
                "⏱ Start timer after first use?",
                buttons=[
                    [Button.inline("✅ Yes", b"mdsa:y"), Button.inline("❌ No", b"mdsa:n")],
                    [Button.inline("◀️ Back", b"sr")],
                ],
            )
        return True

    if state == "md_add":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_add"
            await event.respond("⚠️ Invalid number. Enter days to add:")
            return True
        if days <= 0:
            s["state"] = "md_add"
            await event.respond("⚠️ Must be positive. Enter days to add:")
            return True
        client = s["sr_client"]
        cur = client.get("expiryTime", 0)
        add_ms = days * 86_400_000
        if cur == 0:
            await event.respond("⚠️ Duration is already unlimited.")
            await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return True
        if cur < 0:
            client["expiryTime"] = cur - add_ms  # more negative = longer relative duration
        else:
            client["expiryTime"] = cur + add_ms
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(f"✅ Added {days} day(s).")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return True

    if state == "md_sub":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_sub"
            await event.respond("⚠️ Invalid number. Enter days to subtract:")
            return True
        if days <= 0:
            s["state"] = "md_sub"
            await event.respond("⚠️ Must be positive. Enter days to subtract:")
            return True
        client = s["sr_client"]
        cur = client.get("expiryTime", 0)
        sub_ms = days * 86_400_000
        if cur == 0:
            await event.respond("⚠️ Duration is unlimited, nothing to subtract from.")
            await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return True
        if cur < 0:
            # Relative duration: less negative = shorter
            new_val = cur + sub_ms
            if new_val >= 0:
                new_val = -86_400_000  # minimum 1 day
                await event.respond("⚠️ Result too low. Set to minimum 1 day.")
            client["expiryTime"] = new_val
        else:
            # Absolute: subtract but don't go below now
            new_val = cur - sub_ms
            now_ms = int(time.time() * 1000)
            if new_val <= now_ms:
                new_val = now_ms + 86_400_000  # minimum 1 day from now
                await event.respond("⚠️ Result would be in the past. Set to 1 day from now.")
            client["expiryTime"] = new_val
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(f"✅ Subtracted {days} day(s).")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return True

    return False


def register(bot):
    @bot.on(events.CallbackQuery(data=b"mt"))
    @auth
    async def cb_modify_traffic(event):
        s = st(event.sender_id)
        client = s.get("sr_client")
        if not client:
            return
        traffic = s.get("sr_traffic") or {}
        total = client.get("totalGB", 0)
        up = traffic.get("up", 0)
        down = traffic.get("down", 0)
        lines = [
            "📊 **Modify Traffic**",
            "",
            f"📦 Limit: {format_bytes(total) if total > 0 else 'Unlimited'}",
            f"📊 Used: ↑ {format_bytes(up)}  ↓ {format_bytes(down)}",
        ]
        await reply(
            event,
            "\n".join(lines),
            buttons=[
                [
                    Button.inline("📝 Edit Total", b"mte"),
                    Button.inline("🔄 Reset", b"mtr"),
                ],
                [
                    Button.inline("➕ Add More", b"mta"),
                    Button.inline("➖ Less", b"mts"),
                ],
                [Button.inline("◀️ Back", b"sr")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"mte"))
    @auth
    async def cb_mt_edit(event):
        s = st(event.sender_id)
        s["state"] = "mt_edit"
        await reply(
            event,
            "📝 Enter new traffic limit in GB (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", b"mt")]],
        )

    @bot.on(events.CallbackQuery(data=b"mtr"))
    @auth
    async def cb_mt_reset(event):
        await reply(
            event,
            "⚠️ Reset used traffic to zero?",
            buttons=[
                [
                    Button.inline("✅ Yes, Reset", b"mtrc"),
                    Button.inline("❌ Cancel", b"mt"),
                ],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"mtrc"))
    @auth
    async def cb_mt_reset_confirm(event):
        s = st(event.sender_id)
        iid = s.get("sr_iid")
        email = s.get("sr_email")
        pid = s.get("sr_pid")
        if not iid or not email or not pid:
            return
        p = get_panel(pid)
        try:
            await p.reset_client_traffic(iid, email)
            await event.answer("✅ Traffic reset.")
        except RuntimeError as e:
            await event.answer(f"Error: {e}", alert=True)
        await show_search_result(event, event.sender_id, email, panel_name=pid)

    @bot.on(events.CallbackQuery(data=b"mta"))
    @auth
    async def cb_mt_add(event):
        s = st(event.sender_id)
        s["state"] = "mt_add"
        await reply(
            event,
            "➕ Enter GB to add:",
            buttons=[[Button.inline("◀️ Back", b"mt")]],
        )

    @bot.on(events.CallbackQuery(data=b"mts"))
    @auth
    async def cb_mt_sub(event):
        s = st(event.sender_id)
        s["state"] = "mt_sub"
        await reply(
            event,
            "➖ Enter GB to subtract:",
            buttons=[[Button.inline("◀️ Back", b"mt")]],
        )

    # ── Modify Days ──────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"md"))
    @auth
    async def cb_modify_days(event):
        s = st(event.sender_id)
        client = s.get("sr_client")
        if not client:
            return
        expiry = client.get("expiryTime", 0)
        lines = [
            "⏳ **Modify Duration**",
            "",
            f"⏳ Current: {format_expiry(expiry)}",
        ]
        await reply(
            event,
            "\n".join(lines),
            buttons=[
                [
                    Button.inline("📝 Edit Total", b"mde"),
                ],
                [
                    Button.inline("➕ Add More", b"mda"),
                    Button.inline("➖ Less", b"mds"),
                ],
                [Button.inline("◀️ Back", b"sr")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"mde"))
    @auth
    async def cb_md_edit(event):
        s = st(event.sender_id)
        s["state"] = "md_edit"
        await reply(
            event,
            "📝 Enter new duration in days (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", b"md")]],
        )

    @bot.on(events.CallbackQuery(data=b"mda"))
    @auth
    async def cb_md_add(event):
        s = st(event.sender_id)
        s["state"] = "md_add"
        await reply(
            event,
            "➕ Enter days to add:",
            buttons=[[Button.inline("◀️ Back", b"md")]],
        )

    @bot.on(events.CallbackQuery(data=b"mds"))
    @auth
    async def cb_md_sub(event):
        s = st(event.sender_id)
        s["state"] = "md_sub"
        await reply(
            event,
            "➖ Enter days to subtract:",
            buttons=[[Button.inline("◀️ Back", b"md")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^mdsa:([yn])$"))
    @auth
    async def cb_md_sau(event):
        s = st(event.sender_id)
        choice = event.pattern_match.group(1)
        days = s.get("md_days", 0)
        dur_ms = days * 86_400_000
        if choice == b"y":
            s["sr_client"]["expiryTime"] = -dur_ms
        else:
            s["sr_client"]["expiryTime"] = int(time.time() * 1000) + dur_ms
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], s["sr_client"])
            await event.answer("✅ Duration updated.")
        except RuntimeError as e:
            await event.answer(f"Error: {e}", alert=True)
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
