import asyncio
import io
import json
import time
from datetime import datetime

from telethon import events, Button

from config import panels, server_addrs, sub_urls, get_panel, st, clear, bot, visible_panels, visible_inbounds, user_inbounds, has_perm
from db import log_activity, get_setting
from helpers import format_bytes, format_expiry, make_qr, rand_email, auth, reply, answer, search_result_buttons, format_inbound_button_label, build_client_dict
from i18n import t
from panel import build_client_link, SUPPORTED_PROTOCOLS
from pdf_export import generate_account_pdf


async def handle_move_email_input(event) -> bool:
    """Handle mv_email text input state. Returns True if handled."""
    uid = event.sender_id
    s = st(uid)
    if s.get("state") != "mv_email":
        return False
    s["state"] = None
    mv = s.get("mv")
    if not mv:
        return False
    email = event.text.strip()
    if not email:
        s["state"] = "mv_email"
        return True
    mv["email"] = email
    # _do_move is registered inside register(), call via the module-level ref
    await _do_move_ref(event, uid)
    return True


# Module-level ref set by register() so handle_move_email_input can call _do_move
_do_move_ref = None


async def show_search_result(event, uid: int, email: str, panel_name: str | None = None, back_data: bytes | None = None):
    s = st(uid)
    if back_data is not None:
        s["sr_back"] = back_data

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

    # Unsupported protocol guard
    if protocol not in SUPPORTED_PROTOCOLS:
        lines = [
            t("sr_panel", uid, panel=found_panel),
            t("sr_email", uid, email=actual_email),
            t("sr_inbound", uid, remark=inbound.get("remark", "?")),
            "",
            t("unsupported_protocol", uid, protocol=protocol),
        ]
        back = s.get("sr_back", b"m")
        btns = [[Button.inline(t("btn_back", uid), back)]]
        await reply(event, "\n".join(lines), buttons=btns)
        log_activity(uid, "search", json.dumps({"email": actual_email, "panel": found_panel}))
        return

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
        if total > 0:
            if remaining >= 1024**4:  # >= 1 TB
                remaining_gb = f"{remaining / (1024**4):.2f} {t('sr_simple_tb_unit', uid)}"
            else:
                remaining_gb = f"{remaining / (1024**3):.2f} {t('sr_simple_gb_unit', uid)}"
        else:
            remaining_gb = unlim
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
                remaining_days = t("days_unit", uid, value=dur_ms // 86_400_000)
                if exp < 0:
                    remaining_days += f" ({t('after_first_use', uid)})"
        lines = [
            t("sr_email", uid, email=actual_email),
            t(status_key, uid),
            t("sr_simple_traffic", uid, remaining=remaining_gb),
            t("sr_simple_duration", uid, remaining=remaining_days),
        ]
        if exp < 0:
            lines.append(t("sr_not_used", uid))
        caption = get_setting("simple_search_caption")
        if caption:
            lines.append("")
            lines.append(caption)
        back = s.get("sr_back", b"m")
        btns = [[Button.inline(t("btn_back", uid), back)]]
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
    ]
    if client.get("expiryTime", 0) < 0:
        lines.append(t("sr_not_used", uid))
    lines += [
        t("sr_inbound", uid, remark=inbound.get("remark", "?")),
    ]
    if sub_link:
        lines.append(t("sr_subscription", uid, link=sub_link))
    lines += ["", t("sr_alltime", uid, total=format_bytes(all_time))]
    if proxy_link:
        lines += ["", f"`{proxy_link}`"]
    text = "\n".join(lines)

    btns = search_result_buttons(uid, status, back_data=s.get("sr_back", b"m"))

    # Add re-create button if we just created this account
    rcr = s.get("rcr")
    if rcr and has_perm(uid, "create"):
        from .create import _recreate_label
        label = _recreate_label(uid, rcr.get("duration_days", 0),
                                rcr.get("traffic_gb", 0), rcr.get("start_after_use", False))
        btns.insert(0, [Button.inline(label, b"rcr")])

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
        s = st(uid)
        email = s.get("sr_email", "?")
        await reply(
            event,
            t("confirm_remove", uid, email=email),
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
        await answer(event, t("generating_pdf", uid))
        await bot.send_file(event.chat_id, pdf, caption=t("account_pdf", uid))
        log_activity(uid, "pdf_export", json.dumps({"email": email, "panel": pid}))

    # ── Move Account ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"mv"))
    @auth("modify")
    async def cb_move_start(event):
        """Show panel picker for move target."""
        uid = event.sender_id
        s = st(uid)
        if not s.get("sr_email"):
            return
        vp = visible_panels(uid)
        btns = [[Button.inline(f"\U0001f5a5 {name}", f"mvp:{name}".encode())] for name in sorted(vp)]
        btns.append([Button.inline(t("btn_cancel", uid), b"sr")])
        await reply(event, t("mv_pick_panel", uid), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^mvp:(.+)$"))
    @auth("modify")
    async def cb_move_pick_panel(event):
        """Show inbound picker for the selected panel."""
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        s = st(uid)
        if not s.get("sr_email"):
            return
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        inbounds = visible_inbounds(uid, panel_name, inbounds)
        btns = []
        for ib in inbounds:
            label = format_inbound_button_label(ib)
            btns.append([Button.inline(label, f"mvi:{panel_name}:{ib['id']}".encode())])
        btns.append([Button.inline(t("btn_back", uid), b"mv"),
                     Button.inline(t("btn_main_menu", uid), b"m")])
        await reply(event, t("mv_pick_inbound", uid, panel=panel_name), buttons=btns)

    global _do_move_ref

    async def _delete_client_safe(panel_client, inbound_id, client_id, protocol):
        """Delete a client, working around 3x-ui rejecting empty inbounds."""
        try:
            await panel_client.delete_client(inbound_id, client_id)
        except RuntimeError as e:
            if "no client remained" not in str(e).lower():
                raise
            # Last client in inbound — add a disabled placeholder, then retry
            import uuid as _uuid
            ph = {
                "id": str(_uuid.uuid4()), "email": f"_moved_{int(time.time())}",
                "enable": False, "totalGB": 0, "expiryTime": 1,
                "limitIp": 0, "subId": "", "comment": "", "reset": 0,
                "flow": "", "tgId": 0,
            }
            if protocol == "trojan":
                ph["password"] = "placeholder0"
            elif protocol == "shadowsocks":
                ph["password"] = ""
            await panel_client.add_client(inbound_id, ph)
            await panel_client.delete_client(inbound_id, client_id)

    async def _do_move(event, uid):
        """Execute the move using state data. Checks for duplicate email first."""
        s = st(uid)
        mv = s.get("mv")
        if not mv:
            return
        email = mv["email"]
        target_panel = mv["target_panel"]
        target_iid = mv["target_iid"]
        src_pid = mv["src_pid"]
        src_iid = mv["src_iid"]
        src_cid = mv["src_cid"]
        client = mv["client"]
        src_proto = mv["src_proto"]

        # Check for duplicate on target — skip the source inbound itself
        target_p = get_panel(target_panel)
        target_inbounds = await target_p.list_inbounds()
        target_ib = next((ib for ib in target_inbounds if ib["id"] == target_iid), None)
        if not target_ib:
            await reply(event, t("inbound_not_found", uid),
                        buttons=[[Button.inline(t("btn_back", uid), b"sr")]])
            return

        same_panel = target_panel == src_pid

        # Check duplicate on target panel (exclude source inbound for same-panel)
        dup_found = False
        for ib in target_inbounds:
            if same_panel and ib["id"] == src_iid:
                continue  # skip source inbound — that's the one we're moving FROM
            for tc in json.loads(ib.get("settings", "{}")).get("clients", []):
                if tc.get("email", "").lower() == email.lower():
                    dup_found = True
                    break
            if dup_found:
                break

        if dup_found:
            btns = [
                [Button.inline(t("btn_random_email", uid), b"mv:re")],
                [Button.inline(t("btn_custom_email", uid), b"mv:ce")],
                [Button.inline(t("btn_cancel", uid), b"sr")],
            ]
            await reply(event,
                        t("mv_duplicate", uid, email=email, panel=target_panel),
                        buttons=btns)
            return

        target_proto = target_ib["protocol"]
        target_stream = json.loads(target_ib.get("streamSettings", "{}"))
        target_settings = json.loads(target_ib.get("settings", "{}"))

        # Calculate remaining traffic (new inbound resets counters to 0)
        traffic_data = mv.get("traffic") or {}
        used = traffic_data.get("up", 0) + traffic_data.get("down", 0)
        orig_total = client.get("totalGB", 0)
        remaining_total = max(1, orig_total - used) if orig_total > 0 else 0

        # Build the client dict for the target
        if src_proto == target_proto:
            new_client = dict(client)
            new_client["email"] = email
        else:
            new_client = build_client_dict(
                email, orig_total, client.get("expiryTime", 0),
                target_proto, target_stream, target_settings,
            )
            new_client["enable"] = client.get("enable", True)
        new_client["totalGB"] = remaining_total

        # Adapt flow for vless
        if target_proto == "vless":
            network = target_stream.get("network", "")
            security = target_stream.get("security", "")
            if network == "tcp" and security in ("tls", "reality"):
                new_client["flow"] = "xtls-rprx-vision"
            else:
                new_client["flow"] = ""

        src_p = get_panel(src_pid)

        if same_panel:
            # Same panel: delete first (3x-ui enforces panel-wide email uniqueness)
            try:
                await _delete_client_safe(src_p, src_iid, src_cid, src_proto)
            except RuntimeError as e:
                await reply(event, t("error_msg", uid, error=e),
                            buttons=[[Button.inline(t("btn_back", uid), b"sr")]])
                return
            try:
                await target_p.add_client(target_iid, new_client)
            except RuntimeError as e:
                # Add failed after delete — re-add to source to recover
                try:
                    recover = dict(client)
                    recover["totalGB"] = orig_total  # restore original limit
                    await src_p.add_client(src_iid, recover)
                except Exception:
                    pass
                await reply(event, t("error_msg", uid, error=e),
                            buttons=[[Button.inline(t("btn_back", uid), b"sr")]])
                return
        else:
            # Cross panel: add first (safer — no data loss if add fails)
            try:
                await target_p.add_client(target_iid, new_client)
            except RuntimeError as e:
                if "duplicate" in str(e).lower():
                    btns = [
                        [Button.inline(t("btn_random_email", uid), b"mv:re")],
                        [Button.inline(t("btn_custom_email", uid), b"mv:ce")],
                        [Button.inline(t("btn_cancel", uid), b"sr")],
                    ]
                    await reply(event,
                                t("mv_duplicate", uid, email=email, panel=target_panel),
                                buttons=btns)
                else:
                    await reply(event, t("error_msg", uid, error=e),
                                buttons=[[Button.inline(t("btn_back", uid), b"sr")]])
                return
            try:
                await _delete_client_safe(src_p, src_iid, src_cid, src_proto)
            except RuntimeError as e:
                await reply(event, t("error_msg", uid, error=e),
                            buttons=[[Button.inline(t("btn_back", uid), b"sr")]])
                return

        log_activity(uid, "move", json.dumps({
            "email": email,
            "from_panel": src_pid, "from_inbound": src_iid,
            "to_panel": target_panel, "to_inbound": target_iid,
        }))
        get_panel(src_pid).invalidate_cache()
        s.pop("mv", None)
        await show_search_result(event, uid, email, panel_name=target_panel)

    @bot.on(events.CallbackQuery(pattern=rb"^mvi:(.+):(\d+)$"))
    @auth("modify")
    async def cb_move_select_inbound(event):
        """Save target info and attempt move."""
        uid = event.sender_id
        target_panel = event.pattern_match.group(1).decode()
        target_iid = int(event.pattern_match.group(2))
        s = st(uid)
        client = s.get("sr_client")
        if not client:
            return
        s["mv"] = {
            "email": s["sr_email"],
            "target_panel": target_panel,
            "target_iid": target_iid,
            "src_pid": s["sr_pid"],
            "src_iid": s["sr_iid"],
            "src_cid": s["sr_cid"],
            "client": dict(client),
            "src_proto": s["sr_protocol"],
            "traffic": s.get("sr_traffic"),
        }
        await _do_move(event, uid)

    @bot.on(events.CallbackQuery(data=b"mv:re"))
    @auth("modify")
    async def cb_move_random_email(event):
        """Use random email for move (duplicate resolution)."""
        uid = event.sender_id
        s = st(uid)
        mv = s.get("mv")
        if not mv:
            return
        mv["email"] = rand_email()
        await _do_move(event, uid)

    @bot.on(events.CallbackQuery(data=b"mv:ce"))
    @auth("modify")
    async def cb_move_custom_email(event):
        """Prompt for custom email for move."""
        uid = event.sender_id
        s = st(uid)
        s["state"] = "mv_email"
        await reply(event, t("mv_enter_email", uid),
                    buttons=[[Button.inline(t("btn_cancel", uid), b"sr")]])

    _do_move_ref = _do_move

    @bot.on(events.CallbackQuery(data=b"txt"))
    @auth("pdf")
    async def cb_export_txt(event):
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
        email = client["email"]

        lines = [f"Account ID: {email}"]
        lines.append(f"Panel: {pid}")
        lines.append(f"Traffic: {traffic_str}")
        lines.append(f"Duration: {duration_str}")
        if proxy_link:
            lines.append(f"Link: {proxy_link}")
        if sub_link:
            lines.append(f"Subscription: {sub_link}")

        txt_buf = io.BytesIO("\n".join(lines).encode("utf-8"))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        txt_buf.name = f"{email}_{stamp}.txt"
        await answer(event, t("generating_txt", uid))
        await bot.send_file(event.chat_id, txt_buf, caption=t("account_txt", uid))
        log_activity(uid, "txt_export", json.dumps({"email": email, "panel": pid}))
