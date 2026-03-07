import json
import sqlite3
import time
from pathlib import Path

_DB_PATH = str(Path.home() / "3x-bot" / "3x-bot.db")
_lang_cache: dict[int, str] = {}
_admins_cache: dict[int, tuple[set[str], bool, set[str], dict[str, set[int] | None]]] | None = None
_panels_cache: list[dict] | None = None
_settings_cache: dict[str, str] | None = None
_profiles_cache: dict[int, tuple[str, str, str, str, str]] = {}  # uid → (first, last, user, phone, bio)
_profile_ts_cache: dict[int, float] = {}  # uid → updated_at


def init_db():
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            language TEXT NOT NULL DEFAULT 'en',
            created_at REAL NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS db_admins (
            user_id     INTEGER PRIMARY KEY,
            permissions TEXT NOT NULL DEFAULT '',
            is_owner    INTEGER NOT NULL DEFAULT 0,
            added_by    INTEGER NOT NULL,
            created_at  REAL NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS db_panels (
            name        TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            username    TEXT NOT NULL,
            password    TEXT NOT NULL,
            proxy       TEXT NOT NULL DEFAULT '',
            sub_url     TEXT NOT NULL DEFAULT '',
            added_by    INTEGER NOT NULL,
            created_at  REAL NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS user_profiles (
            user_id    INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL DEFAULT '',
            last_name  TEXT NOT NULL DEFAULT '',
            username   TEXT NOT NULL DEFAULT '',
            phone      TEXT NOT NULL DEFAULT '',
            bio        TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS activity_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            action     TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(created_at)")
    # Migration: add panels column to db_admins
    try:
        con.execute("ALTER TABLE db_admins ADD COLUMN panels TEXT NOT NULL DEFAULT '*'")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migration: add inbounds column to db_admins
    try:
        con.execute("ALTER TABLE db_admins ADD COLUMN inbounds TEXT NOT NULL DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migration: add sort_order column to db_panels
    try:
        con.execute("ALTER TABLE db_panels ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    con.commit()
    con.close()


def get_user_lang(uid: int) -> str | None:
    cached = _lang_cache.get(uid)
    if cached is not None:
        return cached
    con = sqlite3.connect(_DB_PATH)
    row = con.execute("SELECT language FROM users WHERE user_id = ?", (uid,)).fetchone()
    con.close()
    if row:
        _lang_cache[uid] = row[0]
        return row[0]
    return None


def set_user_lang(uid: int, lang: str):
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO users (user_id, language, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET language = excluded.language",
        (uid, lang, time.time()),
    )
    con.commit()
    con.close()
    _lang_cache[uid] = lang


# ── DB Admins ────────────────────────────────────────────────────────────────

def _parse_inbounds_json(raw: str) -> dict[str, set[int] | None]:
    """Parse inbounds JSON string → {panel: set of IDs or None}."""
    try:
        d = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    result: dict[str, set[int] | None] = {}
    for panel, val in d.items():
        if val == "*":
            result[panel] = None
        else:
            result[panel] = {int(x) for x in str(val).split(",") if x.strip()}
    return result


def _serialize_inbounds(d: dict[str, set[int] | None]) -> str:
    """Serialize inbounds dict → JSON string."""
    out: dict[str, str] = {}
    for panel, ids in d.items():
        if ids is None:
            out[panel] = "*"
        else:
            out[panel] = ",".join(str(i) for i in sorted(ids))
    return json.dumps(out)


def get_db_admins() -> dict[int, tuple[set[str], bool, set[str], dict[str, set[int] | None]]]:
    global _admins_cache
    if _admins_cache is not None:
        return _admins_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute("SELECT user_id, permissions, is_owner, panels, inbounds FROM db_admins").fetchall()
    con.close()
    result: dict[int, tuple[set[str], bool, set[str], dict[str, set[int] | None]]] = {}
    for uid, perms_str, is_owner, panels_str, inbounds_str in rows:
        perms = set(perms_str.split(",")) if perms_str else set()
        perms.discard("")
        admin_panels = set(panels_str.split(",")) if panels_str else {"*"}
        admin_panels.discard("")
        inbounds = _parse_inbounds_json(inbounds_str)
        result[uid] = (perms, bool(is_owner), admin_panels, inbounds)
    _admins_cache = result
    return result


def add_db_admin(uid: int, perms: set[str], is_owner: bool, added_by: int,
                  admin_panels: set[str] | None = None,
                  admin_inbounds: dict[str, set[int] | None] | None = None):
    global _admins_cache
    if admin_panels is None:
        admin_panels = {"*"}
    if admin_inbounds is None:
        admin_inbounds = {}
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO db_admins (user_id, permissions, is_owner, added_by, created_at, panels, inbounds)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, ",".join(sorted(perms)), int(is_owner), added_by, time.time(),
         ",".join(sorted(admin_panels)), _serialize_inbounds(admin_inbounds)),
    )
    con.commit()
    con.close()
    _admins_cache = None


def remove_db_admin(uid: int):
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute("DELETE FROM db_admins WHERE user_id = ?", (uid,))
    con.commit()
    con.close()
    _admins_cache = None


def update_db_admin_perms(uid: int, perms: set[str]):
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "UPDATE db_admins SET permissions = ? WHERE user_id = ?",
        (",".join(sorted(perms)), uid),
    )
    con.commit()
    con.close()
    _admins_cache = None


def update_db_admin_owner(uid: int, is_owner: bool):
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "UPDATE db_admins SET is_owner = ? WHERE user_id = ?",
        (int(is_owner), uid),
    )
    con.commit()
    con.close()
    _admins_cache = None


def update_db_admin_inbounds(uid: int, inbounds: dict[str, set[int] | None]):
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "UPDATE db_admins SET inbounds = ? WHERE user_id = ?",
        (_serialize_inbounds(inbounds), uid),
    )
    con.commit()
    con.close()
    _admins_cache = None


