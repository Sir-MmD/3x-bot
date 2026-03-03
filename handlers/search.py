import asyncio

from telethon import events, Button

from config import panels, server_addrs, sub_urls, get_panel, st, clear, bot
from helpers import format_bytes, format_expiry, make_qr, auth, reply
from panel import build_client_link
from pdf_export import generate_account_pdf


async def show_search_result(event, uid: int, email: str, panel_name: str | None = None):
    s = st(uid)

    if panel_name:
        # Search specific panel
        p = get_panel(panel_name)
        client, inbound, traffic = await p.find_client_by_email(email)
        found_panel = panel_name
        if client is None:
            await reply(
                event,
                "❌ User not found!",
                buttons=[[Button.inline("◀️ Back", b"m")]],
            )
            return
    else:
        # Search all panels
        async def _search_one(pname, pc):
            c, ib, tr = await pc.find_client_by_email(email)
            return pname, c, ib, tr
        results = await asyncio.gather(
            *(_search_one(pn, pc) for pn, pc in panels.items()),
            return_exceptions=True,
        )
        matches = [
            (pn, c, ib, tr)
            for r in results
            if not isinstance(r, BaseException)
            for pn, c, ib, tr in [r]
            if c is not None
        ]

        if not matches:
            await reply(
                event,
                "❌ User not found!",
                buttons=[[Button.inline("◀️ Back", b"m")]],
            )
            return

        if len(matches) > 1:
            # Found on multiple panels — let user choose
            s["sr_matches"] = {pn: (c, ib, tr) for pn, c, ib, tr in matches}
            s["sr_email"] = email
            btns = [[Button.inline(f"🖥 {pn}", f"srp:{pn}".encode())] for pn, *_ in matches]
            btns.append([Button.inline("◀️ Back", b"m")])
            await reply(
                event,
                f"🔍 `{email}` found on **{len(matches)} panels**.\nSelect one:",
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

    enabled = client.get("enable", True)

    online_list = await p.get_online_clients()
    online = actual_email in online_list

    up = (traffic or {}).get("up", 0)
    down = (traffic or {}).get("down", 0)
    total = client.get("totalGB", 0)
    all_time = (traffic or {}).get("allTime", 0)
    remaining = max(0, total - up - down) if total > 0 else 0

    addr = server_addrs[found_panel]
    sub_url = sub_urls[found_panel]
    proxy_link = build_client_link(client, inbound, addr)
    sub_id = client.get("subId", "")
    sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None

    lines = [
        f"🖥 Panel: {found_panel}",
        f"👤 Email: `{actual_email}`",
        f"{'✅' if enabled else '🔴'} Status: {'Enabled' if enabled else 'Disabled'}",
        f"{'🟢' if online else '⚫'} Online: {'Yes' if online else 'No'}",
        "",
        f"📊 Traffic: ↑ {format_bytes(up)}  ↓ {format_bytes(down)}",
        f"📦 Limit: {format_bytes(total) if total > 0 else 'Unlimited'}",
        f"📉 Remaining: {format_bytes(remaining) if total > 0 else 'Unlimited'}",
        "",
        f"⏳ Duration: {format_expiry(client.get('expiryTime', 0))}",
        f"🌐 Inbound: {inbound.get('remark', '?')}",
    ]
    if sub_link:
        lines.append(f"🔗 Subscription: `{sub_link}`")
    lines += ["", f"📈 All-time: {format_bytes(all_time)}"]
    if proxy_link:
        lines += ["", f"`{proxy_link}`"]
    text = "\n".join(lines)

    toggle_label = "🔴 Disable" if enabled else "🟢 Enable"
    toggle_data = b"dis" if enabled else b"en"
    btns = [
        [Button.inline(toggle_label, toggle_data), Button.inline("🗑 Remove", b"rm")],
        [Button.inline("📊 Traffic", b"mt"), Button.inline("⏳ Days", b"md")],
        [Button.inline("📄 PDF", b"pdf"), Button.inline("◀️ Back", b"m")],
    ]

    if proxy_link:
        qr = make_qr(proxy_link)
        await reply(event, text, buttons=btns, file=qr)
    else:
        await reply(event, text, buttons=btns)


def register(bot):
    @bot.on(events.CallbackQuery(data=b"s"))
    @auth
    async def cb_search(event):
        await reply(
            event,
            "🔍 Enter email to search:",
            buttons=[[Button.inline("◀️ Back", b"m")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^srp:(.+)$"))
    @auth
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
    @auth
    async def cb_disable(event):
        s = st(event.sender_id)
        client = s.get("sr_client")
        if not client:
            return
        client["enable"] = False
        p = get_panel(s["sr_pid"])
        await p.update_client(s["sr_cid"], s["sr_iid"], client)
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])

    @bot.on(events.CallbackQuery(data=b"en"))
    @auth
    async def cb_enable(event):
        s = st(event.sender_id)
        client = s.get("sr_client")
        if not client:
            return
        client["enable"] = True
        p = get_panel(s["sr_pid"])
        await p.update_client(s["sr_cid"], s["sr_iid"], client)
        await show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])

    @bot.on(events.CallbackQuery(data=b"rm"))
    @auth
    async def cb_remove(event):
        await reply(
            event,
            "⚠️ Are you sure you want to remove this user?",
            buttons=[
                [
                    Button.inline("🗑 Yes, Remove", b"crm"),
                    Button.inline("❌ Cancel", b"sr"),
                ],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"crm"))
    @auth
    async def cb_confirm_remove(event):
        s = st(event.sender_id)
        cid = s.get("sr_cid")
        iid = s.get("sr_iid")
        pid = s.get("sr_pid")
        if not cid or not iid or not pid:
            return
        p = get_panel(pid)
        try:
            await p.delete_client(iid, cid)
            text = "✅ User removed successfully."
        except RuntimeError as e:
            text = f"⚠️ Error: {e}"
        clear(event.sender_id)
        await reply(event, text, buttons=[[Button.inline("◀️ Back", b"m")]])

    @bot.on(events.CallbackQuery(data=b"sr"))
    @auth
    async def cb_back_to_search(event):
        s = st(event.sender_id)
        s["state"] = None
        email = s.get("sr_email")
        if not email:
            return
        await show_search_result(event, event.sender_id, email, panel_name=s.get("sr_pid"))

    @bot.on(events.CallbackQuery(data=b"pdf"))
    @auth
    async def cb_export_pdf(event):
        s = st(event.sender_id)
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
        traffic_str = format_bytes(total) if total > 0 else "Unlimited"
        duration_str = format_expiry(client.get("expiryTime", 0))

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
            title=f"Account: {email}",
        )
        await event.answer("Generating PDF...")
        await bot.send_file(event.chat_id, pdf, caption="📄 Account PDF")
