import sqlite3
import time

_DB_PATH = "users.db"
_lang_cache: dict[int, str] = {}
_admins_cache: dict[int, tuple[set[str], bool]] | None = None
_panels_cache: list[dict] | None = None
_settings_cache: dict[str, str] | None = None


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

def get_db_admins() -> dict[int, tuple[set[str], bool]]:
    global _admins_cache
    if _admins_cache is not None:
        return _admins_cache
    con = sqlite3.connect(_DB_PATH)
    rows = con.execute("SELECT user_id, permissions, is_owner FROM db_admins").fetchall()
    con.close()
    result: dict[int, tuple[set[str], bool]] = {}
    for uid, perms_str, is_owner in rows:
        perms = set(perms_str.split(",")) if perms_str else set()
        perms.discard("")
        result[uid] = (perms, bool(is_owner))
    _admins_cache = result
    return result


def add_db_admin(uid: int, perms: set[str], is_owner: bool, added_by: int):
    global _admins_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO db_admins (user_id, permissions, is_owner, added_by, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (uid, ",".join(sorted(perms)), int(is_owner), added_by, time.time()),
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


# ── DB Panels ────────────────────────────────────────────────────────────────

def get_db_panels() -> list[dict]:
    global _panels_cache
    if _panels_cache is not None:
        return _panels_cache
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM db_panels").fetchall()
    con.close()
    result = [dict(r) for r in rows]
    _panels_cache = result
    return result


def add_db_panel(name: str, url: str, username: str, password: str,
                 proxy: str, sub_url: str, added_by: int):
    global _panels_cache
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO db_panels (name, url, username, password, proxy, sub_url, added_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, url, username, password, proxy, sub_url, added_by, time.time()),
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
