"""Preview formats: .hif, the cheap raws, the CR2 SOF check, MPO drafting, PNG decoded once.

Synthetic cases need no download. Per-extension cases read real public samples (raw.pixls.us, CC0) from the
directory in SPUZA_SAMPLES (any file named *.<ext>) and skip when it is unset or lacks the file.
"""

import io
import os
import struct
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngImageFile

from spuza import preview


def _jpeg(w: int, h: int, color=(200, 30, 30)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def _ifd(entries, next_off, base):
    """Little-endian IFD at file offset base; entries are (tag, type, count, value)."""
    out = struct.pack("<H", len(entries))
    for tag, typ, cnt, val in entries:
        out += struct.pack("<HHII", tag, typ, cnt, val)
    return out + struct.pack("<I", next_off)


def _cr2_like(path: Path, big: bytes, thumb: bytes, lossless: bytes):
    """TIFF laid out like a Canon CR2: IFD0 = full JPEG strip (no NewSubfileType), IFD1 = small thumbnail
    (tags 513/514), IFD3 = wider lossless-JPEG (SOF3) raw strip."""
    n_ifd = 2 + 5 * 12 + 4  # entries used below
    i0, i1, i3 = 8, 8 + n_ifd, 8 + 2 * n_ifd
    data0 = i3 + n_ifd
    off_big, off_thumb, off_ll = data0, data0 + len(big), data0 + len(big) + len(thumb)
    ifd0 = _ifd([(256, 3, 1, 400), (257, 3, 1, 300), (259, 3, 1, 6), (273, 4, 1, off_big),
                 (279, 4, 1, len(big))], i1, i0)
    ifd1 = _ifd([(256, 3, 1, 160), (257, 3, 1, 120), (259, 3, 1, 6), (513, 4, 1, off_thumb),
                 (514, 4, 1, len(thumb))], i3, i1)
    ifd3 = _ifd([(256, 3, 1, 9999), (257, 3, 1, 9999), (259, 3, 1, 6), (273, 4, 1, off_ll),
                 (279, 4, 1, len(lossless))], 0, i3)
    path.write_bytes(b"II*\x00" + struct.pack("<I", i0) + ifd0 + ifd1 + ifd3 + big + thumb + lossless)


def _lossless(data: bytes) -> bytes:
    assert b"\xff\xc0" in data
    return data.replace(b"\xff\xc0", b"\xff\xc3", 1)  # SOF0 -> SOF3, the lossless frame type


def test_cr2_picks_full_jpeg_not_ifd1_thumbnail(tmp_path):
    big, thumb = _jpeg(400, 300), _jpeg(160, 120)
    p = tmp_path / "x.CR2"
    _cr2_like(p, big, thumb, _lossless(_jpeg(900, 700)))
    im, info = preview.load(p, max_side=None)
    assert info.source == "dng-embedded"
    assert (info.width, info.height) == (400, 300)
    assert im.size == (400, 300)


def test_sof_check_is_cr2_only(tmp_path):
    """Other TIFF raws keep the NewSubfileType rule: the SOF path must not widen them."""
    p = tmp_path / "x.cr2"
    _cr2_like(p, _jpeg(400, 300), _jpeg(160, 120), _lossless(_jpeg(900, 700)))
    with open(p, "rb") as f:
        assert preview._tiff_slice(f, p.stat().st_size)[0] == _jpeg(160, 120)
    with open(p, "rb") as f:
        assert preview._tiff_slice(f, p.stat().st_size, sof_check=True)[0] == _jpeg(400, 300)


@pytest.mark.parametrize("junk", [b"", b"\xff\xd8", b"\xff\xd8\xff\xe0\x00\x02" * 400, b"\xff\xd8" + b"\xff" * 600,
                                  b"\xff\xd8\xff\xe0\xff\xff"])
def test_is_dct_jpeg_rejects_malformed(junk):
    f = io.BytesIO(junk)
    assert preview._is_dct_jpeg(f, 0, len(junk), len(junk)) is False


@pytest.mark.parametrize("ext", [".cr2", ".arw", ".nef", ".nrw", ".pef", ".3fr", ".orf", ".rw2", ".cr3", ".hif"])
def test_new_extensions_registered(ext):
    assert ext in preview.IMAGE_EXTS | preview.RAW_EXTS
    assert (ext in preview.HEIF_EXTS) == (ext == ".hif")


def test_hif_decodes_through_heif_path(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    p = tmp_path / "x.hif"
    Image.new("RGB", (64, 48), (10, 120, 200)).save(p, format="HEIF")
    im, info = preview.load(p)
    assert info.source == "hif" and im.size == (64, 48)
    q = tmp_path / "y.heic"
    Image.new("RGB", (64, 48), (10, 120, 200)).save(q, format="HEIF")
    assert preview.load(q)[1].source == "heif"


def test_mpo_is_drafted(tmp_path):
    p = tmp_path / "x.jpg"
    a = Image.new("RGB", (2000, 1500), (90, 90, 90))
    a.save(p, "MPO", save_all=True, append_images=[Image.new("RGB", (2000, 1500), (80, 80, 80))])
    with Image.open(p) as probe:
        assert probe.format == "MPO"
    im, info = preview.load(p, max_side=500)
    assert (info.width, info.height) == (2000, 1500)  # native size reported
    assert max(im.size) < 2000  # but the decode was scaled by draft()


def test_png_decoded_once_and_exif_kept(tmp_path, monkeypatch):
    p = tmp_path / "x.png"
    ex = Image.Exif()
    ex[0x0110] = "TestCam"
    Image.new("RGB", (80, 60), (1, 2, 3)).save(p, exif=ex)
    q = tmp_path / "plain.png"
    Image.new("RGB", (80, 60), (1, 2, 3)).save(q)
    calls = []
    real = PngImageFile.load_prepare
    monkeypatch.setattr(PngImageFile, "load_prepare", lambda self: (calls.append(1), real(self))[1])
    for f, model in ((p, "TestCam"), (q, None)):
        calls.clear()
        _im, info = preview.load(f)
        assert len(calls) == 1, f
        assert (info.exif.model if info.exif else None) == model


# ---- real samples ------------------------------------------------------------------------------

# ext -> (expected source, min long side of the preview); from raws.md (sizes) and p_raw.json.
SAMPLES = {
    ".arw": ("dng-embedded", 3504), ".nef": ("dng-embedded", 8256), ".nrw": ("dng-embedded", 4000),
    ".pef": ("dng-embedded", 6192), ".3fr": ("dng-embedded", 4136), ".cr2": ("dng-embedded", 6720),
    ".orf": ("rawpy-thumb", 3200), ".rw2": ("rawpy-thumb", 1920), ".cr3": ("rawpy-thumb", 5472),
}


def _sample(ext: str) -> Path:
    d = os.environ.get("SPUZA_SAMPLES")
    if not d or not Path(d).is_dir():
        pytest.skip("SPUZA_SAMPLES not set")
    hits = [f for f in Path(d).iterdir() if f.suffix.lower() == ext]
    if not hits:
        pytest.skip(f"no {ext} sample in SPUZA_SAMPLES")
    return hits[0]


@pytest.mark.parametrize("ext", sorted(SAMPLES))
def test_real_sample_preview(ext):
    p = _sample(ext)
    im, info = preview.load(p)
    src, wide = SAMPLES[ext]
    print(f"SAMPLE {ext} {p.stat().st_size} B -> {info.source} {info.width}x{info.height} "
          f"{info.nbytes} B preview {info.ms:.1f} ms")
    assert info.source == src
    assert max(info.width, info.height) >= wide
    assert im.mode == "RGB"
