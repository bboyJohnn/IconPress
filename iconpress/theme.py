"""Light and dark colour themes, applied as a palette plus one application style sheet."""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

from PySide6.QtCore import Qt, QTemporaryDir
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from . import i18n

LIGHT = {
    "window": "#f5f5f7",
    "card": "#ffffff",
    "border": "#e5e5ea",
    "text": "#1d1d1f",
    "muted": "#6e6e73",
    "accent": "#007aff",
    "accent_hover": "#0066d6",
    "accent_text": "#ffffff",
    "field": "#ffffff",
    "field_border": "#d2d2d7",
    "hover": "#f2f2f5",
    "segment": "#ececf0",
    "segment_active": "#ffffff",
    "selection": "rgba(0, 122, 255, 0.14)",
    "checker_a": "#ffffff",
    "checker_b": "#eeeef2",
    "success_bg": "rgba(52, 199, 89, 0.14)",
    "success": "#248a3d",
    "error_bg": "rgba(255, 59, 48, 0.12)",
    "error": "#d70015",
}

DARK = {
    "window": "#1c1c1e",
    "card": "#2c2c2e",
    "border": "#3a3a3c",
    "text": "#f5f5f7",
    "muted": "#98989d",
    "accent": "#0a84ff",
    "accent_hover": "#409cff",
    "accent_text": "#ffffff",
    "field": "#1c1c1e",
    "field_border": "#48484a",
    "hover": "#3a3a3c",
    "segment": "#1c1c1e",
    "segment_active": "#48484a",
    "selection": "rgba(10, 132, 255, 0.28)",
    "checker_a": "#2c2c2e",
    "checker_b": "#353538",
    "success_bg": "rgba(48, 209, 88, 0.16)",
    "success": "#30d158",
    "error_bg": "rgba(255, 69, 58, 0.16)",
    "error": "#ff6961",
}

_STYLE = Template("""
QWidget { color: $text; }
#root { background: $window; }
QLabel { background: transparent; }
#title { font-size: 20px; font-weight: 700; }
#subtitle, #muted, #hint { color: $muted; }
#hint { font-size: 12px; }
#sectionTitle { font-weight: 600; }
#value { color: $muted; }

#card { background: $card; border: 1px solid $border; border-radius: 14px; }

QPushButton {
    background: $field; border: 1px solid $field_border; border-radius: 7px; padding: 5px 12px;
}
QPushButton:hover { background: $hover; }
QPushButton:disabled { color: $muted; }
QPushButton#primary {
    background: $accent; color: $accent_text; border: none; border-radius: 10px;
    padding: 11px 16px; font-size: 14px; font-weight: 600;
}
QPushButton#primary:hover { background: $accent_hover; }
QPushButton#primary:disabled { background: $border; color: $muted; }
QPushButton#link { background: transparent; border: none; color: $accent; padding: 2px 4px; }
QPushButton#link:hover { color: $accent_hover; }
QPushButton#chip { border-radius: 13px; padding: 4px 0; }
QPushButton#chip:checked { background: $accent; border-color: $accent; color: $accent_text; }

#segmented { background: $segment; border-radius: 9px; }
#segmented QPushButton {
    background: transparent; border: none; border-radius: 7px; padding: 5px 12px; color: $muted;
}
#segmented QPushButton:hover { color: $text; }
#segmented QPushButton:checked { background: $segment_active; color: $text; font-weight: 600; }

QComboBox, QLineEdit {
    background: $field; border: 1px solid $field_border; border-radius: 7px; padding: 5px 9px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QComboBox:focus, QLineEdit:focus { border-color: $accent; }
QComboBox::drop-down {
    border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right;
}
QComboBox::down-arrow { image: url("$chevron"); width: 10px; height: 6px; }
QComboBox::down-arrow:disabled { image: url("$chevron_disabled"); }
QComboBox QAbstractItemView {
    background: $card; border: 1px solid $border; outline: none; padding: 4px;
    selection-background-color: $accent; selection-color: $accent_text;
}

QSlider::groove:horizontal { height: 4px; background: $field_border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $slider_left; border-radius: 2px; }
QSlider::add-page:horizontal { background: $slider_right; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #ffffff; border: 1px solid $field_border; width: 16px; height: 16px;
    margin: -7px 0; border-radius: 9px;
}
QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled,
QSlider::add-page:horizontal:disabled { background: $border; }

QProgressBar { background: $segment; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }

QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 9px; padding: 4px; color: $text; }
QListWidget::item:hover { background: $hover; }
QListWidget::item:selected { background: $selection; color: $text; }

QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar { background: transparent; margin: 2px; }
QScrollBar:vertical { width: 10px; }
QScrollBar:horizontal { height: 10px; }
QScrollBar::handle { background: $field_border; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

#banner { border-radius: 10px; }
#banner[kind="ok"] { background: $success_bg; }
#banner[kind="ok"] QLabel#bannerText { color: $success; font-weight: 600; }
#banner[kind="error"] { background: $error_bg; }
#banner[kind="error"] QLabel#bannerText { color: $error; font-weight: 600; }

QToolTip {
    background: $card; color: $text; border: 1px solid $border; padding: 4px 6px;
    border-radius: 6px;
}
QMenu { background: $card; border: 1px solid $border; padding: 4px; border-radius: 8px; }
QMenu::item { padding: 5px 18px; border-radius: 5px; }
QMenu::item:selected { background: $accent; color: $accent_text; }
""")

