import tomllib
from pathlib import Path

from db import get_user_lang

_TRANSLATIONS_DIR = Path(__file__).parent / "translations"

LANGUAGES: dict[str, str] = {
    "en": "\U0001f1ec\U0001f1e7 English",
    "fa": "\U0001f1ee\U0001f1f7 \u0641\u0627\u0631\u0633\u06cc",
    "ru": "\U0001f1f7\U0001f1fa \u0420\u0443\u0441\u0441\u043a\u0438\u0439",
}

RTL_LANGS = {"fa"}

_strings: dict[str, dict[str, str]] = {}

for toml_file in _TRANSLATIONS_DIR.glob("*.toml"):
    lang = toml_file.stem
    _strings[lang] = tomllib.loads(toml_file.read_text("utf-8"))


def t(key: str, uid: int, **kwargs) -> str:
    lang = get_user_lang(uid) or "en"
    text = _strings.get(lang, {}).get(key)
    if text is None:
        text = _strings.get("en", {}).get(key)
    if text is None:
        return key
    if kwargs:
        text = text.format(**kwargs)
    return text


def is_rtl(uid: int) -> bool:
    lang = get_user_lang(uid) or "en"
    return lang in RTL_LANGS
