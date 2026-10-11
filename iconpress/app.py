"""The IconPress main window."""

from __future__ import annotations

import sys
from collections import deque
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
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

from . import __version__, core, theme
from .widgets import PreviewView, SegmentedControl, Switch, app_icon_pixmap, pil_to_qimage

PREVIEW_SIDE = 1200
ICON_PREVIEW_SIDE = 384
THUMB_SIDE = 112
PREVIEW_CACHE = 6

RESIZE_CHOICES = [
    ("Original size", 0), ("3840 px", 3840), ("2560 px", 2560), ("1920 px", 1920),
    ("1280 px", 1280), ("1024 px", 1024), ("512 px", 512),
]
ICON_KINDS = [
    ("ICO — Windows", "ico"), ("ICNS — macOS", "icns"), ("PNG set — web & apps", "png"),
]
DEFAULT_ICON_SIZES = {"ico": core.ICO_SIZES, "png": (16, 32, 180, 192, 512)}
FORMAT_HINTS = {
    "PNG": "Lossless. Keeps transparency and animation.",
    "JPEG": "Small photos. No transparency: transparent areas become white.",
    "WEBP": "Modern web format: small files, transparency and animation.",
    "AVIF": "Newest web format with the best compression. Keeps transparency.",
    "GIF": "256 colours. Keeps animation; good for simple graphics.",
    "TIFF": "Lossless, for print and archiving.",
    "BMP": "Uncompressed bitmap.",
}


