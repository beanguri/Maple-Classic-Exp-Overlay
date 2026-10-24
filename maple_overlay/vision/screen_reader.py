"""Looks at the game window: the EXP bar, map, mesos, party and potions, every time the screen is read."""
import time
from collections import deque, namedtuple

import cv2

from maple_overlay.config import (
    ASSETS, LEVEL_FAILS_FOR_FALLBACK, LEVEL_RECHECK_SECONDS, log_error, MAP_HIDDEN_SECONDS, OUTLINE_SECONDS,
    PLATFORM, POTION_FULL_LOOK, POTION_SECONDS, SCAN_SECONDS, SEARCH_STRIP, WINDOW_GRACE, WINDOW_NAMES,
    WINDOW_SCAN_SECONDS,
)
from maple_overlay.core.levels import fits_exp_table
from maple_overlay.core.meso_tracker import _agrees
from maple_overlay.vision import engine, exp_messages, level_ocr, potions
from maple_overlay.vision.exp_bar import _pct_cells, find_exp, read_layout, tesseract_candidates
from maple_overlay.vision.job import read_job
from maple_overlay.vision.maps import find_map_box, read_map_name
from maple_overlay.vision.mesos import (
    find_mesos_fields, find_money_window, find_trade_mesos_box, mesos_box_lines_up, mesos_layout, MesosBoxFinder,
    MesosDigits, read_mesos_digits, read_mesos_positions, tesseract_mesos,
)
from maple_overlay.vision.party import find_party_bars
from maple_overlay.vision.screen import grab
from maple_overlay.vision.text import _whiteness


# --------------------------------------------------------------------------- finding it on screen
Reading = namedtuple("Reading", "where candidates confident text image boxes map note "
                                 "mesos mesos_image mesos_boxes party mesos_at window bar percent_only level",
                     defaults=((), False, "", None, (), None, None, None, None, (), None, None, None, None, False, None))

