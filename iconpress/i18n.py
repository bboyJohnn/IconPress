"""Interface translations.

Each language is a JSON file in ``iconpress/locales``. Keys are the English texts used in
the code. A value is either the translated string or, for texts that contain a count, an
object with CLDR plural forms (``zero``, ``one``, ``two``, ``few``, ``many``, ``other``).
Missing entries fall back to English. Placeholders such as ``{n}`` are filled with
``str.format``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from pathlib import Path

LOCALES_DIR = Path(__file__).with_name("locales")
DEFAULT = "en"

# Shown in the language menu by their own names, in this order
LANGUAGES = {
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "id": "Bahasa Indonesia",
    "it": "Italiano",
    "nl": "Nederlands",
    "pl": "Polski",
    "pt": "Português (Brasil)",
    "vi": "Tiếng Việt",
    "tr": "Türkçe",
    "ru": "Русский",
    "uk": "Українська",
    "ar": "العربية",
    "hi": "हिन्दी",
    "ja": "日本語",
    "ko": "한국어",
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文",
}
RTL = {"ar"}
_DECIMAL_COMMA = {"de", "es", "fr", "id", "it", "nl", "pl", "pt", "ru", "tr", "uk", "vi"}

_language = DEFAULT


def N_(text: str) -> str:  # noqa: N802 (gettext convention)
    """Mark a text for translation where it is defined; translate it with ``tr`` when used."""
    return text


@cache
def catalog(code: str) -> dict:
    with open(LOCALES_DIR / f"{code}.json", encoding="utf-8") as file:
        return json.load(file)


def set_language(code: str) -> str:
    global _language
    _language = code if code in LANGUAGES else DEFAULT
    return _language


def language() -> str:
    return _language


def is_rtl(code: str | None = None) -> bool:
    return (code or _language) in RTL


def match(preferred: Iterable[str]) -> str:
    """The best available language for locale names like ``ru-RU`` or ``zh-Hant-TW``."""
    for name in preferred:
        parts = name.replace("-", "_").split("_")
        base = parts[0].lower()
        if base == "zh":
            traditional = any(part in ("Hant", "TW", "HK", "MO") for part in parts[1:])
            return "zh_TW" if traditional else "zh_CN"
        if base == "in":  # old code for Indonesian
            base = "id"
        if base in LANGUAGES:
            return base
    return DEFAULT


def plural_category(code: str, n: int) -> str:
    """CLDR plural category of a whole number ``n`` in language ``code``."""
    base = code.split("_")[0]
    n = abs(n)
    if base in ("ru", "uk"):
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if base == "pl":
        if n == 1:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if base == "ar":
        if n in (0, 1, 2):
            return ("zero", "one", "two")[n]
        if 3 <= n % 100 <= 10:
            return "few"
        if 11 <= n % 100 <= 99:
            return "many"
        return "other"
    if base in ("fr", "pt", "hi"):
        return "one" if n in (0, 1) else "other"
    if base in ("id", "ja", "ko", "vi", "zh"):
        return "other"
    return "one" if n == 1 else "other"


def _lookup(text: str):
    entry = catalog(_language).get(text)
    return entry if entry is not None else catalog(DEFAULT).get(text, text)


def tr(text: str, **values) -> str:
    entry = _lookup(text)
    if isinstance(entry, dict):
        entry = entry.get("other", text)
    return entry.format(**values) if values else entry


def ntr(text: str, n: int, **values) -> str:
    """Translate a text that contains the count ``{n}``, picking the right plural form."""
    entry = _lookup(text)
    if isinstance(entry, dict):
        entry = entry.get(plural_category(_language, n)) or entry.get("other", text)
    return entry.format(n=n, **values)


def number(value: float, decimals: int = 1) -> str:
    text = f"{value:.{decimals}f}"
    return text.replace(".", ",") if _language.split("_")[0] in _DECIMAL_COMMA else text
