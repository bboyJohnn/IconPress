# IconPress

🖼️ Image converter and icon maker with a clean, modern interface

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Qt](https://img.shields.io/badge/GUI-PySide6%20(Qt%206)-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20|%20macOS%20|%20Linux-lightgrey.svg)

![IconPress converting images, with the before/after comparison](docs/screenshot-convert.png)
![IconPress making an icon, dark theme](docs/screenshot-icon-dark.png)

## Download

Ready-made apps for **Windows, macOS and Linux** are on the
[Releases page](https://github.com/bboyJohnn/IconPress/releases): unzip and run, no Python
needed.

The apps are not signed, so the first launch needs one extra step:

- **Windows:** if SmartScreen appears, click *More info → Run anyway*.
- **macOS:** right-click IconPress.app and choose *Open*.

## Features

### Converting
- **Formats:** PNG, JPEG, WebP, AVIF, GIF, TIFF and BMP. **Reads SVG** (drawn sharp at any
  size) and **HEIC/HEIF photos from iPhones**.
- **Many files at once.** Drop files or whole folders, or **paste a screenshot with Ctrl+V**.
  Files are processed in parallel on all CPU cores.
- **Before/after comparison:** drag the divider over the preview to compare the original and
  the result. The estimated file size and how much it shrinks are shown before you convert.
- **Quality, or a size limit:** set the quality, or give a maximum size such as "under
  500 KB". IconPress then finds the best quality that fits, and shrinks the picture only if it
  has to.
- **Resize:** by the longest side, a percentage, a width, a height, or to fit in a box. Also
  rotate and flip.
- **Watermark:** text or a logo, in any of 9 positions, with size and opacity.
- **Metadata:** EXIF (camera, date, **GPS location**) is removed by default; keep it if you
  want. You can also keep the original file dates. Phone photos are always turned upright.
- **Careful conversion:** palette GIFs, 16-bit and CMYK images convert correctly. You choose
  the colour transparency becomes in JPEG. Colour profiles are kept, and animated GIF, WebP and
  PNG stay animated.

### Icons
- **Windows** `.ico` with the sizes you pick, and **macOS** `.icns`.
- **Website favicon package:** `favicon.ico`, PNG icons, Apple touch icon, `site.webmanifest`,
  and the HTML to paste into your page. If the source is SVG, it is included as well.
- **iOS:** an `AppIcon.appiconset` for Xcode, with every iPhone and iPad size and
  `Contents.json`.
- **Android:** launcher icons for every density, round and adaptive icons, and the Play Store
  icon.
- **Look:**
  - non-square pictures are fitted or cropped, never stretched;
  - padding;
  - a solid or gradient background;
  - rounded corners;
  - a live preview of the result.

### Everything else
- **Presets:** ready-made ones (web, email, thumbnails, favicon, app icons), and you can save
  your own.
- **File names:** a template such as `{name}_{n:03}` or `{name}-{width}x{height}`, plus find
  and replace. Existing files are never overwritten unless you allow it.
- **19 languages:** English, Deutsch, Español, Français, Bahasa Indonesia, Italiano,
  Nederlands, Polski, Português (Brasil), Tiếng Việt, Türkçe, Русский, Українська, العربية,
  हिन्दी, 日本語, 한국어, 简体中文 and 繁體中文. Arabic uses a right-to-left layout.
- **Light and dark theme,** following the system automatically. Settings are remembered.
- **Command line** for scripts and automation (see below).

## Run from source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/bboyJohnn/IconPress.git
cd IconPress
python -m pip install -r requirements.txt
python IconPress.py
```

`python -m pip` installs into the same Python that runs the app. On Windows a bare `pip` can
belong to a different Python installation.

## Command line

```bash
pip install .
iconpress-cli convert *.png --to webp -q 80
iconpress-cli convert photos/ --to jpg --longest 1920 --max-kb 500 -o out/
iconpress-cli convert scan.tiff --to png --rotate 90 --name "{name}_{n:03}"
iconpress-cli convert *.jpg --to jpg --watermark-text "© Me" --watermark-position bottom-right
iconpress-cli icon logo.svg --kind favicon
iconpress-cli icon logo.png --kind ios --background "#ffffff" --padding 10
```

Without installing, use `python -m iconpress convert …` from the project folder.
`iconpress-cli convert --help` and `iconpress-cli icon --help` list every option.

## Tips

| Action | Shortcut |
| --- | --- |
| Add images | `Ctrl+O` / `⌘O` |
| Paste a screenshot or copied files | `Ctrl+V` / `⌘V` |
| Convert / create icons | `Ctrl+Enter` / `⌘↩` |
| Remove selected images | `Delete` or `Backspace` in the image list |

- **Replace the original:** keep *Save to* empty, turn on *Overwrite existing files*, and convert
  to the same format (for example, to shrink or recompress a JPEG).
- Pasted pictures are saved to *Pictures/IconPress*.
- Right-click an image in the list to remove it or show it in its folder.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

GUI tests run without a display (`QT_QPA_PLATFORM=offscreen`, set automatically by the tests).

```
iconpress/
├── core.py     # all image processing (Pillow; Qt only to draw SVG): conversion, icons, names
├── cli.py      # the iconpress-cli command
├── app.py      # main window, presets, background work, settings
├── widgets.py  # switch, segmented control, colour/position pickers, preview with comparison
├── theme.py    # light and dark themes
├── i18n.py     # translations, plural rules, language detection
└── locales/    # one JSON file per language
packaging/
└── build.py    # stand-alone apps with PyInstaller
tests/          # pytest: core, command line, translations and GUI
```

### Releases

Pushing a tag such as `v1.1.0` runs `.github/workflows/release.yml`. It builds the app on
Windows, macOS and Linux and publishes it on the Releases page. To build locally, run
`pip install pyinstaller` and then `python packaging/build.py`.

### Translations

Each language is a JSON file in `iconpress/locales/`. The keys are the English texts, as they
appear in the code inside `tr("…")`. Texts with a number use plural forms (`one`, `few`,
`many`, `other`, … following [CLDR](https://cldr.unicode.org/index/cldr-spec/plural-rules)):

```json
"Saved {n} files.": {"one": "Сохранён {n} файл.", "few": "Сохранено {n} файла.", "many": "Сохранено {n} файлов."}
```

To add a language, copy `en.json`, translate the values, and add the language to `LANGUAGES`
(and its plural rule, if it is new) in `iconpress/i18n.py`. `pytest` checks that every file
has all texts, the right placeholders and every plural form the language needs.
