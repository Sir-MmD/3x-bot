import json

from telethon import events, Button

from config import st, clear
from db import get_test_account, set_test_account, clear_test_account, log_activity
from helpers import auth, reply, answer
from i18n import t
from .owner import _require_owner, _back_btn


# ── Test Account ──────────────────────────────────────────────────────────────

async def _show_ta_naming_picker(event, uid: int):
    """Show naming method picker for test account setup."""
    s = st(uid)
    s["op_ta"] = {}
    s["state"] = None
    await reply(
        event,
        t("op_test_account_naming_title", uid),
        buttons=[
            [
                Button.inline(t("btn_random", uid), b"op:tan:r"),
                Button.inline(t("btn_rand_prefix", uid), b"op:tan:rp"),
                Button.inline(t("btn_prefix_rand", uid), b"op:tan:pr"),
            ],
            [
                Button.inline(t("btn_prefix_num_rand", uid), b"op:tan:pnr"),
                Button.inline(t("btn_prefix_num_rand_post", uid), b"op:tan:pnrx"),
            ],
            [
                Button.inline(t("btn_prefix_num", uid), b"op:tan:pn"),
                Button.inline(t("btn_prefix_num_post", uid), b"op:tan:pnx"),
            ],
            [Button.inline(t("btn_back", uid), b"op:set"),
             Button.inline(t("btn_main_menu", uid), b"m")],
        ],
    )


async def _handle_ta_prefix(event, uid, s):
    s["state"] = None
    prefix = event.text.strip()
    if not prefix:
        s["state"] = "op_ta_prefix"
        await event.respond(t("prefix_empty", uid))
        return True
    s["op_ta"]["prefix"] = prefix
    method = s["op_ta"]["method"]
    if method in ("pnrx", "pnx"):
        s["state"] = "op_ta_postfix"
        await event.respond(
            t("enter_postfix_prompt", uid, prefix=prefix),
            buttons=_back_btn(uid, b"op:eta"),
        )
    else:
        s["state"] = "op_ta_traffic"
        await event.respond(
            t("enter_traffic_prompt", uid),
            buttons=_back_btn(uid, b"op:eta"),
        )
    return True


async def _handle_ta_postfix(event, uid, s):
    s["state"] = None
    postfix = event.text.strip()
    if not postfix:
        s["state"] = "op_ta_postfix"
        await event.respond(t("postfix_empty", uid))
        return True
    s["op_ta"]["postfix"] = postfix
    s["state"] = "op_ta_traffic"
    await event.respond(
        t("enter_traffic_prompt", uid),
        buttons=_back_btn(uid, b"op:eta"),
    )
    return True


async def _handle_ta_traffic(event, uid, s):
    s["state"] = None
    try:
        gb = float(event.text.strip())
    except ValueError:
        s["state"] = "op_ta_traffic"
        await event.respond(t("enter_traffic_invalid", uid))
        return True
    s["op_ta"]["traffic"] = gb
    s["state"] = "op_ta_days"
    await event.respond(
        t("enter_duration_prompt", uid),
        buttons=_back_btn(uid, b"op:eta"),
    )
    return True


