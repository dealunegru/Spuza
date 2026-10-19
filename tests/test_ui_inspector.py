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


def test_edit_rows_are_listed_crop_first_before_marked():
    w = Inspector()
    w.set_picture(_pic(), _info(None), edit=["Crop: 4:5, 3200 x 4000 px", "Story fit: 9:16, zoom 1.4x"])
    t = w.plain().splitlines()
    assert t.index("Crop: 4:5, 3200 x 4000 px") + 1 == t.index("Story fit: 9:16, zoom 1.4x") < t.index("Not marked")
    w.set_picture(_pic(), _info(None), edit="Crop: free, 1 x 1 px")  # a bare string is still one row
    assert "Crop: free, 1 x 1 px" in w.plain()
    w.set_picture(_pic(), _info(None))
    assert not any(r.startswith(("Crop", "Story")) for r in w.plain().splitlines())


def test_derived_rows_for_a_crop_a_story_and_junk():
    from spuza import derived as dv
    from spuza import ratio
    from spuza.story import Story
    from spuza.ui.inspector import derived_lines

    crop = ratio.largest_centred(0.8, 6000, 4000)
    c = dv.Derived(1, 0, dv.CROP, crop=crop, ratio=ratio.Ratio(4))
    assert derived_lines(c, "A.JPG", (6000, 4000)) == ("Crop of A.JPG", ["4:5", "3200 x 4000 px"])
    assert derived_lines(c, "A.JPG", (6000, 4000), 1)[1] == ["5:4", "4000 x 3200 px"]  # as seen once turned
    free = dv.Derived(2, 0, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    assert derived_lines(free, "A.JPG", (6000, 4000))[1] == ["Free", "3000 x 2000 px"]
    assert derived_lines(free, "A.JPG", None)[1] == ["Free", "50% x 50% of the picture"]  # no native size known
    s = dv.Derived(3, 0, dv.STORY, story=Story(1.5, 0.0, 0.0))
    assert derived_lines(s, "A.JPG") == ("Story fit of A.JPG", ["9:16", "1080 x 1920 px"])
    for bad in (None, 5, "x", object()):  # never raises
        head, rows = derived_lines(bad, "A.JPG", "no", "x")
        assert isinstance(head, str) and isinstance(rows, list)
    assert derived_lines(free, "A.JPG", (0, "x"), None)[0] == "Crop of A.JPG"


def test_inspector_shows_a_derived_picture_in_place_of_the_files():
    w = Inspector()
    w.set_derived(_pic(), "Crop of IMG_0001.JPG", ["4:5", "3200 x 4000 px"], True)
    assert w.lines == ["IMG_0001", "Crop of IMG_0001.JPG", "4:5", "3200 x 4000 px", "Marked"]
    assert "Crop of IMG_0001.JPG" in w.label.text() and "JPG" not in w.label.text().replace("IMG_0001.JPG", "")
    w.set_picture(_pic(), _info(Exif("f/2.8", "1/250 s", "ISO 200", "18 mm", "RICOH GR III")))
    assert "Crop of" not in w.label.text()  # the next picture names its own files again
