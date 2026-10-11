"""Image processing for IconPress: format conversion and icon generation.

Everything here is plain Pillow, so it can be tested and reused without the window (the
command line uses it too). Qt is only used, when installed, to draw SVG files, and
pillow-heif, when installed, adds HEIC/HEIF photos from iPhones.
"""

from __future__ import annotations

import io
import itertools
import json
import os
import shutil
import string
import sys
import warnings
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageFont,
    ImageOps,
    ImageSequence,
    features,
)

try:  # optional: HEIC/HEIF photos; a missing or mismatched plugin just means no HEIC
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF = True
except Exception:  # pragma: no cover - depends on the installation
    HEIF = False

LANCZOS = Image.Resampling.LANCZOS
_EXIF_ORIENTATION = 0x0112
SVG_EXTENSIONS = (".svg", ".svgz")


@dataclass(frozen=True)
class OutputFormat:
    name: str  # Pillow format name
    label: str
    ext: str
    lossy: bool = False
    alpha: bool = True
    animation: bool = False
    feature: str | None = None  # Pillow feature the format depends on


CONVERT_FORMATS = (
    OutputFormat("PNG", "PNG", "png", animation=True),
    OutputFormat("JPEG", "JPEG", "jpg", lossy=True, alpha=False),
    OutputFormat("WEBP", "WebP", "webp", lossy=True, animation=True, feature="webp"),
    OutputFormat("AVIF", "AVIF", "avif", lossy=True, feature="avif"),
    OutputFormat("GIF", "GIF", "gif", animation=True),
    OutputFormat("TIFF", "TIFF", "tiff"),
    OutputFormat("BMP", "BMP", "bmp"),
)
FORMATS = {f.name: f for f in CONVERT_FORMATS}

# Modes each format can be written in without Pillow raising or silently dropping data.
_SAVE_MODES = {
    "PNG": {"1", "L", "LA", "I;16", "P", "RGB", "RGBA"},
    "JPEG": {"L", "RGB"},
    "WEBP": {"RGB", "RGBA"},
    "AVIF": {"RGB", "RGBA"},
    "GIF": {"L", "P", "RGB", "RGBA"},
    "TIFF": {"1", "L", "LA", "I;16", "P", "RGB", "RGBA", "CMYK"},
    "BMP": {"1", "L", "P", "RGB", "RGBA"},
}
_EXIF_FORMATS = {"JPEG", "WEBP", "PNG", "TIFF", "AVIF"}

RESIZE_MODES = ("none", "longest", "percent", "width", "height", "box")
POSITIONS = ("top-left", "top", "top-right", "left", "center", "right",
             "bottom-left", "bottom", "bottom-right")

ICON_KINDS = ("ico", "icns", "png", "favicon", "ios", "android")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)
PNG_SIZES = (16, 32, 48, 64, 128, 180, 192, 256, 512, 1024)
ICON_SIZES = {"ico": ICO_SIZES, "png": PNG_SIZES}  # kinds whose sizes can be picked
_FOLDER_KINDS = {"png": "-icons", "favicon": "-favicon", "ios": "-ios", "android": "-android"}

# Common image types for the file dialog; anything else Pillow opens still works via drop.
_DIALOG_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".avif", ".heic", ".heif", ".svg", ".gif",
    ".bmp", ".tif", ".tiff", ".ico", ".icns", ".tga", ".psd", ".ppm", ".pgm", ".pbm", ".jp2",
)


@dataclass
class Watermark:
    text: str = ""
    image: str | None = None  # a logo file; PNG with transparency works best
    position: str = "bottom-right"
    size: float = 0.25  # width of the mark as a fraction of the picture width
    opacity: float = 0.7
    color: str = "#ffffff"  # text colour
    margin: float = 0.03  # distance from the edges, as a fraction of the shorter side


