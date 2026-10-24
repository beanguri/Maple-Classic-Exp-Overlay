"""The potion bar: the hotkey slots at the bottom right of the game, read from a screenshot.

Each slot shows an icon and, at its bottom left, how many are left. A slot is told apart
by its icon, never by a picture of it: assets/potion_table.json holds a FINGERPRINT for each
item (a few numbers about its colours and shape), so no game art is kept.
The count is read with glyph templates (assets/potion_digits.npz, the game's small count
font; only the pixels of a glyph that look the same on every item are matched, as the icon
behind a count differs), after the slot is brought to the game's 1080 p size: the UI is that picture scaled
by window height / 1080, so one set of templates serves every screen size.

Nothing here reads the screen or the network: read_bar() takes a picture and says what's in
it. A slot that can't be told is left out rather than guessed.
"""
import json
from collections import namedtuple

import cv2
import numpy as np

Slot = namedtuple("Slot", "col row name count")      # count None: the icon is known, the number isn't

# Where things are, measured on the 5120 x 2880 reference screen (the UI scales with window
# height / 2880): the bar's top left is 930 px right of the window's centre and 730 px up from
# its bottom; slots sit on this grid inside it (4 columns; 2 or 3 rows, the top one is absent
# on a 2-row bar).
BAR_DX, BAR_DY, BAR_W, BAR_H = 930, 730, 600, 490
SLOT_X, SLOT_Y, SLOT_W, SLOT_H = (32, 163, 294, 425), (84, 212, 340), 122, 116
SEARCH = 3                  # slots must sit within a pixel or two: tried this far around the estimate

# ---------------------------------------------------------------- icon fingerprints
N = 40                      # a slot is shrunk to N x N
INSET = (0.08, 0.04, 0.08, 0.16)       # left, top, right, bottom share of the slot trimmed off (its border)
LABEL_Y = 0.48              # the key label plate (top left) is ignored above this
COLOUR_Y = 0.40             # saturated pixels count from here down (label letters are pale)
DIGIT_Y, DIGIT_X = 0.62, 0.86          # the count digits (below and left of these) are ignored
SAT_MIN, VAL_MIN = 0.35, 0.28
HUE_BINS = 12
BAND_COLS = 8
SHRINK = 0.04               # few coloured pixels give small numbers
BG_MIN_SHARE = 0.12         # an item slot is at least this share light slot background...
RING_BG_MIN = 0.32          # ...and this share of its edge ring (the pink Attack tile has 0.20)
EMPTY_INK_MAX = 0.03        # a slot with less than this beyond its background is empty
W_COL, W_ACH, W_SHAPE = 1.0, 1.0, 0.5
ACCEPT_MAX = 0.60           # the best item counts if it is this close...
MIN_RATIO = 1.5             # ...and the runner-up is at least this many times farther

_SAFE = np.ones((N, N), bool)
_SAFE[: int(round(LABEL_Y * N)), :] = False
_SAFE[int(round(DIGIT_Y * N)):, : int(round(DIGIT_X * N))] = False
_COLOUR = np.zeros((N, N), bool)
_COLOUR[int(round(COLOUR_Y * N)):, :] = True


