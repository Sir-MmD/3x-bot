import json
import time

from telethon import events, Button

from config import get_panel, st, clear, user_inbounds, visible_panels
from db import get_setting, get_plans, get_test_account, log_activity
from helpers import (
    rand_email, generate_bulk_emails,
    auth, reply, answer, build_client_dict,
)
from i18n import t
from panel import SUPPORTED_PROTOCOLS


def _recreate_label(uid: int, days: int, traffic_gb: float, sau: bool, count: int = 0) -> str:
    """Build the re-create button label."""
    unlim = t("unlimited", uid)
    d_part = f"{days}d" if days > 0 else unlim
    t_part = f"{traffic_gb:.0f}GB" if traffic_gb > 0 else unlim
    sau_part = "\U0001f552" if sau else ""
    prefix = f"{count}x-" if count > 0 else ""
    return f"\U0001f501 Re-Create ({prefix}{d_part}-{t_part}{sau_part})"


async def _check_protocol(event, uid, panel_name, iid):
    """Return True if the inbound protocol is supported, else alert."""
    p = get_panel(panel_name)
    inbounds = await p.list_inbounds()
    inbound = next((ib for ib in inbounds if ib["id"] == iid), None)
    if inbound and inbound["protocol"] not in SUPPORTED_PROTOCOLS:
        await answer(event, t("unsupported_protocol_short", uid, protocol=inbound["protocol"]), alert=True)
        return False
    return True