@dataclass
class ConvertOptions:
    format: str = "PNG"
    quality: int = 90
    resize: str = "none"  # one of RESIZE_MODES
    size: tuple[int, int] = (0, 0)  # longest side / percent / width use [0], height [1], box both
    enlarge: bool = False  # let "longest", "width", "height" and "box" make small images bigger
    rotate: int = 0  # degrees clockwise: 0, 90, 180 or 270
    flip_h: bool = False
    flip_v: bool = False
    background: str = "#ffffff"  # fills transparent areas in formats without transparency
    target_kb: int | None = None  # lossy formats: the best quality that fits in this size
    keep_metadata: bool = False  # EXIF: camera, date, location
    watermark: Watermark | None = None


@dataclass
class IconOptions:
    kind: str = "ico"
    sizes: tuple[int, ...] = ICO_SIZES  # only for "ico" and "png"; other kinds have fixed sets
    fit: str = "pad"  # "pad" keeps the whole picture, "crop" fills the square
    corner_radius: float = 0.0  # fraction of the side, 0 to 0.5
    padding: float = 0.0  # empty margin around the picture, fraction of the side, 0 to 0.4
    background: str | None = None  # None keeps transparency
    background2: str | None = None  # second colour for a top-to-bottom gradient


@dataclass
class OutputOptions:
    folder: str | os.PathLike | None = None  # None saves next to each original
    overwrite: bool = False
    template: str = "{name}"  # {name}, {n}, {width}, {height}, {ext}; {n:03} pads with zeros
    find: str = ""  # text to replace in the original name
    replace: str = ""
    keep_dates: bool = False  # give results the original's modification time


@dataclass
class ImageInfo:
    width: int
    height: int
    format: str
    frames: int
    file_size: int


@dataclass
class _Source:
    frames: list[Image.Image]
    mode: str
    durations: list[int] = field(default_factory=list)
    loop: int | None = None
    icc_profile: bytes | None = None
    exif: bytes | None = None
    vector: bool = False  # drawn from SVG at the size it is needed


# ----- formats and inputs ----------------------------------------------------------------

def _has_feature(name: str) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # older Pillow warns about features it doesn't know
        return bool(features.check(name))


def available_formats() -> list[OutputFormat]:
    return [f for f in CONVERT_FORMATS if f.feature is None or _has_feature(f.feature)]


def svg_supported() -> bool:
    try:
        from PySide6.QtSvg import QSvgRenderer  # noqa: F401
    except ImportError:
        return False
    return True