def _cells():
    """Shape cells (y0, y1, x0, x1): the band under the key label (8 x 2) and the strip right of the digits."""
    cells = []
    y0, y1 = int(round(LABEL_Y * N)), int(round(DIGIT_Y * N))
    ym = (y0 + y1) // 2
    for ya, yb in ((y0, ym), (ym, y1)):
        for c in range(BAND_COLS):
            cells.append((ya, yb, c * N // BAND_COLS, (c + 1) * N // BAND_COLS))
    xs, ym2 = int(round(DIGIT_X * N)), (y1 + N) // 2
    return cells + [(y1, ym2, xs, N), (ym2, N, xs, N)]


_CELLS = _cells()
FINGERPRINT_LEN = HUE_BINS * 2 + 3 + len(_CELLS)


def _normalise(rgb):
    h, w = rgb.shape[:2]
    x0, y0 = int(round(INSET[0] * w)), int(round(INSET[1] * h))
    x1, y1 = w - int(round(INSET[2] * w)), h - int(round(INSET[3] * h))
    return cv2.resize(rgb[y0:y1, x0:x1], (N, N), interpolation=cv2.INTER_AREA)


def is_item_slot(rgb):
    """The slot has the light slot background along its edges (not the pink Attack tile, not the dark world)."""
    g = _normalise(rgb).astype(int)
    bg = (g.min(axis=2) > 205) & ((g.max(axis=2) - g.min(axis=2)) < 38)
    k = int(round(0.14 * N))
    ring = np.zeros((N, N), bool)
    ring[:k] = ring[-k:] = True
    ring[:, :k] = ring[:, -k:] = True
    return bg[ring].mean() >= RING_BG_MIN and bg.mean() >= BG_MIN_SHARE


def fingerprint(rgb, reference=False):
    """The slot's fingerprint (FINGERPRINT_LEN numbers), or None if it isn't an item slot or is empty.

    rgb is the whole slot square, any size. reference=True skips those two checks (for an icon
    drawn on a plain slot background)."""
    if not reference and not is_item_slot(rgb):
        return None
    g = _normalise(rgb)
    hsv = cv2.cvtColor(g, cv2.COLOR_RGB2HSV).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 2.0, hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    mx, mn = g.max(axis=2).astype(int), g.min(axis=2).astype(int)
    bg = (mn > 205) & ((mx - mn) < 38)                   # slot background and white bodies
    dark = mx < 75
    chrom = (sat >= SAT_MIN) & (val >= VAL_MIN) & ~dark
    grey = ~bg & ~dark & ~chrom
    dz = np.zeros((N, N), bool)                          # the pale-blue shadow of the digits is dropped
    dz[int(round(DIGIT_Y * N)):, : int(round(DIGIT_X * N))] = True
    shadow = dz & (hue > 185) & (hue < 245) & (val > 0.75) & (sat < 0.70)
    hb = np.minimum((hue / (360.0 / HUE_BINS)).astype(int), HUE_BINS - 1)
    bright = val >= 0.72
    cm = _COLOUR & ~shadow & chrom
    denom = float(cm.sum()) + SHRINK * N * N
    vec = [(cm & (hb == b) & (bright == br)).sum() / denom for b in range(HUE_BINS) for br in (False, True)]
    n = float(_SAFE.sum())
    vec += [(_SAFE & dark).sum() / n, (_SAFE & grey).sum() / n]
    ink = (_SAFE & ~bg).sum() / n
    if not reference and ink < EMPTY_INK_MAX and cm.sum() < 12:
        return None                                      # nothing but the slot (and its key label)
    vec.append(ink)
    vec += [(~bg[ya:yb, xa:xb]).mean() for ya, yb, xa, xb in _CELLS]
    return np.array(vec, np.float32)


class PotionTable:
    """The items that can sit on the bar: name, kind, price and fingerprint (assets/potion_table.json)."""

    def __init__(self, items):
        self.items = [i for i in items if i.get("fingerprint")]
        self.names = [i["name"] for i in self.items]
        self.kinds = [i.get("kind", "consumable") for i in self.items]
        self.matrix = np.array([i["fingerprint"] for i in self.items], np.float32).reshape(-1, FINGERPRINT_LEN)
        self.prices = {i["name"]: i for i in items}

    @classmethod
    def load(cls, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        table = cls(data["items"])
        table.aliases = data.get("aliases", {})
        return table

    aliases = {}

    def distances(self, vec):
        """How far a slot's fingerprint is from every item's (smaller is closer)."""
        m, n = self.matrix, HUE_BINS * 2
        col = np.abs(m[:, :n] - vec[:n]).sum(1)
        ach = np.abs(m[:, n:n + 3] - vec[n:n + 3]).sum(1)
        shape = np.abs(m[:, n + 3:] - vec[n + 3:]).mean(1)
        return W_COL * col + W_ACH * ach + W_SHAPE * shape * 4.0

    def identify(self, vec):
        """(name, kind) of the item the fingerprint clearly is, or None when it isn't clearly one."""
        if vec is None or not len(self.items):
            return None
        d = self.distances(vec)
        order = np.argsort(d)
        best = order[0]
        second = d[order[1]] if len(order) > 1 else float("inf")
        if d[best] <= ACCEPT_MAX and second >= MIN_RATIO * d[best]:
            return self.names[best], self.kinds[best]
        return None


# ---------------------------------------------------------------- the count digits
NATIVE = (46, 44)           # the slot at the game's 1080 p size (width, height)
BAND = (25, 43)             # rows of it that hold the count
DIGIT_X0 = 3                # the first digit's left edge
DIGIT_TAU = 0.60            # a digit is taken at or above this match (true ones score 0.7 to 0.97)
DIGIT_DOUBT = 0.56          # between this and DIGIT_TAU nothing is certain: no number rather than a guess
MAX_DIGITS = 4


Glyph = namedtuple("Glyph", "template mask")    # mask: 1 where the digit looks the same on every item, 0 elsewhere


def load_digits(path):
    """{"0": Glyph, ...} at the 1080 p size (the band's rows by the glyph's width).

    Behind a digit sits whatever the item's icon has there (the red potion's dark bottle, a pale
    egg), so only the pixels that stay the same from item to item are matched (the mask)."""
    with np.load(path) as z:
        return {k[1:]: Glyph(z[k].astype(np.float32), z["m" + k[1:]].astype(np.float32))
                for k in z.files if k[0] == "d"}


def _masked_match(region, glyph):
    """Normalised correlation of the glyph's masked pixels with every position of region."""
    t, mask = glyph
    n = mask.sum()
    centred = (t - (mask * t).sum() / n) * mask
    img = region.astype(np.float32) - 128.0
    s1 = cv2.matchTemplate(img, mask, cv2.TM_CCORR)
    s2 = cv2.matchTemplate(img * img, mask, cv2.TM_CCORR)
    num = cv2.matchTemplate(img, centred, cv2.TM_CCORR)
    var = s2 - s1 * s1 / n
    flat = var < 4.0 * n                    # under 2 grey levels of spread: nothing there to match
    score = num / np.sqrt(np.maximum(var, 1.0) * float((centred * centred).sum()))
    return np.where(flat, 0.0, np.clip(score, -1.0, 1.0)).astype(np.float32)


def native_gray(slot_bgr):
    """The slot brought to the game's 1080 p size, in grey (what the digit templates are made from)."""
    small = slot_bgr.shape[1] <= NATIVE[0]
    return cv2.cvtColor(cv2.resize(slot_bgr, NATIVE, interpolation=cv2.INTER_CUBIC if small else cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY).astype(np.float32)


PAD = 4                     # how far the count may sit from where the icon puts the slot, in 1080 p pixels


def _decode(maps, widths, j, x0):
    """One reading of the count if the digits start near x0 on row offset j: (text, sum of matches, sure).

    sure is False when a digit is neither clearly there nor clearly not."""
    text, total, x, sure = "", 0.0, x0, True
    for _ in range(MAX_DIGITS):
        best = (-1.0, None, 0)
        for ch, m in maps.items():
            for px in range(x - 1, x + 2):
                i = px + PAD
                if 0 <= i < m.shape[1] and m[j, i] > best[0]:
                    best = (float(m[j, i]), ch, px)
        if best[0] >= DIGIT_TAU:
            text, total, x = text + best[1], total + best[0], best[2] + widths[best[1]]
        else:
            sure = best[0] < DIGIT_DOUBT
            break
    return text, total, sure


def read_count(slot_bgr, digits):
    """The number at the bottom left of a slot, or None if there is none or it isn't certain.

    The digits are matched at every position near where they should be (the icon places the slot
    only to within a pixel or two) and the reading that fits best is kept. If a reading with a
    doubtful digit fits better than the best sure one, it is no reading."""
    g = native_gray(slot_bgr)
    region = np.pad(g, ((PAD, PAD), (PAD, PAD)), mode="edge")[BAND[0]:BAND[1] + 2 * PAD, :]
    maps = {ch: _masked_match(region, g) for ch, g in digits.items()}
    widths = {ch: g.template.shape[1] for ch, g in digits.items()}
    sure, doubtful = ("", 0.0), 0.0
    for j in range(2 * PAD + 1):
        for x0 in range(0, 2 * PAD):
            text, total, ok = _decode(maps, widths, j, x0)
            if not text:
                continue
            if ok and total > sure[1]:
                sure = (text, total)
            elif not ok:
                doubtful = max(doubtful, total)
    return int(sure[0]) if sure[0] and sure[1] >= doubtful else None


# ---------------------------------------------------------------- the bar
def slot_rect(col, row, scale, ox, oy):
    """(x, y, w, h) of a slot, given where the bar's top left is."""
    return (int(round(ox + SLOT_X[col] * scale)), int(round(oy + SLOT_Y[row] * scale)),
            int(round(SLOT_W * scale)), int(round(SLOT_H * scale)))


def bar_estimate(window_w, window_h):
    """(x, y, scale): the bar's estimated top left in the window, and the UI scale."""
    s = window_h / 2880
    return window_w / 2 + BAR_DX * s, window_h - BAR_DY * s, s


def bar_crop_box(window_w, window_h, margin=8):
    """(x, y, w, h) of the part of the window that holds the bar (with a margin all round)."""
    x, y, s = bar_estimate(window_w, window_h)
    return int(x) - margin, int(y) - margin, int(BAR_W * s) + 2 * margin, int(BAR_H * s) + 2 * margin


def _crop(img, rect):
    x, y, w, h = rect
    if x < 0 or y < 0 or x + w > img.shape[1] or y + h > img.shape[0]:
        return None
    return img[y:y + h, x:x + w]


def _fit(img, scale, ox, oy, table):
    """How well the slots at this position look like items: lower is better."""
    total = 0.0
    for row in range(3):
        for col in range(4):
            crop = _crop(img, slot_rect(col, row, scale, ox, oy))
            vec = None if crop is None else fingerprint(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            total += 1.0 if vec is None else min(1.0, float(table.distances(vec).min()))
    return total


def read_bar(img, scale, origin, table, digits, search=SEARCH):
    """What is on the bar: ([Slot, ...], (ox, oy)) for a picture of the window's lower right.

    origin is where the bar's top left is estimated to be in img; the position is refined by
    trying a few pixels each way (and the refined one is returned, to start from next time).
    Only potions and food are returned: skills, empty slots and other items (throwing stars,
    scrolls, pet food) are left out."""
    if not len(table.items):
        return [], origin
    ex, ey = origin
    best = None
    for dx in range(-search, search + 1):
        for dy in range(-search, search + 1):
            score = _fit(img, scale, ex + dx, ey + dy, table)
            if best is None or score < best[0]:
                best = (score, ex + dx, ey + dy)
    _, ox, oy = best
    slots = []
    for row in range(3):
        for col in range(4):
            crop = _crop(img, slot_rect(col, row, scale, ox, oy))
            if crop is None:
                continue
            found = table.identify(fingerprint(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
            if found and found[1] == "consumable":
                slots.append(Slot(col, row, table.aliases.get(found[0], found[0]), read_count(crop, digits)))
    return slots, (ox, oy)


# ---------------------------------------------------------------- potions used
MAX_STEP = 10               # a drop in one look bigger than this must be seen twice (a misread isn't use)


class PotionTracker:
    """Counts what's used from the bar: a slot's count going down is a use.

    Each slot (by place and name) has a baseline, the count at the last look. A rise (buying,
    looting) is no use and becomes the new baseline. Only looks made while tracking runs count;
    any others just set the baselines, and a pause clears them, so town time never counts."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.used = {}                        # item name -> how many used since tracking began
        self.pause()

    def pause(self):
        self.base = {}                        # (col, row, name) -> count at the last look
        self.pending = {}                     # key -> a big drop waiting to be seen a second time
        self.last_use = {}                    # key -> (count after, count before) of the use at the last look

    def cost(self, prices):
        """Mesos spent on what's been used, given {name: price} (an item without a price costs 0)."""
        return sum(n * prices.get(name, 0) for name, n in self.used.items())

    def observe(self, slots, counting):
        if slots:                             # a stack moved or gone: its old place has no say any more
            here = {(s.col, s.row, s.name) for s in slots}
            for key in [k for k in self.base if k not in here]:
                self.base.pop(key)
                self.pending.pop(key, None)
                self.last_use.pop(key, None)
        for s in slots:
            if s.count is None:
                continue
            key, count = (s.col, s.row, s.name), s.count
            before = self.base.get(key)
            if not counting or before is None or count >= before:
                undo = self.last_use.pop(key, None)
                if counting and undo and undo == (before, count):   # back to the count before a use: that was a misread
                    self.used[s.name] -= count - before
                self.base[key] = count
                self.pending.pop(key, None)
                continue
            if before - count > MAX_STEP and self.pending.get(key) != count:
                self.pending[key] = count
                continue
            self.used[s.name] = self.used.get(s.name, 0) + before - count
            self.last_use[key] = (count, before)              # (undone if the next look is `before` again)
            self.base[key] = count
            self.pending.pop(key, None)


# ---------------------------------------------------------------- prices
def clean_prices(saved):
    """The player's own prices as saved: {name: whole mesos >= 0}, anything unusable dropped."""
    if not isinstance(saved, dict):
        return {}
    return {k: v for k, v in saved.items() if isinstance(k, str) and type(v) is int and v >= 0}


class Prices:
    """What each item costs: the shipped price (items: {name: {"price", "unpriced"}} from the table),
    unless the player has set their own."""

    def __init__(self, items, custom):
        self.items, self.custom = items, custom

    def get(self, name):
        if name in self.custom:
            return self.custom[name]
        return self.items.get(name, {}).get("price", 0)

    def mapping(self):
        return {name: self.get(name) for name in self.items} | {n: p for n, p in self.custom.items() if n not in self.items}

    def note(self, used):
        """Asks for a price when something used has none: one the player set to 0 although NPCs sell it.
        (Items no NPC sells cost 0 by default, and that is fine.)"""
        names = [n for n in used if self.custom.get(n) == 0 and not self.items.get(n, {}).get("unpriced", True)]
        return "Set a price for " + ", ".join(names) if names else None
