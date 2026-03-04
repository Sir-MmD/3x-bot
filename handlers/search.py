import asyncio
import json
import time

from telethon import events, Button

from config import panels, server_addrs, sub_urls, get_panel, st, clear, bot, visible_panels, user_inbounds, has_perm
from db import log_activity
from helpers import format_bytes, format_expiry, make_qr, auth, reply, answer, search_result_buttons
from i18n import t
from panel import build_client_link
from pdf_export import generate_account_pdf


async def show_search_result(event, uid: int, email: str, panel_name: str | None = None):
    s = st(uid)

    if panel_name:
        # Search specific panel — verify access
        vp = visible_panels(uid)
        if panel_name not in vp:
            await reply(
                event,
                t("not_found", uid),
                buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            )
            return
        p = get_panel(panel_name)
        client, inbound, traffic = await p.find_client_by_email(email)
        found_panel = panel_name
        if client is None:
            await reply(
                event,
                t("not_found", uid),
                buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            )
            return
        # Check inbound access
        allowed_ib = user_inbounds(uid, panel_name)
        if allowed_ib is not None and inbound["id"] not in allowed_ib:
            await reply(
                event,
                t("not_found", uid),
                buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            )
            return
    else:
        # Search all panels
        async def _search_one(pname, pc):
            c, ib, tr = await pc.find_client_by_email(email)
            return pname, c, ib, tr
        vp = visible_panels(uid)
        results = await asyncio.gather(
            *(_search_one(pn, pc) for pn, pc in vp.items()),
            return_exceptions=True,
        )
        matches = [
            (pn, c, ib, tr)
            for r in results
            if not isinstance(r, BaseException)
            for pn, c, ib, tr in [r]
            if c is not None
        ]
        # Filter out results on restricted inbounds
        matches = [
            (pn, c, ib, tr) for pn, c, ib, tr in matches
            if user_inbounds(uid, pn) is None or ib["id"] in user_inbounds(uid, pn)
        ]

        if not matches:
            await reply(
                event,
                t("not_found", uid),
                buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            )
            return

        if len(matches) > 1:
            # Found on multiple panels — let user choose
            s["sr_matches"] = {pn: (c, ib, tr) for pn, c, ib, tr in matches}
            s["sr_email"] = email
            btns = [[Button.inline(f"\U0001f5a5 {pn}", f"srp:{pn}".encode())] for pn, *_ in matches]
            btns.append([Button.inline(t("btn_back", uid), b"m")])
            await reply(
                event,
                t("found_multi", uid, email=email, count=len(matches)),
                buttons=btns,
            )
            return

        found_panel, client, inbound, traffic = matches[0]

    p = get_panel(found_panel)
    protocol = inbound["protocol"]
    client_id = p.get_client_id(client, protocol)
    actual_email = client["email"]

    s["sr_email"] = actual_email
    s["sr_iid"] = inbound["id"]
    s["sr_cid"] = client_id
    s["sr_client"] = client
    s["sr_protocol"] = protocol
    s["sr_traffic"] = traffic
    s["sr_pid"] = found_panel

    up = (traffic or {}).get("up", 0)
    down = (traffic or {}).get("down", 0)
    total = client.get("totalGB", 0)
    all_time = (traffic or {}).get("allTime", 0)
    remaining = max(0, total - up - down) if total > 0 else 0

    # 3-state status: active / depleted / disabled
    enabled = client.get("enable", True)
    if not enabled:
        status = "disabled"
    else:
        now_ms = int(time.time() * 1000)
        exp = client.get("expiryTime", 0)
        expired = exp > 0 and exp < now_ms
        traffic_exceeded = total > 0 and (up + down) >= total
        status = "depleted" if (expired or traffic_exceeded) else "active"

    status_key = {"active": "sr_status_active", "depleted": "sr_status_depleted", "disabled": "sr_status_disabled"}[status]

    # Check if user only has search_simple (not full search)
    simple_only = has_perm(uid, "search_simple") and not has_perm(uid, "search")

    if simple_only:
        # Simplified result: email, status, remaining traffic, remaining days
        unlim = t("unlimited", uid)
        remaining_gb = f"{remaining / (1024**3):.2f} GB" if total > 0 else unlim
        exp = client.get("expiryTime", 0)
        if exp == 0:
            remaining_days = unlim
        else:
            now_ms = int(time.time() * 1000)
            if exp < 0:
                dur_ms = abs(exp)
            else:
                dur_ms = exp - now_ms
            if dur_ms <= 0:
                remaining_days = t("expired", uid)
            else:
                remaining_days = f"{dur_ms // 86_400_000}d"
        lines = [
            t("sr_email", uid, email=actual_email),
            t(status_key, uid),
            t("sr_simple_traffic", uid, remaining=remaining_gb),
            t("sr_simple_duration", uid, remaining=remaining_days),
        ]
        btns = [[Button.inline(t("btn_back", uid), b"m")]]
        await reply(event, "\n".join(lines), buttons=btns)
        log_activity(uid, "search", json.dumps({"email": actual_email, "panel": found_panel}))
        return

    online_list = await p.get_online_clients()
    online = actual_email in online_list

    addr = server_addrs[found_panel]
    sub_url = sub_urls[found_panel]
    proxy_link = build_client_link(client, inbound, addr)
    sub_id = client.get("subId", "")
    sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None

    unlim = t("unlimited", uid)
    lines = [
        t("sr_panel", uid, panel=found_panel),
        t("sr_email", uid, email=actual_email),
        t(status_key, uid),
        t("sr_online_yes", uid) if online else t("sr_online_no", uid),
        "",
        t("sr_traffic", uid, up=format_bytes(up), down=format_bytes(down)),
        t("sr_limit", uid, limit=format_bytes(total) if total > 0 else unlim),
        t("sr_remaining", uid, remaining=format_bytes(remaining) if total > 0 else unlim),
        "",
        t("sr_duration", uid, duration=format_expiry(client.get("expiryTime", 0), uid)),
        t("sr_inbound", uid, remark=inbound.get("remark", "?")),
    ]
    if sub_link:
        lines.append(t("sr_subscription", uid, link=sub_link))
    lines += ["", t("sr_alltime", uid, total=format_bytes(all_time))]
    if proxy_link:
        lines += ["", f"`{proxy_link}`"]
    text = "\n".join(lines)

    btns = search_result_buttons(uid, status)

    if proxy_link:
        qr = make_qr(proxy_link)
        await reply(event, text, buttons=btns, file=qr)
    else:
        await reply(event, text, buttons=btns)

    log_activity(uid, "search", json.dumps({"email": actual_email, "panel": found_panel}))


