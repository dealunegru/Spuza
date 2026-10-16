"""Inspector EXIF rows: values, missing values and the loading state."""

from pathlib import Path

from spuza.catalog import Picture
from spuza.exif import Exif
from spuza.preview import PreviewInfo
from spuza.ui.inspector import Inspector


def _pic() -> Picture:
    p = Path("IMG_0001.JPG")
    return Picture("IMG_0001", p, None, (p,), "JPG", 1000, 0.0)


def _info(exif):
    return PreviewInfo(Path("IMG_0001.JPG"), "pillow", 100, 50, 1000, 1.5, exif)


def test_rows_with_values():
    w = Inspector()
    w.set_picture(_pic(), _info(Exif("f/2.8", "1/250 s", "ISO 200", "18 mm", "RICOH GR III")))
    t = w.plain()
    for row in ("Aperture: f/2.8", "Shutter: 1/250 s", "ISO: 200", "Focal length: 18 mm",
                "Camera: RICOH GR III"):
        assert row in t
    assert "ISO: ISO" not in t  # the value already says ISO; the row must not repeat it
    assert "f/2.8" in w.label.text() and "1/250 s" in w.label.text()
    assert "Preview: pillow" in t and "Not marked" in t  # existing rows kept


def test_missing_and_loading_show_dashes():
    w = Inspector()
    for info in (_info(None), None):
        w.set_picture(_pic(), info)
        t = w.plain()
        assert "Aperture: --" in t and "Shutter: --" in t and "ISO: --" in t
        assert "Focal length" not in t and "Camera" not in t
        assert "Aperture: --" in w.label.text()
    w.set_picture(_pic(), _info(Exif(aperture="f/4")))
    assert "Aperture: f/4" in w.plain() and "Shutter: --" in w.plain()