_current = dict(LIGHT)
_assets: QTemporaryDir | None = None


def color(name: str) -> QColor:
    """A colour of the active theme, for widgets that paint themselves."""
    return QColor(_current[name])


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hasattr(hints, "colorScheme") and hints.colorScheme() == Qt.ColorScheme.Dark


def apply(app: QApplication, mode: str) -> None:
    """Apply ``"light"``, ``"dark"`` or ``"auto"`` (follow the operating system)."""
    global _current
    dark = mode == "dark" or (mode == "auto" and system_is_dark())
    _current = DARK if dark else LIGHT
    app.setPalette(_palette(_current))
    # Style sheets draw sub-page left of the handle and add-page right of it whatever the
    # text direction, so the filled part has to swap sides for right-to-left languages
    fill, rest = _current["accent"], _current["field_border"]
    left, right = (rest, fill) if i18n.is_rtl() else (fill, rest)
    app.setStyleSheet(_STYLE.substitute(_current, chevron=_chevron(_current["muted"]),
                                        chevron_disabled=_chevron(_current["border"]),
                                        slider_left=left, slider_right=right))


def _chevron(colour: str) -> str:
    """Path to a down-arrow SVG in ``colour``; style sheets can only load images from files."""
    global _assets
    if _assets is None:
        _assets = QTemporaryDir()  # private to this process and removed on exit
    path = Path(_assets.path()) / f"chevron-{colour.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6">'
            f'<path d="M1 1l4 4 4-4" fill="none" stroke="{colour}" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    return path.as_posix()


def _palette(t: dict) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: t["window"],
        QPalette.ColorRole.WindowText: t["text"],
        QPalette.ColorRole.Base: t["field"],
        QPalette.ColorRole.AlternateBase: t["hover"],
        QPalette.ColorRole.Text: t["text"],
        QPalette.ColorRole.PlaceholderText: t["muted"],
        QPalette.ColorRole.Button: t["card"],
        QPalette.ColorRole.ButtonText: t["text"],
        QPalette.ColorRole.Highlight: t["accent"],
        QPalette.ColorRole.HighlightedText: t["accent_text"],
        QPalette.ColorRole.ToolTipBase: t["card"],
        QPalette.ColorRole.ToolTipText: t["text"],
        QPalette.ColorRole.Link: t["accent"],
        QPalette.ColorRole.Mid: t["field_border"],
        QPalette.ColorRole.Midlight: t["border"],
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    disabled = QColor(t["muted"])
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def default_font() -> QFont:
    """The platform UI font (San Francisco, Segoe UI); Inter or Noto on Linux if installed."""
    font = QApplication.font()
    if sys.platform not in ("darwin", "win32"):
        families = set(QFontDatabase.families())
        for name in ("Inter", "Noto Sans", "Cantarell", "Ubuntu"):
            if name in families:
                font.setFamily(name)
                break
    font.setPixelSize(13)
    return font
