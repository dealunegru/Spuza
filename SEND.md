# Sending Spuza to the Mac

Three ways, in order of preference. All need Apple Silicon and a current macOS.

## 1. One install line (needs the GitHub repository to be public)

In Terminal:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

open a new Terminal window, then

```
uv tool install --python 3.13 git+https://github.com/fungimasters/Spuza
```

Run it: `spuza` (card in; Spuza scans `/Volumes/*` for `DCIM`), or `spuza /path/to/folder`.
Update: `uv tool upgrade spuza`. Remove: `uv tool uninstall spuza`.

## 2. The app

Every release at <https://github.com/fungimasters/Spuza/releases> carries `Spuza-macos.zip`
(about 43 MB), built unsigned by CI on a macOS runner. Unzip, drag `Spuza.app` to
Applications. The first launch is refused because the app is not signed: open
System Settings > Privacy & Security, scroll to the message about Spuza, click **Open Anyway**,
then launch again. Or, in Terminal: `xattr -dr com.apple.quarantine /Applications/Spuza.app`.
The app has no command line; it scans the mounted cards when it opens.

## 3. The wheel (works while the repository is private)

Build it here (Windows or Mac, any Python with uv):

```
uv build
```

That writes `dist/spuza-0.1.2-py3-none-any.whl` (about 40 KB, pure Python; the heavy parts,
PySide6 and rawpy, are fetched from PyPI at install time). Get the file onto the Mac
(AirDrop, mail, USB), install `uv` as in 1, then:

```
uv tool install --python 3.13 ~/Downloads/spuza-0.1.2-py3-none-any.whl
```

Update: send a new wheel and run the same line with `--force`.

## Verified

- 2026-10-16, Windows: the wheel installed as a uv tool; `spuza --demo <six test files> --bench`
  from the installed tool: 6 pictures, 0 failed, step mean 21.6 ms under load, one background
  full decode on the X-Pro2 RAF.
- 2026-10-16, Windows: the install line of 1 run against the (then private) repository with
  the owner's credentials installed spuza 0.1.0; the six test files ran with 0 failed,
  step mean 12.1 ms.
- 2026-10-16, CI: tests green on Linux, Windows and macOS 15; `Spuza.app` built on macOS 15
  (118 MB unpacked, Qt, libraw and libheif inside). Not yet launched on a Mac.
