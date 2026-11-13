"""Linux: MapleStory runs under Wine/Proton, so its window is an X11 one (native X11, or XWayland
on a Wayland desktop) and is found by its WM_CLASS or title through Xlib. The overlay itself is
a tkinter (X11) window too, so both live in the same X coordinate space.

On Wayland a copy of the whole screen comes back black, so the game is pictured from its own
window (XGetImage on it, which XWayland serves from the window's own buffer): nothing on top of
it, the overlay included, gets in the way. Hotkeys are X11 key grabs, which XWayland only
delivers while an X11 window (the game or the overlay) has the keyboard: what they're for."""
import fcntl
import os
import platform
import select
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
from Xlib import X, XK, display, error

from .base import Hotkeys, Platform, parse_hotkey

_NOT_GAME = ("firefox", "chrom", "brave", "vivaldi", "opera", "discord", "steamwebhelper", "obs", "konsole")
_MODS = {0x1: X.Mod1Mask, 0x2: X.ControlMask, 0x4: X.ShiftMask}       # base.py's (Windows) modifiers -> X's
_IGNORED = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)       # Caps Lock / Num Lock on or off
_MOD_MASK = X.ShiftMask | X.ControlMask | X.Mod1Mask


def _text(value):
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def is_game(wm_class, title, pid, own_pid):
    """Could a window with this WM_CLASS (instance, class), title and process be MapleStory's?"""
    if pid == own_pid:
        return False
    names = " ".join(wm_class).lower()
    if any(n in names for n in _NOT_GAME):
        return False
    if "maplestory" in names.replace(" ", ""):                # Wine: ("maplestory.exe", "maplestory.exe")
        return True
    return "maplestory" in title.lower().replace(" ", "") and ("wine" in names or ".exe" in names
                                                               or names.startswith("steam_app"))


def _vk_keysym(vk):
    """base.py's (Windows) virtual key -> X keysym."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return XK.string_to_keysym(chr(vk).lower())
    if 0x70 <= vk <= 0x7B:
        return XK.string_to_keysym(f"F{vk - 0x6F}")
    return 0


def _keysym_vk(keysym):
    name = XK.keysym_to_string(keysym) or ""
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    for n in range(1, 13):
        if keysym == XK.string_to_keysym(f"F{n}"):
            return 0x6F + n
    return 0


class _X:
    """One X connection with its atoms. Xlib connections aren't thread safe: callers hold .lock."""

    def __init__(self):
        self.d = display.Display()
        self.root = self.d.screen().root
        self.lock = threading.RLock()
        a = self.d.intern_atom
        self.CLIENT_LIST, self.ACTIVE = a("_NET_CLIENT_LIST"), a("_NET_ACTIVE_WINDOW")
        self.PID, self.NAME, self.UTF8 = a("_NET_WM_PID"), a("_NET_WM_NAME"), a("UTF8_STRING")
        self.STATE, self.HIDDEN = a("_NET_WM_STATE"), a("_NET_WM_STATE_HIDDEN")

    def window(self, wid):
        return self.d.create_resource_object("window", wid)

    def prop(self, win, atom, kind=X.AnyPropertyType):
        try:
            p = win.get_full_property(atom, kind)
            return p.value if p else None
        except error.XError:
            return None

    def pid(self, win):
        v = self.prop(win, self.PID, X.AnyPropertyType)
        return int(v[0]) if v is not None and len(v) else 0

    def title(self, win):
        v = self.prop(win, self.NAME, self.UTF8)
        if v is None:
            try:
                v = win.get_wm_name()
            except error.XError:
                v = None
        return _text(v)

    def wm_class(self, win):
        try:
            return tuple(win.get_wm_class() or ())
        except error.XError:
            return ()

    def clients(self):
        v = self.prop(self.root, self.CLIENT_LIST)
        return [self.window(w) for w in v] if v is not None else []

    def active(self):
        v = self.prop(self.root, self.ACTIVE)
        return self.window(v[0]) if v is not None and len(v) and v[0] else None


