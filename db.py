import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import os as _os
_prog = _os.environ.get("STATICX_PROG_PATH")
if _prog:
    _DB_PATH = str(Path(_prog).resolve().parent / "3x-bot.db")
elif getattr(sys, "frozen", False):
    _DB_PATH = str(Path(sys.executable).resolve().parent / "3x-bot.db")
else:
    _DB_PATH = str(Path(__file__).resolve().parent / "3x-bot.db")


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class Admin:
    perms: set[str]
    is_owner: bool
    panels: set[str]
    inbounds: dict[str, set[int] | None]


@dataclass
class Panel:
    name: str
    url: str
    username: str
    password: str
    proxy: str
    sub_url: str
    added_by: int
    created_at: float
    sort_order: int
    secret_token: str = ""


@dataclass
class UserProfile:
    first_name: str
    last_name: str
    username: str
    phone: str
    bio: str


# ── Caches ───────────────────────────────────────────────────────────────────

_lang_cache: dict[int, str] = {}
_admins_cache: dict[int, Admin] | None = None
_panels_cache: list[Panel] | None = None
_settings_cache: dict[str, str] | None = None
_profiles_cache: dict[int, UserProfile] = {}
_profile_ts_cache: dict[int, float] = {}
_plans_cache: list[dict] | None = None


# ── Migration System ────────────────────────────────────────────────────────

