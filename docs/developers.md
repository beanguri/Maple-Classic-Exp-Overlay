# For developers

**Run from source.** You need Python 3.10 or newer, and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) for map names.

```
py -3 -m pip install -r requirements.txt
run.bat
```

**From source on macOS:** `brew install tesseract`, then `python3 -m pip install -r requirements.txt` and `python3 maple_exp.py`. Allow your Terminal (or Python) in Screen Recording instead of the app.

**From source on Linux:** install Tk and Tesseract (with English data) from your distro, then `./run.sh`; it makes a `.venv` with `requirements.txt` the first time (this adds `python-xlib`). It runs Tk through X11 (XWayland on a Wayland desktop) so the overlay and the game, a Wine/Proton X11 window, share the same coordinates. The game is pictured from its own window, so nothing on top of it gets in the way, and on Wayland a picture of the whole screen would be black anyway. Run the tests with `.venv/bin/python tests/test_logic.py` and so on.

Everything that differs between Windows, macOS and Linux (finding the game window, screen scaling, hotkeys, the one-copy-at-a-time lock) is in `platforms/`: one `Platform` interface in `base.py`, one implementation per OS, and `create_platform()` picks the one for the OS it's on.

**Build the download** (puts `Maple-Classic-Exp-Overlay-v<version>.zip` in `release/`, and a copy to use right away in the project folder: `Maple Classic Exp Overlay.exe` with its `_internal` folder):

```
py -3 -m pip install -r requirements.txt pyinstaller
py -3 build.py
```

**The Mac download** is built by `build_macos.py` on a Mac (Apple Silicon or Intel, for the Mac it runs on); how to use it is at the top of that file. `check_macos.py` checks the result.

**Testing never shares anything:** with `MAPLE_EXP_DATA` set (it moves the data folder, which is how the tests keep away from your real log), the overlay refuses to send runs to the real site, so made-up test runs can't end up there. (Set `MAPLE_EXP_ALLOW_SHARE=1` only to test against a local copy of the server.)

**Run the tests:**

```
py -3 tests/test_logic.py
py -3 tests/test_screen.py
py -3 tests/test_ui.py     # opens the real window for a few seconds (the notes box)
```

To build with exactly the packages the releases use: `py -3 -m pip install --require-hashes -r requirements-build.txt`.

GitHub runs the logic tests on every push ([Tests workflow](../.github/workflows/tests.yml)). The [Build workflow](../.github/workflows/build.yml) builds the download on GitHub (Actions tab, **Build**, **Run workflow**), runs all the tests first, and signs the .exe once code signing is set up. Pushing a version tag also attests the zip and drafts the release with it; releases are only ever made that way.

**The stats site** is in `server/`: a Cloudflare Pages site with a D1 database (free tier). The page is `server/public/`, the API is `server/src/index.js`. `npm --prefix server install`, then `npm --prefix server run dev` runs it locally, `py -3 server/test_server.py http://localhost:8787` tests it, and `npm --prefix server run deploy` puts it online.

**How it reads the screen:**
- It finds the `MapleStory.exe` window, then the EXP numbers (like `12,345 [12.34%]`) in the status bar.
- Each digit is resized to a standard size and compared against saved examples of the game's font in `assets/`.
- A reading only counts if the EXP and % agree with how much EXP that level needs, so misreads are ignored.
- Mesos are read the same way. The mesos box is found by its outline, which also works while the inventory is see-through.
- The potion counts, your level (the orange **LV** badge) and the EXP number are read with the game's own font, drawn from `assets/fonts_v2/`; the party size by the exact shape of the party list's HP bars (`assets/party_shapes.json`). The older EXP reader and party count stay as a backup. Mesos are still read the older way; a newer mesos reader runs in a test mode that only compares and never changes a number you see; the daily health report counts how often the readers agree.
- The party size is the number of HP bars in the party list.
- Map names are read with Tesseract.
- Settings, the log and `errors.log` are saved in `%APPDATA%\MapleEXP` (`~/Library/Application Support/MapleEXP` on macOS, `~/.local/share/MapleEXP` on Linux; from its old name, Maple EXP/hr, so updates keep them).

## Forking or changing this code

[MIT](../LICENSE) lets you change and share this code. One request: **a changed copy must not send anything to mcwo.app.** The site takes shared runs, daily health reports and bug reports only from official releases, so it turns down versions it doesn't know. Numbers from a changed copy would only add noise to the Best maps and the health numbers.

To turn sending off, or point it at your own server, in `maple_exp.py`:

- `STATS_SITE` is the website's address. Change it to your own server.
- `post_runs`, `post_health` and `post_report` are the three functions that send to it. Make them do nothing (or raise) to stop sending.
- `check_for_update` asks GitHub for the newest release of this project. Point `GITHUB_REPO` at your own, or make it do nothing.

The checks are only about the version number: a copy that keeps a real release's version number can't be told apart, so please don't do that.
