<h1 align="center">Spuza</h1>

<p align="center">
  Photo culling for camera cards: step through a card, mark the keepers, copy them off.<br>
  One key per decision, and the next pictures are already decoded.
</p>

<p align="center">
  <img src="docs/img/hero.gif" alt="Spuza stepping through a card and marking pictures" width="900">
</p>

<p align="center">
  <a href="https://github.com/fungimasters/Spuza/actions/workflows/ci.yml"><img src="https://github.com/fungimasters/Spuza/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/fungimasters/Spuza/releases/latest"><img src="https://img.shields.io/github/v/release/fungimasters/Spuza?label=release" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/version-0.5.1-informational" alt="Version 0.5.1">
</p>

**Mac** (Apple Silicon), in Terminal:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```
then, in a new Terminal window:
```sh
uv tool install --python 3.13 git+https://github.com/fungimasters/Spuza
```

**Windows**, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
then, in a new PowerShell window:
```powershell
uv tool install --python 3.13 git+https://github.com/fungimasters/Spuza
```

Then run `spuza` with a card in.
Update with `uv tool upgrade spuza`. There is also a [Mac app](#mac-app) on the release page.

## What's new

- **0.5.1**: Spuza has an icon: a slide on a light table. It shows in the Dock, the Mac app, the Windows exe and taskbar, and the window itself.
- **0.5.0**: Second feedback round. With Move on mark on, unmarking moves on too. Full-bleed shows only the picture; the strip appears for a second when the mouse moves. Copying sorts into `raw`, `soc` and an empty `ed` folder, then ejects the card and quits, each an option in Preferences, all on by default. The copy log lives in Spuza's app-data folder, never in the destination.
- **0.4.1**: Nothing is left behind on copy: a HIF, XMP, THM or Live Photo MOV travels with its picture, and files that are not pictures are counted. Bursts are listed in the order the camera shot them. HIF, ARW, NEF, NRW, PEF, 3FR, CR2, ORF, RW2 and CR3 open. With two cards in, Spuza says so and `--card` picks one. Releases now include a Windows build.
- **0.4.0**: four features in advanced mode: Compare (`N`), Focus peaking (`P`), AF point (`O`), and Resume, with Start fresh (`U`).
- **0.3.3**: classic and advanced mode. Advanced mode is a switch in Preferences, and its first setting is the number of decode threads.
- **0.3.2**: 12 pictures are now decoded ahead and 8 kept behind (it was 8 and 3). A Preferences sheet sets those numbers, and `--ahead` and `--behind` set them for one run.
- **0.3.1**: the inspector shows aperture, shutter, ISO, focal length and camera. Right-click a picture to show it in Finder or Explorer. The status strip loses the clock and the "next N ready" label, and the filmstrip loses its green bars. Fixes a rare crash caused by cleanup running on the wrong thread.
- **0.3.0**: 15 fixes:
  - 16-bit greyscale files are no longer clipped.
  - `Z` gives a true 100% view on Retina screens, and the filmstrip is sharp there.
  - Several copy sheet fixes.
  - Copies go to `~/Pictures/Spuza` by default, never to the card.
- **0.2.0**: move on mark (`A`). Marking a picture shows the next one.
- **0.1.3**: a wheel and an unsigned Mac app on every release, and `spuza --version` prints the right version.

## Culling a card

Spuza is built for the first pass over a shoot: hundreds of frames and a handful of keepers.
When you run `spuza`, it looks for a card with a `DCIM` folder (`/Volumes` on a Mac, every drive except the system drive on Windows) and opens it. To open a folder instead, give its path: `spuza ~/Pictures/trip`. With two cards in (a camera with two slots, say), Spuza lists both, opens the first, and the status bar names the other and how many of its pictures are copies of this card's; `spuza --card 2` opens the second. Pictures are listed in the order the camera took them: by capture time, then by the camera's shot counter on Ricoh, Fujifilm and Nikon bodies, then by name; files without capture data follow, by file date.

<p align="center">
  <img src="docs/img/workbench.png" alt="The workbench: the picture, the filmstrip, the inspector and the marked count" width="900">
</p>

| Key | Does |
|---|---|
| `→` or `J` | next picture |
| `←` or `K` | previous picture |
| `Space` or `M` | mark or unmark the picture |
| `Ctrl+A` | **Mark all** (`Cmd+A` on a Mac). The toolbar button reads Mark all, and Unmark all while every picture is marked |
| `Ctrl+Shift+A` | **Unmark all** (`Cmd+Shift+A` on a Mac) |
| `Ctrl+Z` | undo the last Mark all or Unmark all (`Cmd+Z` on a Mac): one level, and nothing asks first |
| `[` or `]` | rotate the picture left or right by a quarter turn (in the review, every selected picture; in compare, the focused one) |
| `A` | move on mark, on or off: marking a picture then shows the next one (remembered between runs) |
| `Z` | 100% zoom; on a raw file the full decode runs in the background |
| `F` | full screen, picture only |
| `Esc` | leave full screen or the review |
| `R` | review the marked pictures |
| `C` | copy the marked pictures to a folder |
| `Cmd-,` (`Ctrl-,` on Windows) | Preferences |

With Move on mark (A) on, unmarking also moves on to the next picture.

Mark all and Unmark all say how many pictures changed in the status line ("Marked 312 (Ctrl+Z undoes)", "Unmarked 312 (Ctrl+Z undoes)") and never ask first. A stray Unmark all costs nothing: `Ctrl+Z` puts back exactly the marks you had before it; if you marked or unmarked pictures by hand in between, those changes are replaced and the status line says how many. Only the last Mark all or Unmark all can be undone, and the undo stays until the next Mark all or Unmark all, `U` (start fresh) or closing Spuza. The same button and keys work in the review.

Rotate (`[` and `]`) turns the picture on screen, the filmstrip and the review in quarter turns. Your original is never changed: the turn is written, as the EXIF orientation only, into the copy (see Copying and Preferences).
Full-bleed shows only the picture; move the mouse to see the strip for a second.

Every key also has a toolbar button with its shortcut printed on it.
The inspector on the right shows the file names and sizes, the preview's size and decode time, aperture, shutter, ISO, focal length and camera.
Right-click a picture in the filmstrip, the viewer or the review to show it in Finder or Explorer.

**Marks never touch your files.** Spuza never writes to a picture, its EXIF or the card. It writes only four things: the copies you ask for (and the `raw`, `soc` and `ed` folders they go in), a log of each copy in its own app-data folder (never in your destination), its own preferences, and, with Resume in advanced mode, a small session file in its app-data folder.

<p align="center">
  <img src="docs/img/bleed.png" alt="Full screen: the picture alone, with a one-line status strip" width="900">
</p>

## Review

`R` shows every marked picture on one contact sheet, so you can take a second look.

<p align="center">
  <img src="docs/img/review.png" alt="Review: the marked pictures on one contact sheet" width="900">
</p>

| Key | Does in the review |
|---|---|
| `↑` `↓` `←` `→` | move through the sheet |
| `Space` or `M` | unmark the selected picture |
| `Z` | 100% view of the selected picture (`Esc` returns to the sheet) |
| `Enter` | open the selected picture in the workbench |
| `Esc` | back to the card |

Unmarking takes a picture off the list. Spuza never deletes anything.

## Copying

`C` opens the copy sheet. It starts at `~/Pictures/Spuza`, and you can pick any other folder.
The sheet shows the plan before anything is copied: how many files, how many bytes, and what is already in that folder.

<p align="center">
  <img src="docs/img/copy.png" alt="The copy sheet: destination, the plan, and the Copy button" width="900">
</p>

- A raw file and a JPEG with the same name (`DSCF0412.RAF` and `DSCF0412.JPG`) are one picture. They get one mark, and both files are copied together. The same goes for a `HIF` beside a raw, and for any other file with the picture's name (an `XMP`, a `THM`, a Live Photo's `MOV`).
- If a file is already in the folder with identical content, it is skipped.
- If a different file has the same name, the copy gets a suffix (`DSCF0412-1.JPG`). Nothing in the destination is ever overwritten.
- With **Copy into raw / soc / ed folders** on (the default), the destination gets a `raw` folder for raw files and a `soc` folder for JPEGs and other picture formats (HIF, HEIF, PNG...). A raw+JPEG pair is split between them, and a picture's XMP or MOV goes with its raw (or with its JPEG when there is no raw). An empty `ed` folder is made too, for your Lightroom exports. The result line says how many files went to `raw` and to `soc`.
- After a successful copy, **Eject the card after copying** and **Quit Spuza after copying** (both on by default) eject the card you opened and then close Spuza, after a 3 second countdown with a **Stay** button. A failed eject is reported and does not stop the quit. If anything failed or you cancelled, nothing is ejected and Spuza stays open.
- With **Write rotation to copies (EXIF)** on (the default), a picture you turned with `[` or `]` is copied with its EXIF orientation set to match; only the copy is changed, never the original on the card. Turn it off and the copies keep the camera's orientation.
- Each copy writes a log, `spuza-copy-<date>-<time>.log`, with every file copied, skipped or renamed, and the totals. The log is kept in Spuza's own app-data folder (`logs`), never in the destination, which receives only your pictures and the three folders.

## Preferences

`Cmd-,` (`Ctrl-,` on Windows) or the toolbar button opens Preferences:

- **Pictures ready ahead** (1-64, default 12) and **Pictures kept behind** (0-64, default 8). The memory those previews take is shown under the rows.
- **Copy into raw / soc / ed folders**, **Eject the card after copying**, **Quit Spuza after copying** and **Write rotation to copies (EXIF)** (all default on): see Copying.
- **Zoom shows the full sensor on cropped shots** (default off). Some cameras can crop a shot as they take it. With the row off, `Z` on such a shot shows the camera's own frame at 100%. With it on, `Z` shows the whole sensor at 100% instead, so you see what was outside the crop, and the label says `100% · full sensor`. The fitted view, the review sheet and compare always show the camera's frame; shots that were not cropped and JPEGs are the same either way.
- **Advanced mode**: see below.
- Advanced mode only:
  - **Decode threads** (1-8, default 3, applied at the next launch).
  - **Peaking threshold** (1-255, default 60).
  - **Remember marks between runs** (default on).
- **Reset to defaults**.

<p align="center">
  <img src="docs/img/prefs.png" alt="Preferences in advanced mode" width="600">
</p>

## Classic and advanced mode

Spuza opens in classic mode, which has every feature that has shipped. Advanced mode adds the features that are still being tried out.

- To switch for good, tick "Advanced mode" in Preferences. The setting is saved, and the mode changes at once; decode threads follow at the next launch.
- To switch for one run, use `spuza --advanced` or `spuza --classic`. The saved preference is not touched.

In advanced mode the window title reads `Spuza (advanced)` and the status strip shows an `ADVANCED` tag.
A feature that proves itself moves to classic. How flags are added and promoted is in [docs/FEATURE_FLAGS.md](docs/FEATURE_FLAGS.md).

| Key | Does (advanced mode) | Flag |
|---|---|---|
| `N` | **Compare.** Shows this picture and the next side by side; `N` again adds the next one, up to four. | `compare_view` |
| `P` | **Focus peaking.** Paints the sharp edges red on the big picture and on each compare cell. Remembered between runs. | `focus_peaking` |
| `O` | **AF point.** Draws a ring where the camera focused, and `Z` then opens the 100% view on that spot. | `focus_point` |
| `U` | **Start fresh.** Clears this card's marks and its saved session. `U` again within 10 seconds undoes it. | `resume_session` |

<p align="center">
  <img src="docs/img/compare.png" alt="Compare: two pictures side by side" width="900">
</p>

**Compare** in detail:
- `←` and `→` (or `J` and `K`) move the frame between the pictures, and `Space` marks the one with the frame.
- `Z` zooms the framed picture and its neighbour to 100% together, and dragging pans both.
- `Esc` returns to the picture that had the frame.
- From the review, `Ctrl`-click (`Cmd`-click on a Mac) two to four marked pictures, then press `N`.

<p align="center">
  <img src="docs/img/peaking.png" alt="Focus peaking: sharp edges painted red" width="900">
</p>

**AF point** works with Fujifilm RAF files from the X-T4, X-T5, X100V and X-T50. Other files have no ring.

**Resume** puts back each card's marks and your place the next time you open the same card, and a banner says what it restored. The session file is in Spuza's app-data folder, never on the card. Sessions older than 30 days are deleted at start. To turn Resume off, untick "Remember marks between runs".

**Decode threads** (`decode_threads`) sets how many pictures are decoded at once, from 1 to 8. Classic always uses 3.

## Formats

| Family | Files | Shown as |
|---|---|---|
| Raw | Fujifilm `RAF`, `DNG` (cameras and phones), Sony `ARW`, Nikon `NEF`, `NRW`, Pentax `PEF`, Hasselblad `3FR`, Canon `CR2` | the full-size JPEG preview embedded in the raw file |
| Raw, via LibRaw's thumbnail | Olympus/OM `ORF`, Panasonic `RW2`, Canon `CR3` | the preview LibRaw extracts (on some Panasonic bodies a 1920 px one) |
| JPEG | `JPG`, `JPEG` | as is |
| HEIF | `HIF` (Fujifilm, Canon), `HEIC`, `HEIF` | as is; a `HIF` beside a raw of the same name is one picture with it, like a JPEG |
| Other | `PNG`, `GIF`, `BMP`, `TIF`, `TIFF` | as is |

Any other file with the same name as a picture (an `XMP`, a `THM`, the `MOV` of a Live Photo) is copied with that picture. Files that are not pictures and belong to none are not copied; the copy sheet counts them and lists them in its tooltip, and it counts files it could not read. Hidden files, system folders and symlinks are skipped without a note.

## Speed

- **Raw files are not demosaiced while you step.** Each raw file carries a JPEG preview made by the camera. Spuza reads it straight out of the file and shows that. The full decode runs only when you press `Z`, and only if the preview is smaller than the sensor.
- **Prefetch.** The pictures ahead of and behind the current one are decoded on worker threads, so a step only swaps in an image that is already decoded.

Measured on Windows with 0.4.0, using `spuza --demo <folder> --bench`. The test card held five Ricoh GR III DNGs (28.5-28.9 MB each) and one Fujifilm X-Pro2 RAF (23 MB):

| | Measured |
|---|---|
| First picture on screen | 839-1125 ms over two runs |
| Step, mean | 5.7 ms |
| `Z` full decode of the X-Pro2 RAF (0.3.1) | 3.4-3.5 s, in the background |
| Memory after 300 steps, default window of 12 ahead and 8 behind (0.3.2, a different card: 1000 JPEGs of 3000x2000) | 229-259 MB |

These numbers have not been measured on a Mac yet. To set the prefetch window for one run, use `--ahead N --behind N`.

## Mac app

Every [release](https://github.com/fungimasters/Spuza/releases/latest) also has `Spuza-macos.zip`. It holds a self-contained `Spuza.app` that CI builds on a macOS runner. Unzip it and drag `Spuza.app` to Applications.

For Windows, the same release has `Spuza-windows.zip`: unzip it and run `Spuza.exe` from the folder (SmartScreen asks once: More info, then Run anyway). `spuza-cli.exe` beside it is the same app with a console, for `--version` and `--bench`.

The app is not code-signed, so macOS blocks the first launch. To allow it:

1. Open **System Settings > Privacy & Security**.
2. Scroll to the message about Spuza and click **Open Anyway**.

You only need to do this once. The Terminal alternative is:

```sh
xattr -dr com.apple.quarantine /Applications/Spuza.app
```

## Command line

| | |
|---|---|
| `spuza` | open the first camera card found |
| `spuza <folder>` | open a folder |
| `--advanced`, `--classic` | the mode for this run only |
| `--ahead N`, `--behind N` | the prefetch window for this run only |
| `--card N`, `--card <path>` | with two or more cards mounted, open this one (Spuza lists them and opens the first by default) |
| `--demo <folder> --bench` | open a folder, step through it scripted, print timings, exit |
| `--version` | print the version |
| `uv tool upgrade spuza` | update |
| `uv tool uninstall spuza` | remove |

Why uv and not an installer? Spuza is a Python application. `uv tool install` gives it its own Python and its own dependencies, and it puts one `spuza` command on your PATH. Updating or removing it is clean, it never touches the system Python, and it needs no admin rights.

## Development

```sh
git clone https://github.com/fungimasters/Spuza
cd Spuza
uv sync
uv run spuza --demo docs/samples
```

To run the gate from the repository root:

```sh
uv run --project . ruff check --select F src tests && uv run --project . python -m pytest -q
```

- **CI** runs the gate on Linux, Windows and macOS 15, and it builds the unsigned `Spuza.app` and a Windows build, `Spuza-windows.zip`.
- **Releases.** Pushing a tag `vX.Y.Z` publishes a GitHub release. The steps are in [RELEASING.md](RELEASING.md).
- **README check.** `docs/check_readme.py` runs as part of the test suite. It fails if this README misses a key, flag, command-line option, file format or the current version.
- **Images.** `uv run python docs/shots.py` makes every image in this README. It drives the real window over `docs/samples`.

Spuza is built with [PySide6](https://doc.qt.io/qtforpython-6/), [rawpy](https://github.com/letmaik/rawpy), [Pillow](https://python-pillow.org/) and [pillow-heif](https://github.com/bigcat88/pillow_heif). It needs Python 3.12 or newer; CI tests 3.13.

## License

[MIT](LICENSE)
