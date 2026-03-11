import io
import json
import time
from datetime import datetime

from telethon import events, Button

from config import get_panel, st, clear, server_addrs, sub_urls, bot, user_inbounds
from db import get_setting, log_activity
from helpers import (
    format_bytes, format_expiry, generate_bulk_emails,
    make_qr, auth, reply, answer, build_client_dict,
)
from i18n import t
from panel import build_client_link
from pdf_export import generate_account_pdf
from .create import _recreate_label, _check_protocol, _plan_picker_btns


async def _bulk_create_clients(event, uid: int):
    s = st(uid)
    bk = s["bk"]
    iid = s["bk_iid"]
    panel_name = s["bk_pid"]
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

    emails = bk["emails"]
    traffic_gb = bk["traffic_gb"]
    duration_days = bk.get("duration_days", 0)
    start_after_use = bk.get("start_after_use", False)

    total_bytes = int(traffic_gb * 1024**3) if traffic_gb > 0 else 0

    if duration_days > 0:
        dur_ms = duration_days * 86_400_000
        if start_after_use:
            expiry_time = -dur_ms
        else:
            expiry_time = int(time.time() * 1000) + dur_ms
    else:
        expiry_time = 0

    unlim = t("unlimited", uid)
    traffic_str = format_bytes(total_bytes) if total_bytes > 0 else unlim
    duration_str = format_expiry(expiry_time, uid)

    addr = server_addrs[panel_name]
    sub_url = sub_urls[panel_name]

    # Progress message
    progress_msg = await bot.send_message(
        event.chat_id, t("bulk_creating", uid, count=len(emails))
    )

    created = []
    failed = []

    for i, email in enumerate(emails):
        client_dict = build_client_dict(email, total_bytes, expiry_time, protocol, stream, settings)
        try:
            await p.add_client(iid, client_dict)
            proxy_link = build_client_link(client_dict, inbound, addr)
            sub_id = client_dict.get("subId", "")
            sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None
            created.append({
                "email": email,
                "proxy_link": proxy_link,
                "qr_image": make_qr(proxy_link) if proxy_link else None,
                "traffic": traffic_str,
                "duration": duration_str,
                "sub_link": sub_link,
                "panel": panel_name,
            })
        except RuntimeError as e:
            failed.append((email, str(e)))

        # Update progress every 5 accounts
        if (i + 1) % 5 == 0:
            try:
                await progress_msg.edit(
                    t("bulk_creating_progress", uid, done=i + 1, total=len(emails))
                )
            except Exception:
                pass

    # Delete progress message
    try:
        await progress_msg.delete()
    except Exception:
        pass

    log_activity(uid, "bulk_create", json.dumps({"panel": panel_name, "inbound": iid, "created": len(created), "failed": len(failed)}))

    remark = inbound.get('remark', '?')

    # Save re-create params
    s["rcr"] = {
        "pid": panel_name, "iid": iid,
        "traffic_gb": traffic_gb, "duration_days": duration_days,
        "start_after_use": start_after_use,
        "bulk_count": len(emails),
        "bulk_method": bk.get("method", "r"),
        "bulk_prefix": bk.get("prefix", ""),
        "bulk_postfix": bk.get("postfix", ""),
    }

    # Summary
    lines = [
        t("bulk_complete", uid),
        "",
        t("bulk_created", uid, count=len(created)),
        t("bulk_failed", uid, count=len(failed)),
        "",
        t("create_traffic_line", uid, traffic=traffic_str),
        t("create_duration_line", uid, duration=duration_str),
        t("create_inbound_line", uid, remark=remark),
        t("create_panel_line", uid, panel=panel_name),
    ]
    if failed:
        lines += ["", t("bulk_errors_header", uid)]
        for email, err in failed[:5]:
            lines.append(f"  \u2022 `{email}`: {err}")
    text = "\n".join(lines)
    back_data = f"ib:{panel_name}:{iid}".encode()
    rcr_label = _recreate_label(uid, duration_days, traffic_gb, start_after_use, count=len(emails))
    btns = [
        [Button.inline(rcr_label, b"rcrb")],
        [Button.inline(t("btn_back", uid), back_data),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await bot.send_message(event.chat_id, text, buttons=btns, parse_mode="md")

    # Send TXT and PDF files with created account details
    if created:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        txt_lines = []
        for acc in created:
            block = [f"Account ID: {acc['email']}"]
            block.append(f"Panel: {acc['panel']}")
            block.append(f"Traffic: {acc['traffic']}")
            block.append(f"Duration: {acc['duration']}")
            if acc.get("proxy_link"):
                block.append(f"Link: {acc['proxy_link']}")
            if acc.get("sub_link"):
                block.append(f"Subscription: {acc['sub_link']}")
            block.append("")
            txt_lines.extend(block)
        txt_buf = io.BytesIO("\n".join(txt_lines).encode("utf-8"))
        txt_buf.name = f"bulk-{panel_name}-{remark}_{stamp}.txt"
        await bot.send_file(event.chat_id, txt_buf, caption=t("account_txt", uid))

        pdf_buf = generate_account_pdf(
            created,
            title=t("pdf_bulk_title", uid, count=len(created)),
            uid=uid,
        )
        await bot.send_file(event.chat_id, pdf_buf, caption=t("account_pdf", uid))


async def handle_bulk_create_input(event):
    """Handle bk_* text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "bk_count":
        s["state"] = None
        try:
            count = int(event.text.strip())
        except ValueError:
            s["state"] = "bk_count"
            await event.respond(t("bulk_count_invalid", uid))
            return True
        if count < 1 or count > 100:
            s["state"] = "bk_count"
            await event.respond(t("bulk_count_range", uid))
            return True
        s["bk"]["count"] = count
        await event.respond(
            t("bulk_naming_title", uid, count=count),
            buttons=[
                [
                    Button.inline(t("btn_random", uid), b"bkn:r"),
                    Button.inline(t("btn_rand_prefix", uid), b"bkn:rp"),
                    Button.inline(t("btn_prefix_rand", uid), b"bkn:pr"),
                ],
                [
                    Button.inline(t("btn_prefix_num_rand", uid), b"bkn:pnr"),
                    Button.inline(t("btn_prefix_num_rand_post", uid), b"bkn:pnrx"),
                ],
                [
                    Button.inline(t("btn_prefix_num", uid), b"bkn:pn"),
                    Button.inline(t("btn_prefix_num_post", uid), b"bkn:pnx"),
                ],
                [Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ],
        )
        return True

    if state == "bk_prefix":
        s["state"] = None
        prefix = event.text.strip()
        if not prefix:
            s["state"] = "bk_prefix"
            await event.respond(t("prefix_empty", uid))
            return True
        s["bk"]["prefix"] = prefix
        method = s["bk"]["method"]
        # Methods needing postfix go to bk_postfix, others generate now
        if method in ("pnrx", "pnx"):
            s["state"] = "bk_postfix"
            await event.respond(
                t("enter_postfix_prompt", uid, prefix=prefix),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
            )
        else:
            s["bk"]["emails"] = generate_bulk_emails(method, s["bk"]["count"], prefix=prefix)
            if "traffic_gb" in s["bk"]:
                s["state"] = None
                await _bulk_create_clients(event, uid)
            else:
                s["state"] = "bk_traffic"
                sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
                ellipsis = "..." if s["bk"]["count"] > 3 else ""
                await event.respond(
                    t("bulk_preview", uid, sample=sample, ellipsis=ellipsis),
                    buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                          Button.inline(t("btn_main_menu", uid), b"m")]],
                    parse_mode="md",
                )
        return True

    if state == "bk_postfix":
        s["state"] = None
        postfix = event.text.strip()
        if not postfix:
            s["state"] = "bk_postfix"
            await event.respond(t("postfix_empty", uid))
            return True
        s["bk"]["postfix"] = postfix
        method = s["bk"]["method"]
        prefix = s["bk"]["prefix"]
        s["bk"]["emails"] = generate_bulk_emails(method, s["bk"]["count"], prefix=prefix, postfix=postfix)
        if "traffic_gb" in s["bk"]:
            s["state"] = None
            await _bulk_create_clients(event, uid)
        else:
            s["state"] = "bk_traffic"
            sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
            ellipsis = "..." if s["bk"]["count"] > 3 else ""
            await event.respond(
                t("bulk_preview", uid, sample=sample, ellipsis=ellipsis),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                          Button.inline(t("btn_main_menu", uid), b"m")]],
                parse_mode="md",
            )
        return True

    if state == "bk_emails":
        s["state"] = None
        raw = event.text.strip()
        emails = [e.strip() for e in raw.splitlines() if e.strip()]
        if not emails:
            s["state"] = "bk_emails"
            await event.respond(t("bulk_emails_empty", uid))
            return True
        if len(emails) > 100:
            s["state"] = "bk_emails"
            await event.respond(t("bulk_emails_max", uid))
            return True
        s["bk"]["emails"] = emails
        if "traffic_gb" in s["bk"]:
            s["state"] = None
            await _bulk_create_clients(event, uid)
        else:
            s["state"] = "bk_traffic"
            await event.respond(
                t("bulk_emails_received", uid, count=len(emails)),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                          Button.inline(t("btn_main_menu", uid), b"m")]],
            )
        return True

    if state == "bk_traffic":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "bk_traffic"
            await event.respond(t("enter_traffic_invalid", uid))
            return True
        s["bk"]["traffic_gb"] = gb
        s["state"] = "bk_duration"
        await event.respond(
            t("enter_duration_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )
        return True

    if state == "bk_duration":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "bk_duration"
            await event.respond(t("enter_duration_invalid", uid))
            return True
        s["bk"]["duration_days"] = days
        if days > 0:
            s["state"] = "bk_sau"
            await event.respond(
                t("start_after_use_prompt", uid),
                buttons=[
                    [Button.inline(t("btn_yes", uid), b"bksa:y"), Button.inline(t("btn_no", uid), b"bksa:n")],
                    [Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                 Button.inline(t("btn_main_menu", uid), b"m")],
                ],
            )
        else:
            s["bk"]["start_after_use"] = False
            await _bulk_create_clients(event, uid)
        return True

    return False


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):

    def _show_bulk_method_picker(uid, panel_name, iid):
        """Return buttons for bulk create method picker."""
        return [
            [
                Button.inline(t("btn_by_count", uid), b"bkm:c"),
                Button.inline(t("btn_by_email_list", uid), b"bkm:e"),
            ],
            [Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode()),
             Button.inline(t("btn_main_menu", uid), b"m")],
        ]

    @bot.on(events.CallbackQuery(pattern=rb"^bk:(.+):(\d+)$"))
    @auth("create")
    async def cb_bulk_start(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        if not await _check_protocol(event, uid, panel_name, iid):
            return
        s = st(uid)
        s["bk_iid"] = iid
        s["bk_pid"] = panel_name
        s["bk"] = {}
        s["state"] = None
        from db import get_plans
        if get_plans():
            await reply(event, t("plan_picker_title", uid),
                        buttons=_plan_picker_btns(uid, panel_name, iid, "bkp"))
        else:
            await reply(event, t("bulk_create_title", uid),
                        buttons=_show_bulk_method_picker(uid, panel_name, iid))

    @bot.on(events.CallbackQuery(pattern=rb"^bkp:(.+):(\d+):(\d+)$"))
    @auth("create")
    async def cb_bulk_with_plan(event):
        """Plan selected for bulk create — set plan values, show method picker."""
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        plan_id = int(event.pattern_match.group(3))
        from db import get_plan
        plan = get_plan(plan_id)
        if not plan:
            return
        s = st(uid)
        s["bk_iid"] = iid
        s["bk_pid"] = panel_name
        s["bk"] = {
            "traffic_gb": plan.get("traffic", 0),
            "duration_days": plan.get("days", 0),
            "start_after_use": plan.get("sau", False),
        }
        s["state"] = None
        await reply(event, t("bulk_create_title", uid),
                    buttons=_show_bulk_method_picker(uid, panel_name, iid))

    @bot.on(events.CallbackQuery(pattern=rb"^bkp:(.+):(\d+):c$"))
    @auth("create")
    async def cb_bulk_custom(event):
        """Custom (no plan) for bulk create — original flow."""
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        s = st(uid)
        s["bk_iid"] = iid
        s["bk_pid"] = panel_name
        s["bk"] = {}
        s["state"] = None
        await reply(event, t("bulk_create_title", uid),
                    buttons=_show_bulk_method_picker(uid, panel_name, iid))

    @bot.on(events.CallbackQuery(data=b"bkm:c"))
    @auth("create")
    async def cb_bulk_by_count(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "bk_count"
        await reply(
            event,
            t("bulk_count_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )

    @bot.on(events.CallbackQuery(data=b"bkm:e"))
    @auth("create")
    async def cb_bulk_by_emails(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "bk_emails"
        await reply(
            event,
            t("bulk_emails_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^bkn:(.+)$"))
    @auth("create")
    async def cb_bulk_naming(event):
        uid = event.sender_id
        method = event.pattern_match.group(1).decode()
        s = st(uid)
        s["bk"]["method"] = method
        if method == "r":
            # Random — generate immediately
            s["bk"]["emails"] = generate_bulk_emails("r", s["bk"]["count"])
            if "traffic_gb" in s["bk"]:
                s["state"] = None
                await _bulk_create_clients(event, uid)
                return
            s["state"] = "bk_traffic"
            sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
            ellipsis = "..." if s["bk"]["count"] > 3 else ""
            await reply(
                event,
                t("bulk_preview", uid, sample=sample, ellipsis=ellipsis),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
            )
        else:
            # All other methods need a prefix first
            s["state"] = "bk_prefix"
            await reply(
                event,
                t("enter_prefix_prompt", uid),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode()),
                      Button.inline(t("btn_main_menu", uid), b"m")]],
            )

    @bot.on(events.CallbackQuery(pattern=rb"^bksa:([yn])$"))
    @auth("create")
    async def cb_bulk_sau(event):
        uid = event.sender_id
        s = st(uid)
        choice = event.pattern_match.group(1)
        s["bk"]["start_after_use"] = choice == b"y"
        s["state"] = None
        await _bulk_create_clients(event, uid)

    # ── Re-Create Bulk ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"rcrb"))
    @auth("create")
    async def cb_recreate_bulk(event):
        """Re-create bulk with same params + same naming method."""
        uid = event.sender_id
        s = st(uid)
        rcr = s.get("rcr")
        if not rcr or "bulk_count" not in rcr:
            return
        panel_name = rcr["pid"]
        iid = rcr["iid"]
        if not await _check_protocol(event, uid, panel_name, iid):
            return
        method = rcr.get("bulk_method", "r")
        count = rcr["bulk_count"]
        prefix = rcr.get("bulk_prefix", "")
        postfix = rcr.get("bulk_postfix", "")
        emails = generate_bulk_emails(method, count, prefix=prefix, postfix=postfix)
        s["bk_iid"] = iid
        s["bk_pid"] = panel_name
        s["bk"] = {
            "emails": emails,
            "traffic_gb": rcr["traffic_gb"],
            "duration_days": rcr["duration_days"],
            "start_after_use": rcr["start_after_use"],
            "method": method,
            "prefix": prefix,
            "postfix": postfix,
        }
        s["state"] = None
        await _bulk_create_clients(event, uid)
