"""Static checks: the Windows build is wired into CI and the release (stdlib only; CI itself is unmeasured here)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
SPEC = ROOT / "packaging" / "spuza-windows.spec"


def _job(name: str) -> str:
    """Text of one top-level job under `jobs:` (two-space indent keys)."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S[^\n]*:\n|\Z)", CI, re.S | re.M)
    assert m, f"job {name!r} missing from ci.yml"
    return m.group(1)


def test_win_job_builds_on_windows_with_the_windows_spec():
    win = _job("win")
    assert "runs-on: windows-latest" in win
    assert "timeout-minutes:" in win
    assert "--group build" in win
    assert "pyinstaller packaging/spuza-windows.spec" in win


def test_win_job_smoke_runs_version_on_the_built_exe():
    win = _job("win")
    assert re.search(r"dist/Spuza/spuza-cli\.exe\s+--version", win)
    assert "LASTEXITCODE" in win  # a non-zero exit must fail the step


def test_win_job_zips_and_uploads():
    win = _job("win")
    assert "Compress-Archive" in win and "dist/Spuza-windows.zip" in win
    assert "actions/upload-artifact" in win
    assert "name: Spuza-windows" in win
    assert "path: dist/Spuza-windows.zip" in win


def test_release_needs_win_and_attaches_the_zip():
    rel = _job("release")
    needs = re.search(r"needs:\s*\[([^\]]*)\]", rel)
    assert needs and {"test", "app", "win"} <= {n.strip() for n in needs.group(1).split(",")}
    assert re.search(r"download-artifact[^\n]*\n\s+with:\n\s+name: Spuza-windows\b", rel)
    assert "dist/Spuza-windows.zip" in rel.split("gh release create", 1)[1]


def test_windows_spec_names_the_entry_script_and_both_exes():
    text = SPEC.read_text(encoding="utf-8")
    assert "spuza_app.py" in text
    assert (ROOT / "packaging" / "spuza_app.py").is_file()
    assert 'name="Spuza"' in text and 'name="spuza-cli"' in text
    assert "BUNDLE(" not in text  # that is the macOS .app step