# percent_only: the bar shows just "[58.47%]" (candidates are then [(None, pct)]); level: the level read
# from the status bar's LV badge, with where it came from, as (level, source), or None
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
        self.saved_mesos = None     # (window w, h, x, y, w, h) remembered from last time
        self.box_finder = MesosBoxFinder()
        self.mesos_digits = MesosDigits()
        self.mesos_checked = None   # last mesos number Tesseract confirmed
        self.mesos_count = None     # how many digits the mesos number has
        self.mesos_pending = (None, 0)
        self.mesos_fails = 0
        self.next_mesos_check = 0.0
        self.mesos_covered = False  # this app's window is in the way of the mesos
        self.two_boxes = False      # a storage / trade window shows a second mesos box
        self.window = None          # "shop", "storage" or "trade" while one is open (or just closed)
        self.window_until = 0.0     # ...and until when it still counts
        self.window_box = False     # the mesos box being read belongs to such a window (not the inventory)
        self.potion_table = self.potion_digits = None    # loaded at the first look at the potion bar
        self.next_bar, self.bar_reads, self.bar_shift = 0.0, 0, (0, 0)
        self.next_outline = 0.0     # the earliest time to search for a see-through mesos box again
        self.party_area = None      # where the party list was last seen, on screen
        self.next_scan = 0.0
        self.msg_rect = None                    # the game window, for the message reader (its own thread, see App._message_worker)
        self.msg_queue, self._msg_tracker = deque(), exp_messages.LineTracker()     # new message lines [(kind, value)] waiting for the App
        self.sample_dir, self.sample_at, self.samples_saved = None, 0.0, 0     # (collecting EXP message pictures, see collect_samples)
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
        self.next_level_read = 0.0
        self._level_candidate = None    # a level seen once, waiting for a second capture to agree
        self.name_print = None      # a small picture of the character's name row (see level_ocr.name_print); never read as text
        self.next_name_read = 0.0

    def use_window(self):
        self.mode, self.hwnd, self.box = "window", None, None

    def use_region(self, region):
        self.mode, self.region = "region", region

    def request_map(self):
        """Read the map name on the next look, even if it doesn't seem to have changed."""
        self.next_map_search, self.next_map_read, self.map_unread = 0.0, 0.0, True

    def look_again(self):
        """Forget where everything was (EXP bar, minimap, inventory, party list) and look
        for it all again. Done by the reading thread itself, at its next look."""
        self.look_again_requested = True

    def _forget_places(self):
        self.box, self.misses = None, 0
        self.map_box, self.map_print = None, None
        self.mesos_box = self.saved_mesos = None
        self.mesos_fails, self.mesos_count, self.mesos_pending = 0, None, (None, 0)
        self.mesos_covered, self.party_area, self.next_scan = False, None, 0.0
        self.two_boxes, self.next_outline = False, 0.0
        self.window, self.window_until, self.window_box = None, 0.0, False
        self.exp_anchor, self.next_job_read = None, 0.0
        self.next_level_read, self._level_candidate = 0.0, None
        self.request_map()

    def read(self, sct):
        if self.look_again_requested:
            self.look_again_requested = False
            self._forget_places()
        rect = self._game_rect()
        self.last_rect = rect
        if self.mode == "region":
            reading = self._read_region(sct, rect)
        elif not self.hwnd and PLATFORM.missing_permission():
            return Reading(PLATFORM.missing_permission())
        elif not self.hwnd:
            return Reading("MapleStory window not found. Is the game running?")
        elif not rect:
            return Reading("MapleStory is minimized. Bring the game back up.")
        else:
            reading = self._read_window(sct, rect)
        if reading.percent_only and self.mode == "region":
            # (a selected area has no game window around it: no badge to find, so typing the level in is open at once)
            self.level_failures = max(self.level_failures, LEVEL_FAILS_FOR_FALLBACK)
            self.level_look = {"image": None, "votes": {}, "why": "Selected area: the level badge isn't in it"}
        if rect and self.gh:
            name, note = self._read_info(sct, rect)
            reading = self._read_extras(sct, rect, reading._replace(map=name, note=note))
            if self.exp_anchor and self.mode == "window":
                self._read_job(sct, rect)
                self._look_at_name(sct, rect)
                if reading.percent_only:        # no EXP number to tell the level from: the badge has to
                    self._read_level(sct, rect)
        return reading._replace(level=(self.level, "badge") if self.level and reading.percent_only and self.mode == "window" else None)

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
        return dict(mode=self.mode, found=bool(self.hwnd), rect=rect, gh=g, region=self.region,
                    own_rect=self.own_rect, covering=covering, exp_box=self.box, exp_misses=self.misses,
                    map_box=self.map_box, map_unread=self.map_unread, mesos_box=self.mesos_box,
                    mesos_fails=self.mesos_fails, two_boxes=self.two_boxes, mesos_learned=self.mesos_digits.complete,
                    party_area=self.party_area, job_why=(self.job_look or {}).get("why") or "")

    def _read_job(self, sct, rect):
        """Reads the class from the status bar now and then (it only changes at a job advance)."""
        now = time.monotonic()
        if not engine.tesseract_ok or now < self.next_job_read:
            return
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 65 * g), wy + int(ay - 3 * g), int(46 * g), int(6.5 * g)
        if self._covered(x0, y0, w, h):
            self.next_job_read = now + 5
            self.job_look = {"image": None, "boxes": [], "why": "this window is covering the class name: move it"}
            return
        shot = grab(sct, x0, y0, w, h)
        look = {}
        job = read_job(shot[0], wx + ax - shot[1], wy + ay - shot[2], g, look) if shot else None
        self.job_look = look or None
        if job:
            self.job = job
        self.next_job_read = now + (60 if job else 10)

    def _look_at_name(self, sct, rect):
        """Now and then, a print of the name row: how another character is told from this one (the level
        read for one isn't another's). It is only a picture, kept in memory: the name isn't read."""
        now = time.monotonic()
        if now < self.next_name_read:
            return
        self.next_name_read = now + 5
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 50.5 * g) - 2, wy + int(ay + 0.9 * g) - 2, int(12.5 * g) + 5, int(1.7 * g) + 5
        if self._covered(x0, y0, w, h):
            self.name_print = None
            return
        shot = grab(sct, x0, y0, w, h)
        self.name_print = level_ocr.name_print(shot[0], wx + ax - shot[1], wy + ay - shot[2], g) if shot else None

    def recheck_level(self):
        """Read the badge at the next look (the % wrapped: the level may have gone up). The level known so
        far stays until a new one has been seen twice."""
        self.next_level_read, self._level_candidate = 0.0, None

    def _read_level(self, sct, rect):
        """Reads the level from the badge: a level counts once two looks in a row agree. A failing read
        backs off (so a covered or unreadable badge doesn't keep starting Tesseract); a known level is
        looked at again only now and then."""
        now = time.monotonic()
        if now < self.next_level_read:
            return
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 72 * g), wy + int(ay - 1.6 * g), int(34 * g) + 1, int(5.2 * g) + 1
        if not engine.tesseract_ok or engine.TESS is None:      # nothing to wait for: typing the level in is open at once
            self.level_failures = max(self.level_failures, LEVEL_FAILS_FOR_FALLBACK)
            self.level_look = {"image": None, "votes": {}, "why": "Tesseract isn't available"}
            self.next_level_read = now + 60
            return
        if self._covered(x0, y0, w, h):
            self.level_look = {"image": None, "votes": {}, "why": "this window is covering the level badge: move it"}
            self.next_level_read = now + 5
            return
        shot = grab(sct, x0, y0, w, h)
        look = {}
        try:
            seen = level_ocr.read_level(shot[0], wx + ax - shot[1], wy + ay - shot[2], g, engine.TESS, look) if shot else None
        except Exception as e:              # (Tesseract failing or timing out, or anything unexpected: not a level, back off)
            look["why"] = f"the level read failed: {e}"
            seen = None
        self.level_look = look
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

    def _game_rect(self):
        if not (self.hwnd and PLATFORM.window_exists(self.hwnd)):
            self.hwnd, self.box, self.map_box = PLATFORM.find_game_window(), None, None
        rect = PLATFORM.client_rect(self.hwnd) if self.hwnd else None
        if rect and rect[2:] != self.size:                  # resized: find everything again
            self.size, self.box, self.map_box, self.gh = rect[2:], None, None, None
            self.exp_anchor, self.next_job_read = None, 0.0
            self.mesos_box, self.party_area, self.next_scan = None, None, 0.0
            self.window, self.window_until, self.window_box = None, 0.0, False
            saved = self.saved_mesos
            if saved and tuple(saved[:2]) == tuple(rect[2:]):
                self.mesos_box = tuple(saved[2:])           # where it was last time
        return rect

    def _hide_own(self, shot):
        """The screenshot with this app's window blacked out, so it can never read itself."""
        r = self.own_rect
        if not shot or not r:
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

    def _remember(self, lay, rect, shot=None):
        if lay and rect:
            self.gh = lay.gh                # tells the minimap reader how big the UI is
            if shot:                        # where the EXP number starts: the class is left of it
                self.exp_anchor = (shot[1] - rect[0] + lay.box[0], shot[2] - rect[1] + lay.cy)

    def _read_region(self, sct, rect):
        r = self.region
        if not r:
            return Reading("No area selected. Click Select area.")
        shot = self._hide_own(grab(sct, r["left"], r["top"], r["width"], r["height"]))
        reading, lay = self._read(shot, "Selected area")
        if reading.confident:
            self._remember(lay, rect)
        return reading

    def _read_window(self, sct, rect):
        wx, wy, ww, wh = rect
        where = f"MapleStory window {ww}x{wh}"
        if self.box:
            bx, by, bw, bh = self.box
            if self._covered(wx + bx, wy + by, bw, bh):
                return Reading("This window is covering the EXP bar. Move it.")
            shot = grab(sct, wx + bx, wy + by, bw, bh)
            reading, lay = self._read(shot, where)
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
        shot = self._hide_own(grab(sct, wx, wy + top, ww, wh - top))
        lay = find_exp(shot[0]) if shot else None
        if not lay and time.monotonic() - self.last_full_search > 10:
            self.last_full_search = time.monotonic()
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            lay = find_exp(shot[0]) if shot else None
        if not lay:
            if self._covered(wx, wy + top, ww, wh - top):
                return Reading("Can't find the EXP bar. Is this window covering it?")
            return Reading("Can't find the EXP bar at the bottom of the game. Is something covering it?")
        img, sx, sy = shot
        g = lay.gh
        lead = g if lay.exp else 5.5 * g       # (a %-only bar is found by its label, so the label stays in the spot)
        x0, x1 = max(0, int(lay.box[0] - lead)), min(img.shape[1], int(lay.box[2] + 6 * g))
        y0, y1 = max(0, int(lay.cy - 1.4 * g)), min(img.shape[0], int(lay.cy + 1.4 * g))
        reading, lay = self._read(shot, where, lay)
        if reading.confident:             # only remember a spot whose digits match the game font
            self.box = (sx - wx + x0, sy - wy + y0, x1 - x0, y1 - y0)
            self.misses = 0
            self._remember(lay, rect, shot)
        return reading

    def _read(self, shot, where, lay=None):
        """Returns (Reading, Layout or None)."""
        if not shot:
            return Reading(_problem(where, "The EXP bar is off screen.")), None
        img = shot[0]
        lay = lay or find_exp(img)
        if not lay:
            return Reading(_problem(where, "Can't find the EXP bar.")), None
        view, boxes = _view(img, lay)
        got = read_layout(self.templates, lay) if self.templates else None
        if not lay.exp:                       # only "[58.47%]": the level has to come from elsewhere
            if got:
                return Reading(where, [got], True, f"[{got[1]:.2f}%] (percentage only)", view, boxes,
                               percent_only=True), lay
            return Reading(_problem(where, "Found the EXP bar, but can't read its percentage clearly."),
                           image=view, boxes=boxes, percent_only=True), lay
        if got and fits_exp_table(*got):
            return Reading(where, [got], True, f"{got[0]:,} [{got[1]:.2f}%]", view, boxes), lay
        # Unclear digits, or a reading that fits no level: not trusted. Readings that do fit
        # a level go first, so the tracker tries those.
        cands = ([got] if got else []) + [c for c in tesseract_candidates(img, lay) if c != got]
        cands.sort(key=lambda c: not fits_exp_table(*c))
        if cands:
            e, p = cands[0]
            how = "" if (e, p) == got else " (Tesseract)"
            return Reading(where, cands, False, f"{e:,} [{p:.2f}%]{how}", view, boxes), lay
        return Reading(_problem(where, "Found the EXP bar, but can't read its numbers clearly."),
                       image=view, boxes=boxes), lay

    def _read_extras(self, sct, rect, reading):
        """Adds the mesos count and, every few seconds, the party size to a reading."""
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
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
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
                        self.mesos_box, self.mesos_fails = box, 0
                        if not window:                          # a window's own box isn't remembered
                            moved = (ww, wh) + box
                    self.window_box = bool(window)
                area = self.party_area
                if area and self._covered(*area):
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
        mesos, image, boxes = None, None, ()
        self.mesos_covered = False
        if self.mesos_box:
            bx, by, bw, bh = self.mesos_box
            # (the digits only need the box and a sliver around it to be seen)
            if self._covered(wx + bx, wy + by, bw, bh, margin=max(2, int(0.3 * bh))):
                notes.append("This window is covering the inventory's mesos.")
                self.mesos_covered = True
            else:
                mesos, image, boxes = self._read_mesos(sct, wx + bx, wy + by, bw, bh)
        window = self.window if now <= self.window_until else None
        if window:
            notes.append(f"{WINDOW_NAMES[window]} window open.")
        bar = self._read_potion_bar(sct, rect, notes) if now >= self.next_bar else None
        self.msg_rect = rect
        if self.sample_dir:
            self._save_message_sample(sct, rect, now)
        return reading._replace(mesos=mesos, mesos_image=image, mesos_boxes=boxes, party=party,
                                mesos_at=moved, window=window, bar=bar, note=" ".join(notes) or None)

    def read_new_messages(self, sct):
        """One look at the message area from the message thread: the lines that appeared since the last look are
        added to msg_queue as (kind, value)."""
        rect = self.msg_rect
        if not rect:
            return
        cur = self._read_messages(sct, rect)
        if cur is None:
            return
        self.msg_queue.extend(self._msg_tracker.update(cur, time.monotonic()))

    def _read_messages(self, sct, rect):
        """The "You received EXP (+N)" / "Bonus EXP for PARTY (+N)" lines now on screen, oldest first, as
        [(kind, value, y)], or None if the screen can't be read. Read from the pixels directly (exp_messages)."""
        wx, wy, ww, wh = rect
        try:
            shot = grab(sct, wx + int(0.55 * ww), wy + int(0.35 * wh), int(0.45 * ww), int(0.55 * wh))
            return exp_messages.read_messages(shot[0]) if shot else None
        except Exception as e:
            log_error("reading the EXP messages", e)
            self.msg_rect = None                                # (not retried: the split is only an extra)
            return None

    def _save_message_sample(self, sct, rect, now):
        """While collecting: saves a picture of the game's message area (bottom right) whenever it holds yellow
        lines (EXP messages), at most one every 2 seconds and 300 in all. The pictures are the digits' examples
        for reading "You received EXP (+N)" / "Bonus EXP for PARTY (+N)" exactly."""
        if now - self.sample_at < 2 or self.samples_saved >= 300:
            return
        wx, wy, ww, wh = rect
        shot = grab(sct, wx + int(0.55 * ww), wy + int(0.35 * wh), int(0.45 * ww), int(0.55 * wh))
        if not shot or not exp_messages.find_lines(exp_messages.yellow_mask(shot[0])):
            return
        self.sample_at = now
        try:
            self.sample_dir.mkdir(parents=True, exist_ok=True)
            self.samples_saved += 1
            cv2.imwrite(str(self.sample_dir / f"msg_{time.strftime('%H%M%S')}_{self.samples_saved:03d}.png"), shot[0])
        except Exception as e:
            log_error("saving an EXP message sample", e)
            self.sample_dir = None

    def _read_potion_bar(self, sct, rect, notes):
        """The potions and food on the potion bar as a tuple of potions.Slot (empty: none, or it
        can't be seen), or None when there is nothing to report. Read every few seconds."""
        self.next_bar = time.monotonic() + POTION_SECONDS
        wx, wy, ww, wh = rect
        if self.potion_table is None:
            try:
                self.potion_table = potions.PotionTable.load(ASSETS / "potion_table.json")
                self.potion_digits = potions.load_digits(ASSETS / "potion_digits.npz")
            except Exception as e:
                log_error("loading the potion table", e)
                self.next_bar = float("inf")                    # without it there's nothing to read
                return None
        bx, by, bw, bh = potions.bar_crop_box(ww, wh)
        if self._covered(wx + bx, wy + by, bw, bh):
            notes.append("This window is covering the potion bar.")
            return ()
        shot = grab(sct, wx + bx, wy + by, bw, bh)
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

    def _read_mesos(self, sct, x, y, w, h):
        """Reads the mesos box at (x, y, w, h) on screen. Returns (number, image, boxes)."""
        shot = grab(sct, x - 2 * h, y - h, w + 4 * h, 3 * h)
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

    def _read_info(self, sct, rect):
        """Returns (map name if it was just read, note)."""
        if not engine.tesseract_ok:
            return None, "Map names can't be read: Tesseract OCR is missing (see the README)."
        if self.own_rect is None:
            return None, None                   # wait until we know where this app's window is
        wx, wy, ww, wh = rect
        g, now = self.gh, time.monotonic()
        name = note = None

        hidden = self.map_blind_seconds() > MAP_HIDDEN_SECONDS
        if self.map_box is None and now >= self.next_map_search:
            self.next_map_search = now + (1 if hidden else 3)     # looks harder for a name that has gone missing
            shot = self._hide_own(grab(sct, wx, wy, int(ww * 0.4), int(wh * 0.3)))
            box = find_map_box(shot[0], g) if shot else None
            if box:
                self.map_box = (shot[1] - wx + box[0], shot[2] - wy + box[1], box[2], box[3])
                self.map_print, self.map_unread = None, True
        if self.map_box is None:
            if self._covered(wx, wy, int(ww * 0.4), int(wh * 0.3)):
                note = "This window may be covering the minimap. Move it so the map name can be read."
            else:
                note = "Can't see the map name. Is the minimap open?"
        else:
            bx, by, bw, bh = self.map_box
            if self._covered(wx + bx, wy + by, bw, bh, margin=max(3, int(0.1 * g))):
                # Even a box that only touches this window may be cut off by it (it was
                # found with this window blacked out). Find the whole box again (and read
                # the name) once this window is out of the way.
                note = "This window is covering the minimap. Move it so the map name can be read."
                self.map_box, self.map_unread = None, True
            else:
                shot = grab(sct, wx + bx, wy + by, bw, bh)
                if shot:
                    self.map_seen_at = now
                    picture = cv2.resize(_whiteness(shot[0]), (64, 16), interpolation=cv2.INTER_AREA) > 0.3
                    if self.map_print is not None and (picture != self.map_print).mean() > 0.04:
                        # Changed map: find the box again (its size can change), then read it.
                        self.map_box, self.next_map_search = None, now + 1
                        self.map_unread, self.next_map_read = True, 0.0
                    elif self.map_unread and now >= self.next_map_read:
                        name = read_map_name(shot[0], g)
                        self.map_look = {"image": shot[0], "text": name}
                        self.map_unread = name is None
                        self.next_map_read = now + (2 if hidden else 5)   # if unreadable, try again later
                    self.map_print = picture
        return name, note
