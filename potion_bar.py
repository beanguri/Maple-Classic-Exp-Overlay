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


# ---------------------------------------------------------------- where the bars are
# Slot grids in design pixels from the window's bottom centre (T04 fits; the same in both UI modes, and both follow the UI size).
# The keyboard bar (also "Always Standard" with a controller): 4 columns, 3 rows (2-row bar = rows 1-2, indexes 4-11).
KB_X0, KB_Y0, KB_PX, KB_PY, KB_W, KB_H = 257.0, -172.6, 34.93, 34.13, 32.2, 30.9
# The controller's Cross-Style cluster: 8 cells in 6 columns, columns 1 and 4 hold a top and a bottom cell (T04 fits).
# 5 and 7 are the ATTACK and JUMP buttons.
CTL_X0, CTL_Y0, CTL_PX, CTL_DY, CTL_W, CTL_H = 194.6, -117.2, 34.4, 16.4, 31.8, 29.9
CTL_CELLS = ((0, 0, 0), (1, 1, -1), (2, 1, 1), (3, 2, 0), (4, 3, 0), (5, 4, -1), (6, 4, 1), (7, 5, 0))   # index, column, row step
ROW_MIN = 22.0              # a row of slots counts as there when its slots' edges stand out this much (grey levels)
BAR_MIN = 30.0              # ...and the bottom two rows together this much
CTL_BAR_MIN = 60.0          # the cluster: its best CTL_CELLS_MIN cells' edges stand out this much on average (real ones 112+; a dark frame around light cells)
CTL_CELL_MIN = 40.0         # ...and the weakest of those this much (real ones 73+)
CTL_CELLS_MIN = 6           # a popup or the mouse may cover a cell or two of the 8: the rest still show where the cluster is
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


def _rings(ii, x, y, w, h):
    """How much brighter the inside of each slot's edge is than the outside of it (grey levels): x and y are arrays
    of slot corners in the integral picture ii (w, h the same for all); nan where the ring leaves the picture."""
    t = max(1, int(round(min(w, h) / 14)))
    ih, iw = ii.shape[0] - 1, ii.shape[1] - 1
    ok = (x - t >= 0) & (y - t >= 0) & (x + w + t <= iw) & (y + h + t <= ih)
    x = np.clip(x, t, max(t, iw - w - t))
    y = np.clip(y, t, max(t, ih - h - t))

    def box(x0, y0, x1, y1):
        return ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
    inner = (box(x, y + t, x + w, y + 2 * t) + box(x, y + h - 2 * t, x + w, y + h - t)
             + box(x + t, y, x + 2 * t, y + h) + box(x + w - 2 * t, y, x + w - t, y + h))
    inner_n = 2 * w * t + 2 * h * t
    outer = (box(x - t, y - t, x + w + t, y) + box(x - t, y + h, x + w + t, y + h + t)
             + box(x - t, y, x, y + h) + box(x + w, y, x + w + t, y + h))
    outer_n = 2 * (w + 2 * t) * t + 2 * h * t
    return np.where(ok, inner / inner_n - outer / outer_n, np.nan)


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


def controller_rects(window_w, window_h, scale, shift=(0, 0)):
    """{index: (x, y, w, h)} of the 8 cells of the Cross-Style cluster in window pixels, shifted by whole pixels."""
    out = {}
    w, h = int(round(CTL_W * scale)), int(round(CTL_H * scale))
    for index, col, row in CTL_CELLS:
        x = window_w / 2 + (CTL_X0 + CTL_PX * col) * scale
        y = window_h + (CTL_Y0 + CTL_DY * row) * scale
        out[index] = (int(round(x)) + shift[0], int(round(y)) + shift[1], w, h)
    return out


def bar_rects(layout, window_w, window_h, scale, shift=(0, 0)):
    """The slot rectangles of a layout: keyboard_rects or controller_rects."""
    return (controller_rects if is_controller(layout) else keyboard_rects)(window_w, window_h, scale, shift)


def _bar_box(window_w, window_h, scale, margin, layout="keyboard"):
    """(x0, y0, x1, y1) of the window area a layout's bar covers, with a margin."""
    if is_controller(layout):
        x0 = window_w / 2 + (CTL_X0 - 1) * scale - margin
        x1 = window_w / 2 + (CTL_X0 + CTL_PX * 5 + CTL_W + 1) * scale + margin
        y0 = window_h + (CTL_Y0 - CTL_DY - 1) * scale - margin
    else:
        x0 = window_w / 2 + (KB_X0 - 1) * scale - margin
        x1 = window_w / 2 + (KB_X0 + KB_PX * 3 + KB_W + 1) * scale + margin
        y0 = window_h + (KB_Y0 - 1) * scale - margin
    return int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(window_h)


