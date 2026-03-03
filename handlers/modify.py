import time

from telethon import events, Button

from config import get_panel, st
from helpers import format_bytes, format_expiry, auth, reply
from i18n import t
from handlers.search import show_search_result


async def handle_modify_traffic_input(event):
    """Handle mt_edit, mt_add, mt_sub text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "mt_edit":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_edit"
            await event.respond(t("mt_edit_invalid", uid))
            return True
        new_bytes = int(gb * 1024**3) if gb > 0 else 0
        client = s["sr_client"]
        client["totalGB"] = new_bytes
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(t("mt_edit_success", uid))
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e))
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
        return True

    if state == "mt_add":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_add"
            await event.respond(t("mt_add_invalid", uid))
            return True
        if gb <= 0:
            s["state"] = "mt_add"
            await event.respond(t("mt_add_positive", uid))
            return True
        client = s["sr_client"]
        if client.get("totalGB", 0) == 0:
            await event.respond(t("mt_already_unlimited", uid))
            await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
            return True
        client["totalGB"] += int(gb * 1024**3)
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(t("mt_add_success", uid))
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e))
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
        return True

    if state == "mt_sub":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_sub"
            await event.respond(t("mt_sub_invalid", uid))
            return True
        if gb <= 0:
            s["state"] = "mt_sub"
            await event.respond(t("mt_sub_positive", uid))
            return True
        client = s["sr_client"]
        cur = client.get("totalGB", 0)
        if cur == 0:
            await event.respond(t("mt_unlimited_cant_sub", uid))
            await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
            return True
        sub_bytes = int(gb * 1024**3)
        client["totalGB"] = max(0, cur - sub_bytes)
        if client["totalGB"] == 0:
            client["totalGB"] = 1  # avoid setting to unlimited; use Edit Total for that
            await event.respond(t("mt_sub_zero_warning", uid))
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(t("mt_sub_success", uid, gb=gb))
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e))
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
        return True

    return False


async def handle_modify_days_input(event):
    """Handle md_edit, md_add, md_sub text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "md_edit":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_edit"
            await event.respond(t("md_edit_invalid", uid))
            return True
        if days == 0:
            s["sr_client"]["expiryTime"] = 0
            p = get_panel(s["sr_pid"])
            try:
                await p.update_client(s["sr_cid"], s["sr_iid"], s["sr_client"])
                await event.respond(t("md_unlimited_success", uid))
            except RuntimeError as e:
                await event.respond(t("error_msg", uid, error=e))
            await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
        else:
            s["md_days"] = days
            s["state"] = "md_sau"
            await event.respond(
                t("start_after_use_prompt", uid),
                buttons=[
                    [Button.inline(t("btn_yes", uid), b"mdsa:y"), Button.inline(t("btn_no", uid), b"mdsa:n")],
                    [Button.inline(t("btn_back", uid), b"sr")],
                ],
            )
        return True

    if state == "md_add":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_add"
            await event.respond(t("md_add_invalid", uid))
            return True
        if days <= 0:
            s["state"] = "md_add"
            await event.respond(t("md_add_positive", uid))
            return True
        client = s["sr_client"]
        cur = client.get("expiryTime", 0)
        add_ms = days * 86_400_000
        if cur == 0:
            await event.respond(t("md_already_unlimited", uid))
            await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
            return True
        if cur < 0:
            client["expiryTime"] = cur - add_ms  # more negative = longer relative duration
        else:
            client["expiryTime"] = cur + add_ms
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(t("md_add_success", uid, days=days))
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e))
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
        return True

    if state == "md_sub":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_sub"
            await event.respond(t("md_sub_invalid", uid))
            return True
        if days <= 0:
            s["state"] = "md_sub"
            await event.respond(t("md_sub_positive", uid))
            return True
        client = s["sr_client"]
        cur = client.get("expiryTime", 0)
        sub_ms = days * 86_400_000
        if cur == 0:
            await event.respond(t("md_unlimited_cant_sub", uid))
            await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
            return True
        if cur < 0:
            # Relative duration: less negative = shorter
            new_val = cur + sub_ms
            if new_val >= 0:
                new_val = -86_400_000  # minimum 1 day
                await event.respond(t("md_sub_too_low", uid))
            client["expiryTime"] = new_val
        else:
            # Absolute: subtract but don't go below now
            new_val = cur - sub_ms
            now_ms = int(time.time() * 1000)
            if new_val <= now_ms:
                new_val = now_ms + 86_400_000  # minimum 1 day from now
                await event.respond(t("md_sub_past", uid))
            client["expiryTime"] = new_val
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(t("md_sub_success", uid, days=days))
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e))
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
        return True

    return False


