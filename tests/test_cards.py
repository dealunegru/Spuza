"""Several mounted cards: listing, --card, dual-slot twin pairing, and the status-line note."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spuza import cli
from spuza.cards import CardError, catalog_note, describe, pair_twins, pick_card
from spuza.catalog import Catalog, Picture, find_cards, scan


def make_card(root: Path, files: dict[str, int]) -> Path:
    """A fake card: DCIM/100TEST/<name> with `size` bytes each."""
    d = root / "DCIM" / "100TEST"
    d.mkdir(parents=True)
    for name, size in files.items():
        (d / name).write_bytes(b"x" * size)
    return root


def pic(name: str, size: int, folder: str = "/c1") -> Picture:
    p = Path(folder, name)
    return Picture(p.stem, p, None, (p,), "JPG", size, 1.0)


# --- pair_twins ---------------------------------------------------------------------------------

def test_twin_same_name_and_size_is_paired_and_never_double_counted():
    first = [pic("A.JPG", 10), pic("B.JPG", 20)]
    second = [pic("a.jpg", 10, "/c2"), pic("B.JPG", 99, "/c2"), pic("C.JPG", 5, "/c2")]
    paired, left = pair_twins(first, second)
    assert paired[0].twins == (Path("/c2/a.jpg"),)  # case-insensitive name
    assert paired[1].twins == ()  # same name, other size: not a twin
    assert [p.primary.name for p in left] == ["B.JPG", "C.JPG"]
    assert first[0].twins == ()  # inputs are not mutated


def test_one_second_card_picture_pairs_once():
    first = [pic("A.JPG", 10, "/c1/100"), pic("A.JPG", 10, "/c1/101")]
    paired, left = pair_twins(first, [pic("A.JPG", 10, "/c2")])
    assert sum(1 for p in paired if p.twins) == 1 and left == []


def test_twin_carries_the_second_cards_files():
    p = Path("/c2/A.RAF")
    j = Path("/c2/A.JPG")
    second = Picture("A", p, j, (p, j), "RAF+JPG", 30, 1.0)
    first = Picture("A", Path("/c1/A.RAF"), Path("/c1/A.JPG"), (Path("/c1/A.RAF"), Path("/c1/A.JPG")),
                    "RAF+JPG", 30, 1.0)
    paired, _ = pair_twins([first], [second])
    assert paired[0].twins == (p, j)


def test_empty_sides():
    assert pair_twins([], [pic("A.JPG", 1)])[1][0].primary.name == "A.JPG"
    assert pair_twins([pic("A.JPG", 1)], []) == ([pic("A.JPG", 1)], [])


# --- choosing a card ----------------------------------------------------------------------------

def test_pick_card_default_number_and_path(tmp_path):
    cards = [tmp_path / "A", tmp_path / "B"]
    assert pick_card(cards, None) == 0
    assert pick_card(cards, "2") == 1
    assert pick_card(cards, str(tmp_path / "B")) == 1
    assert pick_card(cards, str(tmp_path / "B") + os.sep) == 1


@pytest.mark.parametrize("spec", ["0", "3", "-1", "", "nope", "1.5"])
def test_pick_card_unknown_is_a_clear_error(tmp_path, spec):
    with pytest.raises(CardError) as e:
        pick_card([tmp_path / "A", tmp_path / "B"], spec)
    assert "--card" in str(e.value)


def test_resolve_lists_every_card_and_opens_the_dcim(tmp_path):
    a = make_card(tmp_path / "A", {"1.JPG": 1})
    b = make_card(tmp_path / "B", {"1.JPG": 1})
    lines: list[str] = []
    run = cli.resolve_cards(find_cards([a, b]), None, say=lines.append)
    assert run.root == a / "DCIM" and run.chosen == 0
    joined = "\n".join(lines)
    assert "2 cards found" in lines[0] and str(a) in joined and str(b) in joined
    assert len(lines) == 3
    run2 = cli.resolve_cards([a, b], "2", say=lines.append)
    assert run2.root == b / "DCIM" and run2.chosen == 1


def test_resolve_single_card_is_silent_and_none_is_none(tmp_path):
    a = make_card(tmp_path / "A", {"1.JPG": 1})
    lines: list[str] = []
    assert cli.resolve_cards([a], None, say=lines.append).root == a / "DCIM"
    assert lines == []
    assert cli.resolve_cards([], None, say=lines.append) is None


def test_resolve_card_flag_with_no_cards_is_an_error():
    with pytest.raises(CardError):
        cli.resolve_cards([], "1")


def test_main_unknown_card_exits_1_with_message(tmp_path, monkeypatch, capsys):
    a = make_card(tmp_path / "A", {"1.JPG": 1})
    monkeypatch.setattr("spuza.catalog.find_cards", lambda candidates=None: [a])
    assert cli.main(["--card", "5"]) == 1
    err = capsys.readouterr().err
    assert "--card 5" in err and "1..1" in err


def test_card_flag_parses_and_refuses_a_path():
    assert cli.parse_args(["--card", "2"]).card == "2"
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--card", "2", "/some/folder"])
    assert e.value.code == 2
    assert cli.parse_args([]).card is None


# --- end to end on fake trees -------------------------------------------------------------------

def test_open_card_run_pairs_twins_and_counts_only_there(tmp_path):
    a = make_card(tmp_path / "A", {"IMG_1.JPG": 10, "IMG_2.JPG": 20, "IMG_3.JPG": 30})
    b = make_card(tmp_path / "B", {"img_1.jpg": 10, "IMG_2.JPG": 21, "IMG_9.JPG": 5})
    run = cli.resolve_cards([a, b], None, say=lambda t: None)
    cat, note = cli.open_card_run(run)
    assert isinstance(cat, Catalog) and len(cat) == 3  # the second card is never added to the catalog
    twinned = {p.primary.name: p.twins for p in cat.pictures}
    assert twinned["IMG_1.JPG"] == (b / "DCIM" / "100TEST" / "img_1.jpg",)
    assert twinned["IMG_2.JPG"] == () and twinned["IMG_3.JPG"] == ()
    assert note == f"Card 1 of 2 ({a}) · {b} also mounted: 1 twins, 2 only there — spuza --card 2"


def test_choosing_card_two_opens_it_and_pairs_against_card_one(tmp_path):
    a = make_card(tmp_path / "A", {"X.JPG": 7})
    b = make_card(tmp_path / "B", {"x.jpg": 7})
    run = cli.resolve_cards([a, b], str(b), say=lambda t: None)
    cat, note = cli.open_card_run(run)
    assert [p.primary.parent for p in cat.pictures] == [b / "DCIM" / "100TEST"]
    assert cat.pictures[0].twins and note.startswith(f"Card 2 of 2 ({b})") and note.endswith("spuza --card 1")


def test_describe_one_card_is_empty_and_three_cards_name_each_other(tmp_path):
    a, b, c = (tmp_path / n for n in "ABC")
    assert describe([a], 0) == ""
    text = describe([a, b, c], 0)
    assert "--card 2" in text and "--card 3" in text and "Card 1 of 3" in text


def test_scan_of_both_cards_is_not_called_for_one_card(tmp_path):
    a = make_card(tmp_path / "A", {"1.JPG": 1})
    seen: list[Path] = []

    def fake_scan(root):
        seen.append(root)
        return scan(root)

    cli.open_card_run(cli.resolve_cards([a], None), scan=fake_scan)
    assert seen == [a / "DCIM"]


# --- status line --------------------------------------------------------------------------------

def test_catalog_note_counts_others_and_unreadable():
    assert catalog_note(Catalog([])) == ""
    cat = Catalog([], others=[Path("a.txt"), Path("b.txt")], unreadable=[Path("c")])
    assert catalog_note(cat) == "2 other files · 1 unreadable"
    assert catalog_note(object()) == ""  # a catalog without the fields (older fakes) says nothing


def test_window_status_line_shows_card_note_and_counts(tmp_path):
    from PySide6.QtWidgets import QApplication, QLabel

    from spuza.catalog import Prefetcher
    from spuza.ui.window import MainWindow

    QApplication.instance() or QApplication([])
    cat = Catalog([pic("A.JPG", 1)], others=[Path("n.txt")], unreadable=[Path("u")])
    note = "Card 1 of 2 (E:\\) · F:\\ also mounted: 1 twins, 0 only there — spuza --card 2"
    w = MainWindow(cat, Prefetcher(cat, loader=lambda p, m: None), tmp_path, card_note=note, resume=False)
    try:
        texts = [lb.text() for lb in w.statusBar().findChildren(QLabel)]
        assert f"{note} · 1 other files · 1 unreadable" in texts
        assert not w.lbl_cards.isHidden()
        w2 = MainWindow(Catalog([pic("A.JPG", 1)]), Prefetcher(cat, loader=lambda p, m: None), tmp_path,
                        resume=False)
        assert w2.lbl_cards.isHidden() and w2.lbl_cards.text() == ""
        w2.close()
    finally:
        w.close()
