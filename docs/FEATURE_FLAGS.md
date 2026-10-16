# Classic and advanced mode

Spuza opens in **classic mode**: everything it has shipped, nothing new. **Advanced mode** is the same
app plus the features still being tried out, each behind a flag in `src/spuza/features.py`. A tester
runs advanced; a feature that earns its place is promoted to classic.

## Switching

- Preferences, checkbox "Advanced mode": saved. Ticking or unticking it switches the mode and the
  marker at once; a feature that is set up at startup (decode threads) follows at the next launch.
- `spuza --advanced` or `spuza --classic`: this run only. The saved preference is not touched, and
  `--classic` wins over a saved advanced mode. Giving both is an error.

Order: the command-line flag, else the saved preference, else classic.

## The marker

Advanced mode is never silent: the window title reads `Spuza (advanced) — <folder>` and the status
strip carries an `ADVANCED` tag. Classic shows neither.

## Adding a flag

Add an entry to `FLAGS` in `src/spuza/features.py` with `classic=False` and a one-line description:

```python
Flag("my_feature", "One line saying what it does in advanced mode.", classic=False),
```

Gate the UI or behaviour with `if features.enabled("my_feature")`. An unknown name raises `KeyError`,
so a typo fails loudly. Test it in both modes (`features.set_mode("advanced")`; the test suite resets
to classic around every test).

## Promoting a flag to classic

1. Flip it to `classic=True` in `FLAGS` (the `if features.enabled(...)` calls keep working).
2. Edit `CLASSIC_SET` in `tests/test_features.py` to include the name. That test pins the shipped
   classic set, so promotion is always a deliberate edit.
3. Note it in the README.

## Current flags

| Flag | In classic | What it does |
|---|---|---|
| `decode_threads` | no | Decode threads (1-8) in Preferences, saved as `prefetch/threads`, applied at the next launch. Classic always decodes on 3 threads. |
| `compare_view` | no | `N` compares up to four near-twins side by side (this picture and the next; in review, the grid's selection), zoom and pan locked together. Adds the Compare button. |
| `focus_peaking` | no | `P` paints the sharp edges in red, computed off the GUI thread at half size; the on/off state is saved as `peaking/on`, the strength as `peaking/threshold` (Preferences, 1-255, default 60). Adds the Peaking button. |
| `focus_point` | no | `Z` lands on the camera's AF point and `O` shows it as a ring (saved as `focus/marker`). Fujifilm RAFs from the X-T4, X-T5, X100V and X-T50 only (the allowlist in `src/spuza/focus.py`); any other file or model has no point. Adds the AF point button. |
| `resume_session` | no | Marks and place survive quitting, kept in Spuza's app-data folder (never on the card); `U` starts fresh (again within 10 s to undo). Preferences: "Remember marks between runs" (`resume/enabled`, default on). Sessions older than 30 days are deleted at start. |
