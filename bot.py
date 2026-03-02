import asyncio
import io
import json
import random
import string
import time
import tomllib
import uuid
from base64 import b64encode
from pathlib import Path
from urllib.parse import urlparse

import qrcode
import socks
from telethon import TelegramClient, events, Button
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

from panel import PanelClient, build_client_link
from pdf_export import generate_account_pdf

# ── Config ───────────────────────────────────────────────────────────────────

cfg = tomllib.loads(Path("config.toml").read_text())
bot_cfg = cfg["bot"]

ALLOWED = set(bot_cfg["allowed_users"])

panels: dict[str, PanelClient] = {}
server_addrs: dict[str, str] = {}
sub_urls: dict[str, str | None] = {}

for pcfg in cfg["panels"]:
    name = pcfg["name"]
    panels[name] = PanelClient(pcfg["url"], pcfg["username"], pcfg["password"], name=name, proxy=pcfg.get("proxy", ""))
    server_addrs[name] = urlparse(pcfg["url"]).hostname
    sub_urls[name] = pcfg.get("sub_url", "").rstrip("/") or None

_proxy_types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}

def _parse_proxy(url: str):
    """Parse a proxy URL into a PySocks tuple for Telethon."""
    p = urlparse(url)
    scheme = p.scheme.lower()
    if scheme not in _proxy_types:
        raise ValueError(f"Unsupported proxy type: {scheme} (use socks5, socks4, or http)")
    return (_proxy_types[scheme], p.hostname, p.port, True, p.username, p.password)

_bot_proxy_url = bot_cfg.get("proxy", "")
_bot_proxy = _parse_proxy(_bot_proxy_url) if _bot_proxy_url else None

bot = TelegramClient("bot", bot_cfg["api_id"], bot_cfg["api_hash"], proxy=_bot_proxy)


def get_panel(name: str) -> PanelClient:
    return panels[name]


# ── State ────────────────────────────────────────────────────────────────────

states: dict[int, dict] = {}


def st(uid: int) -> dict:
    return states.setdefault(uid, {})


def clear(uid: int):
    states.pop(uid, None)


# ── Helpers ──────────────────────────────────────────────────────────────────

def format_bytes(b: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def format_expiry(ms: int) -> str:
    if ms == 0:
        return "Unlimited"
    now_ms = int(time.time() * 1000)
    if ms < 0:
        dur = abs(ms)
    else:
        dur = ms - now_ms
    if dur <= 0:
        return "Expired"
    days = dur // 86_400_000
    hours = (dur % 86_400_000) // 3_600_000
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    text = " ".join(parts) or "< 1h"
    if ms > 0:
        exp_date = time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        text += f" ({exp_date})"
    else:
        text += " (after first use)"
    return text


def rand_email() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def generate_bulk_emails(method: str, count: int, prefix: str = "", postfix: str = "") -> list[str]:
    """Generate email list based on naming method."""
    emails = []
    for i in range(1, count + 1):
        if method == "r":
            emails.append(rand_email())
        elif method == "rp":
            emails.append(f"{rand_email()}_{prefix}")
        elif method == "pr":
            emails.append(f"{prefix}_{rand_email()}")
        elif method == "pnr":
            emails.append(f"{prefix}_{i}_{rand_email()}")
        elif method == "pnrx":
            emails.append(f"{prefix}_{i}_{rand_email()}_{postfix}")
        elif method == "pn":
            emails.append(f"{prefix}_{i}")
        elif method == "pnx":
            emails.append(f"{prefix}_{i}_{postfix}")
    return emails


def make_qr(data: str) -> io.BytesIO:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "qr.png"
    return buf


def auth(func):
    """Decorator that silently ignores non-allowed users."""
    async def wrapper(event):
        if event.sender_id not in ALLOWED:
            return
        return await func(event)
    return wrapper


async def reply(event, text, buttons=None, file=None):
    """Send a reply, handling photo<->text transitions for callbacks."""
    chat = event.chat_id
    is_cb = isinstance(event, events.CallbackQuery.Event)
    if file:
        if is_cb:
            try:
                await event.delete()
            except Exception:
                pass
        return await bot.send_file(
            chat, file, caption=text, buttons=buttons, parse_mode="md"
        )
    if is_cb:
        msg = await event.get_message()
        if msg and msg.media:
            try:
                await event.delete()
            except Exception:
                pass
            return await bot.send_message(
                chat, text, buttons=buttons, parse_mode="md"
            )
        return await event.edit(text, buttons=buttons, parse_mode="md")
    return await event.respond(text, buttons=buttons, parse_mode="md")


def _build_client_dict(
    email: str, total_bytes: int, expiry_time: int,
    protocol: str, stream: dict, settings: dict,
) -> dict:
    """Build a client dict with protocol-specific fields."""
    client_uuid = str(uuid.uuid4())
    sub_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))

    client_dict = {
        "id": client_uuid,
        "email": email,
        "enable": True,
        "totalGB": total_bytes,
        "expiryTime": expiry_time,
        "limitIp": 0,
        "subId": sub_id,
        "comment": "",
        "reset": 0,
        "flow": "",
        "tgId": 0,
    }

    if protocol == "vless":
        network = stream.get("network", "")
        security = stream.get("security", "")
        if network == "tcp" and security in ("tls", "reality"):
            client_dict["flow"] = "xtls-rprx-vision"
    elif protocol == "trojan":
        client_dict["password"] = client_uuid
    elif protocol == "shadowsocks":
        method = settings.get("method", "")
        if "2022" in method:
            key_len = 16 if "128" in method else 32
            client_dict["password"] = b64encode(random.randbytes(key_len)).decode()
        else:
            client_dict["password"] = "".join(
                random.choices(string.ascii_letters + string.digits, k=16)
            )

    return client_dict


