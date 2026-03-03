from telethon import events, Button

from config import st, has_perm
from helpers import auth
from handlers.search import show_search_result
from handlers.modify import handle_modify_traffic_input, handle_modify_days_input
from handlers.create import handle_create_input, handle_bulk_create_input
from handlers.bulk_ops import handle_bulk_op_input


def register(bot):
    @bot.on(events.NewMessage)
    @auth
    async def on_message(event):
        if not event.text or event.text.startswith("/"):
            return
        s = st(event.sender_id)
        state = s.get("state")

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
        if not has_perm(event.sender_id, "search"):
            return
        email = event.text.strip()
        searching_msg = await event.respond("🔍 Searching...")
        try:
            await show_search_result(event, event.sender_id, email)
        except Exception:
            await event.respond(
                "⚠️ Error searching. Try again.",
                buttons=[[Button.inline("◀️ Back", b"m")]],
            )
        finally:
            try:
                await searching_msg.delete()
            except Exception:
                pass
