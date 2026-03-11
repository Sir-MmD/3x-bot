import io
import json
import zipfile
from datetime import datetime

from telethon import events, Button

import config as _config_mod
import db as _db_mod
from config import (
    st, clear, bot, DATA_DIR, _CONFIG_PATH,
    load_db_panels, start_auto_backup, stop_auto_backup,
    start_panel_auto_backup, stop_panel_auto_backup,
    panels, get_panel,
)
from db import get_setting, set_setting, log_activity, _DB_PATH
from helpers import auth, reply, answer
from i18n import t
from .owner import _require_owner, _back_btn, _format_interval, is_owner


async def _show_backup_menu(event, uid: int):
    btns = [
        [Button.inline(t("btn_panel_db", uid), b"op:br:panel")],
        [Button.inline(t("btn_bot_db", uid), b"op:br:bot")],
        [Button.inline(t("btn_back", uid), b"op"),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await reply(event, t("op_br_title", uid), buttons=btns)


async def _show_bot_backup_menu(event, uid: int):
    ab_val = get_setting("auto_backup_interval")
    btns = [
        [Button.inline(t("btn_backup_now", uid), b"op:bk")],
        [Button.inline(t("btn_auto_backup", uid), b"op:ab")],
        [Button.inline(t("btn_restore", uid), b"op:rs")],
        [Button.inline(t("btn_back", uid), b"op:br"),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    lines = [t("op_br_bot_title", uid)]
    if ab_val:
        lines.append(t("op_ab_status", uid, interval=_format_interval(int(ab_val))))
    await reply(event, "\n".join(lines), buttons=btns)


async def handle_owner_restore(event) -> bool:
    """Handle uploaded file for restore (state=op_rs or op_prs)."""
    uid = event.sender_id
    s = st(uid)
    state = s.get("state")

    if state == "op_prs":
        return await _handle_panel_restore_upload(event, uid, s)

    if state != "op_rs":
        return False
    if not is_owner(uid):
        return False

    doc = event.message.document
    if not doc:
        return False

    name = event.message.file.name or ""
    if not name.lower().endswith(".zip"):
        await event.respond(t("restore_invalid_zip", uid),
                            buttons=_back_btn(uid, b"op"))
        return True

    buf = io.BytesIO()
    await bot.download_media(event.message, buf)
    buf.seek(0)

    try:
        zf = zipfile.ZipFile(buf)
    except zipfile.BadZipFile:
        await event.respond(t("restore_invalid_zip", uid),
                            buttons=_back_btn(uid, b"op"))
        return True

    names = zf.namelist()
    if "config.toml" not in names or "3x-bot.db" not in names:
        zf.close()
        await event.respond(t("restore_missing_files", uid),
                            buttons=_back_btn(uid, b"op"))
        return True

    # Extract files to DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zf.extract("config.toml", DATA_DIR)
    zf.extract("3x-bot.db", DATA_DIR)
    zf.close()

    # Invalidate all caches
    _db_mod._admins_cache = None
    _db_mod._panels_cache = None
    _db_mod._settings_cache = None
    _db_mod._lang_cache.clear()

    # Reload panels
    load_db_panels()

    log_activity(uid, "restore")
    clear(uid)
    await event.respond(
        t("restore_success", uid) + "\n" + t("restore_restart_note", uid),
        buttons=_back_btn(uid, b"op"),
    )
    return True


async def _handle_panel_restore_upload(event, uid, s) -> bool:
    """Handle uploaded DB file for panel restore (state=op_prs)."""
    if not is_owner(uid):
        return False
    pid = s.get("op_prs_panel")
    if not pid:
        return False

    doc = event.message.document
    if not doc:
        return False

    name = event.message.file.name or ""
    if not name.lower().endswith(".db"):
        await event.respond(t("panel_restore_invalid", uid),
                            buttons=_back_btn(uid, f"op:pdb:{pid}".encode()))
        return True

    buf = io.BytesIO()
    await bot.download_media(event.message, buf)
    db_data = buf.getvalue()

    p = get_panel(pid)
    if not p:
        return True

    try:
        await p.import_db(db_data)
    except RuntimeError as e:
        s["state"] = None
        await event.respond(t("error_msg", uid, error=e),
                            buttons=_back_btn(uid, f"op:pdb:{pid}".encode()))
        return True

    log_activity(uid, "panel_restore", json.dumps({"panel": pid}))
    s["state"] = None
    btns = [
        [Button.inline(t("btn_restart_panel", uid), f"op:prst:{pid}".encode())],
        [Button.inline(t("btn_back", uid), f"op:pdb:{pid}".encode()),
         Button.inline(t("btn_main_menu", uid), b"m")],
    ]
    await event.respond(t("panel_restore_success", uid), buttons=btns)
    return True


async def _handle_panel_auto_backup_input(event, uid, s):
    s["state"] = None
    pid = s.get("op_pab_panel", "")
    try:
        num = int(event.text.strip())
        if num <= 0:
            raise ValueError
    except ValueError:
        s["state"] = "op_pab_input"
        await event.respond(t("op_ab_invalid_number", uid))
        return True
    unit = s.get("op_pab_unit", "h")
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    interval = num * multiplier
    set_setting(f"panel_auto_backup:{pid}", str(interval))
    start_panel_auto_backup(pid, interval)
    log_activity(uid, "panel_auto_backup", json.dumps({"panel": pid, "interval": interval}))
    clear(uid)
    await event.respond(
        t("op_ab_saved", uid, interval=_format_interval(interval)),
        buttons=_back_btn(uid, f"op:pdb:{pid}".encode() if pid else b"op:br:panel"),
    )
    return True


async def _handle_auto_backup_input(event, uid, s):
    s["state"] = None
    try:
        num = int(event.text.strip())
        if num <= 0:
            raise ValueError
    except ValueError:
        s["state"] = "op_ab_input"
        await event.respond(t("op_ab_invalid_number", uid))
        return True
    unit = s.get("op_ab_unit", "h")
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    interval = num * multiplier
    set_setting("auto_backup_interval", str(interval))
    start_auto_backup(interval)
    log_activity(uid, "auto_backup", json.dumps({"interval": interval}))
    clear(uid)
    await event.respond(
        t("op_ab_saved", uid, interval=_format_interval(interval)),
        buttons=_back_btn(uid, b"op:br:bot"),
    )
    return True


def register(bot):

    @bot.on(events.CallbackQuery(data=b"op:br"))
    @auth
    @_require_owner
    async def cb_backup_menu(event):
        uid = event.sender_id
        clear(uid)
        await _show_backup_menu(event, uid)

    # ── Bot DB ─────────────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:br:bot"))
    @auth
    @_require_owner
    async def cb_bot_backup_menu(event):
        uid = event.sender_id
        clear(uid)
        await _show_bot_backup_menu(event, uid)

    @bot.on(events.CallbackQuery(data=b"op:bk"))
    @auth
    @_require_owner
    async def cb_backup(event):
        uid = event.sender_id
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d_%H-%M")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            cfg_path = _CONFIG_PATH
            db_path = _DB_PATH
            if cfg_path.exists():
                zf.write(cfg_path, "config.toml")
            if DATA_DIR.joinpath("3x-bot.db").exists():
                zf.write(db_path, "3x-bot.db")
        buf.seek(0)
        buf.name = f"3x-bot-backup-{stamp}.zip"
        caption = t("backup_caption", uid, date=now.strftime("%Y/%m/%d"), time=now.strftime("%H:%M"))
        await bot.send_file(event.chat_id, buf, caption=caption)
        log_activity(uid, "backup")

    @bot.on(events.CallbackQuery(data=b"op:ab"))
    @auth
    @_require_owner
    async def cb_auto_backup(event):
        uid = event.sender_id
        ab_val = get_setting("auto_backup_interval")
        btns = []
        if ab_val:
            btns.append([Button.inline(t("btn_disable_auto_backup", uid), b"op:abd")])
        btns.extend([
            [Button.inline("⏱ " + t("op_ab_unit_seconds", uid), b"op:abu:s"),
             Button.inline("⏱ " + t("op_ab_unit_minutes", uid), b"op:abu:m")],
            [Button.inline("⏱ " + t("op_ab_unit_hours", uid), b"op:abu:h"),
             Button.inline("⏱ " + t("op_ab_unit_days", uid), b"op:abu:d")],
            [Button.inline(t("btn_back", uid), b"op:br:bot"),
             Button.inline(t("btn_main_menu", uid), b"m")],
        ])
        lines = [t("op_ab_title", uid)]
        if ab_val:
            lines.append(t("op_ab_status", uid, interval=_format_interval(int(ab_val))))
        await reply(event, "\n".join(lines), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:abu:([smhd])$"))
    @auth
    @_require_owner
    async def cb_auto_backup_unit(event):
        uid = event.sender_id
        unit = event.pattern_match.group(1).decode()
        s = st(uid)
        s["op_ab_unit"] = unit
        s["state"] = "op_ab_input"
        unit_labels = {"s": t("op_ab_unit_seconds", uid), "m": t("op_ab_unit_minutes", uid),
                       "h": t("op_ab_unit_hours", uid), "d": t("op_ab_unit_days", uid)}
        await reply(
            event,
            t("op_ab_enter_number", uid, unit=unit_labels[unit]),
            buttons=[[Button.inline(t("btn_back", uid), b"op:ab")]],
        )

    @bot.on(events.CallbackQuery(data=b"op:abd"))
    @auth
    @_require_owner
    async def cb_auto_backup_disable(event):
        uid = event.sender_id
        set_setting("auto_backup_interval", "")
        stop_auto_backup()
        log_activity(uid, "auto_backup", json.dumps({"action": "disabled"}))
        clear(uid)
        await reply(
            event,
            t("op_ab_disabled", uid),
            buttons=_back_btn(uid, b"op:br:bot"),
        )

    @bot.on(events.CallbackQuery(data=b"op:rs"))
    @auth
    @_require_owner
    async def cb_restore_prompt(event):
        uid = event.sender_id
        s = st(uid)
        s["state"] = "op_rs"
        await reply(event, t("restore_prompt", uid),
                    buttons=_back_btn(uid, b"op:br:bot"))

    # ── Panel DB ───────────────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"op:br:panel"))
    @auth
    @_require_owner
    async def cb_panel_db_select(event):
        uid = event.sender_id
        clear(uid)
        pnames = sorted(panels)
        if not pnames:
            await reply(event, t("panel_no_panels", uid),
                        buttons=_back_btn(uid, b"op:br"))
            return
        btns = [[Button.inline(f"\U0001f5a5 {pn}", f"op:pdb:{pn}".encode())] for pn in pnames]
        btns.append([Button.inline(t("btn_back", uid), b"op:br"),
                     Button.inline(t("btn_main_menu", uid), b"m")])
        await reply(event, t("panel_db_select", uid), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pdb:(.+)$"))
    @auth
    @_require_owner
    async def cb_panel_db_menu(event):
        uid = event.sender_id
        pid = event.pattern_match.group(1).decode()
        if pid not in panels:
            return
        pab_val = get_setting(f"panel_auto_backup:{pid}")
        btns = [
            [Button.inline(t("btn_backup_now", uid), f"op:pdbb:{pid}".encode())],
            [Button.inline(t("btn_auto_backup", uid), f"op:pab:{pid}".encode())],
            [Button.inline(t("btn_restore", uid), f"op:pdbr:{pid}".encode())],
            [Button.inline(t("btn_back", uid), b"op:br:panel"),
             Button.inline(t("btn_main_menu", uid), b"m")],
        ]
        lines = [t("panel_db_title", uid, panel=pid)]
        if pab_val:
            lines.append(t("op_ab_status", uid, interval=_format_interval(int(pab_val))))
        await reply(event, "\n".join(lines), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pdbb:(.+)$"))
    @auth
    @_require_owner
    async def cb_panel_db_backup(event):
        uid = event.sender_id
        pid = event.pattern_match.group(1).decode()
        p = get_panel(pid)
        if not p:
            return
        await answer(event, t("downloading_panel_db", uid))
        try:
            db_data = await p.get_db()
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e),
                                buttons=_back_btn(uid, f"op:pdb:{pid}".encode()))
            return
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d_%H-%M")
        buf = io.BytesIO(db_data)
        buf.name = f"{pid}-x-ui-{stamp}.db"
        caption = t("panel_backup_caption", uid, panel=pid,
                     date=now.strftime("%Y/%m/%d"), time=now.strftime("%H:%M"))
        await bot.send_file(event.chat_id, buf, caption=caption)
        log_activity(uid, "panel_backup", json.dumps({"panel": pid}))

    @bot.on(events.CallbackQuery(pattern=rb"^op:pdbr:(.+)$"))
    @auth
    @_require_owner
    async def cb_panel_db_restore_prompt(event):
        uid = event.sender_id
        pid = event.pattern_match.group(1).decode()
        if pid not in panels:
            return
        s = st(uid)
        s["state"] = "op_prs"
        s["op_prs_panel"] = pid
        await reply(event, t("panel_restore_prompt", uid, panel=pid),
                    buttons=_back_btn(uid, f"op:pdb:{pid}".encode()))

    # ── Panel Auto Backup ──────────────────────────────────────────────────

    @bot.on(events.CallbackQuery(pattern=rb"^op:pab:([^:]+)$"))
    @auth
    @_require_owner
    async def cb_panel_auto_backup(event):
        uid = event.sender_id
        pid = event.pattern_match.group(1).decode()
        if pid not in panels:
            return
        s = st(uid)
        s["op_pab_panel"] = pid
        pab_val = get_setting(f"panel_auto_backup:{pid}")
        btns = []
        if pab_val:
            btns.append([Button.inline(t("btn_disable_auto_backup", uid), b"op:pabd")])
        btns.extend([
            [Button.inline("⏱ " + t("op_ab_unit_seconds", uid), b"op:pabu:s"),
             Button.inline("⏱ " + t("op_ab_unit_minutes", uid), b"op:pabu:m")],
            [Button.inline("⏱ " + t("op_ab_unit_hours", uid), b"op:pabu:h"),
             Button.inline("⏱ " + t("op_ab_unit_days", uid), b"op:pabu:d")],
            [Button.inline(t("btn_back", uid), f"op:pdb:{pid}".encode()),
             Button.inline(t("btn_main_menu", uid), b"m")],
        ])
        lines = [t("op_pab_title", uid, panel=pid)]
        if pab_val:
            lines.append(t("op_ab_status", uid, interval=_format_interval(int(pab_val))))
        await reply(event, "\n".join(lines), buttons=btns)

    @bot.on(events.CallbackQuery(pattern=rb"^op:pabu:([smhd])$"))
    @auth
    @_require_owner
    async def cb_panel_auto_backup_unit(event):
        uid = event.sender_id
        unit = event.pattern_match.group(1).decode()
        s = st(uid)
        pid = s.get("op_pab_panel", "")
        s["op_pab_unit"] = unit
        s["state"] = "op_pab_input"
        unit_labels = {"s": t("op_ab_unit_seconds", uid), "m": t("op_ab_unit_minutes", uid),
                       "h": t("op_ab_unit_hours", uid), "d": t("op_ab_unit_days", uid)}
        await reply(
            event,
            t("op_ab_enter_number", uid, unit=unit_labels[unit]),
            buttons=[[Button.inline(t("btn_back", uid),
                                    f"op:pab:{pid}".encode() if pid else b"op:br:panel")]],
        )

    @bot.on(events.CallbackQuery(data=b"op:pabd"))
    @auth
    @_require_owner
    async def cb_panel_auto_backup_disable(event):
        uid = event.sender_id
        s = st(uid)
        pid = s.get("op_pab_panel", "")
        set_setting(f"panel_auto_backup:{pid}", "")
        stop_panel_auto_backup(pid)
        log_activity(uid, "panel_auto_backup", json.dumps({"panel": pid, "action": "disabled"}))
        clear(uid)
        await reply(
            event,
            t("op_ab_disabled", uid),
            buttons=_back_btn(uid, f"op:pdb:{pid}".encode() if pid else b"op:br:panel"),
        )

    @bot.on(events.CallbackQuery(pattern=rb"^op:prst:(.+)$"))
    @auth
    @_require_owner
    async def cb_panel_restart(event):
        uid = event.sender_id
        pid = event.pattern_match.group(1).decode()
        p = get_panel(pid)
        if not p:
            return
        try:
            await p.restart_panel()
        except RuntimeError as e:
            await event.respond(t("error_msg", uid, error=e),
                                buttons=_back_btn(uid, f"op:pdb:{pid}".encode()))
            return
        log_activity(uid, "panel_restart", json.dumps({"panel": pid}))
        await reply(event, t("panel_restarting", uid, panel=pid),
                    buttons=_back_btn(uid, f"op:pdb:{pid}".encode()))

    @bot.on(events.CallbackQuery(data=b"op:restart"))
    @auth
    @_require_owner
    async def cb_restart_confirm(event):
        uid = event.sender_id
        btns = [
            [Button.inline(t("btn_yes_restart", uid), b"op:restartc")],
            [Button.inline(t("btn_cancel", uid), b"op")],
        ]
        await reply(event, t("confirm_restart", uid), buttons=btns)

    @bot.on(events.CallbackQuery(data=b"op:restartc"))
    @auth
    @_require_owner
    async def cb_restart_execute(event):
        uid = event.sender_id
        log_activity(uid, "restart")
        await event.respond(t("restarting", uid))
        _config_mod.restart_requested = uid
        await bot.disconnect()