def register(bot):
    @bot.on(events.CallbackQuery(data=b"mt"))
    @auth("modify")
    async def cb_modify_traffic(event):
        uid = event.sender_id
        s = st(uid)
        client = s.get("sr_client")
        if not client:
            return
        traffic = s.get("sr_traffic") or {}
        total = client.get("totalGB", 0)
        up = traffic.get("up", 0)
        down = traffic.get("down", 0)
        unlim = t("unlimited", uid)
        lines = [
            t("modify_traffic_title", uid),
            "",
            t("sr_limit", uid, limit=format_bytes(total) if total > 0 else unlim),
            t("modify_traffic_used", uid, up=format_bytes(up), down=format_bytes(down)),
        ]
        await reply(
            event,
            "\n".join(lines),
            buttons=[
                [
                    Button.inline(t("btn_edit_total", uid), b"mte"),
                    Button.inline(t("btn_reset", uid), b"mtr"),
                ],
                [
                    Button.inline(t("btn_add_more", uid), b"mta"),
                    Button.inline(t("btn_less", uid), b"mts"),
                ],
                [Button.inline(t("btn_back", uid), b"sr")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"mte"))
    @auth("modify")
    async def cb_mt_edit(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "mt_edit"
        await reply(
            event,
            t("mt_edit_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"mt")]],
        )

    @bot.on(events.CallbackQuery(data=b"mtr"))
    @auth("modify")
    async def cb_mt_reset(event):
        uid = event.sender_id
        await reply(
            event,
            t("mt_reset_confirm", uid),
            buttons=[
                [
                    Button.inline(t("btn_yes_reset", uid), b"mtrc"),
                    Button.inline(t("btn_cancel", uid), b"mt"),
                ],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"mtrc"))
    @auth("modify")
    async def cb_mt_reset_confirm(event):
        uid = event.sender_id
        s = st(uid)
        iid = s.get("sr_iid")
        email = s.get("sr_email")
        pid = s.get("sr_pid")
        if not iid or not email or not pid:
            return
        p = get_panel(pid)
        try:
            await p.reset_client_traffic(iid, email)
            await event.answer(t("mt_reset_success", uid))
        except RuntimeError as e:
            await event.answer(f"Error: {e}", alert=True)
        await show_search_result(event, uid, email, panel_name=pid)

    @bot.on(events.CallbackQuery(data=b"mta"))
    @auth("modify")
    async def cb_mt_add(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "mt_add"
        await reply(
            event,
            t("mt_add_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"mt")]],
        )

    @bot.on(events.CallbackQuery(data=b"mts"))
    @auth("modify")
    async def cb_mt_sub(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "mt_sub"
        await reply(
            event,
            t("mt_sub_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"mt")]],
        )

    # ── Modify Days ──────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"md"))
    @auth("modify")
    async def cb_modify_days(event):
        uid = event.sender_id
        s = st(uid)
        client = s.get("sr_client")
        if not client:
            return
        expiry = client.get("expiryTime", 0)
        lines = [
            t("modify_days_title", uid),
            "",
            t("modify_days_current", uid, duration=format_expiry(expiry, uid)),
        ]
        await reply(
            event,
            "\n".join(lines),
            buttons=[
                [
                    Button.inline(t("btn_edit_total", uid), b"mde"),
                ],
                [
                    Button.inline(t("btn_add_more", uid), b"mda"),
                    Button.inline(t("btn_less", uid), b"mds"),
                ],
                [Button.inline(t("btn_back", uid), b"sr")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"mde"))
    @auth("modify")
    async def cb_md_edit(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "md_edit"
        await reply(
            event,
            t("md_edit_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"md")]],
        )

    @bot.on(events.CallbackQuery(data=b"mda"))
    @auth("modify")
    async def cb_md_add(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "md_add"
        await reply(
            event,
            t("md_add_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"md")]],
        )

    @bot.on(events.CallbackQuery(data=b"mds"))
    @auth("modify")
    async def cb_md_sub(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "md_sub"
        await reply(
            event,
            t("md_sub_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"md")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^mdsa:([yn])$"))
    @auth("modify")
    async def cb_md_sau(event):
        uid = event.sender_id
        s = st(uid)
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
            await event.answer(t("md_edit_success", uid))
        except RuntimeError as e:
            await event.answer(f"Error: {e}", alert=True)
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])
