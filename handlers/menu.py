from telethon import events

from config import clear, user_perms
from db import set_user_lang, get_user_lang
from i18n import t, LANGUAGES
from helpers import auth, reply, main_menu_buttons, main_menu_text, _check_force_join, _lang_picker_buttons


def register(bot):
    @bot.on(events.NewMessage(pattern="/start"))
    @auth
    async def cmd_start(event):
        uid = event.sender_id
        clear(uid)
        await event.respond(main_menu_text(uid), buttons=main_menu_buttons(uid), parse_mode="md")

    @bot.on(events.CallbackQuery(data=b"m"))
    @auth
    async def cb_main(event):
        uid = event.sender_id
        clear(uid)
        await reply(event, main_menu_text(uid), buttons=main_menu_buttons(uid))

    @bot.on(events.CallbackQuery(data=b"fj"))
    async def cb_force_join_check(event):
        uid = event.sender_id
        if not user_perms(uid):
            return
        if not await _check_force_join(event, uid, silent=True):
            await event.answer(t("force_join_not_joined", uid), alert=True)
            return
        clear(uid)
        await reply(event, main_menu_text(uid), buttons=main_menu_buttons(uid))

    @bot.on(events.CallbackQuery(pattern=rb"^lang:(.+)$"))
    async def cb_lang_select(event):
        uid = event.sender_id
        if not user_perms(uid):
            return
        lang = event.pattern_match.group(1).decode()
        if lang not in LANGUAGES:
            return
        set_user_lang(uid, lang)
        # After setting language, proceed to force-join check then main menu
        if not await _check_force_join(event, uid):
            return
        clear(uid)
        await reply(event, main_menu_text(uid), buttons=main_menu_buttons(uid))

    @bot.on(events.CallbackQuery(data=b"cl"))
    @auth
    async def cb_change_language(event):
        uid = event.sender_id
        await reply(event, t("lang_select", uid), buttons=_lang_picker_buttons())
