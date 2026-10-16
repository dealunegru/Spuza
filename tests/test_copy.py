"""Copy marked pictures: plan, run, never touch sources or existing files."""

import os
from pathlib import Path

import pytest

from spuza.copy import CopyResult, free_space, human_size, plan_copy, run_copy


@pytest.fixture(autouse=True)
def _logs_dir(tmp_path, monkeypatch):
    """Copy logs go to a per-test folder, never the real app-data one (and never the destination)."""
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))


def make(path: Path, data: bytes, mtime: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def snapshot(folder: Path) -> dict[str, tuple[bytes, int]]:
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir() if p.is_file()}


def test_pair_and_single_copied_names_kept(tmp_path):
    src = tmp_path / "card"
    raw = make(src / "A1.RAF", b"raw" * 100)
    jpg = make(src / "A1.JPG", b"jpg" * 10)
    one = make(src / "B2.DNG", b"dng" * 50)
    dest = tmp_path / "out"
    plan = plan_copy([[raw, jpg], [one]], dest)
    assert plan.pictures == 2
    assert not dest.exists()  # planning creates nothing
    assert plan.total_bytes == 300 + 30 + 150
    res = run_copy(plan)
    assert sorted(p.name for p in dest.iterdir() if not p.name.endswith(".log")) == [
        "A1.JPG", "A1.RAF", "B2.DNG"]
    assert (dest / "A1.RAF").read_bytes() == b"raw" * 100
    assert len(res.copied) == 3 and res.bytes_copied == 480 and not res.cancelled


def test_identical_existing_skipped(tmp_path):
    a = make(tmp_path / "s" / "x.RAF", b"same")
    dest = tmp_path / "d"
    make(dest / "x.RAF", b"same")
    plan = plan_copy([[a]], dest)
    assert plan.items[0].action == "skip" and plan.total_bytes == 0
    res = run_copy(plan)
    assert res.skipped == [a] and res.copied == []


def test_same_size_different_content_renamed(tmp_path):
    a = make(tmp_path / "s" / "x.RAF", b"aaaa")
    dest = tmp_path / "d"
    make(dest / "x.RAF", b"bbbb")
    plan = plan_copy([[a]], dest)
    assert plan.items[0].action == "rename" and plan.items[0].dst.name == "x-1.RAF"


def test_pair_shares_suffix_and_existing_untouched(tmp_path):
    s = tmp_path / "s"
    raw = make(s / "P.RAF", b"newraw")
    jpg = make(s / "P.JPG", b"newjpg")
    dest = tmp_path / "d"
    make(dest / "P.RAF", b"old-raw!!", mtime=1_000_000_000)
    make(dest / "P.JPG", b"identical?")  # jpg slot taken too, by something else
    before = snapshot(dest)
    plan = plan_copy([[raw, jpg]], dest)
    names = sorted(i.dst.name for i in plan.items)
    assert names == ["P-1.JPG", "P-1.RAF"]
    assert all(i.action == "rename" for i in plan.items)
    run_copy(plan)
    after = snapshot(dest)
    for name, val in before.items():
        assert after[name] == val
    assert (dest / "P-1.RAF").read_bytes() == b"newraw"


def test_pair_suffix_when_only_one_clashes(tmp_path):
    s = tmp_path / "s"
    raw = make(s / "P.RAF", b"newraw")
    jpg = make(s / "P.JPG", b"newjpg")
    dest = tmp_path / "d"
    make(dest / "P.RAF", b"different")
    plan = plan_copy([[raw, jpg]], dest)
    assert sorted(i.dst.name for i in plan.items) == ["P-1.JPG", "P-1.RAF"]


def test_clash_between_groups_in_one_plan(tmp_path):
    a = make(tmp_path / "s1" / "IMG.RAF", b"one")
    b = make(tmp_path / "s2" / "IMG.RAF", b"two!")
    plan = plan_copy([[a], [b]], tmp_path / "d")
    assert sorted(i.dst.name for i in plan.items) == ["IMG-1.RAF", "IMG.RAF"]
    run_copy(plan)
    assert (tmp_path / "d" / "IMG.RAF").read_bytes() == b"one"
    assert (tmp_path / "d" / "IMG-1.RAF").read_bytes() == b"two!"


def test_sources_unchanged_and_mtime_preserved(tmp_path):
    a = make(tmp_path / "s" / "m.RAF", b"data" * 1000, mtime=1_500_000_000)
    before = snapshot(a.parent)
    dest = tmp_path / "deep" / "er" / "d"
    run_copy(plan_copy([[a]], dest))
    assert snapshot(a.parent) == before
    assert int((dest / "m.RAF").stat().st_mtime) == 1_500_000_000
    assert not list(dest.glob("*.spuza-part"))


def test_progress_monotonic_ends_at_total(tmp_path):
    a = make(tmp_path / "s" / "big.RAF", os.urandom(3 * 1024 * 1024 + 17))
    b = make(tmp_path / "s" / "small.RAF", b"x" * 10)
    plan = plan_copy([[a], [b]], tmp_path / "d")
    calls = []
    run_copy(plan, progress=lambda d, t, s: calls.append((d, t, s)))
    done = [c[0] for c in calls]
    assert done == sorted(done) and len(done) >= 4
    assert done[-1] == plan.total_bytes and all(c[1] == plan.total_bytes for c in calls)


