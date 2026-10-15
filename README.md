# Spuza

Fast photo culling. Put in the card, run `spuza`, and the first picture shows.
Step through, mark the keepers, review them, copy them to a folder.

| Key | Does |
|---|---|
| Space or M | mark / unmark the picture |
| Right or J | next picture |
| Left or K | back |
| Z | 100% zoom |
| F | full-screen (Esc goes back) |
| R | review the marked pictures |
| C | copy the marked pictures to a folder |

A RAF or DNG and its same-stem JPEG are one picture and are copied together.
Formats: RAF, DNG, JPEG, PNG, GIF, BMP, TIFF, HEIC. Nothing is ever deleted.

## Install on a Mac (Apple Silicon, macOS 14+)

```
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.13 git+https://github.com/fungimasters/Spuza
```

The uv installer puts `uv` in `~/.local/bin`, which is not on the PATH of the
Terminal window you ran it in. Open a new Terminal window before the second
line (or if `uv` is not found), then run `spuza`.

Update: `uv tool upgrade spuza`

## Windows

From a clone of the repository:

```
uv run spuza
uv run spuza <folder>
uv run spuza --demo <folder>
```

## Development

```
uv run --project . ruff check --select F src tests && uv run --project . python -m pytest -q
```

CI runs this on Linux, Windows and macOS and builds an unsigned `Spuza.app`
(see `packaging/`) as a workflow artifact.
