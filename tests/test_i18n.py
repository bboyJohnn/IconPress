import ast
import json
import string
from pathlib import Path

import pytest

from iconpress import i18n

PACKAGE = Path(i18n.__file__).parent
ENGLISH = i18n.catalog("en")


def keys_used_in_code() -> set[str]:
    keys = set()
    for source in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("tr", "ntr", "N_") and node.args
                    and isinstance(node.args[0], ast.Constant)):
                keys.add(node.args[0].value)
    return keys


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def forms(entry) -> dict[str, str]:
    return entry if isinstance(entry, dict) else {"other": entry}


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.set_language("en")


def test_english_catalog_matches_the_code():
    used = keys_used_in_code()
    assert used - set(ENGLISH) == set(), "texts in the code missing from en.json"
    assert set(ENGLISH) - used == set(), "texts in en.json no longer used in the code"


def test_every_language_has_a_file():
    files = {path.stem for path in (PACKAGE / "locales").glob("*.json")}
    assert files == set(i18n.LANGUAGES)


@pytest.mark.parametrize("code", sorted(i18n.LANGUAGES))
def test_translation_is_complete_and_well_formed(code):
    with open(PACKAGE / "locales" / f"{code}.json", encoding="utf-8") as file:
        table = json.load(file)
    assert set(table) == set(ENGLISH)
    needed = {i18n.plural_category(code, n) for n in range(0, 1000)}
    for key, english in ENGLISH.items():
        entry = table[key]
        assert isinstance(entry, dict) == isinstance(english, dict), key
        expected = placeholders(key)
        for category, text in forms(entry).items():
            assert text.strip(), (key, category)
            found = placeholders(text)
            assert found <= expected, (key, category, found)
            # The count may be spelled out ("one file", "two files") but nothing else may be lost
            assert expected - {"n"} <= found, (key, category, found)
            if not isinstance(entry, dict) or category == "other":
                assert expected <= found, (key, category, found)
        if isinstance(entry, dict):
            assert needed <= set(entry), (key, needed - set(entry))


@pytest.mark.parametrize(("code", "cases"), [
    ("en", {0: "other", 1: "one", 2: "other"}),
    ("ru", {1: "one", 2: "few", 5: "many", 11: "many", 21: "one", 22: "few", 112: "many"}),
    ("uk", {1: "one", 3: "few", 14: "many", 101: "one"}),
    ("pl", {1: "one", 2: "few", 5: "many", 21: "many", 22: "few", 12: "many"}),
    ("fr", {0: "one", 1: "one", 2: "other"}),
    ("ar", {0: "zero", 1: "one", 2: "two", 3: "few", 10: "few", 11: "many", 99: "many",
            100: "other", 102: "other"}),
    ("ja", {1: "other", 5: "other"}),
])
def test_plural_rules(code, cases):
    assert {n: i18n.plural_category(code, n) for n in cases} == cases


def test_plural_forms_in_use():
    i18n.set_language("ru")
    assert i18n.ntr("Saved {n} files.", 21) == "Сохранён 21 файл."
    assert i18n.ntr("Saved {n} files.", 3) == "Сохранено 3 файла."
    assert i18n.ntr("Saved {n} files.", 11) == "Сохранено 11 файлов."
    i18n.set_language("ar")
    assert i18n.ntr("Saved {n} files.", 2) == "تم حفظ ملفين."


def test_fallbacks_and_numbers():
    assert i18n.set_language("xx") == "en"
    assert i18n.tr("Not a known text") == "Not a known text"
    assert i18n.number(1.25, 1) == "1.2"
    i18n.set_language("de")
    assert i18n.tr("Quality") == "Qualität"
    assert i18n.number(52.94) == "52,9"


@pytest.mark.parametrize(("names", "expected"), [
    (["ru-RU"], "ru"),
    (["zh-Hans-CN"], "zh_CN"),
    (["zh-Hant-TW"], "zh_TW"),
    (["zh-HK"], "zh_TW"),
    (["pt-PT"], "pt"),
    (["xx-XX", "de-AT"], "de"),
    (["in-ID"], "id"),
    ([], "en"),
])
def test_match_system_locale(names, expected):
    assert i18n.match(names) == expected