def update_db_admin_panels(uid: int, panel_names: set[str]):
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "UPDATE db_admins SET panels = ? WHERE user_id = ?",
        (",".join(sorted(panel_names)) if panel_names else "*", uid),
    )
    con.commit()
    con.close()
    _admins_cache = None


def rename_panel_in_admins(old: str, new: str):
    """Update panel name in all admin records (panels + inbounds)."""
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute("SELECT user_id, panels, inbounds FROM db_admins").fetchall()
    for uid, panels_str, inbounds_str in rows:
        changed = False
        pset = set(panels_str.split(",")) if panels_str else set()
        if old in pset:
            pset.discard(old)
            pset.add(new)
            con.execute("UPDATE db_admins SET panels = ? WHERE user_id = ?",
                        (",".join(sorted(pset)), uid))
            changed = True
        ib = _parse_inbounds_json(inbounds_str)
        if old in ib:
            ib[new] = ib.pop(old)
            con.execute("UPDATE db_admins SET inbounds = ? WHERE user_id = ?",
                        (_serialize_inbounds(ib), uid))
            changed = True
    con.commit()
    con.close()
    _admins_cache = None


def remove_panel_from_admins(name: str):
    """Remove a panel from all admin records (panels + inbounds)."""
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute("SELECT user_id, panels, inbounds FROM db_admins").fetchall()
    for uid, panels_str, inbounds_str in rows:
        pset = set(panels_str.split(",")) if panels_str else set()
        if name in pset:
            pset.discard(name)
            if not pset:
                pset = {"*"}
            con.execute("UPDATE db_admins SET panels = ? WHERE user_id = ?",
                        (",".join(sorted(pset)), uid))
        ib = _parse_inbounds_json(inbounds_str)
        if name in ib:
            del ib[name]
            con.execute("UPDATE db_admins SET inbounds = ? WHERE user_id = ?",
                        (_serialize_inbounds(ib), uid))
    con.commit()
    con.close()
    _admins_cache = None


def rename_panel_in_settings(old: str, new: str):
    """Update panel name in public_panels and public_inbounds settings."""
    global _settings_cache
    con = sqlite3.connect(_DB_PATH)
    row = con.execute("SELECT value FROM settings WHERE key = 'public_panels'").fetchone()
    if row:
        pset = set(row[0].split(",")) if row[0] else set()
        if old in pset:
            pset.discard(old)
            pset.add(new)
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_panels'",
                        (",".join(sorted(pset)),))
    row2 = con.execute("SELECT value FROM settings WHERE key = 'public_inbounds'").fetchone()
    if row2:
        ib = _parse_inbounds_json(row2[0])
        if old in ib:
            ib[new] = ib.pop(old)
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_inbounds'",
                        (_serialize_inbounds(ib),))
    con.commit()
    _settings_cache = None
    con.close()


def remove_panel_from_settings(name: str):
    """Remove a panel from public_panels and public_inbounds settings."""
    global _settings_cache
    con = sqlite3.connect(_DB_PATH)
    row = con.execute("SELECT value FROM settings WHERE key = 'public_panels'").fetchone()
    if row:
        pset = set(row[0].split(",")) if row[0] else set()
        if name in pset:
            pset.discard(name)
            if not pset:
                pset = {"*"}
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_panels'",
                        (",".join(sorted(pset)),))
    row2 = con.execute("SELECT value FROM settings WHERE key = 'public_inbounds'").fetchone()
    if row2:
        ib = _parse_inbounds_json(row2[0])
        if name in ib:
            del ib[name]
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_inbounds'",
                        (_serialize_inbounds(ib),))
    con.commit()
    _settings_cache = None
    con.close()


# ── DB Panels ────────────────────────────────────────────────────────────────

def get_db_panels() -> list[dict]:
    global _panels_cache
    if _panels_cache is not None:
        return _panels_cache
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM db_panels ORDER BY sort_order, created_at").fetchall()
    con.close()
    result = [dict(r) for r in rows]
    _panels_cache = result
    return result


def add_db_panel(name: str, url: str, username: str, password: str,
                 proxy: str, sub_url: str, added_by: int):
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    max_order = con.execute("SELECT COALESCE(MAX(sort_order), -1) FROM db_panels").fetchone()[0]
    con.execute(
        "INSERT INTO db_panels (name, url, username, password, proxy, sub_url, added_by, created_at, sort_order)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, url, username, password, proxy, sub_url, added_by, time.time(), max_order + 1),
    )
    con.commit()
    con.close()
    _panels_cache = None


