import io
import random
import secrets
import string
import time
import uuid
from base64 import b64encode

import qrcode
from telethon import events, Button

from config import ALLOWED, bot, panels


# ── Formatting ───────────────────────────────────────────────────────────────

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


# ── Email generation ─────────────────────────────────────────────────────────

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


# ── QR ───────────────────────────────────────────────────────────────────────

def make_qr(data: str) -> io.BytesIO:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "qr.png"
    return buf


# ── Auth ─────────────────────────────────────────────────────────────────────

def auth(func):
    """Decorator that silently ignores non-allowed users."""
    async def wrapper(event):
        if event.sender_id not in ALLOWED:
            return
        return await func(event)
    return wrapper


# ── Reply ────────────────────────────────────────────────────────────────────

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


# ── Client dict builder ─────────────────────────────────────────────────────

def build_client_dict(
    email: str, total_bytes: int, expiry_time: int,
    protocol: str, stream: dict, settings: dict,
) -> dict:
    """Build a client dict with protocol-specific fields."""
    _alnum = string.digits + string.ascii_lowercase + string.ascii_uppercase
    _lower_num = string.digits + string.ascii_lowercase

    client_uuid = str(uuid.uuid4())
    sub_id = "".join(secrets.choice(_lower_num) for _ in range(16))

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
        client_dict["password"] = "".join(secrets.choice(_alnum) for _ in range(10))
    elif protocol == "shadowsocks":
        method = settings.get("method", "")
        key_len = 16 if method == "2022-blake3-aes-128-gcm" else 32
        client_dict["password"] = b64encode(secrets.token_bytes(key_len)).decode()

    return client_dict


# ── Main menu ────────────────────────────────────────────────────────────────

def main_menu_buttons():
    btns = [[Button.inline("🔍 Search User", b"s")]]
    for name in panels:
        btns.append([Button.inline(f"📋 Inbound List ({name})", f"il:{name}".encode())])
    return btns


MAIN_TEXT = "🏠 **Main Menu**\nType an email to search, or pick an option:"