class LinuxHotkeys(Hotkeys):
    """Key grabs on the X root window, on their own thread and their own X connection. They're
    grabbed only while MapleStory or the overlay is the active window, so other programs can
    still use those keys (and XWayland wouldn't see them otherwise anyway)."""

    def __init__(self, on_press, is_front):
        super().__init__(on_press)
        self.is_front = is_front
        self._wanted, self._paused, self._changed = {}, False, True
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, keys, on=True):
        with self._lock:
            self._wanted = {a: parse_hotkey(k) for a, k in keys.items() if on and parse_hotkey(k)}
            self._changed = True

    def pause(self, paused):
        with self._lock:
            self._paused, self._changed = paused, True

    def in_front(self):
        return self.is_front()

    def _grab(self, d, root, wanted):
        active = {}                                           # (keycode, X modifiers) -> action
        for action, (mods, vk) in wanted.items():
            keycode = d.keysym_to_keycode(_vk_keysym(vk))
            xmods = sum(x for w, x in _MODS.items() if mods & w)
            if not keycode:
                self.problems[action] = "no such key on this keyboard"
                continue
            catch = error.CatchError(error.BadAccess)
            for extra in _IGNORED:
                root.grab_key(keycode, xmods | extra, False, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
            d.sync()
            if catch.get_error():
                for extra in _IGNORED:
                    root.ungrab_key(keycode, xmods | extra)
                self.problems[action] = "used by another program"
            else:
                active[(keycode, xmods)] = action
                self.problems.pop(action, None)
        return active

    def _run(self):
        try:
            d = display.Display()
        except Exception as e:
            self.problems["*"] = f"no X display: {e}"
            return
        root = d.screen().root
        active, was_on, released = {}, None, {}               # released: keycode -> time of its last release
        while True:
            readable, _, _ = select.select([d.fileno()], [], [], 0.25)
            while d.pending_events():
                ev = d.next_event()
                if ev.type == X.KeyRelease:
                    released[ev.detail] = ev.time
                elif ev.type == X.KeyPress:
                    if released.get(ev.detail) == ev.time:    # key repeat: a release and press at the same moment
                        continue
                    action = active.get((ev.detail, ev.state & _MOD_MASK))
                    if action:
                        self.on_press(action)
            if readable:
                continue
            try:
                on = self.in_front()
            except Exception:
                on = False
            with self._lock:
                changed, wanted, paused, self._changed = self._changed, dict(self._wanted), self._paused, False
            on = on and not paused
            if not changed and on == was_on:
                continue
            root.ungrab_key(X.AnyKey, X.AnyModifier)
            d.sync()
            active, was_on = (self._grab(d, root, wanted) if on else {}), on


class LinuxPlatform(Platform):
    name = "Linux"

    def __init__(self):
        self._x = None
        self._game = None                                     # the game window's id, for in_front()

    @property
    def x(self):
        if self._x is None:
            self._x = _X()
        return self._x

    def setup_process(self):
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("No X display (DISPLAY isn't set). On Wayland, XWayland must be running.")

    def version(self):
        try:
            info = platform.freedesktop_os_release()
            distro = info.get("PRETTY_NAME") or info.get("NAME", "")
        except OSError:
            distro = ""
        session = os.environ.get("XDG_SESSION_TYPE", "?")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
        return f"Linux {platform.release()} ({distro}; {session}, {desktop})"

    def data_root(self):
        return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")

    def tesseract_candidates(self, home_dir, app_dir):
        on_path = [Path(d) / "tesseract" for d in os.environ.get("PATH", "").split(os.pathsep)
                   if d.strip() and os.path.isabs(d.strip())]
        return [home_dir / "tesseract" / "tesseract", app_dir / "tesseract" / "tesseract",
                Path("/usr/bin/tesseract"), Path("/usr/local/bin/tesseract"), *on_path]

    def tessdata_candidates(self, exe):
        return [exe.parent.parent / "share" / "tessdata", Path("/usr/share/tessdata"),
                Path("/usr/share/tesseract-ocr/5/tessdata"), Path("/usr/share/tesseract-ocr/4.00/tessdata"),
                Path("/usr/local/share/tessdata")]

    def tessdata_arg(self, exe, folder):
        return str(folder)

    def claim_single_instance(self, key, title, old_titles=()):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
        self._lock = open(runtime / f"MapleEXP-{key}.lock", "w")               # unlocked when it exits
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            try:
                subprocess.run(["notify-send", title, f"{title} is already running."], capture_output=True, timeout=5)
            except Exception:
                print(f"{title} is already running.")
            return False

    def find_game_window(self):
        x, own, found = self.x, os.getpid(), []
        with x.lock:
            for w in x.clients():
                try:
                    if is_game(x.wm_class(w), x.title(w), x.pid(w), own):
                        rect = self.client_rect(w.id)
                        if rect:
                            found.append((rect[2] * rect[3], w.id))
                except error.XError:
                    continue
        self._game = max(found)[1] if found else None
        return self._game

    def window_exists(self, wid):
        x = self.x
        with x.lock:
            try:
                x.window(wid).get_attributes()
                return True
            except error.XError:
                return False

    def client_rect(self, wid):
        x = self.x
        with x.lock:
            try:
                w = x.window(wid)
                if w.get_attributes().map_state != X.IsViewable:
                    return None                                                 # minimized (or not shown)
                state = x.prop(w, x.STATE)
                if state is not None and x.HIDDEN in state:
                    return None
                g = w.get_geometry()
                pos = w.translate_coords(x.root, 0, 0)
            except error.XError:
                return None
        if g.width < 100 or g.height < 100:
            return None
        return -pos.x, -pos.y, g.width, g.height

    captures_window_alone = True

    def capture_window(self, wid, left, top, width, height):
        rect = self.client_rect(wid)
        if not rect:
            return None
        dx, dy = left - rect[0], top - rect[1]
        if dx < 0 or dy < 0 or dx + width > rect[2] or dy + height > rect[3]:
            return None
        x = self.x
        with x.lock:
            try:
                img = x.window(wid).get_image(dx, dy, width, height, X.ZPixmap, 0xFFFFFFFF)
            except error.XError:
                return None
        if img.depth not in (24, 32) or len(img.data) < width * height * 4:
            return None
        stride = len(img.data) // height
        rows = np.frombuffer(img.data, np.uint8)[:stride * height].reshape(height, stride)
        out = rows[:, :width * 4].reshape(height, width, 4).copy()                # BGRX, like mss
        out[:, :, 3] = 255
        return out

    def is_front(self):
        """Is MapleStory or the overlay the active window?"""
        x = self.x
        with x.lock:
            w = x.active()
            if w is None:
                return False
            if x.pid(w) == os.getpid() or (self._game and w.id == self._game):
                return True
            try:
                return is_game(x.wm_class(w), x.title(w), x.pid(w), os.getpid())
            except error.XError:
                return False

    def own_window_rect(self, root):
        try:
            root.update_idletasks()
            return root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
        except Exception:
            return None

    def screen_dpi(self, x, y):
        # Plasma and GNOME publish the scaling for X11 programs as Xft.dpi.
        try:
            with self.x.lock:
                rm =self.x.prop(self.x.root, self.x.d.intern_atom("RESOURCE_MANAGER"))
            for line in _text(rm).splitlines():
                if line.startswith("Xft.dpi:"):
                    return int(float(line.split(":", 1)[1]))
        except Exception:
            pass
        return 96

    def key_event(self, e):
        with self.x.lock:
            keysym = self.x.d.keycode_to_keysym(e.keycode, 0)
        return bool(e.state & 0x4), bool(e.state & 0x8), bool(e.state & 0x1), _keysym_vk(keysym)

    def create_hotkeys(self, on_press):
        return LinuxHotkeys(on_press, self.is_front)

    def register_fonts(self, paths):
        # Added to fontconfig for this program only (nothing is installed), before Tk loads any font.
        try:
            import ctypes
            fc = ctypes.CDLL("libfontconfig.so.1")
            fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            return all(fc.FcConfigAppFontAddFile(None, str(p).encode()) for p in paths)
        except Exception:
            return False

    def font(self, family, weight):
        family, bold = {"Segoe UI": ("Noto Sans", None), "Segoe UI Semibold": ("Noto Sans", "bold"),
                        "Consolas": ("DejaVu Sans Mono", None)}.get(family, (family, None))
        return family, bold or weight

    def open_folder(self, path):
        subprocess.Popen(["xdg-open", str(path)])

    def show_file(self, path):
        try:
            subprocess.Popen(["dbus-send", "--session", "--dest=org.freedesktop.FileManager1", "--type=method_call",
                              "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                              f"array:string:{Path(path).resolve().as_uri()}", "string:"])
        except Exception:
            self.open_folder(Path(path).parent)
