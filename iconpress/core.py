"""Image processing for IconPress: format conversion and icon generation.

Everything here is plain Pillow, so it can be tested and reused without Qt.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageSequence, features

LANCZOS = Image.Resampling.LANCZOS
_EXIF_ORIENTATION = 0x0112


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

ICON_KINDS = ("ico", "icns", "png")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)
PNG_SIZES = (16, 32, 48, 64, 128, 180, 192, 256, 512, 1024)
ICON_SIZES = {"ico": ICO_SIZES, "icns": ICNS_SIZES, "png": PNG_SIZES}

# Common image types for the file dialog; anything else Pillow opens still works via drop.
_DIALOG_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".avif", ".gif", ".bmp", ".tif", ".tiff",
    ".ico", ".icns", ".tga", ".psd", ".ppm", ".pgm", ".pbm", ".jp2", ".heic",
)


@dataclass
class ConvertOptions:
    format: str = "PNG"
    quality: int = 90
    max_side: int | None = None  # downscale so the longest side fits; None keeps the size


@dataclass
class IconOptions:
    kind: str = "ico"
    sizes: tuple[int, ...] = ICO_SIZES  # ignored for ICNS, which has a fixed size set
    fit: str = "pad"  # "pad" keeps the whole picture, "crop" fills the square
    corner_radius: float = 0.0  # fraction of the side, 0 to 0.5


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


def available_formats() -> list[OutputFormat]:
    return [f for f in CONVERT_FORMATS if f.feature is None or features.check(f.feature)]


def readable_extensions() -> set[str]:
    """Every file extension this Pillow build can open."""
    return {ext for ext, fmt in Image.registered_extensions().items() if fmt in Image.OPEN}


def input_extensions() -> list[str]:
    """Extensions offered in the open dialog, limited to what this Pillow build can read."""
    readable = readable_extensions()
    return [ext for ext in _DIALOG_EXTENSIONS if ext in readable]


def describe(path: str | os.PathLike) -> ImageInfo:
    """Read basic facts about an image without decoding it; raises if Pillow can't open it."""
    with Image.open(path) as im:
        width, height = im.size
        if im.getexif().get(_EXIF_ORIENTATION, 1) in (5, 6, 7, 8):
            width, height = height, width
        return ImageInfo(width, height, im.format or "", getattr(im, "n_frames", 1),
                         os.path.getsize(path))


def load_preview(path: str | os.PathLike, max_side: int) -> Image.Image:
    """First frame, upright, as RGBA and no larger than ``max_side``."""
    with Image.open(path) as im:
        im.draft("RGB", (max_side, max_side))  # lets JPEG decode at a reduced scale
        img = ImageOps.exif_transpose(im).convert("RGBA")
    img.thumbnail((max_side, max_side), LANCZOS)
    return img


def _load(path: str | os.PathLike) -> _Source:
    # Everything is copied into memory and the file closed before returning, so the
    # result can be saved over the very file it came from.
    with Image.open(path) as im:
        source = _Source(frames=[], mode=im.mode, icc_profile=im.info.get("icc_profile"))
        if getattr(im, "n_frames", 1) > 1:
            source.loop = im.info.get("loop")
            for frame in ImageSequence.Iterator(im):
                source.durations.append(int(frame.info.get("duration", 100)))
                source.frames.append(frame.convert("RGBA"))
        else:
            source.frames.append(ImageOps.exif_transpose(im))
    return source


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