# ── Main Menu ────────────────────────────────────────────────────────────────

def main_menu_buttons():
    btns = [[Button.inline("🔍 Search User", b"s")]]
    for name in panels:
        btns.append([Button.inline(f"📋 Inbound List ({name})", f"il:{name}".encode())])
    return btns


MAIN_TEXT = "🏠 **Main Menu**\nType an email to search, or pick an option:"


@bot.on(events.NewMessage(pattern="/start"))
@auth
async def cmd_start(event):
    clear(event.sender_id)
    await event.respond(MAIN_TEXT, buttons=main_menu_buttons(), parse_mode="md")


@bot.on(events.CallbackQuery(data=b"m"))
@auth
async def cb_main(event):
    clear(event.sender_id)
    await reply(event, MAIN_TEXT, buttons=main_menu_buttons())


# ── Search User ──────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"s"))
@auth
async def cb_search(event):
    await reply(
        event,
        "🔍 Enter email to search:",
        buttons=[[Button.inline("◀️ Back", b"m")]],
    )


@bot.on(events.NewMessage)
@auth
async def on_message(event):
    if not event.text or event.text.startswith("/"):
        return
    s = st(event.sender_id)
    state = s.get("state")

    # ── Create flow states ───────────────────────────────────────────────
    if state == "cr_email":
        s["state"] = None
        email = event.text.strip()
        s["cr"]["email"] = email
        s["state"] = "cr_traffic"
        await event.respond(
            "📦 Enter traffic in GB (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())]],
        )
        return

    if state == "cr_traffic":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "cr_traffic"
            await event.respond("⚠️ Invalid number. Enter traffic in GB (0 = unlimited):")
            return
        s["cr"]["traffic_gb"] = gb
        s["state"] = "cr_duration"
        await event.respond(
            "⏳ Enter duration in days (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())]],
        )
        return

    if state == "cr_duration":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "cr_duration"
            await event.respond("⚠️ Invalid number. Enter duration in days (0 = unlimited):")
            return
        s["cr"]["duration_days"] = days
        if days > 0:
            s["state"] = "cr_sau"
            await event.respond(
                "⏱ Start timer after first use?",
                buttons=[
                    [Button.inline("✅ Yes", b"sau:y"), Button.inline("❌ No", b"sau:n")],
                    [Button.inline("◀️ Back", f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())],
                ],
            )
        else:
            s["cr"]["start_after_use"] = False
            await _create_client(event, event.sender_id)
        return

    # ── Bulk create flow states ──────────────────────────────────────────
    if state == "bk_count":
        s["state"] = None
        try:
            count = int(event.text.strip())
        except ValueError:
            s["state"] = "bk_count"
            await event.respond("⚠️ Invalid number. Enter a count (1-100):")
            return
        if count < 1 or count > 100:
            s["state"] = "bk_count"
            await event.respond("⚠️ Count must be 1-100. Try again:")
            return
        s["bk"]["count"] = count
        await event.respond(
            f"✅ Count: {count}\n\n🏷 Choose naming method:",
            buttons=[
                [
                    Button.inline("🎲 Random", b"bkn:r"),
                    Button.inline("Rand+Prefix", b"bkn:rp"),
                    Button.inline("Prefix+Rand", b"bkn:pr"),
                ],
                [
                    Button.inline("Prefix+Num+Rand", b"bkn:pnr"),
                    Button.inline("Prefix+Num+Rand+Post", b"bkn:pnrx"),
                ],
                [
                    Button.inline("Prefix+Num", b"bkn:pn"),
                    Button.inline("Prefix+Num+Post", b"bkn:pnx"),
                ],
                [Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())],
            ],
        )
        return

    if state == "bk_prefix":
        s["state"] = None
        prefix = event.text.strip()
        if not prefix:
            s["state"] = "bk_prefix"
            await event.respond("⚠️ Prefix cannot be empty. Try again:")
            return
        s["bk"]["prefix"] = prefix
        method = s["bk"]["method"]
        # Methods needing postfix go to bk_postfix, others generate now
        if method in ("pnrx", "pnx"):
            s["state"] = "bk_postfix"
            await event.respond(
                f"🏷 Prefix: `{prefix}`\n\nEnter postfix:",
                buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
            )
        else:
            s["bk"]["emails"] = generate_bulk_emails(method, s["bk"]["count"], prefix=prefix)
            s["state"] = "bk_traffic"
            sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
            await event.respond(
                f"✅ Preview: {sample}{'...' if s['bk']['count'] > 3 else ''}\n\n"
                "📦 Enter traffic in GB (0 = unlimited):",
                buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
                parse_mode="md",
            )
        return

    if state == "bk_postfix":
        s["state"] = None
        postfix = event.text.strip()
        if not postfix:
            s["state"] = "bk_postfix"
            await event.respond("⚠️ Postfix cannot be empty. Try again:")
            return
        s["bk"]["postfix"] = postfix
        method = s["bk"]["method"]
        prefix = s["bk"]["prefix"]
        s["bk"]["emails"] = generate_bulk_emails(method, s["bk"]["count"], prefix=prefix, postfix=postfix)
        s["state"] = "bk_traffic"
        sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
        await event.respond(
            f"✅ Preview: {sample}{'...' if s['bk']['count'] > 3 else ''}\n\n"
            "📦 Enter traffic in GB (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
            parse_mode="md",
        )
        return

    if state == "bk_emails":
        s["state"] = None
        raw = event.text.strip()
        emails = [e.strip() for e in raw.splitlines() if e.strip()]
        if not emails:
            s["state"] = "bk_emails"
            await event.respond("⚠️ No emails found. Send one email per line:")
            return
        if len(emails) > 100:
            s["state"] = "bk_emails"
            await event.respond("⚠️ Max 100 accounts. Try again:")
            return
        s["bk"]["emails"] = emails
        s["state"] = "bk_traffic"
        await event.respond(
            f"✅ {len(emails)} email(s) received.\n\n"
            "📦 Enter traffic in GB (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
        )
        return

    if state == "bk_traffic":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "bk_traffic"
            await event.respond("⚠️ Invalid number. Enter traffic in GB (0 = unlimited):")
            return
        s["bk"]["traffic_gb"] = gb
        s["state"] = "bk_duration"
        await event.respond(
            "⏳ Enter duration in days (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
        )
        return

    if state == "bk_duration":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "bk_duration"
            await event.respond("⚠️ Invalid number. Enter duration in days (0 = unlimited):")
            return
        s["bk"]["duration_days"] = days
        if days > 0:
            s["state"] = "bk_sau"
            await event.respond(
                "⏱ Start timer after first use?",
                buttons=[
                    [Button.inline("✅ Yes", b"bksa:y"), Button.inline("❌ No", b"bksa:n")],
                    [Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())],
                ],
            )
        else:
            s["bk"]["start_after_use"] = False
            await _bulk_create_clients(event, event.sender_id)
        return

    # ── Modify traffic states ────────────────────────────────────────────
    if state == "mt_edit":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_edit"
            await event.respond("⚠️ Invalid number. Enter traffic in GB (0 = unlimited):")
            return
        new_bytes = int(gb * 1024**3) if gb > 0 else 0
        client = s["sr_client"]
        client["totalGB"] = new_bytes
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond("✅ Traffic limit updated.")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return

    if state == "mt_add":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_add"
            await event.respond("⚠️ Invalid number. Enter GB to add:")
            return
        if gb <= 0:
            s["state"] = "mt_add"
            await event.respond("⚠️ Must be positive. Enter GB to add:")
            return
        client = s["sr_client"]
        if client.get("totalGB", 0) == 0:
            await event.respond("⚠️ Traffic is already unlimited.")
            await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return
        client["totalGB"] += int(gb * 1024**3)
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond("✅ Traffic added.")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return

    if state == "mt_sub":
        s["state"] = None
        try:
            gb = float(event.text.strip())
        except ValueError:
            s["state"] = "mt_sub"
            await event.respond("⚠️ Invalid number. Enter GB to subtract:")
            return
        if gb <= 0:
            s["state"] = "mt_sub"
            await event.respond("⚠️ Must be positive. Enter GB to subtract:")
            return
        client = s["sr_client"]
        cur = client.get("totalGB", 0)
        if cur == 0:
            await event.respond("⚠️ Traffic is unlimited, nothing to subtract from.")
            await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return
        sub_bytes = int(gb * 1024**3)
        client["totalGB"] = max(0, cur - sub_bytes)
        if client["totalGB"] == 0:
            client["totalGB"] = 1  # avoid setting to unlimited; use Edit Total for that
            await event.respond("⚠️ Result would be 0 (unlimited). Set to minimum instead.")
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(f"✅ Subtracted {gb} GB.")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return

    # ── Modify days states ────────────────────────────────────────────────
    if state == "md_edit":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_edit"
            await event.respond("⚠️ Invalid number. Enter days (0 = unlimited):")
            return
        if days == 0:
            s["sr_client"]["expiryTime"] = 0
            p = get_panel(s["sr_pid"])
            try:
                await p.update_client(s["sr_cid"], s["sr_iid"], s["sr_client"])
                await event.respond("✅ Duration set to unlimited.")
            except RuntimeError as e:
                await event.respond(f"⚠️ Error: {e}")
            await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        else:
            s["md_days"] = days
            s["state"] = "md_sau"
            await event.respond(
                "⏱ Start timer after first use?",
                buttons=[
                    [Button.inline("✅ Yes", b"mdsa:y"), Button.inline("❌ No", b"mdsa:n")],
                    [Button.inline("◀️ Back", b"sr")],
                ],
            )
        return

    if state == "md_add":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_add"
            await event.respond("⚠️ Invalid number. Enter days to add:")
            return
        if days <= 0:
            s["state"] = "md_add"
            await event.respond("⚠️ Must be positive. Enter days to add:")
            return
        client = s["sr_client"]
        cur = client.get("expiryTime", 0)
        add_ms = days * 86_400_000
        if cur == 0:
            await event.respond("⚠️ Duration is already unlimited.")
            await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return
        if cur < 0:
            client["expiryTime"] = cur - add_ms  # more negative = longer relative duration
        else:
            client["expiryTime"] = cur + add_ms
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(f"✅ Added {days} day(s).")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return

    if state == "md_sub":
        s["state"] = None
        try:
            days = int(event.text.strip())
        except ValueError:
            s["state"] = "md_sub"
            await event.respond("⚠️ Invalid number. Enter days to subtract:")
            return
        if days <= 0:
            s["state"] = "md_sub"
            await event.respond("⚠️ Must be positive. Enter days to subtract:")
            return
        client = s["sr_client"]
        cur = client.get("expiryTime", 0)
        sub_ms = days * 86_400_000
        if cur == 0:
            await event.respond("⚠️ Duration is unlimited, nothing to subtract from.")
            await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
            return
        if cur < 0:
            # Relative duration: less negative = shorter
            new_val = cur + sub_ms
            if new_val >= 0:
                new_val = -86_400_000  # minimum 1 day
                await event.respond("⚠️ Result too low. Set to minimum 1 day.")
            client["expiryTime"] = new_val
        else:
            # Absolute: subtract but don't go below now
            new_val = cur - sub_ms
            now_ms = int(time.time() * 1000)
            if new_val <= now_ms:
                new_val = now_ms + 86_400_000  # minimum 1 day from now
                await event.respond("⚠️ Result would be in the past. Set to 1 day from now.")
            client["expiryTime"] = new_val
        p = get_panel(s["sr_pid"])
        try:
            await p.update_client(s["sr_cid"], s["sr_iid"], client)
            await event.respond(f"✅ Subtracted {days} day(s).")
        except RuntimeError as e:
            await event.respond(f"⚠️ Error: {e}")
        await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])
        return

    # ── Bulk operation input ─────────────────────────────────────────────
    if state == "bo_input":
        s["state"] = None
        bo_op = s.get("bo_op")
        if bo_op == "d":
            try:
                days = int(event.text.strip())
            except ValueError:
                s["state"] = "bo_input"
                await event.respond("⚠️ Invalid number. Enter days:")
                return
            if days <= 0:
                s["state"] = "bo_input"
                await event.respond("⚠️ Must be positive. Enter days:")
                return
            s["bo_value"] = days
            if s.get("bo_action") == "add":
                await event.respond(
                    "⏱ Start timer after first use?",
                    buttons=[
                        [Button.inline("✅ Yes", b"bosa:y"), Button.inline("❌ No", b"bosa:n")],
                        [Button.inline("◀️ Back", f"bo:{s['bo_pid']}".encode())],
                    ],
                )
            else:
                await _bulk_op_execute(event, event.sender_id)
        else:
            try:
                gb = float(event.text.strip())
            except ValueError:
                s["state"] = "bo_input"
                await event.respond("⚠️ Invalid number. Enter GB:")
                return
            if gb <= 0:
                s["state"] = "bo_input"
                await event.respond("⚠️ Must be positive. Enter GB:")
                return
            s["bo_value"] = gb
            await _bulk_op_execute(event, event.sender_id)
        return

    # ── Default: search ──────────────────────────────────────────────────
    email = event.text.strip()
    try:
        await _show_search_result(event, event.sender_id, email)
    except Exception:
        await event.respond(
            "⚠️ Error searching. Try again.",
            buttons=[[Button.inline("◀️ Back", b"m")]],
        )


