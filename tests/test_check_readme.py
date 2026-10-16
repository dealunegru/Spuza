"""docs/check_readme.py: a passing README is built from the code's own inventory, then mutated."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_readme", ROOT / "docs" / "check_readme.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

_KEY_TOKEN = {"right": "→", "left": "←", "up": "↑", "down": "↓", "esc": "Esc", "enter": "Enter",
              "space": "Space", "ctrl+,": "Cmd-,"}


@pytest.fixture
def root(tmp_path):
    """A copy of just the root bits the check reads, plus one screenshot."""
    for rel in ("src/spuza/ui/window.py", "src/spuza/ui/review.py", "src/spuza/ui/compare.py",
                "src/spuza/features.py", "src/spuza/cli.py", "src/spuza/catalog.py",
                "pyproject.toml", "docs/FEATURE_FLAGS.md"):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dst)
    (tmp_path / "docs" / "img").mkdir()
    (tmp_path / "docs" / "img" / "a.png").write_bytes(b"x")
    return tmp_path


def good_readme(root: Path) -> str:
    required, _, _ = cr.code_keys(root)
    flags = cr._load_flags(root)
    cli, _ = cr.cli_flags(root)
    exts, _ = cr.code_exts(root)
    version = cr.tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    rows = "\n".join(f"| `{_KEY_TOKEN.get(k, k.upper())}` | does a thing |" for k in sorted(required))
    return (
        f'<img src="docs/img/a.png" alt="x">\n'
        f'![shot](docs/img/a.png) ![ci](https://example.com/x.svg)\n'
        f'<img src="https://img.shields.io/badge/version-{version}-blue">\n\n'
        f"## What's new\n\n- **{version}** the newest.\n- **0.0.1** the oldest.\n\n"
        f"## Using\n\n| Key | Does |\n|---|---|\n{rows}\n\n"
        f"Flags: {' '.join(f'`{f}`' for f in flags)}\n\n"
        f"Options: {' '.join(sorted(cli))}\n\n"
        f"### Formats\n\n| Family | Files |\n|---|---|\n"
        + "\n".join(f"| x | `{e.upper()}` |" for e in sorted(exts)) + "\n\n## License\n"
    )


def test_inventory_is_not_empty(root):
    required, allowed, problems = cr.code_keys(root)
    assert not problems and {"space", "m", "z", "ctrl+,", "a", "esc"} <= required
    assert required <= allowed
    assert "--version" in cr.cli_flags(root)[0] and "--help" not in cr.cli_flags(root)[0]
    assert {"raf", "tif", "heif"} <= cr.code_exts(root)[0]


def test_good_readme_passes(root):
    assert cr.check(good_readme(root), root) == []


def _one(root, text, needle):
    got = cr.check(text, root)
    assert any(needle in g for g in got), got
    assert all(g.startswith("README: ") for g in got)


def test_missing_key(root):
    t = good_readme(root).replace("| `Space` | does a thing |\n", "")
    _one(root, t, "key 'space' is handled")


def test_unknown_key(root):
    t = good_readme(root).replace("## Using\n\n| Key | Does |\n|---|---|\n",
                                  "## Using\n\n| Key | Does |\n|---|---|\n| `Q` | quits |\n")
    _one(root, t, "key 'q' is in a README key table")


def test_keys_in_prose_do_not_count(root):
    t = good_readme(root).replace("| `Space` | does a thing |\n", "") + "\nPress `Space`.\n"
    _one(root, t, "key 'space' is handled")


def test_missing_flag(root):
    flag = cr._load_flags(root)[0]
    _one(root, good_readme(root).replace(f"`{flag}`", flag), f"feature flag `{flag}` is not mentioned")


def test_missing_from_feature_flags_doc(root):
    flag = cr._load_flags(root)[0]
    doc = root / "docs" / "FEATURE_FLAGS.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace(f"`{flag}`", flag), encoding="utf-8")
    _one(root, good_readme(root), f"`{flag}` is missing from docs/FEATURE_FLAGS.md")


def test_missing_cli_flag(root):
    t = good_readme(root).replace("--advanced", "--adv")
    _one(root, t, "command-line flag --advanced is not mentioned")


def test_badge_lagging_pyproject(root):
    py = root / "pyproject.toml"
    py.write_text(py.read_text(encoding="utf-8").replace('version = "0.4.0"', 'version = "0.4.1"', 1),
                  encoding="utf-8")
    t = good_readme(root).replace("badge/version-0.4.1-", "badge/version-0.4.0-")
    _one(root, t, "no static version badge for 0.4.1")


def test_whats_new_missing_version(root):
    t = good_readme(root)
    ver = cr.tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    t = t.replace(f"- **{ver}** the newest.\n", "")
    _one(root, t, "What's new starts at 0.0.1")


def test_whats_new_heading_form_and_newer_version(root):
    ver = cr.tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    t = good_readme(root).replace(f"- **{ver}** the newest.\n", f"### {ver}\n\nthe newest.\n")
    assert cr.check(t, root) == []
    t = t.replace("## What's new\n\n", "## What's new\n\n- **9.9.9** from the future.\n")
    _one(root, t, "What's new starts at 9.9.9")


def test_missing_extension(root):
    _one(root, good_readme(root).replace("`HEIF`", "HEIF"), "extension .heif")


def test_broken_image_path(root):
    _one(root, good_readme(root) + '\n<img src="docs/img/gone.png">\n', "image docs/img/gone.png")
    _one(root, good_readme(root) + "\n![x](docs/img/gone2.png)\n", "image docs/img/gone2.png")


def test_parser_finding_zero_keys(root):
    (root / "src" / "spuza" / "ui" / "window.py").write_text("class MainWindow:\n    pass\n", encoding="utf-8")
    _one(root, good_readme(root), "found no keys in window.py")


def test_moved_source_file_is_drift(root):
    (root / "src" / "spuza" / "cli.py").unlink()
    _one(root, good_readme(root), "cannot read cli.py")


def test_unparsable_source_is_drift_not_a_crash(root):
    (root / "src" / "spuza" / "catalog.py").write_text("def (:\n", encoding="utf-8")
    _one(root, good_readme(root), "cannot read catalog.py")


def test_no_whats_new_and_empty_readme(root):
    got = cr.check("", root)
    assert any("What's new" in g for g in got) and any("no key table" in g for g in got)


def test_main_exit_codes(root, tmp_path, capsys):
    ok = tmp_path / "ok.md"
    ok.write_text(good_readme(root), encoding="utf-8")
    assert cr.main(["--root", str(root), "--readme", str(ok)]) == 0
    assert capsys.readouterr().out.startswith("README matches the code: ")
    bad = tmp_path / "bad.md"
    bad.write_text("nothing", encoding="utf-8")
    assert cr.main(["--root", str(root), "--readme", str(bad)]) == 1
    assert cr.main(["--root", str(root), "--readme", str(tmp_path / "none.md")]) == 1


def test_real_readme_passes():
    """The CI step: the repo's README matches the repo's code."""
    got = cr.check((ROOT / "README.md").read_text(encoding="utf-8"), ROOT)
    assert got == [], "\n".join(got)


def test_newer_version_below_the_first_is_drift(root):
    ver = cr.tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    t = good_readme(root).replace("- **0.0.1** the oldest.", "- **9.9.9** from the future.")
    got = cr.check(t, root)
    assert got == [f"README: What's new names 9.9.9, newer than pyproject's {ver}"], got


def test_cli_flag_is_not_matched_inside_a_longer_flag(root):
    t = good_readme(root).replace("--version", "--version-info")
    _one(root, t, "command-line flag --version is not mentioned")


def test_prefs_key_spellings(root):
    t = good_readme(root).replace("`Cmd-,`", "`⌘,`")
    assert cr.check(t, root) == []
    t = good_readme(root).replace("`Cmd-,`", "`Alt-,`")
    _one(root, t, "`Cmd-,`, `Ctrl-,` or `⌘,`")
