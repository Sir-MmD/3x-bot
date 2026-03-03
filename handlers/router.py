from telethon import events, Button

from config import st, has_perm
from helpers import auth
from i18n import t
from handlers.search import show_search_result
from handlers.modify import handle_modify_traffic_input, handle_modify_days_input
from handlers.create import handle_create_input, handle_bulk_create_input
from handlers.bulk_ops import handle_bulk_op_input
from handlers.owner import handle_owner_input, handle_owner_restore


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

        # ── Bulk operation input ─────────────────────────────────────────
        if state == "bo_input":
            if await handle_bulk_op_input(event):
                return

        # ── Default: search ──────────────────────────────────────────────
        if not has_perm(uid, "search"):
            return
        email = event.text.strip()
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