def _fit(ii, at, window_w, window_h, scale, shifts, layout):
    """Best (score, shift, edge scores) of a layout's grid at this scale over the whole-pixel shifts, or None if none
    fits inside the picture. ii: the integral picture of the grab, whose top left is at `at` in the window. The edge
    scores are, for the keyboard, each row's mean (None where a slot leaves the picture); for the controller, the
    mean and the weakest of the best CTL_CELLS_MIN cells."""
    rects = bar_rects(layout, window_w, window_h, scale)
    n = len(rects)
    bx = np.array([rects[i][0] for i in range(n)]) - at[0]
    by = np.array([rects[i][1] for i in range(n)]) - at[1]
    w, h = rects[0][2], rects[0][3]
    sh = np.array(shifts, int)
    r = _rings(ii, bx[None, :] + sh[:, :1], by[None, :] + sh[:, 1:], w, h)          # (shifts, cells)
    if is_controller(layout):
        best = -np.sort(-np.where(np.isnan(r), -np.inf, r), axis=1)[:, :CTL_CELLS_MIN]     # each shift's strongest cells (a cell off the picture counts as none)
        mean, weakest = best.mean(1), best.min(1)
        score = np.where(np.isfinite(mean), mean, -np.inf)
    else:
        rows = r.reshape(len(sh), 3, 4).mean(2)
        score = np.where(np.isnan(rows[:, 1]) | np.isnan(rows[:, 2]), -np.inf, (rows[:, 1] + rows[:, 2]) / 2)
    k = int(np.argmax(score))
    if not np.isfinite(score[k]):
        return None
    if is_controller(layout):
        return float(score[k]), tuple(int(v) for v in sh[k]), [float(mean[k]), float(weakest[k])]
    return float(score[k]), tuple(int(v) for v in sh[k]), [None if np.isnan(v) else float(v) for v in rows[k]]


def _accepted(fit, layout):
    if fit is None:
        return False
    if is_controller(layout):
        return fit[0] >= CTL_BAR_MIN and fit[2][1] >= CTL_CELL_MIN
    return fit[0] >= BAR_MIN and min(fit[2][1], fit[2][2]) >= ROW_MIN


def _strength(fit, layout):
    """How far above its own acceptance line a fit is (to pick between two layouts that both fit)."""
    return fit[0] / (CTL_BAR_MIN if is_controller(layout) else BAR_MIN)


FIND_LAYOUTS = ("keyboard", "controller-cross")      # the layouts the finder looks for ("Always Standard" is the keyboard bar)


def _crop(gray, at, window_w, window_h, scales):
    """The part of the grab the bars could be in at these scales (both layouts, room for the search's shifts), with
    where its top left is in the window: the integral picture is only made of that."""
    margin = 12
    boxes = [_bar_box(window_w, window_h, s, margin, layout) for s in scales for layout in FIND_LAYOUTS]
    x0, y0 = max(min(b[0] for b in boxes), at[0]), max(min(b[1] for b in boxes), at[1])
    x1, y1 = min(max(b[2] for b in boxes), at[0] + gray.shape[1]), min(window_h, at[1] + gray.shape[0])
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None, at
    return gray[y0 - at[1]:y1 - at[1], x0 - at[0]:x1 - at[0]], (x0, y0)


def find_bar(gray, at, window_w, window_h, hint=None):
    """The bar as a BarLook, or None. Tries the last good spot first, then every candidate scale, in both layouts."""
    last = hint.last if hint is not None and hint.last is not None and hint.last.layout in FIND_LAYOUTS else None
    if last is not None:
        part, pat = _crop(gray, at, window_w, window_h, [last.scale])
        if part is not None:
            ii = cv2.integral(part).astype(np.float64)
            nominal = bar_rects(last.layout, window_w, window_h, last.scale)[0]
            sx, sy = last.origin[0] - nominal[0], last.origin[1] - nominal[1]
            shifts = [(sx + dx, sy + dy) for dy in range(-HINT_SHIFT, HINT_SHIFT + 1) for dx in range(-HINT_SHIFT, HINT_SHIFT + 1)]
            fit = _fit(ii, pat, window_w, window_h, last.scale, shifts, last.layout)
            if _accepted(fit, last.layout):
                return _look(fit, last.layout, last.mode, last.scale, window_w, window_h)
    pairs = ui_layout.candidate_scales(window_h)
    if hint is not None and hint.mode_scale is not None and hint.mode_scale[1]:
        pairs = [hint.mode_scale] + [p for p in pairs if abs(p[1] - hint.mode_scale[1]) > 0.01 * p[1]]
    part, pat = _crop(gray, at, window_w, window_h, [sc for _, sc in pairs])
    if part is None:
        return None
    ii = cv2.integral(part).astype(np.float64)
    layouts = FIND_LAYOUTS if last is None or last.layout == "keyboard" else FIND_LAYOUTS[::-1]
    best = None
    for mode, scale in pairs:
        r = 2 + int(scale > 2.5)
        shifts = [(dx, dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1)]
        for layout in layouts:
            fit = _fit(ii, pat, window_w, window_h, scale, shifts, layout)
            if _accepted(fit, layout) and (best is None or _strength(fit, layout) > best[0]):
                best = (_strength(fit, layout), fit, layout, mode, scale)
    return _look(best[1], best[2], best[3], best[4], window_w, window_h) if best else None


