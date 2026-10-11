from pathlib import Path

import pytest
from PIL import Image, features

from iconpress import core


def save(img: Image.Image, path: Path, **params) -> Path:
    img.save(path, **params)
    return path


@pytest.fixture
def rgba_png(tmp_path):
    """100x50: left half opaque red, right half transparent with hidden red colour."""
    img = Image.new("RGBA", (100, 50), (255, 0, 0, 0))
    img.paste((255, 0, 0, 255), (0, 0, 50, 50))
    return save(img, tmp_path / "rgba.png")


@pytest.fixture
def animated_gif(tmp_path):
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "green", "blue")]
    return save(frames[0], tmp_path / "anim.gif", save_all=True, append_images=frames[1:],
                duration=[100, 200, 300], loop=0)


@pytest.mark.parametrize(
    ("name", "img"),
    [
        ("palette.gif", Image.new("P", (16, 16))),
        ("gray16.png", Image.new("I;16", (16, 16), 40000)),
        ("cmyk.jpg", Image.new("CMYK", (16, 16), (0, 255, 255, 0))),
        ("la.png", Image.new("LA", (16, 16), (128, 100))),
        ("mono.png", Image.new("1", (16, 16), 1)),
    ],
)
@pytest.mark.parametrize("fmt", [f.name for f in core.available_formats()])
def test_every_mode_converts_to_every_format(tmp_path, name, img, fmt):
    src = save(img, tmp_path / name)
    dst = core.run_convert(src, core.ConvertOptions(format=fmt))
    with Image.open(dst) as out:
        assert out.format == fmt
        assert out.size == (16, 16)


def test_jpeg_fills_transparency_with_white(rgba_png):
    dst = core.run_convert(rgba_png, core.ConvertOptions(format="JPEG"))
    with Image.open(dst) as out:
        assert out.mode == "RGB"
        r, g, b = out.getpixel((80, 25))
        assert min(r, g, b) > 245  # was red before: the hidden colour leaked through


def test_png_keeps_transparency(rgba_png):
    dst = core.run_convert(rgba_png, core.ConvertOptions(format="WEBP" if features.check("webp")
                                                          else "PNG"))
    with Image.open(dst) as out:
        assert out.convert("RGBA").getpixel((80, 25))[3] == 0


def test_cmyk_converts_to_rgb_colours(tmp_path):
    src = save(Image.new("CMYK", (8, 8), (0, 255, 255, 0)), tmp_path / "cmyk.jpg")
    dst = core.run_convert(src, core.ConvertOptions(format="PNG"))
    with Image.open(dst) as out:
        r, g, b = out.convert("RGB").getpixel((4, 4))
        assert r > 200 and g < 60 and b < 60


def test_16bit_gray_is_scaled_not_clipped(tmp_path):
    src = save(Image.new("I;16", (8, 8), 32768), tmp_path / "gray16.png")
    dst = core.run_convert(src, core.ConvertOptions(format="JPEG"))
    with Image.open(dst) as out:
        assert 120 < out.getpixel((4, 4)) < 136


@pytest.mark.parametrize("fmt", ["GIF", "PNG", "WEBP"])
def test_animation_is_preserved(animated_gif, fmt):
    if not features.check("webp") and fmt == "WEBP":
        pytest.skip("Pillow built without WebP")
    dst = core.run_convert(animated_gif, core.ConvertOptions(format=fmt))
    with Image.open(dst) as out:
        assert out.n_frames == 3
        out.seek(2)
        assert out.convert("RGB").getpixel((16, 16))[2] > 200


def test_animation_to_still_format_uses_first_frame(animated_gif):
    dst = core.run_convert(animated_gif, core.ConvertOptions(format="JPEG"))
    with Image.open(dst) as out:
        r, g, b = out.getpixel((16, 16))
        assert r > 200 and g < 60


def test_exif_orientation_is_applied(tmp_path):
    img = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° clockwise to display
    src = save(img, tmp_path / "phone.jpg", exif=exif)
    assert core.describe(src).width == 20
    dst = core.run_convert(src, core.ConvertOptions(format="PNG"))
    with Image.open(dst) as out:
        assert out.size == (20, 40)


def test_max_side_downscales_and_keeps_aspect(tmp_path):
    src = save(Image.new("RGB", (400, 200)), tmp_path / "big.png")
    dst = core.run_convert(src, core.ConvertOptions(format="PNG", max_side=100))
    with Image.open(dst) as out:
        assert out.size == (100, 50)


def test_max_side_never_upscales(tmp_path):
    src = save(Image.new("RGB", (40, 20)), tmp_path / "small.png")
    dst = core.run_convert(src, core.ConvertOptions(format="PNG", max_side=100))
    with Image.open(dst) as out:
        assert out.size == (40, 20)


def test_quality_changes_jpeg_size(tmp_path):
    noise = Image.effect_noise((200, 200), 80).convert("RGB")
    src = save(noise, tmp_path / "noise.png")
    low = core.run_convert(src, core.ConvertOptions(format="JPEG", quality=20))
    high = core.run_convert(src, core.ConvertOptions(format="JPEG", quality=95))
    assert low.stat().st_size < high.stat().st_size