def test_cancel_midway_leaves_no_part_file(tmp_path):
    a = make(tmp_path / "s" / "big.RAF", os.urandom(4 * 1024 * 1024))
    b = make(tmp_path / "s" / "b.RAF", b"b")
    dest = tmp_path / "d"
    plan = plan_copy([[a], [b]], dest)
    seen = []
    res = run_copy(plan, progress=lambda d, t, s: seen.append(d), cancel=lambda: len(seen) >= 2,
                   log_path=tmp_path / "c.log")
    assert res.cancelled
    assert not list(dest.glob("*.spuza-part")) and not (dest / "big.RAF").exists()
    assert a.exists() and b.exists()


def test_failing_source_recorded_others_copy(tmp_path):
    good = make(tmp_path / "s" / "g.RAF", b"good")
    missing = tmp_path / "s" / "gone.RAF"
    dest = tmp_path / "d"
    plan = plan_copy([[missing], [good]], dest)
    res = run_copy(plan)
    assert [p for p, _ in res.failed] == [missing] and res.failed[0][1]
    assert res.copied == [good] and (dest / "g.RAF").read_bytes() == b"good"
    assert not list(dest.glob("*.spuza-part"))


def test_target_appearing_meanwhile_is_not_overwritten(tmp_path):
    a = make(tmp_path / "s" / "r.RAF", b"mine")
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    make(dest / "r.RAF", b"theirs")  # appears after planning
    res = run_copy(plan)
    assert (dest / "r.RAF").read_bytes() == b"theirs"
    assert res.copied and res.copied[0].exists() is True
    assert any(p.read_bytes() == b"mine" for p in dest.glob("r-*.RAF"))


def test_log_lists_every_file(tmp_path, monkeypatch):
    logs = tmp_path / "logs-here"
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(logs))
    s = tmp_path / "s"
    a = make(s / "a.RAF", b"aa")
    b = make(s / "b.RAF", b"bb")
    c = make(s / "c.RAF", b"cc")
    gone = s / "gone.RAF"
    dest = tmp_path / "d"
    make(dest / "b.RAF", b"bb")
    make(dest / "c.RAF", b"zz")
    res = run_copy(plan_copy([[a], [b], [c], [gone]], dest))
    assert isinstance(res, CopyResult) and res.log_path is not None and res.log_path.parent == logs
    assert res.log_path.name.startswith("spuza-copy-") and res.log_path.suffix == ".log"
    text = res.log_path.read_text(encoding="utf-8")
    for name in ("a.RAF", "b.RAF", "c.RAF", "c-1.RAF", "gone.RAF"):
        assert name in text


def test_explicit_log_path(tmp_path):
    a = make(tmp_path / "s" / "a.RAF", b"aa")
    res = run_copy(plan_copy([[a]], tmp_path / "d"), log_path=tmp_path / "my.log")
    assert res.log_path == tmp_path / "my.log" and "a.RAF" in res.log_path.read_text(encoding="utf-8")


def test_free_space_and_human_size(tmp_path):
    assert free_space(tmp_path / "not" / "yet" / "there") > 0
    assert human_size(602_000) == "602 KB"
    assert human_size(1_200_000_000) == "1.2 GB"
    assert human_size(0) == "0 B"
    assert human_size(999) == "999 B"


def _no_links(monkeypatch):
    def boom(*a, **k):
        raise OSError("no hard links on this volume")

    monkeypatch.setattr(os, "link", boom)


def test_no_hardlink_fallback_publishes(tmp_path, monkeypatch):
    a = make(tmp_path / "s" / "f.RAF", b"fresh", mtime=1_500_000_000)
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    _no_links(monkeypatch)
    res = run_copy(plan)
    assert res.copied == [a] and (dest / "f.RAF").read_bytes() == b"fresh"
    assert int((dest / "f.RAF").stat().st_mtime) == 1_500_000_000
    assert not list(dest.glob("*.spuza-part"))


def test_no_hardlink_fallback_never_replaces(tmp_path, monkeypatch):
    a = make(tmp_path / "s" / "f.RAF", b"mine")
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    make(dest / "f.RAF", b"theirs")  # appears after planning
    _no_links(monkeypatch)
    res = run_copy(plan)
    assert (dest / "f.RAF").read_bytes() == b"theirs"
    assert (dest / "f-1.RAF").read_bytes() == b"mine" and res.copied == [a]
    assert not list(dest.glob("*.spuza-part"))


def test_unlink_failure_after_publish_is_not_a_failure(tmp_path, monkeypatch):
    a = make(tmp_path / "s" / "f.RAF", b"data")
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    real = os.unlink

    def flaky(p, *args, **kw):
        if str(p).endswith(".spuza-part"):
            raise PermissionError("locked")
        return real(p, *args, **kw)

    monkeypatch.setattr(os, "unlink", flaky)
    res = run_copy(plan)
    assert res.copied == [a] and not res.failed and (dest / "f.RAF").read_bytes() == b"data"


def test_existing_log_never_truncated(tmp_path):
    a = make(tmp_path / "s" / "a.RAF", b"aa")
    mine = tmp_path / "my.log"
    mine.write_text("precious", encoding="utf-8")
    res = run_copy(plan_copy([[a]], tmp_path / "d"), log_path=mine)
    assert mine.read_text(encoding="utf-8") == "precious"
    assert res.log_path == tmp_path / "my-1.log" and "a.RAF" in res.log_path.read_text(encoding="utf-8")
