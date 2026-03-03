from telethon import events

from config import clear
from helpers import auth, reply, main_menu_buttons, MAIN_TEXT


def register(bot):
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
