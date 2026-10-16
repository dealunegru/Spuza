"""Fail when README.md drifts from the code.

    uv run python docs/check_readme.py [--readme PATH] [--root PATH]

Prints one line per drift (``README: <what>``) and exits 1 if there is any; exits 0 with a
one-line summary otherwise. Everything is read from the code (nothing hand-typed here), and
nothing imports PySide6: the Qt sources are parsed with ``ast``.

Checks:
  1. Keys. Every key MainWindow.keyPressEvent handles, and every QShortcut key sequence, must be
     in a README key table (a table whose header's first cell is ``Key``; keys are the backticked
     tokens of the first cell). Every README table key must be a key some window, review or
     compare handler knows. Keys in prose do not count.
  2. Flags. Every name in ``spuza.features.FLAGS`` is backticked in README.md and in
     docs/FEATURE_FLAGS.md.
  3. CLI flags. Every ``--option`` added in src/spuza/cli.py (bar ``--help``) is in the README.
  4. Version. pyproject's version is in a static shields badge
     (``img.shields.io/badge/version-<ver>-``), and the first version named in the
     ``## What's new`` section is that version. "Named" is the first bold or backticked
     ``X.Y.Z`` or a ``### X.Y.Z`` heading, whichever comes first. A newer version there is drift.
  5. Extensions. Every extension in catalog.py's RAW_EXTS/IMAGE_EXTS is a backticked token
     (case-insensitive, dot optional) in the ``Formats`` section.
  6. Images. Every relative image path in the README exists under --root.

A source file that moved, or a parse that finds nothing, is reported as drift, never a pass.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# Qt key names (after "Key_") -> the canonical token; anything else is lowercased as is.
_QT_NAMES = {"right": "right", "left": "left", "up": "up", "down": "down", "escape": "esc",
             "return": "enter", "enter": "enter", "space": "space"}
# README spellings -> canonical.
_README_NAMES = {"→": "right", "←": "left", "↑": "up", "↓": "down", "esc": "esc", "escape": "esc",
                 "enter": "enter", "return": "enter", "space": "space",
                 "⌘,": "ctrl+,", "cmd-,": "ctrl+,", "ctrl-,": "ctrl+,", "ctrl+,": "ctrl+,", "cmd+,": "ctrl+,"}


def _qt_key(attr: str) -> str:
    n = attr[len("Key_"):].lower()
    return _QT_NAMES.get(n, n)


def _readme_key(tok: str) -> str:
    t = tok.strip().lower()
    return _README_NAMES.get(t, t)


def _seq_key(seq: str) -> str:
    t = seq.strip().lower()
    return _README_NAMES.get(t, t)


def _parse(path: Path) -> tuple[ast.Module | None, str | None]:
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except (OSError, SyntaxError, ValueError) as e:
        return None, f"cannot read {path.name} ({e.__class__.__name__}): the parser is out of date"


def _key_attrs(node: ast.AST) -> set[str]:
    return {_qt_key(n.attr) for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr.startswith("Key_")}


def _sequences(node: ast.AST) -> set[str]:
    """Literal strings given to QKeySequence(...). StandardKey.* arguments are not strings."""
    out = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "QKeySequence"
                and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            out.add(_seq_key(n.args[0].value))
    return out


def code_keys(root: Path) -> tuple[set[str], set[str], list[str]]:
    """(required, allowed, problems): keys that must be documented, keys that may be."""
    problems: list[str] = []
    ui = root / "src" / "spuza" / "ui"
    win, err = _parse(ui / "window.py")
    if err:
        return set(), set(), [err]
    required: set[str] = set()
    for n in ast.walk(win):
        if isinstance(n, ast.ClassDef) and n.name == "MainWindow":
            for f in n.body:
                if isinstance(f, ast.FunctionDef) and f.name == "keyPressEvent":
                    required |= _key_attrs(f)
    required |= _sequences(win)
    if not required:
        problems.append("found no keys in window.py: the parser is out of date")
    allowed = set(required) | _key_attrs(win) | _sequences(win)
    for name in ("review.py", "compare.py"):
        tree, err = _parse(ui / name)
        if err:
            problems.append(err)
        else:
            allowed |= _key_attrs(tree)
    return required, allowed, problems


def readme_keys(text: str) -> set[str]:
    """Backticked tokens in the first cell of every table whose header starts with ``Key``."""
    keys: set[str] = set()
    in_table = False
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not in_table and cells and cells[0] == "Key":
            in_table = True
            continue
        if in_table and cells and not re.fullmatch(r"[-: ]+", cells[0]):
            keys |= {_readme_key(t) for t in re.findall(r"`([^`]+)`", cells[0])}
    return keys


def _load_flags(root: Path):
    path = root / "src" / "spuza" / "features.py"
    spec = importlib.util.spec_from_file_location("_check_readme_features", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses looks the module up by name
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return list(mod.FLAGS)


def cli_flags(root: Path) -> tuple[set[str], list[str]]:
    tree, err = _parse(root / "src" / "spuza" / "cli.py")
    if err:
        return set(), [err]
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--"):
                    out.add(a.value)
    out.discard("--help")
    return out, ([] if out else ["found no CLI flags in cli.py: the parser is out of date"])


def _set_literal(node: ast.AST, env: dict[str, set[str]]) -> set[str] | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset" and node.args:
        return _set_literal(node.args[0], env)
    if isinstance(node, ast.Set):
        return {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        a, b = _set_literal(node.left, env), _set_literal(node.right, env)
        return None if a is None or b is None else a | b
    return None


def code_exts(root: Path) -> tuple[set[str], list[str]]:
    tree, err = _parse(root / "src" / "spuza" / "catalog.py")
    if err:
        return set(), [err]
    env: dict[str, set[str]] = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = _set_literal(n.value, env)
            if v is not None:
                env[n.targets[0].id] = v
    exts = env.get("KNOWN_EXTS") or (env.get("RAW_EXTS", set()) | env.get("IMAGE_EXTS", set()))
    exts = {e.lstrip(".").lower() for e in exts}
    return exts, ([] if exts else ["found no extensions in catalog.py: the parser is out of date"])


def section(text: str, title: str) -> str | None:
    """Body of the first heading whose text is ``title`` (straight or curly apostrophe)."""
    norm = title.replace("'", "’")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"(#{1,6})\s+(.*?)\s*$", line)
        if m and m.group(2).replace("'", "’") == norm:
            level = len(m.group(1))
            body = []
            for nxt in lines[i + 1:]:
                h = re.match(r"(#{1,6})\s", nxt)
                if h and len(h.group(1)) <= level:
                    break
                body.append(nxt)
            return "\n".join(body)
    return None


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


_VERSION_MENTION = re.compile(r"(?:\*\*|`)v?(\d+\.\d+\.\d+)|^###\s+v?(\d+\.\d+\.\d+)", re.M)


def check(readme_text: str, root: Path) -> list[str]:
    root = Path(root)
    out: list[str] = []

    def drift(msg: str) -> None:
        out.append(f"README: {msg}")

    # 1. keys
    required, allowed, problems = code_keys(root)
    for p in problems:
        drift(p)
    documented = readme_keys(readme_text)
    if not documented:
        drift("no key table found (a table whose header's first cell is `Key`)")
    for k in sorted(required - documented):
        drift(f"key {k!r} is handled in the window but is in no README key table")
    if allowed:
        for k in sorted(documented - allowed):
            drift(f"key {k!r} is in a README key table but no window, review or compare handler uses it"
                  + (" (the Preferences key is spelled `Cmd-,`, `Ctrl-,` or `⌘,`)" if "," in k else ""))

    # 2. flags
    try:
        flags = _load_flags(root)
    except Exception as e:  # a moved or broken features.py must not pass silently
        flags = []
        drift(f"cannot load spuza.features ({e.__class__.__name__}): the parser is out of date")
    else:
        if not flags:
            drift("found no flags in spuza.features.FLAGS: the parser is out of date")
    try:
        flags_doc = (root / "docs" / "FEATURE_FLAGS.md").read_text(encoding="utf-8")
    except OSError:
        flags_doc = ""
        drift("docs/FEATURE_FLAGS.md is missing")
    for f in flags:
        if f"`{f}`" not in readme_text:
            drift(f"feature flag `{f}` is not mentioned")
        if flags_doc and f"`{f}`" not in flags_doc:
            drift(f"feature flag `{f}` is missing from docs/FEATURE_FLAGS.md")

    # 3. CLI flags
    cflags, problems = cli_flags(root)
    for p in problems:
        drift(p)
    for f in sorted(cflags):
        if not re.search(re.escape(f) + r"(?![\w-])", readme_text):
            drift(f"command-line flag {f} is not mentioned")

    # 4. version
    version = None
    try:
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        _vtuple(version)
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
        version = None
        drift("cannot read a X.Y.Z [project] version from pyproject.toml")
    if version:
        if f"img.shields.io/badge/version-{version}-" not in readme_text:
            drift(f"no static version badge for {version} (img.shields.io/badge/version-{version}-...)")
        wn = section(readme_text, "What's new")
        if wn is None:
            drift("no '## What's new' section")
        else:
            found = [m.group(1) or m.group(2) for m in _VERSION_MENTION.finditer(wn)]
            if not found:
                drift("What's new names no version (bold or backticked X.Y.Z, or a ### X.Y.Z heading)")
            else:
                if found[0] != version:
                    drift(f"What's new starts at {found[0]}, pyproject is {version}")
                for v in found:
                    if _vtuple(v) > _vtuple(version):
                        drift(f"What's new names {v}, newer than pyproject's {version}")

    # 5. extensions
    exts, problems = code_exts(root)
    for p in problems:
        drift(p)
    fmt = section(readme_text, "Formats")
    if fmt is None:
        drift("no 'Formats' section")
    else:
        shown = {t.lstrip(".").lower() for t in re.findall(r"`([^`]+)`", fmt)}
        for e in sorted(exts - shown):
            drift(f"extension .{e} is read by the code but not in the Formats table")

    # 6. images
    refs = re.findall(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", readme_text)
    refs += re.findall(r"!\[[^\]]*\]\(([^)\s]+)", readme_text)
    for ref in dict.fromkeys(refs):
        if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:|//", ref):
            continue
        if not (root / ref.split("#")[0].split("?")[0]).is_file():
            drift(f"image {ref} does not exist")

    # keep the count line honest: stash it for main()
    check.counts = (len(required | documented), len(flags), len(cflags), len(exts), version)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail when README.md drifts from the code.")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--readme", type=Path, default=None)
    a = ap.parse_args(argv)
    readme = a.readme or a.root / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError as e:
        print(f"README: cannot read {readme}: {e}")
        return 1
    problems = check(text, a.root)
    for p in problems:
        print(p)
    if problems:
        return 1
    keys, flags, cli, exts, version = check.counts
    print(f"README matches the code: {keys} keys, {flags} flags, {cli} CLI flags, "
          f"{exts} extensions, version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