def _look(fit, layout, mode, scale, window_w, window_h):
    shift, rows = fit[1], fit[2]
    rects = bar_rects(layout, window_w, window_h, scale, shift)
    if is_controller(layout):
        slots = tuple(SlotSpot(i, rects[i], "controller") for i in sorted(rects))
    else:
        first = 0 if rows[0] is not None and rows[0] >= ROW_MIN else 4
        slots = tuple(SlotSpot(i, rects[i], "keyboard") for i in range(first, 12))
    return BarLook(layout, mode, scale, rects[0][:2], slots)


# ---------------------------------------------------------------- reading the slots
SKILL_MAX = 0.20           # a slot is called a skill icon only this close to one seen on the bar
COUNT_ICON_MAX = 0.40       # a count is only taken from a slot whose icon is this clearly the item: a cursor or popup over the
                            # slot spoils the icon too, and the digits it hides can't be told from a count that ends


def _slot_item(table, crop_bgr, style="keyboard"):
    """(item, kind, distance) of one slot picture: kind "empty" for a bare plate, item None when it can't be told."""
    import potions
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    vec = potions.fingerprint(rgb, style=style)
    if vec is None:
        return (None, "empty", 1.0) if potions.is_item_slot(rgb, style) else (None, None, 1.0)
    found = table.identify(vec, style)
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
    runs the count readers' Runs of the last look."""
    if img is None or not getattr(img, "size", 0):
        return NOT_SEEN
    table = assets or load_assets()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bar = find_bar(gray, at, window_w, window_h, hint)
    if bar is None:
        return NOT_SEEN
    same = hint is not None and (hint.last is None or hint.last.layout == bar.layout)      # (the UI flipped: the other layout's Runs are for other slots)
    runs = (hint.runs if same and hint.runs else None) or {}
    reads = []
    for spot in bar.slots:
        x, y, w, h = spot.rect
        cx, cy = x - at[0], y - at[1]
        crop = img[cy:cy + h, cx:cx + w]
        if crop.shape[0] != h or crop.shape[1] != w:
            continue
        item, kind, dist = _slot_item(table, crop, spot.style)
        count, run = None, None
        if kind == "consumable":
            count, run = glyphs.read_count(img, (cx, cy, w, h), bar.scale, spot.style, runs.get(spot.index))
            if count is not None and count < 1000 and dist > COUNT_ICON_MAX:
                count, run = None, None      # (a 4-digit count can't be a cut-off longer one, and its digits spill into the icon)
        reads.append(SlotRead(spot.index, spot.rect, spot.style, item, kind, count, run))
    return PotionLook(True, bar.layout, bar.mode, bar.scale, tuple(reads))



def grab_box(window_w, window_h, last=None, margin=8):
    """(x, y, w, h) of the part of the window to take a picture of for the bar: around the last good spot's scale
    (either layout may show next: the UI can flip), else around every spot a candidate scale could put it."""
    scales = [last.scale] if last is not None else [s for _, s in ui_layout.candidate_scales(window_h)]
    boxes = [_bar_box(window_w, window_h, s, margin, layout) for s in scales for layout in FIND_LAYOUTS]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    return max(x0, 0), max(y0, 0), x1 - max(x0, 0), window_h - max(y0, 0)


def bar_from_look(look, window_w, window_h):
    """The BarLook a PotionLook came from (to hand back as Hint.last), or None when no bar was seen."""
    if not look.seen or look.layout not in FIND_LAYOUTS or not look.slots:
        return None
    first = look.slots[0]
    nominal = bar_rects(look.layout, window_w, window_h, look.scale)
    shift = (first.rect[0] - nominal[first.index][0], first.rect[1] - nominal[first.index][1])
    rects = bar_rects(look.layout, window_w, window_h, look.scale, shift)
    return BarLook(look.layout, look.mode, look.scale, rects[0][:2],
                   tuple(SlotSpot(s.index, s.rect, s.style) for s in look.slots))