def flatten(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """Composite transparent pixels onto a solid background."""
    rgba = img.convert("RGBA")
    result = Image.new("RGB", rgba.size, background)
    result.paste(rgba, mask=rgba.getchannel("A"))
    return result


def prepare_for_format(img: Image.Image, fmt: str) -> Image.Image:
    """Convert ``img`` to a mode that ``fmt`` can store, keeping transparency where possible."""
    allowed = _SAVE_MODES[fmt]
    has_alpha = img.has_transparency_data
    if not FORMATS[fmt].alpha and has_alpha:
        return flatten(img)
    if img.mode in allowed:
        return img
    if _color_family(img.mode) == "gray" and not has_alpha:
        img = _to_8bit_gray(img)
        if img.mode in allowed:
            return img
    return img.convert("RGBA" if has_alpha and "RGBA" in allowed else "RGB")


def _limit_size(img: Image.Image, max_side: int | None) -> Image.Image:
    if not max_side or max(img.size) <= max_side:
        return img
    if img.mode in ("1", "P"):  # palette images would otherwise be resized with NEAREST
        img = img.convert("RGBA" if img.has_transparency_data else "RGB")
    img = img.copy()
    img.thumbnail((max_side, max_side), LANCZOS)
    return img


def convert_image(src: str | os.PathLike, dst: str | os.PathLike,
                  options: ConvertOptions) -> Path:
    spec = FORMATS[options.format]
    source = _load(src)
    frames = source.frames if spec.animation else source.frames[:1]
    frames = [prepare_for_format(_limit_size(f, options.max_side), spec.name) for f in frames]

    params: dict = {}
    if spec.lossy:
        params["quality"] = options.quality
    if spec.name == "JPEG":
        params["optimize"] = True
    if spec.name == "TIFF" and features.check("libtiff"):
        params["compression"] = "tiff_lzw"
    if (source.icc_profile and spec.name != "GIF" and spec.name != "BMP"
            and _color_family(source.mode) == _color_family(frames[0].mode)):
        params["icc_profile"] = source.icc_profile
    if len(frames) > 1:
        params.update(save_all=True, append_images=frames[1:], duration=source.durations)
        if source.loop is not None:
            params["loop"] = source.loop
        if spec.name == "GIF":
            params["disposal"] = 2

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(dst, format=spec.name, **params)
    return dst


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


def render_icon(img: Image.Image, options: IconOptions, size: int) -> Image.Image:
    """The square, rounded artwork an icon is built from, at ``size`` pixels."""
    art = square(img, options.fit).resize((size, size), LANCZOS)
    return round_corners(art, options.corner_radius)


def icon_sizes(options: IconOptions) -> tuple[int, ...]:
    if options.kind == "icns":
        return ICNS_SIZES
    allowed = ICON_SIZES[options.kind]
    return tuple(sorted(s for s in set(options.sizes) if s in allowed)) or allowed


def make_icon(src: str | os.PathLike, dst: str | os.PathLike, options: IconOptions) -> Path:
    """Write an .ico, an .icns, or a folder of PNGs (for ``kind == "png"``) to ``dst``."""
    img = _load(src).frames[0]
    sizes = icon_sizes(options)
    master = render_icon(img, options, max(sizes))
    dst = Path(dst)
    if options.kind == "png":
        dst.mkdir(parents=True, exist_ok=True)
        for size in sizes:
            master.resize((size, size), LANCZOS).save(dst / f"icon-{size}.png")
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    if options.kind == "ico":
        master.save(dst, format="ICO", sizes=[(s, s) for s in sizes])
    elif options.kind == "icns":
        master.save(dst, format="ICNS")
    else:
        raise ValueError(f"Unknown icon kind: {options.kind}")
    return dst


def output_path(src: str | os.PathLike, ext: str, out_dir: str | os.PathLike | None = None,
                overwrite: bool = False, reserved: Collection[Path] = (), suffix: str = "") -> Path:
    """Where the result for ``src`` goes: next to it, or in ``out_dir``.

    Unless ``overwrite`` is set, an existing name gets a counter: ``photo (1).png``.
    Files written earlier in the same batch are passed as ``reserved``, so two inputs
    with the same name never end up in one output file, even with ``overwrite``.
    """
    src = Path(src)
    folder = Path(out_dir) if out_dir else src.parent
    stem = src.stem + suffix
    ext = f".{ext}" if ext else ""
    target = folder / f"{stem}{ext}"
    if overwrite and target not in reserved:
        return target
    for n in itertools.count():
        candidate = folder / (f"{stem} ({n}){ext}" if n else f"{stem}{ext}")
        if not candidate.exists() and candidate not in reserved:
            return candidate
    raise AssertionError("unreachable")


def run_convert(src: str | os.PathLike, options: ConvertOptions,
                out_dir: str | os.PathLike | None = None, overwrite: bool = False,
                reserved: Collection[Path] = ()) -> Path:
    dst = output_path(src, FORMATS[options.format].ext, out_dir, overwrite, reserved)
    return convert_image(src, dst, options)


def run_icon(src: str | os.PathLike, options: IconOptions,
             out_dir: str | os.PathLike | None = None, overwrite: bool = False,
             reserved: Collection[Path] = ()) -> Path:
    if options.kind == "png":
        dst = output_path(src, "", out_dir, overwrite, reserved, suffix="-icons")
    else:
        dst = output_path(src, options.kind, out_dir, overwrite, reserved)
    return make_icon(src, dst, options)