def _plan_picker_btns(uid, panel_name, iid, prefix):
    """Build plan picker buttons. prefix is 'crp' or 'bkp'."""
    from handlers.plans import format_plan_label
    plans = get_plans()
    btns = []
    for plan in plans:
        btns.append([Button.inline(
            format_plan_label(plan, uid),
            f"{prefix}:{panel_name}:{iid}:{plan['id']}".encode(),
        )])
    btns.append([Button.inline(
        t("btn_custom_plan", uid),
        f"{prefix}:{panel_name}:{iid}:c".encode(),
    )])
    btns.append([Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    return btns


async def _create_client(event, uid: int):
    s = st(uid)
    cr = s["cr"]
    iid = s["cr_iid"]
    panel_name = s["cr_pid"]
    p = get_panel(panel_name)

    inbounds = await p.list_inbounds()
    inbound = next((ib for ib in inbounds if ib["id"] == iid), None)
    if not inbound:
        await reply(
            event, t("inbound_not_found", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"il:{panel_name}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )
        return

    protocol = inbound["protocol"]
    stream = json.loads(inbound.get("streamSettings", "{}"))
    settings = json.loads(inbound.get("settings", "{}"))

    email = cr["email"]
    traffic_gb = cr["traffic_gb"]
    duration_days = cr.get("duration_days", 0)
    start_after_use = cr.get("start_after_use", False)

    total_bytes = int(traffic_gb * 1024**3) if traffic_gb > 0 else 0

    if duration_days > 0:
        dur_ms = duration_days * 86_400_000
        if start_after_use:
            expiry_time = -dur_ms
        else:
            expiry_time = int(time.time() * 1000) + dur_ms
    else:
        expiry_time = 0

    client_dict = build_client_dict(email, total_bytes, expiry_time, protocol, stream, settings)

    try:
        await p.add_client(iid, client_dict)
    except RuntimeError as e:
        await reply(
            event,
            t("create_error", uid, error=e),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )
        return

    log_activity(uid, "create", json.dumps({"email": email, "panel": panel_name, "inbound": iid}))

    # Save re-create params
    s["rcr"] = {
        "pid": panel_name, "iid": iid,
        "traffic_gb": traffic_gb, "duration_days": duration_days,
        "start_after_use": start_after_use,
    }

    from handlers.search import show_search_result
    await show_search_result(event, uid, email, panel_name=panel_name,
                             back_data=f"ib:{panel_name}:{iid}".encode())


async def handle_create_input(event):
    """Handle cr_email, cr_traffic, cr_duration text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "cr_email":
        s["state"] = None
        email = event.text.strip()
        # Check for duplicate email across all panels
        dup_panel = None
        try:
            for pname, pc in visible_panels(uid).items():
                c, _ib, _tr = await pc.find_client_by_email(email)
                if c is not None:
                    dup_panel = pname
                    break
        except Exception:
            pass
        is_recreate = "traffic_gb" in s.get("cr", {})
        back_data = (b"sr" if is_recreate
                     else f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())
        if dup_panel:
            s["state"] = "cr_email"
            btns = [[Button.inline(t("btn_back", uid), back_data),
                      Button.inline(t("btn_main_menu", uid), b"m")]]
            if is_recreate:
                btns.insert(0, [Button.inline(t("btn_random_email", uid), b"rcr:re")])
            await event.respond(
                t("create_email_duplicate", uid, email=email, panel=dup_panel)
                + "\n\n" + t("create_email_prompt", uid),
                buttons=btns,
                parse_mode="md",
            )
            return True
        s["cr"]["email"] = email
        if is_recreate:
            # Re-create: skip traffic/duration, go straight to creation
            s["state"] = None
            await _create_client(event, uid)
            return True
        s["state"] = "cr_traffic"
        await event.respond(
            t("enter_traffic_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), back_data),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
            parse_mode="md",
        )
        return True

    if state == "cr_traffic":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "cr_traffic"
            await event.respond(t("enter_traffic_invalid", uid))
            return True
        s["cr"]["traffic_gb"] = gb
        s["state"] = "cr_duration"
        await event.respond(
            t("enter_duration_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )
        return True

    if state == "cr_duration":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "cr_duration"
            await event.respond(t("enter_duration_invalid", uid))
            return True
        s["cr"]["duration_days"] = days
        if days > 0:
            s["state"] = "cr_sau"
            await event.respond(
                t("start_after_use_prompt", uid),
                buttons=[
                    [Button.inline(t("btn_yes", uid), b"sau:y"), Button.inline(t("btn_no", uid), b"sau:n")],
                    [Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode()),
                     Button.inline(t("btn_main_menu", uid), b"m")],
                ],
            )
        else:
            s["cr"]["start_after_use"] = False
            await _create_client(event, uid)
        return True

    return False


def register(bot):
    from . import bulk_create
    bulk_create.register(bot)

    @bot.on(events.CallbackQuery(pattern=rb"^ca:(.+):(\d+)$"))
    @auth("create")
    async def cb_create_start(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        if not await _check_protocol(event, uid, panel_name, iid):
            return
        s = st(uid)
        s["cr_iid"] = iid
        s["cr_pid"] = panel_name
        s["cr"] = {}
        if get_plans():
            s["state"] = None
            await reply(event, t("plan_picker_title", uid),
                        buttons=_plan_picker_btns(uid, panel_name, iid, "crp"))
        else:
            s["state"] = "cr_email"
            await reply(
                event,
                t("create_email_prompt", uid),
                buttons=[
                    [Button.inline(t("btn_random_email", uid), b"re")],
                    [Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode()),
                     Button.inline(t("btn_main_menu", uid), b"m")],
                ],
            )

    @bot.on(events.CallbackQuery(pattern=rb"^crp:(.+):(\d+):(\d+)$"))
    @auth("create")
    async def cb_create_with_plan(event):
        """Plan selected for single create — set plan values, ask email."""
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        plan_id = int(event.pattern_match.group(3))
        from db import get_plan
        plan = get_plan(plan_id)
        if not plan:
            return
        s = st(uid)
        s["cr_iid"] = iid
        s["cr_pid"] = panel_name
        s["cr"] = {
            "traffic_gb": plan.get("traffic", 0),
            "duration_days": plan.get("days", 0),
            "start_after_use": plan.get("sau", False),
        }
        s["state"] = "cr_email"
        await reply(
            event,
            t("create_email_prompt", uid),
            buttons=[
                [Button.inline(t("btn_random_email", uid), b"re")],
                [Button.inline(t("btn_back", uid), f"ca:{panel_name}:{iid}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^crp:(.+):(\d+):c$"))
    @auth("create")
    async def cb_create_custom(event):
        """Custom (no plan) for single create — original flow."""
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        s = st(uid)
        s["cr_iid"] = iid
        s["cr_pid"] = panel_name
        s["cr"] = {}
        s["state"] = "cr_email"
        await reply(
            event,
            t("create_email_prompt", uid),
            buttons=[
                [Button.inline(t("btn_random_email", uid), b"re")],
                [Button.inline(t("btn_back", uid), f"ca:{panel_name}:{iid}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"re"))
    @auth("create")
    async def cb_random_email(event):
        uid = event.sender_id
        s = st(uid)
        email = rand_email()
        s["cr"]["email"] = email
        if "traffic_gb" in s["cr"]:
            s["state"] = None
            await _create_client(event, uid)
            return
        s["state"] = "cr_traffic"
        await reply(
            event,
            t("create_email_line", uid, email=email) + "\n\n" + t("enter_traffic_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^ta:(.+):(\d+)$"))
    @auth("create")
    async def cb_test_account(event):
        """Create a test account with preset settings from test_account setting."""
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        if not await _check_protocol(event, uid, panel_name, iid):
            return
        ta = get_test_account()
        if not ta:
            return
        method = ta.get("method", "r")
        prefix = ta.get("prefix", "")
        postfix = ta.get("postfix", "")
        email = generate_bulk_emails(method, 1, prefix=prefix, postfix=postfix)[0]
        s = st(uid)
        s["cr_iid"] = iid
        s["cr_pid"] = panel_name
        s["cr"] = {
            "email": email,
            "traffic_gb": ta.get("traffic", 0),
            "duration_days": ta.get("days", 0),
            "start_after_use": ta.get("sau", False),
        }
        s["state"] = None
        await _create_client(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^sau:([yn])$"))
    @auth("create")
    async def cb_start_after_use(event):
        uid = event.sender_id
        s = st(uid)
        choice = event.pattern_match.group(1)
        s["cr"]["start_after_use"] = choice == b"y"
        s["state"] = None
        await _create_client(event, uid)

    # ── Re-Create Single ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"rcr"))
    @auth("create")
    async def cb_recreate_single(event):
        """Re-create with same params, ask for new email."""
        uid = event.sender_id
        s = st(uid)
        rcr = s.get("rcr")
        if not rcr:
            return
        panel_name = rcr["pid"]
        iid = rcr["iid"]
        if not await _check_protocol(event, uid, panel_name, iid):
            return
        s["cr_iid"] = iid
        s["cr_pid"] = panel_name
        s["cr"] = {
            "traffic_gb": rcr["traffic_gb"],
            "duration_days": rcr["duration_days"],
            "start_after_use": rcr["start_after_use"],
        }
        s["state"] = "cr_email"
        await reply(
            event,
            t("create_email_prompt", uid),
            buttons=[
                [Button.inline(t("btn_random_email", uid), b"rcr:re")],
                [Button.inline(t("btn_cancel", uid), b"sr"),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"rcr:re"))
    @auth("create")
    async def cb_recreate_random_email(event):
        """Random email for re-create, skip to creation."""
        uid = event.sender_id
        s = st(uid)
        cr = s.get("cr")
        if not cr or s.get("state") != "cr_email":
            return
        email = rand_email()
        cr["email"] = email
        s["state"] = None
        await _create_client(event, uid)
