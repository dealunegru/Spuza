"""Friend round 2: organised copy (raw / soc / ed), the log out of the destination, and eject. No real eject."""

import os
import subprocess
from pathlib import Path

import pytest

from spuza import copy as copy_mod
from spuza import eject as eject_mod
from spuza.catalog import KNOWN_EXTS, RAW_EXTS
from spuza.copy import logs_dir, plan_copy, run_copy
from spuza.ui import settings


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))


def mk(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def rel(plan, dest):
    return sorted(str(i.dst.relative_to(dest)).replace(os.sep, "/") for i in plan.items)


# ---- plan: organise ---------------------------------------------------------------------------
def test_every_raw_ext_goes_to_raw_and_pictures_to_soc(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    groups = [[mk(src / f"r{i}{ext}")] for i, ext in enumerate(sorted(RAW_EXTS))]
    soc_exts = sorted(KNOWN_EXTS - RAW_EXTS)
    groups += [[mk(src / f"p{i}{ext}")] for i, ext in enumerate(soc_exts)]
    plan = plan_copy(groups, dest, organise=True)
    assert plan.organise
    for it in plan.items:
        want = "raw" if it.src.suffix in RAW_EXTS else "soc"
        assert it.dst.parent == dest / want and it.dst.name == it.src.name


def test_pair_splits_and_companions_follow_the_group(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    pair = [mk(src / "A.RAF"), mk(src / "A.JPG"), mk(src / "A.xmp")]
    lone = [mk(src / "B.JPG"), mk(src / "B.MOV"), mk(src / "B.THM")]
    plan = plan_copy([pair, lone], dest, organise=True)
    assert rel(plan, dest) == ["raw/A.RAF", "raw/A.xmp", "soc/A.JPG", "soc/B.JPG", "soc/B.MOV", "soc/B.THM"]


def test_organise_off_is_flat_as_before(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    plan = plan_copy([[mk(src / "A.RAF"), mk(src / "A.JPG")]], dest)
    assert not plan.organise and rel(plan, dest) == ["A.JPG", "A.RAF"]


def test_organised_skip_identical_and_group_suffix_per_folder(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    a = [mk(src / "A.RAF", b"raw"), mk(src / "A.JPG", b"jpg")]
    mk(dest / "raw" / "A.RAF", b"raw")  # identical already there
    mk(dest / "soc" / "A.JPG", b"jpg")
    plan = plan_copy([a], dest, organise=True)
    assert [i.action for i in plan.items] == ["skip", "skip"] and plan.total_bytes == 0
    # a different file in soc only: the whole group takes -1, in each file's own folder
    mk(dest / "soc" / "A.JPG", b"other")
    plan = plan_copy([a], dest, organise=True)
    assert rel(plan, dest) == ["raw/A-1.RAF", "soc/A-1.JPG"] and {i.action for i in plan.items} == {"rename"}


def test_same_name_in_different_folders_does_not_collide_but_same_folder_does(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    one = [mk(src / "a" / "IMG.RAF", b"1")]
    two = [mk(src / "b" / "IMG.JPG", b"2")]  # different folder: no clash
    three = [mk(src / "c" / "img.raf", b"3")]  # same folder as `one`, case-folded: clash
    plan = plan_copy([one, two, three], dest, organise=True)
    assert rel(plan, dest) == ["raw/IMG.RAF", "raw/img-1.raf", "soc/IMG.JPG"]


# ---- run: folders and the log -----------------------------------------------------------------
def test_run_makes_all_three_folders_ed_empty_even_with_no_soc(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    plan = plan_copy([[mk(src / "A.RAF", b"r")]], dest, organise=True)
    res = run_copy(plan)
    assert not res.failed
    assert sorted(p.name for p in dest.iterdir()) == ["ed", "raw", "soc"]
    assert list((dest / "ed").iterdir()) == [] and list((dest / "soc").iterdir()) == []
    assert (dest / "raw" / "A.RAF").read_bytes() == b"r"


def test_organised_copy_with_nothing_to_copy_still_makes_folders(tmp_path):
    dest = tmp_path / "d"
    run_copy(plan_copy([], dest, organise=True))
    assert sorted(p.name for p in dest.iterdir()) == ["ed", "raw", "soc"]


def test_flat_copy_makes_no_subfolders(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    run_copy(plan_copy([[mk(src / "A.RAF")]], dest))
    assert [p.name for p in dest.iterdir()] == ["A.RAF"]


def test_log_goes_to_logs_dir_never_into_dest(tmp_path):
    src, dest = tmp_path / "s", tmp_path / "d"
    for organise in (True, False):
        res = run_copy(plan_copy([[mk(src / "A.RAF", b"r"), mk(src / "A.JPG", b"j")]], dest, organise=organise))
        assert res.log_path is not None and res.log_path.parent == tmp_path / "logs"
        assert res.log_path.name.startswith("spuza-copy-") and res.log_path.suffix == ".log"
        assert not list(dest.rglob("*.log"))
    top = sorted(p.name for p in dest.iterdir())
    assert top == ["A.JPG", "A.RAF", "ed", "raw", "soc"]  # pictures and the three folders only


def test_explicit_log_path_wins(tmp_path):
    src = tmp_path / "s"
    res = run_copy(plan_copy([[mk(src / "A.RAF")]], tmp_path / "d", organise=True), log_path=tmp_path / "my.log")
    assert res.log_path == tmp_path / "my.log"


def test_logs_dir_env_then_appdata_then_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "x"))
    assert logs_dir() == tmp_path / "x"
    monkeypatch.delenv("SPUZA_LOGS_DIR")
    d = logs_dir()
    assert d.name == "logs" and d != tmp_path / "x"


def test_logs_dir_falls_back_to_home_when_qt_has_no_location(tmp_path, monkeypatch):
    import sys

    monkeypatch.delenv("SPUZA_LOGS_DIR")
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)  # forces the except branch
    d = logs_dir()
    assert d == Path.home() / ".spuza" / "logs"


def test_no_code_path_writes_a_log_into_dest():
    text = Path(copy_mod.__file__).read_text(encoding="utf-8")
    assert "plan.dest / f\"spuza-copy" not in text and "logs_dir() / f\"spuza-copy-" in text


# ---- settings -----------------------------------------------------------------------------------
def test_settings_defaults_on_and_round_trip():
    s = settings.open_settings()
    assert settings.read_organise(s) and settings.read_eject_after(s) and settings.read_quit_after(s)
    assert (settings.COPY_ORGANISE, settings.COPY_EJECT, settings.COPY_QUIT) == ("copy/organise", "copy/eject", "copy/quit")
    settings.write_organise(s, False)
    settings.write_eject_after(s, False)
    settings.write_quit_after(s, False)
    s.sync()
    s2 = settings.open_settings()
    assert not settings.read_organise(s2) and not settings.read_eject_after(s2) and not settings.read_quit_after(s2)


# ---- eject: everything injected, nothing real -------------------------------------------------
class Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Recorder:
    def __init__(self, *results):
        self.calls, self.results = [], list(results)

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        r = self.results.pop(0) if self.results else Done()
        if isinstance(r, BaseException):
            raise r
        return r


def test_mac_eject_ok_and_unmount_fallback():
    run = Recorder(Done())
    assert eject_mod.eject(Path("/Volumes/CARD"), run=run, platform="darwin")[0]
    assert run.calls[0][0][:2] == ["diskutil", "eject"] and run.calls[0][1]["timeout"] == 20.0
    run = Recorder(Done(1, err="busy"), Done())
    ok, _ = eject_mod.eject(Path("/Volumes/CARD"), run=run, platform="darwin")
    assert ok and [c[0][1] for c in run.calls] == ["eject", "unmount"]
    run = Recorder(Done(1, err="busy"), Done(1, err="still busy"))
    ok, why = eject_mod.eject(Path("/Volumes/CARD"), run=run, platform="darwin")
    assert not ok and "busy" in why


@pytest.mark.parametrize("boom", [subprocess.TimeoutExpired("x", 1), OSError("no diskutil"), RuntimeError("odd")])
def test_eject_never_raises(boom):
    ok, why = eject_mod.eject(Path("/Volumes/CARD"), timeout=0.1, run=Recorder(boom, boom), platform="darwin")
    assert ok is False and isinstance(why, str) and why
    ok, why = eject_mod.eject(Path("E:/"), run=Recorder(boom), platform="win32", sleep=lambda s: None)
    assert ok is False and why


def test_windows_eject_checks_the_drive_is_gone_bounded():
    run = Recorder(Done())
    gone = iter([True, True, False])
    sleeps = []
    ok, _ = eject_mod.eject(Path("E:/DCIM"), run=run, platform="win32", sleep=sleeps.append,
                            exists=lambda p: next(gone))
    assert ok and run.calls[0][0][0].lower().startswith("powershell") and "'E:\\'" in run.calls[0][0][-1]
    assert len(sleeps) == 2
    sleeps.clear()
    ok, why = eject_mod.eject(Path("E:/"), run=Recorder(Done()), platform="win32", sleep=sleeps.append,
                              exists=lambda p: True)
    assert not ok and "still mounted" in why
    assert len(sleeps) == eject_mod.WIN_CHECKS - 1 and sum(sleeps) <= 5.0


def test_windows_nonzero_and_unsupported_platform():
    ok, why = eject_mod.eject(Path("E:/"), run=Recorder(Done(1, err="denied")), platform="win32")
    assert not ok and "denied" in why
    run = Recorder()
    assert eject_mod.eject(Path("/media/x"), run=run, platform="linux") == (False, "Eject is not supported here")
    assert run.calls == []
