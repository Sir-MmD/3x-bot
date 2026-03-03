import asyncio
import os
import sys

from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

import config
from config import bot, cfg, load_db_panels
from db import init_db
from i18n import t
from handlers import menu, search, modify, create, inbounds, bulk_ops, owner, router

for mod in (menu, search, modify, create, inbounds, bulk_ops, owner, router):
    mod.register(bot)


async def main():
    init_db()
    load_db_panels()

    # Verify session matches current token; delete stale session and re-exec if not
    expected_id = int(cfg["token"].split(":")[0])
    await bot.start(bot_token=cfg["token"])
    me = await bot.get_me()
    if me.id != expected_id:
        print(f"[WARN] Session belongs to a different bot ({me.id}), expected {expected_id}. Resetting session...")
        await bot.disconnect()
        session_path = config.DATA_DIR / "bot.session"
        session_path.unlink(missing_ok=True)
        (config.DATA_DIR / "bot.session-journal").unlink(missing_ok=True)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    await bot(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(),
        lang_code="",
        commands=[BotCommand(command="start", description="Open main menu")],
    ))

    # Notify user who requested the restart
    notify_uid = os.environ.pop("_3XBOT_RESTART_UID", None)
    if notify_uid:
        try:
            uid = int(notify_uid)
            await bot.send_message(uid, t("restart_success", uid))
        except Exception:
            pass

    print("Bot is running...")
    await bot.run_until_disconnected()


asyncio.run(main())

if config.restart_requested:
    print("Restarting...")
    os.environ["_3XBOT_RESTART_UID"] = str(config.restart_requested)
    os.execv(sys.executable, [sys.executable] + sys.argv)
