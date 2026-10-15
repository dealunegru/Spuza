# Sending Spuza as a bundle

Until the GitHub repository exists, Spuza ships as one wheel file plus one install line.
Build the wheel here (Windows or Mac, any Python with uv):

```
uv build
```

That writes `dist/spuza-0.1.0-py3-none-any.whl` (about 40 KB; it is pure Python, so the same
file installs on Windows, Apple Silicon and Intel Macs; the heavy parts, PySide6 and rawpy,
are fetched from PyPI at install time).

## What the friend does on the Mac (Apple Silicon, current macOS)

1. Get the `.whl` file onto the Mac (AirDrop, mail, USB), for example into `~/Downloads`.
2. If `uv` is not installed yet, in Terminal:

   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   then open a new Terminal window.
3. Install Spuza as a tool:

   ```
   uv tool install --python 3.13 ~/Downloads/spuza-0.1.0-py3-none-any.whl
   ```

4. Run it: `spuza` (card in; Spuza scans `/Volumes/*` for `DCIM`), or `spuza /path/to/folder`.

Update: send a new wheel and run the same install line with `--force`.
Remove: `uv tool uninstall spuza`.

## Verified

- 2026-10-16, Windows: `uv tool install --python 3.13 dist/spuza-0.1.0-py3-none-any.whl` installed
  one executable; `spuza --demo <six test files> --bench` from the installed tool: 6 pictures,
  0 failed, step mean 21.6 ms under load, one background full decode on the X-Pro2 RAF.
- Not yet run on a Mac. First run there fetches PySide6 (about 100 MB) and rawpy.
