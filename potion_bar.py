"""Reader v2 for the potion bar: finds the bar in any layout (keyboard or controller UI) at any UI size, and
says what each slot holds and how many are left.

These are the shapes every part of it hands around (agreed in the v2 plan before any of it was built):
the finder makes a BarLook, read_potions() turns it into a PotionLook, the tracker counts uses from those.
Rects are (x, y, w, h) in the game window's own pixels.
"""
import math
from collections import namedtuple
from pathlib import Path

import cv2
import numpy as np

import glyphs
import ui_layout

LAYOUTS = ("keyboard", "controller-standard", "controller-cross")    # "D-pad Slots": Standard / Cross-Style
STYLES = ("keyboard", "controller")      # how a slot is drawn (key-label plate and light inside, or dark rounded)
KINDS = ("consumable", "other", "skill", "empty")                    # what a slot holds, when it can be told

# One slot where the finder put it. index is stable within a layout (keyboard: row * 4 + col).
SlotSpot = namedtuple("SlotSpot", "index rect style")

# Where the bar is: layout (one of LAYOUTS), mode ("default" / "ideal" / None when both draw the same),
# scale (screen pixels per design pixel), origin (the bar's top left in the window), slots (SlotSpot, ...).
BarLook = namedtuple("BarLook", "layout mode scale origin slots")

# One slot read: item is the item's name or None when it can't be told; kind one of KINDS or None;
# count the number shown or None when it isn't certain (0 is a real count).
SlotRead = namedtuple("SlotRead", "index rect style item kind count run", defaults=(None,))   # run: the count reader's Run, kept to pass back

# What a look at the potion bar saw. seen False: no bar to read (covered, hidden, off-screen, no window);
# seen True with no consumable slots: the bar is there and holds no potions.
PotionLook = namedtuple("PotionLook", "seen layout mode scale slots")

# What read_potions may start from: last (the BarLook of the last good look, tried first) and mode_scale
# ((mode, scale) from the EXP bar's digit height, see ui_layout.mode_from_gh), either may be None.
Hint = namedtuple("Hint", "last mode_scale runs", defaults=(None,))   # runs: {slot index: Run} of the last look

NOT_SEEN = PotionLook(False, None, None, None, ())


def is_controller(layout):
    return bool(layout) and layout.startswith("controller")


# ---------------------------------------------------------------- the keyboard bar
# Slot grid in design pixels from the window's bottom centre (T04 fits: same in both UI modes).
KB_X0, KB_Y0, KB_PX, KB_PY, KB_W, KB_H = 257.0, -172.6, 34.93, 34.13, 32.2, 30.9
ROW_MIN = 22.0              # a row of slots counts as there when its slots' edges stand out this much (grey levels)
BAR_MIN = 30.0              # ...and the bottom two rows together this much
HINT_SHIFT = 1              # the last good spot is tried this far around

_assets = {}


def load_assets(folder=None):
    """The potion table (item fingerprints), loaded once. folder: the assets folder (default: next to this file)."""
    import potions
    key = str(folder)
    if key not in _assets:
        base = Path(folder) if folder else Path(__file__).resolve().parent / "assets"
        _assets[key] = potions.PotionTable.load(base / "potion_table.json")
    return _assets[key]


def _ring(ii, x, y, w, h):
    """How much brighter the inside of a slot's edge is than the outside of it (grey levels), or None off-picture."""
    t = max(1, int(round(min(w, h) / 14)))
    ih, iw = ii.shape[0] - 1, ii.shape[1] - 1
    if x - t < 0 or y - t < 0 or x + w + t > iw or y + h + t > ih:
        return None

    def box(x0, y0, x1, y1):
        return ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
    inner = (box(x, y + t, x + w, y + 2 * t) + box(x, y + h - 2 * t, x + w, y + h - t)
             + box(x + t, y, x + 2 * t, y + h) + box(x + w - 2 * t, y, x + w - t, y + h))
    inner_n = 2 * w * t + 2 * h * t
    outer = (box(x - t, y - t, x + w + t, y) + box(x - t, y + h, x + w + t, y + h + t)
             + box(x - t, y, x, y + h) + box(x + w, y, x + w + t, y + h))
    outer_n = 2 * (w + 2 * t) * t + 2 * h * t
    return float(inner / inner_n - outer / outer_n)


