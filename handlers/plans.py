import json

from telethon import events, Button

from config import st, clear
from db import get_plans, get_plan, add_plan, update_plan, remove_plan, log_activity
from helpers import auth, reply, answer
from i18n import t
from .owner import _require_owner, _back_btn


# ── Account Plans ──────────────────────────────────────────────────────────────

def format_plan_label(plan: dict, uid: int) -> str:
    """Format plan for button display: 'Name (30d-50GB⏱)'"""
    unlim = t("unlimited", uid)
    days = plan.get("days", 0)
    traffic = plan.get("traffic", 0)
    d = f"{days}d" if days > 0 else unlim
    t_str = f"{traffic:g}GB" if traffic > 0 else unlim
    sau = "\U0001f552" if plan.get("sau") else ""
    return f"{plan['name']} ({d}-{t_str}{sau})"


def _format_plan_traffic(plan: dict, uid: int) -> str:
    unlim = t("unlimited", uid)
    traffic = plan.get("traffic", 0)
    return f"{traffic:g}GB" if traffic > 0 else unlim


def _format_plan_days(plan: dict, uid: int) -> str:
    unlim = t("unlimited", uid)
    days = plan.get("days", 0)
    return str(days) if days > 0 else unlim


async def _show_plans_list(event, uid: int):
    plans = get_plans()
    btns = []
    for plan in plans:
        btns.append([Button.inline(format_plan_label(plan, uid), f"op:plv:{plan['id']}".encode())])
    btns.append([Button.inline(t("btn_add_plan", uid), b"op:pla")])
    btns.append([Button.inline(t("btn_back", uid), b"op:set"),
                 Button.inline(t("btn_main_menu", uid), b"m")])
    await reply(event, t("op_plans_title", uid), buttons=btns)