_BASE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        language TEXT NOT NULL DEFAULT 'en',
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS db_admins (
        user_id     INTEGER PRIMARY KEY,
        permissions TEXT NOT NULL DEFAULT '[]',
        is_owner    INTEGER NOT NULL DEFAULT 0,
        added_by    INTEGER NOT NULL,
        created_at  REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS db_panels (
        name        TEXT PRIMARY KEY,
        url         TEXT NOT NULL,
        username    TEXT NOT NULL,
        password    TEXT NOT NULL,
        proxy       TEXT NOT NULL DEFAULT '',
        sub_url     TEXT NOT NULL DEFAULT '',
        added_by    INTEGER NOT NULL,
        created_at  REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_profiles (
        user_id    INTEGER PRIMARY KEY,
        first_name TEXT NOT NULL DEFAULT '',
        last_name  TEXT NOT NULL DEFAULT '',
        username   TEXT NOT NULL DEFAULT '',
        phone      TEXT NOT NULL DEFAULT '',
        bio        TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS activity_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        action     TEXT NOT NULL,
        detail     TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(created_at)",
]


def _m1_admins_panels(con):
    try:
        con.execute("ALTER TABLE db_admins ADD COLUMN panels TEXT NOT NULL DEFAULT '[\"*\"]'")
    except sqlite3.OperationalError:
        pass


def _m2_admins_inbounds(con):
    try:
        con.execute("ALTER TABLE db_admins ADD COLUMN inbounds TEXT NOT NULL DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass


def _m3_panels_sort_order(con):
    try:
        con.execute("ALTER TABLE db_panels ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


def _m4_profiles_first_seen(con):
    try:
        con.execute("ALTER TABLE user_profiles ADD COLUMN first_seen REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


def _m5_settings_updated_at(con):
    try:
        con.execute("ALTER TABLE settings ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


def _m6_csv_to_json(con):
    """Convert admin permissions and panels from CSV to JSON arrays."""
    rows = con.execute("SELECT user_id, permissions, panels FROM db_admins").fetchall()
    for uid, perms_csv, panels_csv in rows:
        # Skip if already JSON
        if perms_csv.startswith("[") and panels_csv.startswith("["):
            continue
        perms = [p for p in perms_csv.split(",") if p] if perms_csv else []
        panels_list = [p for p in panels_csv.split(",") if p] if panels_csv else ["*"]
        con.execute(
            "UPDATE db_admins SET permissions = ?, panels = ? WHERE user_id = ?",
            (json.dumps(sorted(perms)), json.dumps(sorted(panels_list)), uid),
        )
    # Convert public_permissions setting
    row = con.execute("SELECT value FROM settings WHERE key = 'public_permissions'").fetchone()
    if row and row[0] and not row[0].startswith("["):
        perms = [p for p in row[0].split(",") if p]
        con.execute(
            "UPDATE settings SET value = ? WHERE key = 'public_permissions'",
            (json.dumps(sorted(perms)),),
        )
    # Convert public_panels setting
    row = con.execute("SELECT value FROM settings WHERE key = 'public_panels'").fetchone()
    if row and row[0] and not row[0].startswith("["):
        panels_list = [p for p in row[0].split(",") if p]
        if not panels_list:
            panels_list = ["*"]
        con.execute(
            "UPDATE settings SET value = ? WHERE key = 'public_panels'",
            (json.dumps(sorted(panels_list)),),
        )


def _m7_plans_table(con):
    """Create account_plans table and migrate data from settings."""
    con.execute(
        """CREATE TABLE IF NOT EXISTS account_plans (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            traffic    REAL NOT NULL DEFAULT 0,
            days       INTEGER NOT NULL DEFAULT 0,
            sau        INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )"""
    )
    row = con.execute("SELECT value FROM settings WHERE key = 'account_plans'").fetchone()
    if row and row[0]:
        try:
            plans = json.loads(row[0])
            now = time.time()
            for i, plan in enumerate(plans):
                con.execute(
                    "INSERT INTO account_plans (name, traffic, days, sau, sort_order, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (plan["name"], plan.get("traffic", 0), plan.get("days", 0),
                     int(plan.get("sau", False)), i, now),
                )
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        con.execute("DELETE FROM settings WHERE key = 'account_plans'")


def _m8_test_account_table(con):
    """Create test_account_config table and migrate data from settings."""
    con.execute(
        """CREATE TABLE IF NOT EXISTS test_account_config (
            id       INTEGER PRIMARY KEY CHECK (id = 1),
            method   TEXT NOT NULL DEFAULT 'r',
            prefix   TEXT NOT NULL DEFAULT '',
            postfix  TEXT NOT NULL DEFAULT '',
            traffic  REAL NOT NULL DEFAULT 0,
            days     INTEGER NOT NULL DEFAULT 0,
            sau      INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )"""
    )
    row = con.execute("SELECT value FROM settings WHERE key = 'test_account'").fetchone()
    if row and row[0]:
        try:
            ta = json.loads(row[0])
            con.execute(
                "INSERT INTO test_account_config (id, method, prefix, postfix, traffic, days, sau, updated_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (ta.get("method", "r"), ta.get("prefix", ""), ta.get("postfix", ""),
                 ta.get("traffic", 0), ta.get("days", 0), int(ta.get("sau", False)),
                 time.time()),
            )
        except (json.JSONDecodeError, TypeError):
            pass
        con.execute("DELETE FROM settings WHERE key = 'test_account'")


def _m9_activity_log_columns(con):
    """Add structured columns to activity_log for analytics."""
    for stmt in [
        "ALTER TABLE activity_log ADD COLUMN panel_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE activity_log ADD COLUMN inbound_id INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE activity_log ADD COLUMN email TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE activity_log ADD COLUMN lang TEXT NOT NULL DEFAULT ''",
    ]:
        try:
            con.execute(stmt)
        except sqlite3.OperationalError:
            pass
    con.execute("CREATE INDEX IF NOT EXISTS idx_activity_panel ON activity_log(panel_name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_log(action)")


def _m10_panels_secret_token(con):
    try:
        con.execute("ALTER TABLE db_panels ADD COLUMN secret_token TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass


_MIGRATIONS = [
    _m1_admins_panels,
    _m2_admins_inbounds,
    _m3_panels_sort_order,
    _m4_profiles_first_seen,
    _m5_settings_updated_at,
    _m6_csv_to_json,
    _m7_plans_table,
    _m8_test_account_table,
    _m9_activity_log_columns,
    _m10_panels_secret_token,
]


def _run_migrations(con):
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    current = con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    for i, migration in enumerate(_MIGRATIONS, 1):
        if i <= current:
            continue
        migration(con)
        con.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
    con.commit()


# ── Init ─────────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(_DB_PATH)
    for stmt in _BASE_SCHEMA:
        con.execute(stmt)
    con.commit()
    _run_migrations(con)
    con.close()


# ── User Language ────────────────────────────────────────────────────────────

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


# ── Inbound JSON Helpers ─────────────────────────────────────────────────────

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


# ── JSON List Helpers ────────────────────────────────────────────────────────

def _parse_json_set(raw: str) -> set[str]:
    """Parse a JSON array string into a set. Returns empty set on failure."""
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return set()


def _serialize_set(s: set[str]) -> str:
    """Serialize a set to a JSON array string."""
    return json.dumps(sorted(s))


# ── DB Admins ────────────────────────────────────────────────────────────────

def get_db_admins() -> dict[int, Admin]:
    global _admins_cache
    if _admins_cache is not None:
        return _admins_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute("SELECT user_id, permissions, is_owner, panels, inbounds FROM db_admins").fetchall()
    con.close()
    result: dict[int, Admin] = {}
    for uid, perms_str, is_owner, panels_str, inbounds_str in rows:
        perms = _parse_json_set(perms_str)
        admin_panels = _parse_json_set(panels_str) or {"*"}
        inbounds = _parse_inbounds_json(inbounds_str)
        result[uid] = Admin(perms, bool(is_owner), admin_panels, inbounds)
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
        (uid, _serialize_set(perms), int(is_owner), added_by, time.time(),
         _serialize_set(admin_panels), _serialize_inbounds(admin_inbounds)),
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
        (_serialize_set(perms), uid),
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
        (_serialize_set(panel_names) if panel_names else '["*"]', uid),
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
        pset = _parse_json_set(panels_str) or {"*"}
        if old in pset:
            pset.discard(old)
            pset.add(new)
            con.execute("UPDATE db_admins SET panels = ? WHERE user_id = ?",
                        (_serialize_set(pset), uid))
        ib = _parse_inbounds_json(inbounds_str)
        if old in ib:
            ib[new] = ib.pop(old)
            con.execute("UPDATE db_admins SET inbounds = ? WHERE user_id = ?",
                        (_serialize_inbounds(ib), uid))
    con.commit()
    con.close()
    _admins_cache = None


def remove_panel_from_admins(name: str):
    """Remove a panel from all admin records (panels + inbounds)."""
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute("SELECT user_id, panels, inbounds FROM db_admins").fetchall()
    for uid, panels_str, inbounds_str in rows:
        pset = _parse_json_set(panels_str) or {"*"}
        if name in pset:
            pset.discard(name)
            if not pset:
                pset = {"*"}
            con.execute("UPDATE db_admins SET panels = ? WHERE user_id = ?",
                        (_serialize_set(pset), uid))
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
        pset = _parse_json_set(row[0]) or {"*"}
        if old in pset:
            pset.discard(old)
            pset.add(new)
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_panels'",
                        (_serialize_set(pset),))
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
        pset = _parse_json_set(row[0]) or {"*"}
        if name in pset:
            pset.discard(name)
            if not pset:
                pset = {"*"}
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_panels'",
                        (_serialize_set(pset),))
    row2 = con.execute("SELECT value FROM settings WHERE key = 'public_inbounds'").fetchone()
    if row2:
        ib = _parse_inbounds_json(row2[0])
        if name in ib:
            del ib[name]
            con.execute("UPDATE settings SET value = ? WHERE key = 'public_inbounds'",
                        (_serialize_inbounds(ib),))
    con.execute("DELETE FROM settings WHERE key = ?", (f"panel_auto_backup:{name}",))
    con.commit()
    _settings_cache = None
    con.close()


# ── DB Panels ────────────────────────────────────────────────────────────────

def get_db_panels() -> list[Panel]:
    global _panels_cache
    if _panels_cache is not None:
        return _panels_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute(
        "SELECT name, url, username, password, proxy, sub_url, added_by, created_at, sort_order,"
        " COALESCE(secret_token, '') FROM db_panels ORDER BY sort_order, created_at"
    ).fetchall()
    con.close()
    result = [Panel(*r) for r in rows]
    _panels_cache = result
    return result


def add_db_panel(name: str, url: str, username: str, password: str,
                 proxy: str, sub_url: str, added_by: int, secret_token: str = ""):
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    max_order = con.execute("SELECT COALESCE(MAX(sort_order), -1) FROM db_panels").fetchone()[0]
    con.execute(
        "INSERT INTO db_panels (name, url, username, password, proxy, sub_url, added_by, created_at, sort_order, secret_token)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, url, username, password, proxy, sub_url, added_by, time.time(), max_order + 1, secret_token),
    )
    con.commit()
    con.close()
    _panels_cache = None


def get_db_panel(name: str) -> Panel | None:
    """Get a single panel by name (from cache)."""
    for p in get_db_panels():
        if p.name == name:
            return p
    return None


def remove_db_panel(name: str):
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute("DELETE FROM db_panels WHERE name = ?", (name,))
    con.commit()
    con.close()
    _panels_cache = None


_PANEL_FIELDS = {"url", "username", "password", "proxy", "sub_url", "secret_token"}


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
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, time.time()),
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
        "INSERT INTO user_profiles (user_id, first_name, last_name, username, phone, bio, updated_at, first_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET"
        " first_name=excluded.first_name, last_name=excluded.last_name,"
        " username=excluded.username, phone=excluded.phone,"
        " bio=excluded.bio, updated_at=excluded.updated_at",
        (uid, first_name, last_name, username, phone, bio, now, now),
    )
    con.commit()
    con.close()
    _profiles_cache[uid] = UserProfile(first_name, last_name, username, phone, bio)
    _profile_ts_cache[uid] = now


def get_user_profile(uid: int) -> UserProfile | None:
    if uid in _profiles_cache:
        return _profiles_cache[uid]
    con = sqlite3.connect(_DB_PATH)
    row = con.execute(
        "SELECT first_name, last_name, username, phone, bio, updated_at"
        " FROM user_profiles WHERE user_id = ?", (uid,)
    ).fetchone()
    con.close()
    if not row:
        return None
    prof = UserProfile(row[0], row[1], row[2], row[3], row[4])
    _profiles_cache[uid] = prof
    _profile_ts_cache[uid] = row[5]
    return prof


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


def get_all_user_profiles() -> list[tuple[int, UserProfile, float]]:
    """Return all (user_id, UserProfile, first_seen)."""
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute(
        "SELECT user_id, first_name, last_name, username, phone, bio, first_seen"
        " FROM user_profiles ORDER BY user_id"
    ).fetchall()
    con.close()
    return [(r[0], UserProfile(r[1], r[2], r[3], r[4], r[5]), r[6]) for r in rows]


# ── Account Plans ────────────────────────────────────────────────────────────

def get_plans() -> list[dict]:
    """Return all plans as list of dicts with id, name, traffic, days, sau."""
    global _plans_cache
    if _plans_cache is not None:
        return _plans_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute(
        "SELECT id, name, traffic, days, sau FROM account_plans ORDER BY sort_order, id"
    ).fetchall()
    con.close()
    result = [
        {"id": r[0], "name": r[1], "traffic": r[2], "days": r[3], "sau": bool(r[4])}
        for r in rows
    ]
    _plans_cache = result
    return result


def get_plan(plan_id: int) -> dict | None:
    """Return a single plan by ID."""
    for p in get_plans():
        if p["id"] == plan_id:
            return p
    return None


def add_plan(name: str, traffic: float, days: int, sau: bool) -> int:
    """Add a plan, return its ID."""
    global _plans_cache
    con = sqlite3.connect(_DB_PATH)
    max_order = con.execute("SELECT COALESCE(MAX(sort_order), -1) FROM account_plans").fetchone()[0]
    cur = con.execute(
        "INSERT INTO account_plans (name, traffic, days, sau, sort_order, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name, traffic, days, int(sau), max_order + 1, time.time()),
    )
    plan_id = cur.lastrowid
    con.commit()
    con.close()
    _plans_cache = None
    return plan_id


def update_plan(plan_id: int, **kwargs):
    """Update plan fields. Accepted keys: name, traffic, days, sau."""
    global _plans_cache
    allowed = {"name", "traffic", "days", "sau"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    if "sau" in fields:
        fields["sau"] = int(fields["sau"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [plan_id]
    con = sqlite3.connect(_DB_PATH)
    con.execute(f"UPDATE account_plans SET {sets} WHERE id = ?", vals)
    con.commit()
    con.close()
    _plans_cache = None


def remove_plan(plan_id: int):
    """Remove a plan by ID."""
    global _plans_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute("DELETE FROM account_plans WHERE id = ?", (plan_id,))
    con.commit()
    con.close()
    _plans_cache = None


# ── Activity Log ─────────────────────────────────────────────────────────────

def log_activity(uid: int, action: str, detail: str = "", *,
                 panel_name: str = "", inbound_id: int = 0, email: str = ""):
    lang = _lang_cache.get(uid) or get_user_lang(uid) or ""

    # Auto-extract structured fields from detail JSON if not explicitly provided
    if detail and not (panel_name or email or inbound_id):
        try:
            d = json.loads(detail)
            if not panel_name:
                panel_name = d.get("panel", "") or d.get("name", "")
            if not email:
                email = d.get("email", "")
            if not inbound_id:
                inbound_id = d.get("inbound", 0) or d.get("inbound_id", 0)
        except (json.JSONDecodeError, TypeError):
            pass

    # Keep lang in detail JSON for backward compat
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
        "INSERT INTO activity_log (user_id, action, detail, created_at, panel_name, inbound_id, email, lang)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, action, detail, time.time(), panel_name, inbound_id, email, lang),
    )
    con.commit()
    con.close()
