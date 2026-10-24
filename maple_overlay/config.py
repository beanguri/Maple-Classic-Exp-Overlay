"""Where the program keeps its files, its settings and the numbers its parts share (times, limits, defaults)."""
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

from maple_overlay.platforms import create_platform, parse_hotkey
from maple_overlay.vision import potions


# Everything that differs between Windows and macOS (finding the game window, screen scaling,
# hotkeys...) is behind this one object: see platforms/.
PLATFORM = create_platform()

PLATFORM.setup_process()

VERSION = "0.9.12-beta"

WINDOW_TITLE = "Maple Classic Exp Overlay"    # the version is shown in Settings

OLD_TITLES = ("Maple EXP/hr",)               # what versions before 0.9.3 were called

# Files that come with the program (inside the .exe when packaged), the folder it runs from,
# and the folder for your settings, log and learned digits (MAPLE_EXP_DATA can override it).
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

ASSETS = APP_DIR / "assets"

HOME_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.environ.get("MAPLE_EXP_DATA") or PLATFORM.data_root() / "MapleEXP")

CONFIG_PATH = DATA_DIR / "config.json"

ERROR_LOG_PATH = DATA_DIR / "errors.log"

TEMPLATE_PATH = ASSETS / "digits.npz"

ICON_PATH = ASSETS / "icon.ico"

RATE_HZ_DEFAULT, RATE_HZ_MIN, RATE_HZ_MAX = 7, 1, 20      # how many times a second the screen is read (Settings > Data collection rate)

ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 3.0, 0.05           # the window zooms with its size (drag its edge): everything in it grows or shrinks together

UI_HZ_DEFAULT, UI_HZ_MIN, UI_HZ_MAX = 1, 1, 20            # how many times a second the numbers on screen are redrawn (Settings > Display refresh rate)

ROLLING_WINDOW = 300        # seconds used for the "last 5 min" rate

MESO_GAP = 120              # seconds without a mesos look after which a rise is believed, however big

SEARCH_STRIP = 0.22         # bottom part of the game window searched for the EXP text

LOG_PATH = DATA_DIR / "exp_log.json"

LOG_TEXT_PATH = DATA_DIR / "exp_log.txt"

SESSION_LOG_PATH = DATA_DIR / "session_log.csv"       # one line per event (start, pause, map, party, level up...), opens in Excel

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

OUTLINE_SECONDS = 8         # how often the (costly) search for a see-through mesos box may run

LEVEL_RECHECK_SECONDS = 30  # a level read from the badge is looked at again this often (it only changes at a level-up)

LEVEL_FAILS_FOR_FALLBACK = 3    # badge reads that failed in a row before typing the level in is offered

DEFAULTS = {"rate_hz": RATE_HZ_DEFAULT, "ui_hz": UI_HZ_DEFAULT, "gap_seconds": 15, "transparency": 0, "topmost": True,
            "check_updates": True, "skip_version": "", "ui_scale": 100, "zoom": 1.0, "compact": False, "hotkeys_on": True,
            "details": False, "compact_shape": "square", "seen_version": "",
            "potions_on": True, "potion_prices": {}}

SHAPE_LABELS = {"square": "Square", "wide": "Wide", "bar": "Bar"}

SHAPE_BLURBS = {"square": "The narrow card: the big number, the level bar and Start/Pause.",
                "wide": "Longer sideways: the level bar on top and four numbers in a row.",
                "bar": "One thin strip with the main numbers and a slim level bar."}

# What's new, shown once after updating to a version (plain words, newest changes only). A version with no entry shows nothing.
WHATS_NEW = {"0.9.12-beta": (
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

# --------------------------------------------------------------------------- files
_error_lock = threading.Lock()

_last_errors = {}

def log_error(what, exc=None):
    """Adds an error, with details, to errors.log in the data folder (the same one at most
    once a minute), so players can send it in when something goes wrong."""
    key = (what, repr(exc))
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
    if type(cfg.get("zoom")) is int:
        cfg["zoom"] = float(cfg["zoom"])                     # (a whole number is a fine zoom: 2, not only 2.0)
    for key, default in DEFAULTS.items():
        value = cfg.get(key)
        if type(value) is not type(default) or (key == "gap_seconds" and not 1 <= value <= 3600):
            cfg[key] = default
    cfg["rate_hz"] = min(RATE_HZ_MAX, max(RATE_HZ_MIN, cfg["rate_hz"]))
    cfg["ui_hz"] = min(UI_HZ_MAX, max(UI_HZ_MIN, cfg["ui_hz"]))
    cfg["transparency"] = min(MAX_TRANSPARENCY, max(0, cfg["transparency"]))
    cfg["zoom"] = round(min(ZOOM_MAX, max(ZOOM_MIN, cfg["zoom"])), 2)
    cfg["potion_prices"] = potions.clean_prices(cfg["potion_prices"])
    if cfg["compact_shape"] not in SHAPES:
        cfg["compact_shape"] = "square"
    if cfg["ui_scale"] not in UI_SCALES:
        cfg["ui_scale"] = 100
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
              "party_view": lambda v: v in ("closed", "docked", "floating"),
              "share_runs": lambda v: type(v) is bool}       # missing: not asked yet
    for key, ok in checks.items():
        if key in cfg and cfg[key] is not None and not ok(cfg[key]):
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

# --------------------------------------------------------------------------- hotkeys
#
# Shift+1 to 4 by default (changeable in Settings). How they're caught is up to the OS
# (PLATFORM.create_hotkeys); they're only on while MapleStory or the overlay is in front.
HOTKEY_ACTIONS = {"start": "Start / Pause", "reset": "Reset", "skip": "Don't log", "compact": "Compact"}

DEFAULT_HOTKEYS = {"start": "Shift+1", "reset": "Shift+2", "skip": "Shift+3", "compact": "Shift+4"}

EXTRA_KEYS = ("kills", "own_exp", "bonus_exp", "mesos_drops", "mesos_text", "deaths", "levels", "x_seconds")   # per-stretch counters added to a log line (see AutoLog._add)

BLOCKS_KEPT = 300   # the 5-minute stretches kept on a log line, for the pace chart in its Details window

LOG_VERSION = 4     # 2: mesos/hr from the mesos tracker; 3: class, seals and runs; 4: potion cost

NOTE_MAX = 4000     # characters in a note on a log line

NOTE_MARK = "\u270e"            # shown in the log's Note column for lines that have one

BACKUPS_KEPT = 7    # days of log backups kept (backups/ in the data folder)

RUN_SECONDS = 900   # a run: 15 minutes of non-stop farming on one map, one party size

SITE_GAP = 15       # seconds without EXP that end a run, whatever the gap setting (which is for the log lines)

END_LOOK = 30       # a run goes to the website with mesos only with an inventory look this soon after its end...

START_GRACE = 10    # ...and one at or before its start (or this soon after: the first look on a new map)

PAUSED_WAIT = 7     # paused while a run waits for that look: how long it still waits

MAX_LOOKS = 5000    # inventory looks kept (over 40 minutes of them with the inventory open)

MAX_MESOS_HR = 20_000_000     # the website's own ceiling: a run above it is a misread

RUN_ALERT = "Open the inventory now so your run is logged for the website."

SHAPES = ("square", "wide", "bar")             # the compact window's shapes

