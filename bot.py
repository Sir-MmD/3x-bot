import asyncio

from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

from config import bot, bot_cfg
from db import init_db
from handlers import menu, search, modify, create, inbounds, bulk_ops, router

for mod in (menu, search, modify, create, inbounds, bulk_ops, router):
    mod.register(bot)


async def main():
    init_db()
    await bot.start(bot_token=bot_cfg["token"])
    await bot(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(),
        lang_code="",
        commands=[BotCommand(command="start", description="Open main menu")],
    ))
    print("Bot is running...")
    await bot.run_until_disconnected()


asyncio.run(main())