def keyboard_rects(window_w, window_h, scale, shift=(0, 0)):
    """{index: (x, y, w, h)} of the 12 slots of the keyboard bar in window pixels, shifted by whole pixels."""
    out = {}
    w, h = int(round(KB_W * scale)), int(round(KB_H * scale))
    for row in range(3):
        for col in range(4):
            x = window_w / 2 + (KB_X0 + KB_PX * col) * scale
            y = window_h + (KB_Y0 + KB_PY * row) * scale
            out[row * 4 + col] = (int(round(x)) + shift[0], int(round(y)) + shift[1], w, h)
    return out


def _bar_box(window_w, window_h, scale, margin):
    """(x0, y0, x1, y1) of the window area the keyboard bar covers, with a margin."""
    x0 = window_w / 2 + (KB_X0 - 1) * scale - margin
    x1 = window_w / 2 + (KB_X0 + KB_PX * 3 + KB_W + 1) * scale + margin
    y0 = window_h + (KB_Y0 - 1) * scale - margin
    return int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(window_h)


def _fit(gray, at, window_w, window_h, scale, shifts):
    """Best (score, shift, row scores) of the keyboard grid at this scale over the whole-pixel shifts; None if none fits."""
    margin = max(abs(s) for sh in shifts for s in sh) + int(math.ceil(scale)) + 2
    bx0, by0, bx1, by1 = _bar_box(window_w, window_h, scale, margin)
    bx0, by0 = max(bx0, at[0], 0), max(by0, at[1], 0)
    bx1, by1 = min(bx1, at[0] + gray.shape[1]), min(by1, at[1] + gray.shape[0])
    if bx1 - bx0 < 40 or by1 - by0 < 40:
        return None
    ii = cv2.integral(gray[by0 - at[1]:by1 - at[1], bx0 - at[0]:bx1 - at[0]]).astype(np.float64)
    best = None
    for shift in shifts:
        rects = keyboard_rects(window_w, window_h, scale, shift)
        rows = []
        for row in range(3):
            v = [_ring(ii, rects[row * 4 + c][0] - bx0, rects[row * 4 + c][1] - by0, rects[row * 4 + c][2],
                       rects[row * 4 + c][3]) for c in range(4)]
            rows.append(None if any(r is None for r in v) else float(np.mean(v)))
        if rows[1] is None or rows[2] is None:
            continue
        score = (rows[1] + rows[2]) / 2
        if best is None or score > best[0]:
            best = (score, shift, rows)
    return best


def _accepted(fit):
    return fit is not None and fit[0] >= BAR_MIN and min(fit[2][1], fit[2][2]) >= ROW_MIN


def find_keyboard_bar(gray, at, window_w, window_h, hint=None):
    """The keyboard bar as a BarLook, or None. Tries the last good spot first, then every candidate scale."""
    last = hint.last if hint is not None and hint.last is not None and hint.last.layout == "keyboard" else None
    if last is not None:
        nominal = keyboard_rects(window_w, window_h, last.scale)[0]
        sx, sy = last.origin[0] - nominal[0], last.origin[1] - nominal[1]
        shifts = [(sx + dx, sy + dy) for dy in range(-HINT_SHIFT, HINT_SHIFT + 1) for dx in range(-HINT_SHIFT, HINT_SHIFT + 1)]
        fit = _fit(gray, at, window_w, window_h, last.scale, shifts)
        if _accepted(fit):
            return _look(fit, last.mode, last.scale, window_w, window_h)
    pairs = ui_layout.candidate_scales(window_h)
    if hint is not None and hint.mode_scale is not None and hint.mode_scale[1]:
        pairs = [hint.mode_scale] + [p for p in pairs if abs(p[1] - hint.mode_scale[1]) > 0.01 * p[1]]
    best = None
    for mode, scale in pairs:
        r = 2 + int(scale > 2.5)
        fit = _fit(gray, at, window_w, window_h, scale, [(dx, dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1)])
        if _accepted(fit) and (best is None or fit[0] > best[0][0]):
            best = (fit, mode, scale)
    return _look(best[0], best[1], best[2], window_w, window_h) if best else None