def is_svg(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in SVG_EXTENSIONS


def readable_extensions() -> set[str]:
    """Every file extension that can be opened."""
    exts = {ext for ext, fmt in Image.registered_extensions().items() if fmt in Image.OPEN}
    if svg_supported():
        exts.update(SVG_EXTENSIONS)
    return exts


def input_extensions() -> list[str]:
    """Extensions offered in the open dialog, limited to what can actually be read."""
    readable = readable_extensions()
    return [ext for ext in _DIALOG_EXTENSIONS if ext in readable]


# ----- SVG -------------------------------------------------------------------------------

_qt_app = None


def _svg_renderer(path):
    global _qt_app
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtSvg import QSvgRenderer

    if QGuiApplication.instance() is None:  # command line: Qt needs an app to draw text
        _qt_app = QGuiApplication([sys.argv[0] or "iconpress", "-platform", "offscreen"])
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        raise ValueError(f"{Path(path).name} is not a valid SVG file")
    return renderer


def _svg_size(renderer) -> tuple[int, int]:
    size = renderer.defaultSize()
    if size.width() > 0 and size.height() > 0:
        return size.width(), size.height()
    box = renderer.viewBoxF()
    if box.width() > 0 and box.height() > 0:
        return round(box.width()), round(box.height())
    return 512, 512


def render_svg(path: str | os.PathLike, longest: int | None = None) -> Image.Image:
    """Draw an SVG file as RGBA, scaled so its longest side is ``longest`` pixels."""
    from PySide6.QtGui import QImage, QPainter

    renderer = _svg_renderer(path)
    width, height = _svg_size(renderer)
    if longest:
        scale = longest / max(width, height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
    canvas = QImage(width, height, QImage.Format.Format_RGBA8888)
    canvas.fill(0)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    data = bytes(canvas.constBits())
    return Image.frombuffer("RGBA", (width, height), data, "raw", "RGBA",
                            canvas.bytesPerLine(), 1).copy()


# ----- reading -----------------------------------------------------------------------------

def describe(path: str | os.PathLike) -> ImageInfo:
    """Read basic facts about an image without decoding it; raises if it can't be opened."""
    if is_svg(path):
        width, height = _svg_size(_svg_renderer(path))
        return ImageInfo(width, height, "SVG", 1, os.path.getsize(path))
    with Image.open(path) as im:
        width, height = im.size
        if im.getexif().get(_EXIF_ORIENTATION, 1) in (5, 6, 7, 8):
            width, height = height, width
        return ImageInfo(width, height, im.format or "", getattr(im, "n_frames", 1),
                         os.path.getsize(path))


def load_preview(path: str | os.PathLike, max_side: int) -> Image.Image:
    """First frame, upright, as RGBA and no larger than ``max_side``."""
    if is_svg(path):
        return render_svg(path, max_side)
    with Image.open(path) as im:
        im.draft("RGB", (max_side, max_side))  # lets JPEG decode at a reduced scale
        img = ImageOps.exif_transpose(im).convert("RGBA")
    img.thumbnail((max_side, max_side), LANCZOS)
    return img


def _load(path: str | os.PathLike, svg_longest: int | None = None) -> _Source:
    # Everything is copied into memory and the file closed before returning, so the
    # result can be saved over the very file it came from.
    if is_svg(path):
        return _Source(frames=[render_svg(path, svg_longest)], mode="RGBA", vector=True)
    with Image.open(path) as im:
        source = _Source(frames=[], mode=im.mode, icc_profile=im.info.get("icc_profile"))
        if getattr(im, "n_frames", 1) > 1:
            source.loop = im.info.get("loop")
            for frame in ImageSequence.Iterator(im):
                source.durations.append(int(frame.info.get("duration", 100)))
                source.frames.append(frame.convert("RGBA"))
        else:
            upright = ImageOps.exif_transpose(im)  # also drops the orientation tag
            source.frames.append(upright)
            source.exif = upright.info.get("exif")
    return source


# ----- pixels ------------------------------------------------------------------------------

def _rgb(colour: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(colour)[:3]


def _color_family(mode: str) -> str:
    if mode == "CMYK":
        return "cmyk"
    if mode in ("1", "L", "LA", "F") or mode.startswith("I"):
        return "gray"
    return "rgb"


def _to_8bit_gray(img: Image.Image) -> Image.Image:
    img = img.convert("F" if img.mode == "F" else "I")
    if img.getextrema()[1] > 255:
        img = img.point(lambda v: v / 256)
    return img.convert("L")


def flatten(img: Image.Image, background="#ffffff") -> Image.Image:
    """Composite transparent pixels onto a solid background colour."""
    rgba = img.convert("RGBA")
    result = Image.new("RGB", rgba.size, _rgb(background) if isinstance(background, str)
                       else background)
    result.paste(rgba, mask=rgba.getchannel("A"))
    return result


def prepare_for_format(img: Image.Image, fmt: str, background="#ffffff") -> Image.Image:
    """Convert ``img`` to a mode that ``fmt`` can store, keeping transparency where possible."""
    allowed = _SAVE_MODES[fmt]
    has_alpha = img.has_transparency_data
    if not FORMATS[fmt].alpha and has_alpha:
        return flatten(img, background)
    if img.mode in allowed:
        return img
    if _color_family(img.mode) == "gray" and not has_alpha:
        img = _to_8bit_gray(img)
        if img.mode in allowed:
            return img
    return img.convert("RGBA" if has_alpha and "RGBA" in allowed else "RGB")


def target_size(width: int, height: int, options: ConvertOptions) -> tuple[int, int]:
    """The output size for a ``width`` × ``height`` picture under the resize options."""
    first, second = options.size
    mode = options.resize
    if mode == "longest" and first > 0:
        scale = first / max(width, height)
    elif mode == "percent" and first > 0:
        scale = first / 100
    elif mode == "width" and first > 0:
        scale = first / width
    elif mode == "height" and second > 0:
        scale = second / height
    elif mode == "box" and first > 0 and second > 0:
        scale = min(first / width, second / height)
    else:
        return width, height
    if scale > 1 and not options.enlarge and mode != "percent":
        return width, height
    return max(1, round(width * scale)), max(1, round(height * scale))


def _resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    if img.size == size:
        return img
    if img.mode in ("1", "P"):  # palette images would otherwise be resized with NEAREST
        img = img.convert("RGBA" if img.has_transparency_data else "RGB")
    return img.resize(size, LANCZOS)


_ROTATIONS = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
              270: Image.Transpose.ROTATE_90}  # Pillow turns counter-clockwise


def _orient(img: Image.Image, options: ConvertOptions) -> Image.Image:
    if options.rotate % 360 in _ROTATIONS:
        img = img.transpose(_ROTATIONS[options.rotate % 360])
    if options.flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if options.flip_v:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return img


_FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def _place(canvas: tuple[int, int], mark: tuple[int, int], mark_options: Watermark):
    (width, height), (w, h) = canvas, mark
    gap = round(min(width, height) * mark_options.margin)
    vertical, _, horizontal = mark_options.position.partition("-")
    if not horizontal and vertical in ("left", "right"):
        vertical, horizontal = "center", vertical
    x = {"left": gap, "right": width - w - gap}.get(horizontal, (width - w) // 2)
    y = {"top": gap, "bottom": height - h - gap}.get(vertical, (height - h) // 2)
    return x, y


def _watermark_layer(size: tuple[int, int], mark: Watermark) -> Image.Image | None:
    """A transparent layer the size of the picture holding the text or logo."""
    width, height = size
    target = max(1, round(width * mark.size))
    if mark.image:
        with Image.open(mark.image) as logo_file:
            logo = ImageOps.exif_transpose(logo_file).convert("RGBA")
        logo = logo.resize((target, max(1, round(logo.height * target / logo.width))), LANCZOS)
    elif mark.text.strip():
        probe = _font(100)
        box = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), mark.text, font=probe)
        font = _font(max(6, round(100 * target / max(1, box[2] - box[0]))))
        box = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), mark.text, font=font)
        shadow = max(1, (box[3] - box[1]) // 20)  # a soft dark copy keeps light text readable
        logo = Image.new("RGBA", (box[2] - box[0] + shadow, box[3] - box[1] + shadow))
        draw = ImageDraw.Draw(logo)
        draw.text((shadow - box[0], shadow - box[1]), mark.text, font=font, fill=(0, 0, 0, 90))
        draw.text((-box[0], -box[1]), mark.text, font=font, fill=_rgb(mark.color) + (255,))
    else:
        return None
    alpha = logo.getchannel("A").point(lambda a: round(a * max(0.0, min(mark.opacity, 1.0))))
    logo.putalpha(alpha)
    layer = Image.new("RGBA", size)
    layer.alpha_composite(logo, _place(size, logo.size, mark))
    return layer


def _apply_watermark(img: Image.Image, layer: Image.Image | None) -> Image.Image:
    if layer is None:
        return img
    had_alpha = img.has_transparency_data
    result = img.convert("RGBA")
    result.alpha_composite(layer)
    return result if had_alpha else result.convert("RGB")


# ----- conversion --------------------------------------------------------------------------

def _process(src: str | os.PathLike, options: ConvertOptions):
    """Decode, turn, resize and mark ``src``; returns frames ready to encode."""
    spec = FORMATS[options.format]
    svg_longest = None
    if is_svg(src):
        width, height = _svg_size(_svg_renderer(src))
        if options.rotate % 180 == 90:
            width, height = height, width
        svg_longest = max(target_size(width, height, options))
    source = _load(src, svg_longest)
    frames = source.frames if spec.animation else source.frames[:1]
    frames = [_orient(f, options) for f in frames]
    if not source.vector:
        size = target_size(*frames[0].size, options)
        frames = [_resize(f, size) for f in frames]
    if options.watermark:
        layer = _watermark_layer(frames[0].size, options.watermark)
        frames = [_apply_watermark(f, layer) for f in frames]
    frames = [prepare_for_format(f, spec.name, options.background) for f in frames]
    return spec, source, frames


def _save_params(spec: OutputFormat, source: _Source, frames, options: ConvertOptions) -> dict:
    params: dict = {}
    if spec.lossy:
        params["quality"] = options.quality
    if spec.name == "JPEG":
        params["optimize"] = True
    if spec.name == "TIFF" and features.check("libtiff"):
        params["compression"] = "tiff_lzw"
    if (source.icc_profile and spec.name not in ("GIF", "BMP")
            and _color_family(source.mode) == _color_family(frames[0].mode)):
        params["icc_profile"] = source.icc_profile
    if options.keep_metadata and source.exif and spec.name in _EXIF_FORMATS:
        params["exif"] = source.exif
    if len(frames) > 1:
        params.update(save_all=True, append_images=frames[1:], duration=source.durations)
        if source.loop is not None:
            params["loop"] = source.loop
        if spec.name == "GIF":
            params["disposal"] = 2
    return params


def _encode(frames, spec: OutputFormat, params: dict) -> bytes:
    buffer = io.BytesIO()
    frames[0].save(buffer, format=spec.name, **params)
    return buffer.getvalue()


def _best_quality(frames, spec: OutputFormat, params: dict, limit: int, best: int) -> bytes:
    """Encode at the highest quality (up to ``best``) whose file is at most ``limit`` bytes."""
    low, high = 5, best
    result = _encode(frames, spec, {**params, "quality": low})
    while low <= high:
        quality = (low + high) // 2
        data = _encode(frames, spec, {**params, "quality": quality})
        if len(data) <= limit:
            result, low = data, quality + 1
        else:
            high = quality - 1
    return result


def _encode_to_fit(frames, spec: OutputFormat, params: dict, limit: int, best: int) -> bytes:
    """Fit the file in ``limit`` bytes: lower the quality first, then the size if needed."""
    data = _best_quality(frames, spec, params, limit, best)
    scale = 1.0
    while len(data) > limit and scale > 0.05:
        scale *= 0.8
        size = (max(1, round(frames[0].width * scale)), max(1, round(frames[0].height * scale)))
        smaller = [_resize(f, size) for f in frames]
        if len(smaller) > 1:
            params = {**params, "append_images": smaller[1:]}
        data = _best_quality(smaller, spec, params, limit, best)
    return data


def encode(src: str | os.PathLike, options: ConvertOptions) -> tuple[Image.Image, bytes]:
    """The processed picture before encoding, and the encoded file, without writing it."""
    spec, source, frames = _process(src, options)
    params = _save_params(spec, source, frames, options)
    if options.target_kb and spec.lossy:
        data = _encode_to_fit(frames, spec, params, options.target_kb * 1024, options.quality)
    else:
        data = _encode(frames, spec, params)
    return frames[0], data


def convert_image(src: str | os.PathLike, dst: str | os.PathLike,
                  options: ConvertOptions) -> Path:
    _, data = encode(src, options)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return dst


# ----- icons -------------------------------------------------------------------------------

def square(img: Image.Image, fit: str = "pad") -> Image.Image:
    """Make ``img`` square without distorting it, by padding or by cropping the centre."""
    img = img.convert("RGBA")
    width, height = img.size
    if width == height:
        return img
    if fit == "crop":
        side = min(width, height)
        left, top = (width - side) // 2, (height - side) // 2
        return img.crop((left, top, left + side, top + side))
    side = max(width, height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - width) // 2, (side - height) // 2))
    return canvas


def round_corners(img: Image.Image, radius: float) -> Image.Image:
    """Clip the corners of an RGBA image; ``radius`` is a fraction of the shorter side."""
    if radius <= 0:
        return img
    scale = 4  # draw the mask larger and shrink it for smooth edges
    width, height = img.size
    mask = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=min(radius, 0.5) * min(width, height) * scale,
        fill=255,
    )
    mask = mask.resize((width, height), LANCZOS)
    result = img.copy()
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
    return result