def test_existing_files_are_not_overwritten_by_default(tmp_path):
    src = save(Image.new("RGB", (8, 8), "red"), tmp_path / "photo.png")
    first = core.run_convert(src, core.ConvertOptions(format="PNG"))
    second = core.run_convert(src, core.ConvertOptions(format="PNG"))
    assert first.name == "photo (1).png"
    assert second.name == "photo (2).png"
    with Image.open(src) as original:
        assert original.getpixel((0, 0)) == (255, 0, 0)


def test_overwrite_replaces_original_in_place(tmp_path):
    src = save(Image.new("RGBA", (64, 32), (0, 0, 255, 255)), tmp_path / "photo.png")
    dst = core.run_convert(src, core.ConvertOptions(format="PNG", max_side=16), overwrite=True)
    assert dst == src
    with Image.open(src) as out:
        assert out.size == (16, 8)


def test_output_folder_and_batch_name_clashes(tmp_path):
    out_dir = tmp_path / "out" / "nested"
    a = save(Image.new("RGB", (8, 8)), tmp_path / "pic.png")
    b = save(Image.new("RGB", (8, 8)), tmp_path / "pic.bmp")
    written = []
    for src in (a, b):
        written.append(core.run_convert(src, core.ConvertOptions(format="JPEG"), out_dir,
                                        overwrite=True, reserved=written))
    assert [p.name for p in written] == ["pic.jpg", "pic (1).jpg"]
    assert all(p.parent == out_dir for p in written)


def test_output_name_with_dots(tmp_path):
    src = tmp_path / "my.holiday.photo.png"
    assert core.output_path(src, "jpg").name == "my.holiday.photo.jpg"
    assert core.output_path(src, "", suffix="-icons").name == "my.holiday.photo-icons"


def test_icon_keeps_aspect_ratio(rgba_png):
    dst = core.run_icon(rgba_png, core.IconOptions(kind="ico"))
    with Image.open(dst) as ico:
        assert sorted(ico.info["sizes"]) == [(s, s) for s in core.ICO_SIZES]
        ico.size = (256, 256)
        img = ico.convert("RGBA")
    # 100x50 is padded top and bottom instead of being stretched
    assert img.getpixel((10, 10))[3] == 0
    assert img.getpixel((10, 128)) == (255, 0, 0, 255)
    assert img.getpixel((250, 128))[3] == 0  # the transparent half stays transparent


def test_icon_crop_fills_square(tmp_path):
    src = save(Image.new("RGB", (100, 50), "blue"), tmp_path / "wide.png")
    art = core.render_icon(Image.open(src), core.IconOptions(fit="crop"), 64)
    assert art.size == (64, 64)
    assert art.getpixel((0, 0)) == (0, 0, 255, 255)


def test_icon_rounded_corners(tmp_path):
    src = save(Image.new("RGB", (64, 64), "blue"), tmp_path / "sq.png")
    art = core.render_icon(Image.open(src), core.IconOptions(corner_radius=0.25), 128)
    assert art.getpixel((0, 0))[3] == 0
    assert art.getpixel((64, 64))[3] == 255
    assert art.getpixel((64, 0))[3] == 255


def test_icon_selected_sizes(tmp_path):
    src = save(Image.new("RGB", (300, 300), "green"), tmp_path / "app.png")
    dst = core.run_icon(src, core.IconOptions(kind="ico", sizes=(16, 32, 999)))
    with Image.open(dst) as ico:
        assert sorted(ico.info["sizes"]) == [(16, 16), (32, 32)]


def test_icns(tmp_path):
    src = save(Image.new("RGB", (300, 200), "green"), tmp_path / "app.png")
    dst = core.run_icon(src, core.IconOptions(kind="icns"))
    assert dst.suffix == ".icns"
    with Image.open(dst) as icns:
        assert icns.format == "ICNS"


def test_png_icon_set(tmp_path):
    src = save(Image.new("RGB", (300, 200), "green"), tmp_path / "app.png")
    out_dir = core.run_icon(src, core.IconOptions(kind="png", sizes=(16, 180, 512)))
    assert out_dir.name == "app-icons"
    assert sorted(p.name for p in out_dir.iterdir()) == ["icon-16.png", "icon-180.png",
                                                         "icon-512.png"]
    with Image.open(out_dir / "icon-180.png") as png:
        assert png.size == (180, 180)


def test_icon_from_cmyk(tmp_path):
    src = save(Image.new("CMYK", (40, 40)), tmp_path / "cmyk.jpg")
    assert core.run_icon(src, core.IconOptions()).exists()


def test_load_preview_and_describe(animated_gif):
    info = core.describe(animated_gif)
    assert (info.width, info.height, info.format, info.frames) == (32, 32, "GIF", 3)
    preview = core.load_preview(animated_gif, 16)
    assert preview.mode == "RGBA" and preview.size == (16, 16)


def test_describe_rejects_non_images(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(OSError):
        core.describe(bad)


def test_input_extensions():
    exts = core.input_extensions()
    assert ".png" in exts and ".jpg" in exts and ".jpeg" in exts and ".tiff" in exts
