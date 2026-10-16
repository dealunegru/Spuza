"""reveal(): argv per platform, raw chosen for a pair, soft failure; the viewer's right-click menu."""

from pathlib import Path

from spuza import reveal as rv
from spuza.catalog import Picture


class Recorder:
    def __init__(self, exc=None):
        self.calls, self.exc = [], exc

    def __call__(self, argv):
        self.calls.append(argv)
        if self.exc:
            raise self.exc


def test_labels():
    assert rv.reveal_label("darwin") == "Reveal in Finder"
    assert rv.reveal_label("win32") == "Show in Explorer"
    assert rv.reveal_label("linux") == "Show in file manager"
    assert rv.reveal_label("freebsd13") == "Show in file manager"


def test_darwin_argv():
    run = Recorder()
    assert rv.reveal(Path("/pics/a b/R1.RAF"), "darwin", run) is None
    assert run.calls == [["open", "-R", str(Path("/pics/a b/R1.RAF"))]]


def test_windows_argv_native_backslashes_and_space():
    run = Recorder()
    rv.reveal("E:/tmp/my pics/R1.RAF", "win32", run)
    (argv,) = run.calls
    assert argv[:2] == ["explorer", "/select,"]
    assert argv[2] == "E:\\tmp\\my pics\\R1.RAF"


def test_linux_argv_opens_parent_dir():
    run = Recorder()
    rv.reveal(Path("/pics/R1.RAF"), "linux", run)
    assert run.calls == [["xdg-open", str(Path("/pics"))]]


def test_oserror_is_returned_not_raised():
    err = FileNotFoundError("no xdg-open")
    assert rv.reveal(Path("/pics/R1.RAF"), "linux", Recorder(err)) is err


def test_nonzero_exit_is_not_looked_at():
    class Proc:
        returncode = 1

    assert rv.reveal(Path("C:/a.RAF"), "win32", lambda argv: Proc()) is None


def test_raw_is_the_target_for_a_pair():
    raw, jpg = Path("/p/R1.RAF"), Path("/p/R1.JPG")
    pair = Picture("R1", raw, jpg, (raw, jpg), "RAF+JPG", 1, 0.0)
    assert rv.reveal_target(pair) == raw
    assert rv.reveal_target(Picture("R1", jpg, None, (jpg,), "JPG", 1, 0.0)) == jpg


def test_viewer_right_click_menu(monkeypatch):
    from spuza.ui.viewer import Viewer

    got = []
    monkeypatch.setattr(rv, "reveal", lambda p: got.append(p))
    v = Viewer()
    v.resize(400, 300)
    assert v.context_menu_for(v.viewport().rect().center()) is None  # nothing shown yet
    raw = Path("/p/R1.RAF")
    v.show_placeholder(4, Picture("R1", raw, Path("/p/R1.JPG"), (raw,), "RAF+JPG", 1, 0.0))
    menu = v.context_menu_for(v.viewport().rect().center())
    (action,) = menu.actions()
    assert action.text() == rv.reveal_label()
    action.trigger()
    assert got == [raw]
    assert v.index == 4  # nothing else moved