async def _show_search_result(event, uid: int, email: str, panel_name: str | None = None):
    s = st(uid)

    if panel_name:
        # Search specific panel
        p = get_panel(panel_name)
        client, inbound, traffic = await p.find_client_by_email(email)
        found_panel = panel_name
    else:
        # Search all panels, take first match
        client, inbound, traffic, found_panel = None, None, None, None
        async def _search_one(pname, pc):
            c, ib, tr = await pc.find_client_by_email(email)
            return pname, c, ib, tr
        results = await asyncio.gather(
            *(_search_one(pn, pc) for pn, pc in panels.items())
        )
        for pn, c, ib, tr in results:
            if c is not None:
                client, inbound, traffic, found_panel = c, ib, tr, pn
                break

    if client is None:
        await reply(
            event,
            "❌ User not found!",
            buttons=[[Button.inline("◀️ Back", b"m")]],
        )
        return

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


# ── Toggle Enable / Disable ─────────────────────────────────────────────────

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
    await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])


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
    await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])


# ── Remove ───────────────────────────────────────────────────────────────────

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


# ── Back to Search Result ─────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"sr"))
@auth
async def cb_back_to_search(event):
    s = st(event.sender_id)
    s["state"] = None
    email = s.get("sr_email")
    if not email:
        return
    await _show_search_result(event, event.sender_id, email, panel_name=s.get("sr_pid"))