async def _handle_ta_days(event, uid, s):
    s["state"] = None
    try:
        days = int(event.text.strip())
    except ValueError:
        s["state"] = "op_ta_days"
        await event.respond(t("enter_duration_invalid", uid))
        return True
    s["op_ta"]["days"] = days
    if days > 0:
        await event.respond(
            t("start_after_use_prompt", uid),
            buttons=[
                [Button.inline(t("btn_yes", uid), b"op:tasa:y"),
                 Button.inline(t("btn_no", uid), b"op:tasa:n")],
                _back_btn(uid, b"op:eta")[0],
            ],
        )
    else:
        s["op_ta"]["sau"] = False
        ta = s["op_ta"]
        set_test_account(ta.get("method", "r"), ta.get("prefix", ""),
                         ta.get("postfix", ""), ta["traffic"], ta["days"], ta["sau"])
        log_activity(uid, "edit_test_account", json.dumps(ta))
        clear(uid)
        await event.respond(
            t("op_test_account_saved", uid),
            buttons=_back_btn(uid, b"op:set"),
        )
    return True


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):

    @bot.on(events.CallbackQuery(data=b"op:eta"))
    @auth
    @_require_owner
    async def cb_edit_test_account(event):
        uid = event.sender_id
        clear(uid)
        ta = get_test_account()
        if ta:
            # Enabled — show summary with Edit / Disable
            method_key = {
                "r": "btn_random", "rp": "btn_rand_prefix", "pr": "btn_prefix_rand",
                "pnr": "btn_prefix_num_rand", "pnrx": "btn_prefix_num_rand_post",
                "pn": "btn_prefix_num", "pnx": "btn_prefix_num_post",
            }.get(ta.get("method", "r"), "btn_random")
            method_label = t(method_key, uid)
            prefix = ta.get("prefix", "")
            postfix = ta.get("postfix", "")
            if prefix:
                method_label += f" ({prefix}"
                if postfix:
                    method_label += f"…{postfix}"
                method_label += ")"
            sau_str = t("btn_yes", uid) if ta.get("sau") else t("btn_no", uid)
            text = t("op_test_account_summary", uid,
                     method=method_label,
                     traffic=ta.get("traffic", 0),
                     days=ta.get("days", 0),
                     sau=sau_str)
            btns = [
                [Button.inline(t("btn_edit_test_account", uid), b"op:tae")],
                [Button.inline(t("btn_disable_test_account", uid), b"op:tad")],
                [Button.inline(t("btn_back", uid), b"op:set"),
                 Button.inline(t("btn_main_menu", uid), b"m")],
            ]
            await reply(event, text, buttons=btns)
        else:
            # Disabled — show naming picker directly
            await _show_ta_naming_picker(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:tae"))
    @auth
    @_require_owner
    async def cb_ta_edit(event):
        uid = event.sender_id
        clear(uid)
        await _show_ta_naming_picker(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:tan:(.+)$"))
    @auth
    @_require_owner
    async def cb_ta_naming(event):
        uid = event.sender_id
        method = event.pattern_match.group(1).decode()
        s = st(uid)
        if "op_ta" not in s:
            s["op_ta"] = {}
        s["op_ta"]["method"] = method
        if method == "r":
            s["state"] = "op_ta_traffic"
            await reply(
                event,
                t("enter_traffic_prompt", uid),
                buttons=_back_btn(uid, b"op:eta"),
            )
        else:
            s["state"] = "op_ta_prefix"
            await reply(
                event,
                t("enter_prefix_prompt", uid),
                buttons=_back_btn(uid, b"op:eta"),
            )

    @bot.on(events.CallbackQuery(data=b"op:tad"))
    @auth
    @_require_owner
    async def cb_ta_disable(event):
        uid = event.sender_id
        clear_test_account()
        log_activity(uid, "edit_test_account", json.dumps({"action": "cleared"}))
        clear(uid)
        await answer(event, t("op_test_account_cleared", uid))
        from .settings import _show_settings
        await _show_settings(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:tasa:([yn])$"))
    @auth
    @_require_owner
    async def cb_ta_sau(event):
        uid = event.sender_id
        s = st(uid)
        ta = s.get("op_ta", {})
        ta["sau"] = event.pattern_match.group(1) == b"y"
        set_test_account(ta.get("method", "r"), ta.get("prefix", ""),
                         ta.get("postfix", ""), ta.get("traffic", 0),
                         ta.get("days", 0), ta["sau"])
        log_activity(uid, "edit_test_account", json.dumps(ta))
        clear(uid)
        await reply(
            event,
            t("op_test_account_saved", uid),
            buttons=_back_btn(uid, b"op:set"),
        )
