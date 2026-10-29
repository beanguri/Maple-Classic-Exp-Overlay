"""
Maple Classic Exp Overlay: EXP/hr, mesos/hr and time to level for MapleStory Classic World.

Finds the MapleStory window, locates the "EXP. 12,345 [12.34%]" text in its status bar at
any window size, reads the digits by matching them against examples of the game's font
(Tesseract OCR as a fallback), and calculates EXP per hour, %/hr, and time to next level.

While tracking it also reads the map name from the minimap, your mesos from the inventory
and your party size from the party list, and keeps a log of EXP/hr and mesos/hr per day,
map, level and party size.

It only looks at the screen: it never reads or changes the game's files or memory and
never sends input to the game. Your settings and log are kept in the MapleEXP folder
inside %APPDATA% on Windows, ~/Library/Application Support on macOS (named after its old
name, Maple EXP/hr, so updating keeps them). It runs on Windows and macOS: what differs
between them is in platforms/.
"""
import contextlib
import hashlib
import hmac
import json
import math
import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")        # (before numpy is imported)
import platform
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter, deque, namedtuple
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog, ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

from capture import ScreenCapture, as_capture, grab  # noqa: F401
import digits_v2
import exp_font
import healthlog
import level_ocr
import notices
import ocr
import potions
import report
import shadow
from platforms import create_platform, hotkey_text, parse_hotkey
from report import Health, Recent
from ui_gfx import INK, TEXT_DIM
from ui_kit import (ACCENT, ACCENT_SOFT, BAD, BG, DIM, FAINT, FG, GOOD, LINE, LIST_BG, LIST_BG2, LIST_PART,  # noqa: F401
                    ON_ACCENT, RAISED, RAISED_HI, SELECT, SURFACE, WARN, setup_ttk)
from ui_main import SHAPES, Button, MainView, PopupMenu, Slider, Theme, Toggle

# Everything that differs between Windows and macOS (finding the game window, screen scaling,
# hotkeys...) is behind this one object: see platforms/.
PLATFORM = create_platform()
PLATFORM.setup_process()
cv2.setNumThreads(1)

VERSION = "0.9.14-beta"
WINDOW_TITLE = "Maple Classic Exp Overlay"    # the version is shown in Settings
OLD_TITLES = ("Maple EXP/hr",)               # what versions before 0.9.3 were called

# Files that come with the program (inside the .exe when packaged), the folder it runs from,
# and the folder for your settings, log and learned digits (MAPLE_EXP_DATA can override it).
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = APP_DIR / "assets"
HOME_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MAPLE_EXP_DATA") or PLATFORM.data_root() / "MapleEXP")

CONFIG_PATH = DATA_DIR / "config.json"
ERROR_LOG_PATH = DATA_DIR / "errors.log"
HEALTH_PATH = DATA_DIR / "health.json"        # the day-by-day counts behind the health reports (healthlog.py)
TEMPLATE_PATH = ASSETS / "digits.npz"
ICON_PATH = ASSETS / "icon.ico"
POLL_SECONDS = 1.5          # how often to read the EXP bar
WORKER_RETRY_SECONDS = 5       # if the reading thread can't open the screen: how long to wait before trying again
ROLLING_WINDOW = 300        # seconds used for the "last 5 min" rate
MESO_GAP = 45               # seconds without a mesos look after which a rise is believed, however big
MESOS_BOXES_MAX = 8         # window sizes whose mesos box is remembered in the config
SEARCH_STRIP = 0.22         # bottom part of the game window searched for the EXP text
LOG_PATH = DATA_DIR / "exp_log.json"
LOG_TEXT_PATH = DATA_DIR / "exp_log.txt"
LOG_BLOCK = 300             # seconds of non-stop EXP gain behind each log update
MESOS_PATH = DATA_DIR / "mesos_digits.npz"             # learned as you play...
MESOS_START_PATH = ASSETS / "mesos_digits.npz"          # ...starting from these
MESOS_BOX_PATH = ASSETS / "mesos_box.npz"
MAPS_PATH = ASSETS / "maps.txt"            # the game's map names (see server/tools/make_maps.py)
MAP_HIDDEN_SECONDS = 3      # the minimap's name unseen this long: say so, and hold back saving to the log
MAP_BLIND_SECONDS = 45      # the minimap's name box unseen this long: the map may have changed unnoticed
SCAN_SECONDS = 4            # how often the whole window is checked for the inventory and party
WINDOW_SCAN_SECONDS = 1     # ...and while a shop, storage or trade window is open (mesos move there)
WINDOW_GRACE = 4            # seconds a closed window still counts, so its last change is read
WINDOW_NAMES = {"shop": "NPC shop", "storage": "Storage", "trade": "Trade"}
POTION_SECONDS = 3          # how often the potion bar is read (a small picture, no need for more)
POTION_FULL_LOOK = 15       # every this many reads the bar is looked for over its whole search range again
NOTICE_SECONDS = 2          # how often the right-side gain notices are OCR'd (etc value + notice mesos)
OUTLINE_SECONDS = 8         # how often the (costly) search for a see-through mesos box may run
LEVEL_RECHECK_SECONDS = 30  # a level read from the badge is looked at again this often (it only changes at a level-up)
V2_SPOTS_MAX = 8            # window sizes whose reader v2 spot is remembered in the config
PART_LOG_SECONDS = 600      # a reading part that keeps failing is written to errors.log this often at most
PART_NOTE_SECONDS = 120     # ...and its status note stays this long after its last failure
PART_NOTES = {              # what the status line says while a part of the reading fails
    "map name": "Map names can't be read right now",
    "mesos": "Mesos can't be read right now",
    "notices": "Gain notices can't be read right now",
    "potions": "The potion bar can't be read right now",
    "window scan": "The party list and shop windows can't be checked right now",
    "class": "The class can't be read right now",
    "name print": "Telling characters apart isn't working right now",
    "level": "The level badge can't be read right now",
    "Tesseract": "The backup number reader (Tesseract) isn't working right now",
}
TRIED_PARTS = {"map name": "map", "class": "class", "mesos": "mesos", "potions": "potions"}   # (health reports count these)
STALE_LEVEL_SECONDS = 6          # after another character is found, a reading with the old level is still waiting this long
PCT_WAIT_NOTES = ("percentage wrapped", "level up?", "level changed", "level ")   # what a %-only bar says while it waits
LEVEL_FAILS_FOR_FALLBACK = 3    # badge reads that failed in a row before typing the level in is offered
DEFAULTS = {"gap_seconds": 15, "transparency": 0, "topmost": True,
            "check_updates": True, "skip_version": "", "ui_scale": 100, "compact": False, "hotkeys_on": True,
            "details": False, "compact_shape": "square", "seen_version": "",
            "potions_on": True, "potion_prices": {}, "shadow_reader": True,
            "read_status": True,                # the EXP/Mesos/Map/Potions readability strip
            "meso_mode": "notices",             # "notices" = battle/loot lines; "inventory" = mesos box
            "job_override": ""}                 # typed class when OCR can't read the status bar
MESO_FRESH_SECONDS = 20     # a mesos look this recent counts as live
MESO_STALE_SECONDS = 60     # older than this: ask to open the inventory again
MESO_MODES = ("inventory", "notices")
MESO_MODE_LABELS = {"inventory": "Inventory", "notices": "Gain notices"}
SHAPE_LABELS = {"square": "Square", "wide": "Wide", "bar": "Bar"}
SHAPE_BLURBS = {"square": "The narrow card: the big number, the level bar and Start/Pause.",
                "wide": "Longer sideways: the level bar on top and four numbers in a row.",
                "bar": "One thin strip with the main numbers and a slim level bar."}
# What's new, shown once after updating to a version (plain words, newest changes only). A version with no entry shows nothing.
WHATS_NEW = {"0.9.13-beta": (
    (None, None, (
        ("head", "Fewer reading problems"),
        "Reading keeps going when the map name or class can't be read, and a stuck text reader no longer freezes the display.",
        "High levels (a bar with only a %): leveling up, run counts, a covered level badge and Reset work properly now.",
        ("head", "Health reports (your choice)"),
        "Once a day the overlay can send numbers about how well it reads your screen: never pictures, names, chat or your log. "
        "It asks you first, and you can turn it off in Settings.",
        ("head", "Send a report without GitHub"),
        "Report a bug, then Send to the developer. You see exactly what is sent before it goes.",
        ("head", "A new reader, tested quietly"),
        "A new way to read the EXP bar runs in the background and only notes whether it agrees. It changes nothing you see.")),),
    "0.9.12-beta": (
    (None, None, (
        ("head", "Hotfix for high levels"),
        "Past about Lv 57 (levels that need a million EXP or more), the game shows only a percentage on the EXP bar, with no EXP number. The overlay couldn't find the bar then. It does now.",
        "Your level is read from the orange LV badge, and EXP is worked out from it and the percentage. So EXP/hr there is an estimate, and it shows a \u2248 in front.",
        "If the badge can't be read, it uses your level from before, or you can type it: Settings, Level fallback.",
        "Estimates stay on your PC. They are never shared with the best-maps website.")),),
    "0.9.11-beta": (
    # (title, subtitle, items) pages; an item is a line, ("head", text), ("dot", text) or ("pic", file in assets/)
    (None, None, (
        ("head", "Net mesos/hr: what you really make"),
        "The overlay now counts the potions and food you drink while you farm, prices them, and takes that off your "
        "mesos/hr. That's your net mesos/hr.",
        "It only counts potions on your quick-slot bar (bottom right of the game). See how on the next page.",
        ("head", "Mesos/hr you can trust"),
        "Mesos you spend or move at an NPC shop, storage or a trade are left out. Only mesos you pick up count.",
        "The inventory doesn't have to stay open: open it when you start, and now and then.",
        ("head", "The best-maps website"),
        "Only clean 15-minute runs are shared: no shop, storage or trade during the run. Open the inventory when a "
        "run starts and right after it ends, so its mesos count too.")),
    ("How potion cost works", "What it counts, and how.", (
        ("pic", "whatsnew_potion_bar.png"),
        "Put the potions you use on the quick-slot bar, bottom right, so their counts show. The overlay reads that "
        "number every 3 seconds: when it goes down, that's a use. Buying or looting more is never a use.",
        "It counts only while tracking is on and EXP is coming in. Paused or in town, nothing counts.",
        ("pic", "whatsnew_net.png"),
        "Prices are the NPC shop prices. Potions no NPC sells (Mana Elixir, Power Elixir, Sap of Ancient Tree...) count "
        "as free until you type what you pay: Settings, Potions, Prices.",
        "You see it in the Net / hr tile, in Details, in your log, and as \"net\" in the compact shapes. Potion data "
        "stays on your PC: it is never sent anywhere.")))
    ,
    "0.9.10-beta": (
    "Compact mode now comes in three shapes: Square, Wide and Bar. Pick one in Settings, under Compact shape.",
    "Map names are matched to the game's own list, so one map is one line in your log.",
    "If the minimap's name is covered, it tells you after a few seconds, keeps the log clean, and pauses if it stays hidden.",
    "Towns are no longer logged.",
    "The best-maps website moved to mcwo.app, with a new look and an Include new players box.")}
PICTURE_WIDTHS = (430, 340, 250, 0)    # the What's new pictures' widths, biggest first (0: none), until the page fits the screen
# Settings > Size, in %: only these, so the window can't get too small to read or use.
UI_SCALES = (60, 70, 80, 90, 100, 110, 125, 150, 175, 200)
MAX_TRANSPARENCY = 40       # %: more and the window can fade out of sight, with Settings in it
STATS_SITE = "https://mcwo.app"       # shared 15-minute runs (server/)
GITHUB_REPO = "beanguri/Maple-Classic-Exp-Overlay"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# EXP needed to finish each level in MapleStory Classic World (index 0 = level 1),
# from meowdb.com/msclassic/guides/exp-table-level-1-to-100.
EXP_TABLE = [
    15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716,
    2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680, 20216,
    24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700,
    108480, 122760, 138666, 155540, 174216, 194832, 216600, 240500, 266682, 294216,
    324240, 356916, 391160, 428280, 468450, 510420, 555680, 604416, 655200, 709716,
    748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536, 1147132, 1209994,
    1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750, 2062925,
    2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
    3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059,
    25069923, 52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548,
]

tesseract_ok = False        # set in main(); Tesseract is only a fallback
TESS = None                 # the ocr.Tesseract to read text with, once main() has found one
tesseract_state = "missing"  # "ok", "missing" (no program found) or "no_eng" (a program, but no usable English file)
tesseract_why = ""          # for "no_eng": "file" (eng.traineddata isn't there) or "path" (the folder's name can't be passed)
tesseract_where = (None, None)   # the program and data folder in use (or the best one found), for bug reports


# --------------------------------------------------------------------------- files

_error_lock = threading.Lock()
_last_errors = {}


def log_error(what, exc=None):
    """Adds an error, with details, to errors.log in the data folder (the same kind at most
    once a minute), so players can send it in when something goes wrong. The kind is what was being
    done and the exception's type, not its message: a message with a number or a path in it would
    otherwise be written at every look and remembered for good."""
    key = (re.sub(r"\d+", "#", what), type(exc).__name__ if exc is not None else None)   # (a count in `what` is the same kind)
    with _error_lock:
        now = time.monotonic()
        if now - _last_errors.get(key, -1e9) < 60:
            return
        _last_errors[key] = now
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if ERROR_LOG_PATH.exists() and ERROR_LOG_PATH.stat().st_size > 1_000_000:
            os.replace(ERROR_LOG_PATH, ERROR_LOG_PATH.with_suffix(".old.log"))
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] v{VERSION}: {what}\n{details}\n")
    except Exception:
        pass


def good_v2_spots(v):
    """exp_v2_spots in the config: {"WxH/gh": [x0, y0, x1, y1]}, a few at most."""
    return (isinstance(v, dict) and len(v) <= V2_SPOTS_MAX
            and all(isinstance(k, str) and re.fullmatch(r"\d{3,5}x\d{3,5}/\d{1,2}", k) and isinstance(b, list)
                    and len(b) == 4 and all(type(x) is int for x in b) for k, b in v.items()))


def good_mesos_boxes(v):
    """mesos_boxes in the config: {"WxH": [x, y, w, h]}, a few at most."""
    return (isinstance(v, dict) and len(v) <= MESOS_BOXES_MAX
            and all(isinstance(k, str) and re.fullmatch(r"\d{3,5}x\d{3,5}", k) and isinstance(b, list)
                    and len(b) == 4 and all(type(x) is int for x in b) for k, b in v.items()))


def scale_box(box, old_size, new_size):
    """(x, y, w, h) moved from one game-window size to another, or None if it can't be scaled."""
    if not box or not old_size or not new_size:
        return None
    ow, oh = old_size
    nw, nh = new_size
    if ow <= 0 or oh <= 0 or nw <= 0 or nh <= 0:
        return None
    sx, sy = nw / ow, nh / oh
    x, y, w, h = box
    return (round(x * sx), round(y * sy), max(1, round(w * sx)), max(1, round(h * sy)))


def fmt_ago(seconds):
    """How long ago a look was, for the UI: 'just now', '12s ago', '3m ago'."""
    if seconds is None or seconds < 0:
        return None
    if seconds < 3:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    return f"{int(seconds / 3600)}h ago"


def feature_read_status(exp, mesos, map_name, potions, now):
    """How well each feature can be read right now, for the readability strip.

    Each argument is a small dict (see App._feature_reads). Returns
    [(label, state, tip), ...] with state in ok / stale / bad / wait / off.
    """
    rows = []
    # EXP
    if exp.get("covered"):
        rows.append(("EXP", "bad", "This window is covering the EXP bar."))
    elif exp.get("ok"):
        rows.append(("EXP", "ok", "EXP bar is being read."))
    elif exp.get("looking"):
        rows.append(("EXP", "wait", "Looking for the EXP bar."))
    else:
        rows.append(("EXP", "bad", "Can't read the EXP bar clearly."))
    # Mesos (inventory box, or right-side gain notices)
    seen = mesos.get("seen_at")
    age = None if seen is None else now - seen
    if mesos.get("mode") == "notices":
        if mesos.get("covered"):
            rows.append(("Mesos", "bad", "This window is covering the gain notices."))
        elif seen is None:
            rows.append(("Mesos", "wait", "Waiting for gain notices (meso pickups and drops)."))
        elif age is not None and age <= MESO_FRESH_SECONDS:
            rows.append(("Mesos", "ok", f"Notices looked at {fmt_ago(age)}."))
        else:
            rows.append(("Mesos", "stale",
                         f"Last notice look was {fmt_ago(age) or 'a while'} ago."))
    elif mesos.get("covered"):
        rows.append(("Mesos", "bad", "This window is covering the inventory's mesos."))
    elif mesos.get("value") is None:
        rows.append(("Mesos", "wait", "Open the inventory to start mesos tracking."))
    elif age is not None and age <= MESO_FRESH_SECONDS:
        rows.append(("Mesos", "ok", f"Inventory looked at {fmt_ago(age)}."))
    elif age is not None and age <= MESO_STALE_SECONDS:
        rows.append(("Mesos", "stale",
                     f"Holding last mesos ({fmt_ago(age)}). Open inventory to update."))
    else:
        rows.append(("Mesos", "stale",
                     f"Last inventory look was {fmt_ago(age) or 'a while'} ago. Open it to update."))
    # Map
    if map_name.get("hidden"):
        rows.append(("Map", "bad", "Can't see the minimap's name."))
    elif map_name.get("name"):
        rows.append(("Map", "ok", f"Map: {map_name['name']}"))
    else:
        rows.append(("Map", "wait", "Waiting to read the minimap."))
    # Potions
    if not potions.get("on"):
        rows.append(("Potions", "off", "Potion counting is off in Settings."))
    elif potions.get("ok"):
        rows.append(("Potions", "ok", "Potion bar is being read."))
    elif potions.get("looking"):
        rows.append(("Potions", "wait", "Looking for the potion bar."))
    else:
        rows.append(("Potions", "bad", "Can't read the potion bar."))
    return rows


def load_config():
    """The saved settings, with anything missing or damaged replaced by the defaults."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {}
    except Exception as e:
        log_error("reading settings (using the defaults)", e)
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    for key, default in DEFAULTS.items():
        value = cfg.get(key)
        if type(value) is not type(default) or (key == "gap_seconds" and not 1 <= value <= 3600):
            cfg[key] = default
    cfg["transparency"] = min(MAX_TRANSPARENCY, max(0, cfg["transparency"]))
    cfg["potion_prices"] = potions.clean_prices(cfg["potion_prices"])
    if cfg["compact_shape"] not in SHAPES:
        cfg["compact_shape"] = "square"
    if cfg["ui_scale"] not in UI_SCALES:
        cfg["ui_scale"] = 100
    if cfg.get("meso_mode") not in MESO_MODES:
        cfg["meso_mode"] = "notices"
    job = cfg.get("job_override", "")
    if not isinstance(job, str):
        cfg["job_override"] = ""
    else:
        cfg["job_override"] = job.strip()[:40]
    keys = cfg.get("hotkeys") if isinstance(cfg.get("hotkeys"), dict) else {}
    cfg["hotkeys"] = {a: keys[a] if a in keys and (keys[a] == "" or parse_hotkey(keys[a])) else k
                      for a, k in DEFAULT_HOTKEYS.items()}            # "" is a hotkey turned off
    ints = lambda v, n: isinstance(v, list) and len(v) == n and all(type(x) is int for x in v)
    checks = {"pos": lambda v: ints(v, 2), "log_size": lambda v: ints(v, 2) and min(v) >= 100,
              "mesos_box": lambda v: ints(v, 6), "mode": lambda v: v in ("window", "region"),
              "region": lambda v: isinstance(v, dict) and all(type(v.get(k)) is int and (
                  k in ("left", "top") or v[k] > 0) for k in ("left", "top", "width", "height")),
              "log_columns": lambda v: isinstance(v, dict), "log_columns_pt": lambda v: isinstance(v, dict),
              "log_open": lambda v: type(v) is bool,
              "share_runs": lambda v: type(v) is bool,       # missing: not asked yet
              "share_health": lambda v: type(v) is bool,     # (same: the daily health report)
              "health_id": healthlog.good_id,
              "exp_v2_spots": good_v2_spots,                  # (where reader v2 found the bar, by window size)
              "mesos_boxes": good_mesos_boxes,                # (where the mesos box was, by window size)
              "reports_sent": lambda v: isinstance(v, dict) and isinstance(v.get("day"), str) and type(v.get("n")) is int and v["n"] >= 0}
    for key, ok in checks.items():
        if key in cfg and not (cfg[key] is None and key == "log_size") and not ok(cfg[key]):    # (a null log_size is saved on purpose)
            del cfg[key]
    if cfg.get("mode") == "region" and not cfg.get("region"):
        cfg["mode"] = "window"
    return cfg


def save_config(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)                    # never leaves a half-written file
    except Exception as e:
        log_error("saving settings", e)


def move_old_data():
    """Earlier versions kept settings and the log next to the program: copy them over once."""
    for name in ("config.json", "exp_log.json", "exp_log.txt", "mesos_digits.npz"):
        old, new = HOME_DIR / name, DATA_DIR / name
        if old.exists() and not new.exists():
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
            except Exception as e:
                log_error(f"copying {name} from the program's folder", e)


ENG_DATA_MIN = 1_000_000     # a real eng.traineddata is megabytes: anything smaller is cut off or empty


def locate_tesseract():
    """(program, data folder argument, state, why): the Tesseract that comes with the download,
    else an installed one. A program counts only if it has a usable eng.traineddata: Tesseract
    5 on Windows won't find it by itself when the folder's name has letters outside the PC's
    code page, so the folder is always handed over (see Platform.tessdata_arg). Never a program
    in the folder the overlay happens to be started from (Windows' usual search looks there
    first, so a tesseract.exe someone left in, say, Downloads could be run)."""
    global tesseract_where
    state, why, seen = "missing", "", None
    for p in PLATFORM.tesseract_candidates(HOME_DIR, APP_DIR):
        try:
            usable = p.is_file() and os.access(p, os.X_OK)
        except OSError:                     # (a folder in PATH we aren't allowed into: not a program we can use)
            usable = False
        if not usable:
            continue
        state = "no_eng"
        for folder in PLATFORM.tessdata_candidates(p):
            try:
                if (folder / "eng.traineddata").stat().st_size <= ENG_DATA_MIN:
                    continue
            except OSError:
                continue
            arg = PLATFORM.tessdata_arg(p, folder)
            if arg is None:
                why, seen = "path", seen or (p, folder)
                continue
            tesseract_where = (str(p), str(folder))
            return str(p), arg, "ok", ""
        seen = seen or (p, p.parent)
    if seen:
        tesseract_where = (str(seen[0]), str(seen[1]))
    return None, "", state, why or ("file" if state == "no_eng" else "")


def find_tesseract():
    """The Tesseract program to use, or None (see locate_tesseract)."""
    return locate_tesseract()[0]


def tesseract_note(what="Map names"):
    """Why Tesseract can't be used, in the player's words ('' if it can)."""
    if tesseract_state == "no_eng" and tesseract_why == "path":
        return (f"{what} and your class can't be read from this folder because its name has unusual "
                "characters. Move the overlay's folder to a simple place like C:\\Games\\. EXP and mesos still work.")
    if tesseract_state == "no_eng":
        return (f"{what} and your class can't be read: a file is missing from the overlay's folder "
                "(tessdata\\eng.traineddata). Download the zip again and extract it. If it's still missing, "
                "your antivirus may have removed it. EXP and mesos still work.")
    if tesseract_state == "missing":
        return f"{what} can't be read: Tesseract OCR is missing (see the README)."
    return ""


# --------------------------------------------------------------------------- updates
#
# At start (unless turned off in Settings) it asks GitHub for the latest release. The only
# other time it goes online is to share finished 15-minute runs, if the player agreed to that.

def version_key(version):
    """'v0.9.1-beta' -> something to compare versions by. A full release beats its betas."""
    main, _, pre = version.strip().lstrip("vV").partition("-")
    nums = [int(x) for x in re.findall(r"\d+", main)[:3]]
    return tuple(nums + [0] * (3 - len(nums))), 0 if pre else 1, pre


def _plain_text(markdown):
    """Release notes from GitHub, readable as plain text."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", markdown)          # [text](link) -> text
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)                       # **bold** (keeps exp_log.txt)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t.replace("\r", ""))
    return t.strip()[:5000]