# ── Modify Traffic ───────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"mt"))
@auth
async def cb_modify_traffic(event):
    s = st(event.sender_id)
    client = s.get("sr_client")
    if not client:
        return
    traffic = s.get("sr_traffic") or {}
    total = client.get("totalGB", 0)
    up = traffic.get("up", 0)
    down = traffic.get("down", 0)
    lines = [
        "📊 **Modify Traffic**",
        "",
        f"📦 Limit: {format_bytes(total) if total > 0 else 'Unlimited'}",
        f"📊 Used: ↑ {format_bytes(up)}  ↓ {format_bytes(down)}",
    ]
    await reply(
        event,
        "\n".join(lines),
        buttons=[
            [
                Button.inline("📝 Edit Total", b"mte"),
                Button.inline("🔄 Reset", b"mtr"),
            ],
            [
                Button.inline("➕ Add More", b"mta"),
                Button.inline("➖ Less", b"mts"),
            ],
            [Button.inline("◀️ Back", b"sr")],
        ],
    )


@bot.on(events.CallbackQuery(data=b"mte"))
@auth
async def cb_mt_edit(event):
    s = st(event.sender_id)
    s["state"] = "mt_edit"
    await reply(
        event,
        "📝 Enter new traffic limit in GB (0 = unlimited):",
        buttons=[[Button.inline("◀️ Back", b"mt")]],
    )


