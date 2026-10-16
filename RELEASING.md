# Releasing Spuza

In order. Tags are never moved: a broken release gets a new patch version.

1. Bump `version` in `pyproject.toml`.
2. `uv lock`.
3. In `README.md`, add the version's line at the top of "What's new" and update the version badge
   (`img.shields.io/badge/version-<version>-...`).
4. Regenerate the screenshots: `uv run python docs/shots.py` (reads `docs/samples`, writes
   `docs/img`). Run it on the real Windows or macOS platform, not offscreen: offscreen renders
   every glyph as a box.
5. `uv run python docs/check_readme.py` must print "README matches the code". The same check runs
   in CI as `tests/test_check_readme.py::test_real_readme_passes`.
6. Full gate:
   `uv run --project . ruff check --select F src tests && uv run --project . python -m pytest -q`
7. Commit, then `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
8. CI (`.github/workflows/ci.yml`) runs the tests on Linux, Windows and macOS and builds the unsigned
   `Spuza.app`. On a `v*` tag, once those pass, it publishes the GitHub release with the wheel and
   `Spuza-macos.zip`. It does not build a Windows zip yet; when it does, it is attached the same way.
9. Verify: `uv tool upgrade spuza`, then `spuza --version` prints the new version. The release page
   lists the wheel and `Spuza-macos.zip`.