def _look(fit, mode, scale, window_w, window_h):
    shift, rows = fit[1], fit[2]
    rects = keyboard_rects(window_w, window_h, scale, shift)
    first = 0 if rows[0] is not None and rows[0] >= ROW_MIN else 4
    slots = tuple(SlotSpot(i, rects[i], "keyboard") for i in range(first, 12))
    return BarLook("keyboard", mode, scale, rects[0][:2], slots)


# ---------------------------------------------------------------- reading the slots
SKILL_MAX = 0.20           # a slot is called a skill icon only this close to one seen on the bar
COUNT_ICON_MAX = 0.40       # a count is only taken from a slot whose icon is this clearly the item: a cursor or popup over the
                            # slot spoils the icon too, and the digits it hides can't be told from a count that ends


def _slot_item(table, crop_bgr):
    """(item, kind, distance) of one slot picture: kind "empty" for a bare plate, item None when it can't be told."""
    import potions
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    vec = potions.fingerprint(rgb)
    if vec is None:
        return (None, "empty", 1.0) if potions.is_item_slot(rgb) else (None, None, 1.0)
    found = table.identify(vec)
    if found is None:
        return None, None, 1.0
    name, kind, dist = found
    if kind == "skill":                  # (a damaged picture of an item can land here too: only a near-exact match says "skill")
        return (None, "skill", dist) if dist <= SKILL_MAX else (None, None, 1.0)
    return table.aliases.get(name, name), ("consumable" if kind == "consumable" else "other"), dist


def read_potions(img, window_w, window_h, at=(0, 0), hint=None, assets=None):
    """What the potion bar shows, as a PotionLook.

    img is a picture (BGR) of the game window or of part of it, whose top left is at `at` in the window.
    hint = Hint(last, mode_scale, runs): the last good BarLook is tried first, the EXP bar's mode and scale next,
    runs the count readers' Runs of the last look. Keyboard layout only so far (controller: ticket 08)."""
    if img is None or not getattr(img, "size", 0):
        return NOT_SEEN
    table = assets or load_assets()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bar = find_keyboard_bar(gray, at, window_w, window_h, hint)
    if bar is None:
        return NOT_SEEN
    runs = (hint.runs if hint is not None and hint.runs else None) or {}
    reads = []
    for spot in bar.slots:
        x, y, w, h = spot.rect
        cx, cy = x - at[0], y - at[1]
        crop = img[cy:cy + h, cx:cx + w]
        if crop.shape[0] != h or crop.shape[1] != w:
            continue
        item, kind, dist = _slot_item(table, crop)
        count, run = None, None
        if kind == "consumable":
            count, run = glyphs.read_count(img, (cx, cy, w, h), bar.scale, spot.style, runs.get(spot.index))
            if count is not None and count < 1000 and dist > COUNT_ICON_MAX:
                count, run = None, None      # (a 4-digit count can't be a cut-off longer one, and its digits spill into the icon)
        reads.append(SlotRead(spot.index, spot.rect, spot.style, item, kind, count, run))
    return PotionLook(True, bar.layout, bar.mode, bar.scale, tuple(reads))


def grab_box(window_w, window_h, last=None, margin=8):
    """(x, y, w, h) of the part of the window to take a picture of for the bar: around the last good spot, else
    around every spot a candidate scale could put it."""
    scales = [last.scale] if last is not None else [s for _, s in ui_layout.candidate_scales(window_h)]
    boxes = [_bar_box(window_w, window_h, s, margin) for s in scales]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    return max(x0, 0), max(y0, 0), x1 - max(x0, 0), window_h - max(y0, 0)


def bar_from_look(look, window_w, window_h):
    """The BarLook a PotionLook came from (to hand back as Hint.last), or None when no bar was seen."""
    if not look.seen or look.layout != "keyboard" or not look.slots:
        return None
    first = look.slots[0]
    nominal = keyboard_rects(window_w, window_h, look.scale)
    shift = (first.rect[0] - nominal[first.index][0], first.rect[1] - nominal[first.index][1])
    rects = keyboard_rects(window_w, window_h, look.scale, shift)
    return BarLook(look.layout, look.mode, look.scale, rects[0][:2],
                   tuple(SlotSpot(s.index, s.rect, s.style) for s in look.slots))