@bot.on(events.CallbackQuery(data=b"mtr"))
@auth
async def cb_mt_reset(event):
    await reply(
        event,
        "⚠️ Reset used traffic to zero?",
        buttons=[
            [
                Button.inline("✅ Yes, Reset", b"mtrc"),
                Button.inline("❌ Cancel", b"mt"),
            ],
        ],
    )


@bot.on(events.CallbackQuery(data=b"mtrc"))
@auth
async def cb_mt_reset_confirm(event):
    s = st(event.sender_id)
    iid = s.get("sr_iid")
    email = s.get("sr_email")
    pid = s.get("sr_pid")
    if not iid or not email or not pid:
        return
    p = get_panel(pid)
    try:
        await p.reset_client_traffic(iid, email)
        await event.answer("✅ Traffic reset.")
    except RuntimeError as e:
        await event.answer(f"Error: {e}", alert=True)
    await _show_search_result(event, event.sender_id, email, panel_name=pid)


@bot.on(events.CallbackQuery(data=b"mta"))
@auth
async def cb_mt_add(event):
    s = st(event.sender_id)
    s["state"] = "mt_add"
    await reply(
        event,
        "➕ Enter GB to add:",
        buttons=[[Button.inline("◀️ Back", b"mt")]],
    )


@bot.on(events.CallbackQuery(data=b"mts"))
@auth
async def cb_mt_sub(event):
    s = st(event.sender_id)
    s["state"] = "mt_sub"
    await reply(
        event,
        "➖ Enter GB to subtract:",
        buttons=[[Button.inline("◀️ Back", b"mt")]],
    )


# ── Modify Days ──────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"md"))
@auth
async def cb_modify_days(event):
    s = st(event.sender_id)
    client = s.get("sr_client")
    if not client:
        return
    expiry = client.get("expiryTime", 0)
    lines = [
        "⏳ **Modify Duration**",
        "",
        f"⏳ Current: {format_expiry(expiry)}",
    ]
    await reply(
        event,
        "\n".join(lines),
        buttons=[
            [
                Button.inline("📝 Edit Total", b"mde"),
            ],
            [
                Button.inline("➕ Add More", b"mda"),
                Button.inline("➖ Less", b"mds"),
            ],
            [Button.inline("◀️ Back", b"sr")],
        ],
    )


@bot.on(events.CallbackQuery(data=b"mde"))
@auth
async def cb_md_edit(event):
    s = st(event.sender_id)
    s["state"] = "md_edit"
    await reply(
        event,
        "📝 Enter new duration in days (0 = unlimited):",
        buttons=[[Button.inline("◀️ Back", b"md")]],
    )


@bot.on(events.CallbackQuery(data=b"mda"))
@auth
async def cb_md_add(event):
    s = st(event.sender_id)
    s["state"] = "md_add"
    await reply(
        event,
        "➕ Enter days to add:",
        buttons=[[Button.inline("◀️ Back", b"md")]],
    )


@bot.on(events.CallbackQuery(data=b"mds"))
@auth
async def cb_md_sub(event):
    s = st(event.sender_id)
    s["state"] = "md_sub"
    await reply(
        event,
        "➖ Enter days to subtract:",
        buttons=[[Button.inline("◀️ Back", b"md")]],
    )


@bot.on(events.CallbackQuery(pattern=rb"^mdsa:([yn])$"))
@auth
async def cb_md_sau(event):
    s = st(event.sender_id)
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
        await event.answer("✅ Duration updated.")
    except RuntimeError as e:
        await event.answer(f"Error: {e}", alert=True)
    await _show_search_result(event, event.sender_id, s["sr_email"], panel_name=s["sr_pid"])


# ── Noop (section headers) ──────────────────────────────────────────────────

# ── Inbound List ─────────────────────────────────────────────────────────────

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
        label = f"{icon} {ib['remark']} [{active}/{total}]"
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