def get_db_panel(name: str) -> dict | None:
    """Get a single panel by name (from cache)."""
    for p in get_db_panels():
        if p["name"] == name:
            return p
    return None


def remove_db_panel(name: str):
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute("DELETE FROM db_panels WHERE name = ?", (name,))
    con.commit()
    con.close()
    _panels_cache = None


_PANEL_FIELDS = {"url", "username", "password", "proxy", "sub_url"}


def update_db_panel_field(name: str, field: str, value: str):
    """Update a single field of a panel."""
    global _panels_cache
    if field not in _PANEL_FIELDS:
        raise ValueError(f"Invalid field: {field}")
    con = sqlite3.connect(_DB_PATH)
    con.execute(f"UPDATE db_panels SET {field} = ? WHERE name = ?", (value, name))
    con.commit()
    con.close()
    _panels_cache = None


def swap_panel_order(name_a: str, name_b: str):
    """Swap sort_order values of two panels."""
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    row_a = con.execute("SELECT sort_order FROM db_panels WHERE name = ?", (name_a,)).fetchone()
    row_b = con.execute("SELECT sort_order FROM db_panels WHERE name = ?", (name_b,)).fetchone()
    if row_a and row_b:
        con.execute("UPDATE db_panels SET sort_order = ? WHERE name = ?", (row_b[0], name_a))
        con.execute("UPDATE db_panels SET sort_order = ? WHERE name = ?", (row_a[0], name_b))
        con.commit()
    con.close()
    _panels_cache = None


def rename_db_panel(old_name: str, new_name: str):
    """Rename a panel (update primary key)."""
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute("UPDATE db_panels SET name = ? WHERE name = ?", (new_name, old_name))
    con.commit()
    con.close()
    _panels_cache = None


# ── Settings ─────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    global _settings_cache
    if _settings_cache is None:
        con = sqlite3.connect(_DB_PATH)
        rows = con.execute("SELECT key, value FROM settings").fetchall()
        con.close()
        _settings_cache = {r[0]: r[1] for r in rows}
    return _settings_cache.get(key, default)


def set_setting(key: str, value: str):
    global _settings_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    con.commit()
    con.close()
    if _settings_cache is not None:
        _settings_cache[key] = value


# ── User Profiles ────────────────────────────────────────────────────────────

def upsert_user_profile(uid: int, first_name: str, last_name: str,
                         username: str, phone: str, bio: str):
    now = time.time()
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO user_profiles (user_id, first_name, last_name, username, phone, bio, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET"
        " first_name=excluded.first_name, last_name=excluded.last_name,"
        " username=excluded.username, phone=excluded.phone,"
        " bio=excluded.bio, updated_at=excluded.updated_at",
        (uid, first_name, last_name, username, phone, bio, now),
    )
    con.commit()
    con.close()
    _profiles_cache[uid] = (first_name, last_name, username, phone, bio)
    _profile_ts_cache[uid] = now


def get_user_profile(uid: int) -> dict | None:
    if uid in _profiles_cache:
        first, last, user, phone, bio = _profiles_cache[uid]
        return {"first_name": first, "last_name": last, "username": user,
                "phone": phone, "bio": bio}
    con = sqlite3.connect(_DB_PATH)
    row = con.execute(
        "SELECT first_name, last_name, username, phone, bio, updated_at"
        " FROM user_profiles WHERE user_id = ?", (uid,)
    ).fetchone()
    con.close()
    if not row:
        return None
    _profiles_cache[uid] = row[:5]
    _profile_ts_cache[uid] = row[5]
    return {"first_name": row[0], "last_name": row[1], "username": row[2],
            "phone": row[3], "bio": row[4]}


def get_profile_updated_at(uid: int) -> float:
    if uid in _profile_ts_cache:
        return _profile_ts_cache[uid]
    con = sqlite3.connect(_DB_PATH)
    row = con.execute(
        "SELECT updated_at FROM user_profiles WHERE user_id = ?", (uid,)
    ).fetchone()
    con.close()
    if row:
        _profile_ts_cache[uid] = row[0]
        return row[0]
    return 0.0


def get_all_user_profiles() -> list[tuple[int, str, str, str, str, str]]:
    """Return all (user_id, first_name, last_name, username, phone, bio)."""
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute(
        "SELECT user_id, first_name, last_name, username, phone, bio"
        " FROM user_profiles ORDER BY user_id"
    ).fetchall()
    con.close()
    return rows


# ── Activity Log ─────────────────────────────────────────────────────────────

def log_activity(uid: int, action: str, detail: str = ""):
    lang = _lang_cache.get(uid) or get_user_lang(uid) or ""
    if detail:
        try:
            d = json.loads(detail)
            d["lang"] = lang
            detail = json.dumps(d)
        except (json.JSONDecodeError, TypeError):
            pass
    else:
        detail = json.dumps({"lang": lang})
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO activity_log (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (uid, action, detail, time.time()),
    )
    con.commit()
    con.close()
