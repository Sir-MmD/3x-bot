import sqlite3
import time

_DB_PATH = "users.db"
_lang_cache: dict[int, str] = {}


def init_db():
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            language TEXT NOT NULL DEFAULT 'en',
            created_at REAL NOT NULL
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
