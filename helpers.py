import io
import random
import secrets
import string
import time
import uuid
from base64 import b64encode

import qrcode
import re

from telethon import events, Button
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.errors import UserNotParticipantError

from config import bot, panels, user_perms, has_perm, is_owner, get_force_join, visible_panels
from db import get_user_lang, get_db_admins, get_user_profile, get_profile_updated_at, upsert_user_profile
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


_INVITE_HASH_RE = re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)(.+)")


def _extract_invite_hash(entry: str) -> str | None:
    """Extract invite hash from a private channel link, or None if public."""
    m = _INVITE_HASH_RE.match(entry)
    return m.group(1) if m else None


async def _check_force_join(event, uid: int, silent: bool = False) -> bool:
    channels = get_force_join()
    if not channels:
        return True
    if is_owner(uid) or uid in get_db_admins():
        return True
    now = time.time()
    missing = []
    for ch in channels:
        key = (uid, ch)
        if _fj_cache.get(key, 0) > now:
            continue
        invite_hash = _extract_invite_hash(ch)
        if invite_hash:
            # Private channel — resolve via invite, then check participant
            try:
                invite_info = await bot(CheckChatInviteRequest(invite_hash))
                # ChatInviteAlready means the bot is a member; get channel from it
                channel = getattr(invite_info, "chat", None)
                if channel:
                    await bot(GetParticipantRequest(channel, uid))
                    _fj_cache[key] = now + _FJ_TTL
                else:
                    missing.append(ch)
            except UserNotParticipantError:
                missing.append(ch)
            except Exception:
                missing.append(ch)
        else:
            # Public channel
            try:
                await bot(GetParticipantRequest(ch, uid))
                _fj_cache[key] = now + _FJ_TTL
            except UserNotParticipantError:
                missing.append(ch)
            except Exception:
                missing.append(ch)
    if missing:
        if not silent:
            btns = []
            for ch in missing:
                if _extract_invite_hash(ch):
                    # Private — use stored invite link as join URL
                    url = ch if ch.startswith("http") else f"https://{ch}"
                    btns.append([Button.url(t("btn_join_channel", uid, channel="Private"), url)])
                else:
                    btns.append([Button.url(t("btn_join_channel", uid, channel=ch), f"https://t.me/{ch.lstrip('@')}")])
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


# ── Profile ──────────────────────────────────────────────────────────────

_PROFILE_TTL = 86400  # 24 hours


async def _maybe_update_profile(event, uid: int):
    """Capture non-owner Telegram profile info with a 24h TTL."""
    if is_owner(uid):
        return
    try:
        last = get_profile_updated_at(uid)
        if time.time() - last < _PROFILE_TTL:
            return
        sender = await event.get_sender()
        if not sender:
            return
        first_name = getattr(sender, "first_name", "") or ""
        last_name = getattr(sender, "last_name", "") or ""
        username = getattr(sender, "username", "") or ""
        phone = getattr(sender, "phone", "") or ""
        bio = ""
        if last == 0.0:
            # First capture — also fetch bio
            try:
                full = await bot(GetFullUserRequest(uid))
                bio = getattr(full.full_user, "about", "") or ""
            except Exception:
                pass
        upsert_user_profile(uid, first_name, last_name, username, phone, bio)
    except Exception:
        pass


def get_display_name(uid: int) -> str:
    """Return 'FirstName LastName' from cached profile, or str(uid) fallback."""
    prof = get_user_profile(uid)
    if not prof:
        return str(uid)
    name = prof["first_name"]
    if prof["last_name"]:
        name += " " + prof["last_name"]
    return name if name.strip() else str(uid)


# ── Auth ─────────────────────────────────────────────────────────────────────