def icon_background(size: int, options: IconOptions, opaque: bool = False) -> Image.Image:
    first = options.background or ("#ffffff" if opaque else None)
    if first is None:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    top = Image.new("RGBA", (size, size), _rgb(first) + (255,))
    if not options.background2:
        return top
    bottom = Image.new("RGBA", (size, size), _rgb(options.background2) + (255,))
    return Image.composite(bottom, top, Image.linear_gradient("L").resize((size, size)))


def render_icon(img: Image.Image, options: IconOptions, size: int, *, opaque: bool = False,
                rounded: bool = True) -> Image.Image:
    """The finished icon artwork at ``size`` pixels: background, picture, corners."""
    inner = max(1, round(size * (1 - 2 * max(0.0, min(options.padding, 0.45)))))
    art = square(img, options.fit).resize((inner, inner), LANCZOS)
    canvas = icon_background(size, options, opaque)
    offset = (size - inner) // 2
    canvas.alpha_composite(art, (offset, offset))
    return round_corners(canvas, options.corner_radius) if rounded else canvas


def icon_sizes(options: IconOptions) -> tuple[int, ...]:
    if options.kind not in ICON_SIZES:
        return ICNS_SIZES
    allowed = ICON_SIZES[options.kind]
    return tuple(sorted(s for s in set(options.sizes) if s in allowed)) or allowed