def register(bot):
    @bot.on(events.CallbackQuery(data=b"s"))
    @auth("search", "search_simple")
    async def cb_search(event):
        uid = event.sender_id
        await reply(
            event,
            t("search_prompt", uid),
            buttons=[[Button.inline(t("btn_back", uid), b"m")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^srp:(.+)$"))
    @auth("search", "search_simple")
    async def cb_search_panel_select(event):
        panel_name = event.pattern_match.group(1).decode()
        s = st(event.sender_id)
        matches = s.get("sr_matches", {})
        match = matches.get(panel_name)
        if not match:
            return
        email = s["sr_email"]
        client, inbound, traffic = match
        s.pop("sr_matches", None)
        await show_search_result(event, event.sender_id, email, panel_name=panel_name)

    @bot.on(events.CallbackQuery(data=b"dis"))
    @auth("toggle")
    async def cb_disable(event):
        s = st(event.sender_id)
        client = s.get("sr_client")
        if not client:
            return
        client["enable"] = False
        p = get_panel(s["sr_pid"])
        await p.update_client(s["sr_cid"], s["sr_iid"], client)
        log_activity(event.sender_id, "disable", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"]}))
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])

    @bot.on(events.CallbackQuery(data=b"en"))
    @auth("toggle")
    async def cb_enable(event):
        s = st(event.sender_id)
        client = s.get("sr_client")
        if not client:
            return
        client["enable"] = True
        p = get_panel(s["sr_pid"])
        await p.update_client(s["sr_cid"], s["sr_iid"], client)
        log_activity(event.sender_id, "enable", json.dumps({"email": s["sr_email"], "panel": s["sr_pid"]}))
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])

    @bot.on(events.CallbackQuery(data=b"rm"))
    @auth("remove")
    async def cb_remove(event):
        uid = event.sender_id
        await reply(
            event,
            t("confirm_remove", uid),
            buttons=[
                [
                    Button.inline(t("btn_yes_remove", uid), b"crm"),
                    Button.inline(t("btn_cancel", uid), b"sr"),
                ],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"crm"))
    @auth("remove")
    async def cb_confirm_remove(event):
        uid = event.sender_id
        s = st(uid)
        cid = s.get("sr_cid")
        iid = s.get("sr_iid")
        pid = s.get("sr_pid")
        if not cid or not iid or not pid:
            return
        p = get_panel(pid)
        try:
            await p.delete_client(iid, cid)
            text = t("remove_success", uid)
            log_activity(uid, "remove", json.dumps({"email": s.get("sr_email"), "panel": pid}))
        except RuntimeError as e:
            text = t("error_msg", uid, error=e)
        clear(uid)
        await reply(event, text, buttons=[[Button.inline(t("btn_back", uid), b"m")]])

    @bot.on(events.CallbackQuery(data=b"sr"))
    @auth("search", "search_simple")
    async def cb_back_to_search(event):
        s = st(event.sender_id)
        s["state"] = None
        email = s.get("sr_email")
        if not email:
            return
        await show_search_result(event, event.sender_id, email, panel_name=s.get("sr_pid"))

    @bot.on(events.CallbackQuery(data=b"pdf"))
    @auth("pdf")
    async def cb_export_pdf(event):
        uid = event.sender_id
        s = st(uid)
        client = s.get("sr_client")
        iid = s.get("sr_iid")
        pid = s.get("sr_pid")
        if not client or not iid or not pid:
            return
        p = get_panel(pid)
        inbounds = await p.list_inbounds()
        inbound = next((ib for ib in inbounds if ib["id"] == iid), None)
        if not inbound:
            return

        addr = server_addrs[pid]
        sub_url = sub_urls[pid]
        proxy_link = build_client_link(client, inbound, addr)
        sub_id = client.get("subId", "")
        sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None

        total = client.get("totalGB", 0)
        unlim = t("unlimited", uid)
        traffic_str = format_bytes(total) if total > 0 else unlim
        duration_str = format_expiry(client.get("expiryTime", 0), uid)

        qr_img = make_qr(proxy_link) if proxy_link else None
        email = client["email"]
        pdf = generate_account_pdf(
            [
                {
                    "email": email,
                    "proxy_link": proxy_link or "",
                    "qr_image": qr_img,
                    "traffic": traffic_str,
                    "duration": duration_str,
                    "sub_link": sub_link,
                    "panel": pid,
                }
            ],
            title=t("pdf_account_title", uid, email=email),
            uid=uid,
        )
        await answer(event,t("generating_pdf", uid))
        await bot.send_file(event.chat_id, pdf, caption=t("account_pdf", uid))
        log_activity(uid, "pdf_export", json.dumps({"email": email, "panel": pid}))