# ── Create Account Flow ─────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(pattern=rb"^ca:(.+):(\d+)$"))
@auth
async def cb_create_start(event):
    panel_name = event.pattern_match.group(1).decode()
    iid = int(event.pattern_match.group(2))
    s = st(event.sender_id)
    s["state"] = "cr_email"
    s["cr_iid"] = iid
    s["cr_pid"] = panel_name
    s["cr"] = {}
    await reply(
        event,
        "👤 Enter email for new account:",
        buttons=[
            [Button.inline("🎲 Random Email", b"re")],
            [Button.inline("◀️ Back", f"ib:{panel_name}:{iid}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(data=b"re"))
@auth
async def cb_random_email(event):
    s = st(event.sender_id)
    email = rand_email()
    s["cr"]["email"] = email
    s["state"] = "cr_traffic"
    await reply(
        event,
        f"👤 Email: `{email}`\n\n📦 Enter traffic in GB (0 = unlimited):",
        buttons=[[Button.inline("◀️ Back", f"ca:{s['cr_pid']}:{s['cr_iid']}".encode())]],
    )


@bot.on(events.CallbackQuery(pattern=rb"^sau:([yn])$"))
@auth
async def cb_start_after_use(event):
    s = st(event.sender_id)
    choice = event.pattern_match.group(1)
    s["cr"]["start_after_use"] = choice == b"y"
    s["state"] = None
    await _create_client(event, event.sender_id)


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
            event, "❌ Inbound not found.",
            buttons=[[Button.inline("◀️ Back", f"il:{panel_name}".encode())]],
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

    client_dict = _build_client_dict(email, total_bytes, expiry_time, protocol, stream, settings)

    try:
        await p.add_client(iid, client_dict)
    except RuntimeError as e:
        await reply(
            event,
            f"⚠️ Error creating account: {e}",
            buttons=[[Button.inline("◀️ Back", f"ib:{panel_name}:{iid}".encode())]],
        )
        return

    addr = server_addrs[panel_name]
    sub_url = sub_urls[panel_name]
    proxy_link = build_client_link(client_dict, inbound, addr)
    sub_id = client_dict.get("subId", "")
    sub_link = f"{sub_url}/{sub_id}" if sub_url and sub_id else None
    traffic_str = format_bytes(total_bytes) if total_bytes > 0 else "Unlimited"
    duration_str = format_expiry(expiry_time)

    lines = [
        "✅ **Account created!**",
        "",
        f"👤 Email: `{email}`",
        f"📦 Traffic: {traffic_str}",
        f"⏳ Duration: {duration_str}",
        f"🌐 Inbound: {inbound.get('remark', '?')}",
        f"🖥 Panel: {panel_name}",
    ]
    if proxy_link:
        lines += ["", f"`{proxy_link}`"]
    text = "\n".join(lines)
    btns = [[Button.inline("◀️ Back", b"m")]]
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
            title=f"Account: {email}",
        )
        await bot.send_file(event.chat_id, pdf, caption="📄 Account PDF")
    else:
        await reply(event, text, buttons=btns)


# ── PDF Export from Search Result ─────────────────────────────────────────────

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


# ── Bulk Create Flow ─────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(pattern=rb"^bk:(.+):(\d+)$"))
@auth
async def cb_bulk_start(event):
    panel_name = event.pattern_match.group(1).decode()
    iid = int(event.pattern_match.group(2))
    s = st(event.sender_id)
    s["bk_iid"] = iid
    s["bk_pid"] = panel_name
    s["bk"] = {}
    s["state"] = None
    await reply(
        event,
        "📦 **Bulk Create**\nChoose input method:",
        buttons=[
            [
                Button.inline("🔢 By Count", b"bkm:c"),
                Button.inline("📝 By Email List", b"bkm:e"),
            ],
            [Button.inline("◀️ Back", f"ib:{panel_name}:{iid}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(data=b"bkm:c"))
@auth
async def cb_bulk_by_count(event):
    s = st(event.sender_id)
    s["state"] = "bk_count"
    await reply(
        event,
        "🔢 Enter number of accounts to create (1-100):",
        buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
    )


@bot.on(events.CallbackQuery(data=b"bkm:e"))
@auth
async def cb_bulk_by_emails(event):
    s = st(event.sender_id)
    s["state"] = "bk_emails"
    await reply(
        event,
        "📝 Send emails, one per line (max 100):",
        buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
    )


@bot.on(events.CallbackQuery(pattern=rb"^bkn:(.+)$"))
@auth
async def cb_bulk_naming(event):
    method = event.pattern_match.group(1).decode()
    s = st(event.sender_id)
    s["bk"]["method"] = method
    if method == "r":
        # Random — generate immediately
        s["bk"]["emails"] = generate_bulk_emails("r", s["bk"]["count"])
        s["state"] = "bk_traffic"
        sample = ", ".join(f"`{e}`" for e in s["bk"]["emails"][:3])
        await reply(
            event,
            f"✅ Preview: {sample}{'...' if s['bk']['count'] > 3 else ''}\n\n"
            "📦 Enter traffic in GB (0 = unlimited):",
            buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
        )
    else:
        # All other methods need a prefix first
        s["state"] = "bk_prefix"
        await reply(
            event,
            "🏷 Enter prefix:",
            buttons=[[Button.inline("◀️ Back", f"ib:{s['bk_pid']}:{s['bk_iid']}".encode())]],
        )


@bot.on(events.CallbackQuery(pattern=rb"^bksa:([yn])$"))
@auth
async def cb_bulk_sau(event):
    s = st(event.sender_id)
    choice = event.pattern_match.group(1)
    s["bk"]["start_after_use"] = choice == b"y"
    s["state"] = None
    await _bulk_create_clients(event, event.sender_id)


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
            event, "❌ Inbound not found.",
            buttons=[[Button.inline("◀️ Back", f"il:{panel_name}".encode())]],
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

    traffic_str = format_bytes(total_bytes) if total_bytes > 0 else "Unlimited"
    duration_str = format_expiry(expiry_time)

    addr = server_addrs[panel_name]
    sub_url = sub_urls[panel_name]

    # Progress message
    progress_msg = await bot.send_message(
        event.chat_id, f"⏳ Creating {len(emails)} accounts..."
    )

    created = []
    failed = []

    for i, email in enumerate(emails):
        client_dict = _build_client_dict(email, total_bytes, expiry_time, protocol, stream, settings)
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
                    f"⏳ Creating accounts... {i + 1}/{len(emails)}"
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
        f"📦 **Bulk Create Complete**",
        "",
        f"✅ Created: {len(created)}",
        f"❌ Failed: {len(failed)}",
        "",
        f"📦 Traffic: {traffic_str}",
        f"⏳ Duration: {duration_str}",
        f"🌐 Inbound: {remark}",
        f"🖥 Panel: {panel_name}",
    ]
    if failed:
        lines += ["", "**Errors (first 5):**"]
        for email, err in failed[:5]:
            lines.append(f"  • `{email}`: {err}")
    text = "\n".join(lines)
    await bot.send_message(
        event.chat_id, text, buttons=[[Button.inline("◀️ Back", b"m")]],
        parse_mode="md",
    )

    # Generate and send PDF for created accounts
    if created:
        pdf = generate_account_pdf(created, f"Bulk Accounts - {panel_name} / {remark}")
        await bot.send_file(event.chat_id, pdf, caption="📄 Bulk accounts PDF")

    clear(uid)


# ── Bulk Operation Flow ──────────────────────────────────────────────────

@bot.on(events.CallbackQuery(pattern=rb"^bo:(.+)$"))
@auth
async def cb_bulk_op_start(event):
    panel_name = event.pattern_match.group(1).decode()
    s = st(event.sender_id)
    s["bo_pid"] = panel_name
    await reply(
        event,
        "⚡ **Bulk Operation**\nFilter accounts:",
        buttons=[
            [Button.inline("✅ Only Enabled", b"bof:en")],
            [Button.inline("🔴 Only Disabled", b"bof:dis")],
            [Button.inline("📋 All Accounts", b"bof:all")],
            [Button.inline("◀️ Back", f"il:{panel_name}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(pattern=rb"^bof:(.+)$"))
@auth
async def cb_bulk_op_filter(event):
    filt = event.pattern_match.group(1).decode()
    uid = event.sender_id
    s = st(uid)
    s["bo_filter"] = filt
    panel_name = s["bo_pid"]
    p = get_panel(panel_name)

    inbounds = await p.list_inbounds()
    collected = []
    for ib in inbounds:
        protocol = ib["protocol"]
        settings = json.loads(ib.get("settings", "{}"))
        for client in settings.get("clients", []):
            enabled = client.get("enable", True)
            if filt == "en" and not enabled:
                continue
            if filt == "dis" and enabled:
                continue
            client_id = p.get_client_id(client, protocol)
            collected.append((client, ib["id"], client_id, protocol))

    s["bo_clients"] = collected
    filter_label = {"en": "Enabled", "dis": "Disabled", "all": "All"}[filt]
    await reply(
        event,
        f"⚡ **Bulk Operation**\n"
        f"Filter: {filter_label}\n"
        f"Found **{len(collected)}** account(s)\n\n"
        "Choose operation:",
        buttons=[
            [Button.inline("⏳ Days", b"bot:d"), Button.inline("📊 Traffic", b"bot:t")],
            [Button.inline("◀️ Back", f"bo:{panel_name}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(pattern=rb"^bot:([dt])$"))
@auth
async def cb_bulk_op_type(event):
    op = event.pattern_match.group(1).decode()
    s = st(event.sender_id)
    s["bo_op"] = op
    label = "Days" if op == "d" else "Traffic"
    await reply(
        event,
        f"⚡ **Bulk Operation — {label}**\nChoose action:",
        buttons=[
            [Button.inline("➕ Add", b"boa:add"), Button.inline("➖ Subtract", b"boa:sub")],
            [Button.inline("◀️ Back", f"bo:{s['bo_pid']}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(pattern=rb"^boa:(.+)$"))
@auth
async def cb_bulk_op_action(event):
    action = event.pattern_match.group(1).decode()
    s = st(event.sender_id)
    s["bo_action"] = action
    s["state"] = "bo_input"
    op = s["bo_op"]
    if op == "d":
        verb = "add" if action == "add" else "subtract"
        prompt = f"⚡ Enter number of days to {verb}:"
    else:
        verb = "add" if action == "add" else "subtract"
        prompt = f"⚡ Enter GB to {verb}:"
    await reply(
        event,
        prompt,
        buttons=[[Button.inline("◀️ Back", f"bo:{s['bo_pid']}".encode())]],
    )


@bot.on(events.CallbackQuery(pattern=rb"^bosa:([yn])$"))
@auth
async def cb_bulk_op_sau(event):
    choice = event.pattern_match.group(1).decode()
    s = st(event.sender_id)
    s["bo_sau"] = choice == "y"
    await _bulk_op_execute(event, event.sender_id)


async def _bulk_op_execute(event, uid: int):
    s = st(uid)
    clients = s.get("bo_clients", [])
    op = s["bo_op"]
    action = s["bo_action"]
    value = s["bo_value"]
    panel_name = s["bo_pid"]
    p = get_panel(panel_name)

    if not clients:
        await reply(
            event,
            "⚠️ No accounts to process.",
            buttons=[[Button.inline("◀️ Back", b"m")]],
        )
        clear(uid)
        return

    progress_msg = await bot.send_message(
        event.chat_id,
        f"⏳ Processing 0/{len(clients)}...",
    )

    success = 0
    failed = 0
    skipped = 0
    done_count = 0
    total = len(clients)
    counters_lock = asyncio.Lock()

    def apply_change(client):
        """Apply the operation to a client dict copy. Returns (updated, skip)."""
        updated = dict(client)
        if op == "d":
            cur = updated.get("expiryTime", 0)
            add_ms = value * 86_400_000
            if action == "add":
                if cur == 0:
                    return updated, True
                sau = s.get("bo_sau", False)
                if sau:
                    if cur < 0:
                        updated["expiryTime"] = cur - add_ms
                    else:
                        updated["expiryTime"] = -add_ms
                else:
                    if cur < 0:
                        now_ms = int(time.time() * 1000)
                        updated["expiryTime"] = now_ms + abs(cur) + add_ms
                    else:
                        updated["expiryTime"] = cur + add_ms
            else:  # subtract
                if cur == 0:
                    return updated, True
                if cur < 0:
                    new_val = cur + add_ms
                    if new_val >= 0:
                        new_val = -86_400_000
                    updated["expiryTime"] = new_val
                else:
                    now_ms = int(time.time() * 1000)
                    new_val = cur - add_ms
                    if new_val <= now_ms:
                        new_val = now_ms + 86_400_000
                    updated["expiryTime"] = new_val
        else:  # traffic
            cur = updated.get("totalGB", 0)
            delta_bytes = int(value * 1024**3)
            if action == "add":
                if cur == 0:
                    return updated, True
                updated["totalGB"] = cur + delta_bytes
            else:  # subtract
                if cur == 0:
                    return updated, True
                new_val = cur - delta_bytes
                if new_val <= 0:
                    new_val = 1
                updated["totalGB"] = new_val
        return updated, False

    # Group clients by inbound_id so updates within the same inbound
    # run sequentially (3x-ui stores all clients as a JSON array inside
    # the inbound settings — concurrent writes to the same inbound
    # cause a read-modify-write race that silently drops changes).
    by_inbound: dict[int, list] = {}
    for client, iid, cid, proto in clients:
        by_inbound.setdefault(iid, []).append((client, iid, cid, proto))

    async def process_inbound_group(group):
        nonlocal success, failed, skipped, done_count
        for client, inbound_id, client_id, protocol in group:
            updated, skip = apply_change(client)
            if skip:
                async with counters_lock:
                    skipped += 1
                    done_count += 1
                    current = done_count
            else:
                try:
                    await p.update_client(client_id, inbound_id, updated)
                    async with counters_lock:
                        success += 1
                        done_count += 1
                        current = done_count
                except Exception:
                    async with counters_lock:
                        failed += 1
                        done_count += 1
                        current = done_count
            if current % 10 == 0 or current == total:
                try:
                    await progress_msg.edit(f"⏳ Processing {current}/{total}...")
                except Exception:
                    pass

    await asyncio.gather(*[
        process_inbound_group(group) for group in by_inbound.values()
    ])

    try:
        await progress_msg.delete()
    except Exception:
        pass

    op_label = "Days" if op == "d" else "Traffic"
    action_label = "Added" if action == "add" else "Subtracted"
    if op == "d":
        value_str = f"{value} day(s)"
    else:
        value_str = f"{value} GB"

    lines = [
        f"⚡ **Bulk Operation Complete**",
        "",
        f"Operation: {action_label} {value_str}",
        f"Panel: {panel_name}",
        "",
        f"✅ Success: {success}",
        f"❌ Failed: {failed}",
        f"⏭ Skipped (unlimited): {skipped}",
    ]
    await bot.send_message(
        event.chat_id,
        "\n".join(lines),
        buttons=[[Button.inline("◀️ Back", b"m")]],
        parse_mode="md",
    )
    clear(uid)


# ── Run ──────────────────────────────────────────────────────────────────────

async def main():
    await bot.start(bot_token=bot_cfg["token"])
    await bot(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(),
        lang_code="",
        commands=[BotCommand(command="start", description="Open main menu")],
    ))
    print("Bot is running...")
    await bot.run_until_disconnected()


asyncio.run(main())
