"""Build a stand-alone IconPress app with PyInstaller.

    pip install -r requirements.txt pyinstaller
    python packaging/build.py

The result goes to dist/: an IconPress folder (Windows, Linux) or IconPress.app (macOS),
plus a zip or tar.gz archive of it ready to upload. The app icon is drawn and converted to
.ico/.icns by IconPress itself.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))


def make_icons() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from iconpress import core
    from iconpress.widgets import app_icon_pixmap

    _app = QApplication.instance() or QApplication([])
    BUILD.mkdir(exist_ok=True)
    png = BUILD / "icon.png"
    app_icon_pixmap(1024).save(str(png))
    core.make_icon(png, BUILD / "icon.ico", core.IconOptions(kind="ico"))
    core.make_icon(png, BUILD / "icon.icns", core.IconOptions(kind="icns"))


def platform_name() -> str:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    return f"{system}-{machine}"


def build() -> None:
    import PyInstaller.__main__

    icon = BUILD / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    PyInstaller.__main__.run([
        "--noconfirm", "--clean", "--windowed",
        "--name", "IconPress",
        "--icon", str(icon),
        "--add-data", f"{ROOT / 'iconpress' / 'locales'}{os.pathsep}iconpress/locales",
        "--collect-all", "pillow_heif",
        "--osx-bundle-identifier", "io.github.bboyjohnn.iconpress",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
        str(ROOT / "IconPress.py"),
    ])


def archive() -> Path:
    from iconpress import __version__

    name = f"IconPress-{__version__}-{platform_name()}"
    if sys.platform == "darwin":
        # ditto keeps the symlinks inside the Qt frameworks; a plain zip would break the app
        target = DIST / f"{name}.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(DIST / "IconPress.app"),
                        str(target)], check=True)
        return target
    fmt = "zip" if sys.platform == "win32" else "gztar"
    return Path(shutil.make_archive(str(DIST / name), fmt, DIST, "IconPress"))


if __name__ == "__main__":
    make_icons()
    build()
    print(f"Built {archive()}")
