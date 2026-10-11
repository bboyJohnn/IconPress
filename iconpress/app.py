"""The IconPress main window."""

from __future__ import annotations

import sys
from collections import deque
from functools import partial
from pathlib import Path

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QLibraryInfo,
    QLocale,
    QObject,
    QSettings,
    QSize,
    Qt,
    QThread,
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
    QVBoxLayout,
    QWidget,
)

from . import __version__, core, i18n, theme
from .i18n import N_, ntr, tr
from .widgets import PreviewView, SegmentedControl, Switch, app_icon_pixmap, pil_to_qimage

PREVIEW_SIDE = 1200
ICON_PREVIEW_SIDE = 384
THUMB_SIDE = 112
PREVIEW_CACHE = 6

RESIZE_CHOICES = (0, 3840, 2560, 1920, 1280, 1024, 512)  # longest side; 0 keeps the size
ICON_KINDS = [
    (N_("ICO — Windows"), "ico"), (N_("ICNS — macOS"), "icns"),
    (N_("PNG set — web & apps"), "png"),
]
DEFAULT_ICON_SIZES = {"ico": core.ICO_SIZES, "png": (16, 32, 180, 192, 512)}
FORMAT_HINTS = {
    "PNG": N_("Lossless. Keeps transparency and animation."),
    "JPEG": N_("Small photos. No transparency: transparent areas become white."),
    "WEBP": N_("Modern web format: small files, transparency and animation."),
    "AVIF": N_("Newest web format with the best compression. Keeps transparency."),
    "GIF": N_("256 colours. Keeps animation; good for simple graphics."),
    "TIFF": N_("Lossless, for print and archiving."),
    "BMP": N_("Uncompressed bitmap."),
}


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


class Worker(QObject):
    """Runs ``task`` for each file on a background thread."""

    progress = Signal(int, int)
    finished = Signal(object)

    def __init__(self, paths: list[Path], task):
        super().__init__()
        self._paths = paths
        self._task = task
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        results: list[tuple[Path, Path | None, str | None]] = []
        written: list[Path] = []
        for done, path in enumerate(self._paths, 1):
            if self._cancelled:
                break
            try:
                out = self._task(path, reserved=written)
            except Exception as exc:  # reported to the user, one line per file
                results.append((path, None, str(exc) or type(exc).__name__))
            else:
                written.append(out)
                results.append((path, out, None))
            self.progress.emit(done, len(self._paths))
        self.finished.emit(results)


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(8)
    if title:
        layout.addWidget(label(title, "sectionTitle"))
    return frame, layout


