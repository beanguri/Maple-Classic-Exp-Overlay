"""What the overlay needs from the operating system, as one interface (Platform) that each
OS implements: finding the game window, where windows are, screen scaling, hotkeys, and a
few odds and ends. The rest of the overlay is the same everywhere: it takes screenshots with
mss (which works on every OS) and reads them.

Rectangles are (left, top, width, height) in the units mss screenshots use: physical pixels
on Windows, points on macOS. A window handle is whatever the OS uses to name a window."""
from abc import ABC, abstractmethod


# --------------------------------------------------------------------------- hotkey names
#
# A hotkey is written like "Shift+1" in the settings file and the Settings window, and kept
# as (modifiers, key) in Windows' codes: what RegisterHotKey takes, and what tkinter's
# keycode is on Windows.

HOTKEY_MODS = {"Ctrl": 0x2, "Alt": 0x1, "Shift": 0x4}           # MOD_CONTROL, MOD_ALT, MOD_SHIFT


def _key_name(vk):
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x6F}"
    return None


def parse_hotkey(text):
    """'Shift+1' -> (modifiers, virtual key), or None if it isn't a hotkey the overlay takes:
    Ctrl, Alt and/or Shift with a number or letter, or an F key (with or without them)."""
    if not isinstance(text, str) or not text:
        return None
    *mods, key = text.split("+")
    if len(set(mods)) != len(mods) or any(m not in HOTKEY_MODS for m in mods):
        return None
    vk = next((v for v in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)) + list(range(0x70, 0x7C))
               if _key_name(v) == key), None)
    if vk is None or (not mods and not key.startswith("F")):     # a plain letter would block typing
        return None
    return sum(HOTKEY_MODS[m] for m in mods), vk


def hotkey_text(ctrl, alt, shift, vk):
    """The name of a key pressed with these modifiers (e.g. 'Shift+1'), or None if it can't be one."""
    key = _key_name(vk)
    text = "+".join([m for m, on in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)) if on] + [key or "?"])
    return text if key and parse_hotkey(text) else None


# --------------------------------------------------------------------------- interfaces

class Hotkeys(ABC):
    """Keys that work while MapleStory or the overlay is in front. on_press(action) is called
    (from any thread) for each press."""

    supported = True                        # False: Settings says so instead of offering them

    def __init__(self, on_press):
        self.on_press = on_press
        self.problems = {}                  # action -> why it couldn't be used (e.g. taken)

    @abstractmethod
    def set(self, keys, on=True):
        """keys: action -> 'Shift+1' (or '' for off)."""

    @abstractmethod
    def pause(self, paused):
        """Off for a moment (e.g. while a new hotkey is being chosen), then back on."""


class Platform(ABC):
    """One OS. create_platform() picks the one the overlay is running on."""

    name = "?"

    def setup_process(self):
        """Called once at start, before any window or screenshot."""

    @abstractmethod
    def version(self):
        """The OS and its version, for bug reports (e.g. 'Windows 10.0.22631')."""

    @abstractmethod
    def data_root(self):
        """The folder the settings folder goes in, as a Path."""

    @abstractmethod
    def tesseract_candidates(self, home_dir, app_dir):
        """Where to look for Tesseract, best first. Never the current folder."""

    @abstractmethod
    def claim_single_instance(self, key, title, old_titles=()):
        """Makes this the only copy running for `key` (one per data folder). If another copy
        already is, shows it (or says so) and returns False."""

    def ask_permissions(self):
        """Asks for anything the OS makes the user allow before the screen can be read."""

    def missing_permission(self):
        """Why other programs' windows can't be seen right now, or None if they can."""
        return None

    @abstractmethod
    def find_game_window(self):
        """Handle of the MapleStory game window (the biggest, if there are several), or None."""

    @abstractmethod
    def window_exists(self, handle):
        """Is that window still there (even if minimized)?"""

    @abstractmethod
    def client_rect(self, handle):
        """The game area of the window on screen, or None (also when it's minimized)."""

    # True: capture_window pictures the game window alone, so nothing on top of it (this
    # overlay included) is ever in the way.
    captures_window_alone = False

    def capture_window(self, handle, left, top, width, height):
        """A screen rectangle inside that window as only the window shows it (an (h, w, 4) BGRA
        array, like mss gives), or None if that can't be done right now."""
        return None

    @abstractmethod
    def own_window_rect(self, root):
        """The overlay's own window on screen, frame included, from its tkinter root; or None."""

    @abstractmethod
    def screen_dpi(self, x, y):
        """The DPI to size text for on the screen at (x, y), or None if unknown."""

    @abstractmethod
    def create_hotkeys(self, on_press):
        """A Hotkeys for this OS."""

    @abstractmethod
    def open_folder(self, path):
        """Shows the folder in the OS's file browser."""

    @abstractmethod
    def show_file(self, path):
        """Opens the file's folder in the OS's file browser, with the file selected."""

    def dpi_awareness(self):
        """How this program handles screen scaling, in words, for bug reports (None if n/a)."""
        return None

    def font(self, family, weight):
        """(family, weight) to use for one of the overlay's fonts, named by its Windows font."""
        return family, weight

    def register_fonts(self, paths):
        """Makes the font files available to this program only (not installed). True if all worked."""
        return False

    def style_window(self, root, background, text):
        """Makes the window's frame match the overlay's colours where the OS allows it
        (the title bar), given as '#rrggbb'. Does nothing where it can't."""
