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
    assert window.icon_card.isVisible() and not window.convert_card.isVisible()
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
    window._select(window.resize_box, 512)
    window.overwrite.setChecked(True)
    run_and_wait(app, window)
    assert sorted(p.name for p in images.iterdir()) == ["logo.png", "palette.gif", "print.jpg"]


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
