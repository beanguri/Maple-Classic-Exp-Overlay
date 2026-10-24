# Code layout

`maple_exp.py` is the file you run. The program is the `maple_overlay/` package, split by what each part does.
Parts only depend on parts above them in this list (there are no cycles):

| Folder / file | What is in it |
|---|---|
| `config.py` | The files and folders, the settings (`load_config`, `save_config`), the numbers the parts share (times, limits, defaults), the error log. `VERSION` is here. |
| `platforms/` | What differs between Windows and macOS: finding the game window, screen scaling, hotkeys, the one-copy-at-a-time lock. |
| `core/` | The numbers, with no screen and no window: `exp_tracker` (EXP/hr, last 5 minutes, time to level), `meso_tracker`, `session`, `levels` (the EXP table), `session_stats` (Solo / Party and the CSV log). |
| `vision/` | Reading the game's screen: `exp_bar` (find the EXP bar, read its digits), `job`, `maps` (the minimap's name), `mesos`, `party`, `potions`, `exp_messages` (the message lines, without OCR), `ocr` and `engine` (Tesseract, the fallback), `screen_reader` (looks at the game window and puts it all together). |
| `logs/` | `autolog` (the EXP log: 5-minute stretches into one line per day, map, level, class and party size), `entries` (a line's text and the table's rows), `log_details` (the Details window's numbers), `report` (bug reports). |
| `ui/` | `app` (the windows and what the buttons do), `main_view` (the tracker's drawing), `gfx` and `kit` (drawing helpers, colours), `format` (numbers and times as shown), `region_selector`. |
| `updates.py` | Looking for a newer release, and sharing a run (only when Settings says so). |

Files that come with the program are in `assets/`; the tests are in `tests/`; the stats site is in `server/`.

## Window zoom
Dragging the window's edge zooms everything in it together (text, buttons, pictures, the log, the Solo / Party panel).
`App._zoom_to_window` works out the zoom from how much the window grew or shrank against the size it had when its
layout last settled (`_take_ref`), redraws at the new zoom (`_apply_ui_scale`) and saves it as `zoom` in the settings
(0.6 to 3.0). Settings > Size multiplies with it. `tests/test_ui_size.py` drives the real window.

## Tests
`tests/appmod.py` presents the package as one namespace (`import appmod as m`), so a test can read `m.AutoLog` and swap
`m.CONFIG_PATH` or `m.TESS` for one check, wherever the program keeps them. Run any of them from the project folder:
`py -3 tests/test_logic.py`, `test_screen.py`, `test_ocr.py`, `test_report.py`, `test_ui.py`, `test_ui_size.py`,
`test_session_stats.py`, `test_exp_messages.py`, `test_log_details.py`, `test_log_extras.py`.
