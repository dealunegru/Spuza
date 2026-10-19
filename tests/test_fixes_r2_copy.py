"""bug-419: a read-only source must not leave a locked temp or a read-only copy."""

import os
import stat
from pathlib import Path

import pytest

from spuza.copy import plan_copy, run_copy

MTIME = 1_600_000_000


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))


@pytest.fixture
def ro_sources():
    made: list[Path] = []
    yield made
    for p in made:  # let tmp_path cleanup delete them
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def ro(made: list[Path], path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (MTIME, MTIME))
    os.chmod(path, stat.S_IREAD)
    made.append(path)
    return path


def check(dest: Path, src: Path) -> None:
    assert not list(dest.rglob("*.spuza-part"))
    out = dest / src.name
    assert out.read_bytes() == src.read_bytes()
    assert os.stat(out).st_mtime_ns == os.stat(src).st_mtime_ns
    assert os.access(out, os.W_OK)
    if os.name == "nt":
        assert not os.stat(out).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    os.unlink(out)
    assert not out.exists()


def test_read_only_source_copy_is_deletable_and_clean(tmp_path, ro_sources):
    src = ro(ro_sources, tmp_path / "card" / "A1.RAF", b"raw" * 100)
    dest = tmp_path / "out"
    res = run_copy(plan_copy([[src]], dest))
    assert res.copied == [src] and not res.failed
    check(dest, src)


def test_read_only_source_no_hardlink_fallback(tmp_path, ro_sources, monkeypatch):
    def nolink(*a, **k):
        raise OSError("no hard links here")

    monkeypatch.setattr(os, "link", nolink)
    src = ro(ro_sources, tmp_path / "card" / "A2.JPG", b"jpg" * 40)
    dest = tmp_path / "out"
    res = run_copy(plan_copy([[src]], dest))
    assert res.copied == [src] and not res.failed
    check(dest, src)


def test_read_only_source_with_turns(tmp_path, ro_sources):
    # not a real JPEG: the rotation is kept/declined, but the temp and mode rules still apply
    src = ro(ro_sources, tmp_path / "card" / "A3.JPG", b"\xff\xd8not really" * 10)
    dest = tmp_path / "out"
    res = run_copy(plan_copy([[src]], dest, turns={src: 1}))
    assert not res.failed
    check(dest, src)


def test_read_only_source_organised(tmp_path, ro_sources):
    raw = ro(ro_sources, tmp_path / "card" / "B1.RAF", b"raw" * 50)
    jpg = ro(ro_sources, tmp_path / "card" / "B1.JPG", b"jpg" * 20)
    dest = tmp_path / "out"
    res = run_copy(plan_copy([[raw, jpg]], dest, organise=True))
    assert not res.failed
    check(dest / "raw", raw)
    check(dest / "soc", jpg)
