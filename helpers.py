import io
import random
import secrets
import string
import time
import uuid
from base64 import b64encode

import qrcode
from telethon import events, Button
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.errors import UserNotParticipantError

from config import bot, panels, admins, force_join, user_perms, has_perm
from db import get_user_lang
from i18n import t, LANGUAGES


# ── Formatting ───────────────────────────────────────────────────────────────

def format_bytes(b: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def format_expiry(ms: int, uid: int = 0) -> str:
    if ms == 0:
        return t("unlimited", uid) if uid else "Unlimited"
    now_ms = int(time.time() * 1000)
    if ms < 0:
        dur = abs(ms)
    else:
        dur = ms - now_ms
    if dur <= 0:
        return t("expired", uid) if uid else "Expired"
    days = dur // 86_400_000
    hours = (dur % 86_400_000) // 3_600_000
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    text = " ".join(parts) or (t("less_than_1h", uid) if uid else "< 1h")
    if ms > 0:
        exp_date = time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        text += f" ({exp_date})"
    else:
        after = t("after_first_use", uid) if uid else "after first use"
        text += f" ({after})"
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


# ── Force-join cache ──────────────────────────────────────────────────────────

_fj_cache: dict[tuple[int, str], float] = {}  # (uid, channel) → expiry timestamp
_FJ_TTL = 300  # 5 minutes


async def _check_force_join(event, uid: int, silent: bool = False) -> bool:
    if not force_join:
        return True
    if uid in admins:
        return True
    now = time.time()
    missing = []
    for ch in force_join:
        key = (uid, ch)
        if _fj_cache.get(key, 0) > now:
            continue
        try:
            await bot(GetParticipantRequest(ch, uid))
            _fj_cache[key] = now + _FJ_TTL
        except UserNotParticipantError:
            missing.append(ch)
        except Exception:
            missing.append(ch)
    if missing:
        if not silent:
            btns = [[Button.url(t("btn_join_channel", uid, channel=ch), f"https://t.me/{ch.lstrip('@')}")] for ch in missing]
            btns.append([Button.inline(t("btn_ive_joined", uid), b"fj")])
            await reply(event, t("force_join_msg", uid), buttons=btns)
        return False
    return True


# ── Language picker ──────────────────────────────────────────────────────────

def _lang_picker_buttons():
    return [[Button.inline(label, f"lang:{code}".encode())] for code, label in LANGUAGES.items()]


async def _show_lang_picker(event, uid: int):
    text = t("lang_select", uid)
    await reply(event, text, buttons=_lang_picker_buttons())


# ── Auth ─────────────────────────────────────────────────────────────────────

def auth(func_or_perm=None):
    """
    @auth           — any authorized user
    @auth("create") — requires 'create' permission
    """
    if callable(func_or_perm):
        # @auth without parentheses
        func = func_or_perm
        async def wrapper(event):
            uid = event.sender_id
            if not user_perms(uid):
                return
            if get_user_lang(uid) is None:
                await _show_lang_picker(event, uid)
                return
            if not await _check_force_join(event, uid):
                return
            return await func(event)
        return wrapper

    # @auth("perm") with parentheses
    perm = func_or_perm
    def decorator(func):
        async def wrapper(event):
            uid = event.sender_id
            if not has_perm(uid, perm):
                return
            if get_user_lang(uid) is None:
                await _show_lang_picker(event, uid)
                return
            if not await _check_force_join(event, uid):
                return
            return await func(event)
        return wrapper
    return decorator


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

def main_menu_buttons(uid: int):
    btns = []
    if has_perm(uid, "search"):
        btns.append([Button.inline(t("btn_search", uid), b"s")])
    perms = user_perms(uid)
    if perms & {"create", "bulk"}:
        for name in panels:
            btns.append([Button.inline(t("btn_inbound_list", uid, name=name), f"il:{name}".encode())])
    btns.append([Button.inline(t("btn_language", uid), b"cl")])
    return btns


def search_result_buttons(uid: int, enabled: bool):
    """Build search result action buttons filtered by user permissions."""
    btns = []
    row1 = []
    if has_perm(uid, "toggle"):
        if enabled:
            row1.append(Button.inline(t("btn_disable", uid), b"dis"))
        else:
            row1.append(Button.inline(t("btn_enable", uid), b"en"))
    if has_perm(uid, "remove"):
        row1.append(Button.inline(t("btn_remove", uid), b"rm"))
    if row1:
        btns.append(row1)
    row2 = []
    if has_perm(uid, "modify"):
        row2.append(Button.inline(t("btn_traffic", uid), b"mt"))
        row2.append(Button.inline(t("btn_days", uid), b"md"))
    if row2:
        btns.append(row2)
    row3 = []
    if has_perm(uid, "pdf"):
        row3.append(Button.inline(t("btn_pdf", uid), b"pdf"))
    row3.append(Button.inline(t("btn_back", uid), b"m"))
    btns.append(row3)
    return btns


def main_menu_text(uid: int) -> str:
    if has_perm(uid, "search"):
        return t("main_menu", uid)
    return t("main_menu_no_search", uid)
