import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from iconpress import cli


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    Image.new("RGB", (800, 400), "red").save(folder / "a.png")
    Image.new("RGBA", (300, 600), (0, 0, 255, 128)).save(folder / "b.png")
    (folder / "notes.txt").write_text("not an image")
    return folder


def test_convert_folder(photos, tmp_path, capsys):
    out = tmp_path / "out"
    code = cli.main(["convert", str(photos), "--to", "webp", "--longest", "200", "-o", str(out),
                     "--name", "pic_{n:02}"])
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["pic_01.webp", "pic_02.webp"]
    with Image.open(out / "pic_01.webp") as first, Image.open(out / "pic_02.webp") as second:
        assert first.size == (200, 100) and second.size == (100, 200)
    assert "2 of 2 done" in capsys.readouterr().out


def test_convert_options(photos, tmp_path):
    out = tmp_path / "out"
    assert cli.main(["convert", str(photos / "b.png"), "--to", "jpg", "--rotate", "90",
                     "--background", "#00ff00", "--max-kb", "20", "--watermark-text", "hi",
                     "-o", str(out)]) == 0
    with Image.open(out / "b.jpg") as jpg:
        assert jpg.size == (600, 300)
    assert (out / "b.jpg").stat().st_size <= 20 * 1024


@pytest.mark.parametrize("kind", ["ico", "favicon", "ios", "android"])
def test_icon_kinds(photos, tmp_path, kind):
    out = tmp_path / "icons"
    assert cli.main(["icon", str(photos / "a.png"), "--kind", kind, "--padding", "10",
                     "--background", "#ffffff", "-o", str(out)]) == 0
    assert len(list(out.iterdir())) == 1


def test_wildcards_and_errors(photos, tmp_path, capsys):
    broken = photos / "broken.png"
    broken.write_bytes(b"not really a png")
    code = cli.main(["convert", str(photos / "*.png"), "--to", "png", "-o", str(tmp_path / "o")])
    assert code == 1
    captured = capsys.readouterr()
    assert "broken.png" in captured.err and "2 of 3 done, 1 failed" in captured.out


def test_bad_template_and_no_files(photos, tmp_path, capsys):
    assert cli.main(["convert", str(photos), "--to", "png", "--name", "{oops}"]) == 2
    assert "oops" in capsys.readouterr().err
    assert cli.main(["convert", str(tmp_path / "missing.png"), "--to", "png"]) == 2


def test_python_m_iconpress_runs_the_cli(photos, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "iconpress", "convert", str(photos / "a.png"), "--to", "gif",
         "-o", str(tmp_path / "o")],
        capture_output=True, text=True, cwd=Path(__file__).parents[1], timeout=120)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "o" / "a.gif").exists()
