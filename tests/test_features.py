"""Tests for resizing, watermarks, metadata, naming, SVG/HEIC input and the icon kinds."""

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from iconpress import core

needs_svg = pytest.mark.skipif(not core.svg_supported(), reason="PySide6 QtSvg not installed")

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20" viewBox="0 0 40 20">
<rect width="20" height="20" fill="#ff0000"/><rect x="20" width="20" height="20" fill="#0000ff"/>
</svg>"""


def save(img, path, **params):
    img.save(path, **params)
    return Path(path)


def opened(path):
    with Image.open(path) as im:
        im.load()
        return im.copy()


# ----- resizing and turning -----

@pytest.mark.parametrize(("mode", "size", "enlarge", "expected"), [
    ("none", (0, 0), False, (400, 200)),
    ("longest", (100, 0), False, (100, 50)),
    ("longest", (800, 0), False, (400, 200)),
    ("longest", (800, 0), True, (800, 400)),
    ("percent", (50, 0), False, (200, 100)),
    ("percent", (150, 0), False, (600, 300)),
    ("width", (80, 0), False, (80, 40)),
    ("height", (0, 50), False, (100, 50)),
    ("box", (100, 100), False, (100, 50)),
    ("box", (300, 30), False, (60, 30)),
])
def test_target_size(mode, size, enlarge, expected):
    options = core.ConvertOptions(resize=mode, size=size, enlarge=enlarge)
    assert core.target_size(400, 200, options) == expected


def test_rotate_and_flip(tmp_path):
    img = Image.new("RGB", (40, 20), "white")
    img.putpixel((0, 0), (255, 0, 0))  # red pixel in the top-left corner
    src = save(img, tmp_path / "a.png")
    out = opened(core.run_convert(src, core.ConvertOptions(rotate=90)))
    assert out.size == (20, 40) and out.getpixel((19, 0)) == (255, 0, 0)
    out = opened(core.run_convert(src, core.ConvertOptions(flip_h=True)))
    assert out.getpixel((39, 0)) == (255, 0, 0)
    out = opened(core.run_convert(src, core.ConvertOptions(flip_v=True)))
    assert out.getpixel((0, 19)) == (255, 0, 0)


def test_rotate_then_resize_by_width(tmp_path):
    src = save(Image.new("RGB", (400, 200)), tmp_path / "a.png")
    out = opened(core.run_convert(src, core.ConvertOptions(rotate=270, resize="width",
                                                           size=(100, 0))))
    assert out.size == (100, 200)


# ----- background, target size, metadata -----

def test_jpeg_background_colour(tmp_path):
    src = save(Image.new("RGBA", (10, 10), (0, 0, 0, 0)), tmp_path / "clear.png")
    out = opened(core.run_convert(src, core.ConvertOptions(format="JPEG", background="#00ff00")))
    r, g, b = out.getpixel((5, 5))
    assert g > 240 and r < 20 and b < 20


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_target_file_size(tmp_path, fmt):
    src = save(Image.effect_noise((400, 400), 90).convert("RGB"), tmp_path / "noise.png")
    big = core.run_convert(src, core.ConvertOptions(format=fmt, quality=95))
    limited = core.run_convert(src, core.ConvertOptions(format=fmt, quality=95, target_kb=40))
    assert limited.stat().st_size <= 40 * 1024 < big.stat().st_size


def test_target_size_keeps_dimensions_when_quality_is_enough(tmp_path):
    gradient = Image.linear_gradient("L").resize((400, 400)).convert("RGB")
    src = save(gradient, tmp_path / "smooth.png")
    out = core.run_convert(src, core.ConvertOptions(format="JPEG", quality=95, target_kb=8))
    assert out.stat().st_size <= 8 * 1024
    assert opened(out).size == (400, 400)


def test_target_size_ignored_for_lossless(tmp_path):
    src = save(Image.new("RGB", (50, 50)), tmp_path / "a.png")
    assert core.run_convert(src, core.ConvertOptions(format="PNG", target_kb=1)).exists()


def _photo_with_gps(path):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x0112] = 6  # rotated
    gps = exif.get_ifd(0x8825)
    gps[2] = (55.0, 45.0, 0.0)
    return save(Image.new("RGB", (40, 20), "red"), path, exif=exif)


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP", "PNG"])
def test_metadata_removed_by_default(tmp_path, fmt):
    src = _photo_with_gps(tmp_path / "phone.jpg")
    out = opened(core.run_convert(src, core.ConvertOptions(format=fmt)))
    assert dict(out.getexif()) == {}


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP", "PNG"])
def test_metadata_kept_on_request(tmp_path, fmt):
    src = _photo_with_gps(tmp_path / "phone.jpg")
    out = opened(core.run_convert(src, core.ConvertOptions(format=fmt, keep_metadata=True)))
    exif = out.getexif()
    assert exif.get(0x010F) == "PhoneMaker"
    assert exif.get(0x0112, 1) == 1  # already turned upright, so no double rotation
    assert out.size == (20, 40)


def test_keep_dates(tmp_path):
    src = save(Image.new("RGB", (8, 8)), tmp_path / "old.png")
    os.utime(src, (1_000_000_000, 1_000_000_000))
    dst = core.run_convert(src, core.ConvertOptions(format="JPEG"))
    core.copy_dates(src, dst)
    assert int(dst.stat().st_mtime) == 1_000_000_000


# ----- watermarks -----

def test_text_watermark_bottom_right(tmp_path):
    src = save(Image.new("RGB", (400, 200), "black"), tmp_path / "a.png")
    mark = core.Watermark(text="IconPress", position="bottom-right", opacity=1.0)
    out = opened(core.run_convert(src, core.ConvertOptions(watermark=mark))).convert("L")
    assert out.crop((200, 100, 400, 200)).getextrema()[1] > 200  # white text appears there
    assert out.crop((0, 0, 200, 100)).getextrema()[1] == 0  # and nowhere else


def test_image_watermark_with_opacity(tmp_path):
    src = save(Image.new("RGB", (200, 200), "black"), tmp_path / "a.png")
    logo = save(Image.new("RGBA", (50, 50), (255, 255, 255, 255)), tmp_path / "logo.png")
    mark = core.Watermark(image=str(logo), position="center", size=0.5, opacity=0.5)
    out = opened(core.run_convert(src, core.ConvertOptions(watermark=mark))).convert("L")
    assert 110 < out.getpixel((100, 100)) < 145  # half-transparent white on black
    assert out.getpixel((10, 10)) == 0


@pytest.mark.parametrize("position", core.POSITIONS)
def test_watermark_positions_stay_inside(position):
    mark = core.Watermark(position=position, margin=0.05)
    x, y = core._place((200, 100), (40, 20), mark)
    assert 0 <= x <= 160 and 0 <= y <= 80


def test_watermark_keeps_transparency(tmp_path):
    src = save(Image.new("RGBA", (100, 100), (0, 0, 0, 0)), tmp_path / "a.png")
    mark = core.Watermark(text="x", position="top-left")
    out = opened(core.run_convert(src, core.ConvertOptions(watermark=mark)))
    assert out.mode == "RGBA" and out.getpixel((90, 90))[3] == 0


# ----- names -----

def test_name_templates(tmp_path):
    src = save(Image.new("RGB", (30, 20)), tmp_path / "IMG_0001.jpg")
    output = core.OutputOptions(template="{name}-{width}x{height}")
    assert core.render_name(src, 1, output) == "IMG_0001-30x20"
    output = core.OutputOptions(template="holiday_{n:03}", find="IMG_", replace="")
    assert core.render_name(src, 7, output) == "holiday_007"
    output = core.OutputOptions(template="{name}", find="IMG_", replace="photo-")
    assert core.render_name(src, 1, output) == "photo-0001"
    assert core.render_name(src, 1, core.OutputOptions(template="a/b:c")) == "a_b_c"


@pytest.mark.parametrize("template", ["{nope}", "{}", "{0}"])
def test_bad_templates_raise(tmp_path, template):
    with pytest.raises(ValueError):
        core.render_name(tmp_path / "a.png", 1, core.OutputOptions(template=template))


def test_plan_outputs_numbers_and_avoids_clashes(tmp_path):
    files = [save(Image.new("RGB", (8, 8)), tmp_path / f"{n}.png") for n in ("a", "b", "c")]
    out = tmp_path / "out"
    planned = core.plan_outputs(files, "jpg", core.OutputOptions(folder=out, template="pic"))
    assert [p.name for p in planned] == ["pic.jpg", "pic (1).jpg", "pic (2).jpg"]
    planned = core.plan_outputs(files, "jpg", core.OutputOptions(folder=out, template="{n}"))
    assert [p.name for p in planned] == ["1.jpg", "2.jpg", "3.jpg"]


# ----- SVG and HEIC -----

@needs_svg
def test_svg_describe_and_convert(tmp_path):
    src = tmp_path / "logo.svg"
    src.write_text(SVG)
    info = core.describe(src)
    assert (info.width, info.height, info.format) == (40, 20, "SVG")
    out = opened(core.run_convert(src, core.ConvertOptions(format="PNG")))
    assert out.size == (40, 20)
    assert out.getpixel((5, 10))[:3] == (255, 0, 0) and out.getpixel((35, 10))[:3] == (0, 0, 255)


@needs_svg
def test_svg_is_drawn_at_the_output_size(tmp_path):
    src = tmp_path / "logo.svg"
    src.write_text(SVG)
    out = opened(core.run_convert(src, core.ConvertOptions(resize="width", size=(400, 0),
                                                           enlarge=True)))
    assert out.size == (400, 200)
    # Sharp edge where the colours meet: drawn big, not stretched from 40 px
    assert out.getpixel((198, 100))[:3] == (255, 0, 0)
    assert out.getpixel((202, 100))[:3] == (0, 0, 255)
    out = opened(core.run_convert(src, core.ConvertOptions(resize="percent", size=(50, 0))))
    assert out.size == (20, 10)


@needs_svg
def test_svg_icon_and_favicon_copy(tmp_path):
    src = tmp_path / "logo.svg"
    src.write_text(SVG)
    folder = core.run_icon(src, core.IconOptions(kind="favicon"))
    assert (folder / "favicon.svg").read_text() == SVG
    assert 'type="image/svg+xml"' in (folder / "favicon.html").read_text()


@pytest.mark.skipif(not core.HEIF, reason="pillow-heif not installed")
def test_heic_input(tmp_path):
    src = save(Image.new("RGB", (64, 32), "red"), tmp_path / "phone.heic")
    assert ".heic" in core.readable_extensions()
    assert core.describe(src).width == 64
    out = opened(core.run_convert(src, core.ConvertOptions(format="JPEG")))
    assert out.size == (64, 32)


def test_non_svg_rejected(tmp_path):
    bad = tmp_path / "broken.svg"
    bad.write_text("not svg at all")
    if core.svg_supported():
        with pytest.raises(ValueError):
            core.describe(bad)


# ----- icon looks and kinds -----

def test_icon_padding_and_background(tmp_path):
    img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    options = core.IconOptions(padding=0.25, background="#0000ff")
    art = core.render_icon(img, options, 100)
    assert art.getpixel((5, 5)) == (0, 0, 255, 255)  # background in the margin
    assert art.getpixel((50, 50)) == (255, 0, 0, 255)  # picture in the middle
    clear = core.render_icon(img, core.IconOptions(padding=0.25), 100)
    assert clear.getpixel((5, 5))[3] == 0


def test_icon_gradient(tmp_path):
    options = core.IconOptions(padding=0.45, background="#000000", background2="#ffffff")
    art = core.render_icon(Image.new("RGBA", (10, 10)), options, 100)
    assert art.getpixel((50, 1))[0] < 20 and art.getpixel((50, 98))[0] > 235


def test_favicon_package(tmp_path):
    src = save(Image.new("RGBA", (300, 200), (255, 0, 0, 128)), tmp_path / "site.png")
    folder = core.run_icon(src, core.IconOptions(kind="favicon"))
    assert folder.name == "site-favicon"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["apple-touch-icon.png", "favicon-96x96.png", "favicon.html", "favicon.ico",
                     "site.webmanifest", "web-app-manifest-192x192.png",
                     "web-app-manifest-512x512.png"]
    with Image.open(folder / "favicon.ico") as ico:
        assert sorted(ico.info["sizes"]) == [(16, 16), (32, 32), (48, 48)]
    touch = opened(folder / "apple-touch-icon.png").convert("RGBA")
    assert touch.size == (180, 180) and touch.getextrema()[3][0] == 255  # no transparency
    manifest = json.loads((folder / "site.webmanifest").read_text())
    assert [i["sizes"] for i in manifest["icons"]] == ["192x192", "512x512"]
    assert "favicon.svg" not in (folder / "favicon.html").read_text()


def test_ios_app_icon_set(tmp_path):
    src = save(Image.new("RGBA", (300, 300), (0, 128, 0, 0)), tmp_path / "app.png")
    folder = core.run_icon(src, core.IconOptions(kind="ios", corner_radius=0.3))
    iconset = folder / "AppIcon.appiconset"
    contents = json.loads((iconset / "Contents.json").read_text())
    assert len(contents["images"]) == 18
    for entry in contents["images"]:
        image = opened(iconset / entry["filename"])
        points = float(entry["size"].split("x")[0])
        assert image.size == (round(points * int(entry["scale"][0])),) * 2
        assert image.mode == "RGB"  # App Store icons must not be transparent
    marketing = opened(iconset / "Icon-App-1024x1024@1x.png")
    assert marketing.getpixel((0, 0)) == (255, 255, 255)  # square corners, filled


def test_android_icons(tmp_path):
    src = save(Image.new("RGBA", (300, 300), (0, 0, 255, 255)), tmp_path / "app.png")
    folder = core.run_icon(src, core.IconOptions(kind="android", background="#123456"))
    res = folder / "res"
    for density, size in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144,
                          "xxxhdpi": 192}.items():
        assert opened(res / f"mipmap-{density}" / "ic_launcher.png").size == (size, size)
        round_icon = opened(res / f"mipmap-{density}" / "ic_launcher_round.png")
        assert round_icon.getpixel((0, 0))[3] == 0  # circle
        foreground = opened(res / f"mipmap-{density}" / "ic_launcher_foreground.png")
        assert foreground.size == (round(108 * size / 48),) * 2
        assert foreground.getpixel((2, 2))[3] == 0  # picture kept inside the safe zone
    assert "#123456" in (res / "values" / "ic_launcher_background.xml").read_text()
    assert "adaptive-icon" in (res / "mipmap-anydpi-v26" / "ic_launcher.xml").read_text()
    assert opened(folder / "playstore-icon.png").size == (512, 512)


def test_encode_returns_preview_and_bytes(tmp_path):
    src = save(Image.new("RGB", (100, 50), "red"), tmp_path / "a.png")
    before, data = core.encode(src, core.ConvertOptions(format="JPEG", quality=50))
    assert before.size == (100, 50)
    assert data[:2] == b"\xff\xd8"  # a JPEG file
