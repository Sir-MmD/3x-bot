import json
import time

from telethon import events, Button

from config import get_panel, clear, has_perm
from helpers import auth, reply
from i18n import t


def register(bot):
    @bot.on(events.CallbackQuery(pattern=rb"^il:(.+)$"))
    @auth("search")
    async def cb_inbound_list(event):
        uid = event.sender_id
        clear(uid)
        panel_name = event.pattern_match.group(1).decode()
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
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
            label = f"{icon} {ib['remark']} | {ib['port']} [{active}/{total}]"
            btns.append([Button.inline(label, f"ib:{panel_name}:{ib['id']}".encode())])
        if has_perm(uid, "bulk"):
            btns.append([Button.inline(t("btn_bulk_operation", uid), f"bo:{panel_name}".encode())])
        btns.append([Button.inline(t("btn_back", uid), b"m")])
        await reply(event, t("inbound_list_title", uid, panel=panel_name), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^ib:(.+):(\d+)$"))
    @auth("search")
    async def cb_inbound_detail(event):
        uid = event.sender_id
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
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
        enabled_text = t("status_enabled", uid) if inbound.get("enable") else t("status_disabled", uid)

        lines = [
            f"\U0001f310 **{inbound['remark']}**",
            t("sr_panel", uid, panel=panel_name),
            t("ib_protocol", uid, protocol=inbound["protocol"]),
            t("ib_port", uid, port=inbound["port"]),
            enabled_text,
            t("ib_clients", uid, count=len(clients)),
        ]
        text = "\n".join(lines)
        btns = []
        if has_perm(uid, "create"):
            btns.append([
                Button.inline(t("btn_create_account", uid), f"ca:{panel_name}:{iid}".encode()),
                Button.inline(t("btn_bulk_create", uid), f"bk:{panel_name}:{iid}".encode()),
            ])
        btns.append([Button.inline(t("btn_back", uid), f"il:{panel_name}".encode())])
        await reply(event, text, buttons=btns)
