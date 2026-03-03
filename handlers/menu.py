from telethon import events

from config import clear
from helpers import auth, reply, main_menu_buttons, _check_force_join, MAIN_TEXT


def register(bot):
    @bot.on(events.NewMessage(pattern="/start"))
    @auth
    async def cmd_start(event):
        clear(event.sender_id)
        await event.respond(MAIN_TEXT, buttons=main_menu_buttons(event.sender_id), parse_mode="md")

    @bot.on(events.CallbackQuery(data=b"m"))
    @auth
    async def cb_main(event):
        clear(event.sender_id)
        await reply(event, MAIN_TEXT, buttons=main_menu_buttons(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"fj"))
    @auth
    async def cb_force_join_check(event):
        uid = event.sender_id
        if not await _check_force_join(event, uid):
            return
        clear(uid)
        await reply(event, MAIN_TEXT, buttons=main_menu_buttons(uid))
