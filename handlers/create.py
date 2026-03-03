import json
import time

from telethon import events, Button

from config import get_panel, st, clear, server_addrs, sub_urls, bot, user_inbounds
from helpers import (
    format_bytes, format_expiry, rand_email, generate_bulk_emails,
    make_qr, auth, reply, build_client_dict,
)
from i18n import t
from panel import build_client_link
from pdf_export import generate_account_pdf


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
            buttons=[[Button.inline(t("btn_back", uid), f"il:{panel_name}".encode())]],
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
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode())]],
        )
        return

    addr = server_addrs[panel_name]
    sub_url = sub_urls[panel_name]
    proxy_link = build_client_link(client_dict, inbound, addr)
    sub_id = client_dict.get("subId", "")
    sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None
    unlim = t("unlimited", uid)
    traffic_str = format_bytes(total_bytes) if total_bytes > 0 else unlim
    duration_str = format_expiry(expiry_time, uid)

    lines = [
        t("create_success", uid),
        "",
        t("create_email_line", uid, email=email),
        t("create_traffic_line", uid, traffic=traffic_str),
        t("create_duration_line", uid, duration=duration_str),
        t("create_inbound_line", uid, remark=inbound.get("remark", "?")),
        t("create_panel_line", uid, panel=panel_name),
    ]
    if proxy_link:
        lines += ["", f"`{proxy_link}`"]
    text = "\n".join(lines)
    btns = [[Button.inline(t("btn_back", uid), b"m")]]
    clear(uid)

    if proxy_link:
        qr = make_qr(proxy_link)
        await reply(event, text, buttons=btns, file=qr)
        # Also send PDF
        pdf_qr = make_qr(proxy_link)
        pdf = generate_account_pdf(
            [{
                "email": email,
                "proxy_link": proxy_link,
                "qr_image": pdf_qr,
                "traffic": traffic_str,
                "duration": duration_str,
                "sub_link": sub_link,
                "panel": panel_name,
            }],
            title=t("pdf_account_title", uid, email=email),
            uid=uid,
        )
        await bot.send_file(event.chat_id, pdf, caption=t("account_pdf", uid))
    else:
        await reply(event, text, buttons=btns)


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
            buttons=[[Button.inline(t("btn_back", uid), f"il:{panel_name}".encode())]],
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

    remark = inbound.get('remark', '?')

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
    await bot.send_message(
        event.chat_id, text, buttons=[[Button.inline(t("btn_back", uid), b"m")]],
        parse_mode="md",
    )

    # Generate and send PDF for created accounts
    if created:
        pdf = generate_account_pdf(
            created,
            f"Bulk Accounts - {panel_name} / {remark}",
            uid=uid,
        )
        await bot.send_file(event.chat_id, pdf, caption=t("bulk_pdf", uid))

    clear(uid)


async def handle_create_input(event):
    """Handle cr_email, cr_traffic, cr_duration text input. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "cr_email":
        s["state"] = None
        email = event.text.strip()
        s["cr"]["email"] = email
        s["state"] = "cr_traffic"
        await event.respond(
            t("enter_traffic_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())]],
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
            buttons=[[Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())]],
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
                    [Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())],
                ],
            )
        else:
            s["cr"]["start_after_use"] = False
            await _create_client(event, uid)
        return True

    return False


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
                [Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())],
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
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
            )
        else:
            s["bk"]["emails"] = generate_bulk_emails(method, s["bk"]["count"], prefix=prefix)
            s["state"] = "bk_traffic"
            sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
            ellipsis = "..." if s["bk"]["count"] > 3 else ""
            await event.respond(
                t("bulk_preview", uid, sample=sample, ellipsis=ellipsis),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
        s["state"] = "bk_traffic"
        sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
        ellipsis = "..." if s["bk"]["count"] > 3 else ""
        await event.respond(
            t("bulk_preview", uid, sample=sample, ellipsis=ellipsis),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
        s["state"] = "bk_traffic"
        await event.respond(
            t("bulk_emails_received", uid, count=len(emails)),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
                    [Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())],
                ],
            )
        else:
            s["bk"]["start_after_use"] = False
            await _bulk_create_clients(event, uid)
        return True

    return False


def register(bot):
    @bot.on(events.CallbackQuery(pattern=rb"^ca:(.+):(\d+)$"))
    @auth("create")
    async def cb_create_start(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        s = st(uid)
        s["state"] = "cr_email"
        s["cr_iid"] = iid
        s["cr_pid"] = panel_name
        s["cr"] = {}
        await reply(
            event,
            t("create_email_prompt", uid),
            buttons=[
                [Button.inline(t("btn_random_email", uid), b"re")],
                [Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"re"))
    @auth("create")
    async def cb_random_email(event):
        uid = event.sender_id
        s = st(uid)
        email = rand_email()
        s["cr"]["email"] = email
        s["state"] = "cr_traffic"
        await reply(
            event,
            t("create_email_line", uid, email=email) + "\n\n" + t("enter_traffic_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^sau:([yn])$"))
    @auth("create")
    async def cb_start_after_use(event):
        uid = event.sender_id
        s = st(uid)
        choice = event.pattern_match.group(1)
        s["cr"]["start_after_use"] = choice == b"y"
        s["state"] = None
        await _create_client(event, uid)

    # ── Bulk Create ──────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^bk:(.+):(\d+)$"))
    @auth("create")
    async def cb_bulk_start(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        s = st(uid)
        s["bk_iid"] = iid
        s["bk_pid"] = panel_name
        s["bk"] = {}
        s["state"] = None
        await reply(
            event,
            t("bulk_create_title", uid),
            buttons=[
                [
                    Button.inline(t("btn_by_count", uid), b"bkm:c"),
                    Button.inline(t("btn_by_email_list", uid), b"bkm:e"),
                ],
                [Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"bkm:c"))
    @auth("create")
    async def cb_bulk_by_count(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "bk_count"
        await reply(
            event,
            t("bulk_count_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
            buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
            s["state"] = "bk_traffic"
            sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
            ellipsis = "..." if s["bk"]["count"] > 3 else ""
            await reply(
                event,
                t("bulk_preview", uid, sample=sample, ellipsis=ellipsis),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
            )
        else:
            # All other methods need a prefix first
            s["state"] = "bk_prefix"
            await reply(
                event,
                t("enter_prefix_prompt", uid),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
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
