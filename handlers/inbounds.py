import json
import time

from telethon import events, Button

from config import get_panel, clear
from helpers import auth, reply


def register(bot):
    @bot.on(events.CallbackQuery(pattern=rb"^il:(.+)$"))
    @auth
    async def cb_inbound_list(event):
        clear(event.sender_id)
        panel_name = event.pattern_match.group(1).decode()
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        btns = []
        for ib in inbounds:
            icon = "✅" if ib.get("enable") else "🔴"
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
        btns.append([Button.inline("⚡ Bulk Operation", f"bo:{panel_name}".encode())])
        btns.append([Button.inline("◀️ Back", b"m")])
        await reply(event, f"📋 **Inbounds — {panel_name}:**", buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^ib:(.+):(\d+)$"))
    @auth
    async def cb_inbound_detail(event):
        panel_name = event.pattern_match.group(1).decode()
        iid = int(event.pattern_match.group(2))
        p = get_panel(panel_name)
        inbounds = await p.list_inbounds()
        inbound = next((ib for ib in inbounds if ib["id"] == iid), None)
        if not inbound:
            await reply(
                event, "❌ Inbound not found.",
                buttons=[[Button.inline("◀️ Back", f"il:{panel_name}".encode())]],
            )
            return

        settings = json.loads(inbound.get("settings", "{}"))
        clients = settings.get("clients", [])
        enabled = "✅ Enabled" if inbound.get("enable") else "🔴 Disabled"

        lines = [
            f"🌐 **{inbound['remark']}**",
            f"🖥 Panel: {panel_name}",
            f"🔒 Protocol: {inbound['protocol']}",
            f"🔌 Port: {inbound['port']}",
            f"{enabled}",
            f"👥 Clients: {len(clients)}",
        ]
        text = "\n".join(lines)
        btns = [
            [
                Button.inline("➕ Create Account", f"ca:{panel_name}:{iid}".encode()),
                Button.inline("📦 Bulk Create", f"bk:{panel_name}:{iid}".encode()),
            ],
            [Button.inline("◀️ Back", f"il:{panel_name}".encode())],
        ]
        await reply(event, text, buttons=btns)
