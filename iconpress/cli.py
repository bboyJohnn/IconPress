"""Command line: ``iconpress-cli convert …`` and ``iconpress-cli icon …``."""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from . import __version__, core

EPILOG = """examples:
  iconpress-cli convert *.png --to webp -q 80
  iconpress-cli convert photos/ --to jpg --longest 1920 --max-kb 500 -o out/
  iconpress-cli convert scan.tiff --to png --rotate 90 --name "{name}_{n:03}"
  iconpress-cli icon logo.svg --kind favicon
  iconpress-cli icon logo.png --kind ios --background "#ffffff" --padding 0.1
"""


def _colour(text: str) -> str:
    from PIL import ImageColor

    try:
        ImageColor.getrgb(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a colour: {text}") from exc
    return text


def _fraction(text: str) -> float:
    value = float(text)
    return value / 100 if value > 1 else value  # "20" and "0.2" both mean 20 %


def _box(text: str) -> tuple[int, int]:
    try:
        width, height = (int(v) for v in text.lower().split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT, for example 800x600") from exc
    return width, height


def _sizes(text: str) -> tuple[int, ...]:
    return tuple(int(v) for v in text.replace(" ", "").split(",") if v)


def _output_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("output")
    group.add_argument("-o", "--output", metavar="DIR", help="folder for the results "
                       "(default: next to each original)")
    group.add_argument("--overwrite", action="store_true",
                       help="replace existing files instead of adding a number")
    group.add_argument("--name", metavar="TEMPLATE", default="{name}",
                       help="file name template: {name}, {n}, {n:03}, {width}, {height}, {ext}")
    group.add_argument("--find", default="", help="text to replace in the original names")
    group.add_argument("--replace", default="", help="replacement for --find")
    group.add_argument("--keep-dates", action="store_true",
                       help="give results the modification date of the original")
    group.add_argument("-j", "--jobs", type=int, default=0,
                       help=f"files processed at once (default: {core.default_workers()})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iconpress-cli", description="Convert images and make icons.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"IconPress {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="convert images to another format",
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    convert.add_argument("files", nargs="+", help="images or folders")
    formats = {f.ext: f.name for f in core.available_formats()} | {
        f.name.lower(): f.name for f in core.available_formats()} | {"jpeg": "JPEG"}
    convert.add_argument("-t", "--to", required=True, choices=sorted(formats),
                         help="output format")
    convert.add_argument("-q", "--quality", type=int, default=90,
                         help="quality for JPEG, WebP and AVIF, 1-100 (default: 90)")
    convert.add_argument("--max-kb", type=int, help="keep each file under this size in KB "
                         "(JPEG, WebP, AVIF)")
    size = convert.add_argument_group("size").add_mutually_exclusive_group()
    size.add_argument("--longest", type=int, metavar="PX", help="longest side in pixels")
    size.add_argument("--percent", type=int, metavar="P", help="scale to P percent")
    size.add_argument("--width", type=int, metavar="PX", help="width in pixels")
    size.add_argument("--height", type=int, metavar="PX", help="height in pixels")
    size.add_argument("--box", type=_box, metavar="WxH", help="fit inside WIDTHxHEIGHT")
    convert.add_argument("--enlarge", action="store_true",
                         help="let --longest/--width/--height/--box make small images bigger")
    convert.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0,
                         help="turn clockwise by this many degrees")
    convert.add_argument("--flip-h", action="store_true", help="mirror left to right")
    convert.add_argument("--flip-v", action="store_true", help="mirror top to bottom")
    convert.add_argument("--background", type=_colour, default="#ffffff",
                         help="fill for transparency in JPEG (default: white)")
    convert.add_argument("--keep-metadata", action="store_true",
                         help="keep EXIF data such as camera, date and location")
    mark = convert.add_argument_group("watermark")
    mark.add_argument("--watermark-text", metavar="TEXT")
    mark.add_argument("--watermark-image", metavar="FILE")
    mark.add_argument("--watermark-position", choices=core.POSITIONS, default="bottom-right")
    mark.add_argument("--watermark-size", type=_fraction, default=0.25,
                      help="width as a share of the picture: 0.25 or 25 (default: 25%%)")
    mark.add_argument("--watermark-opacity", type=_fraction, default=0.7)
    mark.add_argument("--watermark-color", type=_colour, default="#ffffff")
    _output_arguments(convert)
    convert.set_defaults(formats=formats)

    icon = commands.add_parser("icon", help="make icons from images")
    icon.add_argument("files", nargs="+", help="images or folders")
    icon.add_argument("-k", "--kind", choices=core.ICON_KINDS, default="ico",
                      help="ico (Windows), icns (macOS), png (a set of PNGs), favicon "
                           "(website), ios (Xcode app icon set), android (launcher icons)")
    icon.add_argument("--sizes", type=_sizes, help="for ico and png, e.g. 16,32,48,256")
    icon.add_argument("--fit", choices=("pad", "crop"), default="pad",
                      help="non-square pictures: pad keeps all of it, crop fills the square")
    icon.add_argument("--radius", type=_fraction, default=0.0,
                      help="rounded corners, 0 to 0.5 (or 0 to 50)")
    icon.add_argument("--padding", type=_fraction, default=0.0,
                      help="empty margin around the picture, 0 to 0.4 (or 0 to 40)")
    icon.add_argument("--background", type=_colour, help="background colour")
    icon.add_argument("--gradient", type=_colour, metavar="COLOUR",
                      help="second colour: a top-to-bottom gradient with --background")
    _output_arguments(icon)
    return parser


def collect(paths: list[str]) -> list[Path]:
    """Files from the arguments: folders are expanded, wildcards work on Windows too."""
    readable = core.readable_extensions()
    found: list[Path] = []
    for argument in paths:
        matches = glob.glob(argument) if any(c in argument for c in "*?[") else [argument]
        for match in map(Path, matches):
            if match.is_dir():
                found += sorted(p for p in match.iterdir()
                                if p.is_file() and p.suffix.lower() in readable)
            elif match.exists():
                found.append(match)
            else:
                print(f"not found: {match}", file=sys.stderr)
    unique = list(dict.fromkeys(p.resolve() for p in found))
    return unique


def _size(path: Path) -> str:
    if path.is_dir():
        return f"{sum(1 for p in path.rglob('*') if p.is_file())} files"
    size = path.stat().st_size
    return f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # file names and arrows on any console encoding
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    sources = collect(args.files)
    if not sources:
        print("no images to process", file=sys.stderr)
        return 2

    output = core.OutputOptions(folder=args.output, overwrite=args.overwrite,
                                template=args.name, find=args.find, replace=args.replace,
                                keep_dates=args.keep_dates)
    if args.command == "convert":
        resize, size = "none", (0, 0)
        for mode in ("longest", "percent", "width"):
            if getattr(args, mode):
                resize, size = mode, (getattr(args, mode), 0)
        if args.height:
            resize, size = "height", (0, args.height)
        if args.box:
            resize, size = "box", args.box
        watermark = None
        if args.watermark_text or args.watermark_image:
            watermark = core.Watermark(
                text=args.watermark_text or "", image=args.watermark_image,
                position=args.watermark_position, size=args.watermark_size,
                opacity=args.watermark_opacity, color=args.watermark_color)
        options = core.ConvertOptions(
            format=args.formats[args.to], quality=args.quality, resize=resize, size=size,
            enlarge=args.enlarge, rotate=args.rotate, flip_h=args.flip_h, flip_v=args.flip_v,
            background=args.background, target_kb=args.max_kb,
            keep_metadata=args.keep_metadata, watermark=watermark)
        ext, suffix = core.FORMATS[options.format].ext, ""

        def work(src, dst):
            return core.convert_image(src, dst, options)
    else:
        options = core.IconOptions(
            kind=args.kind, sizes=args.sizes or core.ICON_SIZES.get(args.kind, ()),
            fit=args.fit, corner_radius=args.radius, padding=args.padding,
            background=args.background, background2=args.gradient)
        ext, suffix = core.icon_target(options)

        def work(src, dst):
            return core.make_icon(src, dst, options)

    try:
        targets = core.plan_outputs(sources, ext, output, suffix)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if any(core.is_svg(p) for p in sources):
        core.init_svg()

    failed = 0
    for src, dst, error in core.run_batch(list(zip(sources, targets, strict=True)), work,
                                          args.jobs or None, args.keep_dates):
        if error:
            failed += 1
            print(f"✗ {src.name}: {error}", file=sys.stderr)
        else:
            print(f"✓ {src.name} → {dst} ({_size(dst)})")
    done = len(sources) - failed
    print(f"{done} of {len(sources)} done" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