def human_size(size: float) -> str:
    for unit in ("bytes", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def to_int(value, default: int) -> int:
    """A setting as int; a hand-edited or damaged config must not stop the app starting."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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
        self._icon_sizes = {kind: set(sizes) for kind, sizes in DEFAULT_ICON_SIZES.items()}
        self._theme_mode = "auto"

        self.setWindowTitle("IconPress")
        self.setWindowIcon(QIcon(app_icon_pixmap()))
        self.setAcceptDrops(True)
        self.setMinimumSize(900, 620)
        self.resize(1080, 700)

        self._preview_timer = QTimer(self, singleShot=True, interval=30)
        self._preview_timer.timeout.connect(self._update_preview)

        self._build()
        self._load_settings()
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

        QShortcut(QKeySequence.StandardKey.Open, self, self.add_files_dialog)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self, self._on_run)

    def _build_header(self) -> QHBoxLayout:
        logo = QLabel()
        pixmap = app_icon_pixmap(88)
        pixmap.setDevicePixelRatio(2)
        logo.setPixmap(pixmap)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("IconPress", "title"))
        titles.addWidget(label("Convert images and make app icons", "subtitle"))

        self.theme_control = SegmentedControl([("Auto", "auto"), ("Light", "light"),
                                               ("Dark", "dark")])
        self.theme_control.setToolTip("Appearance")
        self.theme_control.changed.connect(self._set_theme)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(logo)
        header.addLayout(titles)
        header.addStretch()
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
        self.files_title = label("Images", "sectionTitle")
        self.add_button = QPushButton("Add…", objectName="link")
        self.add_button.clicked.connect(self.add_files_dialog)
        self.clear_button = QPushButton("Clear", objectName="link")
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
        remove = QAction("Remove", self.file_list)
        remove.setShortcuts([QKeySequence.StandardKey.Delete, QKeySequence("Backspace")])
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self.remove_selected)
        reveal = QAction("Show in Folder", self.file_list)
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

        self.mode = SegmentedControl([("Convert", "convert"), ("Make icon", "icon")])
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
        frame, layout = card("Format")
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
        layout.addLayout(value_row("Quality", self.quality_value))
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(10, 100)
        self.quality.valueChanged.connect(lambda v: self.quality_value.setText(str(v)))
        layout.addWidget(self.quality)

        layout.addSpacing(4)
        layout.addWidget(label("Max size (longest side)"))
        self.resize_box = QComboBox()
        for text, value in RESIZE_CHOICES:
            self.resize_box.addItem(text, value)
        layout.addWidget(self.resize_box)
        return frame

    def _build_icon_card(self) -> QFrame:
        frame, layout = card("Icon")
        self.icon_kind = QComboBox()
        for text, value in ICON_KINDS:
            self.icon_kind.addItem(text, value)
        self.icon_kind.currentIndexChanged.connect(self._on_icon_kind)
        layout.addWidget(self.icon_kind)

        layout.addSpacing(4)
        layout.addWidget(label("Sizes"))
        self.chips_box = QWidget()
        self.chips = QGridLayout(self.chips_box)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(6)
        layout.addWidget(self.chips_box)
        self.icns_note = label("macOS icons always include every size from 16 to 1024 px.",
                               "hint")
        self.icns_note.setWordWrap(True)
        layout.addWidget(self.icns_note)

        layout.addSpacing(4)
        layout.addWidget(label("Non-square images"))
        self.fit = SegmentedControl([("Fit whole image", "pad"), ("Crop to fill", "crop")])
        self.fit.changed.connect(lambda _: self._schedule_preview())
        layout.addWidget(self.fit)

        layout.addSpacing(4)
        self.radius_value = label("", "value")
        layout.addLayout(value_row("Rounded corners", self.radius_value))
        self.radius = QSlider(Qt.Orientation.Horizontal)
        self.radius.setRange(0, 50)
        self.radius.valueChanged.connect(self._on_radius)
        layout.addWidget(self.radius)
        return frame

    def _build_output_card(self) -> QFrame:
        frame, layout = card("Save to")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.out_field = QLineEdit(readOnly=True, placeholderText="Next to the original files")
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_output_folder)
        row.addWidget(self.out_field, 1)
        row.addWidget(choose)
        layout.addLayout(row)
        self.reset_out = QPushButton("Save next to the originals", objectName="link")
        self.reset_out.clicked.connect(lambda: self._set_output_dir(""))
        layout.addWidget(self.reset_out, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(4)
        overwrite = QHBoxLayout()
        overwrite.addWidget(label("Overwrite existing files"))
        overwrite.addStretch()
        self.overwrite = Switch()
        overwrite.addWidget(self.overwrite)
        layout.addLayout(overwrite)
        hint = label("When off, a number is added instead: photo (1).png. When on, converting "
                     "to the same format next to the original replaces it.", "hint")
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
        if geometry := s.value("geometry"):
            self.restoreGeometry(geometry)
        self._on_mode(self.mode.value())
        self._on_icon_kind()

    def _save_settings(self) -> None:
        s = self.settings
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
        self.radius_value.setText("Square" if value == 0 else f"{value}%")
        self._schedule_preview()

    # ----- files --------------------------------------------------------------------------

    def add_files_dialog(self) -> None:
        if self._busy():
            return
        patterns = " ".join(f"*{ext}" for ext in core.input_extensions())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images", self._last_dir, f"Images ({patterns});;All files (*)")
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
            item = QListWidgetItem(self._placeholder_icon(), path.name)
            item.setToolTip(str(path))
            self.file_list.addItem(item)
            self.files.append(path)
            self._thumb_queue.append(path)
            added.append(path)

        if added:
            self._last_dir = str(added[0].parent)
            self.file_list.setCurrentRow(self.files.index(added[0]))
            QTimer.singleShot(0, self._load_next_thumb)
        if skipped:
            self._show_banner("error", f"Skipped {plural(len(skipped), 'file')} that "
                              f"{'is' if len(skipped) == 1 else 'are'} not an image.",
                              "Details", details="\n".join(skipped))
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

    def _placeholder_icon(self) -> QIcon:
        pixmap = QPixmap(THUMB_SIDE, THUMB_SIDE)
        pixmap.fill(Qt.GlobalColor.transparent)
        return QIcon(pixmap)

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
            self, "Save To", self.out_field.text() or self._last_dir)
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
            details.append(f"{info.frames} frames")
        try:
            image = self._preview_image(path)
            ratio = 1.0
            if self.mode.value() == "icon":
                options = self.icon_options()
                ratio = max(1.0, self.devicePixelRatioF())  # sharp on Retina / HiDPI screens
                image = core.render_icon(image, options, round(ICON_PREVIEW_SIDE * ratio))
                details = [path.name, "icon preview", options.kind.upper()]
            qimage = pil_to_qimage(image)
            qimage.setDevicePixelRatio(ratio)
            self.preview.set_image(qimage)
        except Exception as exc:
            self.preview.set_image(None)
            details.append(f"can't preview: {exc}")
        self.caption.setText("   ·   ".join(details))
        self.caption.show()

    # ----- running ------------------------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None

    def _on_run(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.run_button.setEnabled(False)
            self.run_button.setText("Stopping…")
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
        self._set_busy(False)
        self.progress.hide()
        self._results = results

        for _src, out, _error in results:
            if out in self._infos:  # an original was replaced: refresh what we show for it
                self._infos[out] = core.describe(out)
                self._previews.pop(out, None)
                self._thumb_queue.append(out)
        QTimer.singleShot(0, self._load_next_thumb)
        self._update_preview()

        saved = sum(1 for r in results if r[1])
        failed = [r for r in results if r[2]]
        stopped = "Stopped. " if len(results) < total else ""
        if failed:
            self._show_banner("error", f"{stopped}{len(failed)} of {len(results)} failed.",
                              "Details", details="\n".join(f"{src.name}: {error}"
                                                            for src, _, error in failed))
        elif saved:
            self._show_banner("ok", f"{stopped}Saved {plural(saved, 'file')}.",
                              "Show in Folder")
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.settings_area, self.mode, self.add_button, self.clear_button):
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
        self.files_title.setText(f"Images · {count}")

        fmt = core.FORMATS[self.format_box.currentData()]
        self.format_hint.setText(FORMAT_HINTS.get(fmt.name, ""))
        self.quality.setEnabled(fmt.lossy)
        self.quality_value.setText(str(self.quality.value()) if fmt.lossy else "Lossless")

        button = self.run_button
        if self._worker is not None:
            if button.text() != "Stopping…":
                button.setText("Stop")
                button.setEnabled(True)
            return
        button.setEnabled(count > 0)
        if not count:
            button.setText("Add images to start")
        elif self.mode.value() == "convert":
            button.setText(f"Convert {plural(count, 'image')} to {fmt.label}" if count > 1
                           else f"Convert to {fmt.label}")
        else:
            kind = self.icon_kind.currentData()
            noun = "icon set" if kind == "png" else f"{kind.upper()} icon"
            button.setText(f"Create {count} {noun}s" if count > 1 else f"Create {noun}")

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
    return app.exec()
