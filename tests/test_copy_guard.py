"""Race guard: dst appearing between the existence check and publish must never be replaced.

Forces POSIX replace semantics (os.rename == os.replace) so a check-then-rename publish would
overwrite; the tests fail against such an implementation.
"""

import os
from pathlib import Path

import spuza.copy as copy_module
from spuza.copy import plan_copy, run_copy


def _src(tmp_path: Path) -> Path:
    p = tmp_path / "s" / "r.RAF"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"mine")
    return p


def _check(res, a, dest):
    assert (dest / "r.RAF").read_bytes() == b"theirs"
    assert (dest / "r-1.RAF").read_bytes() == b"mine"
    assert res.copied == [a] and not res.failed
    assert not list(dest.glob("*.spuza-part"))


def test_dst_appears_after_check_before_publish_not_replaced(tmp_path, monkeypatch):
    """The other writer wins at the last moment: after any existence check, right before the
    publishing call (os.rename in a check-then-rename publish, the O_EXCL open otherwise)."""
    a = _src(tmp_path)
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    target = dest / "r.RAF"
    fired = []

    def theirs_arrive():
        if not fired:
            fired.append(1)
            target.write_bytes(b"theirs")

    real_replace = os.replace

    def racy_rename(src, dst, *args, **kw):
        if Path(dst).name == "r.RAF":
            theirs_arrive()
        return real_replace(src, dst)  # POSIX rename semantics: silently replaces

    real_open = open

    def racy_open(file, mode="r", *args, **kw):
        if "x" in mode and Path(file).name == "r.RAF":
            theirs_arrive()
        return real_open(file, mode, *args, **kw)

    def no_link(*args, **kw):
        raise OSError("no hard links on this volume")

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(os, "rename", racy_rename)
    monkeypatch.setattr(copy_module, "open", racy_open, raising=False)
    _check(run_copy(plan), a, dest)
    assert fired


def test_lexists_check_blind_to_dst_not_replaced(tmp_path, monkeypatch):
    a = _src(tmp_path)
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    dest.mkdir()
    (dest / "r.RAF").write_bytes(b"theirs")
    real = os.path.lexists
    monkeypatch.setattr(os, "rename", os.replace)
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no links")))
    # the existence check says "free" for r.RAF: a publish relying on it would overwrite
    monkeypatch.setattr(os.path, "lexists", lambda p: False if Path(p).name == "r.RAF" else real(p))
    _check(run_copy(plan), a, dest)
