"""The IconPress main window."""

from __future__ import annotations

import io
import json
import sys
from collections import deque
from datetime import datetime
from functools import partial
from pathlib import Path

import shiboken6
from PIL import Image
from PySide6.QtCore import (
    QEvent,
    QLibraryInfo,
    QLocale,
    QObject,
    QRunnable,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    QTranslator,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import __version__, core, i18n, theme
from .i18n import N_, ntr, tr
from .widgets import (
    ColorButton,
    PositionPicker,
    PreviewView,
    SegmentedControl,
    Switch,
    app_icon_pixmap,
    pil_to_qimage,
)

PREVIEW_SIDE = 1200
ICON_PREVIEW_SIDE = 384
THUMB_SIDE = 112
PREVIEW_CACHE = 6
PANEL_WIDTH = 360

RESIZE_MODES = [
    (N_("Original size"), "none"), (N_("Longest side"), "longest"),
    (N_("Percentage"), "percent"), (N_("Width"), "width"), (N_("Height"), "height"),
    (N_("Fit in a box"), "box"),
]
RESIZE_DEFAULTS = {"longest": (1920, 0), "percent": (50, 0), "width": (1280, 0),
                   "height": (0, 720), "box": (1920, 1080)}
ICON_KINDS = [
    (N_("ICO — Windows"), "ico"), (N_("ICNS — macOS"), "icns"),
    (N_("PNG set — web & apps"), "png"), (N_("Favicon — website"), "favicon"),
    (N_("iOS — Xcode app icon"), "ios"), (N_("Android — launcher icons"), "android"),
]
KIND_NOTES = {
    "icns": N_("macOS icons always include every size from 16 to 1024 px."),
    "favicon": N_("favicon.ico, PNG icons, Apple touch icon, web app manifest and the HTML "
                  "to paste into your page."),
    "ios": N_("AppIcon.appiconset for Xcode: every iPhone and iPad size and the 1024 px "
              "App Store icon. iOS rounds the corners itself."),
    "android": N_("Launcher icons for every screen density, round and adaptive icons, and "
                  "the 512 px Play Store icon."),
}
FORMAT_HINTS = {
    "PNG": N_("Lossless. Keeps transparency and animation."),
    "JPEG": N_("Small photos. No transparency: transparent areas become white."),
    "WEBP": N_("Modern web format: small files, transparency and animation."),
    "AVIF": N_("Newest web format with the best compression. Keeps transparency."),
    "GIF": N_("256 colours. Keeps animation; good for simple graphics."),
    "TIFF": N_("Lossless, for print and archiving."),
    "BMP": N_("Uncompressed bitmap."),
}

# Every option in the window, with its default. Used for settings, presets and rebuilding
# the window when the language changes.
STATE_DEFAULTS = {
    "mode": "convert",
    "format": "PNG", "quality": 90, "target_on": False, "target_kb": 500,
    "background": "#ffffff",
    "resize_mode": "none", "resize_a": 1920, "resize_b": 1080, "enlarge": False,
    "rotate": 0, "flip_h": False, "flip_v": False,
    "keep_metadata": False, "keep_dates": False,
    "wm_on": False, "wm_kind": "text", "wm_text": "©", "wm_image": "",
    "wm_position": "bottom-right", "wm_size": 25, "wm_opacity": 70, "wm_color": "#ffffff",
    "icon_kind": "ico", "icon_sizes/ico": ",".join(map(str, core.ICO_SIZES)),
    "icon_sizes/png": "16,32,180,192,512",
    "fit": "pad", "radius": 0, "padding": 0, "icon_bg": "none",
    "icon_bg1": "#ffffff", "icon_bg2": "#0a84ff",
    "out_dir": "", "overwrite": False, "template": "{name}", "find": "", "replace": "",
}
BUILTIN_PRESETS = [
    (N_("For the web: WebP, 1920 px"),
     {"mode": "convert", "format": "WEBP", "quality": 80, "target_on": False,
      "resize_mode": "longest", "resize_a": 1920}),
    (N_("For email: JPEG under 500 KB"),
     {"mode": "convert", "format": "JPEG", "quality": 90, "target_on": True, "target_kb": 500,
      "resize_mode": "longest", "resize_a": 2560}),
    (N_("Thumbnails: JPEG, 512 px"),
     {"mode": "convert", "format": "JPEG", "quality": 85, "target_on": False,
      "resize_mode": "longest", "resize_a": 512}),
    (N_("Website favicon"), {"mode": "icon", "icon_kind": "favicon"}),
    (N_("iOS app icon"), {"mode": "icon", "icon_kind": "ios", "icon_bg": "color"}),
    (N_("Android app icon"), {"mode": "icon", "icon_kind": "android"}),
]


def human_size(size: float) -> str:
    if size < 1024:
        return ntr("{n} bytes", int(size))
    for unit in (N_("{size} KB"), N_("{size} MB")):
        size /= 1024
        if size < 1024:
            return tr(unit, size=i18n.number(size))
    return tr("{size} GB", size=i18n.number(size / 1024))


def to_int(value, default: int) -> int:
    """A setting as int; a hand-edited or damaged config must not stop the app starting."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce(value, default):
    """A stored setting converted to the type of its default."""
    if value is None:
        return default
    if isinstance(default, bool):
        return value is True or str(value).lower() in ("true", "1", "yes")
    if isinstance(default, int):
        return to_int(value, default)
    return str(value)


def isolate(parts: list[str], separator: str = "   ·   ") -> str:
    """Join parts so numbers and names keep their order in right-to-left languages too."""
    direction = "‏" if i18n.is_rtl() else "‎"
    return direction + separator.join(f"⁨{part}⁩" for part in parts)


class Worker(QObject):
    """Runs the batch on a thread pool, from a background thread."""

    progress = Signal(int, int)
    finished = Signal(object)

    def __init__(self, jobs: list[tuple[Path, Path]], work, keep_dates: bool):
        super().__init__()
        self._jobs = jobs
        self._work = work
        self._keep_dates = keep_dates
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        results = []
        batch = core.run_batch(self._jobs, self._work, keep_dates=self._keep_dates)
        try:
            for result in batch:
                results.append(result)
                self.progress.emit(len(results), len(self._jobs))
                if self._cancelled:
                    break
        finally:
            batch.close()  # files not started yet are skipped
        self.finished.emit(results)


class _PreviewSignals(QObject):
    done = Signal(int, object, object, int, object)
    failed = Signal(int, str)


class _CompareTask(QRunnable):
    """Encodes the current file with the current options to show the result and its size."""

    def __init__(self, signals: _PreviewSignals, generation: int, is_current, path: Path,
                 options: core.ConvertOptions):
        super().__init__()
        self.signals, self.generation, self.is_current = signals, generation, is_current
        self.path, self.options = path, options

    def run(self) -> None:
        if not self.is_current(self.generation):
            return  # the options changed again before this one started
        try:
            before, data = core.encode(self.path, self.options)
            with Image.open(io.BytesIO(data)) as decoded:
                after = decoded.convert("RGBA")
            size = before.size
            before = before.convert("RGBA")
            for image in (before, after):
                image.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), core.LANCZOS)
            self.signals.done.emit(self.generation, pil_to_qimage(before),
                                   pil_to_qimage(after), len(data), size)
        except Exception as exc:  # shown under the preview
            self.signals.failed.emit(self.generation, str(exc) or type(exc).__name__)


def card(title: str | None = None, switch: Switch | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(8)
    if title:
        header = QHBoxLayout()
        header.addWidget(label(title, "sectionTitle"))
        header.addStretch()
        if switch is not None:
            header.addWidget(switch)
        layout.addLayout(header)
    return frame, layout


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def row(*widgets, stretch_first: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for index, widget in enumerate(widgets):
        if widget is None:
            layout.addStretch()
        else:
            layout.addWidget(widget, 1 if stretch_first and index == 0 else 0)
    return layout


def boxed(layout: QHBoxLayout) -> QWidget:
    """A layout wrapped in a widget, so the whole row can be shown or hidden."""
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget


def slider(low: int, high: int) -> QSlider:
    widget = QSlider(Qt.Orientation.Horizontal)
    widget.setRange(low, high)
    return widget


def spin(low: int, high: int, suffix: str) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setSuffix(suffix)
    widget.setAccelerated(True)
    widget.lineEdit().setTextMargins(5, 0, 5, 0)  # style sheet padding misses the inner field
    return widget


def chip(text: str) -> QPushButton:
    button = QPushButton(text, objectName="chip")
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return button


def pixels_suffix() -> str:
    return " " + tr("{n} px", n="").strip()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings()
        self.files: list[Path] = []
        self._infos: dict[Path, core.ImageInfo] = {}
        self._previews: dict[Path, Image.Image] = {}
        self._thumbs: dict[Path, QIcon] = {}
        self._thumb_queue: deque[Path] = deque()
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._results: list = []
        self._icon_sizes = {kind: set() for kind in core.ICON_SIZES}
        self._resize_values = dict(RESIZE_DEFAULTS)
        self._theme_mode = "auto"
        self._stopping = False
        self._building = False
        self._banner_details = ""
        self._last_dir = str(Path.home())
        self._qt_translator = QTranslator(self)
        self._presets: dict[str, dict] = {}
        self._compare_generation = 0
        self._estimate: tuple | None = None

        self.setWindowTitle("IconPress")
        self.setWindowIcon(QIcon(app_icon_pixmap()))
        # Free the window (and its combo-box popups, which are separate windows) while the
        # QApplication still exists; PySide crashes if it has to delete them at exit.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        self.setMinimumSize(940, 640)
        self.resize(1120, 740)

        self._preview_timer = QTimer(self, singleShot=True, interval=30)
        self._preview_timer.timeout.connect(self._update_preview)
        self._compare_timer = QTimer(self, singleShot=True, interval=350)
        self._compare_timer.timeout.connect(self._start_compare)
        self._compare_pool = QThreadPool(self)
        self._compare_pool.setMaxThreadCount(1)
        self._preview_signals = _PreviewSignals(self)
        self._preview_signals.done.connect(self._on_compare_done)
        self._preview_signals.failed.connect(self._on_compare_failed)

        QShortcut(QKeySequence.StandardKey.Open, self, self.add_files_dialog)
        QShortcut(QKeySequence.StandardKey.Paste, self, self.paste_from_clipboard)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self, self._on_run)

        self._apply_language(self.settings.value("language", "auto"))
        self._build()
        self._load_settings()
        if geometry := self.settings.value("geometry"):
            self.restoreGeometry(geometry)
        hints = QApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_theme)
        self._refresh()

    # ----- layout -------------------------------------------------------------------------

    def _build(self) -> None:
        self._building = True
        root = QWidget(objectName="root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # start without a highlighted field
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(16)
        outer.addLayout(self._build_header())
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_files_panel(), 1)
        body.addWidget(self._build_settings_panel())
        outer.addLayout(body, 1)
        root.setFocus()
        self._building = False

    def _build_header(self) -> QHBoxLayout:
        logo = QLabel()
        pixmap = app_icon_pixmap(88)
        pixmap.setDevicePixelRatio(2)
        logo.setPixmap(pixmap)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("IconPress", "title"))
        titles.addWidget(label(tr("Convert images and make app icons"), "subtitle"))

        self.language_box = QComboBox()
        self.language_box.setToolTip(tr("Language"))
        self.language_box.addItem(tr("System language"), "auto")
        for code, name in i18n.LANGUAGES.items():
            self.language_box.addItem(name, code)
        self._select(self.language_box, self._language_choice)
        self.language_box.currentIndexChanged.connect(
            lambda _: self._set_language(self.language_box.currentData()))

        self.theme_control = SegmentedControl([(tr("Auto"), "auto"), (tr("Light"), "light"),
                                               (tr("Dark"), "dark")])
        self.theme_control.setToolTip(tr("Appearance"))
        self.theme_control.changed.connect(self._set_theme)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(logo)
        header.addLayout(titles)
        header.addStretch()
        header.addWidget(self.language_box, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.theme_control, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _build_files_panel(self) -> QFrame:
        frame, layout = card()
        layout.setSpacing(10)

        self.preview = PreviewView()
        self.preview.clicked.connect(self.add_files_dialog)
        layout.addWidget(self.preview, 1)

        self.caption = label("", "muted")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.caption)

        self.strip = QWidget()
        strip = QVBoxLayout(self.strip)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(4)
        self.files_title = label("", "sectionTitle")
        self.add_button = QPushButton(tr("Add…"), objectName="link")
        self.add_button.clicked.connect(self.add_files_dialog)
        self.clear_button = QPushButton(tr("Clear"), objectName="link")
        self.clear_button.clicked.connect(self.clear_files)
        strip.addLayout(row(self.files_title, None, self.add_button, self.clear_button))

        self.file_list = QListWidget()
        self.file_list.setViewMode(QListView.ViewMode.IconMode)
        self.file_list.setFlow(QListView.Flow.LeftToRight)
        self.file_list.setWrapping(False)
        self.file_list.setMovement(QListView.Movement.Static)
        self.file_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.file_list.setWordWrap(False)
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setIconSize(QSize(56, 56))
        self.file_list.setGridSize(QSize(92, 90))
        self.file_list.setFixedHeight(104)
        self.file_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.currentRowChanged.connect(lambda _: self._on_current_file())
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        remove = QAction(tr("Remove"), self.file_list)
        remove.setShortcuts([QKeySequence.StandardKey.Delete, QKeySequence("Backspace")])
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self.remove_selected)
        reveal = QAction(tr("Show in Folder"), self.file_list)
        reveal.triggered.connect(self._reveal_selected)
        self.file_list.addActions([remove, reveal])
        strip.addWidget(self.file_list)
        layout.addWidget(self.strip)
        return frame

    def _build_settings_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(PANEL_WIDTH)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.preset_box = QComboBox()
        self.preset_box.activated.connect(self._on_preset)
        self.save_preset_button = QPushButton(tr("Save…"), objectName="link")
        self.save_preset_button.setToolTip(tr("Save the current settings as a preset"))
        self.save_preset_button.clicked.connect(self.save_preset)
        self.delete_preset_button = QPushButton(tr("Delete"), objectName="link")
        self.delete_preset_button.clicked.connect(self.delete_preset)
        self.presets_row = boxed(row(self.preset_box, self.save_preset_button,
                                     self.delete_preset_button, stretch_first=True))
        layout.addWidget(self.presets_row)

        self.mode = SegmentedControl([(tr("Convert"), "convert"), (tr("Make icon"), "icon")])
        self.mode.changed.connect(self._on_mode)
        layout.addWidget(self.mode)

        self.settings_area = QWidget()
        content = QVBoxLayout(self.settings_area)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)
        self.convert_cards = [self._build_format_card(), self._build_size_card(),
                              self._build_watermark_card(), self._build_metadata_card()]
        self.icon_cards = [self._build_icon_card(), self._build_look_card()]
        for widget in self.convert_cards + self.icon_cards:
            content.addWidget(widget)
        content.addWidget(self._build_output_card())
        content.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.settings_area)
        layout.addWidget(scroll, 1)

        self.banner = QFrame(objectName="banner")
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(12, 8, 8, 8)
        self.banner_text = label("", "bannerText", wrap=True)
        self.banner_action = QPushButton(objectName="link")
        self.banner_action.clicked.connect(self._on_banner_action)
        banner.addWidget(self.banner_text, 1)
        banner.addWidget(self.banner_action)
        self.banner.hide()
        layout.addWidget(self.banner)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.run_button = QPushButton(objectName="primary")
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_button.clicked.connect(self._on_run)
        layout.addWidget(self.run_button)
        return panel

    def _build_format_card(self) -> QFrame:
        frame, layout = card(tr("Format"))
        self.format_box = QComboBox()
        for fmt in core.available_formats():
            self.format_box.addItem(fmt.label, fmt.name)
        self.format_box.currentIndexChanged.connect(self._options_changed)
        layout.addWidget(self.format_box)
        self.format_hint = label("", "hint", wrap=True)
        layout.addWidget(self.format_hint)

        self.quality_value = label("", "value")
        layout.addLayout(row(label(tr("Quality")), None, self.quality_value))
        self.quality = slider(10, 100)
        self.quality.valueChanged.connect(self._options_changed)
        layout.addWidget(self.quality)

        self.target_switch = Switch()
        self.target_switch.toggled.connect(self._options_changed)
        self.target_label = label(tr("Limit file size"))
        layout.addLayout(row(self.target_label, None, self.target_switch))
        self.target_kb = spin(5, 1_000_000, " " + tr("{size} KB", size="").strip())
        self.target_kb.valueChanged.connect(self._options_changed)
        self.target_hint = label(tr("Lowers the quality, and the size if needed, until the "
                                    "file fits."), "hint", wrap=True)
        layout.addWidget(self.target_kb)
        layout.addWidget(self.target_hint)

        self.background_button = ColorButton()
        self.background_button.changed.connect(self._options_changed)
        self.background_row = boxed(row(label(tr("Background for transparency"), wrap=True),
                                        self.background_button, stretch_first=True))
        layout.addWidget(self.background_row)
        return frame

    def _build_size_card(self) -> QFrame:
        frame, layout = card(tr("Size and rotation"))
        self.resize_box = QComboBox()
        for text, value in RESIZE_MODES:
            self.resize_box.addItem(tr(text), value)
        self.resize_box.currentIndexChanged.connect(self._on_resize_mode)
        layout.addWidget(self.resize_box)

        self.size_a = spin(1, 100_000, pixels_suffix())
        self.size_b = spin(1, 100_000, pixels_suffix())
        self.size_times = label("×", "muted")
        for box in (self.size_a, self.size_b):
            box.valueChanged.connect(self._on_size_value)
        sizes = QHBoxLayout()
        sizes.setSpacing(8)
        sizes.addWidget(self.size_a, 1)
        sizes.addWidget(self.size_times)
        sizes.addWidget(self.size_b, 1)
        self.size_row = boxed(sizes)
        layout.addWidget(self.size_row)
        self.enlarge = Switch()
        self.enlarge.toggled.connect(self._options_changed)
        self.enlarge_row = boxed(row(label(tr("Enlarge smaller images"), wrap=True),
                                     self.enlarge, stretch_first=True))
        layout.addWidget(self.enlarge_row)

        layout.addSpacing(4)
        layout.addWidget(label(tr("Rotate clockwise")))
        self.rotate = SegmentedControl([("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)])
        self.rotate.changed.connect(self._options_changed)
        layout.addWidget(self.rotate)
        self.flip_h = Switch()
        self.flip_v = Switch()
        for switch, text in ((self.flip_h, tr("Flip horizontally")),
                             (self.flip_v, tr("Flip vertically"))):
            switch.toggled.connect(self._options_changed)
            layout.addLayout(row(label(text, wrap=True), switch, stretch_first=True))
        return frame

    def _build_watermark_card(self) -> QFrame:
        self.wm_switch = Switch()
        self.wm_switch.toggled.connect(self._options_changed)
        frame, layout = card(tr("Watermark"), self.wm_switch)
        self.wm_body = QWidget()
        body = QVBoxLayout(self.wm_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.wm_kind = SegmentedControl([(tr("Text"), "text"), (tr("Picture"), "image")])
        self.wm_kind.changed.connect(self._options_changed)
        body.addWidget(self.wm_kind)
        self.wm_text = QLineEdit(placeholderText=tr("Text, for example © Your Name"))
        self.wm_text.textChanged.connect(self._options_changed)
        body.addWidget(self.wm_text)
        self.wm_image = QLineEdit(readOnly=True, placeholderText=tr("No picture chosen"))
        choose = QPushButton(tr("Choose…"))
        choose.clicked.connect(self.choose_watermark_image)
        self.wm_image_row = boxed(row(self.wm_image, choose, stretch_first=True))
        body.addWidget(self.wm_image_row)

        self.wm_position = PositionPicker(core.POSITIONS)
        self.wm_position.changed.connect(self._options_changed)
        position = QVBoxLayout()
        position.addWidget(label(tr("Position")))
        position.addWidget(self.wm_position)
        position.addStretch()
        self.wm_size = slider(5, 100)
        self.wm_opacity = slider(10, 100)
        self.wm_size_value = label("", "value")
        self.wm_opacity_value = label("", "value")
        for widget in (self.wm_size, self.wm_opacity):
            widget.valueChanged.connect(self._options_changed)
        sliders = QVBoxLayout()
        sliders.addLayout(row(label(tr("Size")), None, self.wm_size_value))
        sliders.addWidget(self.wm_size)
        sliders.addLayout(row(label(tr("Opacity")), None, self.wm_opacity_value))
        sliders.addWidget(self.wm_opacity)
        placement = QHBoxLayout()
        placement.setSpacing(16)
        placement.addLayout(position)
        placement.addLayout(sliders, 1)
        body.addLayout(placement)
        self.wm_color = ColorButton()
        self.wm_color.changed.connect(self._options_changed)
        self.wm_color_row = boxed(row(label(tr("Text colour")), None, self.wm_color))
        body.addWidget(self.wm_color_row)
        layout.addWidget(self.wm_body)
        return frame

    def _build_metadata_card(self) -> QFrame:
        frame, layout = card(tr("Metadata"))
        self.keep_metadata = Switch()
        self.keep_metadata.toggled.connect(self._options_changed)
        layout.addLayout(row(label(tr("Keep EXIF: camera, date, location"), wrap=True),
                             self.keep_metadata, stretch_first=True))
        layout.addWidget(label(tr("Off by default: photos often record where they were taken."),
                               "hint", wrap=True))
        self.keep_dates = Switch()
        layout.addLayout(row(label(tr("Keep the original file dates"), wrap=True),
                             self.keep_dates, stretch_first=True))
        return frame

    def _build_icon_card(self) -> QFrame:
        frame, layout = card(tr("Icon"))
        self.icon_kind = QComboBox()
        for text, value in ICON_KINDS:
            self.icon_kind.addItem(tr(text), value)
        self.icon_kind.currentIndexChanged.connect(self._on_icon_kind)
        layout.addWidget(self.icon_kind)

        self.sizes_label = label(tr("Sizes"))
        layout.addWidget(self.sizes_label)
        self.chips_box = QWidget()
        self.chips = QGridLayout(self.chips_box)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(6)
        layout.addWidget(self.chips_box)
        self.kind_note = label("", "hint", wrap=True)
        layout.addWidget(self.kind_note)
        return frame

    def _build_look_card(self) -> QFrame:
        frame, layout = card(tr("Look"))
        layout.addWidget(label(tr("Non-square images")))
        self.fit = SegmentedControl([(tr("Fit whole image"), "pad"),
                                     (tr("Crop to fill"), "crop")])
        self.fit.changed.connect(self._on_icon_look)
        layout.addWidget(self.fit)

        self.padding_value = label("", "value")
        layout.addLayout(row(label(tr("Padding")), None, self.padding_value))
        self.padding = slider(0, 40)
        self.padding.valueChanged.connect(self._on_icon_look)
        layout.addWidget(self.padding)

        layout.addWidget(label(tr("Background")))
        self.icon_bg = SegmentedControl([(tr("None"), "none"), (tr("Colour"), "color"),
                                         (tr("Gradient"), "gradient")])
        self.icon_bg.changed.connect(self._on_icon_look)
        layout.addWidget(self.icon_bg)
        self.icon_bg1 = ColorButton()
        self.icon_bg2 = ColorButton()
        for button in (self.icon_bg1, self.icon_bg2):
            button.changed.connect(self._on_icon_look)
        layout.addLayout(row(self.icon_bg1, self.icon_bg2, None))

        self.radius_value = label("", "value")
        layout.addLayout(row(label(tr("Rounded corners")), None, self.radius_value))
        self.radius = slider(0, 50)
        self.radius.valueChanged.connect(self._on_icon_look)
        layout.addWidget(self.radius)
        return frame

    def _build_output_card(self) -> QFrame:
        frame, layout = card(tr("Save to"))
        self.out_field = QLineEdit(readOnly=True,
                                   placeholderText=tr("Next to the original files"))
        choose = QPushButton(tr("Choose…"))
        choose.clicked.connect(self.choose_output_folder)
        layout.addLayout(row(self.out_field, choose, stretch_first=True))
        self.reset_out = QPushButton(tr("Save next to the originals"), objectName="link")
        self.reset_out.clicked.connect(lambda: self._set_output_dir(""))
        layout.addWidget(self.reset_out, 0, Qt.AlignmentFlag.AlignLeft)

        self.overwrite = Switch()
        layout.addLayout(row(label(tr("Overwrite existing files"), wrap=True), self.overwrite,
                             stretch_first=True))
        layout.addWidget(label(tr("When off, a number is added instead: photo (1).png. When on, "
                                  "converting to the same format next to the original replaces "
                                  "it."), "hint", wrap=True))

        layout.addSpacing(4)
        layout.addWidget(label(tr("File names")))
        self.template = QLineEdit(placeholderText="{name}")
        self.find = QLineEdit(placeholderText=tr("Find"))
        self.replace = QLineEdit(placeholderText=tr("Replace with"))
        for field in (self.template, self.find, self.replace):
            field.textChanged.connect(self._refresh)
        layout.addWidget(self.template)
        layout.addLayout(row(self.find, self.replace))
        layout.addWidget(label(tr("Use {name}, {n}, {n:03}, {width}, {height}"), "hint",
                               wrap=True))
        self.name_preview = label("", "muted", wrap=True)
        layout.addWidget(self.name_preview)
        return frame

    # ----- state: settings and presets ----------------------------------------------------

    def _collect_state(self) -> dict:
        resize_mode = self.resize_box.currentData()
        a, b = self._resize_values.get(resize_mode, (STATE_DEFAULTS["resize_a"],
                                                     STATE_DEFAULTS["resize_b"]))
        return {
            "mode": self.mode.value(),
            "format": self.format_box.currentData(), "quality": self.quality.value(),
            "target_on": self.target_switch.isChecked(), "target_kb": self.target_kb.value(),
            "background": self.background_button.color(),
            "resize_mode": resize_mode, "resize_a": a, "resize_b": b,
            "enlarge": self.enlarge.isChecked(),
            "rotate": self.rotate.value(), "flip_h": self.flip_h.isChecked(),
            "flip_v": self.flip_v.isChecked(),
            "keep_metadata": self.keep_metadata.isChecked(),
            "keep_dates": self.keep_dates.isChecked(),
            "wm_on": self.wm_switch.isChecked(), "wm_kind": self.wm_kind.value(),
            "wm_text": self.wm_text.text(), "wm_image": self.wm_image.text(),
            "wm_position": self.wm_position.value(), "wm_size": self.wm_size.value(),
            "wm_opacity": self.wm_opacity.value(), "wm_color": self.wm_color.color(),
            "icon_kind": self.icon_kind.currentData(),
            "icon_sizes/ico": ",".join(map(str, sorted(self._icon_sizes["ico"]))),
            "icon_sizes/png": ",".join(map(str, sorted(self._icon_sizes["png"]))),
            "fit": self.fit.value(), "radius": self.radius.value(),
            "padding": self.padding.value(), "icon_bg": self.icon_bg.value(),
            "icon_bg1": self.icon_bg1.color(), "icon_bg2": self.icon_bg2.color(),
            "out_dir": self.out_field.text(), "overwrite": self.overwrite.isChecked(),
            "template": self.template.text(), "find": self.find.text(),
            "replace": self.replace.text(),
        }

    def _apply_state(self, state: dict) -> None:
        state = {key: coerce(state.get(key), default) for key, default in STATE_DEFAULTS.items()}
        self._building = True
        self._select(self.format_box, state["format"])
        self.quality.setValue(state["quality"])
        self.target_switch.setChecked(state["target_on"])
        self.target_kb.setValue(state["target_kb"])
        self.background_button.set_color(state["background"])
        mode = state["resize_mode"] if state["resize_mode"] in core.RESIZE_MODES else "none"
        if mode != "none":
            self._resize_values[mode] = (state["resize_a"], state["resize_b"])
        self._select(self.resize_box, mode)
        self.enlarge.setChecked(state["enlarge"])
        self.rotate.set_value(state["rotate"] if state["rotate"] in (90, 180, 270) else 0)
        self.flip_h.setChecked(state["flip_h"])
        self.flip_v.setChecked(state["flip_v"])
        self.keep_metadata.setChecked(state["keep_metadata"])
        self.keep_dates.setChecked(state["keep_dates"])
        self.wm_switch.setChecked(state["wm_on"])
        self.wm_kind.set_value(state["wm_kind"])
        self.wm_text.setText(state["wm_text"])
        self.wm_image.setText(state["wm_image"])
        self.wm_position.set_value(state["wm_position"] if state["wm_position"] in core.POSITIONS
                                   else "bottom-right")
        self.wm_size.setValue(state["wm_size"])
        self.wm_opacity.setValue(state["wm_opacity"])
        self.wm_color.set_color(state["wm_color"])
        for kind in core.ICON_SIZES:
            saved = {to_int(v, 0) for v in state[f"icon_sizes/{kind}"].split(",")}
            self._icon_sizes[kind] = (saved & set(core.ICON_SIZES[kind])) or {
                to_int(v, 0) for v in STATE_DEFAULTS[f"icon_sizes/{kind}"].split(",")}
        self._select(self.icon_kind, state["icon_kind"])
        self.fit.set_value(state["fit"])
        self.radius.setValue(state["radius"])
        self.padding.setValue(state["padding"])
        self.icon_bg.set_value(state["icon_bg"])
        self.icon_bg1.set_color(state["icon_bg1"])
        self.icon_bg2.set_color(state["icon_bg2"])
        self.overwrite.setChecked(state["overwrite"])
        self.template.setText(state["template"])
        self.find.setText(state["find"])
        self.replace.setText(state["replace"])
        self.mode.set_value(state["mode"])
        self._building = False
        out_dir = state["out_dir"]
        self._set_output_dir(out_dir if out_dir and Path(out_dir).is_dir() else "")
        self._on_resize_mode()
        self._on_icon_kind()
        self._on_mode(self.mode.value())

    def _load_settings(self) -> None:
        s = self.settings
        self._set_theme(s.value("theme", "auto"), save=False)
        self.theme_control.set_value(self._theme_mode)
        state = {key: s.value(key) for key in STATE_DEFAULTS}
        if s.value("resize_mode") is None and to_int(s.value("max_side"), 0) > 0:  # version 1
            state.update(resize_mode="longest", resize_a=s.value("max_side"))
        self._apply_state(state)
        self._last_dir = str(s.value("last_dir", str(Path.home())))
        try:
            presets = json.loads(str(s.value("presets", "{}")))
            self._presets = {str(k): v for k, v in presets.items() if isinstance(v, dict)}
        except (ValueError, AttributeError):
            self._presets = {}
        self._fill_presets()

    def _save_settings(self) -> None:
        s = self.settings
        for key, value in self._collect_state().items():
            s.setValue(key, value)
        s.setValue("language", self._language_choice)
        s.setValue("theme", self._theme_mode)
        s.setValue("last_dir", self._last_dir)
        s.setValue("presets", json.dumps(self._presets, ensure_ascii=False))
        s.setValue("geometry", self.saveGeometry())
        s.sync()

    # Preset items carry "builtin:<name>" or "user:<name>" as their data
    def _fill_presets(self, select: str | None = None) -> None:
        box = self.preset_box
        box.clear()
        box.addItem(tr("Presets"), "")
        for name, _preset in BUILTIN_PRESETS:
            box.addItem(tr(name), f"builtin:{name}")
        if self._presets:
            box.insertSeparator(box.count())
            for name in sorted(self._presets, key=str.lower):
                box.addItem(name, f"user:{name}")
        if select:
            self._select(box, select)
        self._refresh_preset_buttons()

    def _user_preset(self) -> str | None:
        kind, _, name = str(self.preset_box.currentData() or "").partition(":")
        return name if kind == "user" else None

    def _refresh_preset_buttons(self) -> None:
        self.delete_preset_button.setVisible(self._user_preset() is not None)

    def _on_preset(self, index: int) -> None:
        kind, _, name = str(self.preset_box.itemData(index) or "").partition(":")
        preset = dict(BUILTIN_PRESETS).get(name) if kind == "builtin" else self._presets.get(name)
        if preset:
            self._apply_state({**self._collect_state(), **preset})
        self._refresh_preset_buttons()

    def save_preset(self) -> None:
        suggestion = self._user_preset() or ""
        name, ok = QInputDialog.getText(self, tr("Save preset"), tr("Preset name:"),
                                        text=suggestion)
        name = name.strip()
        if ok and name:
            state = self._collect_state()
            state.pop("out_dir", None)  # a folder on this computer is not part of a preset
            self._presets[name] = state
            self.settings.setValue("presets", json.dumps(self._presets, ensure_ascii=False))
            self._fill_presets(select=f"user:{name}")

    def delete_preset(self) -> None:
        name = self._user_preset()
        if name is not None:
            self._presets.pop(name, None)
            self.settings.setValue("presets", json.dumps(self._presets, ensure_ascii=False))
            self._fill_presets()

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_theme(self, mode, save: bool = True) -> None:
        self._theme_mode = mode if mode in ("auto", "light", "dark") else "auto"
        theme.apply(QApplication.instance(), self._theme_mode)
        if save:
            self.settings.setValue("theme", self._theme_mode)

    def _on_system_theme(self, *_args) -> None:
        if self._theme_mode == "auto":
            theme.apply(QApplication.instance(), "auto")

    def _apply_language(self, choice) -> None:
        """Switch translations, number formats and text direction; ``"auto"`` follows the OS."""
        self._language_choice = choice if choice in i18n.LANGUAGES else "auto"
        code = i18n.set_language(i18n.match(QLocale.system().uiLanguages())
                                 if self._language_choice == "auto" else self._language_choice)
        locale = QLocale(code)
        QLocale.setDefault(locale)
        app = QApplication.instance()
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl()
                               else Qt.LayoutDirection.LeftToRight)
        # Qt's own texts: standard dialog buttons, text field context menus
        app.removeTranslator(self._qt_translator)
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if self._qt_translator.load(locale, "qtbase", "_", path):
            app.installTranslator(self._qt_translator)

    def _set_language(self, choice) -> None:
        if choice == self._language_choice or self._busy():
            return
        state = self._collect_state()
        preset = self.preset_box.currentData()
        self._apply_language(choice)
        self.settings.setValue("language", self._language_choice)
        # Rebuild the interface in the new language, keeping the files and options
        current = self._current_path()
        self._build()
        self.theme_control.set_value(self._theme_mode)
        self._set_theme(self._theme_mode, save=False)
        self._apply_state(state)
        self._fill_presets(select=preset)
        for path in self.files:
            self.file_list.addItem(self._file_item(path))
        if current in self.files:
            self.file_list.setCurrentRow(self.files.index(current))
        self._refresh()

    # ----- options ------------------------------------------------------------------------

    def convert_options(self) -> core.ConvertOptions:
        mode = self.resize_box.currentData()
        watermark = None
        if self.wm_switch.isChecked():
            image = self.wm_image.text() if self.wm_kind.value() == "image" else ""
            text = self.wm_text.text() if self.wm_kind.value() == "text" else ""
            if image or text.strip():
                watermark = core.Watermark(
                    text=text, image=image or None, position=self.wm_position.value(),
                    size=self.wm_size.value() / 100, opacity=self.wm_opacity.value() / 100,
                    color=self.wm_color.color())
        return core.ConvertOptions(
            format=self.format_box.currentData(),
            quality=self.quality.value(),
            resize=mode,
            size=self._resize_values.get(mode, (0, 0)),
            enlarge=self.enlarge.isChecked(),
            rotate=self.rotate.value(),
            flip_h=self.flip_h.isChecked(),
            flip_v=self.flip_v.isChecked(),
            background=self.background_button.color(),
            target_kb=self.target_kb.value() if self.target_switch.isChecked() else None,
            keep_metadata=self.keep_metadata.isChecked(),
            watermark=watermark,
        )

    def icon_options(self) -> core.IconOptions:
        kind = self.icon_kind.currentData()
        background = self.icon_bg.value()
        return core.IconOptions(
            kind=kind,
            sizes=tuple(sorted(self._icon_sizes.get(kind, ()))),
            fit=self.fit.value(),
            corner_radius=self.radius.value() / 100,
            padding=self.padding.value() / 100,
            background=self.icon_bg1.color() if background != "none" else None,
            background2=self.icon_bg2.color() if background == "gradient" else None,
        )

    def output_options(self) -> core.OutputOptions:
        return core.OutputOptions(
            folder=self.out_field.text() or None, overwrite=self.overwrite.isChecked(),
            template=self.template.text() or "{name}", find=self.find.text(),
            replace=self.replace.text(),
            keep_dates=self.mode.value() == "convert" and self.keep_dates.isChecked())

    def _target_ext(self) -> tuple[str, str]:
        if self.mode.value() == "convert":
            return core.FORMATS[self.format_box.currentData()].ext, ""
        return core.icon_target(self.icon_options())

    def _on_mode(self, mode) -> None:
        if self._building:
            return
        for widget in self.convert_cards:
            widget.setVisible(mode == "convert")
        for widget in self.icon_cards:
            widget.setVisible(mode == "icon")
        self._update_preview()
        self._refresh()

    def _options_changed(self, *_args) -> None:
        if self._building:
            return
        self._refresh()
        self._schedule_compare()

    def _on_resize_mode(self, *_args) -> None:
        if self._building:
            return
        mode = self.resize_box.currentData()
        a, b = self._resize_values.get(mode, (1, 1))
        self._building = True
        percent = mode == "percent"
        self.size_a.setSuffix(" %" if percent else pixels_suffix())
        self.size_a.setRange(1, 1000 if percent else 100_000)
        self.size_a.setValue(max(1, a))
        self.size_b.setValue(max(1, b))
        self._building = False
        self.size_row.setVisible(mode != "none")
        self.size_a.setVisible(mode in ("longest", "percent", "width", "box"))
        self.size_b.setVisible(mode in ("height", "box"))
        self.size_times.setVisible(mode == "box")
        self.enlarge_row.setVisible(mode in ("longest", "width", "height", "box"))
        self._options_changed()

    def _on_size_value(self, *_args) -> None:
        if self._building:
            return
        mode = self.resize_box.currentData()
        if mode != "none":
            self._resize_values[mode] = (self.size_a.value(), self.size_b.value())
        self._options_changed()

    def _on_icon_kind(self, *_args) -> None:
        if self._building:
            return
        kind = self.icon_kind.currentData()
        while self.chips.count():
            old_chip = self.chips.takeAt(0).widget()
            old_chip.hide()
            old_chip.deleteLater()
        sizes = core.ICON_SIZES.get(kind, ())
        for index, size in enumerate(sizes):
            button = chip(str(size))
            button.setChecked(size in self._icon_sizes[kind])
            button.toggled.connect(partial(self._on_chip, kind, size, button))
            self.chips.addWidget(button, index // 5, index % 5)
        self.sizes_label.setVisible(bool(sizes))
        self.chips_box.setVisible(bool(sizes))
        self.kind_note.setText(tr(KIND_NOTES[kind]) if kind in KIND_NOTES else "")
        self.kind_note.setVisible(kind in KIND_NOTES)
        self._on_icon_look()

    def _on_chip(self, kind: str, size: int, button: QPushButton, checked: bool) -> None:
        sizes = self._icon_sizes[kind]
        if checked:
            sizes.add(size)
        elif len(sizes) > 1:
            sizes.discard(size)
        else:
            button.setChecked(True)  # an icon needs at least one size

    def _on_icon_look(self, *_args) -> None:
        if self._building:
            return
        radius, padding = self.radius.value(), self.padding.value()
        self.radius_value.setText(tr("{n}%", n=radius) if radius else tr("Square"))
        self.padding_value.setText(tr("{n}%", n=padding))
        background = self.icon_bg.value()
        self.icon_bg1.setVisible(background != "none")
        self.icon_bg2.setVisible(background == "gradient")
        self._schedule_preview()
        self._refresh()

    # ----- files --------------------------------------------------------------------------

    def add_files_dialog(self) -> None:
        if self._busy():
            return
        patterns = " ".join(f"*{ext}" for ext in core.input_extensions())
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Add images"), self._last_dir,
            f"{tr('Images')} ({patterns});;{tr('All files')} (*)")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths) -> list[Path]:
        """Add files (and the images directly inside dropped folders) to the list."""
        if self._busy():
            return []
        candidates: list[Path] = []
        for path in map(Path, paths):
            if path.is_dir():
                readable = core.readable_extensions()
                candidates += sorted(p for p in path.iterdir()
                                     if p.is_file() and p.suffix.lower() in readable)
            else:
                candidates.append(path)

        added, skipped = [], []
        for path in candidates:
            path = path.resolve()
            if path in self._infos:
                continue
            try:
                self._infos[path] = core.describe(path)
            except Exception:
                skipped.append(path.name)
                continue
            self.file_list.addItem(self._file_item(path))
            self.files.append(path)
            self._thumb_queue.append(path)
            added.append(path)

        if added:
            self._last_dir = str(added[0].parent)
            self.file_list.setCurrentRow(self.files.index(added[0]))
            QTimer.singleShot(0, self._load_next_thumb)
        if skipped:
            self._show_banner("error", ntr("Skipped {n} files that are not images.",
                                           len(skipped)),
                              tr("Details"), details="\n".join(skipped))
        elif added:
            self.banner.hide()
        self._refresh()
        return added

    def paste_from_clipboard(self) -> None:
        """Ctrl+V: add copied files, or save a copied picture (a screenshot) and add it."""
        if self._busy():
            return
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasUrls():
            files = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if files:
                self.add_paths(files)
                return
        image = clipboard.image()
        if image.isNull():
            self._show_banner("error", tr("The clipboard has no picture to paste."), "")
            return
        location = QStandardPaths.StandardLocation.PicturesLocation
        folder = Path(QStandardPaths.writableLocation(location) or Path.home()) / "IconPress"
        folder.mkdir(parents=True, exist_ok=True)
        path = core.output_path(folder / f"Pasted {datetime.now():%Y-%m-%d %H.%M.%S}.png", "png")
        if not image.save(str(path), "PNG"):
            self._show_banner("error", tr("Couldn't save the pasted picture."), "")
            return
        self.add_paths([path])
        self._results = [(path, path, None)]
        self._show_banner("ok", tr("Pasted picture saved to {folder}.", folder=folder),
                          tr("Show in Folder"))

    def remove_selected(self) -> None:
        if self._busy():
            return
        rows = sorted((self.file_list.row(i) for i in self.file_list.selectedItems()),
                      reverse=True)
        for index in rows:
            self._forget(self.files.pop(index))
            self.file_list.takeItem(index)
        self._on_current_file()

    def clear_files(self) -> None:
        if self._busy():
            return
        for path in self.files:
            self._forget(path)
        self.files.clear()
        self.file_list.clear()
        self.banner.hide()
        self._on_current_file()

    def _forget(self, path: Path) -> None:
        self._infos.pop(path, None)
        self._previews.pop(path, None)
        self._thumbs.pop(path, None)

    def _file_item(self, path: Path) -> QListWidgetItem:
        icon = self._thumbs.get(path)
        if icon is None:
            pixmap = QPixmap(THUMB_SIDE, THUMB_SIDE)
            pixmap.fill(Qt.GlobalColor.transparent)
            icon = QIcon(pixmap)
        item = QListWidgetItem(icon, path.name)
        item.setToolTip(str(path))
        return item

    def _load_next_thumb(self) -> None:
        # One thumbnail per event-loop turn keeps the window responsive for big batches
        if not self._thumb_queue:
            return
        path = self._thumb_queue.popleft()
        if path in self._infos:
            try:
                # Square thumbnails keep the file names under them on one line
                image = pil_to_qimage(core.square(core.load_preview(path, THUMB_SIDE)))
                pixmap = QPixmap.fromImage(image)
                icon = QIcon(pixmap)
                icon.addPixmap(pixmap, QIcon.Mode.Selected)  # no tint on the selected one
                self._thumbs[path] = icon
                self.file_list.item(self.files.index(path)).setIcon(icon)
            except Exception:
                pass  # the file stays in the list; converting it will report the error
        QTimer.singleShot(0, self._load_next_thumb)

    def _current_path(self) -> Path | None:
        index = self.file_list.currentRow()
        return self.files[index] if 0 <= index < len(self.files) else None

    def _reveal_selected(self) -> None:
        if path := self._current_path():
            self._reveal(path)

    @staticmethod
    def _reveal(path: Path) -> None:
        folder = path if path.is_dir() else path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("Save to"), self.out_field.text() or self._last_dir)
        if folder:
            self._set_output_dir(folder)

    def choose_watermark_image(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in core.input_extensions()
                            if ext not in core.SVG_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, tr("Watermark"), self._last_dir,
                                              f"{tr('Images')} ({patterns})")
        if path:
            self.wm_image.setText(path)
            self.wm_image.setToolTip(path)

    def _set_output_dir(self, folder: str) -> None:
        self.out_field.setText(folder)
        self.out_field.setToolTip(folder)
        self.reset_out.setVisible(bool(folder))
        self._refresh()

    # ----- preview ------------------------------------------------------------------------

    def _on_current_file(self) -> None:
        self._update_preview()
        self._refresh()

    def _schedule_preview(self) -> None:
        if self.mode.value() == "icon":
            self._preview_timer.start()

    def _preview_image(self, path: Path) -> Image.Image:
        if path not in self._previews:
            if len(self._previews) >= PREVIEW_CACHE:
                self._previews.pop(next(iter(self._previews)))
            self._previews[path] = core.load_preview(path, PREVIEW_SIDE)
        return self._previews[path]

    def _details(self, path: Path) -> list[str]:
        info = self._infos[path]
        details = [path.name, f"{info.width} × {info.height}", info.format,
                   human_size(info.file_size)]
        if info.frames > 1:
            details.append(ntr("{n} frames", info.frames))
        return details

    def _update_preview(self) -> None:
        if self._building:
            return
        path = self._current_path()
        self._estimate = None
        if path is None:
            self.preview.set_image(None)
            self.caption.hide()
            return
        details = self._details(path)
        try:
            image = self._preview_image(path)
            ratio = 1.0
            if self.mode.value() == "icon":
                options = self.icon_options()
                ratio = max(1.0, self.devicePixelRatioF())  # sharp on Retina / HiDPI screens
                opaque = options.kind == "ios"
                image = core.render_icon(image, options, round(ICON_PREVIEW_SIDE * ratio),
                                         opaque=opaque, rounded=not opaque)
                details = [path.name, tr("icon preview"), self.icon_kind.currentText()]
            qimage = pil_to_qimage(image)
            qimage.setDevicePixelRatio(ratio)
            self.preview.set_image(qimage)
        except Exception as exc:
            self.preview.set_image(None)
            details.append(tr("can't preview: {error}", error=exc))
        self.caption.setText(isolate(details))
        self.caption.show()
        self._schedule_compare()

    def _schedule_compare(self) -> None:
        self._compare_generation += 1
        if self.mode.value() == "convert" and self._current_path() is not None:
            self._compare_timer.start()

    def _arrow(self) -> str:
        return "   ←   " if i18n.is_rtl() else "   →   "

    def _start_compare(self) -> None:
        path = self._current_path()
        if path is None or self.mode.value() != "convert":
            return
        self.caption.setText(isolate(self._details(path)) + self._arrow() + tr("calculating…"))
        self._compare_pool.start(_CompareTask(
            self._preview_signals, self._compare_generation,
            lambda generation: generation == self._compare_generation, path,
            self.convert_options()))

    def _on_compare_done(self, generation: int, before, after, size: int, dimensions) -> None:
        path = self._current_path()
        if (generation != self._compare_generation or path is None
                or self.mode.value() != "convert"):
            return
        self.preview.set_image(before, after)
        original = self._infos[path].file_size
        change = round((size - original) / original * 100) if original else 0
        sign = "+" if change > 0 else "−" if change < 0 else "±"
        result = [f"{dimensions[0]} × {dimensions[1]}", "≈ " + human_size(size),
                  f"{sign}{abs(change)}%"]
        self.caption.setText(isolate(self._details(path)) + self._arrow() + isolate(result))
        self._estimate = (size, dimensions)

    def _on_compare_failed(self, generation: int, error: str) -> None:
        path = self._current_path()
        if generation == self._compare_generation and path is not None:
            self.caption.setText(isolate(self._details(path) + [
                tr("can't preview: {error}", error=error)]))

    # ----- running ------------------------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None

    def _on_run(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._stopping = True
            self._refresh()
            return
        if not self.files:
            return
        ext, suffix = self._target_ext()
        output = self.output_options()
        try:
            targets = core.plan_outputs(self.files, ext, output, suffix)
        except ValueError as exc:
            self._show_banner("error", tr("Can't use this file name template: {error}",
                                          error=exc), "")
            return
        if self.mode.value() == "convert":
            work = partial(core.convert_image, options=self.convert_options())
        else:
            work = partial(core.make_icon, options=self.icon_options())
        self._start(list(zip(self.files, targets, strict=True)), work, output.keep_dates)

    def _start(self, jobs: list[tuple[Path, Path]], work, keep_dates: bool) -> None:
        self.banner.hide()
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self._thread = QThread(self)
        self._worker = Worker(jobs, work, keep_dates)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._worker.deleteLater)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._set_busy(True)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setValue(done)

    def _on_finished(self, results) -> None:
        total = len(self.files)
        self._thread.quit()
        self._thread.wait()
        self._thread.deleteLater()
        self._thread = self._worker = None
        self._stopping = False
        self._set_busy(False)
        self.progress.hide()
        self._results = results

        for _src, out, _error in results:
            if out in self._infos:  # an original was replaced: refresh what we show for it
                self._infos[out] = core.describe(out)
                self._previews.pop(out, None)
                self._thumbs.pop(out, None)
                self._thumb_queue.append(out)
        QTimer.singleShot(0, self._load_next_thumb)
        self._update_preview()

        saved = sum(1 for r in results if r[1])
        failed = [r for r in results if r[2]]
        stopped = tr("Stopped.") + " " if len(results) < total else ""
        if failed:
            self._show_banner("error", stopped + tr("{failed} of {total} failed.",
                                                    failed=len(failed), total=len(results)),
                              tr("Details"), details="\n".join(f"{src.name}: {error}"
                                                                for src, _, error in failed))
        elif saved:
            self._show_banner("ok", stopped + ntr("Saved {n} files.", saved),
                              tr("Show in Folder"))
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.settings_area, self.mode, self.add_button, self.clear_button,
                       self.language_box, self.presets_row):
            widget.setEnabled(not busy)
        self._refresh()

    def _show_banner(self, kind: str, text: str, action: str, details: str = "") -> None:
        self._banner_details = details
        self.banner_text.setText(text)
        self.banner_action.setText(action)
        self.banner_action.setVisible(bool(action))
        self.banner.setProperty("kind", kind)
        for widget in (self.banner, self.banner_text):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.banner.show()

    def _on_banner_action(self) -> None:
        if self.banner.property("kind") == "error":
            box = QMessageBox(QMessageBox.Icon.Warning, "IconPress", self.banner_text.text(),
                              QMessageBox.StandardButton.Ok, self)
            box.setDetailedText(self._banner_details)
            box.exec()
        elif saved := [out for _, out, _ in self._results if out]:
            self._reveal(saved[0])

    def _refresh(self, *_args) -> None:
        if self._building:
            return
        count = len(self.files)
        self.strip.setVisible(count > 0)
        self.files_title.setText(tr("Images · {n}", n=count))

        fmt = core.FORMATS[self.format_box.currentData()]
        self.format_hint.setText(tr(FORMAT_HINTS[fmt.name]))
        self.quality.setEnabled(fmt.lossy)
        self.quality_value.setText(str(self.quality.value()) if fmt.lossy else tr("Lossless"))
        for widget in (self.target_label, self.target_switch):
            widget.setEnabled(fmt.lossy)
        limited = fmt.lossy and self.target_switch.isChecked()
        self.target_kb.setVisible(limited)
        self.target_hint.setVisible(limited)
        self.background_row.setVisible(not fmt.alpha)

        self.wm_body.setVisible(self.wm_switch.isChecked())
        text_mark = self.wm_kind.value() == "text"
        self.wm_text.setVisible(text_mark)
        self.wm_color_row.setVisible(text_mark)
        self.wm_image_row.setVisible(not text_mark)
        self.wm_size_value.setText(tr("{n}%", n=self.wm_size.value()))
        self.wm_opacity_value.setText(tr("{n}%", n=self.wm_opacity.value()))
        self._refresh_name_preview()

        button = self.run_button
        if self._worker is not None:
            button.setText(tr("Stopping…") if self._stopping else tr("Stop"))
            button.setEnabled(not self._stopping)
            return
        button.setEnabled(count > 0)
        kind = self.icon_kind.currentData()
        if not count:
            button.setText(tr("Add images to start"))
        elif self.mode.value() == "convert":
            button.setText(ntr("Convert {n} images to {fmt}", count, fmt=fmt.label) if count > 1
                           else tr("Convert to {fmt}", fmt=fmt.label))
        elif kind in ("ico", "icns"):
            button.setText(ntr("Create {n} {kind} icons", count, kind=kind.upper()) if count > 1
                           else tr("Create {kind} icon", kind=kind.upper()))
        else:
            button.setText(ntr("Create {n} icon sets", count) if count > 1
                           else tr("Create icon set"))

    def _refresh_name_preview(self) -> None:
        path = self._current_path() or (self.files[0] if self.files else None)
        if path is None:
            self.name_preview.hide()
            return
        ext, suffix = self._target_ext()
        index = self.files.index(path) + 1
        try:
            name = core.render_name(path, index, self.output_options()) + suffix
        except ValueError:
            name = tr("Can't use this file name template")
        else:
            name += f".{ext}" if ext else "/"
        arrow = "←" if i18n.is_rtl() else "→"
        self.name_preview.setText(isolate([path.name, name], f"  {arrow}  "))
        self.name_preview.show()

    # ----- events -------------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if not self._busy() and any(url.isLocalFile() for url in urls):
            event.acceptProposedAction()
            self.preview.set_drag_active(True)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.preview.set_drag_active(False)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.preview.set_drag_active(False)
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls()
                        if url.isLocalFile()])
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._compare_generation += 1  # a queued preview then skips its work
        self._compare_pool.clear()
        self._compare_pool.waitForDone()
        self._save_settings()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("IconPress")
    app.setOrganizationName("IconPress")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setFont(theme.default_font())
    window = MainWindow()
    window.show()
    if files := [arg for arg in argv[1:] if Path(arg).exists()]:
        window.add_paths(files)
    code = app.exec()
    if shiboken6.isValid(window):  # quit without closing the window, e.g. app.quit()
        window.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return code
