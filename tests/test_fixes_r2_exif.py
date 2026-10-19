"""bug-420: the exiftool PATH lookup is paid once on the main thread, never on a copy worker."""

from spuza import rotation


def test_lookup_is_cached_before_any_test_body_runs():
    # the session fixture in conftest warmed it; a sheet copy with rotations then only hits the cache
    assert rotation._which_exiftool.cache_info().currsize == 1


def test_cached_lookup_does_not_search_the_path_again(monkeypatch):
    import shutil

    calls = []
    monkeypatch.setattr(shutil, "which", lambda *a, **k: calls.append(a) or "never")
    rotation._which_exiftool()
    assert calls == []
