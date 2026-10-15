"""Race guard: dst appearing between the existence check and publish must never be replaced.

Forces POSIX replace semantics (os.rename == os.replace) so a check-then-rename publish would
overwrite; the tests fail against such an implementation.
"""

import os
from pathlib import Path

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


def test_dst_created_during_failed_link_not_replaced(tmp_path, monkeypatch):
    a = _src(tmp_path)
    dest = tmp_path / "d"
    plan = plan_copy([[a]], dest)
    monkeypatch.setattr(os, "rename", os.replace)

    def racy_link(src, dst, *args, **kw):
        if Path(dst).name == "r.RAF":
            Path(dst).write_bytes(b"theirs")  # the other writer wins the race
        raise OSError("no hard links on this volume")

    monkeypatch.setattr(os, "link", racy_link)
    _check(run_copy(plan), a, dest)


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