def check_for_update(timeout=8):
    """(newer version, its release notes) if GitHub has a newer release, else None.
    Raises if GitHub can't be reached, and while testing (MAPLE_EXP_DATA set: nothing goes online then)."""
    if sharing_blocked():
        raise RuntimeError("the update check is off while testing (MAPLE_EXP_DATA is set)")
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": f"MapleClassicExpOverlay/{VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    if not re.fullmatch(r"v?\d+(\.\d+){0,2}(-[\w.]+)?", tag):
        raise ValueError(f"unexpected release name {tag[:40]!r}")
    if version_key(tag) <= version_key(VERSION):
        return None
    return tag.lstrip("vV"), _plain_text(str(data.get("body") or ""))


def post_runs(install, runs, timeout=15):
    """Sends finished runs to the stats site (only if the player agreed to share).
    Returns the start times of the runs it's done with: the ones the site has (now or from
    before), and ones it turned down for good. Runs turned down only for today ("retry", too
    many sent today) are left to be sent again later."""
    if sharing_blocked():
        # MAPLE_EXP_DATA moves the data folder, which only tests do. What they have in their logs is
        # made up, and made-up runs must never reach the real site: so not while testing.
        raise RuntimeError("sharing is off while testing (MAPLE_EXP_DATA is set)")
    shared = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
              "seconds", "mesos", "mesos_seconds", "version", "seal")
    body = json.dumps({"install": install, "runs": [{k: r.get(k) for k in shared} for r in runs]}).encode()
    request = urllib.request.Request(f"{STATS_SITE}/api/runs", data=body, headers={
        "content-type": "application/json", "User-Agent": f"MapleClassicExpOverlay/{VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    return [r.get("started") for r in data.get("results", []) if r.get("ok") or not r.get("retry")]


def sharing_blocked():
    """True while testing: MAPLE_EXP_DATA moves the data folder, which only tests do, and what a test holds is
    made up, so nothing is sent unless MAPLE_EXP_ALLOW_SHARE says so."""
    return bool(os.environ.get("MAPLE_EXP_DATA") and not os.environ.get("MAPLE_EXP_ALLOW_SHARE"))


def post_health(body, timeout=15):
    """Sends one health report (healthlog.HealthLog.body) to the stats site. Returns (outcome, detail):
    'ok' (taken; also when the site already had that day), 'bad' (refused: detail names the field), 'big'
    (too large) or 'later' (a limit, a network problem or the site is down: try again tomorrow)."""
    if sharing_blocked():
        raise RuntimeError("health reports are off while testing (MAPLE_EXP_DATA is set)")
    request = urllib.request.Request(f"{STATS_SITE}/api/health", data=json.dumps(body).encode(), headers={
        "content-type": "application/json", "User-Agent": f"MapleClassicExpOverlay/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(10_000).decode("utf-8"))
        return ("ok", "") if isinstance(data, dict) and data.get("ok") else ("later", "")
    except urllib.error.HTTPError as e:
        if e.code == 413:
            return "big", ""
        if e.code == 400:
            try:
                field = str(json.loads(e.read(1000).decode("utf-8")).get("error", ""))
            except Exception:
                field = ""
            field = re.sub(r"[^A-Za-z_]", "", field)[:40]
            # (only the site's own refusal names a field; a proxy or a hotel Wi-Fi page answering 400 is "try later",
            # or that day would be dropped for nothing)
            return ("bad", field) if field else ("later", "")
        return "later", ""
    except Exception:
        return "later", ""


# --------------------------------------------------------------------------- hotkeys
#
# Shift+1 to 4 by default (changeable in Settings). How they're caught is up to the OS
# (PLATFORM.create_hotkeys); they're only on while MapleStory or the overlay is in front.

HOTKEY_ACTIONS = {"start": "Start / Pause", "reset": "Reset", "skip": "Don't log", "compact": "Compact"}
DEFAULT_HOTKEYS = {"start": "Shift+1", "reset": "Shift+2", "skip": "Shift+3", "compact": "Shift+4"}


# --------------------------------------------------------------------------- screenshots

# (grab() and the Capture classes live in capture.py: where the pictures come from)


# --------------------------------------------------------------------------- reading the bar
#
# The status bar shows "EXP. 38,849 [61.02%]": white digits and green brackets. The game
# draws it in a small pixel font scaled up to the window size, so once a digit is rescaled
# to a standard height it can be recognised by comparing it pixel by pixel with saved
# examples of each digit (digits.npz).

Blob = namedtuple("Blob", "i x y w h")
# cells are digit positions (centre x, width, "digit" or "wide"); V is the brightness image
Layout = namedtuple("Layout", "gh cy exp pct box V")

CANON = 24                  # digits are compared at this height
PH, PW = 28, 19             # size of one digit patch at that height
SHIFT = 3                   # px of wiggle room when lining a digit up with an example
MIN_SCORE = 0.75            # correlation the best digit needs (true matches score ~0.9+)
MIN_LEAD = 0.03             # ...and how far ahead of the runner-up it must be



MAX_REPORTS_PER_DAY = 3         # Send report: the site takes 3 per day per ID too


def reports_sent_today(cfg, now=None):
    """How many reports this player has sent today (UTC day, like the site counts)."""
    sent = cfg.get("reports_sent")
    today = healthlog.utc_day(time.time() if now is None else now)
    return sent["n"] if isinstance(sent, dict) and sent.get("day") == today else 0


def post_report(body, timeout=15):
    """Sends one Send report (report.report_body) to the stats site. Returns 'ok', 'limit' (3 a day),
    'message' (the site doesn't like the typed text), 'bad' (refused for another reason, or too large)
    or 'later' (a network problem or the site is down)."""
    if sharing_blocked():
        raise RuntimeError("Send report is off while testing (MAPLE_EXP_DATA is set)")
    request = urllib.request.Request(f"{STATS_SITE}/api/report", data=report.encode_body(body), headers={
        "content-type": "application/json", "User-Agent": f"MapleClassicExpOverlay/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(10_000).decode("utf-8"))
        return "ok" if isinstance(data, dict) and data.get("ok") else "later"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "limit"
        if e.code == 400:
            try:
                field = str(json.loads(e.read(1000).decode("utf-8")).get("error", ""))
            except Exception:
                field = ""
            return "message" if field == "message" else "bad"
        return "bad" if e.code == 413 else "later"
    except Exception:
        return "later"


def _blobs(mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return labels, [Blob(i, *map(int, stats[i][:4])) for i in range(1, n)]


def _left_heavy(labels, b):
    """True for '[' (stroke on the left), False for ']'."""
    m = labels[b.y:b.y + b.h, b.x:b.x + b.w] == b.i
    half = max(1, b.w // 2)
    return m[:, :half].sum() > m[:, b.w - half:].sum()


def _cells(blobs, gh):
    """Digit positions. A blob as wide as several digits (touching digits) is split evenly."""
    pitch = 0.84 * gh
    out = []
    for b in blobs:
        if 0.95 * b.h < b.w < 1.25 * gh:
            out.append((b.x + b.w / 2, b.w, "wide"))          # the '%' sign
            continue
        n = max(1, int(round((b.w + 0.1 * gh) / pitch)))
        out.extend((b.x + (k + 0.5) * b.w / n, b.w / n, "digit") for k in range(n))
    return out


def _exp_label_dot(wblobs, bracket, bh):
    """The '.' of the label "EXP." just left of a '[' (a blob), or None. The dot sits low, under the
    right side of the P, and two taller letters come before it. "HP" and "MP" have no such dot, and
    this holds at any size because it only compares the blobs with the bracket's height."""
    base = bracket.y + bracket.h
    dots = [b for b in wblobs if b.x + b.w <= bracket.x and bracket.x - b.x <= 3.0 * bh
            and b.h <= 0.45 * bh and b.w <= 0.45 * bh and abs(b.y + b.h - base) <= 0.35 * bh]
    for dot in sorted(dots, key=lambda b: -b.x):
        tall = lambda b: 1.15 * bh <= b.h <= 1.9 * bh and abs(b.y + b.h - base) <= 0.35 * bh
        under = [p for p in wblobs if tall(p) and p.x + 0.4 * p.w <= dot.x and dot.x + dot.w <= p.x + p.w + 1
                 and dot.y >= p.y + 0.6 * p.h]
        if not under:
            continue
        p = under[0]
        # Two or more letters come before the dot: separate blobs, or touching ones in one wide blob.
        if p.w >= 1.8 * bh or any(tall(e) and p.x - (e.x + e.w) <= 0.6 * bh and e.x + e.w <= p.x + 1
                                  for e in wblobs):
            return dot
    return None


def find_exp(bgr):
    """Locate the EXP text in a screenshot. Returns a Layout or None."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    green = (sat >= 90) & (val >= 110) & (hue >= 26) & (hue <= 50)
    white = (sat < 70) & (val >= 140)
    glabels, gblobs = _blobs(green)
    _, wblobs = _blobs(white)
    brackets = sorted((b for b in gblobs if b.h >= 5 and b.w <= 0.6 * b.h), key=lambda b: b.x)
    V = val.astype(np.float32)
    found = []
    for o in brackets:
        bh, cy = o.h, o.y + o.h / 2
        on_line = lambda b: abs(b.y + b.h / 2 - cy) <= 0.3 * bh
        if o.w >= 6 and not _left_heavy(glabels, o):
            continue                                          # clearly a ']'
        right = [c for c in brackets if c.x > o.x + o.w and c.x - o.x <= 9 * bh
                 and on_line(c) and abs(c.h - bh) <= 0.25 * bh]
        if not right:
            continue
        c = right[0]
        if c.w >= 6 and _left_heavy(glabels, c):
            continue                                          # clearly another '['
        line = [b for b in wblobs if on_line(b) and b.h >= 0.6 * bh]
        inside = sorted((b for b in line if b.x >= o.x + o.w and b.x + b.w <= c.x), key=lambda b: b.x)
        if len(inside) < 2 or any(b.h > 1.25 * bh for b in inside):
            continue
        # The EXP number ends right before '['; walk left until a gap or the taller label.
        # Digit height comes from the % digits (same font): a single digit can be shorter
        # when the game scales its font by an odd amount (a '1' was 9 px next to 11 px digits).
        dh = float(np.median([b.h for b in inside]))
        run, edge = [], o.x
        for b in sorted((b for b in line if b.x + b.w <= o.x), key=lambda b: -b.x):
            if edge - (b.x + b.w) > 2.0 * bh or b.h > 1.25 * bh or b.h > 1.15 * dh:
                break
            run.append(b)
            edge = b.x
        if not run:
            # No number: the game shows only "EXP. [58.47%]" once the EXP is big. Only the label
            # "EXP." tells that from HP/MP, so no label means this isn't the EXP bar.
            dot = _exp_label_dot(wblobs, o, bh)
            if dot is None:
                continue
            heights = [b.h for b in inside if b.w <= 0.85 * b.h]
            gh = float(np.median(heights)) if heights else float(bh)
            # box[0] is where the number would start (the class is read left of it): a bit right of the dot
            found.append(Layout(gh, cy, [], _cells(inside, gh),
                                (int(dot.x + 0.84 * gh), int(cy - bh), c.x + c.w, int(cy + bh)), V))
            continue
        run.reverse()
        heights = [b.h for b in run + inside if b.w <= 0.85 * b.h]
        gh = float(np.median(heights)) if heights else float(bh)
        found.append(Layout(gh, cy, _cells(run, gh), _cells(inside, gh),
                            (run[0].x, int(cy - bh), c.x + c.w, int(cy + bh)), V))
    if not found:
        return None
    # The status bar is the lowest line found; EXP is the right-most value on it.
    low = max(f.cy for f in found)
    return max((f for f in found if f.cy >= low - 0.5 * f.gh), key=lambda f: f.box[0])


# ---- finder v2: shape, layout and contrast instead of fixed colours
#
# Works on brightness only (the brightest of B, G, R, like HSV "value"), so a colour change, a
# paler bracket or a greyscale screen doesn't matter. Text is whatever is clearly brighter than its
# surroundings; a bracket is a stroke with two short feet; the EXP block is the one with a "%" sign
# before its "]" (HP and MP have "/").

V2_TEXT = 0.12      # how far above its surroundings text must be (share of how bright the brightest text is)
V2_PERCENT = 0.5    # how well a blob must match the game's "%" glyph (correlation) to be taken for one
V2_FAINT = 0.07     # ...and for the thin, paler brackets
V2_TILE = 128       # width of a stretch of a very wide strip whose text brightness is judged on its own
V2_WIDE = 2200      # a strip this wide also gets the small blur when nothing is found


def _v2_brightness(bgr):
    """Brightest of B, G, R per pixel (what HSV calls value): white digits and green brackets alike."""
    b, g, r = cv2.split(bgr)
    return cv2.max(cv2.max(b, g), r)


def _v2_text_mask(V, width=None, tiled=False):
    """(text, faint): pixels clearly brighter than the local average, and a second mask that also takes
    the paler ones (brackets); None if nothing in the picture is bright enough to be text. The local
    average is taken over about 3 digit heights. "How bright text is" is the 99.7th percentile of that
    contrast; tiled=True asks every stretch of V2_TILE columns alone and takes the strongest (for a very
    wide strip, where small text is far under 0.3% of all the pixels)."""
    k = int(min(63, max(31, 0.0165 * (width or V.shape[1])))) | 1
    D = cv2.subtract(V, cv2.blur(V, (k, k), borderType=cv2.BORDER_REPLICATE))
    top = 0
    for x in range(0, D.shape[1], V2_TILE if tiled else max(1, D.shape[1])):
        part = D[:, x:x + V2_TILE] if tiled else D
        hist = cv2.calcHist([part], [0], None, [256], [0, 256]).ravel().cumsum()
        top = max(top, int(np.searchsorted(hist, 0.997 * hist[-1])))
    if top < 25:
        return None
    return D >= V2_TEXT * top, D >= (V2_FAINT / 2 if tiled else V2_FAINT) * top


def _v2_has_feet(V, b):
    """True if the stroke at blob b has short feet: more brightness in its top and bottom fifth than in
    its middle (a digit "1" or a letter "l" has a plain end)."""
    x0, x1 = max(0, b.x - 2), b.x + b.w + 2
    win = V[max(0, b.y - 1):b.y + b.h + 1, x0:x1].astype(np.float32)
    win = (win - win.min(axis=1, keepdims=True)).sum(axis=1)[1:-1]
    z = max(2, int(round(0.2 * len(win))))
    if len(win) < 2 * z + 1:
        return False
    return min(win[:z].mean(), win[-z:].mean()) >= 1.12 * max(float(np.median(win[z:-z])), 1.0)


def _v2_bracket_side(sub, V, b):
    """'[' or ']' if the blob mask (bool, h x w) is a thin vertical stroke with feet at the top and the
    bottom pointing inwards; '|' if the feet are there but the side can't be told (a small or blurred
    bracket); else None."""
    h, w = sub.shape
    if h < 5 or w < 2 or w > 0.6 * h:
        return None
    col = sub.sum(axis=0)
    half = max(1, (w + 1) // 2)
    left, right = col[:half].max(), col[w - half:].max()
    if max(left, right) < 0.7 * h or not _v2_has_feet(V, b):
        return None
    if left >= 0.7 * h and right <= 0.5 * h:
        return "["
    if right >= 0.7 * h and left <= 0.5 * h:
        return "]"
    return "|"


def _v2_merge_pieces(blobs, bh):
    """Pieces of one glyph become one blob (a "%" often breaks into the two circles and the slash, or into
    a left and a right half), as long as the whole is no bigger than a glyph: 1.2 x bh tall, 1.4 x bh wide."""
    out = []
    for b in sorted(blobs, key=lambda b: b.x):
        for k, o in enumerate(out):
            x, y = min(o.x, b.x), min(o.y, b.y)
            w, h = max(o.x + o.w, b.x + b.w) - x, max(o.y + o.h, b.y + b.h) - y
            ov = min(o.x + o.w, b.x + b.w) - max(o.x, b.x)
            tall = min(o.h, b.h) >= 0.6 * bh
            if (ov >= 0.5 * min(o.w, b.w) or (ov >= 1 and tall)) and h <= 1.2 * bh and w <= 1.4 * bh:
                out[k] = Blob(o.i, x, y, w, h)
                break
        else:
            out.append(b)
    return out


def _v2_is_percent(hi, b):
    """True if the blob looks like "%": its text mask, squeezed to 7 x 7, matches the game's own "%" glyph
    (a digit, "/" or a letter at that spot is far off: 0.1 or less against 0.75+ for a "%")."""
    sub = hi[b.y:b.y + b.h, b.x:b.x + b.w].astype(np.float32)
    if sub.size == 0:
        return False
    sub = cv2.resize(sub, (7, 7), interpolation=cv2.INTER_AREA).reshape(-1)
    pct = _v2_percent_glyph().reshape(-1)
    sub, pct = sub - sub.mean(), pct - pct.mean()
    return float(sub @ pct) >= V2_PERCENT * max(float(np.linalg.norm(sub) * np.linalg.norm(pct)), 1e-6)


def _v2_percent_glyph(_cache=[]):
    if not _cache:
        _cache.append(np.array(exp_font.load()["glyphs"]["%"]["rows"], np.float32))
    return _cache[0]


def find_exp_v2(bgr, hint=None):
    """Locate the EXP text in a screenshot by shape and contrast, not colour. Returns a Layout
    (the same as find_exp) or None. hint is a box (x0, y0, x1, y1) where the bar was last seen: it
    is searched first (the Layout's box and cy say where it was found, to remember for next time)."""
    if bgr.size == 0:
        return None
    if bgr.ndim == 2 or (bgr.ndim == 3 and bgr.shape[2] == 1):      # (a grey or BGRA picture is read like a BGR one)
        bgr = cv2.cvtColor(bgr.reshape(bgr.shape[:2]), cv2.COLOR_GRAY2BGR)
    elif bgr.ndim != 3 or bgr.shape[2] < 3:
        return None
    elif bgr.shape[2] > 3:
        bgr = bgr[:, :, :3]
    if hint is not None:
        H, W = bgr.shape[:2]
        try:
            x0, y0, x1, y1 = hint
            x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
        except (TypeError, ValueError, OverflowError):              # (a garbled remembered spot is no spot)
            x0 = y0 = x1 = y1 = 0
        if x1 - x0 > 8 and y1 - y0 > 8:
            lay = _find_exp_v2(bgr[y0:y1, x0:x1], W)
            if lay:
                V = np.zeros(bgr.shape[:2], np.float32)         # only the searched box is filled in: all the digits need
                V[y0:y1, x0:x1] = lay.V
                sh = lambda cells: [(c[0] + x0, c[1], c[2]) for c in cells]
                b = lay.box
                return Layout(lay.gh, lay.cy + y0, sh(lay.exp), sh(lay.pct),
                              (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0), V)
    return _find_exp_v2(bgr)


def _v2_bracket_candidates(mask, V8, have):
    """Brackets in a text mask: thin tall blobs that have a partner of the same height to their right on
    the same line (and whose feet and stroke look right). Skips ones already in have."""
    n, glabels, stats, _ = cv2.connectedComponentsWithStats(mask.view(np.uint8), connectivity=8)
    thin = np.nonzero((stats[:, 3] >= 5) & (stats[:, 2] <= 0.6 * stats[:, 3]))[0]
    thin = thin[thin > 0]
    if len(thin) < 2:
        return []
    x, y, h = stats[thin, 0], stats[thin, 1], stats[thin, 3]
    dx = x[None, :] - x[:, None]
    pair = ((dx >= 3 * h[:, None]) & (dx <= 9 * h[:, None]) & (np.abs(y[None, :] - y[:, None]) <= 0.3 * h[:, None])
            & (np.abs(h[None, :] - h[:, None]) <= 0.25 * h[:, None]))
    keep = thin[pair.any(axis=1) | pair.any(axis=0)]
    out = []
    for i in keep:
        b = Blob(int(i), *map(int, stats[i][:4]))
        if any(abs(k.x - b.x) <= 2 and abs(k.y - b.y) <= 2 for k, _ in have):
            continue
        side = _v2_bracket_side(glabels[b.y:b.y + b.h, b.x:b.x + b.w] == b.i, V8, b)
        if side:
            out.append((b, side))
    return out


def _find_exp_v2(bgr, width=None):
    """(width: the whole screen's width where bgr is only a box of it; it sets the size of the blur.)"""
    lay = _find_exp_v2_try(bgr, width, False)
    if lay is None:
        # Text that is a tiny share of the picture (the small UI on a big screen: a very wide strip) is not
        # what the 99.7th percentile sees: look again asking each stretch alone, and on a very wide strip
        # also with the small blur (the big one runs the text's soft edges together).
        lay = _find_exp_v2_try(bgr, width, True)
        if lay is None and (width or bgr.shape[1]) >= V2_WIDE:
            lay = _find_exp_v2_try(bgr, 1, True)
    return lay


def _find_exp_v2_try(bgr, width, tiled):
    V8 = _v2_brightness(bgr)
    masks = _v2_text_mask(V8, width, tiled)
    if masks is None:
        return None
    low, faint = masks
    brackets = _v2_bracket_candidates(low, V8, [])
    lay = _v2_pair_up(low, V8, brackets)
    if lay is None:                    # a blur can cut the paler brackets short: look again with the softer mask
        brackets += _v2_bracket_candidates(faint, V8, brackets)
        lay = _v2_pair_up(low, V8, brackets)
    return lay


def _v2_pair_up(low, V8, brackets):
    """The Layout of the EXP block among the bracket candidates, or None."""
    # Only the rows where two brackets could pair up are looked at closely.
    rows = [(a.y - a.h, a.y + 2 * a.h) for a, _ in brackets for c, _ in brackets
            if 3 * a.h <= c.x - a.x <= 9 * a.h and abs(a.y - c.y) <= 0.3 * a.h]
    if not rows:
        return None
    y0, y1 = max(0, min(r[0] for r in rows)), min(low.shape[0], max(r[1] for r in rows))
    band = low[y0:y1].copy()
    # a bracket's pixels are never part of a digit
    for b, _ in brackets:
        band[max(0, b.y - y0):max(0, b.y + b.h - y0), b.x:b.x + b.w] = False
    _, wblobs = _blobs(band)
    wblobs = [Blob(b.i, b.x, b.y + y0, b.w, b.h) for b in wblobs]
    V = V8.astype(np.float32)
    strokes = [b for b, _ in brackets]
    opens = sorted((b for b, s in brackets if s != "]"), key=lambda b: b.x)
    closes = sorted((b for b, s in brackets if s != "["), key=lambda b: b.x)
    found = []
    for o in opens:
        bh, cy = o.h, o.y + o.h / 2
        on_line = lambda b: abs(b.y + b.h / 2 - cy) <= 0.3 * bh
        right = [c for c in closes if c.x > o.x + o.w and c.x - o.x <= 9 * bh
                 and on_line(c) and abs(c.h - bh) <= 0.25 * bh]
        if not right:
            continue
        c = right[0]
        near = [b for b in wblobs if abs(b.y + b.h / 2 - cy) <= 0.8 * bh and o.x - 12 * bh <= b.x <= c.x + c.w]
        near = _v2_merge_pieces([b for b in near if b.h <= 1.3 * bh], bh) + [b for b in near if b.h > 1.3 * bh]
        line = [b for b in near if on_line(b) and b.h >= 0.6 * bh]
        inside = sorted((b for b in line if b.x >= o.x + o.w and b.x + b.w <= c.x), key=lambda b: b.x)
        if len(inside) < 3 or any(b.h > 1.25 * bh for b in inside):
            continue
        # the "%" sign right before "]": that's what tells EXP from HP and MP
        last = inside[-1]
        if not (0.9 * bh <= last.w <= 1.4 * bh and _v2_is_percent(low, last)):
            continue
        dh = float(np.median([b.h for b in inside]))
        run, edge = [], o.x
        for b in sorted((b for b in line if b.x + b.w <= o.x), key=lambda b: -b.x):
            if edge - (b.x + b.w) > 2.0 * bh or b.h > 1.25 * bh or b.h > 1.15 * dh or b.w < 0.2 * dh:
                break                                     # (a sliver is a piece of the label's thick letters, not a digit)
            if any(b.x + b.w <= k.x and k.x + k.w <= edge and on_line(k) for k in strokes):
                break                                     # another bracket in the way: the number stops here
            run.append(b)
            edge = b.x
        if not run:
            # No number: only "EXP. [58.47%]" is left once the EXP is big. The label's dot says it's EXP.
            dot = _exp_label_dot(wblobs, o, bh)
            if dot is None:
                continue
            heights = [b.h for b in inside if b.w <= 0.85 * b.h]
            gh = float(np.median(heights)) if heights else float(bh)
            found.append(Layout(gh, cy, [], _cells(inside, gh),
                                (int(dot.x + 0.84 * gh), int(cy - bh), c.x + c.w, int(cy + bh)), V))
            continue
        run.reverse()
        heights = [b.h for b in run + inside if b.w <= 0.85 * b.h]
        gh = float(np.median(heights)) if heights else float(bh)
        if not 5 <= gh <= 34:
            continue
        found.append(Layout(gh, cy, _cells(run, gh), _cells(inside, gh),
                            (run[0].x, int(cy - bh), c.x + c.w, int(cy + bh)), V))
    if not found:
        return None
    low_cy = max(f.cy for f in found)
    # the lowest line; if a digit "1" passed for the "[", the real "[" (further left) gives the longer number
    return min((f for f in found if f.cy >= low_cy - 0.5 * f.gh), key=lambda f: f.box[0])


def digit_patch(V, cx, cy, gh, shift):
    """Brightness patch around one digit, rescaled so the digit is CANON px tall."""
    k = gh / CANON
    x0 = cx - (PW / 2 + shift) * k
    y0 = cy - (PH / 2 + shift) * k
    M = np.float32([[1 / k, 0, -x0 / k], [0, 1 / k, -y0 / k]])
    return cv2.warpAffine(V, M, (PW + 2 * shift, PH + 2 * shift), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _unit_rows(a):
    a = a - a.mean(axis=1, keepdims=True)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-6)


class DigitTemplates:
    """Example patches of each digit; scores a patch against all of them at once."""

    def __init__(self, examples):
        labels, rows = [], []
        for digit, patches in sorted(examples.items()):
            for p in patches:
                labels.append(digit)
                rows.append(np.asarray(p, np.float32).reshape(-1))
        self.labels = np.array(labels)
        self.digits = sorted(set(labels))
        self.mat = _unit_rows(np.stack(rows))

    @classmethod
    def load(cls, path=TEMPLATE_PATH):
        try:
            data = np.load(path)
            if int(data["canon"]) != CANON:
                return None
            return cls({k[1:]: data[k] for k in data.files if k.startswith("d")})
        except Exception:
            return None

    def scores(self, patch):
        """Best correlation per digit, trying every small shift of the patch."""
        windows = np.lib.stride_tricks.sliding_window_view(patch, (PH, PW)).reshape(-1, PH * PW)
        s = (_unit_rows(windows) @ self.mat.T).max(axis=0)
        return {d: float(s[self.labels == d].max()) for d in self.digits}

    def match(self, lay, cell):
        """(digit, score, lead over the runner-up) for one digit position."""
        best = {}
        for f in (0.95, 1.0, 1.05):         # allow for small errors in the size estimate
            for d, s in self.scores(digit_patch(lay.V, cell[0], lay.cy, lay.gh * f, SHIFT)).items():
                best[d] = max(best.get(d, -1.0), s)
        ranked = sorted(best.values(), reverse=True)
        lead = ranked[0] - ranked[1] if len(ranked) > 1 else 1.0
        return max(best, key=best.get), ranked[0], lead


def _pct_cells(lay):
    """The digits inside the brackets: drops the '%' sign right before the ']'."""
    return [c for c in lay.pct if lay.box[2] - c[0] > 1.5 * lay.gh]


def _read_digits(tpl, lay, cells):
    s = ""
    for cell in cells:
        if cell[2] != "digit":
            return None
        digit, score, lead = tpl.match(lay, cell)
        if score < MIN_SCORE or lead < MIN_LEAD:
            return None
        s += digit
    return s


def read_layout(tpl, lay):
    """(exp, pct) read with the digit templates, or None if any digit is unclear. exp is None
    when the bar shows only a percentage (the layout has no number cells)."""
    exp_s = _read_digits(tpl, lay, lay.exp)
    pct_s = _read_digits(tpl, lay, _pct_cells(lay))
    if (lay.exp and not exp_s) or not pct_s or not 3 <= len(pct_s) <= 5:
        return None
    if (exp_s and len(exp_s) > 1 and exp_s[0] == "0") or (len(pct_s) > 3 and pct_s[0] == "0"):
        return None                                # a leading 0: some digits were cut off
    return (int(exp_s) if exp_s else None), int(pct_s) / 100       # the game always shows 2 decimals


def _ocr_cells(bgr, lay, cells):
    """Tesseract on the area covering cells, two ways. Returns digit strings."""
    if not cells:
        return []
    g = lay.gh
    x0 = int(min(c[0] - c[1] / 2 for c in cells) - 0.1 * g)
    x1 = int(max(c[0] + c[1] / 2 for c in cells) + 0.1 * g)
    crop = bgr[max(0, int(lay.cy - 0.75 * g)):int(lay.cy + 0.75 * g), max(0, x0):x1]
    if crop.size == 0:
        return []
    big = cv2.resize(crop, None, fx=40 / g, fy=40 / g, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    white = (((hsv[..., 1] < 70) & (hsv[..., 2] >= 140)) * 255).astype(np.uint8)
    white = cv2.dilate(white, np.ones((3, 3), np.uint8))
    _, bright = cv2.threshold(hsv[..., 2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    images = [cv2.copyMakeBorder(255 - mask, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
              for mask in (white, bright)]
    return [re.sub(r"\D", "", text) for text in TESS.read_lines(images, "0123456789,.")]


def tesseract_candidates(bgr, lay):
    """Fallback: (exp, pct) guesses from Tesseract. The tracker picks one that adds up."""
    if not tesseract_ok:
        return []
    exps = {int(s) for s in _ocr_cells(bgr, lay, lay.exp) if s}
    pcts = {int(s) / 100 for s in _ocr_cells(bgr, lay, _pct_cells(lay))
            if 3 <= len(s) <= 5}
    return [(e, p) for e in sorted(exps) for p in sorted(pcts)]


def build_templates(samples, scales=(1.0, 0.75, 0.55, 0.4, 0.3), path=TEMPLATE_PATH):
    """Make digits.npz from screenshots of the bar: [(bgr, "38,849", "61.02"), ...].

    Each screenshot is also shrunk to several sizes so small windows are covered too.
    """
    examples = {}
    for bgr, exp_s, pct_s in samples:
        for f in scales:
            img = bgr if f == 1.0 else cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            lay = find_exp(img)
            if not lay:
                continue
            exp_cells, pct_cells = lay.exp, _pct_cells(lay)
            exp_t, pct_t = re.sub(r"\D", "", exp_s), re.sub(r"\D", "", pct_s)
            if len(exp_cells) != len(exp_t) or len(pct_cells) != len(pct_t):
                continue
            for cell, ch in zip(exp_cells + pct_cells, exp_t + pct_t):
                examples.setdefault(ch, []).append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
    np.savez(path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in examples.items()})
    return {k: len(v) for k, v in sorted(examples.items())}


# --------------------------------------------------------------------------- map name and level
#
# The minimap's light-blue info box shows the region and, under it, the map name in white
# text. It's read with Tesseract a few different ways, and the most common answer wins.
# The level comes from the EXP table: each level needs a different amount of EXP.

def level_for_req(req):
    """The level whose EXP requirement matches req, from the Classic World EXP table."""
    if not req:
        return None
    for level, need in enumerate(EXP_TABLE, start=1):
        if abs(req - need) <= 0.005 * need:
            return level
    return None


def levels_for(exp, pct):
    """The levels whose EXP requirement fits exp at pct% (% is shown with 2 decimals)."""
    req, err = exp / (pct / 100), 0.002 + 0.01 / pct
    return [level for level, need in enumerate(EXP_TABLE, start=1)
            if abs(req - need) <= err * need and exp < need]


def fits_exp_table(exp, pct):
    """True if exp at pct% matches some level's EXP requirement. A misread almost never does
    (e.g. '1 [71.68%]' for '68,601 [71.68%]'), so a reading that doesn't isn't trusted."""
    return pct < 0.2 or bool(levels_for(exp, pct))      # under 0.2%: too little EXP to tell


# Job names as the status bar shows them (old-style, as in Classic World). A reading is
# matched to the closest of these; a clear reading that matches none is kept as read.
JOBS = ["Beginner",
        "Swordman", "Fighter", "Crusader", "Hero", "Page", "White Knight", "Paladin",
        "Spearman", "Dragon Knight", "Dark Knight",
        "Magician", "Wizard (Fire,Poison)", "Mage (Fire,Poison)", "Arch Mage (Fire,Poison)",
        "Wizard (Ice,Lightning)", "Mage (Ice,Lightning)", "Arch Mage (Ice,Lightning)",
        "Cleric", "Priest", "Bishop",
        "Archer", "Hunter", "Ranger", "Bowmaster", "Crossbowman", "Sniper", "Marksman",
        "Rogue", "Assassin", "Hermit", "Night Lord", "Bandit", "Chief Bandit", "Shadower",
        "Pirate", "Brawler", "Marauder", "Buccaneer", "Gunslinger", "Outlaw", "Corsair",
        "Warrior", "Bowman", "Thief"]


def _clean_job(text):
    t = re.sub(r"[^A-Za-z(),/ ]", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 3 else ""


def read_job(bgr, x0, cy, g, debug=None):
    """The job name in the status bar (e.g. 'Bandit'), from a shot where the EXP number's
    first digit is at x0 and its middle at cy, or None if it can't be read clearly.
    debug: a dict to fill with what it looked at ("image", "boxes") and, if it gave up, "why".

    The job sits on the line of the HP bar's label ("HP"), right before it; the character
    name is on the line under it and is never read."""
    ry, rx = max(0, int(cy - 2.5 * g)), max(0, int(x0 - 64 * g))
    reg = bgr[ry:int(cy + 3 * g), rx:max(0, int(x0 - 20 * g))]
    if debug is not None:
        debug.clear()
        debug.update(image=reg, boxes=[], why="")

    def give_up(why):
        if debug is not None:
            debug["why"] = why
        return None

    if reg.size == 0:
        return give_up("the status bar is off screen here")
    white = _whiteness(reg)
    _, blobs = _blobs(white > 0.3)                    # thin letters: a gentle white check
    # The HP bar: 13-16 EXP digits wide, its left end 31-35 of them before the EXP number
    # (the region starts 64 before it). Not to be mixed up with the panels' light borders.
    # Classic World's HP pill can be a bit shorter/narrower vs EXP digits than older clients.
    bars = [b for b in blobs if 7 * g <= b.w <= 22 * g and 1.0 * g <= b.h <= 3.2 * g
            and 20 * g <= b.x <= 42 * g]
    if not bars:
        return give_up("can't find the HP bar next to the class name")
    hp = min(bars, key=lambda b: b.x)                 # the HP bar (the MP bar is right of it)
    if debug is not None:
        debug["boxes"].append((hp.x, hp.y, hp.x + hp.w, hp.y + hp.h, "mesos"))
    label = [b for b in blobs if hp.x - 0.8 * g <= b.x <= hp.x + 3.5 * g
             and b.y + b.h <= hp.y + 0.35 * g and 0.5 * g <= b.h <= 1.8 * g]
    if not label:
        return give_up("can't find the HP label")
    top, bottom = min(b.y for b in label), max(b.y + b.h for b in label)
    row = sorted((b for b in blobs if b.x + b.w <= hp.x - 0.5 * g and b.y >= top - 0.45 * g
                  and b.y + b.h <= bottom + 0.55 * g and b.h >= 0.25 * g), key=lambda b: b.x)
    if len(row) < 2:
        return give_up("no class name found left of the HP bar")
    words = [[row[0]]]
    for b in row[1:]:
        if b.x - max(c.x + c.w for c in words[-1]) > 2.0 * g:
            words.append([b])
        else:
            words[-1].append(b)
    word = words[-1]                                  # the letters right before "HP"
    x_a, x_b = max(0, min(b.x for b in word) - int(0.4 * g)), max(b.x + b.w for b in word) + int(0.4 * g)
    if debug is not None:
        debug["boxes"].append((x_a, top, x_b, bottom, "exp"))
    crop = white[max(0, top - int(0.4 * g)):bottom + int(0.4 * g), x_a:x_b]
    glyph_h = max(b.h for b in word)
    if glyph_h > 10:                                  # big screens: read at small-window size,
        k = 10.0 / glyph_h                            # where Tesseract reads this font best
        crop, glyph_h = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA), 10.0
    votes = _ocr_votes(crop.astype(np.float32), glyph_h, None, _clean_job)
    text, n = votes.most_common(1)[0] if votes else ("", 0)
    if n < 2:
        return give_up("the tries didn't agree on what it says" + (f" (best guess: {text})" if text else ""))
    score, best = max((SequenceMatcher(None, text.lower(), j.lower()).ratio(), j) for j in JOBS)
    if score >= 0.72:
        return best
    if n >= 3:
        return text.title()
    return give_up(f"it reads \"{text}\", which isn't a class it knows")


def _whiteness(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    return np.clip((hsv[..., 2] - 150) / 90, 0, 1) * np.clip((80 - hsv[..., 1]) / 50, 0, 1)


def _ocr_votes(whiteness, glyph_h, chars, clean):
    """Tesseract on white text at a few sizes, with and without smoothing (all in one run).
    Counts answers. chars: the only characters it may answer with, or None."""
    images = []
    for target in (36, 48, 60):
        s = target / glyph_h
        big = cv2.resize(whiteness, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for img in ((big > 0.5) * 255.0, np.clip(big, 0, 1) * 255):
            images.append(cv2.copyMakeBorder((255 - img).astype(np.uint8), 24, 24, 24, 24,
                                             cv2.BORDER_CONSTANT, value=255))
    votes = Counter()
    for text in TESS.read_lines(images, chars):
        text = clean(text)
        if text:
            votes[text] += 1
    return votes


def find_map_box(bgr, gh):
    """(x, y, w, h) of the minimap's info box in a shot of the window's top-left, or None."""
    hue, sat, val = [c.astype(np.int16) for c in cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))]
    box = (abs(hue - 100) <= 8) & (abs(sat - 64) <= 30) & (abs(val - 204) <= 30)
    n, _, stats, _ = cv2.connectedComponentsWithStats(box.astype(np.uint8), connectivity=8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = map(int, stats[i][:4])
    h = min(h, int(round(7.3 * gh)))            # just the info box, not the whole minimap
    return (x, y, w, h) if w >= 10 * gh and h >= 4 * gh else None


_ROMAN = re.compile(r"^(?:X{0,2})(?:IX|IV|V?I{0,3})$")


def _fix_roman(name):
    """A last word that is a Roman numeral misread ("Vil" -> VII, "lV" -> IV, "il" -> II, "V1" -> VI). Plain numbers
    ("Free Market 11") and ordinary words are left alone. The site does the same (server/src/mapnames.js)."""
    def fix(m):
        token = m.group(2)
        if not re.search(r"[IVXivxl|!]", token):
            return m.group(0)                          # only digits: a number
        numeral = re.sub(r"[li1|!]", "I", token, flags=re.I).upper()
        return m.group(1) + numeral if numeral and _ROMAN.match(numeral) else m.group(0)
    return re.sub(r"(\s)([IVXivxl1|!]{1,6})$", fix, name)


def _clean_map_name(text):
    t = text.replace("|", "I")                         # a lone "I" (as in "... I") reads as "|"
    t = re.sub(r"(?<=\s)[!\[\]](?=\s*$)", "I", t)
    t = re.sub(r"[^A-Za-z0-9 '<>:.&()\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    t = _fix_roman(t)                                  # Roman numerals read as l's, 1's, i's ("Vil" is VII)
    t = re.sub(r"(^|\s)[IiLl]st(?=\s|$)", r"\g<1>1st", t)     # "ist Accompaniment" is "1st Accompaniment"
    t = re.sub(r"(^|\s)[Zz]nd(?=\s|$)", r"\g<1>2nd", t)
    t = re.sub(r"(^|\s)[SsBb5]rd(?=\s|$)", r"\g<1>3rd", t)
    return t if len(t) >= 3 else ""


def _map_key(name):
    """Only letters and digits, lower case: names that differ in punctuation are the same map."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = row
    return prev[-1]


class MapList:
    """The game's own map names (assets/maps.txt, from meowdb.com), to spell a map the way the game
    does and to know the towns, where nobody farms. It helps; it isn't a gate: a name that isn't
    in it (an event map, a new map) is used as it was read. The site applies the same rules."""

    def __init__(self, path=MAPS_PATH):
        self.entries, self.by_key = [], {}
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            name, _, town = line.partition("\t")
            style = _clean_map_name(name)               # as the overlay writes names (no commas)
            if style:
                entry = (style, _map_key(name), town.strip() == "1", _numbers(style))
                self.entries.append(entry)
                self.by_key[entry[1]] = entry

    def find(self, read):
        """The list's entry (name, key, is_town, numbers) for a name as read, or None."""
        key = _map_key(read)
        if key in self.by_key:
            return self.by_key[key]
        nums, best, second = _numbers(read), None, float("inf")
        for e in self.entries:
            if e[3] != nums or abs(len(e[1]) - len(key)) > 2:
                continue
            d = _edit_distance(key, e[1])
            if best is None or d < best[0]:
                second = best[0] if best else second
                best = (d, e)
            elif d < second:
                second = d
        # one clear match: a letter misread (two in a long name). Two names about as close, or too far, are left alone.
        allowed = 2 if len(key) >= 24 else 1
        return best[1] if best and best[0] <= allowed and second - best[0] >= 1 else None


_MAP_LIST = None


def map_list():
    global _MAP_LIST
    if _MAP_LIST is None:
        _MAP_LIST = MapList()
    return _MAP_LIST


def snap_map_name(name):
    """The game's spelling of a map name as read (or the name as it is, if it isn't on the list)."""
    e = map_list().find(name)
    return e[0] if e else name


def is_town(name):
    """Is this one of the game's towns? (A name not on the list isn't.)"""
    e = map_list().by_key.get(_map_key(name or ""))
    return bool(e and e[2])


def read_map_name(bgr, gh):
    """The map name (the lower line of white text) from a shot of the minimap's info box."""
    white = _whiteness(bgr[:, int(0.85 * bgr.shape[0]):])      # skip the map's icon
    _, blobs = _blobs(white > 0.5)
    glyphs = sorted((b for b in blobs if 0.3 * gh <= b.h <= 1.6 * gh and b.w <= 2.5 * gh),
                    key=lambda b: b.y + b.h / 2)
    lines = []
    for b in glyphs:
        last = lines[-1][-1] if lines else None
        if last and abs(b.y + b.h / 2 - (last.y + last.h / 2)) <= 0.5 * gh:
            lines[-1].append(b)
        else:
            lines.append([b])
    lines = [ln for ln in lines if len(ln) >= 3]
    if not lines:
        return None
    ln = lines[-1]
    x0, x1 = max(0, min(b.x for b in ln) - 2), max(b.x + b.w for b in ln) + 2
    y0, y1 = max(0, min(b.y for b in ln) - 2), max(b.y + b.h for b in ln) + 2
    votes = _ocr_votes(white[y0:y1, x0:x1], max(b.h for b in ln), None, _clean_map_name)
    name, n = votes.most_common(1)[0] if votes else (None, 0)
    return snap_map_name(name) if n >= 2 else None


# --------------------------------------------------------------------------- mesos and party
#
# The inventory's mesos box (white, gold coin on its left) is found while it's solid, then
# read where it is, even while the window is see-through during fights: the digits stay
# about as much darker than the box as when it's solid. The digits are a different font
# from the EXP bar's, so examples of them are learned from your own screen
# (mesos_digits.npz), each checked by Tesseract first. Until all ten are learned the EXP
# font's digits are used, which match these closely enough.
# Party: the small party list shows one HP bar per member, in an evenly spaced column.

def find_mesos_fields(bgr, gh=None, hsv=None, narrow=False):
    """Every solid white mesos box (gold coin on its left) as (x, y, w, h), biggest first.

    With gh (the EXP digit height), a box must also be the mesos box's size at that scale:
    about 19.1 x 1.82 EXP digits. hsv: the same picture as HSV, if it's at hand already.
    narrow: also the shop's shorter box (about 12 x 1.82 EXP digits)."""
    hue, sat, val = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)
    n, _, stats, _ = cv2.connectedComponentsWithStats(((val > 225) & (sat < 30)).astype(np.uint8),
                                                      connectivity=8)
    coin = (hue >= 12) & (hue <= 35) & (sat >= 100) & (val >= 140)
    found = []
    for i in range(1, n):
        x, y, w, h, area = map(int, stats[i])
        if h < 8 or not (6.4 if narrow else 7) * h <= w <= 16 * h or area < 0.6 * w * h:
            continue
        if gh and not ((11.5 if narrow else 16.2) * gh <= w <= 22 * gh and 1.45 * gh <= h <= 2.2 * gh):
            continue
        left = coin[y:y + h, max(0, x - int(1.6 * h)):x]
        if left.size == 0 or left.mean() < 0.15:
            continue
        found.append((area, (x, y, w, h)))
    return [box for _, box in sorted(found, reverse=True)]


def find_mesos_field(bgr, gh=None):
    """The biggest solid mesos box (see find_mesos_fields), or None."""
    fields = find_mesos_fields(bgr, gh)
    return fields[0] if fields else None


def find_trade_mesos_box(bgr, gh, hsv=None):
    """Your mesos box beside a trade window, as (x, y, w, h), or None.

    The trade's chat panel can cover the box's left end and its coin, so the box is
    picked by its size alone: a white box 13.5 to 22 EXP digits wide (the two small offer
    boxes are about 12.5 wide, the chat input over 26). The right-most one wins."""
    hue, sat, val = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)
    n, _, stats, _ = cv2.connectedComponentsWithStats(((val > 225) & (sat < 30)).astype(np.uint8), connectivity=8)
    found = []
    for i in range(1, n):
        x, y, w, h, area = map(int, stats[i])
        if (13.5 * gh <= w <= 22 * gh and 1.45 * gh <= h <= 2.2 * gh and 7.5 * h <= w <= 12.5 * h
                and area >= 0.6 * w * h):
            found.append((x, y, w, h))
    return max(found) if found else None


MONEY_COLOURS = {"orange": ((6, 150, 170), (16, 255, 255)),        # TAKE OUT, BUY ITEM, TRADE...
                 "green": ((28, 60, 140), (50, 255, 255)),         # EXIT, LEAVE STORE, CANCEL
                 "blue": ((92, 100, 140), (110, 255, 255))}        # ORGANIZE ITEM
MONEY_LOOK_WIDTH = 1600     # the window is looked at no bigger than this (buttons stay 10+ px tall)


def find_money_window(bgr):
    """"shop", "storage" or "trade" when that window is open (told apart by their buttons),
    else None. Mesos move at these windows without being earned.

    Each has a column of same-sized, same-width buttons: a shop's green LEAVE STORE over
    the orange BUY ITEM; storage the same with a blue ORGANIZE ITEM under it; a trade's
    orange TRADE over the green CANCEL. Orange chat lines and lone buttons don't count."""
    scale = min(1.0, MONEY_LOOK_WIDTH / bgr.shape[1])
    if scale < 1:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    kernel = np.ones((3, 3), np.uint8)
    buttons = {}
    for colour, (lo, hi) in MONEY_COLOURS.items():
        mask = cv2.morphologyEx(cv2.inRange(hsv, np.array(lo), np.array(hi)), cv2.MORPH_CLOSE, kernel)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        buttons[colour] = [tuple(map(int, s[:4])) for s in stats[1:n]
                           if s[3] >= 8 and 2.5 * s[3] <= s[2] <= 7 * s[3] and s[4] >= 0.6 * s[2] * s[3]]

    def below(a, b):
        """b is the next button down from a: same left edge and width, a little gap."""
        return (abs(a[0] - b[0]) <= 0.08 * a[2] and abs(a[2] - b[2]) <= 0.1 * a[2]
                and 0 <= b[1] - a[1] - a[3] <= 0.8 * a[3])

    found = set()
    for orange in buttons["orange"]:
        if any(below(g, orange) for g in buttons["green"]):
            found.add("storage" if any(below(orange, b) for b in buttons["blue"]) else "shop")
        elif any(below(orange, g) for g in buttons["green"]):
            found.add("trade")
    return next((kind for kind in ("storage", "shop", "trade") if kind in found), None)


def mesos_layout(bgr, field):
    """Digit cells of the number in the mesos box, or None if they can't be picked out cleanly.

    Works on how much darker each pixel is than the box right around it, so a see-through
    box over a changing background still works. Anything doubtful returns None: a wrong
    number is worse than waiting for a clean look.
    """
    x, y, w, h = field
    inner = bgr[y + 2:y + h - 2, x + 2:x + w - 2]
    if inner.size == 0:
        return None
    V = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    dh0 = 0.61 * h                                   # digits are ~61% as tall as the box
    k = max(3, int(round(0.35 * dh0)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    peak = float(np.percentile(strokes, 99.7))
    if peak < 40:
        return None
    band = np.zeros(strokes.shape, bool)             # the row the digits sit on
    mid = inner.shape[0] / 2
    b0, b1 = max(0, int(mid - 0.65 * dh0)), int(mid + 0.65 * dh0)
    band[b0:b1] = strokes[b0:b1] > 0.4 * peak
    c = max(1, int(round(0.06 * dh0)))
    band = cv2.morphologyEx(band.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((c, c), np.uint8)) > 0
    _, blobs = _blobs(band)
    digits = sorted((b for b in blobs if 0.6 * dh0 <= b.h <= 1.25 * dh0), key=lambda b: -b.x)
    if not digits or inner.shape[1] - (digits[0].x + digits[0].w) > 0.8 * dh0:
        return None                                  # the number is right-aligned
    run, edge = [], None
    for b in digits:
        if edge is not None and edge - (b.x + b.w) > 0.9 * dh0:
            break
        run.append(b)
        edge = b.x if edge is None else min(edge, b.x)
    run.reverse()
    dh = float(np.median([b.h for b in run]))
    pitch = 0.74 * dh
    cells = []
    for b in run:
        n = max(1, int(round((b.w + 0.12 * dh) / pitch)))
        cells.extend((b.x + (j + 0.5) * b.w / n, b.w / n, "digit") for j in range(n))
    # Nothing digit-like may be left over to the left of the number (a dropped digit).
    left = band[:, :max(0, int(cells[0][0] - cells[0][1] / 2 - 0.25 * dh))]
    if left.sum() > 0.25 * band[:, int(cells[-1][0] - cells[-1][1] / 2):].sum():
        return None
    # A comma after every 3 digits from the right makes that gap wider; anything else is off.
    centers = [c[0] for c in cells][::-1]
    for i in range(len(centers) - 1):
        want = 1.11 * dh if (i + 1) % 3 == 0 else 0.74 * dh
        if abs(centers[i] - centers[i + 1] - want) > 0.2 * dh:
            return None
    cy = float(np.median([b.y + b.h / 2 for b in run]))
    return Layout(dh, cy, cells, [], (0, 0, 0, 0), strokes)


# The game right-aligns the number: digit centres sit at these distances from the box's right
# edge, in box heights: first digit, each further digit, and extra room for every comma.
MESOS_FIRST, MESOS_PITCH, MESOS_COMMA = 0.36, 0.447, 0.30


def _mesos_spot(field, i):
    """x of the i-th digit from the right in the mesos box (i = -1: just right of them)."""
    x, y, w, h = field
    return x + w - (MESOS_FIRST + i * MESOS_PITCH + (max(i, 0) // 3) * MESOS_COMMA) * h


def _match_near(templates, lay, cx, h):
    """Match at cx and about a pixel either side. A shifted spot wins only if it scores at
    least 0.02 higher, so well-placed digits read exactly as before."""
    digit, score, lead = templates.match(lay, (cx, 0.4 * h, "digit"))
    best = (digit, score, lead, cx)
    for dx in (-h / 14, h / 14):
        d, s, l = templates.match(lay, (cx + dx, 0.4 * h, "digit"))
        if s >= best[1] + 0.02:
            best = (d, s, l, cx + dx)
    return best


def _mesos_strokes(bgr, field):
    """A Layout for matching digits in the mesos box: how much darker each pixel is than
    the box around it (works solid or see-through)."""
    x, y, w, h = field
    V = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    k = max(3, int(round(0.35 * 0.615 * h)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    return Layout(0.615 * h, y + 0.525 * h, [], [], (0, 0, 0, 0), strokes)


def mesos_box_lines_up(bgr, field, templates):
    """For a box found by its outline: does its right edge line up with the number? The
    first digit must match very clearly (in the wrong place the box's border can pass for
    a '1'), and just right of it there must be no digit (else the box is a digit too far
    left and the number would be read short)."""
    lay, h = _mesos_strokes(bgr, field), field[3]
    _, first, _, _ = _match_near(templates, lay, _mesos_spot(field, 0), h)
    _, after, _, _ = _match_near(templates, lay, _mesos_spot(field, -1), h)
    return first >= 0.85 and after < 0.84


def read_mesos_positions(bgr, field, templates, digit_count=None):
    """Reads the mesos box digit by digit at the spots the game draws them.

    Nothing has to be separated from the background first, so it also works when the
    inventory is see-through over a busy background. Every spot must clearly be a digit
    or clearly not one, and once the number ends none of the next few spots to its left
    may be a clear digit, so a digit lost in a busy background can't silently shorten the
    number from the middle. If digit_count is given, the number must have exactly that many digits (which
    also catches a lost first digit). Returns (number, Layout, weakest score, weakest lead),
    or None.
    """
    x, y, w, h = field
    lay = _mesos_strokes(bgr, field)
    digits, cells, scores, leads, ended = "", [], [], [], 0
    for i in range(16):
        cx = _mesos_spot(field, i)
        if cx - 0.3 * h < x:
            break
        digit, score, lead, cx = _match_near(templates, lay, cx, h)
        empty = bool(digits) and score < 0.65
        if ended:
            # A clear digit after the end means one was missed in between. (Only a clear
            # one: a see-through box's background often looks a little digit-like.)
            if score >= 0.75 and lead >= 0.05:
                return None
            ended += 1
            if ended > 3:
                break
        elif score >= 0.75 and lead >= 0.05:          # clearly a digit
            digits, cells = digit + digits, [(cx, 0.4 * h, "digit")] + cells
            scores.append(score)
            leads.append(lead)
        elif empty:
            ended = 1                                 # the number ends here
        else:
            return None                               # unclear: don't guess
    if not digits or (len(digits) > 1 and digits[0] == "0"):
        return None
    if scores[-1] < 0.85:
        return None          # the first digit must be very clear: a see-through box's background
                             # can pass for an extra '1' in front (real first digits score ~0.95)
    if digit_count and len(digits) != digit_count:
        return None
    return int(digits), lay._replace(exp=cells), min(scores), min(leads)


def read_mesos_digits(lay, templates):
    """The number in the mesos box, or None if any digit is unclear."""
    s = ""
    for cell in lay.exp:
        digit, score, lead = templates.match(lay, cell)
        if score < 0.70 or lead < 0.04:
            return None
        s += digit
    return int(s)


def tesseract_mesos(lay):
    """(number, how many of 6 Tesseract tries agreed) for the mesos box, or (None, 0)."""
    if not tesseract_ok:
        return None, 0
    first, last = lay.exp[0], lay.exp[-1]
    x0, x1 = max(0, int(first[0] - first[1] / 2 - 0.5 * lay.gh)), int(last[0] + last[1] / 2 + 0.5 * lay.gh)
    y0, y1 = max(0, int(lay.cy - 0.8 * lay.gh)), int(lay.cy + 0.8 * lay.gh)
    img = np.clip(lay.V[y0:y1, x0:x1] / max(1.0, float(np.percentile(lay.V, 99.7))), 0, 1)

    def clean(text):
        return re.sub(r"\D", "", text) if re.fullmatch(r"[\d,.\s]+", text.strip() or "x") else ""

    votes = _ocr_votes(img.astype(np.float32), lay.gh, "0123456789,", clean)
    digits, n = votes.most_common(1)[0] if votes else ("", 0)
    return (int(digits), n) if n >= 4 else (None, n)


class MesosDigits:
    """Examples of the mesos box's digits, learned from your own screen as you play."""

    PER_DIGIT = 12

    def __init__(self, path=MESOS_PATH, start=MESOS_START_PATH):
        self.path = path
        self.examples = {}
        self._templates = None
        for source in (path, start):                 # what you've learned, else what comes with it
            try:
                data = np.load(source)
                if int(data["canon"]) == CANON:
                    self.examples = {k[1:]: list(data[k]) for k in data.files if k.startswith("d")}
                    break
            except Exception:
                continue

    @property
    def complete(self):
        return all(str(d) in self.examples for d in range(10))

    def templates(self):
        if self._templates is None and self.examples:
            self._templates = DigitTemplates({d: np.stack(v) for d, v in self.examples.items()})
        return self._templates

    def learn(self, lay, value):
        digits = str(value)
        if len(lay.exp) != len(digits):
            return
        added = False
        for cell, ch in zip(lay.exp, digits):
            examples = self.examples.setdefault(ch, [])
            if len(examples) < self.PER_DIGIT:
                examples.append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
                added = True
        if added:
            self._templates = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                np.savez(self.path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in self.examples.items()})
            except Exception as e:
                log_error("saving the learned mesos digits", e)


def _edges(bgr, hsv=None):
    v = (cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2].astype(np.float32)
    return cv2.magnitude(cv2.Sobel(v, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(v, cv2.CV_32F, 0, 1, ksize=3))


class MesosBoxFinder:
    """Finds the mesos box even while the inventory is see-through.

    It looks for the outline of the whole row (gold coin, box, MESOS label) at the size the
    UI is drawn at, which stays visible when the window is see-through. The outline is kept
    in mesos_box.npz. Work is done at a small scale (EXP digits 12 px tall) to be quick.
    """

    GH = 12.0

    def __init__(self, path=MESOS_BOX_PATH):
        self.edges = None
        try:
            data = np.load(path)
            self.edges, self.mask = data["edges"], data["mask"]
            self.ox, self.oy, self.bw, self.bh = map(float, data["box"])
        except Exception:
            pass

    @classmethod
    def build(cls, bgr, field, gh, path=MESOS_BOX_PATH):
        """Make mesos_box.npz from a screenshot showing the solid box at `field`."""
        x, y, w, h = field
        x0, x1 = int(x - 1.45 * h), int(x + w + 2.7 * h)
        y0, y1 = int(y - 0.35 * h), int(y + 1.35 * h)
        s = cls.GH / gh
        edges = cv2.resize(_edges(bgr[y0:y1, x0:x1]), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        mask = np.ones(edges.shape, np.float32)       # leave out the inside of the box
        mask[int((y - y0 + 0.12 * h) * s):int((y - y0 + 0.88 * h) * s),
             int((x - x0 + 0.12 * h) * s):int((x - x0 + w - 0.12 * h) * s)] = 0
        np.savez(path, edges=edges, mask=mask, box=np.float32([(x - x0) * s, (y - y0) * s, w * s, h * s]))

    def find(self, bgr, gh, hsv=None):
        """(x, y, w, h) of the best-matching mesos box in a screenshot, or None.

        Compared with the EXP digits the box is drawn bigger in small game windows (in real
        screenshots: x1.12 at EXP digits 7 px tall, x1.07 at 11 px, x1.0 at 28 px), so sizes
        around that are tried and the clearest wins. Its left and right edges are then
        lined up with the box's border lines, because the digits are read at fixed
        distances from the right edge."""
        if self.edges is None:
            return None
        edges0 = _edges(bgr, hsv)
        expect = 0.961 + 1.092 / gh
        found = None
        for size in (expect, expect * 1.025, expect / 1.025):
            s = self.GH / (gh * size)
            edges = cv2.resize(edges0, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if edges.shape[0] < self.edges.shape[0] or edges.shape[1] < self.edges.shape[1]:
                continue
            r = np.nan_to_num(cv2.matchTemplate(edges, self.edges, cv2.TM_CCORR_NORMED, mask=self.mask),
                              nan=0.0, posinf=0.0, neginf=0.0)
            r[np.abs(r) > 1.0] = 0.0                   # flat areas (e.g. all black) give junk values
            _, best, _, (px, py) = cv2.minMaxLoc(r)
            r[max(0, py - 3):py + 4, max(0, px - 12):px + 13] = 0
            _, second, _, _ = cv2.minMaxLoc(r)
            enough = 0.72 if gh >= 10 else 0.64       # tiny windows match less sharply
            if best >= enough and best - second >= 0.05 and (found is None or best > found[0]):
                found = (best, (int((px + self.ox) / s), int((py + self.oy) / s),
                                int(self.bw / s), int(self.bh / s)))
        if not found:
            return None                                # not clearly there
        x, y, w, h = found[1]
        # Line up the right edge (the digits are read from there) with the inside of the
        # box's border: leaving the box it gets darker, solid or see-through.
        gx = cv2.Sobel((cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2].astype(np.float32),
                       cv2.CV_32F, 1, 0, ksize=3)
        rows = gx[max(0, y + h // 4):y + 3 * h // 4 + 1].sum(axis=0)
        reach = max(2, int(0.25 * h))
        lo, hi = max(0, x + w - reach), min(len(rows), x + w + reach + 1)
        right = lo + int(np.argmin(rows[lo:hi])) if hi - lo >= 3 else x + w
        return right - w, y, w, h


def _agrees(value, last):
    """Two mesos readings that could both be right: same number of digits and close together."""
    return last is not None and len(str(value)) == len(str(last)) and abs(value - last) <= max(1000, 0.02 * last)


def find_party_bars(bgr, gh, hsv=None):
    """HP bars of the party list's members: the longest evenly spaced column of identical
    HP-bar outlines. A single bar (or none) means you're on your own.

    Classic World's party HP pills are shorter/narrower vs EXP digit height than older
    clients, so size and row pitch accept a wider range (still a vertical column of
    matching tracks, not any red blob on screen).
    """
    val = (cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2]
    n, _, stats, _ = cv2.connectedComponentsWithStats((val < 70).astype(np.uint8), connectivity=8)
    bars = sorted((tuple(map(int, stats[i][:4])) for i in range(1, n)
                   if 3.5 * gh <= stats[i][2] <= 12.0 * gh and 0.55 * gh <= stats[i][3] <= 2.2 * gh
                   and stats[i][2] >= 2.5 * stats[i][3]),
                  key=lambda b: b[1])
    best = []
    for i, first in enumerate(bars):
        same = [b for b in bars[i:] if abs(b[0] - first[0]) <= 0.8 * gh
                and abs(b[2] - first[2]) <= 1.0 * gh and abs(b[3] - first[3]) <= 0.5 * gh]
        column, pitch = [first], None
        for b in same[1:]:
            step = b[1] - column[-1][1]
            if pitch is None and 1.0 * gh <= step <= 3.5 * gh:
                pitch = step
            if pitch is None or abs(step - pitch) > 0.45 * gh:
                break
            column.append(b)
        if len(column) > len(best):
            best = column
    return best if len(best) >= 2 else best[:1]


# --------------------------------------------------------------------------- finding it on screen

Reading = namedtuple("Reading", "where candidates confident text image boxes map note "
                                 "mesos mesos_image mesos_boxes party mesos_at window bar percent_only level exp_kind tries ms shadow v2_at "
                                 "notices",
                     defaults=((), False, "", None, (), None, None, None, None, (), None, None, None, None, False, None,
                               None, (), None, None, None, None))
# percent_only: the bar shows just "[58.47%]" (candidates are then [(None, pct)]); level: the level read
# from the status bar's LV badge, with where it came from, as (level, source), or None
# exp_kind, tries and ms are for health reports (healthlog.py): how the EXP look went (read, tesseract, unclear,
# not_found, covered or no_window), which other readers tried during the look as (part, worked) pairs, and
# how long the look took in milliseconds
# shadow and v2_at are for shadow mode (shadow.py): (what happened, v2's milliseconds), and (key, box) when reader v2
# found the bar in a new place, to remember in the config
# notices: OCR lines from the right-side battle/loot strip when meso_mode is "notices" (None = not read this look)


def _view(img, lay):
    """Crop around the EXP text and the digit boxes in it, for View capture."""
    g = lay.gh
    x0, x1 = max(0, int(lay.box[0] - 2 * g)), min(img.shape[1], int(lay.box[2] + g))
    y0, y1 = max(0, int(lay.cy - 1.2 * g)), min(img.shape[0], int(lay.cy + 1.2 * g))
    boxes = [(c[0] - c[1] / 2 - x0, lay.cy - g / 2 - y0, c[0] + c[1] / 2 - x0, lay.cy + g / 2 - y0, kind)
             for kind, cells in (("exp", lay.exp), ("pct", _pct_cells(lay))) for c in cells]
    return img[y0:y1, x0:x1].copy(), boxes


def _problem(where, text):
    """A status message about reading the EXP bar ("where" only matters for a selected area)."""
    return f"Selected area: {text}" if where == "Selected area" else text


class ScreenReader:
    """Finds the EXP text on screen and reads it, following the game window around.

    It also watches the minimap so the map name is read again whenever it changes, reads
    the inventory's mesos and, every few seconds, counts the members in the party list.
    """

    def __init__(self, templates, mode="window", region=None):
        self.templates = templates
        self.mode, self.region = mode, region
        self.hwnd = None
        self.size = None
        self.box = None             # where the EXP text is, relative to the game window
        self.misses = 0
        self.last_full_search = 0.0
        self.gh = None              # EXP digit height: how big the game's UI is drawn
        self.map_box = None         # the minimap's info box, relative to the game window
        self.map_print = None       # rough picture of its text, to notice map changes
        self.map_unread = False
        self.next_map_search = 0.0
        self.next_map_read = 0.0
        self.mesos_box = None       # the inventory's mesos box, relative to the game window
        self.mesos_provisional = False  # True when mesos_box was only scaled after a resize (not saved yet)
        self.saved_mesos = None     # (window w, h, x, y, w, h) remembered from last time (legacy)
        self.saved_mesos_boxes = {} # {"WxH": (x, y, w, h)} remembered for each window size
        self.box_finder = MesosBoxFinder()
        self.mesos_digits = MesosDigits()
        self.mesos_checked = None   # last mesos number Tesseract confirmed
        self.mesos_count = None     # how many digits the mesos number has
        self.mesos_pending = (None, 0)
        self.mesos_fails = 0
        self.next_mesos_check = 0.0
        self.mesos_covered = False  # this app's window is in the way of the mesos
        self.notices_covered = False  # ...or of the right-side gain notices
        self.notices_look = None      # {"image", "text"} from the last notice strip OCR
        self.next_notices = 0.0       # earliest time to OCR the notice strip again
        self.meso_mode = "notices"    # "notices" or "inventory" (set from Settings)
        self.capture = ScreenCapture(None)  # where the pictures come from (set at each look)
        self.two_boxes = False      # a storage / trade window shows a second mesos box
        self.window = None          # "shop", "storage" or "trade" while one is open (or just closed)
        self.window_until = 0.0     # ...and until when it still counts
        self.window_box = False     # the mesos box being read belongs to such a window (not the inventory)
        self.potion_table = self.potion_digits = None    # loaded at the first look at the potion bar
        self.next_bar, self.bar_reads, self.bar_shift = 0.0, 0, (0, 0)
        self.next_outline = 0.0     # the earliest time to search for a see-through mesos box again
        self.party_area = None      # where the party list was last seen, on screen
        self.next_scan = 0.0
        self.own_rect = None        # this app's window, which screenshots can't see through
        self.look_again_requested = False
        self.exp_anchor = None      # where the EXP number starts, relative to the game window
        self.job = None             # the class shown in the status bar
        self.job_look = None        # what the last try at reading it saw: {"image", "boxes", "why"}
        self.map_look = None        # the same for the map name: {"image", "text"}
        self.map_seen_at = None     # when the minimap's name box was last seen (so a map change would have shown)
        self.last_rect = None       # where the game window was at the last look (x, y, w, h)
        self.next_job_read = 0.0
        self.level = None           # the level read from the status bar's badge (two captures agreed)
        self.level_look = None      # what the last try at reading it saw: {"image", "votes", "why"}
        self.level_failures = 0     # badge reads in a row that gave nothing
        self.badge_covered = False  # the last badge look found a window over it
        self.next_level_read = 0.0
        self._level_candidate = None    # a level seen once, waiting for a second capture to agree
        self.name_print = None      # a small picture of the character's name row (see level_ocr.name_print); never read as text
        self.next_name_read = 0.0
        self._tries = []                    # (part, worked) for the readers that tried during this look
        self.part_errors = Counter()        # how often each part of the reading failed (by part name)
        self.error_types = Counter()        # ...and by exception type name (never the message)
        self.part_failed_at = {}            # part -> when it last failed, until a later try works
        self._part_logged = {}              # part -> (when errors.log last heard of it, failures skipped since)
        self.shadow = shadow.Shadow(find_exp_v2, digits_v2.read_layout_v2, fits_exp_table, exp_font.load, _view,
                                    lambda exc: self._part_failed("reader v2", exc))      # reader v2, beside this one
        self.saved_v2 = {}                  # "WxH/gh" -> where reader v2 found the bar last time (from the config)
        self.v2_spot = None                 # ...and where it has found it in this game window (x0, y0, x1, y1)

    @contextlib.contextmanager
    def _part(self, part):
        """One part of a look (map name, mesos, ...): if it raises, that part counts as not read this look
        and the others carry on. A part that really ran and worked says so with _part_ok (one that wasn't due
        yet proves nothing, so a failure's status note stays its PART_NOTE_SECONDS)."""
        try:
            yield
        except Exception as e:
            self._part_failed(part, e)
            if part in TRIED_PARTS:
                self._tried(TRIED_PARTS[part], False)

    def _part_ok(self, part):
        self.part_failed_at.pop(part, None)

    def _tried(self, part, worked):
        """Notes that a reader tried during this look (map, class, level, mesos, potions), for health reports."""
        self._tries.append((part, bool(worked)))

    def _part_failed(self, part, exc):
        now = time.monotonic()
        self.part_errors[part] += 1
        self.error_types[type(exc).__name__] += 1
        self.part_failed_at[part] = now
        last, skipped = self._part_logged.get(part, (None, 0))
        if last is not None and now - last < PART_LOG_SECONDS:
            self._part_logged[part] = (last, skipped + 1)           # (it would fill errors.log at every look)
            return
        self._part_logged[part] = (now, 0)
        more = f" ({skipped} more failures skipped since the last entry)" if skipped else ""
        log_error(f"reading the {part} failed{more}", exc)

    def _with_part_notes(self, reading):
        now = time.monotonic()
        said = [f"{PART_NOTES[p]} (details in errors.log)." for p, t in self.part_failed_at.items()
                if p in PART_NOTES and now - t < PART_NOTE_SECONDS]
        if not said:
            return reading
        return reading._replace(note=" ".join(([reading.note] if reading.note else []) + said))

    def use_window(self):
        self.mode, self.hwnd, self.box = "window", None, None
        self.forget_level()

    def use_region(self, region):
        self.mode, self.region = "region", region
        self.forget_level()

    def forget_level(self):
        """The badge level read so far belongs to what was looked at before (another place, character or reset)."""
        self.level, self.level_failures, self._level_candidate = None, 0, None
        self.recheck_level()

    def request_map(self):
        """Read the map name on the next look, even if it doesn't seem to have changed."""
        self.next_map_search, self.next_map_read, self.map_unread = 0.0, 0.0, True

    def look_again(self):
        """Forget where everything was (EXP bar, minimap, inventory, party list) and look
        for it all again. Done by the reading thread itself, at its next look."""
        self.look_again_requested = True

    def _forget_places(self):
        self.box, self.misses = None, 0
        self.v2_spot = None
        self.map_box, self.map_print = None, None
        self.mesos_box = self.saved_mesos = None
        self.mesos_provisional = False
        self.saved_mesos_boxes = {}
        self.mesos_fails, self.mesos_count, self.mesos_pending = 0, None, (None, 0)
        self.mesos_covered, self.notices_covered, self.notices_look = False, False, None
        self.next_notices, self.party_area, self.next_scan = 0.0, None, 0.0
        self.two_boxes, self.next_outline = False, 0.0
        self.window, self.window_until, self.window_box = None, 0.0, False
        self.exp_anchor, self.next_job_read = None, 0.0
        self.next_level_read, self._level_candidate = 0.0, None
        self.request_map()

    def read(self, capture):
        capture = self.capture = as_capture(capture)
        capture.begin_look()
        try:
            return self._look(capture)
        finally:
            capture.end_look()

    def _look(self, capture):
        self._tries = []
        if self.look_again_requested:
            self.look_again_requested = False
            self._forget_places()
        rect = self._game_rect()
        self.last_rect = rect
        if self.mode == "region":
            reading = self._read_region(capture, rect)
        elif not self.hwnd and PLATFORM.missing_permission():
            return Reading(PLATFORM.missing_permission(), exp_kind="no_window")
        elif not self.hwnd:
            return Reading("MapleStory window not found. Is the game running?", exp_kind="no_window")
        elif not rect:
            return Reading("MapleStory is minimized. Bring the game back up.", exp_kind="no_window")
        else:
            reading = self._read_window(capture, rect)
        if reading.percent_only and self.mode == "region":
            # (a selected area has no game window around it: no badge to find, so typing the level in is open at once)
            self.level_failures = max(self.level_failures, LEVEL_FAILS_FOR_FALLBACK)
            self.level_look = {"image": None, "votes": {}, "why": "Selected area: the level badge isn't in it"}
        if rect and self.gh:
            name = note = None
            with self._part("map name"):
                name, note = self._read_info(capture, rect)
            reading = self._read_extras(capture, rect, reading._replace(map=name, note=note))
            if self.exp_anchor and self.mode == "window":
                with self._part("class"):
                    self._read_job(capture, rect)
                with self._part("name print"):
                    self._look_at_name(capture, rect)
                if reading.percent_only:        # no EXP number to tell the level from: the badge has to
                    with self._part("level"):
                        self._read_level(capture, rect)
        return self._with_part_notes(reading._replace(
            level=(self.level, "badge") if self.level and reading.percent_only and self.mode == "window" else None,
            tries=tuple(self._tries)))

    def map_blind_seconds(self):
        """How long the minimap's name box hasn't been visible (0 if it never was: no map to lose)."""
        return 0.0 if self.map_seen_at is None else time.monotonic() - self.map_seen_at

    def diagnostics(self):
        """What this reader knows about where things are, for bug reports (a plain dict)."""
        rect, g = self.last_rect, self.gh
        covering = []
        if rect and g:
            wx, wy = rect[0], rect[1]
            if self.box and self._covered(wx + self.box[0], wy + self.box[1], self.box[2], self.box[3]):
                covering.append("EXP bar")
            if self.mesos_box and self._covered(wx + self.mesos_box[0], wy + self.mesos_box[1], self.mesos_box[2],
                                                self.mesos_box[3], margin=max(2, int(0.3 * self.mesos_box[3]))):
                covering.append("mesos box")
            if self.map_box and self._covered(wx + self.map_box[0], wy + self.map_box[1], self.map_box[2], self.map_box[3]):
                covering.append("minimap name")
            if self.party_area and self._covered(*self.party_area):
                covering.append("party list")
            if self.exp_anchor:
                ax, ay = self.exp_anchor
                if self._covered(wx + int(ax - 65 * g), wy + int(ay - 3 * g), int(46 * g), int(6.5 * g)):
                    covering.append("class name")
        return dict(mode=self.mode, found=bool(self.hwnd), rect=rect, gh=g, region=self.region, capture=self.capture.name,
                    own_rect=self.own_rect, covering=covering, exp_box=self.box, exp_misses=self.misses,
                    map_box=self.map_box, map_unread=self.map_unread, mesos_box=self.mesos_box,
                    mesos_fails=self.mesos_fails, two_boxes=self.two_boxes, mesos_learned=self.mesos_digits.complete,
                    party_area=self.party_area, job_why=(self.job_look or {}).get("why") or "",
                    part_errors=dict(self.part_errors), error_types=dict(self.error_types))

    def _read_job(self, capture, rect):
        """Reads the class from the status bar now and then (it only changes at a job advance)."""
        capture = self.capture = as_capture(capture)
        now = time.monotonic()
        if not tesseract_ok:
            self.job_look = {"image": None, "boxes": [], "why": tesseract_note("The class")}
            return
        if now < self.next_job_read:
            return
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 65 * g), wy + int(ay - 3 * g), int(46 * g), int(6.5 * g)
        if self._blocked(x0, y0, w, h):
            self.next_job_read = now + 5
            self.job_look = {"image": None, "boxes": [], "why": "this window is covering the class name: move it"}
            return
        shot = capture.grab(x0, y0, w, h)
        look = {}
        self.next_job_read = now + 10         # (kept if the read raises: it isn't started again at every look)
        job = read_job(shot[0], wx + ax - shot[1], wy + ay - shot[2], g, look) if shot else None
        self._part_ok("class")
        self.job_look = look or None
        self._tried("class", job)
        if job:
            self.job = job
        self.next_job_read = now + (60 if job else 10)

    def _look_at_name(self, capture, rect):
        """Now and then, a print of the name row: how another character is told from this one (the level
        read for one isn't another's). It is only a picture, kept in memory: the name isn't read."""
        capture = self.capture = as_capture(capture)
        now = time.monotonic()
        if now < self.next_name_read:
            return
        self.next_name_read = now + 5
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 50.5 * g) - 2, wy + int(ay + 0.9 * g) - 2, int(12.5 * g) + 5, int(1.7 * g) + 5
        if self._blocked(x0, y0, w, h):
            self.name_print = None
            return
        shot = capture.grab(x0, y0, w, h)
        self.name_print = level_ocr.name_print(shot[0], wx + ax - shot[1], wy + ay - shot[2], g) if shot else None
        self._part_ok("name print")

    def recheck_level(self):
        """Read the badge at the next look (the % wrapped: the level may have gone up). The level known so
        far stays until a new one has been seen twice."""
        self.next_level_read, self._level_candidate = 0.0, None

    def _read_level(self, capture, rect):
        """Reads the level from the badge: a level counts once two looks in a row agree. A failing read
        backs off (so a covered or unreadable badge doesn't keep starting Tesseract); a known level is
        looked at again only now and then."""
        capture = self.capture = as_capture(capture)
        now = time.monotonic()
        if now < self.next_level_read:
            return
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 72 * g), wy + int(ay - 1.6 * g), int(34 * g) + 1, int(5.2 * g) + 1
        if not tesseract_ok or TESS is None:      # nothing to wait for: typing the level in is open at once
            self.level_failures = max(self.level_failures, LEVEL_FAILS_FOR_FALLBACK)
            self.level_look = {"image": None, "votes": {}, "why": tesseract_note("The level badge") or "Tesseract isn't available"}
            self.next_level_read = now + 60
            return
        if self._blocked(x0, y0, w, h):
            self.level_look = {"image": None, "votes": {}, "why": "this window is covering the level badge: move it"}
            self.badge_covered = True
            self.level_failures += 1            # (a badge that stays out of sight opens the Level fallback like one that can't be read)
            self.next_level_read = now + 5
            return
        self.badge_covered = False
        shot = capture.grab(x0, y0, w, h)
        look = {}
        try:
            seen = level_ocr.read_level(shot[0], wx + ax - shot[1], wy + ay - shot[2], g, TESS, look) if shot else None
        except Exception as e:              # (Tesseract failing or timing out, or anything unexpected: not a level, back off)
            look["why"] = f"the level read failed: {e}"
            self._part_failed("level", e)
            seen = None
        else:
            self._part_ok("level")
        self.level_look = look
        self._tried("level", seen is not None)
        if seen is None:
            self.level_failures += 1
            self._level_candidate = None
            self.next_level_read = now + (2 if self.level_failures < LEVEL_FAILS_FOR_FALLBACK
                                         else 5 if self.level_failures < 2 * LEVEL_FAILS_FOR_FALLBACK else 30)
        elif seen == self._level_candidate:       # the second look agrees
            self.level, self.level_failures, self._level_candidate = seen, 0, None
            self.next_level_read = now + LEVEL_RECHECK_SECONDS
        else:
            self._level_candidate, self.level_failures = seen, 0
            self.next_level_read = now + 1.5

    def _mesos_for_size(self, size):
        """The mesos box last known for this window size, or None."""
        key = f"{size[0]}x{size[1]}"
        box = self.saved_mesos_boxes.get(key)
        if box:
            return tuple(box)
        saved = self.saved_mesos
        if saved and tuple(saved[:2]) == tuple(size):
            return tuple(saved[2:])
        return None

    def _game_rect(self):
        if not (self.hwnd and PLATFORM.window_exists(self.hwnd)):
            self.hwnd, self.box, self.map_box = PLATFORM.find_game_window(), None, None
        rect = PLATFORM.client_rect(self.hwnd) if self.hwnd else None
        if rect and rect[2:] != self.size:                  # resized: remap what we can, find the rest again
            old_size, old_mesos, old_exp = self.size, self.mesos_box, self.box
            self.size, self.map_box, self.gh = rect[2:], None, None
            self.v2_spot = None
            self.exp_anchor, self.next_job_read = None, 0.0
            self.party_area, self.next_scan = None, 0.0
            self.window, self.window_until, self.window_box = None, 0.0, False
            # Prefer the box remembered for this exact size; otherwise scale the last one so
            # tracking doesn't start over from scratch. A scaled guess is provisional: it is
            # used for reading, but not saved until a clean mesos look confirms it.
            known = self._mesos_for_size(rect[2:])
            if known:
                self.mesos_box, self.mesos_provisional = known, False
            elif old_mesos and old_size:
                self.mesos_box = scale_box(old_mesos, old_size, rect[2:])
                self.mesos_provisional = self.mesos_box is not None
            else:
                self.mesos_box, self.mesos_provisional = None, False
            self.box = scale_box(old_exp, old_size, rect[2:]) if old_exp and old_size else None
            self.mesos_fails = 0
        return rect

    def _hide_own(self, shot, screen_copy=False):
        """The screenshot with this app's window blacked out, so it can never read itself. (A capture that
        sees through windows has nothing to hide; screen_copy=True is for a picture taken from the screen.)"""
        r = self.own_rect
        if not shot or not r or (self.capture.sees_through_windows and not screen_copy):
            return shot
        img, sx, sy = shot
        x0, y0 = max(0, r[0] - sx), max(0, r[1] - sy)
        x1, y1 = min(img.shape[1], r[0] + r[2] - sx), min(img.shape[0], r[1] + r[3] - sy)
        if x0 < x1 and y0 < y1:
            img = img.copy()
            img[y0:y1, x0:x1] = 0
        return img, sx, sy

    def _covered(self, x, y, w, h, margin=0):
        """Does this app's window overlap (or come within margin px of) the given area?"""
        r = self.own_rect
        return bool(r) and (x - margin < r[0] + r[2] and r[0] < x + w + margin
                            and y - margin < r[1] + r[3] and r[1] < y + h + margin)

    def _blocked(self, x, y, w, h, margin=0):
        """Is this area unreadable because this app's window is on top of it? (Never, if the capture sees
        through windows; _covered is the plain geometry.)"""
        return self._covered(x, y, w, h, margin) and not self.capture.sees_through_windows

    def _remember(self, lay, rect, shot=None):
        if lay and rect:
            self.gh = lay.gh                # tells the minimap reader how big the UI is
            if shot:                        # where the EXP number starts: the class is left of it
                self.exp_anchor = (shot[1] - rect[0] + lay.box[0], shot[2] - rect[1] + lay.cy)

    def _read_region(self, capture, rect):
        capture = self.capture = as_capture(capture)
        r = self.region
        if not r:
            return Reading("No area selected. Click Select area.", exp_kind="no_window")
        shot = self._hide_own(capture.grab(r["left"], r["top"], r["width"], r["height"]))
        reading, lay = self._read(shot, "Selected area")
        if reading.confident:
            self._remember(lay, rect)
        return reading

    def _read_window(self, capture, rect):
        capture = self.capture = as_capture(capture)
        wx, wy, ww, wh = rect
        where = f"MapleStory window {ww}x{wh}"
        started = time.perf_counter()
        if self.box:
            bx, by, bw, bh = self.box
            if self._blocked(wx + bx, wy + by, bw, bh):
                return Reading("This window is covering the EXP bar. Move it.", exp_kind="covered")
            shot = capture.grab(wx + bx, wy + by, bw, bh)
            reading, lay = self._read(shot, where)
            if reading.confident or self.misses < 2:        # (the 3rd miss searches again below: that look is the one handed on)
                reading = self._shadow_look(capture, rect, shot, reading, lay, started)
            if reading.confident:
                self.misses = 0
                self._remember(lay, rect, shot)
                return reading
            # Not reading cleanly here (a dialog in the way, or this was the wrong spot):
            # after a few tries, look for the EXP text again.
            self.misses += 1
            if self.misses < 3:
                return reading
            self.box = None

        # Search the bottom of the window (and now and then the whole window).
        top = int(wh * (1 - SEARCH_STRIP))
        shot = self._hide_own(capture.grab(wx, wy + top, ww, wh - top))
        lay = find_exp(shot[0]) if shot else None
        if not lay and time.monotonic() - self.last_full_search > 10:
            self.last_full_search = time.monotonic()
            shot = self._hide_own(capture.grab(wx, wy, ww, wh))
            lay = find_exp(shot[0]) if shot else None
        if not lay:
            if self._blocked(wx, wy + top, ww, wh - top):
                reading = Reading("Can't find the EXP bar. Is this window covering it?", exp_kind="covered")
            else:
                reading = Reading("Can't find the EXP bar at the bottom of the game. Is something covering it?",
                                  exp_kind="not_found")
            return self._shadow_look(capture, rect, shot, reading, None, started)     # (v2 may still see it)
        img, sx, sy = shot
        g = lay.gh
        lead = g if lay.exp else 5.5 * g       # (a %-only bar is found by its label, so the label stays in the spot)
        x0, x1 = max(0, int(lay.box[0] - lead)), min(img.shape[1], int(lay.box[2] + 6 * g))
        y0, y1 = max(0, int(lay.cy - 1.4 * g)), min(img.shape[0], int(lay.cy + 1.4 * g))
        reading, lay = self._read(shot, where, lay)
        reading = self._shadow_look(capture, rect, shot, reading, lay, started)
        if reading.confident:             # only remember a spot whose digits match the game font
            self.box = (sx - wx + x0, sy - wy + y0, x1 - x0, y1 - y0)
            self.misses = 0
            self._remember(lay, rect, shot)
        return reading

    def _shadow_look(self, capture, rect, shot, reading, lay, started):
        """Runs reader v2 on the picture just read and notes in the Reading how the two compare (shadow.py).
        It only adds to the Reading's shadow and v2_at, never changes what was read: anything going wrong here
        leaves the Reading exactly as it was."""
        try:
            return self._shadow_look_v2(capture, rect, shot, reading, lay, started)
        except Exception as e:
            self.shadow.failed(e)           # (counted like any v2 error, and v2 rests a minute)
            return reading._replace(shadow=("errors", None))

    def _today_answer(self, img):
        """Today's reader (templates, no Tesseract) on another picture: only to tell a v2 disagreement from EXP that
        changed between two screen grabs."""
        lay = find_exp(img)
        got = read_layout(self.templates, lay) if lay and self.templates else None
        if got and got[0] is not None and not fits_exp_table(*got):
            return None
        return got

    def _shadow_look_v2(self, capture, rect, shot, reading, lay, started):
        if not shot or self.shadow.idle():
            return reading
        wx, wy, ww, wh = rect
        old_img, ox, oy = shot[0], shot[1] - wx, shot[2] - wy      # where this picture sits in the game window
        img = old_img
        key = f"{ww}x{wh}/{round(lay.gh) if lay else round(self.gh or 0)}"
        spot = self.v2_spot or self.saved_v2.get(key)
        hint, own = None, None
        where = {"ox": ox, "oy": oy}                # where the picture v2 reads sits in the game window
        if spot:
            x0, y0, x1, y1 = max(0, spot[0]), max(0, spot[1]), min(ww, spot[2]), min(wh, spot[3])
            if x1 - x0 > 8 and y1 - y0 > 8:
                if ox <= x0 and oy <= y0 and x1 <= ox + img.shape[1] and y1 <= oy + img.shape[0]:
                    hint = (x0 - ox, y0 - oy, x1 - ox, y1 - oy)       # the picture holds its spot
                elif not self._blocked(wx + x0, wy + y0, x1 - x0, y1 - y0):
                    def own():                      # (a small crop of its own, grabbed only if v2 really runs)
                        pic = self._hide_own(capture.grab(wx + x0, wy + y0, x1 - x0, y1 - y0))
                        if not pic:
                            return None
                        self.shadow.counts["grabs"] += 1
                        where["ox"], where["oy"] = pic[1] - wx, pic[2] - wy
                        return pic[0]
        top = int(wh * (1 - SEARCH_STRIP))

        def strip():                                # (the bottom strip, if the picture didn't hold the bar)
            if self._blocked(wx, wy + top, ww, wh - top):
                return None
            big = self._hide_own(capture.grab(wx, wy + top, ww, wh - top))
            return (big[0], big[1] - wx - where["ox"], big[2] - wy - where["oy"]) if big else None

        old = reading.candidates[0] if reading.confident and reading.candidates else None
        small = own or img.shape[0] < wh - top or img.shape[1] < ww       # (a picture that already is the strip needs no other)
        res = self.shadow.look(img, old, (time.perf_counter() - started) * 1000, lay, hint, strip if small else None, old_img, own,
                                searching=lay is None, recheck=self._today_answer)
        ox, oy = where["ox"], where["oy"]
        if res is None:
            return reading
        at = None
        if res.spot:
            box = (res.spot[0] + ox, res.spot[1] + oy, res.spot[2] + ox, res.spot[3] + oy)
            if self.v2_spot is None or max(abs(a - b) for a, b in zip(box, self.v2_spot)) > 3:
                self.v2_spot = box
                at = (key, list(box))
        return reading._replace(shadow=(res.category, res.ms), v2_at=at)

    def _read(self, shot, where, lay=None):
        """Returns (Reading, Layout or None). Its exp_kind (for health reports): read = the templates gave a
        number that fits (or a clear percentage); tesseract = only Tesseract's candidate is left; unclear = the
        bar is there but nothing can be trusted; not_found = no bar (or off screen)."""
        if not shot:
            return Reading(_problem(where, "The EXP bar is off screen."), exp_kind="not_found"), None
        img = shot[0]
        lay = lay or find_exp(img)
        if not lay:
            return Reading(_problem(where, "Can't find the EXP bar."), exp_kind="not_found"), None
        view, boxes = _view(img, lay)
        got = read_layout(self.templates, lay) if self.templates else None
        if not lay.exp:                       # only "[58.47%]": the level has to come from elsewhere
            if got:
                return Reading(where, [got], True, f"[{got[1]:.2f}%] (percentage only)", view, boxes,
                               percent_only=True, exp_kind="read"), lay
            return Reading(_problem(where, "Found the EXP bar, but can't read its percentage clearly."),
                           image=view, boxes=boxes, percent_only=True, exp_kind="unclear"), lay
        if got and fits_exp_table(*got):
            return Reading(where, [got], True, f"{got[0]:,} [{got[1]:.2f}%]", view, boxes, exp_kind="read"), lay
        # Unclear digits, or a reading that fits no level: not trusted. Readings that do fit
        # a level go first, so the tracker tries those.
        cands = [got] if got else []
        with self._part("Tesseract"):           # (the backup failing leaves the template reading standing)
            cands += [c for c in tesseract_candidates(img, lay) if c != got]
            self._part_ok("Tesseract")
        cands.sort(key=lambda c: not fits_exp_table(*c))
        if cands:
            e, p = cands[0]
            how = "" if (e, p) == got else " (Tesseract)"
            return Reading(where, cands, False, f"{e:,} [{p:.2f}%]{how}", view, boxes,
                           exp_kind="tesseract" if how else "unclear"), lay
        return Reading(_problem(where, "Found the EXP bar, but can't read its numbers clearly."),
                       image=view, boxes=boxes, exp_kind="unclear"), lay

    def _read_extras(self, capture, rect, reading):
        """Adds the mesos count and, every few seconds, the party size to a reading."""
        capture = self.capture = as_capture(capture)
        wx, wy, ww, wh = rect
        now, g = time.monotonic(), self.gh
        notes = [reading.note] if reading.note else []
        party = moved = None
        if self.window and now > self.window_until:   # the window closed: its box went with it
            self.window = None
            if self.window_box:
                self.mesos_box, self.window_box = None, False
                self.mesos_fails, self.next_outline = 0, 0.0
        if now >= self.next_scan:                     # look over the whole window
            self.next_scan = now + (SCAN_SECONDS if self.mesos_box else 2)
            with self._part("window scan"):
                shot = self._hide_own(capture.grab(wx, wy, ww, wh))
                if shot:
                    img, sx, sy = shot
                    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)      # (made once for everything below)
                    window = find_money_window(img)                 # (shop, storage or trade)
                    if window:
                        self.window, self.window_until = window, now + WINDOW_GRACE
                    if self.window:
                        self.next_scan = now + WINDOW_SCAN_SECONDS  # mesos change here: look often
                    fields = find_mesos_fields(img, g, hsv, narrow=window == "shop")   # the solid box is easy to spot
                    if window == "trade" and not fields:            # its chat panel may cover the coin
                        box = find_trade_mesos_box(img, g, hsv)
                        fields = [box] if box else []
                    if window == "storage" and len(fields) == 2:
                        fields = [max(fields)]                      # yours is the right-hand one
                        self.two_boxes = False
                    elif window == "shop":
                        self.two_boxes = False                      # (both would be yours: the first will do)
                    else:
                        # A storage / trade window outside those cases shows a second mesos box next to
                        # yours, and which is which can't be told: read neither until it's closed.
                        self.two_boxes = len(fields) > 1
                        if window == "storage":
                            fields = []
                    field = None if self.two_boxes else (fields[0] if fields else None)
                    if field and window == "shop" and field[2] < 16 * g:
                        # (its digits end a little further from the box's right edge)
                        field = (field[0], field[1], field[2] - round(0.12 * field[3]), field[3])
                    if field and not self._readable(img, field):
                        field = None                      # a white patch in the scene, not the box
                    # Look for it by its outline too if it's lost, or this window is in the way of
                    # where it was (it may have been moved from there).
                    if (field is None and not self.two_boxes and now >= self.next_outline
                            and (self.mesos_box is None or self.mesos_fails >= 10 or self.mesos_covered)):
                        field = self._find_seethrough_box(img, g, hsv)
                        if field is None:
                            self.next_outline = now + OUTLINE_SECONDS   # (it costs a lot on a big screen)
                    if self.two_boxes:
                        self.mesos_box = None
                    if window and not field:
                        self.mesos_box = None                       # (the old spot isn't yours here)
                    if field:
                        box = (sx - wx + field[0], sy - wy + field[1], field[2], field[3])
                        old = self.mesos_box
                        if old is None or max(abs(a - b) for a, b in zip(box, old)) > 3:
                            self.mesos_box, self.mesos_fails, self.mesos_provisional = box, 0, False
                            if not window:                          # a window's own box isn't remembered
                                moved = (ww, wh) + box
                        self.window_box = bool(window)
                    area = self.party_area
                    if area and self._blocked(*area):
                        notes.append("This window is covering the party list.")
                    else:
                        bars = find_party_bars(img, g, hsv)
                        party = max(1, len(bars))
                        if len(bars) >= 2:
                            x0, y0 = min(b[0] for b in bars), min(b[1] for b in bars)
                            x1, y1 = max(b[0] + b[2] for b in bars), max(b[1] + b[3] for b in bars)
                            # Only the HP bars are counted, so only they (and a little room
                            # around them) must be clear of this window.
                            self.party_area = (sx + x0 - int(g), sy + y0 - int(g),
                                               x1 - x0 + int(2 * g), y1 - y0 + int(2 * g))
                self._part_ok("window scan")
        mesos, image, boxes, notice_lines = None, None, (), None
        self.mesos_covered = False
        self.notices_covered = False
        # Notices are always scanned (for Etc value). Inventory mode also reads the mesos box.
        if now >= self.next_notices or self.meso_mode == "notices":
            with self._part("notices"):
                notice_lines = self._read_notices(capture, rect, notes)
                self.next_notices = time.monotonic() + NOTICE_SECONDS
                if notice_lines is not None:
                    self._part_ok("notices")
                    if self.meso_mode == "notices":
                        self._tried("mesos", True)      # strip was read (empty is still a look)
        if self.meso_mode == "inventory" and self.mesos_box:
            bx, by, bw, bh = self.mesos_box
            # (the digits only need the box and a sliver around it to be seen)
            if self._blocked(wx + bx, wy + by, bw, bh, margin=max(2, int(0.3 * bh))):
                notes.append("This window is covering the inventory's mesos.")
                self.mesos_covered = True
            else:
                with self._part("mesos"):
                    mesos, image, boxes = self._read_mesos(capture, wx + bx, wy + by, bw, bh)
                    self._part_ok("mesos")
                    self._tried("mesos", mesos is not None)
                    # A clean read of a scaled-after-resize box is enough to remember it.
                    if mesos is not None and self.mesos_provisional and not window:
                        self.mesos_provisional = False
                        moved = (ww, wh) + self.mesos_box
        window = self.window if now <= self.window_until else None
        if window:
            notes.append(f"{WINDOW_NAMES[window]} window open.")
        bar = None
        if now >= self.next_bar:
            with self._part("potions"):
                bar = self._read_potion_bar(capture, rect, notes)
                if bar is not None:
                    self._part_ok("potions")
                    self._tried("potions", bool(bar))
        return reading._replace(mesos=mesos, mesos_image=image, mesos_boxes=boxes, party=party,
                                mesos_at=moved, window=window, bar=bar, notices=notice_lines,
                                note=" ".join(notes) or None)

    def _read_notices(self, capture, rect, notes):
        """OCR lines from the right-side battle/loot strip, or None if this look couldn't try.

        Also stores the crop on self.notices_look for View capture.
        """
        capture = self.capture = as_capture(capture)
        wx, wy, ww, wh = rect
        bx, by, bw, bh = notices.crop_box(ww, wh)
        if self._blocked(wx + bx, wy + by, bw, bh, margin=4):
            notes.append("This window is covering the gain notices.")
            self.notices_covered = True
            return None
        if TESS is None:
            return None
        shot = capture.grab(wx + bx, wy + by, bw, bh)
        if not shot:
            return None
        try:
            lines = notices.read_strip(shot[0], TESS)
            self.notices_look = {"image": shot[0].copy(), "text": " | ".join(lines) if lines else ""}
            return lines
        except ocr.TesseractError as e:
            self._part_failed("notices", e)
            return None

    def _read_potion_bar(self, capture, rect, notes):
        """The potions and food on the potion bar as a tuple of potions.Slot (empty: none, or it
        can't be seen), or None when there is nothing to report. Read every few seconds."""
        capture = self.capture = as_capture(capture)
        self.next_bar = time.monotonic() + POTION_SECONDS
        wx, wy, ww, wh = rect
        if self.potion_table is None:
            try:
                self.potion_table = potions.PotionTable.load(ASSETS / "potion_table.json")
                self.potion_digits = potions.load_digits(ASSETS / "potion_digits.npz")
            except Exception as e:
                self._part_failed("potions", e)
                self.next_bar = float("inf")                    # without it there's nothing to read
                return None
        bx, by, bw, bh = potions.bar_crop_box(ww, wh)
        if self._blocked(wx + bx, wy + by, bw, bh):
            notes.append("This window is covering the potion bar.")
            return ()
        shot = capture.grab(wx + bx, wy + by, bw, bh)
        if not shot:
            return ()
        img, sx, sy = shot
        ex, ey, scale = potions.bar_estimate(ww, wh)
        full = self.bar_reads % POTION_FULL_LOOK == 0
        origin = (ex + wx - sx + (0 if full else self.bar_shift[0]), ey + wy - sy + (0 if full else self.bar_shift[1]))
        slots, (ox, oy) = potions.read_bar(img, scale, origin, self.potion_table, self.potion_digits,
                                           search=potions.SEARCH if full else 1)
        self.bar_shift = (ox - (ex + wx - sx), oy - (ey + wy - sy))
        self.bar_reads += 1
        return tuple(slots)

    def _readable(self, img, field):
        """Clear mesos digits at field? (Always yes while the mesos font is still being
        learned: then Tesseract checks every new number instead.)"""
        learned = self.mesos_digits
        return not learned.complete or read_mesos_positions(img, field, learned.templates()) is not None

    def _find_seethrough_box(self, img, g, hsv=None):
        """The mesos box by its outline, checked by reading clear digits there.

        The digits are read at fixed spots from the box's right edge, so a box found a bit
        off could read a wrong number: it must also line up with the number."""
        field = self.box_finder.find(img, g, hsv)
        if not field:
            return None
        learned = self.mesos_digits
        templates = learned.templates() if learned.complete else self.templates
        if not templates or not mesos_box_lines_up(img, field, templates):
            return None
        return field if read_mesos_positions(img, field, templates) else None

    def _read_mesos(self, capture, x, y, w, h):
        """Reads the mesos box at (x, y, w, h) on screen. Returns (number, image, boxes)."""
        capture = self.capture = as_capture(capture)
        shot = capture.grab(x - 2 * h, y - h, w + 4 * h, 3 * h)
        if not shot:
            return None, None, ()
        img, sx, sy = shot
        fx, fy = x - sx, y - sy
        inner = cv2.cvtColor(img[fy + 2:fy + h - 2, fx + 2:fx + w - 2], cv2.COLOR_BGR2HSV)
        solid = ((inner[..., 2] > 225) & (inner[..., 1] < 30)).mean() > 0.6

        def boxes_of(lay, ox=0, oy=0):
            return [(ox + c[0] - c[1] / 2, oy + lay.cy - lay.gh / 2,
                     ox + c[0] + c[1] / 2, oy + lay.cy + lay.gh / 2, "mesos") for c in lay.exp]

        learned = self.mesos_digits
        if learned.complete:                          # all ten mesos digits learned
            got = read_mesos_positions(img, (fx, fy, w, h), learned.templates())
            if not got:
                self.mesos_fails += 1
                return None, img, ()
            self.mesos_fails = 0
            value, lay, score, lead = got
            if not solid and len(str(value)) != self.mesos_count:
                # The first reading, or a different number of digits than before (that only
                # happens when crossing 10,000, 100,000...): on a see-through box, only
                # believe it once 3 readings in a row agree.
                last, n = self.mesos_pending
                self.mesos_pending = (value, n + 1 if _agrees(value, last) else 1)
                if self.mesos_pending[1] < 3:
                    return None, img, boxes_of(lay)
            self.mesos_count, self.mesos_pending = len(str(value)), (None, 0)
            if solid and score >= 0.95 and lead >= 0.15:
                learned.learn(lay, value)             # more examples, from very clean looks
            return value, img, boxes_of(lay)
        # Still learning the mesos font: a new number needs Tesseract's OK. On a solid box
        # Tesseract alone is enough; on a see-through box both readers must agree.
        lay = mesos_layout(img, (fx, fy, w, h))
        if not lay:
            self.mesos_fails += 1
            return None, img, ()
        self.mesos_fails = 0
        boxes = boxes_of(lay, fx + 2, fy + 2)
        value = read_mesos_digits(lay, self.templates) if self.templates else None
        if value is not None and value == self.mesos_checked:
            return value, img, boxes
        now = time.monotonic()
        if now < self.next_mesos_check or (not solid and value is None):
            return None, img, boxes
        self.next_mesos_check = now + (2 if value is not None else 5)
        checked, votes = tesseract_mesos(lay)
        if checked is None or (not solid and checked != value):
            return None, img, boxes
        self.mesos_checked = checked
        if checked == value or votes == 6:            # two readers agree: learn these digits
            learned.learn(lay, checked)
        return checked, img, boxes

    def _read_info(self, capture, rect):
        """Returns (map name if it was just read, note)."""
        capture = self.capture = as_capture(capture)
        if not tesseract_ok:
            return None, tesseract_note()
        if self.own_rect is None:
            return None, None                   # wait until we know where this app's window is
        wx, wy, ww, wh = rect
        g, now = self.gh, time.monotonic()
        name = note = None

        hidden = self.map_blind_seconds() > MAP_HIDDEN_SECONDS
        if self.map_box is None and now >= self.next_map_search:
            self.next_map_search = now + (1 if hidden else 3)     # looks harder for a name that has gone missing
            shot = self._hide_own(capture.grab(wx, wy, int(ww * 0.4), int(wh * 0.3)))
            box = find_map_box(shot[0], g) if shot else None
            if box:
                self.map_box = (shot[1] - wx + box[0], shot[2] - wy + box[1], box[2], box[3])
                self.map_print, self.map_unread = None, True
        if self.map_box is None:
            if self._blocked(wx, wy, int(ww * 0.4), int(wh * 0.3)):
                note = "This window may be covering the minimap. Move it so the map name can be read."
            else:
                note = "Can't see the map name. Is the minimap open?"
        else:
            bx, by, bw, bh = self.map_box
            if self._blocked(wx + bx, wy + by, bw, bh, margin=max(3, int(0.1 * g))):
                # Even a box that only touches this window may be cut off by it (it was
                # found with this window blacked out). Find the whole box again (and read
                # the name) once this window is out of the way.
                note = "This window is covering the minimap. Move it so the map name can be read."
                self.map_box, self.map_unread = None, True
            else:
                shot = capture.grab(wx + bx, wy + by, bw, bh)
                if shot:
                    self.map_seen_at = now
                    picture = cv2.resize(_whiteness(shot[0]), (64, 16), interpolation=cv2.INTER_AREA) > 0.3
                    if self.map_print is not None and (picture != self.map_print).mean() > 0.04:
                        # Changed map: find the box again (its size can change), then read it.
                        self.map_box, self.next_map_search = None, now + 1
                        self.map_unread, self.next_map_read = True, 0.0
                    elif self.map_unread and now >= self.next_map_read:
                        with self._part("map name"):
                            name = read_map_name(shot[0], g)
                            self._part_ok("map name")
                            self._tried("map", name is not None)
                        self.map_look = {"image": shot[0], "text": name}
                        self.map_unread = name is None
                        self.next_map_read = now + (2 if hidden else 5)   # if unreadable, try again later
                    self.map_print = picture
        return name, note


# --------------------------------------------------------------------------- tracking

class ExpTracker:
    """Turns noisy readings into trustworthy EXP gains.

    Every level needs a fixed amount of EXP (req), and the bar shows exp = pct% * req.
    req comes from the EXP table as soon as two clean readings point at the same level
    (otherwise it's learned from several *different* readings that agree with each other),
    then a reading only counts if its EXP and % agree with req. Misread digits break that,
    so they are ignored instead of being counted as gains.
    """

    WRAP_WAIT_READS = 40                      # %-only readings (about a minute) to wait for the level after a wrap

    def __init__(self):
        self.reset()

    def reset(self):
        self.running = False
        self._accum = 0.0
        self._resumed = None
        self._wrap_reads = 0                  # readings in a row spent waiting for a level after the % wrapped
        self.exp = None                       # last trusted EXP
        self.pct = None
        self.last_read = None                 # latest reading, trusted or not (for display)
        self.req = None                       # EXP required for the current level
        self._req_exact = False               # req came straight from the EXP table
        self._table_level = None              # level the last clean reading pointed at
        self._seen = {}                       # distinct (exp, pct) readings this level -> weight
        self._pending = None                  # big change waiting for confirmation (e, p, reads)
        self._recent = deque(maxlen=40)       # (when, EXP) of the latest trusted readings
        self._reads = 0                       # readings seen, as a clock for _recent
        self._misfits = 0                     # readings in a row that didn't fit req
        self._pending_lvl = None              # level-up waiting for confirmation
        self._level_start = None              # (exp, pct) right after a level-up, unverified
        self._prev_req = None                 # previous level's req, bounds the new one
        self._before_death = None             # (reads, EXP) just before the last death
        self.deaths = 0
        self.last_loss = None                 # % of the level's EXP the last death cost
        self.gained = 0
        self.levels = 0
        self.samples = deque()                # (elapsed, gained)
        self._format = None                   # what the bar showed last: "exact" (number and %) or "percent"
        self.estimated = False                # some EXP here came from a % alone, so it is an estimate
        self.level_source = None              # where a %-only level came from: "badge", "typed"

    def start(self):
        if not self.running:
            self.running = True
            self._resumed = time.monotonic()

    def pause(self, at=None):
        """Stops the clock (as of `at`, e.g. the last EXP gain, so idle time doesn't count)."""
        if self.running:
            end = time.monotonic() if at is None else min(time.monotonic(), at)
            self._accum += max(0.0, end - self._resumed)
            self.running = False

    def new_session(self):
        """Numbers from zero again, keeping what's known about the level and current EXP."""
        self.running = False
        self._accum, self._resumed = 0.0, None
        self.gained, self.levels, self.deaths = 0, 0, 0
        self.samples = deque()
        self.estimated = False

    def elapsed(self):
        if self.running:
            return self._accum + time.monotonic() - self._resumed
        return self._accum

    # ---- level requirement
    @staticmethod
    def _rel_err(pct):
        # % is shown with 2 decimals, so exp / pct is only this precise
        return 0.002 + 0.01 / pct

    def _learn_req(self, candidates, confident):
        weight = 2 if confident else 1        # font-matched readings are rarely wrong
        for e, p in candidates:
            if p >= 0.2 and e > 0:                # (a misread 0 says nothing about req)
                self._seen[(e, p)] = max(self._seen.get((e, p), 0), weight)
        while len(self._seen) > 60:
            del self._seen[next(iter(self._seen))]
        ests = [(e / (p / 100), self._rel_err(p), w) for (e, p), w in self._seen.items()]
        if self._prev_req:                    # the next level needs about as much or more
            ests = [x for x in ests if 0.98 <= x[0] / self._prev_req <= 2.5]
        group_of = lambda c, ce: [x for x in ests if abs(x[0] - c) / c <= ce + x[1]]
        weight_of = lambda g: sum(x[2] for x in g)
        center = lambda g: sum(x[0] * x[2] / x[1] ** 2 for x in g) / sum(x[2] / x[1] ** 2 for x in g)
        groups = sorted((group_of(c, ce) for c, ce, _ in ests), key=weight_of, reverse=True)
        if not groups or weight_of(groups[0]) < 3:
            return
        best = groups[0]
        mid = center(best)
        if self.req is None:
            # Lock in only once one value clearly beats any other (misreads can agree too).
            rivals = [g for g in groups if not 0.99 <= center(g) / mid <= 1.01]
            if not rivals or weight_of(best) >= 2 * weight_of(rivals[0]):
                self.req = mid
            return
        if self._req_exact:
            return                            # straight from the EXP table: misreads can't move it
        current = group_of(self.req, 0.003)
        if 0.99 <= mid / self.req <= 1.01 or weight_of(best) < 1.5 * weight_of(current) + 1:
            if current:
                self.req = center(current)
            return
        # A different value now clearly has more support: the old one came from misreads.
        # Switch, and restart the baseline so the change isn't counted as a gain.
        self.req, self._req_exact = mid, False
        self.exp = self.pct = None
        self._pending = None

    def _req_from_table(self, reading):
        """Takes the level's EXP straight from the EXP table once two clean readings in a
        row point at the same single level, so there's no wait for EXP gains."""
        e, p = reading
        levels = levels_for(e, p) if p >= 0.5 else []
        level = levels[0] if len(levels) == 1 else None
        if level and self._prev_req and EXP_TABLE[level - 1] < 0.98 * self._prev_req:
            level = None                      # after a level-up, the next level needs more
        if level and level == self._table_level:
            self.req, self._req_exact = EXP_TABLE[level - 1], True
        self._table_level = level

    def _consistent(self, e, p):
        return abs(e / self.req * 100 - p) <= 0.02 + 0.003 * p

    def _add_gain(self, amount):
        if self.running:
            self.gained += amount
            now = self.elapsed()
            self.samples.append((now, self.gained))
            while self.samples and now - self.samples[0][0] > ROLLING_WINDOW:
                self.samples.popleft()

    # ---- main entry
    def observe(self, candidates, confident=False):
        """Feed the (exp, pct) guesses for one screenshot. Returns (accepted, note)."""
        self._switch_format("exact")
        return self._observe(candidates, confident)

    def _switch_format(self, kind):
        """A bar that went from showing its number to only a %, or back: the estimate and the
        real EXP differ by rounding, so the next reading starts a fresh baseline (not a gain)."""
        if self._format not in (None, kind):
            self.exp = self.pct = self._pending = self._before_death = None
            self._pending_lvl = self._level_start = None
            self._recent.clear()                  # (the other format's numbers are off by rounding)
            self._wrap_reads = 0
        self._format = kind

    def observe_percent(self, pct, level=None, source=None):
        """Feed the % of a bar that shows no number ("EXP. [58.47%]"), with the level when it was read
        (source: "badge" or "typed"). The EXP is an estimate: the level's EXP from the table times pct.
        Returns (accepted, note), like observe."""
        if pct is None or not 0 <= pct < 100:
            return False, "unreadable"
        if level is not None and not 1 <= level <= len(EXP_TABLE):
            return False, f"level {level} isn't supported yet (no EXP table for it)"
        cur = level_for_req(self.req) if self.req else None
        if cur is None:
            if level is None:
                return False, "waiting to read your level"
            self._use_level(level, source)
            cur = level
        if level is not None and level == cur and source:
            self.level_source = source           # (a level read from the badge beats a carried or typed one)
        # A % that wrapped around (the level went up, or a misread): only the next level, read, says which.
        wrapped = self.exp is not None and self.pct is not None and self._format == "percent" and self.pct - pct > 30
        if level is not None and level != cur:
            # (the next level with a lower %, whatever the bar showed before and however far it fell: a level-up)
            fell = self.exp is not None and self.pct is not None and pct < self.pct
            if (wrapped or fell) and level == cur + 1:
                last = self._pending_lvl
                n = last[2] + 1 if last and last[0] == level else 1
                self._pending_lvl = (level, pct, n)
                if n < 2:
                    return False, "level up? waiting to confirm"
                self.estimated = True
                self._enter_level(round(EXP_TABLE[level - 1] * pct / 100), pct, EXP_TABLE[level - 1])
                self.level_source = source
                return True, "LEVEL UP!"
            if self.gained:
                return False, "level changed: Reset to start again on the new level"
            self._use_level(level, source)      # nothing counted yet: the newer reading wins
            cur = level
        elif wrapped:
            if level is not None:                 # (a look where the badge wasn't read doesn't cancel a level-up seen before it)
                self._pending_lvl = None
            self._wrap_reads += 1
            if self._wrap_reads < self.WRAP_WAIT_READS:
                return False, "percentage wrapped: waiting to read the new level"
            self.forget_level()                   # nothing explained it for a minute: the level is unknown again, and the
            self._wrap_reads = 0                  # next one read (even a new one) is taken as it is, with a fresh start
            return False, "waiting to read your level"
        self._wrap_reads = 0
        self._pending_lvl = None
        self._switch_format("percent")
        self.estimated = True
        return self._observe([(round(EXP_TABLE[cur - 1] * pct / 100), pct)], True)

    def _use_level(self, level, source):
        """Starts estimating from this level (a fresh baseline: nothing is counted for the change)."""
        self.req, self._req_exact, self.level_source = EXP_TABLE[level - 1], True, source
        self.exp = self.pct = self._pending = self._pending_lvl = self._level_start = None

    @property
    def percent_mode(self):
        """The bar shows only a % right now (so the EXP coming in is an estimate)."""
        return self._format == "percent"

    def set_level(self, level, source):
        """The level, given from outside the bar (typed in). False if there is no EXP table for it."""
        if not 1 <= level <= len(EXP_TABLE):
            return False
        self._use_level(level, source)
        return True

    def forget_level(self):
        """Another character, or a level that can't be trusted: nothing to estimate from until read again."""
        self.req, self._req_exact, self._table_level, self.level_source = None, False, None, None
        self.exp = self.pct = self._pending = self._pending_lvl = self._level_start = None
        self._seen.clear()

    def _observe(self, candidates, confident=False):
        candidates = [(e, p) for e, p in candidates if p is not None and 0 <= p < 100]
        if not candidates:
            return False, "unreadable"
        self._reads += 1
        self.last_read = candidates[0]

        if self._check_level_up(candidates):
            return True, "LEVEL UP!"
        if self._pending_lvl:
            # Looks like a level-up, not confirmed yet: don't also take it as EXP lost.
            return False, "level up? waiting to confirm"

        if self.req is None and confident:
            self._req_from_table(candidates[0])
        self._learn_req(candidates, confident)
        if self.req is None:
            return False, "calibrating: learns your level's EXP after your next EXP gain"

        good = [c for c in candidates if self._consistent(*c)]
        if not good:
            self._misfits += 1
            if self._req_exact and self._misfits >= 30:
                # Nothing has fitted this level for a long while: the level itself may be
                # wrong. Work it out again.
                self.req, self._req_exact, self._table_level, self._misfits = None, False, None, 0
                self.exp = self.pct = self._pending = None      # and start counting afresh
            return False, "misread, ignored"
        self._misfits = 0
        e, p = good[0]
        self.last_read = good[0]

        if self._level_start:
            # First trusted reading after a level-up: fix the start value if it was misread.
            e0, p0 = self._level_start
            self._level_start = None
            if not self._consistent(e0, p0):
                fixed = round(p0 / 100 * self.req)
                self._add_gain(fixed - e0)
                self.exp = fixed

        if self.exp is None:
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"

        delta = e - self.exp
        if (0.01 * self.req <= -delta or (e == 0 and delta < 0)) and -delta <= 0.15 * self.req                 and not self._was_near(e):
            # EXP went down a few % of the level (or to 0): dying (it costs up to about 10%). Once a
            # second reading agrees, carry on from the new number without counting the loss,
            # so a death doesn't drag EXP/hr down (or below zero).
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.005 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "EXP went down, waiting to confirm"
            self._pending = None
            self._before_death = (self._reads, self.exp)
            self.last_loss = -delta / self.req * 100
            self.deaths += 1
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "died"
        if self._before_death and self._reads - self._before_death[0] <= 40 and delta > 0 \
                and e >= self._before_death[1] - 0.005 * self.req:
            # Right back to where it was before "dying", in one go: those low readings were a
            # misread, not a death (EXP lost on dying comes back only slowly, from monsters).
            self._before_death, self.deaths = None, max(0, self.deaths - 1)
            self._pending = None
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"
        if abs(delta) > 0.15 * self.req:
            # Unusually big change in one tick: only believe it if the next read agrees.
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.15 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "big jump, waiting to confirm"
            if delta < 0 and not self._undoes_jump(e):
                # EXP never drops this much (dying costs at most 10%; level-ups are checked
                # separately), so this is a misread, even twice in a row. If it keeps
                # showing up, carry on from it, without counting the drop.
                if n < 8:
                    return False, "misread, ignored"
                self._pending, self.exp, self.pct = None, e, p
                self._recent.append((self._reads, e))
                return True, "baseline"
        self._pending = None
        self._add_gain(delta)
        self.exp, self.pct = e, p
        self._recent.append((self._reads, e))
        return True, ""

    def _was_near(self, e):
        """Was EXP about e a moment ago? Then going down to it is the right number coming
        back after a misread that was a bit too high (and counts, to cancel that out)."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.005 * self.req for r, x in self._recent)

    def _undoes_jump(self, e):
        """Was EXP around e a moment ago? Then a big drop to it is the right number coming
        back after a misread that was too high, and must count, to cancel that out."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.15 * self.req for r, x in self._recent)

    def _check_level_up(self, candidates):
        """% dropped a lot and EXP reset low: a level-up, once readings in a row agree on it.

        If the level is known, readings that fit the next level's EXP from the table count,
        and two in a row are enough. Otherwise a reading that still fits this level's EXP is
        never a level-up (e.g. the right number coming back after a misread that was too
        high), and as the next level's EXP is only known to be bigger, it takes five.
        """
        if self.exp is None or self.pct is None or self.req is None:
            return False
        level = level_for_req(self.req)
        nxt = EXP_TABLE[level] if level and level < len(EXP_TABLE) else None
        hits = []
        for e, p in candidates:
            if not (self.pct - p > 30 and e < self.exp):
                continue
            if nxt and abs(e / nxt * 100 - p) <= 0.02 + 0.003 * p:
                hits.append((e, p, 2))        # fits the next level exactly
            elif self._consistent(e, p):
                continue                      # still this level: not a level-up
            elif p >= 0.2 and 0.98 * self.req <= e / (p / 100) <= 2.5 * self.req:
                hits.append((e, p, 5))        # next level's EXP is similar or bigger
        if not hits:
            self._pending_lvl = None
            return False
        e, p, need = hits[0]
        last = self._pending_lvl
        # Each reading must carry on from the one before (EXP only goes up from there).
        n = last[2] + 1 if last and 0 <= e - last[0] <= 0.15 * (nxt or self.req) else 1
        self._pending_lvl = (e, p, n)
        if n < need:
            return False
        self._enter_level(e, p, nxt if need == 2 else None)     # (2: the new level's EXP is known, no relearning)
        return True

    def _enter_level(self, e, p, new_req=None):
        """A confirmed level-up: the rest of the old level and e count as gained. new_req is the new
        level's EXP when the table gives it (otherwise it is learned again)."""
        self._pending_lvl = self._pending = None
        if self.running:
            self.levels += 1
        self._add_gain(self.req - self.exp + e)
        self.exp, self.pct = e, p
        self._prev_req = self.req
        self.req, self._req_exact = new_req, new_req is not None
        self._table_level = None
        self._seen.clear()
        self._recent.clear()                   # the old level's numbers mean nothing now
        self._recent.append((self._reads, e))
        self._level_start = (e, p)

    def rate(self):
        t = self.elapsed()
        return self.gained / t * 3600 if t >= 10 else None

    def rolling_rate(self):
        if len(self.samples) < 2:
            return None
        (t0, g0), (t1, g1) = self.samples[0], self.samples[-1]
        return (g1 - g0) / (t1 - t0) * 3600 if t1 - t0 >= 30 else None


class MesoTracker:
    """Turns mesos readings into mesos gained per hour and per 5 minutes.

    The first count is the baseline; every later one adds what changed since. The
    inventory doesn't have to stay open: after a short wait (MESO_GAP) a big rise is
    believed (you weren't dropping mesos while it was closed), and the rates cover the
    run up to the last look, so they hold still while the inventory is closed.

    A new count only counts once two readings in a row agree on it (three for a big jump),
    so a one-off misread never shows up as mesos gained. A change by more than half,
    or a digit-count change bigger than 10%, starts counting afresh from the new number
    instead: that's a shop visit or a corrected misread, not mesos picked up. Small digit
    rollovers count after three identical readings.
    """

    def __init__(self):
        self.resets = 0                       # times reset(): looks from before one don't compare with looks after
        self.reset()

    def reset(self):
        self.resets += 1
        self.value = None                     # confirmed mesos count
        self.seen_at = None                   # when it was last confirmed
        self._candidate = (None, 0, 0)
        self.gained = 0
        self.samples = deque()                # (elapsed, gained)
        self.start = None                     # elapsed time when counting began
        self.read_elapsed = None              # elapsed time of the last confirmed count
        self.counting = False
        self.window = None                    # "shop", "storage" or "trade" while one is open
        self.window_reads = 0                 # confirmed counts seen in it so far
        self.drop_next = False                # a window was never read: the next change isn't earned
        self.after_window = False             # the next count follows a window that just closed
        self.late_change = False              # ...and a new count was still being confirmed as it closed
        self.idle_pause = False               # paused only because EXP stopped (same map): nothing is left out
        self.ignored = None                   # (amount, window, when) of the last change left out
        self.stale = False                    # paused since the last count: what changed meanwhile is unknown
        self.stale_since = None               # when the status line first had to ask about it
        self.bank_window = False              # the first window opened after a trip banks what came before it
        self.bank_inventory = False           # ...and so does the inventory, right after pausing on the map
        self.left_out = [0, 0]                # mesos gained and lost at windows (kept apart, for Details)
        self.window_at = None                 # when a shop, storage or trade window was last seen open
        self.misreads = []                    # (from, to): when a count later proven wrong was shown
        self._shown = None                    # [count, first seen, last seen] of the count shown now
        self._before = None                   # ...and of the one before it
        self._window_since = False            # a window was seen since the count shown now came in

    def pause(self, stale=True, bank_inventory=False):
        """Tracking paused. Unless it's only an idle pause on the same map, what changes now
        (a sale in town, a trade...) can't be told from earnings: the count goes stale.

        What was earned before the pause hasn't been counted yet if the inventory was closed.
        It is banked by the first look at the inventory right after pausing on the grinding
        map (bank_inventory), or by the first shop, storage or trade window that is read."""
        self.counting = False                 # don't count what changes while paused
        self.idle_pause = not stale
        self.stale = stale and self.value is not None
        self.bank_window = self.stale
        self.bank_inventory = self.stale and bank_inventory

    def stale_note(self, now, running):
        """Asks for the inventory: right after pausing (to keep what was earned), or once
        tracking has carried on with the count still stale."""
        if not running and self.bank_inventory and (self.seen_at is None or now - self.seen_at > 8):
            return "Open the inventory now to save your mesos."
        if not (self.stale and running):
            self.stale_since = None
            return None
        if self.stale_since is None:
            self.stale_since = now
        if now - self.stale_since >= 5:       # (not at once: the inventory may be open already)
            return "Open the inventory to set the mesos start."
        return None

    def set_window(self, window, now):
        """Which money window is open (or None). Mesos change there without being earned:
        the first count in a window is still earned (it shows what came before), every
        later change is left out. A window closed without a single count leaves the next
        change unexplained, so that is left out too."""
        if window:
            self.window_at = now
            self._window_since = True
        if window == self.window:
            return
        if self.window and not window:        # (a sale or purchase seen, not yet confirmed: not earned either)
            self.late_change = self._candidate[0] is not None and self._candidate[0] != self.value
        if window and not self.window:
            self.window_reads = 0
        elif self.window and not window and self.window_reads == 0:
            self.drop_next = True
            self.after_window = True
        elif self.window and not window:
            self.after_window = True
            if not self.counting:
                self.stale = False            # read exactly while paused: the count is still good
        self.window = window

    def ignored_note(self, now):
        """Why the last change was left out, for the status line, or None after a while."""
        if not self.ignored or now - self.ignored[2] > 30:
            return None
        amount, window, _ = self.ignored
        where = {"shop": "the NPC shop", "storage": "storage", "trade": "a trade"}.get(window, "a shop or trade")
        return f"Left out {amount:+,} mesos ({where}, not earned)."

    def observe(self, value, now, elapsed, running):
        jump = False
        if value == self.value:
            self._candidate = (None, 0, 0)        # unchanged: nothing new to confirm
        else:
            # How many readings in a row back the new number: close to it, and exactly it.
            last, n, same = self._candidate
            n = n + 1 if _agrees(value, last) else 1
            same = same + 1 if value == last else 1
            self._candidate = (value, n, same)
            old = self.value
            # Measured against the bigger of the two, so a misread and its correction are
            # always judged the same way (both counted, or both not), and cancel out.
            top = max(value, old) if old is not None else 0
            digits_changed = old is not None and len(str(value)) != len(str(old))
            jump = old is not None and abs(value - old) > (0.1 if digits_changed else 0.5) * top
            # Not looked at for a while, so a big rise is more likely mesos earned than a shop
            # visit (selling is seen as it happens, and a drop is still treated as a shop).
            big_jump = jump
            if jump and value > old and self.seen_at is not None and now - self.seen_at > MESO_GAP:
                jump = False
            big = old is not None and abs(value - old) > max(10000, 0.05 * top)
            # The first count, a digit change and a jump need the very same number 3 times.
            # First counts and jumps aren't gained, so a misread there would never cancel
            # out; digit changes can also be different, nearby misreads of an extra digit.
            # About 10x (or a tenth): almost always a digit read too many (or lost).
            tenfold = old is not None and old > 0 and (9 <= value / old <= 11 or 0.09 <= value / old <= 0.11)
            if old is None or big_jump or digits_changed:
                if same < (8 if tenfold else 3):
                    return
            elif n < (3 if big else 2):
                return
            self._candidate = (None, 0, 0)
        if (not running and self.idle_pause and not self.window
                and self.value is not None and self.start is not None):
            if value == self.value:
                self.seen_at = now                # (still a look, for a run waiting for one)
            return                                # an idle pause: counted once tracking carries on
        self.seen_at = now
        self.read_elapsed = elapsed
        resumed = running and not self.counting
        first = self.value is None
        if resumed:
            self.counting = True                  # count changes from this reading on
            self.start = elapsed if self.start is None else self.start
            if not self.samples:                  # the rates always have a starting point
                self.samples.append((elapsed, self.gained))
        moved = value != self.value and not jump
        if jump and self.window and self.value is not None:
            self._leave_out(value - self.value, now)          # a big sale or purchase: a new start, and said so
        # Mesos never go down by grinding. A drop at a window's first look, or at the first look
        # after one, is a purchase that wasn't read in time, not negative earnings.
        unread_spend = (moved and self.value is not None
                        and ((self.window and self.window_reads == 0 or self.after_window) and value < self.value
                             or self.after_window and self.late_change))
        banking = (not running and self.stale and self.value is not None
                   and ((self.window and self.window_reads == 0 and self.bank_window)
                        or (not self.window and self.bank_inventory)))
        if banking:                               # what was earned before the trip, seen at last
            self.bank_window = self.bank_inventory = False
            if moved and unread_spend:
                self._leave_out(value - self.value, now)
            elif moved:
                self._earn(value - self.value, elapsed)
        elif moved and self.window and self.window_reads:
            self._leave_out(value - self.value, now)                        # exact change at a window
        elif moved and not running and not self.idle_pause and self.value is not None and self.start is not None:
            self._leave_out(value - self.value, now)                        # anything while paused
        elif resumed and (self.stale or first):
            self.stale = False                    # a first or stale count is only a new baseline
            self.samples.append((elapsed, self.gained))
        elif running and moved:
            if self.drop_next or unread_spend:
                self._leave_out(value - self.value, now)
            else:
                self._earn(value - self.value, elapsed)
        self.drop_next = False
        if not self.window:
            self.after_window = self.late_change = False
        if self.window:
            self.window_reads += 1
        self._snap_back(value, now)
        self.value = value

    def _snap_back(self, value, now):
        """A count that goes back to the one before it (A, B, A) with no window seen meanwhile
        is a snap back: grinding can't do that, so B was a misread. When B was shown is kept
        (misreads), so nothing that used it goes to the website with mesos."""
        if self._shown and value == self._shown[0]:
            self._shown[2] = now
            return
        if self._shown and self._before and value == self._before[0] and not self._window_since:
            self.misreads = self.misreads[-49:] + [(self._shown[1], self._shown[2])]
        self._before, self._shown = self._shown, [value, now, now]
        self._window_since = bool(self.window)

    def _earn(self, amount, elapsed):
        self.gained += amount
        self.samples.append((elapsed, self.gained))
        while len(self.samples) > 2 and self.samples[1][0] <= elapsed - ROLLING_WINDOW:
            self.samples.popleft()

    def _leave_out(self, amount, now):
        self.ignored = (amount, self.window, now)
        self.left_out[amount < 0] += abs(amount)

    def look(self):
        """The last inventory look as the log is given it: (mesos gained so far, when read),
        however old (the log decides what is too old), or None before the first count."""
        return None if self.value is None else (self.gained, self.seen_at)

    def for_log(self):
        """What AutoLog.tick and AutoLog.settle are given about mesos, as keyword arguments."""
        return dict(mesos=self.look(), window_at=self.window_at, misreads=self.misreads, meso_epoch=self.resets)

    def per_hour(self, elapsed):
        # Up to the last look, not up to now: the number holds still while nobody looks.
        end = self.read_elapsed if self.read_elapsed is not None else elapsed
        if self.start is None or end - self.start < 30:
            return None
        return self.gained / (end - self.start) * 3600

    def per_5min(self, elapsed):
        if self.start is None:
            return None
        end = self.read_elapsed if self.read_elapsed is not None else elapsed
        t0 = max(self.start, end - ROLLING_WINDOW)
        if end - t0 < 60:
            return None
        return (self.gained - self._gained_at(t0)) / (end - t0) * ROLLING_WINDOW

    def _gained_at(self, t0):
        """Mesos gained by run time t0, assuming a steady rate between two looks."""
        before = self.samples[0] if self.samples else (self.start, 0)
        for t, g in self.samples:
            if t > t0:
                if t == before[0]:                # (t0 is before the first look we have)
                    return g
                return before[1] + (g - before[1]) * (t0 - before[0]) / (t - before[0])
            before = (t, g)
        return before[1]


class Session:
    """What's on screen: EXP and mesos since Start, and when tracking pauses by itself.

    After Start it tracks until Pause is pressed, except that it pauses by itself when no
    EXP comes in for `gap` seconds or you leave the map, and carries on at the next EXP
    gain: from zero if that's on a different map, so the numbers are always for the map
    you're on. Time spent paused never counts, and an idle pause rewinds the clock to the
    last EXP gain. The gain that ends a pause starts the clock again rather than counting,
    just like a log stretch's first gain. Paused longer than PAUSE_RESTART_SECONDS (idle or
    by hand): the next resume is a fresh run (totals from zero) instead of continuing.
    """

    def __init__(self, gap):
        self.exp, self.meso = ExpTracker(), MesoTracker()
        self.notices = notices.NoticeTracker()
        self.potions = potions.PotionTracker()
        self.gap = gap
        self.on = False             # Start was pressed (and Pause wasn't)
        self.waiting = None         # paused by itself: "idle" or "map"
        self.map = None             # the map these numbers are for
        self.here = None            # the map you're on now
        self.last_gain = None
        self.paused_at = None       # when the current pause began (manual or by itself)
        self.fresh_restart = False  # True once after a long-pause fresh run (for the status line)

    def start(self, now):
        self.on, self.waiting, self.last_gain = True, None, now
        self.paused_at = None
        self.map = self.map or self.here
        self.exp.start()

    def pause(self, now=None):
        self.on, self.waiting = False, None
        if self.paused_at is None:
            self.paused_at = time.monotonic() if now is None else now
        self.exp.pause()
        # (the inventory can bank what was earned only while still on the grinding map)
        self.meso.pause(bank_inventory=self.map is None or self.here is None or self.here == self.map)
        self.notices.pause()
        self.potions.pause()

    def reset(self):
        self.exp.reset()
        self.meso.reset()
        self.notices.reset()
        self.potions.reset()
        self.on, self.waiting, self.map, self.last_gain = False, None, None, None
        self.paused_at, self.fresh_restart = None, False

    def should_fresh_run(self, now):
        """Paused (idle or by hand) long enough that the next resume should be a new run."""
        return self.paused_at is not None and now - self.paused_at >= PAUSE_RESTART_SECONDS

    def begin_fresh_run(self):
        """Clear farm totals for a new run; keep the current EXP/level reading."""
        self.exp.new_session()
        self.meso.reset()
        self.notices.reset()
        self.potions.reset()
        self.map = self.here
        self.fresh_restart = True

    def _wait(self, why, now=None):
        self.exp.pause(at=self.last_gain)
        self.meso.pause(stale=why != "idle")      # (leaving the map: a shop or a trade may follow)
        self.notices.pause(stale=why != "idle")
        self.potions.pause()
        self.waiting = why
        if self.paused_at is None:
            self.paused_at = time.monotonic() if now is None else now

    def observe(self, candidates, confident, now):
        """Feeds an EXP reading to the tracker. Returns the tracker's (accepted, note)."""
        return self._feed(lambda t: t.observe(candidates, confident), now)

    def observe_percent(self, pct, level, source, now):
        """Feeds the % of a bar that shows no EXP number (see ExpTracker.observe_percent)."""
        return self._feed(lambda t: t.observe_percent(pct, level, source), now)

    def _feed(self, observe, now):
        t = self.exp
        exp_before, gained_before = t.exp, t.gained
        result = observe(t)
        rose = exp_before is not None and t.exp is not None and (
            t.exp > exp_before or result[1] == "LEVEL UP!")
        if t.running and t.gained > gained_before:
            self.last_gain = now
        elif self.on and self.waiting and self.waiting != "blind" and rose:
            # Different map, or paused a long time: totals from zero for a new run.
            if self.should_fresh_run(now) or (self.map and self.here and self.here != self.map):
                self.begin_fresh_run()
            self.waiting = None
            self.paused_at = None
            self.last_gain = now
            t.start()
        return result

    def look_at_bar(self, slots):
        """Feeds a look at the potion bar: what went down since the last look was used."""
        self.potions.observe(slots, self.exp.running)

    def map_blind(self):
        """The minimap's name has been out of sight for a while (hidden, covered, or the game
        closed it): you may have changed maps without it noticing, so it pauses instead of
        carrying on under the old name. It carries on once the name can be read again."""
        self.here = None
        if self.waiting == "blind" or not self.on:
            return
        if self.exp.running:
            self._wait("blind")
        elif self.waiting:
            self.waiting = "blind"

    def map_seen(self, name):
        """The map name was read (already matched to known names, see AutoLog.canonical)."""
        if self.waiting == "blind":
            self.waiting = "map"            # visible again: the usual rule decides (same map: carry on; another: new numbers)
        self.here = name
        if self.map and name != self.map and not self.on:
            self.meso.bank_inventory = False            # walked off after pausing: a read here isn't just earnings
        if not self.on:
            return
        if self.map is None:
            self.map = name
        elif name != self.map:              # left the map
            if self.exp.running:
                self._wait("map")
            elif self.waiting:
                if self.waiting == "idle":
                    self.meso.pause()       # (an idle pause kept the count; off the map it goes stale)
                self.waiting = "map"

    def tick(self, now):
        """Call regularly: pauses after `gap` seconds without EXP."""
        if self.on and self.exp.running and now - self.last_gain > self.gap:
            self._wait("idle", now)


# --------------------------------------------------------------------------- auto log

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def party_label(n):
    return "Solo" if not n or n <= 1 else f"{n}ppl party"


def fmt_logged(seconds, short=False):
    """How long a log line was measured over: '25 min' / '1 h 05 min', or short '25m' / '1h 05m'."""
    hours, minutes = divmod(int(round((seconds or 0) / 60)), 60)
    if short:
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


APPROX = "≈"           # shown before a number worked out from a % alone: "≈15,429"


def net_text(e):
    """Net mesos/hr of a log line ('' if no potion cost was counted, '?' if the mesos weren't read):
    mesos/hr minus what the potions cost per hour."""
    if "potion_cost" not in e:
        return ""
    ms, ps = e.get("mesos_seconds", 0), e.get("potion_seconds", e["seconds"])
    if not ms or not ps:
        return "?"
    return f"{e['mesos'] / ms * 3600 - e['potion_cost'] / ps * 3600:,.0f}"


def format_entry(e):
    """e.g. 'Oct 8th, 2026 - Rocky Road II - Lv 27 - Bandit - Solo - 15,429exp/hr - 3,210mesos/hr - 25 min'"""
    d = date.fromisoformat(e["date"])
    level = e["level"] if e["level"] is not None else "?"
    parts = [f"{MONTHS[d.month - 1]} {_ordinal(d.day)}, {d.year}", e["map"], f"Lv {level}"]
    if e.get("class"):
        parts.append(e["class"])
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    if tracked:
        parts.append(party_label(e["party"]))
    parts.append(f"{APPROX if e.get('estimated') else ''}{e['exp'] / e['seconds'] * 3600 if e['seconds'] else 0:,.0f}exp/hr")
    if tracked:
        ms = e.get("mesos_seconds", 0)
        parts.append(f"{e['mesos'] / ms * 3600:,.0f}mesos/hr" if ms else "?mesos/hr")
        if net_text(e):
            parts.append(f"{net_text(e)}net/hr")
    parts.append(fmt_logged(e["seconds"]))
    return " - ".join(parts)


def clean_note(text):
    """A note as it's kept: plain text with \\n line breaks, no control characters or lone
    surrogates (neither can be shown or saved), without blanks at either end."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or (ch >= " " and ch != "\x7f"))
    return text.encode("utf-8", "ignore").decode("utf-8").strip()


def note_of(e):
    """The personal note on a log line, ready to show ('' if none, or if what's there isn't text)."""
    n = e.get("note")
    return clean_note(n) if isinstance(n, str) else ""


def notes_of(entries):
    """One note for log lines shown as one (a map's party sizes): each different note once."""
    return "\n\n".join(dict.fromkeys(n for n in map(note_of, entries) if n))


def log_row(e, part=False):
    """The log table's cells for an entry: Date, Map, Lv, Class, Party, Time, EXP/hr, Mesos/hr,
    Net/hr, Note. `part`: one party size under a merged party line, so just its own numbers."""
    d = date.fromisoformat(e["date"])
    day = f"{MONTHS[d.month - 1]} {d.day}" + ("" if d.year == date.today().year else f" '{d.year % 100:02d}")
    level = e["level"] if e["level"] is not None else "?"
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    party = ("Solo" if not e["party"] or e["party"] <= 1 else f"{e['party']}ppl") if tracked else ""
    exp = f"{APPROX if e.get('estimated') else ''}{e['exp'] / e['seconds'] * 3600:,.0f}" if e["seconds"] else "—"
    ms = e.get("mesos_seconds", 0)
    mesos = f"{e['mesos'] / ms * 3600:,.0f}" if ms else ("?" if tracked else "")
    note = NOTE_MARK if note_of(e) else ""
    if part:
        return "", "", "", "", party, fmt_logged(e["seconds"], short=True), exp, mesos, net_text(e), note
    return (day, e["map"], level, e.get("class") or "", party, fmt_logged(e["seconds"], short=True), exp, mesos,
            net_text(e), note)


def party_groups(entries):
    """Log lines as the log shows them, newest first: the party sizes (2 to 6) of one map on
    one day, at one level and class, together (smallest party first); everything else, solo
    play included, on its own. (Party sizes come and go as people join and leave.)"""
    groups = {}
    for e in entries:
        party = e.get("party")
        key = (e["date"], e["map"], e["level"], e.get("class"), "party") if party and party >= 2 else id(e)
        groups.setdefault(key, []).append(e)
    return [(key, sorted(g, key=lambda e: e["party"]) if len(g) > 1 else g) for key, g in groups.items()]


def merged(entries):
    """Log lines added together, so EXP/hr and mesos/hr are over all of their time."""
    m = dict(entries[0])
    if any(e.get("estimated") for e in entries):
        m["estimated"] = True                    # (one estimated part makes the whole line an estimate)
    for k in ("exp", "seconds", "mesos", "mesos_seconds"):
        m[k] = sum(e.get(k) or 0 for e in entries)
    if any("potion_cost" in e for e in entries):
        m["potion_cost"] = sum(e.get("potion_cost") or 0 for e in entries)
        m["potion_seconds"] = sum(e.get("potion_seconds", e["seconds"]) for e in entries if "potion_cost" in e)
    else:
        m.pop("potion_cost", None)
    return m


def group_row(entries, is_open=False):
    """The log table's cells for a map's party sizes together, e.g. Party '4–5ppl ▸'."""
    row = list(log_row(merged(entries)))
    sizes = [e["party"] for e in entries]
    row[4] = f"{sizes[0]}–{sizes[-1]}ppl {'▾' if is_open else '▸'}"
    row[9] = NOTE_MARK if notes_of(entries) else ""
    return tuple(row)


def _numbers(name):
    """Roman numerals and numbers in a map name. Maps that differ in these are different maps."""
    return re.findall(r"\b(?:[IVX]+|\d+)\b", name)


LOG_VERSION = 4     # 2: mesos/hr from the mesos tracker; 3: class, seals and runs; 4: potion cost
                    # (a line's personal "note" needed no new version: it's one more optional field,
                    # which older versions leave alone and keep)
NOTE_MAX = 4000     # characters in a note on a log line
NOTE_MARK = "\u270e"            # shown in the log's Note column for lines that have one
BACKUPS_KEPT = 7    # days of log backups kept (backups/ in the data folder)
RUN_SECONDS = 900   # a run: 15 minutes of non-stop farming on one map, one party size
                    # (until 0.9.4 a run was an hour; those are still shared as they are)
PAUSE_RESTART_SECONDS = 600  # paused (idle or by hand) this long: next resume is a fresh run
SITE_GAP = 15       # seconds without EXP that end a run, whatever the gap setting (which is for the log lines)
END_LOOK = 30       # a run goes to the website with mesos only with an inventory look this soon after its end...
START_GRACE = 10    # ...and one at or before its start (or this soon after: the first look on a new map)
PAUSED_WAIT = 7     # paused while a run waits for that look: how long it still waits
MAX_LOOKS = 5000    # inventory looks kept (over 40 minutes of them with the inventory open)
MAX_MESOS_HR = 20_000_000     # the website's own ceiling: a run above it is a misread
RUN_ALERT = "Open the inventory now so your run is logged for the website."

# Each line the tracker writes is sealed: a code worked out from its numbers. A line edited
# (or added) by hand doesn't match its seal any more, so it's known not to be the tracker's
# own. (Someone who digs into the program could still forge one, which is why anything
# shared will also be checked against what other players get.)
# (The old name stays in the key: changing it would unseal every log written so far.)
_SEAL_KEY = hashlib.sha256(b"Maple EXP/hr log seal: keep this log honest").digest()
_SEALED_LINE = ("date", "map_read", "level", "party", "class", "exp", "seconds", "mesos", "mesos_seconds")
_SEALED_LINE_POTIONS = _SEALED_LINE + ("potion_cost",)     # lines written with a potion cost (older lines keep their seal as it was)
_SEALED_RUN = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
               "seconds", "mesos", "mesos_seconds", "version")


class AutoLog:
    """EXP/hr and mesos/hr per day, map, level, class and party size, measured over 5-minute
    stretches of non-stop EXP gain, plus 15-minute runs (shared with the stats site, if agreed).

    A stretch starts with an EXP gain and is thrown away if no EXP comes in for `gap`
    seconds, or the map or party size changes. Each finished stretch is added to that day's
    entry for the map, level, class and party size, so the entry gets more accurate the
    longer you play there. Mesos are counted for a stretch when the mesos were read during it.

    Alongside, a run goes on as long as the stretches do (a level-up doesn't stop it, a new
    map or party size does). Every full 15 minutes is saved as a run of its own.

    Every change re-reads the log file first and saves straight away, so lines written by
    anyone else (another copy of the app, or by hand) are never overwritten.
    """

    def __init__(self, gap_seconds, path=LOG_PATH, text_path=LOG_TEXT_PATH):
        self.gap = gap_seconds
        self.path, self.text_path = path, text_path
        self.entries, self.aliases, self.runs = [], {}, []   # aliases: misread/renamed map -> name
        self.install = secrets.token_hex(8)           # this log's own id, part of its seals
        self._kept = set()                            # problems a copy of the file was kept for
        self.looks, self.epoch = [], None             # inventory looks (when, mesos gained so far)
        self.pending = None                           # a finished run waiting for a look after its end
        self._misreads = ()                           # the MesoTracker's misreads, as last given
        version = self._load()
        if version < 2:
            def clear_old_mesos():                    # logged by the old, unreliable mesos counting
                for e in self.entries:
                    if "party" in e:
                        e["mesos"] = e["mesos_seconds"] = 0
            self._change(clear_old_mesos)
        elif version < LOG_VERSION:
            self._change(lambda: None)                # saves this log's id with it
        if any(r.get("pending") for r in self.runs):
            # Closed while a run waited for a look: it goes as it was saved, with EXP only.
            self._change(lambda: [r.pop("pending", None) for r in self.runs])
        self.reset_clock()

    @staticmethod
    def key(e):
        return e["date"], e["map"], e["level"], e.get("party"), e.get("class")

    # ---- seals
    def _seal(self, record, fields):
        data = json.dumps([record.get(k) for k in fields], separators=(",", ":"))
        return hmac.new(_SEAL_KEY + self.install.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]

    @staticmethod
    def _line_fields(e):
        return _SEALED_LINE_POTIONS if "potion_cost" in e else _SEALED_LINE

    def sealed(self, e, fields=None):
        """True for a line exactly as the tracker wrote it."""
        fields = fields or self._line_fields(e)
        return isinstance(e.get("seal"), str) and hmac.compare_digest(e["seal"], self._seal(e, fields))

    def edited(self, e):
        """True for a line that was changed outside the tracker (older lines have no seal)."""
        return "seal" in e and not self.sealed(e)

    def _reseal(self, e, ok):
        e["seal"] = self._seal(e, self._line_fields(e)) if ok else "edited"

    # ---- the file
    def _load(self):
        """Reads the log file into memory (keeps what's there if it can't be read).
        Returns the file's format version."""
        if not self.path.exists():
            return LOG_VERSION
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries, aliases = data.get("entries", []), data.get("aliases", {})
            runs = data.get("runs", [])
            if not isinstance(entries, list) or not isinstance(aliases, dict) or not isinstance(runs, list):
                raise ValueError("unexpected contents")
        except Exception as e:
            self._keep_copy("reading the EXP log", e)        # damaged, e.g. edited by hand
            return LOG_VERSION
        good = [e for e in entries if self._valid(e)]
        if len(good) < len(entries):
            self._keep_copy(f"reading the EXP log: left out {len(entries) - len(good)} damaged lines")
        self.entries = good
        self.aliases = {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}
        self.runs = [r for r in runs if isinstance(r, dict)]   # (only sealed ones are ever shared)
        if isinstance(data.get("install"), str) and re.fullmatch(r"[0-9a-f]{16}", data["install"]):
            self.install = data["install"]
        version = data.get("version", 1)
        return version if type(version) is int else LOG_VERSION

    def _keep_copy(self, what, exc=None):
        """Keeps a copy of the log file before it's saved over (once per problem)."""
        backup = self.path.with_name(f"exp_log.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json")
        if what not in self._kept:
            self._kept.add(what)
            try:
                shutil.copy2(self.path, backup)
            except Exception:
                pass
            log_error(f"{what} (the file was copied to {backup.name})", exc)

    @staticmethod
    def _valid(e):
        """An entry with everything the log needs, each of the right kind, as the overlay writes
        them. Anything else is left out, not crashed on: the file may have been edited by hand."""
        def number(x):
            return type(x) in (int, float) and math.isfinite(x)

        def optional(key, ok, empty=False):           # missing (or empty), or of the right kind
            return key not in e or (empty and e[key] is None) or ok(e[key])
        try:
            date.fromisoformat(e["date"])
            return (isinstance(e["map"], str) and e["map"].strip() != "" and len(e["map"]) <= 200
                    and number(e["exp"]) and number(e["seconds"]) and e["seconds"] >= 0
                    and (e["level"] is None or (type(e["level"]) is int and 1 <= e["level"] <= 300))
                    and optional("party", lambda v: type(v) is int and 1 <= v <= 6, empty=True)
                    and optional("class", lambda v: isinstance(v, str), empty=True)
                    and optional("map_read", lambda v: isinstance(v, str), empty=True)
                    and optional("seal", lambda v: isinstance(v, str))
                    and optional("updated", number)
                    and optional("mesos", number) and optional("mesos_seconds", number)
                    and optional("potion_cost", lambda v: number(v) and v >= 0)
                    and optional("potion_seconds", lambda v: number(v) and v >= 0)
                    and optional("potions", lambda v: isinstance(v, dict))
                    and optional("estimated", lambda v: type(v) is bool)
                    and (not e.get("mesos_seconds") or number(e.get("mesos"))))
        except Exception:
            return False

    def _change(self, change):
        """Applies change() to the latest log on disk and saves it."""
        self._load()
        result = change()
        self.save()
        return result

    # ---- tracking
    def reset_clock(self):
        self.start = self.last_gain = self.block_map = self.block_party = self.meso0 = self.pot0 = None
        self.block_exp = 0
        self.run = None                                  # the 15-minute run going on, if any
        self.chain = None                                # when the runs going on straight after each other began
        self.block_estimated = False                     # some of the stretch's EXP was estimated from a % alone

    def gain(self, now, amount):
        if amount > 0:
            starts = self.start is None
            if starts:
                self.start, self.block_exp = now, 0          # this gain starts the clock
            else:
                self.block_exp += amount
            if self.run is None:                             # ...and a run's
                self.run = {"start": now, "exp": 0, "map": None}
                self.chain = now if self.chain is None else self.chain
            elif not starts:
                self.run["exp"] += amount
            self.last_gain = now
        elif self.start is not None:
            self.block_exp += amount                         # a misread too high, taken back
            if self.run:
                self.run["exp"] += amount

    def tick(self, now, map_name, level, party=1, mesos=None, job=None, map_read=None, hidden=False, potions=None,
             window_at=None, misreads=(), meso_epoch=0, estimated=False):
        """Call regularly while tracking. mesos is the MesoTracker's last look (mesos gained so
        far, when read), however old, or None: a stretch's mesos are those gained between its
        first and last look (a run's: see _site_mesos). job is the class; map_read the map name exactly as read;
        potions ({name: used so far}, their cost so far) or None when potions aren't counted.
        window_at, misreads and meso_epoch: see MesoTracker.for_log. estimated: the EXP now coming in is
        estimated from a % alone (the line and run say so, and such a run is never shared).
        Returns the entry when one was logged."""
        self._note_look(mesos, meso_epoch)
        self._misreads = misreads
        if estimated and self.start is not None:
            self.block_estimated = True
        if self.start is None:
            return None
        if now - self.last_gain > self.gap:
            self.reset_clock()                               # no EXP for too long: start over
            return None
        if self.run and now - self.last_gain > SITE_GAP:
            self.run = self.chain = None                     # ...for a run, 15 s is too long
        if map_name:
            if self.block_map and map_name != self.block_map:
                self.reset_clock()                           # changed maps: start over
                return None
            self.block_map = map_name
        if self.block_party is not None and party != self.block_party:
            self.reset_clock()                               # party size changed: start over
            return None
        self.block_party = party
        if self.meso0 is None and mesos and mesos[1] >= self.start - 2:
            self.meso0 = mesos                               # mesos as the stretch began
        if self.pot0 is None and potions is not None:
            self.pot0 = (dict(potions[0]), potions[1])       # potions used as the stretch began
        self._run_tick(now, map_name, level, party, job, map_read, hidden, window_at, estimated)
        if hidden or now - self.start < LOG_BLOCK:       # (hidden: the map may have changed, so nothing is saved yet)
            return None
        if self.block_exp <= 0:                              # nothing gained after all
            self.start, self.block_exp = now, 0
            return None
        meso_gain = meso_seconds = 0
        if self.meso0 and mesos and mesos[1] > self.meso0[1]:
            meso_gain, meso_seconds = mesos[0] - self.meso0[0], mesos[1] - self.meso0[1]
        used = cost = None
        if potions is not None and self.pot0 is not None:
            cost = max(0, potions[1] - self.pot0[1])
            used = {n: c - self.pot0[0].get(n, 0) for n, c in potions[0].items() if c > self.pot0[0].get(n, 0)}
        entry = self._add(self.block_map or "Unknown map", level, party, self.block_exp,
                          now - self.start, meso_gain, meso_seconds, job, map_read or self.block_map, cost, used,
                          self.block_estimated)
        self.start, self.block_exp, self.block_estimated = now, 0, False       # the next 5 minutes start now
        self.meso0 = mesos if mesos and now - mesos[1] < 10 else None
        self.pot0 = (dict(potions[0]), potions[1]) if potions is not None else None
        return entry

    def _run_tick(self, now, map_name, level, party, job, map_read, hidden=False, window_at=None, estimated=False):
        r = self.run
        if r is None:
            return
        if r["map"] is None:                                 # what the run is about, once known
            if not map_name:
                r["start"] = now                             # no map yet: the run can't start
                r["exp"] = 0
                return
            r.update(map=map_name, map_read=map_read or map_name, party=party, job=job,
                     level_start=level, wall=time.time() - (now - r["start"]),
                     chain=min(self.chain if self.chain is not None else now, r["start"]))
        if job and r["job"] and job != r["job"]:
            self.run = {"start": now, "exp": 0, "map": None}  # a different class: a new run
            self.chain = now
            return
        r["job"] = r["job"] or job
        r["level"] = level
        if estimated:
            r["estimated"] = True                            # (a % alone: kept on this PC, never shared)
        if window_at is not None and window_at >= r["start"]:
            r["window"] = True                               # a shop, storage or trade: not a clean run
        if hidden or now - r["start"] < RUN_SECONDS:
            return
        if r["exp"] > 0:
            self._end_run(r, now)
        self.run = {"start": now, "exp": 0, "map": None}     # straight on into the next run
        self._prune()
        self._run_tick(now, map_name, level, party, job, map_read, hidden, window_at, estimated)

    # ---- what a run takes to the website (fail closed: any doubt and it goes with EXP only)
    def _note_look(self, mesos, epoch):
        if epoch != self.epoch:                              # the mesos count started over: older looks don't compare
            self.epoch, self.looks = epoch, []
            if self.pending:
                self.pending["no_mesos"] = True              # (its looks are gone: it goes with EXP only)
        if mesos and mesos[1] is not None and (not self.looks or mesos[1] > self.looks[-1][0]):
            self.looks.append((mesos[1], mesos[0]))
            if self.run is None and self.pending is None:
                self._prune()
            elif len(self.looks) > MAX_LOOKS:                # (a run waiting or going on for hours: forget the oldest)
                del self.looks[:len(self.looks) - MAX_LOOKS]

    def _prune(self):
        """Forgets the looks no run going on or waiting needs (keeps the last one before them)."""
        live = [r["start"] for r in (self.run, self.pending) if r]
        t = min(live) if live else math.inf
        before = [k for k in self.looks if k[0] <= t]
        self.looks = before[-1:] + [k for k in self.looks if k[0] > t]

    def _end_run(self, r, now):
        """Saves a finished run. One with a shop, storage or trade window seen, or an unknown
        level, is never shared: it keeps the mesos between its first and last look. A clean one
        is saved with EXP only and, given a look at its start, waits for one after its end (settle)."""
        seconds = now - r["start"]
        if r.get("window") or r.get("estimated") or r["level_start"] is None or r["level"] is None:
            inside = [k for k in self.looks if r["start"] - 2 <= k[0] <= now]
            gain = gain_s = 0
            if len(inside) >= 2:
                gain, gain_s = inside[-1][1] - inside[0][1], round(inside[-1][0] - inside[0][0], 1)
            self._add_run(r, seconds, gain, gain_s, local={"window": True} if r.get("window") else {})
            return
        if self.pending:
            self._settle(now)                                # (can't still be waiting, but never lose one)
        waits = self._gained_at(r["start"], r["chain"]) is not None
        run = self._add_run(r, seconds, 0, 0, local={"pending": True} if waits else {})
        if waits:
            self.pending = dict(r, end=now, started=run["started"], paused_at=None)

    def _gained_at(self, t, chain=None, upto=None):
        """Mesos gained by time t, spread evenly between the looks around it: (gained, looks
        used), or None. Needs a look at or before t (not from before `chain`'s runs; or just
        after t when there's none), or with `upto` a look at or after t, no later than upto."""
        before = [k for k in self.looks if k[0] <= t]
        after = [k for k in self.looks if k[0] >= t]
        a = before[-1] if before else None
        b = after[0] if after else None
        if upto is not None:
            if a is None or b is None or b[0] > upto:
                return None
        elif a is None or a[0] < chain - END_LOOK:
            if b is None or b[0] > t + START_GRACE:
                return None
            return b[1], (b,)                                # (a little less than it was: never more)
        if b is None or b[0] == a[0]:
            return a[1], (a,)
        return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]), (a, b)

    def _site_mesos(self, p, upto, misreads):
        """A waiting run's (mesos, mesos seconds) for the website, or (0, 0) when in doubt: no
        look at its start or after its end, a look later proven a misread, below zero, or
        above the site's ceiling."""
        start, end = self._gained_at(p["start"], p["chain"]), self._gained_at(p["end"], upto=upto)
        if start is None or end is None or p.get("no_mesos"):
            return 0, 0
        if any(t0 <= k[0] <= t1 for k in start[1] + end[1] for t0, t1 in misreads):
            return 0, 0
        mesos, seconds = round(end[0] - start[0]), round(p["end"] - p["start"], 1)
        if mesos < 0 or seconds <= 0 or mesos / seconds * 3600 > MAX_MESOS_HR:
            return 0, 0
        return mesos, seconds

    def _end_look(self, p):
        """A waiting run's mesos at its end, given a look soon enough after it (see _gained_at)."""
        return self._gained_at(p["end"], upto=self._deadline(p))

    @staticmethod
    def _deadline(p):
        return p["end"] + END_LOOK if p["paused_at"] is None else p["paused_at"] + PAUSED_WAIT

    def settle(self, now, running, mesos=None, window_at=None, misreads=(), meso_epoch=0):
        """Call regularly, also while paused. A finished run waits for a look after its end:
        while tracking goes on it waits END_LOOK seconds (so a misread can still snap back);
        paused, it goes as soon as the look comes, or after PAUSED_WAIT seconds (see alert).
        Returns True when a run is ready to be shared."""
        self._note_look(mesos, meso_epoch)
        self._misreads = misreads
        p = self.pending
        if p is None:
            return False
        if window_at is not None and window_at >= p["end"]:
            p["no_mesos"] = True                             # a shop, storage or trade before its end look
        if running:
            p["paused_at"] = None
        elif p["paused_at"] is None:
            p["paused_at"] = now
        found = self._end_look(p) is not None
        if now < self._deadline(p) and (running or not found) and not p.get("no_mesos"):
            return False
        self._settle(now, misreads)
        return True

    def _settle(self, now, misreads=None):
        p, self.pending = self.pending, None
        misreads = self._misreads if misreads is None else misreads
        mesos, seconds = self._site_mesos(p, self._deadline(p), misreads)

        def apply():
            for r in self.runs:
                if r.get("started") == p["started"] and r.get("pending"):
                    r.pop("pending")
                    r["mesos"], r["mesos_seconds"] = mesos, seconds
                    r["seal"] = self._seal(r, _SEALED_RUN)
        self._change(apply)
        self._prune()

    def alert(self, now, running):
        """Asks for the inventory when tracking stops while a run waits for a look."""
        p = self.pending
        if (p is None or running or p.get("no_mesos") or self._end_look(p) is not None
                or (self.looks and now - self.looks[-1][0] <= 3)):     # (open already: the look is on its way)
            return None
        return RUN_ALERT

    def run_progress(self, now):
        """Seconds into the 15-minute run going on, or None."""
        r = self.run
        return None if r is None or r["map"] is None else max(0.0, now - r["start"])

    def runs_done(self, map_name, party, job, day=None):
        """How many runs were finished (sealed) on a day for a map, party size and class. A run estimated from a %
        alone carries the seal "estimated" instead: it counts here, it is just never shared."""
        day = day or date.today().isoformat()
        return sum(1 for r in self.runs if r.get("date") == day and r.get("map") == map_name
                   and r.get("party") == party and r.get("class") == job
                   and (self.sealed(r, _SEALED_RUN) or (r.get("estimated") and r.get("seal") == "estimated")))

    def unshared_runs(self):
        """Finished runs (exactly as the overlay saved them) not shared yet: only clean ones
        (no shop, storage or trade window seen, no EXP estimated from a % alone), with a known level, not
        waiting for a look."""
        return [r for r in self.runs if not r.get("shared") and not r.get("window") and not r.get("pending")
                and not r.get("estimated") and r.get("level_start") is not None and r.get("level_end") is not None
                and self.sealed(r, _SEALED_RUN)][:50]

    def mark_shared(self, started):
        def apply():
            for r in self.runs:
                if r.get("started") in started:
                    r["shared"] = True
        self._change(apply)

    def seconds_left(self, now):
        return None if self.start is None else max(0.0, LOG_BLOCK - (now - self.start))

    def _add(self, map_name, level, party, exp, seconds, mesos, meso_seconds, job=None, map_read=None,
             potion_cost=None, potions=None, estimated=False):
        def add():
            key = (date.today().isoformat(), map_name, level, party, job)
            for e in self.entries:
                if self.key(e) == key:
                    ok = self.sealed(e)                      # still exactly the tracker's own?
                    if potion_cost is not None or "potion_cost" in e:     # (the time potions were counted over)
                        e["potion_seconds"] = (e.get("potion_seconds", e["seconds"] if "potion_cost" in e else 0)
                                               + (seconds if potion_cost is not None else 0))
                    e["exp"] += exp
                    e["seconds"] += seconds
                    e["mesos"] = e.get("mesos", 0) + mesos
                    e["mesos_seconds"] = e.get("mesos_seconds", 0) + meso_seconds
                    if potion_cost is not None:
                        e["potion_cost"] = e.get("potion_cost", 0) + potion_cost
                        used = dict(e.get("potions") or {})
                        for n, c in (potions or {}).items():
                            used[n] = used.get(n, 0) + c
                        e["potions"] = used
                    if estimated:
                        e["estimated"] = True                # (not sealed: it only says how the EXP was got)
                    if "seal" in e:
                        self._reseal(e, ok)
                    break
            else:
                e = {"date": key[0], "map": map_name, "map_read": map_read or map_name, "level": level,
                     "party": party, "class": job, "exp": exp, "seconds": seconds, "mesos": mesos,
                     "mesos_seconds": meso_seconds}
                if potion_cost is not None:
                    e["potion_cost"], e["potions"], e["potion_seconds"] = potion_cost, dict(potions or {}), seconds
                if estimated:
                    e["estimated"] = True
                self._reseal(e, True)
                self.entries.append(e)
            e["updated"] = time.time()
            return e
        return self._change(add)

    def _add_run(self, r, seconds, mesos, meso_seconds, local=None):
        run = {"date": date.fromtimestamp(r["wall"]).isoformat(),
               "started": time.strftime("%Y-%m-%dT%H:%M", time.localtime(r["wall"])),
               "map": r["map"], "map_read": r["map_read"], "class": r["job"],
               "level_start": r["level_start"], "level_end": r["level"], "party": r["party"],
               "exp": r["exp"], "seconds": round(seconds, 1), "mesos": mesos,
               "mesos_seconds": round(meso_seconds, 1), "version": VERSION}
        run["seal"] = self._seal(run, _SEALED_RUN)
        local = dict(local or {})
        if r.get("estimated"):
            # An older overlay doesn't know the flag and would share a sealed run: a seal that never verifies stops it.
            run["seal"], local["estimated"] = "estimated", True
        run.update(local)                                    # (kept on this PC only: not sealed, never sent)
        self._change(lambda: self.runs.append(run))
        return run

    def canonical(self, name, also=()):
        """The name to log a map under: applies renames and fixes small misreads (matching
        the maps in the log, and any names in `also`, e.g. the map you were just on)."""
        name = self.aliases.get(name, name)
        best, score = name, 0.0
        for known in {e["map"] for e in self.entries} | set(self.aliases.values()) | {n for n in also if n}:
            if _numbers(known) == _numbers(name):
                s = SequenceMatcher(None, known.lower(), name.lower()).ratio()
                if s > score:
                    best, score = known, s
        return best if score >= 0.85 else name

    def rename(self, entry, new_name):
        """Renames a map (and remembers it for future reads). The map name as read stays on
        record (map_read), so renamed lines keep their seal."""
        old, key = entry["map"], self.key(entry)

        def apply():
            if old == "Unknown map":
                for e in self.entries:                       # just this one
                    if self.key(e) == key:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
            else:
                self.aliases[old] = new_name                 # and future reads of the old name
                for k, v in self.aliases.items():
                    if v == old:
                        self.aliases[k] = new_name
                for e in self.entries:
                    if e["map"] == old:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
                for r in self.runs:
                    if r.get("map") == old:
                        r["map"] = new_name                  # (runs are sealed by map_read)
            merged = {}
            for e in self.entries:                           # combine entries that now match
                if self.key(e) in merged:
                    m = merged[self.key(e)]
                    ok = self.sealed(m) and self.sealed(e)
                    for field in ("exp", "seconds", "mesos", "mesos_seconds", "potion_cost"):
                        if field in e:
                            m[field] = m.get(field, 0) + e[field]
                    if e.get("potions"):
                        m["potions"] = {n: (m.get("potions") or {}).get(n, 0) + (e["potions"] or {}).get(n, 0)
                                        for n in {*(m.get("potions") or {}), *e["potions"]}}
                    m["updated"] = max(m.get("updated", 0), e.get("updated", 0))
                    if "seal" in m or "seal" in e:
                        self._reseal(m, ok)
                    both = notes_of([m, e])                   # a note on either line is kept
                    if both and both != m.get("note"):
                        m["note"] = both
                else:
                    merged[self.key(e)] = e
            self.entries = list(merged.values())

        self._change(apply)
        if old != "Unknown map" and self.block_map == old:
            self.block_map = new_name
        if old != "Unknown map" and self.run and self.run.get("map") == old:
            self.run["map"] = new_name

    def set_note(self, entries, text):
        """Writes a personal note on these log lines (the same one on each: e.g. a map's party
        sizes shown as one line). An empty note takes it off. Returns True if anything changed.

        A note only ever lives in this log file, on this PC. It isn't one of the sealed fields,
        so a line stays as trusted as it was, and it's never part of a run: what's shared with
        the stats site is made from the runs alone (see post_runs)."""
        keys = {self.key(e) for e in entries}
        text = clean_note(text)[:NOTE_MAX]

        def apply():
            changed = False
            for e in self.entries:
                if self.key(e) not in keys:
                    continue
                if text and e.get("note") != text:
                    e["note"], changed = text, True
                elif not text and "note" in e:
                    del e["note"]
                    changed = True
            return changed

        return self._change(apply)

    def delete(self, entry):
        key = self.key(entry)

        def apply():
            self.entries = [e for e in self.entries if self.key(e) != key]

        self._change(apply)

    def newest_first(self):
        return sorted(self.entries, key=lambda e: e.get("updated") or 0, reverse=True)

    def _back_up(self):
        """Before the first save of each day: a copy of the log as it was, in backups/ (the last
        few days are kept), so that whatever goes wrong, a recent copy of it all, notes
        included, is still there. To go back to one, close the overlay and copy it over
        exp_log.json."""
        try:
            if not self.path.exists():
                return
            folder = self.path.parent / "backups"
            copy = folder / f"exp_log-{date.today().isoformat()}.json"
            if copy.exists():
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))     # (a damaged file has its own copy)
            if not (data.get("entries") or data.get("runs")):
                return                                                  # nothing in it worth a backup
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, copy)
            for old in sorted(folder.glob("exp_log-*.json"))[:-BACKUPS_KEPT]:
                old.unlink()
        except Exception as e:
            log_error("backing up the EXP log", e)

    def save(self):
        self._back_up()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": LOG_VERSION, "install": self.install, "entries": self.entries,
                                       "aliases": self.aliases, "runs": self.runs}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self.text_path.write_text("".join(format_entry(e) + "\n" for e in self.newest_first()),
                                      encoding="utf-8")
        except Exception as e:
            log_error("saving the EXP log", e)


# --------------------------------------------------------------------------- UI helpers

def fmt_num(n):
    return "—" if n is None else f"{n:,.0f}"


def fmt_short(n):
    if n is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.2f}{suffix}"
    return f"{n:,.0f}"


def approx_num(n, estimated):
    """A number as shown: with an approx sign in front when it was worked out from a % alone. A very big one is
    shortened (99,999,999 -> 100.00M) so the sign never makes it run out of its box."""
    if n is None or not estimated:
        return fmt_num(n)
    return APPROX + (fmt_num(n) if abs(n) < 10_000_000 else fmt_short(n))


def fmt_eta(seconds):
    """A time to wait, the way people say it: '36 min', '2 h 05 min'. None if too far off."""
    if seconds is None or seconds < 0 or seconds >= 100 * 3600:
        return None
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_dur(seconds):
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RegionSelector:
    """Full-screen translucent overlay; drag a box around the EXP bar text."""

    def __init__(self, root, on_done):
        self.on_done = on_done
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.origin = (v["left"], v["top"])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, bg="black", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(v["width"] // 2, 60, fill="white", font=(PLATFORM.font("Segoe UI", "bold")[0], 20, PLATFORM.font("Segoe UI", "bold")[1]),
                                text="Drag a box around the EXP numbers  (Esc to cancel)")
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ffcc00", width=2)

    def _drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, *self.start, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, x1 = sorted((self.start[0], e.x))
        y0, y1 = sorted((self.start[1], e.y))
        if x1 - x0 < 10 or y1 - y0 < 5:
            self._finish(None)
            return
        self._finish({"left": self.origin[0] + x0, "top": self.origin[1] + y0,
                      "width": x1 - x0, "height": y1 - y0})

    def _finish(self, region):
        self.win.destroy()
        self.on_done(region)


# --------------------------------------------------------------------------- app

FIELD = RAISED              # inputs and buttons
# The window's fonts: (family, size at 100%, weight, underline). Settings > Size scales them all.
FONTS = {"small": ("Manrope", 9, "normal", False), "text": ("Manrope", 10, "normal", False),
         "link": ("Manrope SemiBold", 10, "normal", False), "row": ("Manrope", 10, "normal", False),
         "icon": ("Segoe UI", 12, "normal", False), "value": ("Manrope Bold", 11, "normal", False),
         "next": ("Manrope SemiBold", 11, "normal", False), "title": ("Manrope Bold", 13, "normal", False),
         "caps": ("Manrope SemiBold", 9, "normal", False), "strong": ("Manrope SemiBold", 10, "normal", False),
         "big": ("Manrope ExtraBold", 32, "normal", False)}
FONT_FILES = ("Manrope.ttf", "ManropeSemiBold.ttf", "ManropeBold.ttf", "ManropeExtraBold.ttf")
FALLBACK_FONT = {"Manrope": "Segoe UI", "Manrope SemiBold": "Segoe UI Semibold", "Manrope Bold": "Segoe UI Semibold",
                 "Manrope ExtraBold": "Segoe UI Semibold"}
LOG_HINT = "Click a line to write a note about it. Right-click to copy, rename or delete it."


class App:
    def __init__(self, root, templates):
        self.root = root
        self._fresh = not CONFIG_PATH.exists()          # a first install: it picks a compact shape
        self.cfg = {**DEFAULTS, **load_config()}
        mode = self.cfg.get("mode", "window")
        self.reader = ScreenReader(templates, mode, self.cfg.get("region"))
        saved = self.cfg.get("mesos_box")
        if isinstance(saved, list) and len(saved) == 6:
            self.reader.saved_mesos = tuple(saved)
        boxes = self.cfg.get("mesos_boxes") or {}
        if isinstance(boxes, dict):
            self.reader.saved_mesos_boxes = {k: tuple(v) for k, v in boxes.items()
                                            if isinstance(v, list) and len(v) == 4}
            # A single legacy mesos_box fills the dict so a resize back to that size still finds it.
            if isinstance(saved, list) and len(saved) == 6:
                key = f"{saved[0]}x{saved[1]}"
                self.reader.saved_mesos_boxes.setdefault(key, tuple(saved[2:]))
        self.reader.saved_v2 = dict(self.cfg.get("exp_v2_spots") or {})
        self.reader.shadow.on = self.cfg["shadow_reader"]           # (reader v2 in shadow mode: off with shadow_reader false)
        self.session = Session(self.cfg["gap_seconds"])
        self.tracker = self.session.exp
        self.reader.meso_mode = self.cfg.get("meso_mode", "notices")
        self.party = 1
        self._party_seen = (None, 0)
        self.last_mesos = None
        self.rejected = 0
        self.started, self.cpu0 = time.monotonic(), time.process_time()
        self.recent, self.health = Recent(40, self.started), Health()      # for bug reports
        self.autolog = AutoLog(self.cfg["gap_seconds"])
        self.map_name = None
        self.potion_bar = ()                # what the potion bar showed at the last look: potions.Slot tuple
        self._potions_seen_at = None        # when the potion bar last read successfully
        self._exp_seen_at = None            # when the EXP bar last read cleanly
        self.in_town = False                # on a town map: nobody farms there, so nothing is logged
        self.level = None
        self.note = None
        self.last_logged = None
        self.skip_log = False               # "Don't log" is on: this run isn't logged
        self._sharing, self._next_share = False, 0.0       # sending runs to the site
        self.healthlog = healthlog.HealthLog(HEALTH_PATH)   # counts for the daily health report
        self._health_sending, self._health_at, self._health_snap_at = False, None, 0.0
        self._health_monitors, self._health_errors = [], {}
        self.job = None                     # the class, from the status bar
        self.map_read = None                # the map name exactly as read
        self.map_hidden, self.map_back_at = False, None     # the minimap's name out of sight for a few seconds
        self.found_at = None                # when the EXP bar was first read for this level
        self.level_wait = False             # a %-only bar is waiting for its level
        self.pct_note = ""                  # what the tracker last said about a %-only reading
        self._char_print = None             # the name row's print when the level was last known (memory only)
        self._char_seen = None              # the newest print looked at, so each is counted once
        self.name_note = ""                 # what to say about a covered or partly covered name row
        self._stale_level = None            # (level, when) of the character just left: a queued reading may still carry it
        self._char_diff = 0                 # prints in a row that differ from _char_print
        self._same_char = False             # the newest print matches the one from when the level was known
        self.other_char_at = None           # when another character was last noticed (the status says so for a while)
        self.level_btn = None               # Settings > Level fallback (enabled only when it can help)
        self.level_up_at = None
        self.died_at = None
        self.log_open = False
        self.settings_win = self.prices_win = None
        self._price_table = None
        self._refresh_notice_prices()
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.last_capture = None

        # The typeface is bundled with the overlay and made available to this program only.
        self._own_fonts = PLATFORM.register_fonts([ASSETS / "fonts" / n for n in FONT_FILES])
        family_of = lambda f: f if self._own_fonts else FALLBACK_FONT.get(f, f)
        self.fonts = {name: tkfont.Font(root, underline=underline, **dict(zip(("family", "weight"),
                                                                               PLATFORM.font(family_of(family), weight))))
                      for name, (family, _, weight, underline) in FONTS.items()}
        pos = self.cfg.get("pos")
        self._dpi = (PLATFORM.screen_dpi(pos[0] + 40, pos[1] + 15) if pos else None) or root.winfo_fpixels("1i")
        self._apply_ui_scale()
        self.hotkey_note = None
        self.hotkeys = PLATFORM.create_hotkeys(lambda action: self.q.put(("hotkey", action)))
        self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        self.compact = False

        root.title(WINDOW_TITLE)
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass
        root.report_callback_exception = lambda kind, exc, tb: self._oops("a button or menu", exc)
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._apply_window_settings()
        self._restore_position()

        setup_ttk(root)
        PLATFORM.style_window(root, INK, TEXT_DIM)          # a title bar in the overlay's own colours
        self._trend, self._trend_at, self._start_text = deque(maxlen=60), 0.0, "Start"
        self.status = tk.StringVar()
        self.status.trace_add("write", lambda *_: self.view.show(message=self.status.get()) if hasattr(self, "view") else None)
        root.columnconfigure(1, weight=1)                 # the log gets any extra room
        root.rowconfigure(0, weight=1)
        self._build_view()
        self._show_skip_log()
        for delay in (0, 150, 800):                        # (Windows can reset it while the window is first shown)
            root.after(delay, lambda: PLATFORM.style_window(root, INK, TEXT_DIM))

        self._build_log_panel()
        if self.cfg["compact"]:
            self.set_compact(True)
        elif self.cfg.get("log_open"):
            self.toggle_log()

        if not templates:
            self.status.set("Some of the program's files are missing, so EXP reading is less "
                            "reliable. Please download it again.")
        else:
            self.status.set("Looking for the MapleStory window..." if mode == "window"
                            else "Using your selected area.")

        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        root.after(200, self._pump)
        root.after(500, self._refresh)
        if self.cfg["check_updates"]:
            self.check_updates()                       # only now, when it starts
        root.after(1500, self._first_run)              # (once: the compact shape or what's new, then the sharing question)

    # ---- the main window
    def _build_view(self):
        """(Re)builds the main window at the current size: it's drawn smoothly for each screen's scaling
        and for Settings > Size, so a new size means new pictures."""
        if getattr(self, "view", None):
            self.view.destroy()
        self.theme = Theme(self.root, self._px_per_pt * 0.75, ASSETS / "fonts", self._own_fonts, PLATFORM.font)
        callbacks = dict(settings=self.open_settings, compact=lambda: self.set_compact(not self.compact),
                         log=self.toggle_log, start=self.toggle, reset=self.reset, logging=self._logging_clicked,
                         best=self.open_best_maps, menu=self._open_menu, details=self._details_changed)
        self.view = self.main = MainView(self.root, self.theme, callbacks, details=bool(self.cfg.get("details")))
        self.view.grid(row=0, column=0, sticky="n")
        if self.compact:
            self.view.set_compact(True, self.cfg["compact_shape"])
        self._set_start(self._start_text)
        self.view.log_btn.set(kind="secondary" if self.log_open else "ghost")
        self.view.show(message=self.status.get())
        self._show_skip_log()

    def _style_frame(self):
        """The title bar in the overlay's colours. (Windows resets it when the frame changes, e.g. when the
        log opens or it goes compact, so this runs again a moment after.)"""
        for delay in (30, 300):
            self.root.after(delay, lambda: PLATFORM.style_window(self.root, INK, TEXT_DIM))

    def _details_changed(self, is_open):
        self.cfg["details"] = is_open
        save_config(self.cfg)

    def _open_menu(self, anchor):
        PopupMenu(self.theme, anchor, [
            ("Follow the game" if self.reader.mode == "region" else "Select area", None, self.toggle_area),
            ("View capture", None, self.view_capture)])

    def _logging_clicked(self, on):
        if self.skip_log == on:                           # the switch and the state disagree: flip
            self.toggle_skip_log()

    # ---- log panel
    def _build_log_panel(self):
        p = self.log_panel = tk.Frame(self.root, bg=BG)
        tk.Label(p, text="EXP log", bg=BG, fg=FG, font=self.fonts["title"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.log_status = tk.StringVar()
        tk.Label(p, textvariable=self.log_status, bg=BG, fg=DIM, font=self.fonts["text"],
                 justify="left", anchor="w").grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 10))

        # A compact table: one row per day, map, level and class, solo or in a party (click a
        # party row for a row per party size). Drag the lines between the column titles to
        # resize a column; widths are remembered.
        cell, head = self.fonts["text"], self.fonts["small"]
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Log.Treeview", background=LIST_BG, fieldbackground=LIST_BG, foreground=FG,
                        font=cell, rowheight=int(cell.metrics("linespace") * 1.8), borderwidth=0)
        style.configure("Log.Treeview.Heading", background=BG, foreground=DIM, relief="flat",
                        borderwidth=0, font=self.fonts["caps"], padding=(0, 6))
        style.map("Log.Treeview", background=[("selected", SELECT)], foreground=[("selected", ACCENT)])
        style.map("Log.Treeview.Heading", background=[("active", BG)])
        # (key, title, widest usual value, alignment): starting widths are measured in the
        # real font, so nothing is cut off at any screen scaling.
        columns = (("date", "Date", "Sep 30", "w"), ("map", "Map", "The Swamp of Despair II", "w"),
                   ("lv", "Lv", "99", "center"), ("class", "Class", "Crossbowman", "w"),
                   ("party", "Party", "2–6ppl ▸", "center"),
                   ("time", "Time", "12h 30m", "center"),
                   ("exp", "EXP/hr", "888,888", "e"), ("mesos", "Mesos/hr", "888,888", "e"),
                   ("net", "Net/hr", "888,888", "e"), ("note", "Note", NOTE_MARK, "center"))
        # Saved widths are in points, so they look the same on screens with different scaling
        # (older versions saved pixels: those are used as they are, once).
        saved = {k: round(v * self._px_per_pt) for k, v in (self.cfg.get("log_columns_pt") or {}).items()
                 if type(v) in (int, float)} or self.cfg.get("log_columns") or {}
        pad = cell.measure("00")
        self._log_titles = {c[0]: c[1] for c in columns}
        tree = self.log_tree = ttk.Treeview(p, columns=[c[0] for c in columns], show="headings",
                                            style="Log.Treeview", height=11, selectmode="browse")
        for key, title, sample, anchor in columns:
            width = max(cell.measure(sample), head.measure(title)) + pad
            if isinstance(saved.get(key), int) and 10 <= saved[key] <= 2000:
                # (Party is never narrower than a merged party row: older versions' Party
                # column was sized for "6ppl".)
                width = max(saved[key], width) if key == "party" else saved[key]
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width, minwidth=pad, anchor=anchor, stretch=False)
        tree.tag_configure("odd", background=LIST_BG2)
        tree.tag_configure("part", background=LIST_PART, foreground="#b3b8c0")   # one party size
        tree.tag_configure("edited", foreground="#6b7079")      # changed outside the tracker
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(p, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        # Notes: a place for the player's own notes on the selected line. They're only ever kept
        # in the log file on this PC (never shared), and saved as they're typed.
        self._note_key = self._note_entries = self._note_job = None
        self._note_dirty, self._note_label = False, ""
        nb = tk.Frame(p, bg=BG)
        nb.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        nb.columnconfigure(0, weight=1)
        self.note_title = tk.Label(nb, text="", bg=BG, fg=DIM, font=head, anchor="w", justify="left")
        self.note_title.grid(row=0, column=0, sticky="we")
        self.note_text = tk.Text(nb, height=3, wrap="word", undo=True, maxundo=100, bg=LIST_BG, fg=FG,
                                 insertbackground=FG, relief="flat", font=cell, padx=8, pady=6,
                                 highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        self.note_text.grid(row=1, column=0, sticky="we", pady=(3, 0))
        self.note_text.bind("<<Modified>>", self._note_modified)
        self.note_text.bind("<FocusIn>", lambda e: self.hotkeys.pause(True))      # typing "!" isn't Shift+1
        self.note_text.bind("<FocusOut>", lambda e: (self.hotkeys.pause(False), self._note_flush()))
        self._note_show(None)
        self.log_hint = tk.StringVar(value=LOG_HINT)
        hint = tk.Label(p, textvariable=self.log_hint, bg=BG, fg=DIM, font=head, justify="left", anchor="w")
        hint.grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))

        def fit(e):
            hint.config(wraplength=max(120, e.width - 8))
            self.note_title.config(wraplength=max(120, e.width - 8))
        p.bind("<Configure>", fit)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(2, weight=1)
        tree.bind("<Button-3>", self._log_menu)
        tree.bind("<<TreeviewSelect>>", self._log_selected)
        tree.bind("<Button-1>", self._log_click)
        tree.bind("<<TreeviewOpen>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        tree.bind("<<TreeviewClose>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        self.shown, self.groups, self.open_groups = {}, {}, set()
        self._fill_log()

    def toggle_log(self, remember=True):
        if self.log_open:
            self._note_flush()
            self._remember_log_size()                   # before it closes
        self.log_open = not self.log_open
        root = self.root
        if self.log_open:
            # With the log open the window can be resized (e.g. for long map names).
            self.log_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=(10, 8))
            root.update_idletasks()
            root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
            root.resizable(True, True)
            size = self.cfg.get("log_size")
            root.geometry(f"{size[0]}x{size[1]}" if size else "")
        else:
            self.log_panel.grid_remove()
            root.resizable(False, False)
            root.minsize(1, 1)
            root.geometry("")                           # back to just the tracker's size
        self.view.log_btn.set(kind="secondary" if self.log_open else "ghost")
        self._style_frame()
        if remember:
            self.cfg["log_open"] = self.log_open
            save_config(self.cfg)

    # ---- compact mode and size
    def set_compact(self, on):
        """Compact mode: just EXP/hr, when you'll level, the last 5 minutes and mesos/hr on one
        line, and Start/Pause, in a much smaller window. The Compact button (it says Full in
        compact mode), Shift+4, or double-clicking the big number switches it."""
        self.compact = self.cfg["compact"] = on
        if on and self.log_open:
            self.toggle_log(remember=False)                 # out of the way, but remembered
        self.view.set_compact(on, self.cfg["compact_shape"])
        self._style_frame()
        if not on and self.cfg.get("log_open"):
            self.toggle_log(remember=False)
        else:
            self.root.geometry("")
        save_config(self.cfg)

    def set_shape(self, shape):
        """Settings > Compact shape: Square, Wide or Bar. Only compact mode looks different."""
        if shape not in SHAPES:
            return
        self.cfg["compact_shape"] = shape
        if self.compact:
            self.view.set_compact(True, shape)
            self._style_frame()
            self.root.geometry("")
        save_config(self.cfg)

    def _apply_ui_scale(self):
        """Sizes every font by Settings > Size and for the screen the window is on (screens can
        have different Windows scaling), and the log's rows and columns with them."""
        old, self._px_per_pt = getattr(self, "_px_per_pt", None), self.cfg["ui_scale"] / 100 * self._dpi / 72
        for name, (_, size, _, _) in FONTS.items():
            # In pixels (negative), never under 9 so the smallest text stays readable.
            self.fonts[name].configure(size=-max(9, round(size * self._px_per_pt)))
        if old is not None and getattr(self, "view", None):
            self._build_view()
        if old is None or not hasattr(self, "log_tree"):    # at start, before the window is built
            return
        self._fit_settings()
        ttk.Style(self.root).configure("Log.Treeview", rowheight=int(self.fonts["text"].metrics("linespace") * 1.8))
        head, pad = self.fonts["caps"], self.fonts["text"].measure("00")
        for c in self.log_tree["columns"]:
            width = round(self.log_tree.column(c, "width") * self._px_per_pt / old)
            # (text doesn't shrink as far as the window does, so a column keeps room for its heading)
            self.log_tree.column(c, width=max(10, width, head.measure(self._log_titles.get(c, "")) + pad))
        self.cfg["log_size"] = None                         # a size from before may not fit any more
        if self.log_open:
            self.root.update_idletasks()
            self.root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
        self.root.geometry("")

    def _fit_settings(self):
        """Settings is scrollable and resizable; nothing to force-fit after a DPI change."""
        return

    def _check_screen(self):
        """Resizes the text when the window is moved to a screen with different Windows scaling
        (judged by its title bar, which stays put when the window changes size)."""
        r = self._window_rect()
        dpi = PLATFORM.screen_dpi(r[0] + 40, r[1] + 15) if r else None
        if dpi and dpi != self._dpi:
            self._dpi = dpi
            self._apply_ui_scale()

    def _fill_log(self):
        """The log, newest first. A map's party sizes on a day share a row (smallest to biggest
        party, all their time together); clicking it shows a row for each party size."""
        tree = self.log_tree
        self._note_flush()                                  # a note being typed is saved first,
        keep, rows = self._selected_key(), {}               # and the same line stays selected
        tree.delete(*tree.get_children())
        self.shown, self.groups = {}, {}
        edited = lambda e: ("edited",) if self.autolog.edited(e) else ()
        for i, (key, entries) in enumerate(party_groups(self.autolog.newest_first())):
            stripe = ("odd",) if i % 2 else ()
            if len(entries) == 1:
                iid = tree.insert("", "end", values=log_row(entries[0]), tags=stripe + edited(entries[0]))
                self.shown[iid] = entries[0]
                rows[self._line_key(entries[0])] = iid
                continue
            is_open = key in self.open_groups
            all_edited = all(self.autolog.edited(e) for e in entries)
            iid = tree.insert("", "end", values=group_row(entries, is_open), open=is_open,
                              tags=stripe + (("edited",) if all_edited else ()))
            self.groups[iid] = (key, entries)
            rows[key] = iid
            for e in entries:
                child = tree.insert(iid, "end", values=log_row(e, part=True), tags=("part",) + edited(e))
                self.shown[child] = e
                rows[self._line_key(e)] = child
        if keep in rows:
            tree.selection_set(rows[keep])
        else:
            self._note_show(None)                           # that line is gone (renamed, merged, deleted)
        self.log_hint.set(LOG_HINT if self.shown else "No logs yet. Press Start and gain EXP for 5 minutes.")

    # ---- personal notes on log lines
    @staticmethod
    def _line_key(e):
        return ("line",) + AutoLog.key(e)

    def _selected_entries(self):
        """(a key for the selected row, the log lines it stands for), or (None, None)."""
        sel = self.log_tree.selection()
        if not sel:
            return None, None
        if sel[0] in self.groups:
            return self.groups[sel[0]]
        e = self.shown.get(sel[0])
        return (self._line_key(e), [e]) if e else (None, None)

    def _selected_key(self):
        return self._selected_entries()[0]

    def _note_show(self, key, entries=None):
        """Puts the selected line's note in the box (or empties it, with nothing selected)."""
        t = self.note_text
        if key is not None and key == self._note_key:
            self._note_entries = entries                    # the same line (the log was refilled): leave
            return                                          # the text, and whoever is typing in it, alone
        self._note_key, self._note_entries, self._note_dirty = key, entries, False
        t.config(state="normal")
        t.delete("1.0", "end")
        if key is None:
            self._note_label = ""
            self.note_title.config(text="Notes: click a line to write one (they stay on this PC).")
            t.config(state="disabled", bg=LIST_PART)
        else:
            first, party = entries[0], entries[0].get("party")
            where = f"{log_row(first)[0]} \u00b7 {first['map']}" + (f" \u00b7 {party}ppl" if len(entries) == 1 and party and party > 1 else "")
            self._note_label = f"Note for {where} (kept on this PC only)"
            self.note_title.config(text=self._note_label)
            t.insert("1.0", notes_of(entries))
            t.config(bg=LIST_BG)
        t.edit_reset()
        t.edit_modified(False)

    def _note_modified(self, _event=None):
        t = self.note_text
        if not t.edit_modified():
            return
        t.edit_modified(False)
        if self._note_key is None:
            return
        over = len(t.get("1.0", "end-1c")) - NOTE_MAX
        if over > 0:                                        # too long: what doesn't fit isn't kept
            t.delete(f"end-1c-{over}c", "end-1c")
            self.note_title.config(text=f"A note can be {NOTE_MAX:,} characters at most.")
        else:
            self.note_title.config(text=self._note_label)
        self._note_dirty = True
        if self._note_job:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(900, self._note_flush)

    def _note_flush(self):
        """Saves the note being typed, if it was changed (as you type, when you click another
        line or leave the box, and when the log or the overlay closes)."""
        if self._note_job:
            self.root.after_cancel(self._note_job)
            self._note_job = None
        if not self._note_dirty or self._note_key is None:
            return
        self._note_dirty = False
        text = clean_note(self.note_text.get("1.0", "end-1c"))[:NOTE_MAX]
        try:
            self.autolog.set_note(self._note_entries, text)
        except Exception as e:
            log_error("saving a note", e)
            self._note_dirty = True
            self.note_title.config(text="Couldn't save the note (see errors.log).")
            return
        for e in self._note_entries:                        # the rows on screen
            if text:
                e["note"] = text
            else:
                e.pop("note", None)
        self._note_marks()
        self.note_title.config(text=f"{self._note_label} \u00b7 saved")

    def _note_marks(self):
        """The Note column: a mark on the lines that have a note."""
        for iid, (_, entries) in self.groups.items():
            self.log_tree.set(iid, "note", NOTE_MARK if notes_of(entries) else "")
        for iid, e in self.shown.items():
            self.log_tree.set(iid, "note", NOTE_MARK if note_of(e) else "")

    def _log_click(self, event):
        """Clicking a merged party row opens it (a row per party size) or closes it."""
        iid = self.log_tree.identify_row(event.y)
        if iid in self.groups and self.log_tree.identify_region(event.x, event.y) == "cell":
            self.log_tree.item(iid, open=not self.log_tree.item(iid, "open"))
            self._sync_group(iid)

    def _sync_group(self, iid):
        """After a party row opens or closes: its arrow, and remembering it for next time."""
        if iid not in self.groups:
            return
        key, entries = self.groups[iid]
        is_open = bool(self.log_tree.item(iid, "open"))
        self.open_groups.add(key) if is_open else self.open_groups.discard(key)
        self.log_tree.item(iid, values=group_row(entries, is_open))
        if self.log_tree.selection() == (iid,):
            self._log_selected(None)

    def _log_selected(self, _event):
        self._note_flush()                                  # the line left is saved first
        key, entries = self._selected_entries()
        self._note_show(key, entries)
        sel = self.log_tree.selection()
        group = self.groups.get(sel[0]) if sel else None
        if group:
            entries = group[1]
            sizes = ", ".join(str(e["party"]) for e in entries[:-1]) + f" and {entries[-1]['party']}"
            runs = sum(self.autolog.runs_done(e["map"], e["party"], e.get("class"), e["date"]) for e in entries)
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            what = "hide" if self.log_tree.item(sel[0], "open") else "see"
            self.log_hint.set(f"{entries[0]['map']}: parties of {sizes}, over {fmt_logged(sum(e['seconds'] for e in entries))} "
                              f"of non-stop EXP gain in all.{hours} Click it to {what} each party size. "
                              f"Right-click to copy, rename or delete.")
            return
        e = self.shown.get(sel[0]) if sel else None
        if e:
            if self.autolog.edited(e):
                self.log_hint.set("This line was changed outside the overlay (it no longer matches "
                                  "the overlay's seal), so it won't count toward the best-maps site.")
                return
            runs = self.autolog.runs_done(e["map"], e.get("party"), e.get("class"), e["date"])
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            self.log_hint.set(f"{e['map']}: measured over {fmt_logged(e['seconds'])} of non-stop EXP gain."
                              f"{hours} Right-click to copy, rename or delete.")

    def _log_menu(self, event):
        """Right-click: copy, rename or delete a line (a merged party row: all its party sizes)."""
        iid = self.log_tree.identify_row(event.y)
        entries = self.groups[iid][1] if iid in self.groups else [self.shown[iid]] if iid in self.shown else None
        if not entries:
            return
        self.log_tree.selection_set(iid)
        many = len(entries) > 1
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy lines" if many else "Copy line", command=lambda: self._copy(entries))
        menu.add_command(label="Rename map...", command=lambda: self._rename(entries))
        menu.add_command(label=f"Delete all {len(entries)} party sizes" if many else "Delete entry",
                         command=lambda: self._delete(entries))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy(self, entries):
        text = "\n".join(format_entry(e) for e in entries)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log_hint.set("Copied: " + text if len(entries) == 1 else f"Copied {len(entries)} lines, one per party size.")

    def _rename(self, entries):
        self._note_flush()
        self.hotkeys.pause(True)                            # typing "!" in a name isn't Shift+1
        try:
            name = simpledialog.askstring("Rename map", "Map name:", initialvalue=entries[0]["map"],
                                          parent=self.root)
        finally:
            self.hotkeys.pause(False)
        if name and name.strip():
            for entry in entries:             # (one rename covers them all, unless it's "Unknown map")
                if entry["map"] != name.strip():
                    self.autolog.rename(entry, name.strip())
            if self.map_name:
                self.map_name = self.autolog.canonical(self.map_name)
            s = self.session                    # so a renamed map isn't taken as a new one
            s.map, s.here = (self.autolog.canonical(n) if n else n for n in (s.map, s.here))
            self._fill_log()

    def _delete(self, entries):
        self._note_flush()
        lines = "\n".join(format_entry(e) for e in entries)
        question = f"Delete these {len(entries)} log entries?" if len(entries) > 1 else "Delete this log entry?"
        if any(note_of(e) for e in entries):
            question += " The note" + ("s" if len(entries) > 1 else "") + " on " + ("them" if len(entries) > 1 else "it") + " will be deleted too."
        if messagebox.askyesno("Delete entry", f"{question}\n\n{lines}", parent=self.root):
            for entry in entries:
                self.autolog.delete(entry)
            self._fill_log()

    def _log_status_text(self, now):
        where = " · ".join(x for x in (self.map_name or "Map not read yet", f"Lv {self.level or '?'}",
                                       self._class_name(), party_label(self.party)) if x)
        if self.skip_log:
            return f"{where}\nNot logging this run."
        if not self.session.on:
            return f"{where}\nPress Start to log EXP/hr here."
        if self.session.waiting:
            return f"{where}\nPaused. Logging carries on at your next EXP gain."
        left = self.autolog.seconds_left(now)
        if left is None:
            text = f"{where}\nWaiting for EXP gain..."
        else:
            text = f"{where}\nNext log in {int(left) // 60}:{int(left) % 60:02d} of non-stop EXP gain"
        if self.last_logged and now - self.last_logged < 8:
            text += "  (just logged)"
        run = self.autolog.run_progress(now)
        done = self.autolog.runs_done(self.map_name, self.party, self._class_name()) if self.map_name else 0
        if run is not None or done:
            line = f"{RUN_SECONDS // 60}-min run: {int(run // 60) if run is not None else 0} of {RUN_SECONDS // 60} min"
            text += f"\n{line}" + (f" · {done} done here today" if done else "")
        return text

    # ---- settings
    def _apply_window_settings(self):
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.attributes("-alpha", 1 - min(MAX_TRANSPARENCY, max(0, int(self.cfg["transparency"]))) / 100)

    def _switch(self, parent, text, var):
        """An on/off switch tied to a BooleanVar."""
        t = Toggle(parent, self.theme, text, bool(var.get()), lambda on: var.set(on), BG)
        var.trace_add("write", lambda *_: t.set(bool(var.get())))
        return t

    def _section(self, parent, text, first=False):
        tk.Label(parent, text=text.upper(), bg=BG, fg=DIM, font=self.theme.font("semibold", 10)).pack(
            anchor="w", pady=(0 if first else self.theme.px(20), self.theme.px(8)))

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        th = self.theme
        px = th.px
        w = self.settings_win = tk.Toplevel(self.root)
        w.title("Settings")
        w.configure(bg=BG)
        w.resizable(True, True)
        w.minsize(px(420), px(360))
        w.transient(self.root)
        r = self._window_rect()
        if r:                                    # over the overlay, so on the same screen (and scaling)
            w.geometry(f"+{r[0] + 40}+{r[1] + 40}")
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        # Scrollable body: tall Settings used to clip Tracking / Gain notices on small screens.
        holder = tk.Frame(w, bg=BG)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, borderwidth=0)
        bar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _sync_scroll(_=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win_id, width=max(canvas.winfo_width(), body.winfo_reqwidth()))

        body.bind("<Configure>", _sync_scroll)
        canvas.bind("<Configure>", _sync_scroll)

        def _wheel(e):
            if w.winfo_exists():
                canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind_all("<MouseWheel>", _wheel)
        w.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>") if e.widget is w else None)
        body.pack_propagate(True)
        pad = tk.Frame(body, bg=BG)
        pad.pack(fill="both", expand=True, padx=px(26), pady=px(22))
        body = pad
        text = dict(bg=BG, fg=FG, font=th.font("semibold", 12))
        dim = dict(bg=BG, fg=DIM, font=th.font("regular", 11))
        wrap = px(380)
        w.after(50, lambda: w.geometry(f"{max(w.winfo_reqwidth(), px(460))}x{min(w.winfo_reqheight() + 20, px(720))}"))

        self._section(body, "Tracking", first=True)
        gap = tk.StringVar(value=str(self.cfg["gap_seconds"]))
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        tk.Label(line, text="Pause after no EXP for", **text).pack(side="left")
        gap_step = tk.Frame(line, bg=BG)
        gap_step.pack(side="right")

        def gap_by(by):
            try:
                now_ = int(gap.get())
            except ValueError:
                now_ = self.cfg["gap_seconds"]
            gap.set(str(min(300, max(5, now_ + by))))
        Button(gap_step, th, text="−", kind="secondary", width=38, height=34, radius=10, size=15,
               command=lambda: gap_by(-5), bg=BG).pack(side="left")
        tk.Label(gap_step, textvariable=gap, width=4, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
        Button(gap_step, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
               command=lambda: gap_by(5), bg=BG).pack(side="left")
        tk.Label(gap_step, text="s", bg=BG, fg=DIM, font=th.font("regular", 11)).pack(side="left", padx=(px(6), 0))
        tk.Label(body, text="Pausing also restarts the 5-minute log timer.", **dim).pack(anchor="w", pady=(px(5), 0))

        meso_line = tk.Frame(body, bg=BG)
        meso_line.pack(fill="x", pady=(px(14), 0))
        tk.Label(meso_line, text="Mesos / hr from", **text).pack(side="left")
        meso_box = tk.Frame(meso_line, bg=BG)
        meso_box.pack(side="right")
        meso_btns = {}

        def pick_meso_mode(mode):
            self.cfg["meso_mode"] = mode
            self.reader.meso_mode = mode
            if mode == "notices":
                self._refresh_notice_prices()
            for k, b in meso_btns.items():
                b.set(kind="primary" if k == mode else "secondary")
            save_config(self.cfg)
        for k in MESO_MODES:
            meso_btns[k] = Button(meso_box, th, text=MESO_MODE_LABELS[k], width=110, height=34, radius=10, size=12,
                                  bg=BG, kind="primary" if k == self.cfg.get("meso_mode", "notices") else "secondary",
                                  command=lambda k=k: pick_meso_mode(k))
            meso_btns[k].pack(side="left", padx=(px(6), 0))
        tk.Label(body, text="Inventory = mesos box. Gain notices = floating right-side pickups "
                            "(mesos + Etc gain + Potion gain in Details). Keep Battle Messages on screen.",
                 wraplength=wrap, justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        self._section(body, "Class")
        job_line = tk.Frame(body, bg=BG)
        job_line.pack(fill="x")
        tk.Label(job_line, text="Your class", **text).pack(side="left")
        job_var = tk.StringVar(value=self.cfg.get("job_override") or self.job or "")
        job_entry = tk.Entry(job_line, textvariable=job_var, width=18, bg="#1a1d26", fg=FG,
                             insertbackground=FG, relief="flat", font=th.font("semibold", 12))
        job_entry.pack(side="right", ipady=px(6), ipadx=px(8))
        read_job = self.job or "not read yet"
        tk.Label(body, text=f"Read from the status bar (left of HP): {read_job}. "
                            "Type Assassin, Bandit, … here if it keeps missing — used for the log and Details.",
                 wraplength=wrap, justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        self._section(body, "Window")
        transparency = tk.IntVar(value=int(self.cfg["transparency"]))
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        tk.Label(line, text="Transparency", **text).pack(side="left")
        percent = tk.Label(line, text=f"{transparency.get()}%", **dim)
        percent.pack(side="right")
        transparency.trace_add("write", lambda *_: percent.config(text=f"{int(float(transparency.get()))}%"))
        Slider(body, th, transparency, 0, MAX_TRANSPARENCY, 330, BG).pack(anchor="w", pady=(px(6), px(12)))
        size_line = tk.Frame(body, bg=BG)
        size_line.pack(fill="x")
        tk.Label(size_line, text="Size", **text).pack(side="left")
        # \u2212 100% +: steps through UI_SCALES only, so it stops at the smallest and biggest sizes.
        size = tk.StringVar(value=f"{self.cfg['ui_scale']}%")
        stepper = tk.Frame(size_line, bg=BG)
        stepper.pack(side="right")

        def nudge(by):
            i = UI_SCALES.index(int(size.get().rstrip("%"))) + by
            if 0 <= i < len(UI_SCALES):
                size.set(f"{UI_SCALES[i]}%")
            smaller.set(enabled=i > 0)
            bigger.set(enabled=i < len(UI_SCALES) - 1)
        smaller = Button(stepper, th, text="\u2212", kind="secondary", width=38, height=34, radius=10, size=15,
                         command=lambda: nudge(-1), bg=BG)
        smaller.pack(side="left")
        tk.Label(stepper, textvariable=size, width=5, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
        bigger = Button(stepper, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
                        command=lambda: nudge(1), bg=BG)
        bigger.pack(side="left")
        nudge(0)
        shape_line = tk.Frame(body, bg=BG)
        shape_line.pack(fill="x", pady=(px(14), 0))
        tk.Label(shape_line, text="Compact shape", **text).pack(side="left")
        shape_box = tk.Frame(shape_line, bg=BG)
        shape_box.pack(side="right")
        shape_btns = {}

        def pick_shape(shape):
            self.set_shape(shape)
            for k, b in shape_btns.items():
                b.set(kind="primary" if k == shape else "secondary")
        for k in SHAPES:
            shape_btns[k] = Button(shape_box, th, text=SHAPE_LABELS[k], width=64, height=34, radius=10, size=12, bg=BG,
                                   kind="primary" if k == self.cfg["compact_shape"] else "secondary",
                                   command=lambda k=k: pick_shape(k))
            shape_btns[k].pack(side="left", padx=(px(6), 0))
        topmost = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        self._switch(body, "Always on top of other windows", topmost).pack(anchor="w", pady=(px(14), 0))
        read_status = tk.BooleanVar(value=bool(self.cfg.get("read_status", True)))
        self._switch(body, "Show reading status (EXP, Mesos, Map, Potions)", read_status).pack(
            anchor="w", pady=(px(10), 0))
        tk.Label(body, text="A row of dots under the big number: green means it can read that part, "
                            "yellow means it's holding the last look, red means it can't.",
                 wraplength=wrap, justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        self._section(body, "Potions")
        potions_on = tk.BooleanVar(value=bool(self.cfg["potions_on"]))
        self._switch(body, "Count potions used and what they cost", potions_on).pack(anchor="w")
        price_line = tk.Frame(body, bg=BG)
        price_line.pack(fill="x", pady=(px(8), 0))
        tk.Label(price_line, text="Your own price for each one", **text).pack(side="left")
        Button(price_line, th, text="Prices", kind="secondary", width=80, height=34, radius=10, size=12,
               command=self.open_potion_prices, bg=BG).pack(side="right")
        self._potions_on = potions_on

        self._section(body, "EXP bar without a number")
        level_line = tk.Frame(body, bg=BG)
        level_line.pack(fill="x")
        tk.Label(level_line, text="Type your level if it can't be read", **text).pack(side="left")
        self.level_btn = Button(level_line, th, text="Level fallback", kind="secondary", width=124, height=34, radius=10,
                                size=12, command=lambda: self.type_level(w), bg=BG)
        self.level_btn.pack(side="right")
        self.level_btn.set(enabled=self._can_type_level())
        tk.Label(body, text="Past a certain level the game shows only a percentage. The overlay reads your level "
                            "from the status bar and estimates EXP from it. Typing it in is only a last resort.",
                 wraplength=wrap, justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        self._section(body, "Updates and sharing")
        updates = tk.BooleanVar(value=bool(self.cfg["check_updates"]))
        self._switch(body, "Check for updates when it starts", updates).pack(anchor="w")
        sharing = tk.BooleanVar(value=bool(self.cfg.get("share_runs")))
        self._switch(body, f"Share my {RUN_SECONDS // 60}-minute runs, anonymously", sharing).pack(anchor="w", pady=(px(6), 0))
        tk.Label(body, text="They feed the best-maps site. Your notes and log never leave this PC.", wraplength=wrap,
                 justify="left", **dim).pack(anchor="w", pady=(px(4), 0))
        health_on = self._health_on = tk.BooleanVar(value=self.cfg.get("share_health") is True)
        self._switch(body, "Send daily health reports", health_on).pack(anchor="w", pady=(px(6), 0))
        tk.Label(body, text="A short anonymous summary of how well the overlay reads your game, so problems get "
                            "fixed. Numbers only: no names, chat, maps or pictures.", wraplength=wrap,
                 justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        def apply(*_):
            try:
                seconds = int(gap.get())
            except ValueError:
                seconds = 0
            if 1 <= seconds <= 3600:
                self.cfg["gap_seconds"] = self.autolog.gap = self.session.gap = seconds
            self.cfg["transparency"] = min(MAX_TRANSPARENCY, max(0, int(float(transparency.get()))))
            self.cfg["topmost"] = bool(topmost.get())
            self.cfg["check_updates"] = bool(updates.get())
            self.cfg["potions_on"] = bool(potions_on.get())
            self.cfg["read_status"] = bool(read_status.get())
            typed = job_var.get().strip()
            if typed:
                # Snap to a known job name when close enough (OCR typos in the box).
                score, best = max((SequenceMatcher(None, typed.lower(), j.lower()).ratio(), j) for j in JOBS)
                self.cfg["job_override"] = best if score >= 0.72 else typed[:40]
                if score >= 0.72 and job_var.get() != best:
                    job_var.set(best)
            else:
                self.cfg["job_override"] = ""
            if bool(sharing.get()) != bool(self.cfg.get("share_runs")):
                self.cfg["share_runs"] = bool(sharing.get())
                self.share_runs()
            if bool(health_on.get()) != (self.cfg.get("share_health") is True):
                self.cfg["share_health"] = bool(health_on.get())
            self._apply_window_settings()
            w.attributes("-topmost", self.cfg["topmost"])
            scale = int(size.get().rstrip("%"))
            if scale in UI_SCALES and scale != self.cfg["ui_scale"]:
                self.cfg["ui_scale"] = scale
                self._apply_ui_scale()
                self.root.after(60, self._reopen_settings)        # (drawn again at the new size)
            save_config(self.cfg)

        for var in (gap, transparency, topmost, updates, sharing, health_on, size, potions_on, read_status, job_var):
            var.trace_add("write", apply)
        self._hotkey_settings(body)
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(px(22), px(4)))
        Button(buttons, th, text="Close", kind="primary", width=96, height=38, radius=11, size=12,
               command=w.destroy, bg=BG).pack(side="right")
        Button(buttons, th, text="Data folder", kind="secondary", width=104, height=38, radius=11, size=12,
               command=self.open_data_folder, bg=BG).pack(side="left")
        Button(buttons, th, text="Check for updates", kind="secondary", width=146, height=38, radius=11, size=12,
               command=lambda: self.check_updates(manual=True), bg=BG).pack(side="left", padx=(px(8), 0))
        Button(buttons, th, text="Report a bug", kind="secondary", width=116, height=38, radius=11, size=12,
               command=lambda: self.report_bug(w), bg=BG).pack(side="left", padx=(px(8), 0))
        more = tk.Frame(body, bg=BG)
        more.pack(fill="x", pady=(px(8), 0))
        Button(more, th, text="What's new", kind="secondary", width=116, height=38, radius=11, size=12,
               command=self.show_whats_new, bg=BG).pack(side="left")
        tk.Label(body, text=f"Your log, notes and settings are saved in {DATA_DIR}", wraplength=wrap, justify="left",
                 bg=BG, fg=FAINT, font=th.font("regular", 10)).pack(anchor="w", pady=(px(8), 0))
        tk.Label(body, text=f"{WINDOW_TITLE} v{VERSION}. This is a beta: it's still being improved.", bg=BG, fg=FAINT,
                 font=th.font("regular", 10)).pack(anchor="w", pady=(px(2), 0))

    def open_potion_prices(self):
        """One price per item the bar can show, over the shipped one. Clear a box for the default."""
        if self.prices_win and self.prices_win.winfo_exists():
            self.prices_win.lift()
            return
        table = self._potion_table()
        if not table:
            return
        th = self.theme
        px = th.px
        w = self.prices_win = tk.Toplevel(self.root)
        w.title("Potion prices")
        w.configure(bg=BG)
        w.transient(self.settings_win if self.settings_win and self.settings_win.winfo_exists() else self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        tk.Label(w, text="Mesos each. Leave a box empty to use the shown price; 0 means free.", bg=BG, fg=DIM,
                 font=th.font("regular", 11)).pack(anchor="w", padx=px(20), pady=(px(16), px(8)))
        holder = tk.Frame(w, bg=BG)
        holder.pack(fill="both", expand=True, padx=px(20))
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, width=px(380), height=px(420))
        bar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        rows = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        w.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>") if e.widget is w else None)
        self._price_vars = {}
        for item in table.prices.values():
            if item.get("kind") != "consumable":
                continue
            name = item["name"]
            row = tk.Frame(rows, bg=BG)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=name, bg=BG, fg=FG, font=th.font("regular", 11), anchor="w", width=24).pack(side="left")
            default = "no NPC" if item.get("unpriced") else f"{item['price']:,}"
            own = self.cfg["potion_prices"].get(name)
            var = tk.StringVar(value="" if own is None else str(own))
            tk.Entry(row, textvariable=var, width=8, justify="right", relief="flat", bg=INK, fg=FG,
                     insertbackground=FG, font=th.font("regular", 11)).pack(side="right")
            tk.Label(row, text=default, bg=BG, fg=DIM, font=th.font("regular", 11), width=8, anchor="e").pack(side="right")
            var.trace_add("write", lambda *_, n=name, v=var: self._set_price(n, v.get()))
            self._price_vars[name] = var
        Button(w, th, text="Close", kind="primary", width=96, height=38, radius=11, size=12,
               command=w.destroy, bg=BG).pack(side="right", padx=px(20), pady=px(14))

    def _set_price(self, name, text):
        """A price typed in the prices window: whole mesos, or empty for the default."""
        text = text.replace(",", "").strip()
        if text.isascii() and text.isdigit() and len(text) <= 10:
            self.cfg["potion_prices"][name] = int(text)
        elif not text:
            self.cfg["potion_prices"].pop(name, None)
        else:
            return                                          # not a number (yet): leave what's saved
        save_config(self.cfg)
        self._refresh_notice_prices()

    def _reopen_settings(self):
        """After a size change: Settings again, in the same place, drawn for the new size."""
        old = self.settings_win
        if old and old.winfo_exists():
            where = f"+{old.winfo_x()}+{old.winfo_y()}"
            old.destroy()
            self.open_settings()
            self.settings_win.geometry(where)

    def _hotkey_settings(self, body):
        """Settings' Hotkeys section: on or off, and each one changeable (click it, then press
        the keys; Esc keeps it as it was, Backspace turns it off)."""
        th, w = self.theme, body.winfo_toplevel()
        px = th.px
        self._section(body, "Hotkeys")
        box = tk.Frame(body, bg=BG)
        box.pack(anchor="w", fill="x")
        if not self.hotkeys.supported:
            tk.Label(box, text=f"Hotkeys aren't available on {PLATFORM.name} yet: use the buttons.",
                     bg=BG, fg=DIM, font=th.font("regular", 11)).pack(anchor="w")
            return
        on = tk.BooleanVar(value=self.cfg["hotkeys_on"])
        self._switch(box, "Hotkeys, while MapleStory or the overlay is in front", on).pack(anchor="w", pady=(0, px(8)))
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w", padx=(px(2), 0))
        buttons, notes, choosing = {}, {}, {"action": None}
        idle_hint = "Click one, then press the keys you want. Backspace turns it off."
        hint = tk.StringVar(value=idle_hint)

        def show():
            for action, b in buttons.items():
                key = self.cfg["hotkeys"][action]
                if action == choosing["action"]:
                    b.set(text="Press keys...", kind="primary")
                else:
                    b.set(text=key or "Off", kind="secondary" if key else "ghost")
                notes[action].config(text=self.hotkeys.problems.get(action, ""))

        def stop():
            """Nobody is waiting for keys: hotkeys work again, every button shows its key."""
            choosing["action"] = None
            hint.set(idle_hint)
            self.hotkeys.pause(False)
            show()

        def watch():
            """Clicking away from Settings (into the game, say) ends the wait, so the hotkeys never stay off."""
            if choosing["action"] and w.winfo_exists():
                if w.focus_displayof() is None:
                    stop()
                else:
                    w.after(400, watch)

        def choose(action):
            if choosing["action"] == action:         # clicking the waiting one again cancels it
                stop()
                return
            first = choosing["action"] is None
            choosing["action"] = action              # (one at a time: another that was waiting goes back to its key)
            self.hotkeys.pause(True)                 # so the keys reach this window
            hint.set(f"Press the keys for {HOTKEY_ACTIONS[action]}. Click it again or press Esc to cancel.")
            show()
            w.focus_force()
            if first:
                w.after(400, watch)

        def done(action, key):
            choosing["action"] = None
            hint.set(idle_hint)
            if key:
                for other, k in self.cfg["hotkeys"].items():
                    if k == key and other != action:
                        self.cfg["hotkeys"][other] = ""      # one key, one job
            self.cfg["hotkeys"][action] = key
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
            self.hotkeys.pause(False)
            show()

        def pressed(e):
            action = choosing["action"]
            if not action or e.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                                          "Win_L", "Win_R", "Caps_Lock", "Num_Lock"):
                return None
            if e.keysym == "Escape":
                done(action, self.cfg["hotkeys"][action])
            elif e.keysym in ("BackSpace", "Delete"):
                done(action, "")
            else:
                key = hotkey_text(bool(e.state & 0x4), bool(e.state & 0x20000), bool(e.state & 0x1), e.keycode)
                if not key:
                    hint.set("Use Ctrl, Alt or Shift with a number or letter, or an F key.")
                    return "break"
                done(action, key)
            return "break"

        for i, (action, name) in enumerate(HOTKEY_ACTIONS.items()):
            tk.Label(grid, text=name, bg=BG, fg=FG, font=th.font("regular", 12), width=14, anchor="w").grid(
                row=i, column=0, sticky="w", pady=px(3))
            buttons[action] = Button(grid, th, text="", kind="secondary", width=124, height=34, radius=10, size=12,
                                     command=lambda a=action: choose(a), bg=BG)
            buttons[action].grid(row=i, column=1, sticky="w", pady=px(3))
            notes[action] = tk.Label(grid, bg=BG, fg="#ff8a7a", font=th.font("regular", 10))
            notes[action].grid(row=i, column=2, sticky="w", padx=(px(8), 0))
        tk.Label(box, textvariable=hint, bg=BG, fg=FAINT, font=th.font("regular", 10)).pack(anchor="w", pady=(px(6), 0))
        w.bind("<KeyPress>", pressed)
        w.bind("<Destroy>", lambda e: e.widget is w and self.hotkeys.pause(False))
        self._hotkey_ui = {"buttons": buttons, "choose": choose, "choosing": choosing, "pressed": pressed, "on": on}   # (tests)

        def switch(*_):
            if choosing["action"]:
                stop()
            self.cfg["hotkeys_on"] = bool(on.get())
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        on.trace_add("write", switch)
        show()

    def open_data_folder(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            PLATFORM.open_folder(DATA_DIR)
        except Exception as e:
            self._oops("opening the data folder", e)

    def _restore_position(self):
        pos = self.cfg.get("pos")
        if not pos:
            return
        with mss.mss() as sct:
            screens = sct.monitors[1:]
        # Only if its top-left is still on a screen (not one that was unplugged, nor the
        # gap between two screens of different sizes).
        if any(v["left"] <= pos[0] < v["left"] + v["width"] - 50
               and v["top"] <= pos[1] < v["top"] + v["height"] - 50 for v in screens):
            self.root.geometry(f"+{pos[0]}+{pos[1]}")

    def _window_rect(self):
        """This app's window on screen (physical pixels), or None."""
        try:
            return PLATFORM.own_window_rect(self.root)
        except Exception:
            return None

    # ---- background capture loop
    def _worker(self):
        while not self.stop_evt.is_set():
            try:
                with mss.mss() as sct:
                    capture = ScreenCapture(sct)
                    while not self.stop_evt.is_set():
                        t0 = time.monotonic()
                        try:
                            reading = self.reader.read(capture)
                            self.q.put(("reading", reading._replace(ms=(time.monotonic() - t0) * 1000)))
                            self.health.finished(t0)
                        except Exception as e:
                            log_error("reading the screen", e)
                            self.health.failed(e)
                            self.q.put(("error", f"{type(e).__name__}: {e}"))
                        self.stop_evt.wait(max(0.1, POLL_SECONDS - (time.monotonic() - t0)))
            except Exception as e:                  # (the screen couldn't be opened, or something outside a look broke:
                log_error("starting to read the screen", e)         # never the end of the reading, try again)
                self.health.failed(e)
                self.q.put(("error", f"{type(e).__name__}: {e}"))
                self.stop_evt.wait(WORKER_RETRY_SECONDS)
        self.health.alive = False

    # ---- sharing runs
    def open_best_maps(self):
        """The best-maps site, already showing the maps for this level, party size and class."""
        ask = {k: v for k, v in (("level", self.level), ("party", self.party), ("job", self._class_name())) if v}
        webbrowser.open(STATS_SITE + ("/?" + urllib.parse.urlencode(ask) if ask else ""))

    def _first_run(self):
        """Once per install or update: a new install picks its compact shape, an update shows what's new. The
        sharing question comes after."""
        seen = self.cfg.get("seen_version", "")
        newer = not seen or version_key(VERSION) > version_key(seen)
        if newer:
            self.cfg["seen_version"] = VERSION                 # (first, so a crash in a window can't repeat it)
            save_config(self.cfg)
        if self._fresh and newer:
            self._shape_picker(self._ask_to_share)
        elif newer and WHATS_NEW.get(VERSION):
            self._whats_new(WHATS_NEW[VERSION], self._ask_to_share)
        else:
            self._ask_to_share()

    def _small_window(self, title, then):
        """A window for the one-time messages: like the other dialogs, and `then` runs when it closes."""
        th = self.theme
        w = tk.Toplevel(self.root)
        w.title(title)
        w.configure(bg=BG, padx=th.px(26), pady=th.px(22))
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        w.bind("<Destroy>", lambda e: then and e.widget is w and self.root.after(100, then))
        return w

    def _shape_photo(self, shape, width):
        """The picture of a compact shape in the picker (assets/shape_<name>.png), `width` logical pixels wide."""
        try:
            img = Image.open(ASSETS / f"shape_{shape}.png").convert("RGBA")
            w = self.theme.px(width)
            return ImageTk.PhotoImage(img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS), master=self.root)
        except Exception:
            return None

    def _shape_picker(self, then):
        """A new install chooses how compact mode looks. Closing the window keeps Square."""
        th = self.theme
        w = self._small_window("Choose a compact look", then)
        tk.Label(w, text="Pick a compact look", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text="Compact mode is the small version of the overlay (the compact icon, or Shift+4). "
                         "Choose how it looks. You can change it any time in Settings.", bg=BG, fg=DIM,
                 font=th.font("regular", 12), wraplength=th.px(500), justify="left").pack(anchor="w", pady=(2, th.px(14)))
        widths = {"square": 150, "wide": 250, "bar": 300}

        def choose(shape):
            self.set_shape(shape)
            w.destroy()
        for shape in SHAPES:
            card = tk.Frame(w, bg=LIST_BG, highlightthickness=1, highlightbackground=LINE)
            card.pack(fill="x", pady=(0, th.px(10)))
            photo = self._shape_photo(shape, widths[shape])
            if photo:
                pic = tk.Label(card, image=photo, bg=LIST_BG, cursor="hand2")
                pic.image = photo
                pic.pack(side="left", padx=th.px(12), pady=th.px(12))
                pic.bind("<Button-1>", lambda e, s=shape: choose(s))
            side = tk.Frame(card, bg=LIST_BG)
            side.pack(side="left", fill="both", expand=True, padx=(0, th.px(12)), pady=th.px(12))
            tk.Label(side, text=SHAPE_LABELS[shape], bg=LIST_BG, fg=FG, font=th.font("bold", 14)).pack(anchor="w")
            tk.Label(side, text=SHAPE_BLURBS[shape], bg=LIST_BG, fg=DIM, font=th.font("regular", 11),
                     wraplength=th.px(190), justify="left").pack(anchor="w", pady=(2, th.px(8)))
            Button(side, th, text=f"Use {SHAPE_LABELS[shape]}", kind="primary" if shape == "square" else "secondary",
                   width=120, height=34, radius=10, size=12, command=lambda s=shape: choose(s), bg=LIST_BG).pack(anchor="w")
        w.update_idletasks()

    def show_whats_new(self):
        """Settings > What's new: this version's notes again (they are otherwise shown once, after an update)."""
        self._whats_new(WHATS_NEW.get(VERSION) or ("Nothing new to show for this version.",), None)

    def _note_photo(self, name, width):
        """A picture from assets/ for the What's new window, `width` logical pixels wide (None if it can't be read)."""
        try:
            img = Image.open(ASSETS / name).convert("RGBA")
            w = self.theme.px(width)
            return ImageTk.PhotoImage(img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS),
                                      master=self.root)
        except Exception:
            return None

    def _whats_new(self, notes, then):
        """After an update: a few plain lines on what changed, in one or more pages (see WHATS_NEW).
        Shown once per version."""
        th = self.theme
        pages = notes if notes and not isinstance(notes[0], str) else ((None, None, tuple(notes)),)
        w = self._small_window("What's new", then)
        body = tk.Frame(w, bg=BG)
        body.pack(anchor="w")
        w._pictures = []                                  # (kept, or tk drops them)

        def show(n, size=0):
            """Page n, its pictures at the first width in PICTURE_WIDTHS (then smaller, and finally none)
            if the page would be taller than the screen."""
            for child in body.winfo_children():
                child.destroy()
            w._pictures.clear()
            pic_width = PICTURE_WIDTHS[size]
            title, sub, items = pages[n]
            tk.Label(body, text=title or f"What's new in v{VERSION.split('-')[0]}", bg=BG, fg=ACCENT,
                     font=th.font("extra", 20)).pack(anchor="w")
            tk.Label(body, text=sub or "Thanks for updating. Your log, notes and settings are kept.", bg=BG, fg=DIM,
                     font=th.font("regular", 12)).pack(anchor="w", pady=(2, th.px(12)))
            for item in items:
                kind, text = ("dot", item) if isinstance(item, str) else item[:2]
                if kind == "head":
                    tk.Label(body, text=text, bg=BG, fg=FG, font=th.font("bold", 14)).pack(anchor="w", pady=(th.px(4), th.px(4)))
                elif kind == "pic":
                    photo = self._note_photo(text, pic_width) if pic_width else None
                    if photo:
                        w._pictures.append(photo)
                        tk.Label(body, image=photo, bg=BG).pack(anchor="w", pady=(0, th.px(8)))
                else:
                    row = tk.Frame(body, bg=BG)
                    row.pack(fill="x", pady=(0, th.px(6)))
                    tk.Label(row, text="\u2022", bg=BG, fg=ACCENT, font=th.font("bold", 12)).pack(side="left", anchor="n")
                    tk.Label(row, text=text, bg=BG, fg=FG, font=th.font("regular", 12), wraplength=th.px(430),
                             justify="left").pack(side="left", padx=(th.px(8), 0))
            last = n == len(pages) - 1
            Button(body, th, text="Got it" if last else "Next", kind="primary", width=110, height=40, radius=12, size=13,
                   command=w.destroy if last else lambda: show(n + 1), bg=BG).pack(anchor="e", pady=(th.px(10), 0))
            w.update_idletasks()
            if w.winfo_reqheight() > w.winfo_screenheight() - th.px(80) and size < len(PICTURE_WIDTHS) - 1:
                show(n, size + 1)
        show(0)

    def _ask_to_share(self):
        if "share_runs" not in self.cfg:
            self._ask_run_sharing()
        self._ask_health()

    def _ask_health(self):
        """Once: may the overlay send a short anonymous summary of how well it reads the game each day?"""
        if "share_health" in self.cfg:
            return
        yes = messagebox.askyesno(
            "Help make the overlay more reliable?",
            "Once a day, send a short anonymous summary of how well the overlay is reading your game "
            "(for example how often it found the EXP bar, your game's window size and the overlay's "
            "version), so problems can be fixed even when nobody reports them.\n\n"
            "Never your character's name, chat, maps, pictures or your log. You can change this any "
            "time in Settings.", parent=self.root)
        self.cfg["share_health"] = bool(yes)
        save_config(self.cfg)

    def _ask_run_sharing(self):
        yes = messagebox.askyesno(
            "Help other players pick maps?",
            "Share your farming, so everyone can see which maps give the most EXP for their "
            "class and level?\n\n"
            f"Every {RUN_SECONDS // 60} minutes of non-stop farming is shared: the map, your level, "
            "class and party size, and the EXP and mesos you got. Never your character's name.\n\n"
            f"They're combined with other players' on {STATS_SITE.split('//')[1]}. You can change "
            "this any time in Settings.", parent=self.root)
        self.cfg["share_runs"] = bool(yes)
        save_config(self.cfg)
        self.share_runs()

    def share_runs(self):
        """Sends finished runs to the stats site in the background (if agreed to)."""
        self._next_share = time.monotonic() + 1800
        if not self.cfg.get("share_runs") or self._sharing:
            return
        runs = self.autolog.unshared_runs()
        if not runs:
            return
        self._sharing, install = True, self.autolog.install

        def work():
            try:
                self.q.put(("shared", post_runs(install, runs)))
            except Exception:
                self.q.put(("shared", None))           # offline or the site is down: later
        threading.Thread(target=work, daemon=True).start()

    # ---- health reports (healthlog.py): numbers only, once a day, only if the player said yes
    def _count_health(self, r, now):
        """Counts one look in today's health record. Runs on the main thread, once per Reading, so the
        categories always add up to the number of looks."""
        try:
            last, self._health_at = self._health_at, now
            rect, gh = self.reader.last_rect, self.reader.gh
            game = None
            if rect and gh:
                game = (rect[2], rect[3], any(rect[2] == w and rect[3] == h for w, h, _ in self._health_monitors), gh)
            self.healthlog.look(healthlog.kind_of(r), r.percent_only, r.tries, r.ms,
                                (now - last) if last is not None and self.tracker.running else 0.0, game, r.shadow)
        except Exception as e:                      # (counting must never keep a reading from being handled)
            log_error("counting a look for the health report", e)

    def _health_snapshot(self):
        """What the setup is like today (recorded while the game is there, not when the report is sent)."""
        screens = []
        try:
            with mss.mss() as sct:
                for m in sct.monitors[1:5]:
                    dpi = PLATFORM.screen_dpi(m["left"] + m["width"] // 2, m["top"] + m["height"] // 2)
                    scale = round(dpi / 96 * 100) if dpi else None
                    screens.append((m["width"], m["height"], scale if scale and 50 <= scale <= 500 else None))
        except Exception as e:
            log_error("listing the screens for the health report", e)
        self._health_monitors = screens
        capture = self.reader.capture.name
        self.healthlog.snapshot(VERSION, PLATFORM.version(), screens,
                                "region" if self.reader.mode == "region" else capture if capture == "window" else "screen",
                                tesseract_state)

    def _health_tick(self):
        """Called a few times a second from the refresh: keeps the record up to date and saved, and sends a
        finished day when it is time (at most one a hour, even across restarts)."""
        now = time.monotonic()
        if now >= self._health_snap_at:
            self._health_snap_at = now + healthlog.SAVE_EVERY
            self._health_snapshot()
        try:
            seen = dict(self.reader.error_types)
        except RuntimeError:                            # (the reading thread changed it just then: next time)
            seen = self._health_errors
        self.healthlog.errors({k: n - self._health_errors.get(k, 0) for k, n in seen.items()})
        self._health_errors = seen
        self.healthlog.save(force=False)
        if self.cfg.get("share_health") is not True or self._health_sending or sharing_blocked():
            return
        if healthlog.ensure_id(self.cfg):
            save_config(self.cfg)
        due = self.healthlog.due(self.cfg["health_id"])
        if due is None:
            return
        day, body = due
        self._health_sending = True
        self.healthlog.attempting()

        def work():
            try:
                self.q.put(("health_sent", (day,) + post_health(body)))
            except Exception:
                self.q.put(("health_sent", (day, "later", "")))
        threading.Thread(target=work, daemon=True).start()

    def _health_done(self, day, outcome, detail):
        self._health_sending = False
        if outcome == "bad":
            log_error(f"the site refused the health report for {day}", ValueError(detail or "unknown field"))
        elif outcome == "big":
            log_error(f"the site refused the health report for {day}", ValueError("too large"))
        self.healthlog.finish(day, outcome)

    # ---- updates
    def check_updates(self, manual=False):
        """Asks GitHub for a newer version in the background; the answer comes via _pump."""
        def work():
            try:
                self.q.put(("update", (check_for_update(), manual, None)))
            except Exception as e:
                self.q.put(("update", (None, manual, e)))
        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, found, manual, error):
        parent = self.settings_win if manual and self.settings_win and self.settings_win.winfo_exists() \
            else self.root
        if error is not None:
            if manual:
                messagebox.showinfo("Updates", "Couldn't check for updates. Are you connected to the "
                                    "internet?", parent=parent)
            return                                      # at start: quietly try again next time
        if not found:
            if manual:
                messagebox.showinfo("Updates", f"You have the latest version (v{VERSION}).", parent=parent)
            return
        version, notes = found
        if not manual and self.cfg.get("skip_version") == version:
            return
        w = tk.Toplevel(self.root)
        w.title("Update available")
        w.configure(bg=BG, padx=self.theme.px(26), pady=self.theme.px(22))
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        th = self.theme
        tk.Label(w, text=f"v{version} is out", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text=f"You have v{VERSION}. Your log, notes and settings are kept when you update.",
                 bg=BG, fg=DIM, font=th.font("regular", 12)).pack(anchor="w", pady=(2, th.px(14)))
        tk.Label(w, text="WHAT'S NEW", bg=BG, fg=DIM, font=th.font("semibold", 10)).pack(anchor="w", pady=(0, th.px(6)))
        box = tk.Frame(w, bg=LIST_BG, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="both", expand=True, pady=(0, th.px(16)))
        text = tk.Text(box, width=62, height=12, wrap="word", bg=LIST_BG, fg=FG, relief="flat",
                       font=th.font("regular", 12), padx=th.px(12), pady=th.px(10), highlightthickness=0)
        scroll = ttk.Scrollbar(box, command=text.yview, style="Dark.Vertical.TScrollbar")
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", notes or "(No details were given for this version.)")
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        buttons = tk.Frame(w, bg=BG)
        buttons.pack(fill="x")

        def get_it():
            webbrowser.open(RELEASES_PAGE)                # always your own releases page
            w.destroy()

        def skip():
            self.cfg["skip_version"] = version
            save_config(self.cfg)
            w.destroy()
        Button(buttons, th, text="Get the update", kind="primary", width=140, height=40, radius=12, size=13,
               command=get_it, bg=BG).pack(side="left")
        Button(buttons, th, text="Not now", kind="secondary", width=96, height=40, radius=12, size=12,
               command=w.destroy, bg=BG).pack(side="left", padx=(th.px(8), 0))
        Button(buttons, th, text="Skip this version", kind="ghost", width=140, height=40, radius=12, size=12,
               command=skip, bg=BG).pack(side="right")

    def _oops(self, what, exc):
        """Something unexpected went wrong: note it in errors.log and keep going."""
        log_error(what, exc)
        self.status.set("Something went wrong, but the overlay keeps running. Details are in "
                        "errors.log (Settings > Open data folder).")

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "error":
                    self.healthlog.errors({payload.split(":")[0].strip(): 1})      # (the type's name, never the message)
                    self.recent.add(f"Error reading the screen: {payload}")
                    self.status.set("Something went wrong reading the screen. Trying again... "
                                    "(details are in errors.log: Settings > Open data folder)")
                elif kind == "update":
                    self._show_update(*payload)
                elif kind == "hotkey":
                    self._hotkey(payload)
                elif kind == "shared":
                    self._sharing = False
                    if payload:
                        self.autolog.mark_shared(payload)
                elif kind == "health_sent":
                    self._health_done(*payload)
                elif kind == "report_sent":
                    self._report_done(*payload)
                else:
                    self._handle(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self._oops("handling a reading", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(200, self._pump)

    def _handle(self, r):
        t, now = self.tracker, time.monotonic()
        self._count_health(r, now)
        if r.image is not None:
            self.last_capture = r
        if r.mesos_image is not None:
            self.last_mesos = r
        if r.mesos_at:                                  # remember where the mesos box is (per window size)
            self.cfg["mesos_box"] = list(r.mesos_at)
            self.reader.saved_mesos = tuple(r.mesos_at)
            ww, wh, *box = r.mesos_at
            key = f"{ww}x{wh}"
            spots = {k: v for k, v in (self.cfg.get("mesos_boxes") or {}).items() if k != key}
            spots[key] = list(box)
            self.cfg["mesos_boxes"] = dict(list(spots.items())[-MESOS_BOXES_MAX:])
            self.reader.saved_mesos_boxes = {k: tuple(v) for k, v in self.cfg["mesos_boxes"].items()}
            save_config(self.cfg)
        if r.v2_at:                                     # ...and where reader v2 found the EXP bar
            key, box = r.v2_at
            spots = {k: v for k, v in (self.cfg.get("exp_v2_spots") or {}).items() if k != key}
            spots[key] = list(box)
            self.cfg["exp_v2_spots"] = dict(list(spots.items())[-V2_SPOTS_MAX:])
            self.reader.saved_v2 = dict(self.cfg["exp_v2_spots"])
            save_config(self.cfg)
        if r.bar is not None:
            self.potion_bar = r.bar
            if r.bar:
                self._potions_seen_at = now
            if self.cfg["potions_on"]:
                self.session.look_at_bar(r.bar)
            else:
                self.session.potions.pause()            # (switched on again later: nothing used meanwhile counts)
        notice_mode = self.cfg.get("meso_mode") == "notices"
        # Notices always feed Etc value (and notice-mode mesos). Inventory mesos are separate.
        if r.notices is not None:
            self._refresh_notice_prices()
            self.session.notices.observe_lines(r.notices, now, t.elapsed(), t.running)
        if not notice_mode:
            self.session.meso.set_window(r.window, now)     # mesos moved at these windows aren't earned
            if r.mesos is not None:
                self.session.meso.observe(r.mesos, now, t.elapsed(), t.running)
        if r.confident:
            self._exp_seen_at = now
        if r.party is not None:                         # believe a new party size once seen twice
            seen, n = self._party_seen
            n = n + 1 if r.party == seen else 1
            self._party_seen = (r.party, n)
            if n >= 2:
                self.party = r.party
        if self.reader.job:
            self.job = self.reader.job
        if r.map:
            self.map_read = r.map                       # as read (kept on record if renamed)
            # A slightly misread name of the map you're on still counts as that map.
            self.map_name = self.autolog.canonical(r.map, also=(self.map_name, self.session.map))
            waiting = self.session.waiting
            self.session.map_seen(self.map_name)
            self._after_pause_check(waiting)
        self.in_town = is_town(self.map_name)
        hidden = self._name_hidden()
        if hidden != self.map_hidden:
            self.map_hidden = hidden
            if not hidden:
                self.map_back_at = now          # (says "back" for a few seconds)
        if (self.session.on and self.map_name and self.reader.mode == "window" and tesseract_ok
                and self.reader.map_blind_seconds() > MAP_BLIND_SECONDS):
            self.session.map_blind()
        self.note = r.note
        if r.candidates:
            if self.found_at is None or t.req is not None:
                self.found_at = now
            before = t.gained
            if r.percent_only:
                ok, note = self._observe_percent(r, now)
            else:
                ok, note = self.session.observe(r.candidates, r.confident, now)
                self.level_wait, self.pct_note = False, ""
                self._char_print = self.reader.name_print if self.reader.name_print is not None else self._char_print
            waiting = r.percent_only and self.pct_note.startswith(PCT_WAIT_NOTES)    # (a %-only bar waiting for its level isn't misread)
            self.rejected = 0 if ok or self.level_wait or waiting else self.rejected + 1
            if self.rejected >= 10:                 # keeps not adding up: find the EXP bar again
                self.reader.box, self.rejected = None, 0
            if t.running and t.gained != before and not self.skip_log:
                self.autolog.gain(now, t.gained - before)
            if note == "died":
                self.died_at = now
            if note == "LEVEL UP!":
                self.level_up_at = now
                if self.level:
                    self.level += 1             # until the new level's EXP confirms it
            status = self._status_for(note)
        else:
            status = r.where
        if self.in_town:
            status += "\nIn a town: nothing is logged here."
        if self.map_hidden and self.tracker.running:
            status += (f"\nCan't see the minimap's name. Still tracking; it pauses if this lasts "
                       f"{MAP_BLIND_SECONDS} seconds.")
        elif self.map_back_at and now - self.map_back_at < 6 and self.session.on:
            status += "\nThe minimap's name is back."
        if self.note:
            status += f"\n{self.note}"
        if r.percent_only and self.name_note:
            status += f"\n{self.name_note}"
        if self.autolog.settle(now, self.tracker.running, **self.meso.for_log()):
            self.share_runs()                          # a finished run got its mesos (or went without)
        stale = self.meso.stale_note(now, self.tracker.running)       # (always asked, it keeps its own time)
        inv_alert = None if notice_mode else self.autolog.alert(now, self.tracker.running)
        left_out = (inv_alert or self.meso.ignored_note(now) or stale
                    or self._net(self.tracker.elapsed())[1])
        if left_out:
            status += f"\n{left_out}"
        # Holding a mesos count while the inventory stays closed: say so once it's been a while.
        if (not notice_mode and self.session.on and self.meso.value is not None
                and self.meso.seen_at is not None
                and now - self.meso.seen_at >= MESO_STALE_SECONDS and not self.reader.mesos_covered
                and r.mesos is None):
            status += "\nOpen the inventory to update mesos (holding the last look)."
        self.status.set(status)
        self.recent.add(status)
        self.level = level_for_req(t.req) or self.level
        self._log_tick(now)

    def _observe_percent(self, r, now):
        """A bar with only a %: the level comes from the badge (or the level known before, or typed in)."""
        level, source = r.level or (None, None)
        if self._check_character():
            self.level_wait = True
            return False, "another character"           # (this reading's level may be the other one's)
        stale = self._stale_level
        if stale and level is not None and level == stale[0] and time.monotonic() - stale[1] < STALE_LEVEL_SECONDS:
            level, source = None, None                  # (read before the switch was seen: the old character's)
        if level is None and self.tracker.req is not None and self.tracker.level_source is None and not self._same_char:
            self.tracker.forget_level()                 # a level carried over from the number, but the character can't be told
        had_level = self.tracker.req is not None
        ok, note = self.session.observe_percent(r.candidates[0][1], level, source, now)
        if had_level and self.tracker.req is None:
            self.reader.forget_level()                  # (the tracker gave its level up: the badge's old one must not bring it back)
        self.level_wait = note == "waiting to read your level"
        self.pct_note = note
        if note.startswith(("percentage wrapped", "level up?")):
            self.reader.recheck_level()                 # it may have gone up: look at the badge now
        return ok, note

    def _check_character(self):
        """Another character (its name row looks different) has its own level: start over for it. Only a
        print that differs on two looks in a row counts. A name row partly covered is judged by what shows:
        the parts that show matching = the same character (kept, but said), not matching = another one.
        Nothing showing = can't tell: it says so. Returns True when it started over."""
        cur = self.reader.name_print
        if cur is None:
            self._same_char = False
            self.name_note = "The name under your character is covered, so it can't tell if it's the same character. Move what covers it."
            return False
        if cur is self._char_seen:
            return False
        self._char_seen = cur
        self.name_note = ""
        match = "same" if self._char_print is None else level_ocr.name_match(self._char_print, cur)
        if match == "hidden":                        # (so little shows it can't tell: like a covered name, never a reset)
            self._same_char = False
            self.name_note = "Most of the name under your character is covered, so it can't tell if it's the same character. Move what covers it."
            return False
        if match in ("same", "alike"):
            self._same_char = self._char_print is not None
            self._char_print, self._char_diff = (cur if self._char_print is None else self._char_print), 0
            if match == "alike":
                self.name_note = "Part of the name is hidden. It still looks like the same character."
            return False
        self._same_char = False
        self._char_diff += 1
        if self._char_diff < 2:
            return False
        old_level = self.reader.level or level_for_req(self.tracker.req)
        self.reset()
        self._stale_level = (old_level, time.monotonic())   # (a reading already queued still carries it)
        self._char_print = self._char_seen = cur          # (the new character is the one to compare with from now on)
        self.other_char_at = time.monotonic()
        return True

    def _can_type_level(self):
        """Typing the level in is offered only when it can help: a %-only bar, no level known, and
        reading it from the badge keeps failing."""
        t = self.tracker
        return bool(t.req is None and self.level_wait and self.reader.level is None
                    and self.reader.level_failures >= LEVEL_FAILS_FOR_FALLBACK)

    def type_level(self, parent=None):
        """Settings > Level fallback: the player types the level shown in the game. Session only."""
        if not self._can_type_level():
            messagebox.showinfo("Level fallback", "Not needed right now: the level is read from the game by itself.",
                                parent=parent or self.root)
            return
        level = simpledialog.askinteger("Level fallback", "Your character's level, as the game shows it:",
                                        parent=parent or self.root, minvalue=1, maxvalue=len(EXP_TABLE))
        if level is None or not self._can_type_level():      # (cancelled, or the badge was read while it was open)
            return
        self.tracker.set_level(level, "typed")      # (nothing was counted without a level, so a session that runs carries on)
        self.level_wait, self.pct_note = False, ""
        self._char_print = self.reader.name_print
        self.status.set(f"Using Lv {level} as you typed it. EXP is estimated from the % until the badge can be read.")

    def _status_for(self, note):
        """What the status line says while the EXP bar is being read (details: View capture)."""
        t = self.tracker
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return f"Level up! You're now Lv {self.level}." if self.level else "Level up!"
        if self.hotkey_note and time.monotonic() - self.hotkey_note[1] < 4:
            return self.hotkey_note[0]
        if self.died_at and time.monotonic() - self.died_at < 30 and t.last_loss:
            return f"You died and lost {t.last_loss:.1f}% EXP. That isn't taken off your EXP/hr."
        if self.rejected >= 5:
            return "Having trouble reading the EXP bar clearly. View capture shows what it sees."
        if self.other_char_at and time.monotonic() - self.other_char_at < 8:
            return "This looks like another character: starting over for it."
        if self.level_wait and t.req is None:
            if self.reader.mode == "region":
                return ("Selected area: the level badge can't be read from here. "
                        "Settings, Level fallback lets you type your level in.")
            if self.reader.badge_covered:
                return ("The EXP bar shows only a percentage, and a window is covering the level badge: move it"
                        + (". Or Settings, Level fallback lets you type your level in." if self._can_type_level() else "."))
            if self._can_type_level():
                return ("The EXP bar shows only a percentage, and your level can't be read from the badge. "
                        "Settings, Level fallback lets you type it in.")
            return "The EXP bar shows only a percentage. Reading your level from the badge..."
        if self.pct_note.startswith("level changed"):
            return "Your level changed. Press Reset to start again on the new level."
        if self.pct_note.startswith("level ") and "isn't supported" in self.pct_note:
            return f"Can't estimate EXP: {self.pct_note}."
        if self.pct_note.startswith("percentage wrapped"):
            return "The percentage wrapped around. Looking at the level badge to see if you leveled up..."
        if self.pct_note.startswith("level up?"):
            return "Level up? Making sure from the level badge..."
        if t.req is None:
            # Normally the level is known within seconds; if not, it needs an EXP gain.
            if self.found_at is None or time.monotonic() - self.found_at < 8:
                return "Found your EXP bar. Working out your level..."
            return "Found your EXP bar. Your level shows after your next EXP gain."
        s = self.session
        if s.waiting == "idle":
            return (f"Paused: no EXP for {s.gap} seconds, so this time doesn't count. "
                    "It carries on at your next EXP gain.")
        if s.waiting == "map":
            return ("Paused: you left the map. It carries on at your next EXP gain "
                    "(from zero if that's on a different map).")
        if s.waiting == "blind":
            return ("Paused: the minimap's name can't be seen, so it can't tell which map you're on. "
                    "It carries on when the minimap is back.")
        if t.running:
            where = {"badge": "read from the badge", "typed": "typed by you"}.get(t.level_source, "known from before")
            how = f" Estimated from the % (the bar shows no number; your level was {where})." if t.percent_mode else ""
            return ("Tracking your EXP. Not logging this run." if self.skip_log else "Tracking your EXP.") + how
        if t.elapsed() > 0:
            return "Paused. Press Resume to carry on."
        return "Ready. Press Start when you begin farming."

    def _name_hidden(self):
        """True while tracking and the minimap's name hasn't been seen for a few seconds."""
        return bool(self.session.on and self.map_name and self.reader.mode == "window" and tesseract_ok
                    and self.reader.map_blind_seconds() > MAP_HIDDEN_SECONDS)

    def _log_tick(self, now):
        if not self.tracker.running or self.skip_log or self.in_town:
            return
        runs = len(self.autolog.runs)
        potions_now = None
        if self.cfg["potions_on"]:
            used = self.session.potions.used
            potions_now = (dict(used), self.session.potions.cost(self._prices().mapping()))
        entry = self.autolog.tick(now, self.map_name, self.level, self.party, job=self._class_name(), map_read=self.map_read,
                                  hidden=self.map_hidden, potions=potions_now,
                                  estimated=self.tracker.estimated and self.tracker.percent_mode, **self.meso.for_log())
        if len(self.autolog.runs) > runs:
            self.share_runs()                          # a run just finished
        if entry:
            self.last_logged = now
            self._fill_log()

    def _refresh(self):
        try:
            self._check_screen()
            self._fit_settings()
            self._update_display()
        except Exception as e:
            self._oops("updating the window", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(500, self._refresh)

    def _state(self):
        """A word for the status pill: where things stand."""
        t, sess = self.tracker, self.session
        if self.reader.mode == "window" and not self.reader.hwnd:
            return "waiting", "Looking for the game"
        if self.rejected >= 5:
            return "alert", "Can't read the EXP bar"
        if t.req is None:
            return "waiting", "Reading your EXP bar"
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return "tracking", "Level up!"
        if sess.waiting:
            return "paused", "Paused"
        if t.running:
            return "tracking", f"Tracking \u00b7 Lv {self.level}" if self.level else "Tracking"
        return ("paused", "Paused") if t.elapsed() > 0 else ("ready", "Ready")

    def _potion_table(self):
        """The shipped item table, for prices (the reader loads its own copy at the first look at the bar)."""
        if self._price_table is None:
            try:
                self._price_table = potions.PotionTable.load(ASSETS / "potion_table.json")
            except Exception as e:
                log_error("loading the potion table", e)
        return self._price_table

    def _meso_hr(self, el):
        """Liquid mesos/hr: inventory box, or notice meso pickups only (etc is separate)."""
        if self.cfg.get("meso_mode") == "notices":
            return self.session.notices.mesos_per_hour(el)
        return self.session.meso.per_hour(el)

    def _etc_hr(self, el):
        """NPC sell value of priced drops / hr, from the gain-notice strip."""
        return self.session.notices.loot_per_hour(el)

    def _class_name(self):
        """Class for the log / Details: typed override in Settings, else OCR from the status bar."""
        typed = (self.cfg.get("job_override") or "").strip()
        return typed or self.job

    def _class_text(self):
        """Details → Class."""
        name = self._class_name()
        if not name:
            why = ((self.reader.job_look or {}).get("why") or "").strip()
            return "—" if not why else f"— ({why.split(':')[0]})"
        if (self.cfg.get("job_override") or "").strip():
            return f"{name}  (set)"
        return name

    def _loot_text(self, amount, rate):
        """Details loot line: session total, with /hr when it has one."""
        n = self.session.notices
        if not amount and n.seen_at is None:
            return "—"
        if not amount:
            return "0"
        text = fmt_num(amount)
        if rate is not None:
            text += f"  ({fmt_num(rate)}/hr)"
        return text

    def _etc_text(self):
        """Details → Etc gain: NPC sell of etc drops only."""
        n = self.session.notices
        return self._loot_text(n.etc_gained, n.etc_per_hour(self.tracker.elapsed()))

    def _potion_loot_text(self):
        """Details → Potion gain: NPC sell of potion/food drops (not what you drank)."""
        n = self.session.notices
        return self._loot_text(n.potion_gained, n.potion_loot_per_hour(self.tracker.elapsed()))

    def _net(self, el):
        """(Net mesos/hr as text, a note or None).

        Own row: mesos/hr + etc gain/hr + potion gain/hr − potion cost/hr.
        Etc/Potion gain are listed separately in Details; this combines them.
        Potion cost is only subtracted when potion counting is on; a used potion
        priced at 0 still asks for a price first.
        """
        dash = "—"
        if not el:
            return dash, None
        meso_hr, loot_hr = self._meso_hr(el), self._etc_hr(el)
        if meso_hr is None and loot_hr is None:
            return dash, None
        gross = (meso_hr or 0) + (loot_hr or 0)
        cost = 0
        if self.cfg["potions_on"]:
            prices, used = self._prices(), self.session.potions.used
            note = prices.note(used)
            if note:
                return dash, note
            cost = self.session.potions.cost(prices.mapping()) / el * 3600
        return fmt_num(gross - cost), None

    def _prices(self):
        table = self._potion_table()
        return potions.Prices(table.prices if table else {}, self.cfg["potion_prices"])

    @property
    def meso(self):
        """The active mesos tracker: inventory box, or gain notices, from Settings."""
        return self.session.notices if self.cfg.get("meso_mode") == "notices" else self.session.meso

    def _refresh_notice_prices(self):
        """Sell values NoticeTracker uses: MeowDB etc/potions, gaps filled by half shop buy."""
        table = self._potion_table()
        etc, meow_pots = notices.load_loot_tables(ASSETS / "loot_prices.json")
        potion_sell = notices.potion_sell_prices(table.prices if table else {}, self.cfg.get("potion_prices") or {})
        # MeowDB sell-back wins when present; half shop buy covers bar items MeowDB missed.
        prices = {**potion_sell, **etc, **meow_pots}
        aliases = dict(getattr(table, "aliases", {}) or {}) if table else {}
        potion_names = set(potion_sell) | set(meow_pots)
        self.session.notices.set_prices(prices, aliases, potion_names=potion_names)

    def _left_out_text(self, m, notice_mode):
        """Details → Left out: shop/storage jumps in inventory mode; unpriced drops in notices mode."""
        if notice_mode:
            if not getattr(m, "unpriced", None):
                return "—"
            names = ", ".join(m.unpriced[-4:])
            extra = f" (+{len(m.unpriced) - 4} more)" if len(m.unpriced) > 4 else ""
            return f"unpriced: {names}{extra}"
        if any(m.left_out):
            return f"+{fmt_short(m.left_out[0])} / -{fmt_short(m.left_out[1])}"
        return "—"

    def _update_display(self):
        t, now = self.tracker, time.monotonic()
        rate, rolling = t.rate(), t.rolling_rate()
        shown = (t.exp, t.pct) if t.exp is not None else t.last_read
        best_rate = rolling or rate
        level = level_for_req(t.req) or self.level
        eta = fmt_eta((t.req - t.exp) / best_rate * 3600) if (
            t.req and t.exp is not None and best_rate and best_rate > 0) else None
        if t.running and rolling and now - self._trend_at >= 15:      # a point on the trend line every 15 s
            self._trend.append(rolling)
            self._trend_at = now
        m, el = self.meso, t.elapsed()
        notice_mode = self.cfg.get("meso_mode") == "notices"
        meso_age = None if m.seen_at is None else now - m.seen_at
        ago = fmt_ago(meso_age)
        if notice_mode:
            n = self.session.notices
            if n.seen_at is None:
                mesos = "move this window" if self.reader.notices_covered else "waiting for notices"
            else:
                mesos = f"+{fmt_short(n.mesos_gained)}" if n.mesos_gained else "0"
                if ago and meso_age is not None and meso_age > 3:
                    mesos += f"  · {ago}"
        elif m.value is None:
            mesos = "move this window" if self.reader.mesos_covered else "open inventory"
        else:
            mesos = fmt_num(m.value) + (f"  (+{fmt_short(m.gained)})" if m.gained > 0 else "")
            if ago and meso_age is not None and meso_age > 3:
                mesos += f"  · {ago}"
        state, state_text = self._state()
        meso_hr = self._meso_hr(el)
        net = self._net(el)[0]
        show_net = net != "\u2014" and (self.cfg["potions_on"] or self._etc_hr(el) is not None)
        est = lambda text: APPROX + text if t.estimated and text and text != "\u2014" else text     # (worked out from a % alone)
        reads = feature_read_status(
            exp={"ok": bool(t.req is not None and self.rejected < 5
                            and self._exp_seen_at is not None and now - self._exp_seen_at < 10),
                 "looking": t.req is None and self.rejected < 5,
                 "covered": self.rejected >= 5 or (self.last_capture and getattr(self.last_capture, "exp_kind", None) == "covered")},
            mesos={"mode": self.cfg.get("meso_mode", "notices"), "value": m.value, "seen_at": m.seen_at,
                   "covered": self.reader.notices_covered if notice_mode else self.reader.mesos_covered},
            map_name={"name": self.map_name, "hidden": self.map_hidden},
            potions={"on": self.cfg["potions_on"],
                     "ok": bool(self.cfg["potions_on"] and self._potions_seen_at
                                and now - self._potions_seen_at < 15),
                     "looking": bool(self.cfg["potions_on"] and self._potions_seen_at is None)},
            now=now)
        self.view.show(
            rate=approx_num(rate, t.estimated) if rate is not None else None,
            delta=(rolling / rate - 1) if rate and rolling else None,
            spark=list(self._trend), pct=shown[1] if shown else None,
            eta=f"Lv {level + 1} in {eta}" if level and eta else f"Lv {level}" if level else "",
            level=level or None, eta_time=eta or "",
            mesos=fmt_num(meso_hr),
            net=net if show_net else "",
            tiles={"Last 5 min": approx_num(rolling, t.estimated), "Mesos / hr": fmt_num(meso_hr),
                   "Net / hr": net if show_net else "—", "Elapsed": fmt_dur(el)},
            details={"% / hr": f"{rate / t.req * 100:.2f}%" if rate and t.req else "\u2014",
                     "Gained": est(f"{fmt_short(t.gained)}{f'  (+{t.levels} lvl)' if t.levels else ''}"),
                     "Current": (APPROX if t.percent_mode else "") + f"{fmt_num(shown[0])} [{shown[1]:.2f}%]" if shown else "\u2014",
                     "Class": self._class_text(),
                     "Party": party_label(self.party),
                     "Mesos": mesos,
                     "Mesos / 5 min": fmt_num(self.session.meso.per_5min(el) if not notice_mode
                                             else self.session.notices.per_5min(el)),
                     "Etc gain": self._etc_text(),
                     "Potion gain": self._potion_loot_text(),
                     "Potion cost": (fmt_num(self.session.potions.cost(self._prices().mapping()))
                                     if self.cfg["potions_on"] else "—"),
                     "Net mesos / hr": net,
                     "Left out": self._left_out_text(m, notice_mode)},
            state=state, state_text=state_text,
            read_health_on=bool(self.cfg.get("read_status", True)),
            read_health=reads)
        if self.level_btn is not None and self.level_btn.winfo_exists():
            self.level_btn.set(enabled=self._can_type_level())
        waiting = self.session.waiting
        self.session.tick(now)
        if self.session.fresh_restart:                  # long AFK pause → new run on resume
            self.session.fresh_restart = False
            self.autolog.reset_clock()
            self.status.set("Paused over 10 min — new run started.")
        elif self.session.waiting != waiting:           # just paused by itself: say why now
            self._after_pause_check(waiting)
            self.status.set(self._status_for(""))
        self._log_tick(now)
        self.log_status.set(self._log_status_text(now))
        self.reader.own_rect = self._window_rect()
        if now >= self._next_share:                   # retry runs that couldn't be sent
            self.share_runs()
        self._health_tick()

    # ---- buttons
    def _hotkey(self, action):
        if action == "start":
            self.toggle()
            done = "started" if self.session.on else "paused"
        elif action == "reset":
            self.reset()
            done = "reset"
        elif action == "skip":
            self.toggle_skip_log()
            done = "not logging this run" if self.skip_log else "logging this run again"
        else:
            self.set_compact(not self.compact)
            return
        self.hotkey_note = (f"{self.cfg['hotkeys'][action]}: {done}.", time.monotonic())
        self.status.set(self.hotkey_note[0])

    def toggle(self):
        now = time.monotonic()
        if self.session.on:                     # (also while it's paused by itself)
            self.recent.add("You paused")
            self.session.pause(now)
            self._set_start("Resume")
            self.autolog.reset_clock()
            return
        fresh = self.session.should_fresh_run(now)
        if fresh:
            self.session.begin_fresh_run()
            self.session.fresh_restart = False
        self.recent.add("You pressed Start")
        self.session.start(now)
        self.reader.request_map()           # read the map name now
        self._set_start("Pause")
        self.autolog.reset_clock()
        if fresh:
            self.status.set("Paused over 10 min — new run started.")
        elif self.cfg.get("meso_mode") == "notices":
            self.status.set("Started. Watching gain notices for mesos/hr.")
        elif self.meso.value is None:
            self.status.set("Started. Open the inventory once so mesos can be tracked.")
        elif self.tracker.exp is not None:
            self.status.set(self._status_for(""))   # right away, not at the next read

    def _set_start(self, text):
        """The Start button: gold when pressing it starts something, quiet while it's running."""
        self._start_text = text
        self.view.set_start(text, "pause" if text == "Pause" else "play", "secondary" if text == "Pause" else "primary")

    def reset(self):
        self.session.reset()
        self.autolog.reset_clock()
        self._char_print = self._char_seen = self.other_char_at = None      # (what was known about the character is over too)
        self._char_diff, self._same_char = 0, False
        self.pct_note, self.level_wait = "", False
        self.reader.forget_level()
        self._stale_level = None
        self.skip_log = False
        self._show_skip_log()
        self._set_start("Start")
        # Also look for the minimap, inventory and party list again (e.g. after moving them).
        self.reader.look_again()
        self.map_name, self.party, self._party_seen = None, 1, (None, 0)
        self.cfg.pop("mesos_box", None)
        self.cfg.pop("mesos_boxes", None)
        self.reader.saved_mesos = None
        self.reader.saved_mesos_boxes = {}
        save_config(self.cfg)
        self.status.set("Reset. Looking for the map name, inventory and party list again...")

    def toggle_skip_log(self):
        self.skip_log = not self.skip_log
        self.autolog.reset_clock()              # nothing from before or during it is logged
        self._show_skip_log()
        self.status.set("This run won't be logged. Logging turns back on when you leave the map "
                        "or stop getting EXP." if self.skip_log else "Logging this run again.")

    def _show_skip_log(self):
        self.view.logging.set(not self.skip_log, "Not logging" if self.skip_log else "Log this run")

    def _after_pause_check(self, waiting_before):
        """When the run ends (tracking pauses by itself), "Don't log" turns off again, so
        the next run is logged. (Pressed while already paused, it's for the next run.)"""
        if self.skip_log and waiting_before is None and self.session.waiting:
            self.skip_log = False
            self._show_skip_log()

    def _new_source(self):
        # New place to read from -> new baseline, but keep the session totals.
        self.tracker.exp = self.tracker.pct = None
        self.tracker._pending = self.tracker._pending_lvl = None
        self.autolog.reset_clock()
        save_config(self.cfg)

    def _show_area_mode(self):
        pass                                              # (the More menu reads the mode when it opens)

    def toggle_area(self):
        if self.reader.mode == "region":
            self.follow_game()
        else:
            self.select_area()

    def follow_game(self):
        self.reader.use_window()
        self.cfg["mode"] = "window"
        self._new_source()
        self._show_area_mode()
        self.status.set("Following the MapleStory window...")

    def select_area(self):
        def done(region):
            if region:
                self.reader.use_region(region)
                self.cfg.update(mode="region", region=region)
                self._new_source()
                self._show_area_mode()
                self.status.set("Using your selected area. Click Follow game to go back to "
                                "finding it automatically.")
        RegionSelector(self.root, done)

    @staticmethod
    def _boxed_image(bgr, boxes):
        """A screenshot crop, enlarged, with a box drawn around each digit found."""
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        scale = min(4.0, max(1.0, 120 / img.height), 900 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        colors = {"exp": "#ffcc33", "pct": "#33ccff", "mesos": "#66dd66"}
        for x0, y0, x1, y1, kind in boxes:
            draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                           outline=colors[kind], width=2)
        return img

    def _boxed(self, bgr, boxes):
        return ImageTk.PhotoImage(self._boxed_image(bgr, boxes))

    def _class_line(self):
        """What the class reading shows: (picture or None, boxes, a line saying how it went)."""
        look = self.reader.job_look
        name = self._class_name()
        if (self.cfg.get("job_override") or "").strip():
            how = f"Class. Set in Settings: {name}" + (f" (OCR: {self.job})" if self.job else "")
        elif self.job:
            how = f"Class. Read: {self.job}"
        elif look:
            how = f"Class. Not read: {look.get('why') or 'no clear look yet'}"
        else:
            how = "Class. Not read yet (it's tried every few seconds once the EXP bar is found)"
        return (look or {}).get("image"), (look or {}).get("boxes", []), how

    def view_capture(self):
        r, mr = self.last_capture, self.last_mesos
        if not r and not mr:
            messagebox.showinfo("View capture", "Nothing has been read from the game yet.\n\n"
                                "Make sure MapleStory is open and its EXP bar is showing.",
                                parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Last capture")
        win.configure(bg=BG)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            win.after(delay, lambda: PLATFORM.style_window(win, INK, TEXT_DIM) if win.winfo_exists() else None)
        th = self.theme
        shots = self._look_shots()
        win._imgs = []
        for shot, boxes, text in shots:
            if shot is not None and shot.size:
                win._imgs.append(self._boxed(shot, boxes))
                tk.Label(win, image=win._imgs[-1], bg=BG).pack(padx=th.px(16), pady=(th.px(16), th.px(4)))
            else:
                tk.Frame(win, bg=BG, height=th.px(12)).pack()
            tk.Label(win, text=text, bg=BG, fg=FG, font=th.font("semibold", 12), wraplength=th.px(560),
                     justify="left").pack(anchor="w", padx=th.px(16))
        bottom = tk.Frame(win, bg=BG)
        bottom.pack(fill="x", padx=th.px(16), pady=(th.px(12), th.px(16)))
        tk.Label(bottom, text="Boxes show where each digit was found.", bg=BG, fg=DIM,
                 font=th.font("regular", 11)).pack(side="left")
        Button(bottom, th, text="Something's wrong? Report a bug", kind="secondary", width=240, height=36, radius=10,
               size=12, command=lambda: self.report_bug(win), bg=BG).pack(side="right")

    # ---- bug reports
    def report_bug(self, parent=None):
        """Asks how to report: straight to the developer (no account) or on GitHub."""
        parent = parent or self.root
        th = self.theme
        w = self._small_window("Report a bug", None)
        tk.Label(w, text="Report a bug", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text="How would you like to report it?", bg=BG, fg=DIM, font=th.font("regular", 12)
                 ).pack(anchor="w", pady=(2, th.px(14)))

        def pick(then):
            w.destroy()
            then(parent)
        Button(w, th, text="Send to the developer", kind="primary", width=300, height=40, radius=12, size=13,
               command=lambda: pick(self.send_report), bg=BG).pack(anchor="w")
        tk.Label(w, text="No account needed. You see exactly what is sent first.", bg=BG, fg=FAINT,
                 font=th.font("regular", 11)).pack(anchor="w", pady=(2, th.px(12)))
        Button(w, th, text="Report on GitHub", kind="secondary", width=300, height=40, radius=12, size=13,
               command=lambda: pick(self._report_on_github), bg=BG).pack(anchor="w")
        tk.Label(w, text="Needs a free GitHub account. You can add pictures.", bg=BG, fg=FAINT,
                 font=th.font("regular", 11)).pack(anchor="w", pady=(2, th.px(12)))
        Button(w, th, text="Cancel", kind="secondary", width=96, height=36, radius=10, size=12,
               command=w.destroy, bg=BG).pack(anchor="e")
        self._report_choice = w
        return w

    def _report_on_github(self, parent=None):
        """Opens a new GitHub issue in the browser, filled in with what the tracker knows, and
        saves a picture and a text file of the details for the player to drag in. Nothing is
        sent by the tracker itself: the player sees everything and clicks Submit (or doesn't)."""
        parent = parent or self.root
        if not messagebox.askokcancel(
                "Report a bug",
                "Your browser will open a bug report on GitHub, already filled in with your "
                "version and what the overlay was reading (nothing personal).\n\n"
                "1. Write a sentence about what went wrong.\n"
                "2. Drag in the two files from the folder that opens.\n"
                "3. Click Submit.\n\n"
                "You need a free GitHub account.", parent=parent):
            return
        screen = messagebox.askyesno(
            "Report a bug",
            "Also save a small picture of the whole game window?\n\n"
            "It shows exactly what the overlay sees, which helps a lot when something can't be found. "
            "But it can include chat and character names, so only drag it in if you're happy with that.\n\n"
            "(No is fine: the report works without it.)", default="no", parent=parent)
        files = self._save_report_files(screen)
        query = urllib.parse.urlencode({"title": f"Bug: (describe it here) [v{VERSION}]",
                                        "body": self._report_text(files)})
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new?{query}")
        shown = files.get("picture") or files.get("details")
        if shown:
            try:
                PLATFORM.show_file(shown)
            except Exception as e:
                log_error("opening the bug report's folder", e)

    def _report_details(self):
        """The bug report's details text, scrubbed: the same words go to GitHub and to Send report."""
        facts = self._report_facts()
        self._report_facts_cache = facts
        return self._private(report.details(facts))

    def send_report(self, parent=None):
        """The Send report dialog: the player types what went wrong, sees exactly what will be sent
        (text only), and the overlay posts it to the stats site."""
        th = self.theme
        try:
            details = self._report_details()
        except Exception as e:
            log_error("collecting the details for Send report", e)
            details = ""
        w = self._small_window("Send to the developer", None)
        w.details = details
        tk.Label(w, text="Send to the developer", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text="What went wrong?", bg=BG, fg=FG, font=th.font("semibold", 12)
                 ).pack(anchor="w", pady=(th.px(10), th.px(4)))
        box = tk.Frame(w, bg=LIST_BG, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="x")
        w.message = tk.Text(box, width=58, height=5, wrap="word", bg=LIST_BG, fg=FG, insertbackground=FG, relief="flat",
                            font=th.font("regular", 12), padx=th.px(10), pady=th.px(8), highlightthickness=0, undo=True)
        w.message.pack(fill="x")
        counter = tk.Label(w, text=f"0 / {report.MAX_MESSAGE:,}", bg=BG, fg=FAINT, font=th.font("regular", 10))
        counter.pack(anchor="e")

        def typed(event=None):
            text = w.message.get("1.0", "end-1c")
            counter.configure(text=f"{report.units(text):,} / {report.MAX_MESSAGE:,}",
                              fg=BAD if report.units(report.clean_message(text)) > report.MAX_MESSAGE else FAINT)
        w.message.bind("<KeyRelease>", typed)
        w.counter, w.typed = counter, typed
        fold = tk.Frame(w, bg=BG)
        shown = tk.BooleanVar(value=False)
        w.fold, w.fold_open = fold, shown
        w.sent_text = tk.Text(fold, width=58, height=10, wrap="word", bg=LIST_BG, fg=DIM, relief="flat",
                              font=th.font("regular", 10), padx=th.px(10), pady=th.px(8), highlightthickness=0)
        w.sent_text.insert("1.0", details or "(nothing)")
        w.sent_text.configure(state="disabled")
        scroll = ttk.Scrollbar(fold, command=w.sent_text.yview, style="Dark.Vertical.TScrollbar")
        w.sent_text.configure(yscrollcommand=scroll.set)
        w.sent_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def toggle():
            if shown.get():
                fold.pack_forget()
            else:
                fold.pack(fill="x", before=w.note)
            shown.set(not shown.get())
            w.fold_button.set(text="What will be sent  ▾" if shown.get() else "What will be sent  ▸")
        w.fold_button = Button(w, th, text="What will be sent  ▸", kind="secondary", width=200, height=32, radius=10,
                               size=11, command=toggle, bg=BG)
        w.fold_button.pack(anchor="w", pady=(th.px(8), th.px(6)))
        w.note = tk.Label(w, text="No pictures are sent. To add a picture, use Report on GitHub.", bg=BG, fg=FAINT,
                          font=th.font("regular", 11), wraplength=th.px(520), justify="left")
        w.note.pack(anchor="w")
        w.status = tk.Label(w, text="", bg=BG, fg=DIM, font=th.font("regular", 11), wraplength=th.px(520), justify="left")
        w.status.pack(anchor="w", pady=(th.px(6), 0))
        buttons = tk.Frame(w, bg=BG)
        buttons.pack(fill="x", pady=(th.px(10), 0))
        w.cancel_button = Button(buttons, th, text="Cancel", kind="secondary", width=96, height=38, radius=11, size=12,
                                 command=w.destroy, bg=BG)
        w.cancel_button.pack(side="right")
        w.send_button = Button(buttons, th, text="Send", kind="primary", width=96, height=38, radius=11, size=12,
                               command=lambda: self._submit_report(w), bg=BG)
        w.send_button.pack(side="right", padx=(0, th.px(8)))
        self._report_window = w
        w.message.focus_set()
        return w

    def _submit_report(self, w):
        """Send was clicked: check the text and the daily limit, then post in the background."""
        def say(text, bad=True):
            w.status.configure(text=text, fg=BAD if bad else GOOD)
        message = w.message.get("1.0", "end-1c")
        problem = report.message_problem(message)
        if problem:
            return say(problem)
        if reports_sent_today(self.cfg) >= MAX_REPORTS_PER_DAY:
            return say("You've sent 3 reports today. Please try again tomorrow, or use Report on GitHub.")
        if sharing_blocked():
            return say("Sending is off while testing.")
        if healthlog.ensure_id(self.cfg):
            save_config(self.cfg)
        body = report.report_body(self.cfg["health_id"], VERSION, message, w.details)
        w.send_button.set(enabled=False)
        say("Sending...", bad=False)

        def work():
            try:
                outcome = post_report(body)
            except Exception:
                outcome = "later"
            self.q.put(("report_sent", (w, outcome)))
        threading.Thread(target=work, daemon=True).start()

    def _report_done(self, w, outcome):
        """The site answered a Send report."""
        if not w.winfo_exists():
            if outcome == "ok":
                self._count_report()
            return
        texts = {"limit": "You've sent 3 reports today. Please try again tomorrow, or use Report on GitHub.",
                 "message": "Please leave out links.",
                 "bad": "The site couldn't take this report. Please use Report on GitHub instead.",
                 "later": "Couldn't send it. Are you connected to the internet?"}
        if outcome == "ok":
            self._count_report()
            w.status.configure(text="Sent, thank you!", fg=GOOD)
            w.cancel_button.set(text="Close")
            return
        if outcome == "limit":
            self.cfg["reports_sent"] = {"day": healthlog.utc_day(time.time()), "n": MAX_REPORTS_PER_DAY}
            save_config(self.cfg)
        w.status.configure(text=texts.get(outcome, texts["later"]), fg=BAD)
        w.send_button.set(enabled=True)

    def _count_report(self):
        self.cfg["reports_sent"] = {"day": healthlog.utc_day(time.time()), "n": reports_sent_today(self.cfg) + 1}
        save_config(self.cfg)

    @staticmethod
    def _private(text, limit=report.MAX_DETAILS):
        """Nothing personal: file paths cut to their last part, and the Windows or macOS user name and the
        computer name taken out wherever they appear (see report.scrub)."""
        names = [Path.home().name, os.environ.get("USERNAME", ""), os.environ.get("USER", ""),
                 os.environ.get("LOGNAME", ""), os.environ.get("COMPUTERNAME", ""), platform.node()]
        return report.scrub(text, names, limit)

    @staticmethod
    def _errors_tail(size=2500):
        """The end of errors.log, scrubbed. All of it is scrubbed first and only then cut, so the cut can't
        leave half a path, and it really is the end of the file."""
        try:
            return App._private(ERROR_LOG_PATH.read_text(encoding="utf-8", errors="replace"), None)[-size:]
        except Exception:
            return ""

    def _report_facts(self):
        """Everything a bug report says, as a plain dict (see report.py)."""
        r, t = self.last_capture, self.tracker
        d = self.reader.diagnostics()
        rect = d["rect"]
        monitors = []
        try:
            with mss.mss() as sct:
                monitors = [(m_["width"], m_["height"]) for m_ in sct.monitors[1:]]
        except Exception as e:
            log_error("listing the screens for a bug report", e)
        errors = self._errors_tail()
        now = time.monotonic()
        h = self.health
        up = max(1.0, now - self.started)
        tess = TESS
        return {
            "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "system": PLATFORM.version(),
            "python": sys.version.split()[0], "monitors": monitors, "uptime_s": up,
            "cpu_pct": round((time.process_time() - self.cpu0) / up * 100, 1),
            "dpi_awareness": PLATFORM.dpi_awareness(),
            "dpi": PLATFORM.screen_dpi(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2) if rect else None,
            "tesseract": {"found": bool(tess), "version": tess.version() if tess else None,
                          "where": ("included" if str(APP_DIR) in str(tess.cmd) else "installed") if tess else None,
                          "state": tesseract_state, "why": tesseract_why or None,
                          "exe": self._private(tesseract_where[0]) if tesseract_where[0] else None,
                          "tessdata": self._private(tesseract_where[1]) if tesseract_where[1] else None},
            "game": {"mode": d["mode"], "found": d["found"], "rect": rect, "gh": d["gh"], "region": d["region"],
                     "minimized": bool(d["found"] and not rect and d["mode"] == "window"),
                     "fullscreen": bool(rect and any(rect[2] == mw and rect[3] == mh for mw, mh in monitors))},
            "overlay": {"rect": d["own_rect"], "covering": d["covering"]},
            "reader": {**{k: d[k] for k in ("exp_box", "exp_misses", "map_box", "mesos_box", "mesos_fails", "two_boxes",
                                            "mesos_learned", "party_area", "job_why")},
                       "level_unknown": t.req is None},
            "shadow": self.reader.shadow.facts(),
            "health": {"alive": h.alive and self._worker_alive(), "polls": h.polls, "errors": h.errors,
                       "last_error": h.last_error, "average_ms": h.average_ms(), "max_ms": h.max_ms,
                       "last_ms": h.last_ms, "age": None if h.last_at is None else now - h.last_at},
            "rejected": self.rejected, "status": self.status.get().replace("\n", " | "),
            "last_exp": r.text if r else None, "level": self.level, "job": self._class_name(), "map": self.map_name,
            "party": party_label(self.party),
            "mesos": fmt_num(self.meso.value) if self.meso.value is not None else None, "tracking": bool(self.session.on),
            "tracker": f"paused: {self.session.waiting or 'no'}, gained {fmt_num(t.gained)} EXP in {fmt_dur(t.elapsed())}",
            "settings": {"mode": self.reader.mode, **{k: self.cfg.get(k) for k in ("gap_seconds", "transparency", "topmost", "ui_scale",
                                                      "compact", "hotkeys_on", "share_runs", "check_updates")},
                         "share_health": self.cfg.get("share_health"), "health_last_sent": self.healthlog.last_sent or None},
            "timeline": [self._private(x) for x in self.recent.lines()], "errors": errors,
        }

    def _worker_alive(self):
        return getattr(self, "_worker_thread", None) is None or self._worker_thread.is_alive()

    def _look_shots(self):
        """What each reader last saw, with a line on how it went: [(picture or None, boxes, text)]."""
        r, mr = self.last_capture, self.last_mesos
        shots = []
        if r:
            shots.append((r.image, r.boxes, f"EXP bar. Read: {r.text or 'nothing'}   ({r.where})"))
        if mr:
            read = fmt_num(mr.mesos) if mr.mesos is not None else "nothing (waiting for a clear look)"
            shots.append((mr.mesos_image, mr.mesos_boxes, f"Mesos. Read: {read}"))
        look = getattr(self.reader, "notices_look", None)
        if look and self.cfg.get("meso_mode") == "notices":
            shots.append((look.get("image"), [],
                          f"Gain notices. Read: {look.get('text') or 'nothing (no bright lines)'}"))
        for kept in list(self.reader.shadow.kept):
            shots.append((kept["image"], kept["boxes"], f"Reader v2 test ({kept['kind']}). Old: {report.answer_text(kept['old'])}"
                                                        f"   v2: {report.answer_text(kept['v2'])}"))
        if r:
            shots.append(self._class_line())
            look = self.reader.map_look
            if look:
                shots.append((look["image"], [], f"Map name. Read: {look['text'] or 'nothing (not clear yet)'}"))
        return shots

    def _save_report_files(self, with_screen=False):
        """The picture of what was read, the details as text, and (if asked) the game window:
        saved in the data folder. Returns {"picture", "details", "screen"} (missing ones left out)."""
        files = {}
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_error("making the data folder for a bug report", e)
            return files
        facts = None
        try:
            facts = self._report_facts()
            path = DATA_DIR / "bug-report.txt"
            path.write_text(self._private(report.details(facts)), encoding="utf-8")
            files["details"] = path
        except Exception as e:
            log_error("saving the bug report details", e)
        self._report_facts_cache = facts
        picture = self._save_report_picture()
        if picture:
            files["picture"] = picture
        if with_screen:
            screen = self._save_report_screen()
            if screen:
                files["screen"] = screen
        return files

    def _captioned(self, image, text):
        """A crop with a line of text above it."""
        try:
            font = ImageFont.truetype(str(ASSETS / "fonts" / "ManropeSemiBold.ttf"), 15)
        except Exception:
            font = ImageFont.load_default()
        w = max(image.width if image is not None else 0, 760)
        sheet = Image.new("RGB", (w, (image.height if image is not None else 0) + 26), BG)
        ImageDraw.Draw(sheet).text((2, 3), text[:110], fill=FG, font=font)
        if image is not None:
            sheet.paste(image, (0, 26))
        return sheet

    def _save_report_picture(self):
        """What each reader last saw (EXP bar, mesos, class, map name), with captions, as one PNG."""
        parts = []
        for shot, boxes, text in self._look_shots():
            image = self._boxed_image(shot, boxes) if shot is not None and shot.size else None
            parts.append(self._captioned(image, text))
        if not parts:
            return None
        sheet = Image.new("RGB", (max(p.width for p in parts), sum(p.height + 8 for p in parts)), BG)
        y = 0
        for p in parts:
            sheet.paste(p, (0, y))
            y += p.height + 8
        path = DATA_DIR / "bug-report.png"
        try:
            sheet.save(path)
            return path
        except Exception as e:
            log_error("saving the bug report picture", e)
            return None

    def _save_report_screen(self):
        """The game window as the overlay sees it (this window blacked out), shrunk, as a JPEG."""
        rect = self.reader.last_rect
        if not rect:
            return None
        try:
            with mss.mss() as sct:
                # (always a screen copy, whatever the reader uses: it shows what is on screen, this window hidden)
                shot = self.reader._hide_own(grab(sct, *rect), screen_copy=True)
            if not shot:
                return None
            img = Image.fromarray(cv2.cvtColor(shot[0], cv2.COLOR_BGR2RGB))
            img.thumbnail((1600, 900), Image.LANCZOS)
            path = DATA_DIR / "bug-report-game.jpg"
            img.save(path, quality=72)
            return path
        except Exception as e:
            log_error("saving the bug report's game picture", e)
            return None

    def _report_text(self, files):
        """The bug report's text: room to describe it, then the short details (the whole of it is
        in bug-report.txt: the address a report is opened with can't be very long)."""
        facts = getattr(self, "_report_facts_cache", None) or self._report_facts()
        attach = [f"`{files[k].name}`" for k in ("picture", "details", "screen") if k in files]
        errors = (facts.get("errors") or "").strip()[-700:]
        lines = [
            "**What went wrong?**", "(Describe it here: what did you do, what did you expect, what did it show?)", "",
            "**Files**",
            (f"(Drag in {' and '.join(attach)} from the folder that just opened: they show what the overlay read "
             "and how it's doing.)" if attach else "(If you can, add a screenshot of the game and the overlay.)"), "",
            "---", "<details><summary>Details (filled in by the overlay)</summary>", "",
            self._private(report.summary(facts)),
        ]
        if errors:
            lines += ["", "Recent errors:", "```", errors, "```"]
        lines += ["", "</details>"]
        return "\n".join(lines)

    def _remember_log_size(self):
        """Keeps the log's column widths, and the window size if the user resized it."""
        tree, root = self.log_tree, self.root
        self.cfg["log_columns_pt"] = {c: round(int(tree.column(c, "width")) / self._px_per_pt, 1)
                                      for c in tree["columns"]}
        self.cfg.pop("log_columns", None)
        if self.log_open:
            size = [root.winfo_width(), root.winfo_height()]
            natural = [root.winfo_reqwidth(), root.winfo_reqheight()]
            self.cfg["log_size"] = size if size != natural else None

    def close(self):
        self._note_flush()
        self.stop_evt.set()
        try:
            self._remember_log_size()
        except Exception as e:
            log_error("saving the log size", e)
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.healthlog.save()
        self.root.destroy()


def main():
    global tesseract_ok, TESS, tesseract_state, tesseract_why
    # Only one copy at a time (per data folder): two copies would each keep their own log.
    # If it's already running, bring that window to the front instead. (The lock keeps the old
    # name, so an older version and this one can't run at the same time either.)
    key = hashlib.md5(str(DATA_DIR).lower().encode()).hexdigest()[:12]
    if not PLATFORM.claim_single_instance(key, WINDOW_TITLE, OLD_TITLES):
        return
    PLATFORM.ask_permissions()
    move_old_data()
    try:
        tess, tessdata, tesseract_state, tesseract_why = locate_tesseract()
    except Exception as e:                  # (EXP and mesos work without Tesseract: never a reason not to start)
        log_error("looking for Tesseract", e)
        tess = None
    if tess:
        TESS = ocr.Tesseract(tess, tessdata=tessdata or None)
        tesseract_ok = True
    root = tk.Tk()
    App(root, DigitTemplates.load())
    root.mainloop()


if __name__ == "__main__":
    main()
