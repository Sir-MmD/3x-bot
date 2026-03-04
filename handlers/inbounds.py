import json
import math
import time

from telethon import events, Button

from config import get_panel, clear, has_perm, user_perms, visible_panels, visible_inbounds, user_inbounds
from db import log_activity
from helpers import auth, reply, answer, format_client_line, format_bytes
from i18n import t

_PAGE_SIZE = 50


def register(bot):
    # ── Panel sub-menu ──────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^pm:(.+)$"))
    @auth
    async def cb_panel_menu(event):
        uid = event.sender_id
        clear(uid)
        panel_name = event.pattern_match.group(1).decode()
        if panel_name not in visible_panels(uid):
            return

        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        vis = visible_inbounds(uid, panel_name, inbounds)

        # Collect stats from visible inbounds
        now_ms = int(time.time() * 1000)
        total_clients = 0
        depleted = 0
        disabled = 0
        total_up = 0
        total_down = 0
        all_emails: set[str] = set()

        for ib in vis:
            clients = json.loads(ib.get("settings", "{}")).get("clients", [])
            stats = {cs["email"]: cs for cs in ib.get("clientStats") or []}
            for c in clients:
                total_clients += 1
                email = c.get("email", "")
                all_emails.add(email)
                tr = stats.get(email)
                if tr:
                    total_up += tr.get("up", 0)
                    total_down += tr.get("down", 0)
                if not c.get("enable", True):
                    disabled += 1
                    continue
                exp = c.get("expiryTime", 0)
                if exp > 0 and exp < now_ms:
                    depleted += 1
                    continue
                limit = c.get("totalGB", 0)
                if limit > 0 and tr and tr.get("up", 0) + tr.get("down", 0) >= limit:
                    depleted += 1

        # Online count
        try:
            online_emails = await p.get_online_clients()
            online_count = sum(1 for e in online_emails if e in all_emails)
        except Exception:
            online_count = 0

        text = t("pm_stats", uid,
                  panel=panel_name,
                  total_clients=total_clients,
                  online=online_count,
                  depleted=depleted,
                  disabled=disabled,
                  total_inbounds=len(vis),
                  total_usage=format_bytes(total_up + total_down),
                  sent=format_bytes(total_up),
                  received=format_bytes(total_down))

        perms = user_perms(uid)
        btns = []
        if perms & {"create", "bulk"}:
            btns.append([Button.inline(t("btn_inbound_list_short", uid), f"il:{panel_name}".encode())])
        if has_perm(uid, "bulk"):
            btns.append([Button.inline(t("btn_bulk_operation", uid), f"bo:{panel_name}".encode())])
        btns.append([Button.inline(t("btn_back", uid), b"m")])
        await reply(event, text, buttons=btns)

    # ── Inbound list ────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^il:(.+)$"))
    @auth
    async def cb_inbound_list(event):
        uid = event.sender_id
        clear(uid)
        panel_name = event.pattern_match.group(1).decode()
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        inbounds = visible_inbounds(uid, panel_name, inbounds)
        btns = []
        for ib in inbounds:
            icon = "\u2705" if ib.get("enable") else "\U0001f534"
            clients = json.loads(ib.get("settings", "{}")).get("clients", [])
            stats = {cs["email"]: cs for cs in ib.get("clientStats") or []}
            now_ms = int(time.time() * 1000)
            active = 0
            for c in clients:
                if not c.get("enable", True):
                    continue
                exp = c.get("expiryTime", 0)
                if exp > 0 and exp < now_ms:
                    continue
                limit = c.get("totalGB", 0)
                if limit > 0:
                    cs = stats.get(c.get("email", ""))
                    if cs and cs.get("up", 0) + cs.get("down", 0) >= limit:
                        continue
                active += 1
            total = len(clients)
            label = f"{icon} {ib['remark']} | {ib['port']} | [{active}/{total}]"
            btns.append([Button.inline(label, f"ib:{panel_name}:{ib['id']}".encode())])
        btns.append([Button.inline(t("btn_back", uid), f"pm:{panel_name}".encode())])
        await reply(event, t("inbound_list_title", uid, panel=panel_name), buttons=btns)

    # ── Client list ─────────────────────────────────────────────────────

    async def _show_client_list(event, uid, panel_name, iid, page=1):
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        inbound = next((ib for ib in inbounds if ib["id"] == iid), None)
        if not inbound:
            await reply(
                event, t("inbound_not_found", uid),
                buttons=[[Button.inline(t("btn_back", uid), f"il:{panel_name}".encode())]],
            )
            return

        settings = json.loads(inbound.get("settings", "{}"))
        clients = settings.get("clients", [])
        stats = {cs["email"]: cs for cs in inbound.get("clientStats") or []}

        total = len(clients)
        pages = max(1, math.ceil(total / _PAGE_SIZE))
        page = max(1, min(page, pages))
        start = (page - 1) * _PAGE_SIZE
        page_clients = clients[start:start + _PAGE_SIZE]

        lines = [t("client_list_title", uid,
                    remark=inbound["remark"], panel=panel_name,
                    count=total, page=page, pages=pages), ""]
        for c in page_clients:
            tr = stats.get(c.get("email", ""))
            lines.append(format_client_line(c, tr, uid))

        text = "\n".join(lines)
        btns = []

        # Pagination
        if pages > 1:
            nav = []
            if page > 1:
                nav.append(Button.inline("\u25c0\ufe0f", f"ibp:{panel_name}:{iid}:{page - 1}".encode()))
            nav.append(Button.inline(f"{page}/{pages}", b"noop"))
            if page < pages:
                nav.append(Button.inline("\u25b6\ufe0f", f"ibp:{panel_name}:{iid}:{page + 1}".encode()))
            btns.append(nav)

        # Action buttons
        if has_perm(uid, "create"):
            btns.append([
                Button.inline(t("btn_add_client", uid), f"ca:{panel_name}:{iid}".encode()),
                Button.inline(t("btn_add_bulk", uid), f"bk:{panel_name}:{iid}".encode()),
            ])
        if has_perm(uid, "bulk"):
            btns.append([
                Button.inline(t("btn_reset_traffic", uid), f"ibrt:{panel_name}:{iid}".encode()),
                Button.inline(t("btn_delete_depleted", uid), f"ibdd:{panel_name}:{iid}".encode()),
            ])
        btns.append([Button.inline(t("btn_back", uid), f"il:{panel_name}".encode())])
        await reply(event, text, buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^ib:(.+):(\d+)$"))
    @auth
    async def cb_client_list(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        await _show_client_list(event, uid, panel_name, iid)

    @bot.on(events.CallbackQuery(pattern=rb"^ibp:(.+):(\d+):(\d+)$"))
    @auth
    async def cb_client_list_page(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        page = int(event.pattern_match.group(3))
        await _show_client_list(event, uid, panel_name, iid, page)

    # ── Reset all traffic ───────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^ibrt:(.+):(\d+)$"))
    @auth("bulk")
    async def cb_reset_all_traffic(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        await reply(
            event,
            t("confirm_reset_all_traffic", uid),
            buttons=[
                [Button.inline(t("btn_yes_reset", uid), f"ibrtc:{panel_name}:{iid}".encode())],
                [Button.inline(t("btn_cancel", uid), f"ib:{panel_name}:{iid}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^ibrtc:(.+):(\d+)$"))
    @auth("bulk")
    async def cb_confirm_reset_all_traffic(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        p = get_panel(panel_name)
        try:
            await p.reset_all_client_traffics(iid)
        except Exception as e:
            await reply(
                event, t("error_msg", uid, error=e),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode())]],
            )
            return
        log_activity(uid, "reset_all_traffic", json.dumps({"panel": panel_name, "inbound": iid}))
        await answer(event,t("reset_all_traffic_success", uid))
        await _show_client_list(event, uid, panel_name, iid)

    # ── Delete depleted ─────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^ibdd:(.+):(\d+)$"))
    @auth("bulk")
    async def cb_delete_depleted(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        await reply(
            event,
            t("confirm_delete_depleted", uid),
            buttons=[
                [Button.inline(t("btn_yes_delete_depleted", uid), f"ibddc:{panel_name}:{iid}".encode())],
                [Button.inline(t("btn_cancel", uid), f"ib:{panel_name}:{iid}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^ibddc:(.+):(\d+)$"))
    @auth("bulk")
    async def cb_confirm_delete_depleted(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        allowed = user_inbounds(uid, panel_name)
        if allowed is not None and iid not in allowed:
            return
        p = get_panel(panel_name)
        try:
            await p.delete_depleted_clients(iid)
        except Exception as e:
            await reply(
                event, t("error_msg", uid, error=e),
                buttons=[[Button.inline(t("btn_back", uid), f"ib:{panel_name}:{iid}".encode())]],
            )
            return
        log_activity(uid, "delete_depleted", json.dumps({"panel": panel_name, "inbound": iid}))
        await answer(event,t("delete_depleted_success", uid))
        await _show_client_list(event, uid, panel_name, iid)

    # ── Noop (page indicator button) ────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"noop"))
    async def cb_noop(event):
        await answer(event,)