async def _show_plan_detail(event, uid: int, plan_id: int):
    plan = get_plan(plan_id)
    if not plan:
        await _show_plans_list(event, uid)
        return
    sau_str = t("btn_yes", uid) if plan.get("sau") else t("btn_no", uid)
    text = t("op_plan_detail", uid,
             name=plan["name"],
             traffic=_format_plan_traffic(plan, uid),
             days=_format_plan_days(plan, uid),
             sau=sau_str)
    btns = [
        [Button.inline(t("btn_edit_plan", uid), f"op:ple:{plan_id}".encode()),
         Button.inline(t("btn_remove_plan", uid), f"op:plr:{plan_id}".encode())],
        [Button.inline(t("btn_back", uid), b"op:pl"),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await reply(event, text, buttons=btns)


async def _show_plan_edit(event, uid: int, plan_id: int):
    plan = get_plan(plan_id)
    if not plan:
        await _show_plans_list(event, uid)
        return
    sau_str = t("btn_yes", uid) if plan.get("sau") else t("btn_no", uid)
    text = t("op_plan_detail", uid,
             name=plan["name"],
             traffic=_format_plan_traffic(plan, uid),
             days=_format_plan_days(plan, uid),
             sau=sau_str)
    btns = [
        [Button.inline(t("btn_edit_plan_name", uid), f"op:plen:{plan_id}".encode()),
         Button.inline(t("btn_edit_plan_traffic", uid), f"op:plet:{plan_id}".encode())],
        [Button.inline(t("btn_edit_plan_days", uid), f"op:pled:{plan_id}".encode()),
         Button.inline(t("btn_toggle_sau", uid), f"op:ples:{plan_id}".encode())],
        [Button.inline(t("btn_remove_plan", uid), f"op:plr:{plan_id}".encode())],
        [Button.inline(t("btn_back", uid), f"op:plv:{plan_id}".encode()),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await reply(event, text, buttons=btns)


# ── Add Plan text handlers ─────────────────────────────────────────────────

async def _handle_pl_name(event, uid, s):
    s["state"] = None
    name = event.text.strip()
    if not name:
        s["state"] = "op_pl_name"
        return True
    plans = get_plans()
    if any(p["name"] == name for p in plans):
        s["state"] = "op_pl_name"
        await event.respond(t("op_plan_name_exists", uid))
        return True
    s["op_pl"] = {"name": name}
    s["state"] = "op_pl_traffic"
    await event.respond(
        t("enter_traffic_prompt", uid),
        buttons=_back_btn(uid, b"op:pl"),
    )
    return True


async def _handle_pl_traffic(event, uid, s):
    s["state"] = None
    try:
        gb = float(event.text.strip())
    except ValueError:
        s["state"] = "op_pl_traffic"
        await event.respond(t("enter_traffic_invalid", uid))
        return True
    s["op_pl"]["traffic"] = gb
    s["state"] = "op_pl_days"
    await event.respond(
        t("enter_duration_prompt", uid),
        buttons=_back_btn(uid, b"op:pl"),
    )
    return True


async def _handle_pl_days(event, uid, s):
    s["state"] = None
    try:
        days = int(event.text.strip())
    except ValueError:
        s["state"] = "op_pl_days"
        await event.respond(t("enter_duration_invalid", uid))
        return True
    s["op_pl"]["days"] = days
    if days > 0:
        await event.respond(
            t("start_after_use_prompt", uid),
            buttons=[
                [Button.inline(t("btn_yes", uid), b"op:plsa:y"),
                 Button.inline(t("btn_no", uid), b"op:plsa:n")],
                _back_btn(uid, b"op:pl")[0],
            ],
        )
    else:
        s["op_pl"]["sau"] = False
        pl = s["op_pl"]
        new_id = add_plan(pl["name"], pl["traffic"], pl["days"], pl["sau"])
        log_activity(uid, "add_plan", json.dumps({"id": new_id, **pl}))
        clear(uid)
        await event.respond(
            t("op_plan_added", uid),
            buttons=_back_btn(uid, b"op:pl"),
        )
    return True


# ── Edit Plan text handlers ────────────────────────────────────────────────

async def _handle_ple_name(event, uid, s):
    s["state"] = None
    name = event.text.strip()
    if not name:
        s["state"] = "op_ple_name"
        return True
    plan_id = s.get("op_pl_id", -1)
    plan = get_plan(plan_id)
    if not plan:
        return True
    plans = get_plans()
    if any(p["id"] != plan_id and p["name"] == name for p in plans):
        s["state"] = "op_ple_name"
        await event.respond(t("op_plan_name_exists", uid))
        return True
    update_plan(plan_id, name=name)
    log_activity(uid, "edit_plan", json.dumps({"id": plan_id, "field": "name", "value": name}))
    await answer(event, t("op_plan_updated", uid))
    await _show_plan_edit(event, uid, plan_id)
    return True


async def _handle_ple_traffic(event, uid, s):
    s["state"] = None
    try:
        gb = float(event.text.strip())
    except ValueError:
        s["state"] = "op_ple_traffic"
        await event.respond(t("enter_traffic_invalid", uid))
        return True
    plan_id = s.get("op_pl_id", -1)
    plan = get_plan(plan_id)
    if not plan:
        return True
    update_plan(plan_id, traffic=gb)
    log_activity(uid, "edit_plan", json.dumps({"id": plan_id, "field": "traffic", "value": gb}))
    await answer(event, t("op_plan_updated", uid))
    await _show_plan_edit(event, uid, plan_id)
    return True


async def _handle_ple_days(event, uid, s):
    s["state"] = None
    try:
        days = int(event.text.strip())
    except ValueError:
        s["state"] = "op_ple_days"
        await event.respond(t("enter_duration_invalid", uid))
        return True
    plan_id = s.get("op_pl_id", -1)
    plan = get_plan(plan_id)
    if not plan:
        return True
    kwargs = {"days": days}
    if days == 0:
        kwargs["sau"] = False
    update_plan(plan_id, **kwargs)
    log_activity(uid, "edit_plan", json.dumps({"id": plan_id, "field": "days", "value": days}))
    await answer(event, t("op_plan_updated", uid))
    await _show_plan_edit(event, uid, plan_id)
    return True


# ── Register ────────────────────────────────────────────────────────────────

def register(bot):

    @bot.on(events.CallbackQuery(data=b"op:pl"))
    @auth
    @_require_owner
    async def cb_plans_list(event):
        uid = event.sender_id
        clear(uid)
        await _show_plans_list(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:pla"))
    @auth
    @_require_owner
    async def cb_plan_add(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_pl_name"
        await reply(event, t("op_plan_name_prompt", uid),
                    buttons=_back_btn(uid, b"op:pl"))

    @bot.on(events.CallbackQuery(pattern=rb"^op:plv:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_view(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        clear(uid)
        await _show_plan_detail(event, uid, plan_id)

    @bot.on(events.CallbackQuery(pattern=rb"^op:ple:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_edit(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        clear(uid)
        await _show_plan_edit(event, uid, plan_id)

    @bot.on(events.CallbackQuery(pattern=rb"^op:plen:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_edit_name(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        s = st(uid)
        s["op_pl_id"] = plan_id
        s["state"] = "op_ple_name"
        await reply(event, t("op_plan_name_prompt", uid),
                    buttons=_back_btn(uid, f"op:ple:{plan_id}".encode()))

    @bot.on(events.CallbackQuery(pattern=rb"^op:plet:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_edit_traffic(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        s = st(uid)
        s["op_pl_id"] = plan_id
        s["state"] = "op_ple_traffic"
        await reply(event, t("enter_traffic_prompt", uid),
                    buttons=_back_btn(uid, f"op:ple:{plan_id}".encode()))

    @bot.on(events.CallbackQuery(pattern=rb"^op:pled:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_edit_days(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        s = st(uid)
        s["op_pl_id"] = plan_id
        s["state"] = "op_ple_days"
        await reply(event, t("enter_duration_prompt", uid),
                    buttons=_back_btn(uid, f"op:ple:{plan_id}".encode()))

    @bot.on(events.CallbackQuery(pattern=rb"^op:ples:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_toggle_sau(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        plan = get_plan(plan_id)
        if not plan:
            return
        new_sau = not plan.get("sau", False)
        if new_sau and plan.get("days", 0) == 0:
            new_sau = False
        update_plan(plan_id, sau=new_sau)
        log_activity(uid, "edit_plan", json.dumps({"id": plan_id, "field": "sau"}))
        await answer(event, t("op_plan_updated", uid))
        await _show_plan_edit(event, uid, plan_id)

    @bot.on(events.CallbackQuery(pattern=rb"^op:plr:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_remove_confirm(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        plan = get_plan(plan_id)
        if not plan:
            return
        await reply(
            event,
            t("op_plan_remove_confirm", uid, name=plan["name"]),
            buttons=[
                [Button.inline(t("btn_yes", uid), f"op:plrc:{plan_id}".encode())],
                [Button.inline(t("btn_cancel", uid), f"op:plv:{plan_id}".encode())],
            ],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^op:plrc:(\d+)$"))
    @auth
    @_require_owner
    async def cb_plan_remove_execute(event):
        uid = event.sender_id
        plan_id = int(event.pattern_match.group(1))
        plan = get_plan(plan_id)
        if not plan:
            return
        remove_plan(plan_id)
        log_activity(uid, "remove_plan", json.dumps({"name": plan["name"]}))
        await answer(event, t("op_plan_removed", uid))
        await _show_plans_list(event, uid)

    @bot.on(events.CallbackQuery(pattern=rb"^op:plsa:([yn])$"))
    @auth
    @_require_owner
    async def cb_plan_add_sau(event):
        uid = event.sender_id
        s = st(uid)
        pl = s.get("op_pl")
        if not pl:
            return
        pl["sau"] = event.pattern_match.group(1) == b"y"
        new_id = add_plan(pl["name"], pl["traffic"], pl["days"], pl["sau"])
        log_activity(uid, "add_plan", json.dumps({"id": new_id, **pl}))
        clear(uid)
        await reply(
            event,
            t("op_plan_added", uid),
            buttons=_back_btn(uid, b"op:pl"),
        )
