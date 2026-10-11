import time

import pytest
from PIL import Image

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iconpress import core, theme  # noqa: E402
from iconpress.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    return application


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(app, settings):
    win = MainWindow(settings)
    win.show()
    yield win
    win.close()


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
    first.close()

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
        second.close()


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
        win.close()