def auth(func_or_perm=None, *extra_perms):
    """
    @auth                        — any authorized user
    @auth("create")              — requires 'create' permission
    @auth("search", "search_simple") — requires any of the listed perms
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
            await _maybe_update_profile(event, uid)
            try:
                return await func(event)
            except RuntimeError as e:
                await reply(event, t("error_msg", uid, error=e),
                            buttons=[[Button.inline(t("btn_back", uid), b"m")]])
        return wrapper

    # @auth("perm") or @auth("perm1", "perm2") with parentheses
    perms = [func_or_perm] + list(extra_perms)
    def decorator(func):
        async def wrapper(event):
            uid = event.sender_id
            if not any(has_perm(uid, p) for p in perms):
                return
            if get_user_lang(uid) is None:
                await _show_lang_picker(event, uid)
                return
            if not await _check_force_join(event, uid):
                return
            await _maybe_update_profile(event, uid)
            try:
                return await func(event)
            except RuntimeError as e:
                await reply(event, t("error_msg", uid, error=e),
                            buttons=[[Button.inline(t("btn_back", uid), b"m")]])
        return wrapper
    return decorator


# ── Safe answer ──────────────────────────────────────────────────────────────

async def answer(event, *args, **kwargs):
    """Wrapper around event.answer() that silently ignores expired query IDs."""
    try:
        await event.answer(*args, **kwargs)
    except Exception:
        pass


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
    if has_perm(uid, "search") or has_perm(uid, "search_simple"):
        btns.append([Button.inline(t("btn_search", uid), b"s")])
    perms = user_perms(uid)
    if perms & {"create", "bulk"}:
        for name in visible_panels(uid):
            btns.append([Button.inline(t("btn_panel", uid, name=name), f"pm:{name}".encode())])
    if has_perm(uid, "bulk"):
        btns.append([Button.inline(t("btn_bulk_ops_main", uid), b"bom_start")])
    if "owner" in perms:
        btns.append([Button.inline(t("btn_owner_panel", uid), b"op")])
    btns.append([Button.inline(t("btn_language", uid), b"cl")])
    return btns


def search_result_buttons(uid: int, status: str):
    """Build search result action buttons filtered by user permissions.

    status: "active", "depleted", or "disabled"
    """
    btns = []
    row1 = []
    if has_perm(uid, "toggle"):
        if status == "disabled":
            row1.append(Button.inline(t("btn_enable", uid), b"en"))
        else:
            row1.append(Button.inline(t("btn_disable", uid), b"dis"))
    if has_perm(uid, "remove"):
        row1.append(Button.inline(t("btn_remove", uid), b"rm"))
    if row1:
        btns.append(row1)
    row2 = []
    if has_perm(uid, "modify"):
        row2.append(Button.inline(t("btn_traffic", uid), b"mt"))
        row2.append(Button.inline(t("btn_days", uid), b"md"))
        row2.append(Button.inline(t("btn_renew", uid), b"rn"))
    if row2:
        btns.append(row2)
    row3 = []
    if has_perm(uid, "pdf"):
        row3.append(Button.inline(t("btn_pdf", uid), b"pdf"))
    row3.append(Button.inline(t("btn_back", uid), b"m"))
    btns.append(row3)
    return btns


def format_client_line(client: dict, traffic: dict | None, uid: int) -> str:
    """Format one client as a text line for the client list."""
    email = client.get("email", "?")
    enabled = client.get("enable", True)

    # Traffic
    limit = client.get("totalGB", 0)
    used = 0
    if traffic:
        used = traffic.get("up", 0) + traffic.get("down", 0)
    if limit > 0:
        traffic_str = f"{format_bytes(used)}/{format_bytes(limit)}"
    else:
        traffic_str = "\u221e"

    # Duration
    exp = client.get("expiryTime", 0)
    if exp == 0:
        dur_str = "\u221e"
    else:
        now_ms = int(time.time() * 1000)
        if exp < 0:
            dur = abs(exp)
        else:
            dur = exp - now_ms
        if dur <= 0:
            dur_str = t("expired", uid)
        else:
            days = dur // 86_400_000
            dur_str = f"{days}d"

    # 3-state icon: ✅ active, ⛔ depleted, 🔴 disabled
    if not enabled:
        icon = "\U0001f534"  # 🔴
    else:
        now_ms = int(time.time() * 1000)
        expired = exp > 0 and exp < now_ms
        traffic_exceeded = limit > 0 and used >= limit
        icon = "\u26d4" if (expired or traffic_exceeded) else "\u2705"  # ⛔ or ✅

    return f"`{email}` {icon} | {traffic_str} | {dur_str}"


def main_menu_text(uid: int) -> str:
    if has_perm(uid, "search") or has_perm(uid, "search_simple"):
        return t("main_menu", uid)
    return t("main_menu_no_search", uid)