def _icon_source(src: str | os.PathLike, options: IconOptions, master: int) -> Image.Image:
    if is_svg(src):
        width, height = _svg_size(_svg_renderer(src))
        stretch = max(width, height) / min(width, height) if options.fit == "crop" else 1
        return render_svg(src, min(4096, round(master * stretch)))
    return _load(src).frames[0]


def _save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)


_IOS_ICONS = (  # (idiom, size in points, scale)
    ("iphone", 20, 2), ("iphone", 20, 3), ("iphone", 29, 2), ("iphone", 29, 3),
    ("iphone", 40, 2), ("iphone", 40, 3), ("iphone", 60, 2), ("iphone", 60, 3),
    ("ipad", 20, 1), ("ipad", 20, 2), ("ipad", 29, 1), ("ipad", 29, 2),
    ("ipad", 40, 1), ("ipad", 40, 2), ("ipad", 76, 1), ("ipad", 76, 2), ("ipad", 83.5, 2),
    ("ios-marketing", 1024, 1),
)
_ANDROID_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
_ADAPTIVE_XML = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
</adaptive-icon>
"""


def _make_favicon(img, src, dst: Path, options: IconOptions) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    render_icon(img, options, 48).save(dst / "favicon.ico", format="ICO",
                                       sizes=[(16, 16), (32, 32), (48, 48)])
    _save_png(render_icon(img, options, 96), dst / "favicon-96x96.png")
    # iOS fills transparency with black and rounds the corners itself
    _save_png(render_icon(img, options, 180, opaque=True, rounded=False),
              dst / "apple-touch-icon.png")
    for size in (192, 512):
        _save_png(render_icon(img, options, size), dst / f"web-app-manifest-{size}x{size}.png")
    links = ['<link rel="icon" type="image/png" href="/favicon-96x96.png" sizes="96x96" />']
    if is_svg(src):
        shutil.copyfile(src, dst / "favicon.svg")
        links.append('<link rel="icon" type="image/svg+xml" href="/favicon.svg" />')
    links += ['<link rel="shortcut icon" href="/favicon.ico" />',
              '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />',
              '<link rel="manifest" href="/site.webmanifest" />']
    (dst / "favicon.html").write_text("\n".join(links) + "\n", encoding="utf-8")
    colour = options.background or "#ffffff"
    manifest = {
        "name": Path(src).stem, "short_name": Path(src).stem,
        "icons": [{"src": f"/web-app-manifest-{s}x{s}.png", "sizes": f"{s}x{s}",
                   "type": "image/png", "purpose": "any"} for s in (192, 512)],
        "theme_color": colour, "background_color": colour, "display": "standalone",
    }
    (dst / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n",
                                          encoding="utf-8")


def _make_ios(img, dst: Path, options: IconOptions) -> None:
    folder = dst / "AppIcon.appiconset"
    folder.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, Image.Image] = {}
    images = []
    for idiom, points, scale in _IOS_ICONS:
        pixels = round(points * scale)
        label = f"{points:g}x{points:g}"
        name = f"Icon-App-{label}@{scale}x.png"
        if pixels not in rendered:  # the App Store rejects transparency; iOS rounds itself
            rendered[pixels] = render_icon(img, options, pixels, opaque=True,
                                           rounded=False).convert("RGB")
        _save_png(rendered[pixels], folder / name)
        images.append({"size": label, "idiom": idiom, "filename": name, "scale": f"{scale}x"})
    contents = {"images": images, "info": {"version": 1, "author": "xcode"}}
    (folder / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n",
                                          encoding="utf-8")


def _make_android(img, dst: Path, options: IconOptions) -> None:
    res = dst / "res"
    # Adaptive icon layers are 108 dp and launchers mask them to about 66 dp: keep the
    # picture inside that safe zone
    foreground_options = IconOptions(fit=options.fit, padding=max(0.19, options.padding))
    for density, factor in _ANDROID_DENSITIES.items():
        folder = res / f"mipmap-{density}"
        size = round(48 * factor)
        _save_png(render_icon(img, options, size), folder / "ic_launcher.png")
        round_icon = render_icon(img, IconOptions(
            fit=options.fit, padding=options.padding, corner_radius=0.5,
            background=options.background, background2=options.background2), size)
        _save_png(round_icon, folder / "ic_launcher_round.png")
        _save_png(render_icon(img, foreground_options, round(108 * factor)),
                  folder / "ic_launcher_foreground.png")
    anydpi = res / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (anydpi / name).write_text(_ADAPTIVE_XML, encoding="utf-8")
    values = res / "values"
    values.mkdir(parents=True, exist_ok=True)
    colour = (options.background or "#ffffff").upper()
    (values / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <color name="ic_launcher_background">{colour}</color>\n</resources>\n',
        encoding="utf-8")
    _save_png(render_icon(img, options, 512, opaque=True, rounded=False).convert("RGB"),
              dst / "playstore-icon.png")


def make_icon(src: str | os.PathLike, dst: str | os.PathLike, options: IconOptions) -> Path:
    """Write the icon file, or the folder of icons for the folder kinds, to ``dst``."""
    dst = Path(dst)
    kind = options.kind
    if kind == "favicon":
        _make_favicon(_icon_source(src, options, 512), src, dst, options)
    elif kind == "ios":
        _make_ios(_icon_source(src, options, 1024), dst, options)
    elif kind == "android":
        _make_android(_icon_source(src, options, 512), dst, options)
    elif kind in ("ico", "icns", "png"):
        sizes = icon_sizes(options)
        master = render_icon(_icon_source(src, options, max(sizes)), options, max(sizes))
        if kind == "png":
            dst.mkdir(parents=True, exist_ok=True)
            for size in sizes:
                master.resize((size, size), LANCZOS).save(dst / f"icon-{size}.png")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if kind == "ico":
                master.save(dst, format="ICO", sizes=[(s, s) for s in sizes])
            else:
                master.save(dst, format="ICNS")
    else:
        raise ValueError(f"Unknown icon kind: {kind}")
    return dst


# ----- file names --------------------------------------------------------------------------

_BAD_CHARACTERS = str.maketrans({c: "_" for c in '<>:"/\\|?*\0'})


class _Fields(dict):
    def __missing__(self, key):
        raise ValueError(f"Unknown placeholder {{{key}}} in the name template")


def render_name(src: str | os.PathLike, index: int, output: OutputOptions) -> str:
    """The new file name (without extension) for the ``index``-th file, from the template."""
    src = Path(src)
    stem = src.stem.replace(output.find, output.replace) if output.find else src.stem
    template = output.template or "{name}"
    fields = _Fields(name=stem, n=index, ext=src.suffix.lstrip("."))
    if any(name in ("width", "height")
           for _, name, _, _ in string.Formatter().parse(template) if name):
        info = describe(src)
        fields.update(width=info.width, height=info.height)
    try:
        name = string.Formatter().vformat(template, (), fields)
    except (IndexError, KeyError) as exc:
        raise ValueError(f"Can't use the name template {template!r}") from exc
    name = name.translate(_BAD_CHARACTERS).strip().rstrip(".")
    return name or stem


def output_path(src: str | os.PathLike, ext: str, out_dir: str | os.PathLike | None = None,
                overwrite: bool = False, reserved: Collection[Path] = (), suffix: str = "",
                stem: str | None = None) -> Path:
    """Where the result for ``src`` goes: next to it, or in ``out_dir``.

    Unless ``overwrite`` is set, an existing name gets a counter: ``photo (1).png``.
    Files planned earlier in the same batch are passed as ``reserved``, so two inputs
    with the same name never end up in one output file, even with ``overwrite``.
    """
    src = Path(src)
    folder = Path(out_dir) if out_dir else src.parent
    stem = (src.stem if stem is None else stem) + suffix
    ext = f".{ext}" if ext else ""
    target = folder / f"{stem}{ext}"
    if overwrite and target not in reserved:
        return target
    for n in itertools.count():
        candidate = folder / (f"{stem} ({n}){ext}" if n else f"{stem}{ext}")
        if not candidate.exists() and candidate not in reserved:
            return candidate
    raise AssertionError("unreachable")


def plan_outputs(sources: Sequence[str | os.PathLike], ext: str, output: OutputOptions,
                 suffix: str = "") -> list[Path]:
    """Output paths for a whole batch, decided up front so files can be written in parallel."""
    planned: list[Path] = []
    for index, src in enumerate(sources, 1):
        stem = render_name(src, index, output)
        planned.append(output_path(src, ext, output.folder, output.overwrite, planned, suffix,
                                   stem))
    return planned


def icon_target(options: IconOptions) -> tuple[str, str]:
    """(extension, suffix) of what ``make_icon`` writes: a file, or a folder for some kinds."""
    if options.kind in _FOLDER_KINDS:
        return "", _FOLDER_KINDS[options.kind]
    return options.kind, ""


def copy_dates(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    stat = os.stat(src)
    targets = [Path(dst)]
    if Path(dst).is_dir():
        targets += [p for p in Path(dst).rglob("*")]
    for target in targets:
        os.utime(target, (stat.st_atime, stat.st_mtime))


# ----- batches -----------------------------------------------------------------------------

def default_workers() -> int:
    """Threads for a batch: Pillow works outside the GIL; capped to bound memory use."""
    return max(1, min(8, os.cpu_count() or 2))


def init_svg() -> None:
    """Create the Qt app SVG drawing needs, from the main thread, before any batch starts."""
    if svg_supported():
        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance() is None:
            global _qt_app
            from PySide6.QtGui import QGuiApplication

            _qt_app = QGuiApplication([sys.argv[0] or "iconpress", "-platform", "offscreen"])


def run_batch(jobs: Sequence[tuple[Path, Path]], work, workers: int | None = None,
              keep_dates: bool = False):
    """Run ``work(src, dst)`` for every job on a thread pool.

    Yields ``(src, dst, error)`` as each file finishes (``dst`` is None after an error).
    Closing the generator early cancels the files that have not started.
    """
    def one(src: Path, dst: Path):
        try:
            work(src, dst)
            if keep_dates:
                copy_dates(src, dst)
        except Exception as exc:  # reported per file, the batch carries on
            return src, None, str(exc) or type(exc).__name__
        return src, dst, None

    pool = ThreadPoolExecutor(max_workers=workers or default_workers())
    try:
        futures = [pool.submit(one, src, dst) for src, dst in jobs]
        for future in as_completed(futures):
            yield future.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# ----- single files (used by tests and simple callers) -------------------------------------

def run_convert(src: str | os.PathLike, options: ConvertOptions,
                out_dir: str | os.PathLike | None = None, overwrite: bool = False,
                reserved: Collection[Path] = ()) -> Path:
    dst = output_path(src, FORMATS[options.format].ext, out_dir, overwrite, reserved)
    return convert_image(src, dst, options)


def run_icon(src: str | os.PathLike, options: IconOptions,
             out_dir: str | os.PathLike | None = None, overwrite: bool = False,
             reserved: Collection[Path] = ()) -> Path:
    ext, suffix = icon_target(options)
    return make_icon(src, output_path(src, ext, out_dir, overwrite, reserved, suffix), options)
