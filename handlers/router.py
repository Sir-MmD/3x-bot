import json
import re
import time
from base64 import b64decode
from urllib.parse import unquote

from telethon import events, Button

from config import st, has_perm, is_owner
from db import get_db_admins, get_setting
from helpers import auth
from i18n import t
from handlers.search import show_search_result
from handlers.modify import handle_modify_traffic_input, handle_modify_days_input, handle_renew_input
from handlers.create import handle_create_input, handle_bulk_create_input
from handlers.bulk_ops import handle_bulk_op_input, handle_bulk_op_manual
from handlers.owner import handle_owner_input, handle_owner_restore

# ── Rate limit tracker ────────────────────────────────────────────────────────
_search_times: dict[int, list[float]] = {}


def _check_rate_limit(uid: int) -> int:
    """Check if a public user is rate-limited. Returns seconds to wait, or 0."""
    if is_owner(uid) or uid in get_db_admins():
        return 0
    raw = get_setting("search_rate_limit")
    if not raw:
        return 0
    parts = raw.split(",")
    if len(parts) != 2:
        return 0
    try:
        count = int(parts[0])
        window = int(parts[1])
    except ValueError:
        return 0
    if count <= 0 or window <= 0:
        return 0
    now = time.time()
    times = _search_times.get(uid, [])
    # Prune old entries
    times = [t for t in times if now - t < window]
    _search_times[uid] = times
    if len(times) >= count:
        wait = int(window - (now - times[0])) + 1
        return max(wait, 1)
    return 0


def _record_search(uid: int):
    """Record a search timestamp for rate limiting."""
    _search_times.setdefault(uid, []).append(time.time())

_PROXY_LINK_RE = re.compile(r"^(vless|vmess|trojan|ss)://", re.IGNORECASE)


def _extract_email_from_link(text: str) -> str | None:
    """Extract the email from a proxy link's tag/remark.

    Handles raw proxy links and base64-encoded links.
    Tag format is '{remark}-{email}', so we take everything after the last '-'.
    For vmess (base64 JSON), email is extracted from the 'ps' field.
    """
    text = text.strip()

    # If not a proxy link, try base64-decoding first
    if not _PROXY_LINK_RE.match(text):
        try:
            padded = text + "=" * (4 - len(text) % 4) if len(text) % 4 else text
            decoded = b64decode(padded).decode()
            # Could be multiple lines; find the first proxy link
            for line in decoded.splitlines():
                line = line.strip()
                if _PROXY_LINK_RE.match(line):
                    text = line
                    break
            else:
                return None
        except Exception:
            return None

    scheme = text.split("://", 1)[0].lower()

    if scheme == "vmess":
        b64_part = text.split("://", 1)[1].split("#")[0].strip()
        padding = 4 - len(b64_part) % 4
        if padding != 4:
            b64_part += "=" * padding
        try:
            cfg = json.loads(b64decode(b64_part).decode())
            tag = cfg.get("ps", "")
        except Exception:
            return None
    else:
        # vless, trojan, ss — email is in the URL fragment
        fragment = text.split("#", 1)[1] if "#" in text else ""
        tag = unquote(fragment)

    if not tag:
        return None
    # Format: {email}@{remark} (3x-ui panel) or {remark}-{email} (bot-generated)
    if "@" in tag:
        return tag.split("@", 1)[0]
    if "-" in tag:
        return tag.rsplit("-", 1)[-1]
    return tag


def register(bot):
    @bot.on(events.NewMessage(func=lambda e: e.document))
    @auth
    async def on_document(event):
        uid = event.sender_id
        s = st(uid)
        if s.get("state") == "op_rs":
            await handle_owner_restore(event)

    @bot.on(events.NewMessage(func=lambda e: e.text and not e.text.startswith("/")))
    @auth
    async def on_message(event):
        uid = event.sender_id
        s = st(uid)
        state = s.get("state")

        # ── Owner panel states ────────────────────────────────────────────
        if state and state.startswith("op_"):
            if await handle_owner_input(event):
                return

        # ── Create flow states ───────────────────────────────────────────
        if state and state.startswith("cr_"):
            if await handle_create_input(event):
                return

        # ── Bulk create flow states ──────────────────────────────────────
        if state and state.startswith("bk_"):
            if await handle_bulk_create_input(event):
                return

        # ── Modify traffic states ────────────────────────────────────────
        if state and state.startswith("mt_"):
            if await handle_modify_traffic_input(event):
                return

        # ── Modify days states ───────────────────────────────────────────
        if state and state.startswith("md_"):
            if await handle_modify_days_input(event):
                return

        # ── Renew plan states ─────────────────────────────────────────
        if state and state.startswith("rn_"):
            if await handle_renew_input(event):
                return

        # ── Bulk operation input ─────────────────────────────────────────
        if state == "bo_manual":
            if await handle_bulk_op_manual(event):
                return
        if state == "bo_input":
            if await handle_bulk_op_input(event):
                return

        # ── Default: search ──────────────────────────────────────────────
        if not (has_perm(uid, "search") or has_perm(uid, "search_simple")):
            return
        # Rate limit for public users
        wait = _check_rate_limit(uid)
        if wait > 0:
            msg = await event.respond(t("rate_limit_wait", uid, seconds=wait))
            try:
                import asyncio
                await asyncio.sleep(5)
                await msg.delete()
            except Exception:
                pass
            return
        _record_search(uid)
        email = _extract_email_from_link(event.text) or event.text.strip()
        searching_msg = await event.respond(t("searching", uid))
        try:
            await show_search_result(event, uid, email)
        except Exception:
            await event.respond(
                t("search_error", uid),
                buttons=[[Button.inline(t("btn_back", uid), b"m")]],
            )
        finally:
            try:
                await searching_msg.delete()
            except Exception:
                pass