def label(text: str = "", name: str | None = None) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def value_row(text: str, value: QLabel) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(label(text))
    row.addStretch()
    row.addWidget(value)
    return row


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings()
        self.files: list[Path] = []
        self._infos: dict[Path, core.ImageInfo] = {}
        self._previews: dict[Path, object] = {}
        self._thumb_queue: deque[Path] = deque()
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._results: list = []
        self._thumbs: dict[Path, QIcon] = {}
        self._icon_sizes = {kind: set(sizes) for kind, sizes in DEFAULT_ICON_SIZES.items()}
        self._theme_mode = "auto"
        self._stopping = False
        self._qt_translator = QTranslator(self)

        self.setWindowTitle("IconPress")
        self.setWindowIcon(QIcon(app_icon_pixmap()))
        # Free the window (and its combo-box popups, which are separate windows) while the
        # QApplication still exists; PySide crashes if it has to delete them at exit.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        self.setMinimumSize(900, 620)
        self.resize(1080, 700)

        self._preview_timer = QTimer(self, singleShot=True, interval=30)
        self._preview_timer.timeout.connect(self._update_preview)

        QShortcut(QKeySequence.StandardKey.Open, self, self.add_files_dialog)
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
        root = QWidget(objectName="root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        header = QHBoxLayout()
        self.files_title = label("", "sectionTitle")
        self.add_button = QPushButton(tr("Add…"), objectName="link")
        self.add_button.clicked.connect(self.add_files_dialog)
        self.clear_button = QPushButton(tr("Clear"), objectName="link")
        self.clear_button.clicked.connect(self.clear_files)
        header.addWidget(self.files_title)
        header.addStretch()
        header.addWidget(self.add_button)
        header.addWidget(self.clear_button)
        strip.addLayout(header)

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
        self.file_list.currentRowChanged.connect(lambda _: self._update_preview())
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
        panel.setFixedWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.mode = SegmentedControl([(tr("Convert"), "convert"), (tr("Make icon"), "icon")])
        self.mode.changed.connect(self._on_mode)
        layout.addWidget(self.mode)

        self.settings_area = QWidget()
        content = QVBoxLayout(self.settings_area)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)
        self.convert_card = self._build_convert_card()
        self.icon_card = self._build_icon_card()
        content.addWidget(self.convert_card)
        content.addWidget(self.icon_card)
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
        self.banner_text = label("", "bannerText")
        self.banner_text.setWordWrap(True)
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

    def _build_convert_card(self) -> QFrame:
        frame, layout = card(tr("Format"))
        self.format_box = QComboBox()
        for fmt in core.available_formats():
            self.format_box.addItem(fmt.label, fmt.name)
        self.format_box.currentIndexChanged.connect(self._refresh)
        layout.addWidget(self.format_box)
        self.format_hint = label("", "hint")
        self.format_hint.setWordWrap(True)
        layout.addWidget(self.format_hint)

        layout.addSpacing(4)
        self.quality_value = label("", "value")
        layout.addLayout(value_row(tr("Quality"), self.quality_value))
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(10, 100)
        self.quality.valueChanged.connect(lambda v: self.quality_value.setText(str(v)))
        layout.addWidget(self.quality)

        layout.addSpacing(4)
        layout.addWidget(label(tr("Max size (longest side)")))
        self.resize_box = QComboBox()
        for value in RESIZE_CHOICES:
            self.resize_box.addItem(tr("{n} px", n=value) if value else tr("Original size"),
                                    value)
        layout.addWidget(self.resize_box)
        return frame

    def _build_icon_card(self) -> QFrame:
        frame, layout = card(tr("Icon"))
        self.icon_kind = QComboBox()
        for text, value in ICON_KINDS:
            self.icon_kind.addItem(tr(text), value)
        self.icon_kind.currentIndexChanged.connect(self._on_icon_kind)
        layout.addWidget(self.icon_kind)

        layout.addSpacing(4)
        layout.addWidget(label(tr("Sizes")))
        self.chips_box = QWidget()
        self.chips = QGridLayout(self.chips_box)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(6)
        layout.addWidget(self.chips_box)
        self.icns_note = label(tr("macOS icons always include every size from 16 to 1024 px."),
                               "hint")
        self.icns_note.setWordWrap(True)
        layout.addWidget(self.icns_note)

        layout.addSpacing(4)
        layout.addWidget(label(tr("Non-square images")))
        self.fit = SegmentedControl([(tr("Fit whole image"), "pad"),
                                     (tr("Crop to fill"), "crop")])
        self.fit.changed.connect(lambda _: self._schedule_preview())
        layout.addWidget(self.fit)

        layout.addSpacing(4)
        self.radius_value = label("", "value")
        layout.addLayout(value_row(tr("Rounded corners"), self.radius_value))
        self.radius = QSlider(Qt.Orientation.Horizontal)
        self.radius.setRange(0, 50)
        self.radius.valueChanged.connect(self._on_radius)
        layout.addWidget(self.radius)
        return frame

    def _build_output_card(self) -> QFrame:
        frame, layout = card(tr("Save to"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.out_field = QLineEdit(readOnly=True,
                                   placeholderText=tr("Next to the original files"))
        choose = QPushButton(tr("Choose…"))
        choose.clicked.connect(self.choose_output_folder)
        row.addWidget(self.out_field, 1)
        row.addWidget(choose)
        layout.addLayout(row)
        self.reset_out = QPushButton(tr("Save next to the originals"), objectName="link")
        self.reset_out.clicked.connect(lambda: self._set_output_dir(""))
        layout.addWidget(self.reset_out, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(4)
        overwrite = QHBoxLayout()
        overwrite.addWidget(label(tr("Overwrite existing files")))
        overwrite.addStretch()
        self.overwrite = Switch()
        overwrite.addWidget(self.overwrite)
        layout.addLayout(overwrite)
        hint = label(tr("When off, a number is added instead: photo (1).png. When on, "
                        "converting to the same format next to the original replaces it."), "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return frame

    # ----- settings -----------------------------------------------------------------------

    def _load_settings(self) -> None:
        s = self.settings
        self._set_theme(s.value("theme", "auto"), save=False)
        self.theme_control.set_value(self._theme_mode)
        self.mode.set_value(s.value("mode", "convert"))
        self._select(self.format_box, s.value("format", "PNG"))
        self.quality.setValue(to_int(s.value("quality"), 90))
        self.quality_value.setText(str(self.quality.value()))
        self._select(self.resize_box, to_int(s.value("max_side"), 0))
        for kind in DEFAULT_ICON_SIZES:
            saved = {to_int(v, 0) for v in str(s.value(f"icon_sizes/{kind}", "")).split(",")}
            if saved & set(core.ICON_SIZES[kind]):
                self._icon_sizes[kind] = saved & set(core.ICON_SIZES[kind])
        self._select(self.icon_kind, s.value("icon_kind", "ico"))
        self.fit.set_value(s.value("fit", "pad"))
        self.radius.setValue(to_int(s.value("radius"), 0))
        self._on_radius(self.radius.value())
        out_dir = s.value("out_dir", "")
        self._set_output_dir(out_dir if out_dir and Path(out_dir).is_dir() else "")
        self.overwrite.setChecked(s.value("overwrite", False, type=bool))
        self._last_dir = s.value("last_dir", str(Path.home()))
        self._on_mode(self.mode.value())
        self._on_icon_kind()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("language", self._language_choice)
        s.setValue("theme", self._theme_mode)
        s.setValue("mode", self.mode.value())
        s.setValue("format", self.format_box.currentData())
        s.setValue("quality", self.quality.value())
        s.setValue("max_side", self.resize_box.currentData())
        for kind, sizes in self._icon_sizes.items():
            s.setValue(f"icon_sizes/{kind}", ",".join(map(str, sorted(sizes))))
        s.setValue("icon_kind", self.icon_kind.currentData())
        s.setValue("fit", self.fit.value())
        s.setValue("radius", self.radius.value())
        s.setValue("out_dir", self.out_field.text())
        s.setValue("overwrite", self.overwrite.isChecked())
        s.setValue("last_dir", self._last_dir)
        s.setValue("geometry", self.saveGeometry())
        s.sync()

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
        self._save_settings()
        self._apply_language(choice)
        self.settings.setValue("language", self._language_choice)
        # Rebuild the interface in the new language, keeping the files and options
        current = self._current_path()
        self._build()
        self._load_settings()
        for path in self.files:
            self.file_list.addItem(self._file_item(path))
        if current in self.files:
            self.file_list.setCurrentRow(self.files.index(current))
        self._refresh()

    # ----- options ------------------------------------------------------------------------

    def convert_options(self) -> core.ConvertOptions:
        return core.ConvertOptions(
            format=self.format_box.currentData(),
            quality=self.quality.value(),
            max_side=self.resize_box.currentData() or None,
        )

    def icon_options(self) -> core.IconOptions:
        kind = self.icon_kind.currentData()
        return core.IconOptions(
            kind=kind,
            sizes=tuple(sorted(self._icon_sizes.get(kind, ()))),
            fit=self.fit.value(),
            corner_radius=self.radius.value() / 100,
        )

    def _on_mode(self, mode) -> None:
        self.convert_card.setVisible(mode == "convert")
        self.icon_card.setVisible(mode == "icon")
        self._update_preview()
        self._refresh()

    def _on_icon_kind(self, *_args) -> None:
        kind = self.icon_kind.currentData()
        while self.chips.count():
            old_chip = self.chips.takeAt(0).widget()
            old_chip.hide()
            old_chip.deleteLater()
        sizes = core.ICON_SIZES[kind] if kind in self._icon_sizes else ()
        for index, size in enumerate(sizes):
            chip = QPushButton(str(size), objectName="chip")
            chip.setCheckable(True)
            chip.setChecked(size in self._icon_sizes[kind])
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chip.toggled.connect(partial(self._on_chip, kind, size, chip))
            self.chips.addWidget(chip, index // 5, index % 5)
        self.chips_box.setVisible(bool(sizes))
        self.icns_note.setVisible(not sizes)
        self._schedule_preview()
        self._refresh()

    def _on_chip(self, kind: str, size: int, chip: QPushButton, checked: bool) -> None:
        sizes = self._icon_sizes[kind]
        if checked:
            sizes.add(size)
        elif len(sizes) > 1:
            sizes.discard(size)
        else:
            chip.setChecked(True)  # an icon needs at least one size

    def _on_radius(self, value: int) -> None:
        self.radius_value.setText(tr("{n}%", n=value) if value else tr("Square"))
        self._schedule_preview()

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

    def add_paths(self, paths) -> None:
        """Add files (and the images directly inside dropped folders) to the list."""
        if self._busy():
            return
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

    def remove_selected(self) -> None:
        if self._busy():
            return
        rows = sorted((self.file_list.row(i) for i in self.file_list.selectedItems()),
                      reverse=True)
        for row in rows:
            self._forget(self.files.pop(row))
            self.file_list.takeItem(row)
        self._update_preview()
        self._refresh()

    def clear_files(self) -> None:
        if self._busy():
            return
        for path in self.files:
            self._forget(path)
        self.files.clear()
        self.file_list.clear()
        self.banner.hide()
        self._update_preview()
        self._refresh()

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
        row = self.file_list.currentRow()
        return self.files[row] if 0 <= row < len(self.files) else None

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

    def _set_output_dir(self, folder: str) -> None:
        self.out_field.setText(folder)
        self.out_field.setToolTip(folder)
        self.reset_out.setVisible(bool(folder))

    # ----- preview ------------------------------------------------------------------------

    def _schedule_preview(self) -> None:
        if self.mode.value() == "icon":
            self._preview_timer.start()

    def _preview_image(self, path: Path):
        if path not in self._previews:
            if len(self._previews) >= PREVIEW_CACHE:
                self._previews.pop(next(iter(self._previews)))
            self._previews[path] = core.load_preview(path, PREVIEW_SIDE)
        return self._previews[path]

    def _update_preview(self) -> None:
        path = self._current_path()
        if path is None:
            self.preview.set_image(None)
            self.caption.hide()
            return
        info = self._infos[path]
        details = [path.name, f"{info.width} × {info.height}", info.format,
                   human_size(info.file_size)]
        if info.frames > 1:
            details.append(ntr("{n} frames", info.frames))
        try:
            image = self._preview_image(path)
            ratio = 1.0
            if self.mode.value() == "icon":
                options = self.icon_options()
                ratio = max(1.0, self.devicePixelRatioF())  # sharp on Retina / HiDPI screens
                image = core.render_icon(image, options, round(ICON_PREVIEW_SIDE * ratio))
                details = [path.name, tr("icon preview"), options.kind.upper()]
            qimage = pil_to_qimage(image)
            qimage.setDevicePixelRatio(ratio)
            self.preview.set_image(qimage)
        except Exception as exc:
            self.preview.set_image(None)
            details.append(tr("can't preview: {error}", error=exc))
        # Isolate each part so numbers and names keep their order in right-to-left languages
        direction = "\u200f" if i18n.is_rtl() else "\u200e"
        self.caption.setText(direction + "   ·   ".join(f"\u2068{part}\u2069"
                                                       for part in details))
        self.caption.show()

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
        out_dir = self.out_field.text() or None
        if self.mode.value() == "convert":
            task = partial(core.run_convert, options=self.convert_options(), out_dir=out_dir,
                           overwrite=self.overwrite.isChecked())
        else:
            task = partial(core.run_icon, options=self.icon_options(), out_dir=out_dir,
                           overwrite=self.overwrite.isChecked())
        self._start(list(self.files), task)

    def _start(self, paths: list[Path], task) -> None:
        self.banner.hide()
        self.progress.setRange(0, len(paths))
        self.progress.setValue(0)
        self.progress.show()
        self._thread = QThread(self)
        self._worker = Worker(paths, task)
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
                       self.language_box):
            widget.setEnabled(not busy)
        self._refresh()

    def _show_banner(self, kind: str, text: str, action: str, details: str = "") -> None:
        self._banner_details = details
        self.banner_text.setText(text)
        self.banner_action.setText(action)
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
        count = len(self.files)
        self.strip.setVisible(count > 0)
        self.files_title.setText(tr("Images · {n}", n=count))

        fmt = core.FORMATS[self.format_box.currentData()]
        self.format_hint.setText(tr(FORMAT_HINTS[fmt.name]))
        self.quality.setEnabled(fmt.lossy)
        self.quality_value.setText(str(self.quality.value()) if fmt.lossy else tr("Lossless"))

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
        elif kind == "png":
            button.setText(ntr("Create {n} icon sets", count) if count > 1
                           else tr("Create icon set"))
        else:
            button.setText(ntr("Create {n} {kind} icons", count, kind=kind.upper()) if count > 1
                           else tr("Create {kind} icon", kind=kind.upper()))

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
