"""Task spp-copy: the copy sheet's Pills block, and the pills it passes on to plan_copy (offscreen)."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel

from spuza import copy as copy_mod
from spuza.catalog import Picture
from spuza.ui.copy_sheet import CopySheet
from spuza.xmp import PillTag

HERO = PillTag("Hero", "#2fb4e8")
EDIT = PillTag("Edit", "#ff8a1f")
DELIVERED = PillTag("Delivered", "#8fd14f")

from test_ui_copy_sheet import _APP  # noqa: E402,F401  (makes the QApplication before any widget)


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))


def pic(tmp_path: Path, stem: str, *, raw: bool = False, companion: bool = False) -> Picture:
    card = tmp_path / "card"
    card.mkdir(exist_ok=True)
    files = []
    primary = card / (f"{stem}.RAF" if raw else f"{stem}.JPG")
    primary.write_bytes(b"x" * 100)
    files.append(primary)
    twin = None
    if raw:
        twin = card / f"{stem}.JPG"
        twin.write_bytes(b"j" * 50)
        files.append(twin)
    comps = ()
    if companion:
        c = card / f"{stem}.xmp"
        c.write_bytes(b"<x/>")
        files.append(c)
        comps = (c,)
    return Picture(stem, primary, twin, tuple(files), "RAF+JPG" if raw else "JPG", 150, 0.0, companions=comps)


def sheet(tmp_path, pictures, **kw):
    s = CopySheet(pictures, tmp_path / "out", **kw)
    s.free_space_fn = lambda p: 10**12
    s._update_dest()
    return s


def chip_texts(s):
    return [c.text() for c in s.pill_chips]


def test_block_shows_chips_counts_and_picture_total(tmp_path):
    a, b, c = pic(tmp_path, "A"), pic(tmp_path, "B"), pic(tmp_path, "C")
    s = sheet(tmp_path, [a, b, c], pills={a.primary: (HERO, EDIT), b.primary: (HERO,)})
    assert not s.pills_block.isHidden()
    assert s.pills_intro.text().startswith("Pills: written to copies as keywords + colour label (XMP)")
    assert "Finder tags on Mac" in s.pills_intro.text()
    texts = chip_texts(s)
    assert len(texts) == 2
    assert "Hero" in texts[0] and "&times;2" in texts[0] and HERO.hex in texts[0]
    assert "Edit" in texts[1] and "&times;1" in texts[1] and EDIT.hex in texts[1]
    assert s.pills_count_label.text() == "2 pictures carry pills"
    assert s.pill_counts() == [(HERO, 2), (EDIT, 1)]


def test_one_picture_wording(tmp_path):
    a = pic(tmp_path, "A")
    s = sheet(tmp_path, [a], pills={a.primary: (HERO,)})
    assert s.pills_count_label.text() == "1 picture carries pills"


def test_block_hidden_with_no_pills(tmp_path):
    a = pic(tmp_path, "A")
    for kw in ({}, {"pills": {}}, {"pills": {a.primary: ()}}, {"pills": {Path("elsewhere.JPG"): (HERO,)}}, {"pills": "junk"},
               {"pills": {a.primary: ("not a pill",)}}):
        s = sheet(tmp_path, [a], **kw)
        assert s.pills_block.isHidden(), kw
        assert s._pills_map() == {}


def test_block_hidden_when_the_write_is_off(tmp_path):
    a = pic(tmp_path, "A")
    s = sheet(tmp_path, [a], pills={a.primary: (HERO,)}, write_pills=False)
    assert s.pills_block.isHidden()
    assert s._pills_map() == {}


def test_a_pill_on_an_unmarked_picture_is_not_counted(tmp_path):
    a, b = pic(tmp_path, "A"), pic(tmp_path, "B")
    s = sheet(tmp_path, [a], pills={a.primary: (HERO,), b.primary: (EDIT, DELIVERED)})
    assert s.pill_counts() == [(HERO, 1)]


def test_sheet_passes_pills_to_plan_copy_on_picture_files_only(tmp_path, monkeypatch):
    r = pic(tmp_path, "X1", raw=True, companion=True)
    j = pic(tmp_path, "J")
    seen = {}
    real = copy_mod.plan_copy

    def spy(*args, **kw):
        seen["args"], seen["kw"] = args, kw
        return real(*args, **kw)

    monkeypatch.setattr(copy_mod, "plan_copy", spy)
    s = sheet(tmp_path, [r, j], pills={r.primary: (HERO, EDIT), j.primary: (HERO,)})
    passed = seen["kw"].get("pills") if "pills" in seen["kw"] else seen["args"][-1]
    assert passed == {
        r.primary: (HERO, EDIT),
        r.files[1]: (HERO, EDIT),  # the JPEG twin
        j.primary: (HERO,),
    }
    assert r.files[2] not in passed  # the companion never gets pills
    assert all(it.pills == () for it in s.plan.items if it.src == r.files[2])
    assert [it.pills for it in s.plan.items if it.src == r.primary] == [(HERO, EDIT)]


def test_sheet_copy_writes_pills_end_to_end(tmp_path):
    from PIL import Image

    from spuza import xmp
    from test_ui_copy_sheet import wait_for

    card = tmp_path / "card"
    card.mkdir()
    jp = card / "A.JPG"
    Image.new("RGB", (16, 16), (200, 10, 10)).save(jp, "JPEG")
    p = Picture("A", jp, None, (jp,), "JPG", jp.stat().st_size, 0.0)
    s = sheet(tmp_path, [p], pills={jp: (HERO,)}, organise=False)
    s.start_copy()
    assert wait_for(lambda: s.result is not None)
    assert not s.result.failed and s.result.pilled == [jp]
    assert xmp.parse_pills(xmp.read_embedded(tmp_path / "out" / "A.JPG")) == [HERO]
    s.wait_idle()


@pytest.mark.parametrize("dark", [True, False])
def test_block_uses_the_theme_tokens_in_both_modes(tmp_path, monkeypatch, dark):
    from spuza.ui import theme

    monkeypatch.setattr(theme, "_current", theme.DARK if dark else theme.LIGHT)
    a = pic(tmp_path, "A")
    s = sheet(tmp_path, [a], pills={a.primary: (HERO,)})
    cur = theme.DARK if dark else theme.LIGHT
    assert cur.border in s.pills_block.styleSheet()
    assert cur.text in s.pill_chips[0].styleSheet() and HERO.hex in s.pill_chips[0].styleSheet()
    assert cur.text_dim in s.pills_count_label.styleSheet()
    assert isinstance(s.pill_chips[0], QLabel)
