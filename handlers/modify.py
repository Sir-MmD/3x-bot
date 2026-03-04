import json
import time

from telethon import events, Button

from config import get_panel, st
from db import log_activity
from helpers import format_bytes, format_expiry, auth, reply, answer
from i18n import t
from handlers.search import show_search_result


def _renew_info(uid, s):
    """Build the info header for renew plan steps."""
    email = s.get("sr_email", "?")
    client = s.get("sr_client") or {}
    traffic = s.get("sr_traffic") or {}
    total = client.get("totalGB", 0)
    up = traffic.get("up", 0)
    down = traffic.get("down", 0)
    unlim = t("unlimited", uid)
    lines = [
        t("rn_title", uid),
        "",
        t("rn_info_email", uid, email=email),
        t("rn_info_traffic", uid,
          limit=format_bytes(total) if total > 0 else unlim,
          used=format_bytes(up + down)),
        t("rn_info_duration", uid,
          duration=format_expiry(client.get("expiryTime", 0), uid)),
    ]
    # Show selections made so far
    rn_gb = s.get("rn_gb")
    if rn_gb is not None:
        lines.append("")
        lines.append(t("rn_selected_traffic", uid, gb=rn_gb))
    rn_days = s.get("rn_days")
    if rn_days is not None:
        lines.append(t("rn_selected_days", uid, days=rn_days))
    return lines


async def handle_renew_input(event):
    """Handle rn_gb and rn_days text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "rn_gb":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "rn_gb"
            await event.respond(t("rn_gb_invalid", uid))
            return True
        if gb < 0:
            s["state"] = "rn_gb"
            await event.respond(t("rn_gb_invalid", uid))
            return True
        s["rn_gb"] = gb
        # Next step: ask days
        lines = _renew_info(uid, s)
        lines += ["", t("rn_days_prompt", uid)]
        await event.respond(
            "\n".join(lines),
            buttons=[[Button.inline(t("btn_back", uid), b"rn"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )
        s["state"] = "rn_days"
        return True

    if state == "rn_days":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "rn_days"
            await event.respond(t("rn_days_invalid", uid))
            return True
        if days < 0:
            s["state"] = "rn_days"
            await event.respond(t("rn_days_invalid", uid))
            return True
        s["rn_days"] = days
        if days > 0:
            # Ask start-after-use
            lines = _renew_info(uid, s)
            lines += ["", t("start_after_use_prompt", uid)]
            await event.respond(
                "\n".join(lines),
                buttons=[
                    [Button.inline(t("btn_yes", uid), b"rnsa:y"),
                     Button.inline(t("btn_no", uid), b"rnsa:n")],
                    [Button.inline(t("btn_back", uid), b"rn"),
                     Button.inline(t("btn_main_menu", uid), b"m")],
                ],
            )
        else:
            # Days = 0 (unlimited), skip SAU, apply directly
            s["rn_sau"] = False
            await _apply_renew(event, uid)
        return True

    return False


async def _apply_renew(event, uid):
    """Apply the renew plan (add traffic + set days)."""
    s = st(uid)
    client = s.get("sr_client")
    if not client:
        return
    gb = s.get("rn_gb", 0)
    days = s.get("rn_days", 0)
    sau = s.get("rn_sau", False)

    # Apply traffic
    if gb > 0:
        add_bytes = int(gb * 1024**3)
        cur = client.get("totalGB", 0)
        if cur > 0:
            client["totalGB"] = cur + add_bytes
        # If unlimited (0), adding doesn't change it
    elif gb == 0:
        pass  # No traffic change

    # Apply days
    if days > 0:
        add_ms = days * 86_400_000
        cur = client.get("expiryTime", 0)
        if sau:
            if cur < 0:
                client["expiryTime"] = cur - add_ms
            else:
                client["expiryTime"] = -add_ms
        else:
            if cur == 0:
                pass  # unlimited stays unlimited
            elif cur < 0:
                now_ms = int(time.time() * 1000)
                client["expiryTime"] = now_ms + abs(cur) + add_ms
            else:
                client["expiryTime"] = cur + add_ms
    elif days == 0:
        pass  # No duration change

    p = get_panel(s["sr_pid"])
    try:
        await p.update_client(s["sr_cid"], s["sr_iid"], client)
        log_activity(uid, "renew_plan", json.dumps({
            "email": s["sr_email"], "panel": s["sr_pid"],
            "gb": gb, "days": days, "sau": sau,
        }))
        await event.respond(t("rn_success", uid, gb=gb, days=days))
    except RuntimeError as e:
        await event.respond(t("error_msg", uid, error=e))
    await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])


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
            log_activity(uid, "modify_traffic_edit", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "gb": gb}))
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
            log_activity(uid, "modify_traffic_add", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "gb": gb}))
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
            log_activity(uid, "modify_traffic_sub", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "gb": gb}))
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
                log_activity(uid, "modify_days_edit", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "days": 0}))
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
                    [Button.inline(t("btn_back", uid), b"sr"),
                     Button.inline(t("btn_main_menu", uid), b"m")],
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
            log_activity(uid, "modify_days_add", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "days": days}))
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
            log_activity(uid, "modify_days_sub", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "days": days}))
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
                [Button.inline(t("btn_back", uid), b"sr"),
                 Button.inline(t("btn_main_menu", uid), b"m")],
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
            buttons=[[Button.inline(t("btn_back", uid), b"mt"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
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
            log_activity(uid, "modify_traffic_reset", json.dumps({"email": email, "panel": pid}))
            await answer(event,t("mt_reset_success", uid))
        except RuntimeError as e:
            await answer(event,f"Error: {e}", alert=True)
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
            buttons=[[Button.inline(t("btn_back", uid), b"mt"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
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
            buttons=[[Button.inline(t("btn_back", uid), b"mt"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
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
                [Button.inline(t("btn_back", uid), b"sr"),
                 Button.inline(t("btn_main_menu", uid), b"m")],
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
            buttons=[[Button.inline(t("btn_back", uid), b"md"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
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
            buttons=[[Button.inline(t("btn_back", uid), b"md"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
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
            buttons=[[Button.inline(t("btn_back", uid), b"md"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
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
            log_activity(uid, "modify_days_edit", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"], "days": days}))
            await answer(event,t("md_edit_success", uid))
        except RuntimeError as e:
            await answer(event,f"Error: {e}", alert=True)
        await show_search_result(event, uid, s["sr_email"], panel_name=s["sr_pid"])

    # ── Renew Plan ────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"rn"))
    @auth("modify")
    async def cb_renew(event):
        uid = event.sender_id
        s = st(uid)
        client = s.get("sr_client")
        if not client:
            return
        # Clear any previous renew state
        s.pop("rn_gb", None)
        s.pop("rn_days", None)
        s.pop("rn_sau", None)
        lines = _renew_info(uid, s)
        lines += ["", t("rn_gb_prompt", uid)]
        s["state"] = "rn_gb"
        await reply(
            event,
            "\n".join(lines),
            buttons=[[Button.inline(t("btn_back", uid), b"sr"),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^rnsa:([yn])$"))
    @auth("modify")
    async def cb_renew_sau(event):
        uid = event.sender_id
        s = st(uid)
        choice = event.pattern_match.group(1).decode()
        s["rn_sau"] = choice == "y"
        await _apply_renew(event, uid)
