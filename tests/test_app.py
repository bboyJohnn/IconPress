import json
import time

import pytest
from PIL import Image

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iconpress import core, i18n, theme  # noqa: E402
from iconpress.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    return application


@pytest.fixture
def settings(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("language", "en")  # the tests check English texts, whatever the OS uses
    return settings


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.set_language("en")
    if QApplication.instance():
        QApplication.instance().setLayoutDirection(Qt.LayoutDirection.LeftToRight)


def close(win):
    """Close a window and let Qt delete it now, as the event loop would."""
    win.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def window(app, settings):
    win = MainWindow(settings)
    win.show()
    yield win
    close(win)


@pytest.fixture
def images(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    rgba = Image.new("RGBA", (120, 60), (255, 0, 0, 0))
    rgba.paste((255, 0, 0, 255), (0, 0, 60, 60))
    rgba.save(src / "logo.png")
    Image.new("P", (40, 40)).save(src / "palette.gif")
    Image.new("CMYK", (50, 50)).save(src / "print.jpg")
    return src


def run_and_wait(app, window, timeout=20):
    window._on_run()
    deadline = time.monotonic() + timeout
    while window._worker is not None:
        assert time.monotonic() < deadline, "processing did not finish"
        app.processEvents()
        time.sleep(0.01)


def test_starts_empty(window):
    assert window.file_list.count() == 0
    assert not window.run_button.isEnabled()
    assert not window.strip.isVisible()


def test_folder_drop_skips_non_images(window, images):
    (images / "notes.txt").write_text("hello")
    window.add_paths([images, images / "notes.txt"])
    assert sorted(p.name for p in window.files) == ["logo.png", "palette.gif", "print.jpg"]
    assert window.banner.isVisible() and window.banner.property("kind") == "error"
    window.add_paths([images / "logo.png"])  # duplicates are ignored
    assert window.file_list.count() == 3


def test_batch_convert_to_jpeg(app, window, images, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    window.add_paths(sorted(images.iterdir()))
    window._select(window.format_box, "JPEG")
    window._set_output_dir(str(out))
    assert window.run_button.text() == "Convert 3 images to JPEG"
    run_and_wait(app, window)
    assert sorted(p.name for p in out.iterdir()) == ["logo.jpg", "palette.jpg", "print.jpg"]
    assert window.banner.property("kind") == "ok"
    with Image.open(out / "logo.jpg") as jpg:
        assert min(jpg.getpixel((100, 30))) > 245  # transparency became white


def test_make_icons(app, window, images):
    window.add_paths([images / "logo.png"])
    window.mode.set_value("icon")
    window._select(window.icon_kind, "png")
    window.radius.setValue(20)
    assert window.icon_cards[0].isVisible() and not window.convert_cards[0].isVisible()
    assert window.run_button.text() == "Create icon set"
    run_and_wait(app, window)
    icon_dir = images / "logo-icons"
    assert (icon_dir / "icon-512.png").exists()
    with Image.open(icon_dir / "icon-512.png") as png:
        assert png.getpixel((0, 0))[3] == 0


def test_last_icon_size_cannot_be_unchecked(window):
    window._select(window.icon_kind, "ico")
    chips = [window.chips.itemAt(i).widget() for i in range(window.chips.count())]
    for chip in chips:
        chip.setChecked(False)
    assert sum(chip.isChecked() for chip in chips) == 1
    assert len(window.icon_options().sizes) == 1


def test_overwrite_replaces_original(app, window, images):
    window.add_paths([images / "logo.png"])
    window._select(window.format_box, "PNG")
    window._select(window.resize_box, "longest")
    window.size_a.setValue(64)
    window.overwrite.setChecked(True)
    run_and_wait(app, window)
    assert sorted(p.name for p in images.iterdir()) == ["logo.png", "palette.gif", "print.jpg"]
    with Image.open(images / "logo.png") as replaced:
        assert replaced.size == (64, 32)


def test_settings_are_remembered(app, settings, images):
    first = MainWindow(settings)
    first._set_theme("dark")
    first._select(first.format_box, "WEBP")
    first.quality.setValue(42)
    first.mode.set_value("icon")
    first._select(first.icon_kind, "png")
    for i in range(first.chips.count()):
        chip = first.chips.itemAt(i).widget()
        chip.setChecked(chip.text() in ("32", "1024"))
    close(first)

    second = MainWindow(settings)
    try:
        assert second._theme_mode == "dark"
        assert theme.color("window").name() == theme.DARK["window"]
        assert second.format_box.currentData() == "WEBP"
        assert second.quality.value() == 42
        assert second.mode.value() == "icon"
        assert second.icon_options().kind == "png"
        assert second.icon_options().sizes == (32, 1024)
    finally:
        second._set_theme("light")
        close(second)


def test_damaged_settings_fall_back_to_defaults(app, settings):
    for key, value in {"quality": "abc", "max_side": "", "radius": "x",
                       "icon_sizes/ico": "foo,,bar", "theme": "purple"}.items():
        settings.setValue(key, value)
    win = MainWindow(settings)
    try:
        assert win.quality.value() == 90
        assert win.icon_options().sizes == core.ICO_SIZES
        assert win._theme_mode == "auto"
    finally:
        close(win)


def test_switch_language_keeps_files_and_options(app, window, images, settings):
    window.add_paths([images / "logo.png", images / "palette.gif"])
    window._select(window.format_box, "WEBP")
    window._select(window.language_box, "de")
    assert window.run_button.text() == "2 Bilder in WebP konvertieren"
    assert window.file_list.count() == 2 and window.format_box.currentData() == "WEBP"
    assert settings.value("language") == "de"

    window._select(window.language_box, "ar")
    assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert window.run_button.text() == "تحويل صورتين إلى WebP"

    window._select(window.language_box, "ru")
    assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
    window.add_paths([images / "print.jpg"])
    assert window.run_button.text() == "Конвертировать 3 изображения в WebP"


def test_language_follows_system_when_auto(app, settings, monkeypatch):
    from PySide6.QtCore import QLocale

    settings.setValue("language", "auto")
    monkeypatch.setattr(QLocale, "system", staticmethod(lambda: QLocale("pl_PL")))
    win = MainWindow(settings)
    try:
        assert i18n.language() == "pl"
        assert win.run_button.text() == "Dodaj obrazy, aby zacząć"
        assert win.language_box.currentData() == "auto"
    finally:
        close(win)


def wait_for(app, condition, timeout=20):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        app.processEvents()
        time.sleep(0.01)


def test_compare_preview_and_size_estimate(app, window, images):
    window.add_paths([images / "logo.png"])
    window._select(window.format_box, "JPEG")
    wait_for(app, lambda: window.preview.is_comparing())
    assert "≈" in window.caption.text()
    window.mode.set_value("icon")
    assert not window.preview.is_comparing()


def test_all_options_reach_the_converter(app, window, images, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    window.add_paths([images / "logo.png", images / "print.jpg"])
    window._select(window.format_box, "JPEG")
    window._select(window.resize_box, "width")
    window.size_a.setValue(60)
    window.rotate.set_value(90)
    window.flip_h.setChecked(True)
    window.background_button.set_color("#00ff00")
    window.wm_switch.setChecked(True)
    window.wm_text.setText("IconPress")
    window.target_switch.setChecked(True)
    window.target_kb.setValue(30)
    window.template.setText("pic_{n:02}")
    window._set_output_dir(str(out))
    options = window.convert_options()
    assert (options.resize, options.size[0], options.rotate, options.flip_h) == (
        "width", 60, 90, True)
    assert options.watermark.text == "IconPress" and options.target_kb == 30
    assert "pic_01.jpg" in window.name_preview.text()
    run_and_wait(app, window)
    assert sorted(p.name for p in out.iterdir()) == ["pic_01.jpg", "pic_02.jpg"]
    with Image.open(out / "pic_01.jpg") as first:
        assert first.width == 60  # turned, then 60 px wide


def test_icon_look_and_kinds(app, window, images):
    window.add_paths([images / "logo.png"])
    window.mode.set_value("icon")
    window.padding.setValue(20)
    window.icon_bg.set_value("gradient")
    assert window.icon_bg1.isVisible() and window.icon_bg2.isVisible()
    options = window.icon_options()
    assert options.padding == 0.2 and options.background and options.background2
    for kind in ("favicon", "ios", "android"):
        window._select(window.icon_kind, kind)
        assert not window.chips_box.isVisible() and window.kind_note.isVisible()
    run_and_wait(app, window)
    assert (images / "logo-android" / "playstore-icon.png").exists()


def test_presets(app, window, settings):
    index = window.preset_box.findText("For email: JPEG under 500 KB")
    window.preset_box.setCurrentIndex(index)
    window._on_preset(index)
    assert window.format_box.currentData() == "JPEG"
    assert window.convert_options().target_kb == 500

    window.quality.setValue(55)
    from PySide6.QtWidgets import QInputDialog

    original = QInputDialog.getText
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Mine", True))
    try:
        window.save_preset()
    finally:
        QInputDialog.getText = original
    assert json.loads(settings.value("presets"))["Mine"]["quality"] == 55
    assert window.delete_preset_button.isVisible()
    window.quality.setValue(90)
    window._on_preset(window.preset_box.findText("Mine"))
    assert window.quality.value() == 55
    window.delete_preset()
    assert window.preset_box.findText("Mine") == -1


def test_paste_picture_from_clipboard(app, window, tmp_path, monkeypatch):
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtGui import QColor, QImage

    monkeypatch.setattr(QStandardPaths, "writableLocation", staticmethod(lambda _: str(tmp_path)))
    picture = QImage(30, 20, QImage.Format.Format_RGB32)
    picture.fill(QColor("red"))
    QApplication.clipboard().setImage(picture)
    window.paste_from_clipboard()
    assert len(window.files) == 1
    assert window.files[0].parent == tmp_path / "IconPress"
    assert window.banner.property("kind") == "ok"
    QApplication.clipboard().clear()
    window.paste_from_clipboard()
    assert window.banner.property("kind") == "error"


def test_bad_template_is_reported(app, window, images):
    window.add_paths([images / "logo.png"])
    window.template.setText("{oops}")
    assert "template" in window.name_preview.text().lower()
    window._on_run()
    assert window._worker is None and window.banner.property("kind") == "error"
