"""The potion bar's items and what's used from it.

A slot is told apart by its icon, never by a picture of it: assets/potion_table.json holds a FINGERPRINT for
each item (a few numbers about its colours and shape), so no game art is kept. Finding the bar and reading its
slots is potion_bar.py; this file knows what an icon fingerprint is, which item it is, and counts what's used.

Nothing here reads the screen or the network.
"""
import json

import cv2
import numpy as np

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
RING_BG_MIN = 0.35          # ...and this share of its edge ring (the pink Attack tile has 0.20)
CONTROLLER_RING_BG_MIN = 0.25   # in the controller's slots the icons fill more of the slot (Meat has 0.32 of its ring light), and the
                                # pink ATTACK / JUMP / QUEST tiles have at most 0.18: skills are told by their own entries instead
EMPTY_INK_MAX = 0.03        # a slot with less than this beyond its background is empty
W_COL, W_ACH, W_SHAPE = 1.0, 1.0, 0.5
ACCEPT_MAX = 0.60           # the best item counts if it is this close...
MIN_RATIO = 1.5             # ...and the runner-up is at least this many times farther
UNSEEN_MAX, UNSEEN_RATIO = 0.30, 3.0    # an item never recorded on a real slot (only drawn from its icon) must be closer and clearer

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


def is_item_slot(rgb, style="keyboard"):
    """The slot has the light slot background along its edges (not the pink Attack tile, not the dark world)."""
    g = _normalise(rgb).astype(int)
    bg = (g.min(axis=2) > 205) & ((g.max(axis=2) - g.min(axis=2)) < 38)
    k = int(round(0.14 * N))
    ring = np.zeros((N, N), bool)
    ring[:k] = ring[-k:] = True
    ring[:, :k] = ring[:, -k:] = True
    return bg[ring].mean() >= (CONTROLLER_RING_BG_MIN if style == "controller" else RING_BG_MIN) and bg.mean() >= BG_MIN_SHARE


def fingerprint(rgb, reference=False, style="keyboard"):
    """The slot's fingerprint (FINGERPRINT_LEN numbers), or None if it isn't an item slot or is empty.

    rgb is the whole slot square, any size. reference=True skips those two checks (for an icon
    drawn on a plain slot background). style: the slot style it was cut from (potion_bar.STYLES)."""
    if not reference and not is_item_slot(rgb, style):
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
        # the icon drawn the way the controller's slot draws it (where the table has one: else the keyboard's drawing)
        self.matrix_controller = np.array([i.get("fingerprint_controller") or i["fingerprint"] for i in self.items],
                                          np.float32).reshape(-1, FINGERPRINT_LEN)
        # items recorded on real slots also carry "seen" (keyboard slots) and "seen_controller" (controller slots):
        # fingerprints measured there (one per UI mode and brightness); an item is as close as its closest one
        self.seen_owner, self.seen_matrix = {}, {}
        for style, key in (("keyboard", "seen"), ("controller", "seen_controller")):
            rows = [(k, fp) for k, i in enumerate(self.items) for fp in i.get(key, ())]
            self.seen_owner[style] = np.array([k for k, _ in rows], int)
            self.seen_matrix[style] = np.array([fp for _, fp in rows], np.float32).reshape(-1, FINGERPRINT_LEN)
        self.prices = {i["name"]: i for i in items}

    @classmethod
    def load(cls, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        table = cls(data["items"])
        table.aliases = data.get("aliases", {})
        return table

    aliases = {}

    def distances(self, vec, style="keyboard"):
        """How far a slot's fingerprint is from every item's (smaller is closer). style: the slot style it was cut from."""
        d = self._distances(self.matrix_controller if style == "controller" else self.matrix, vec)
        if len(self.seen_owner[style]):
            np.minimum.at(d, self.seen_owner[style], self._distances(self.seen_matrix[style], vec))
        return d

    @staticmethod
    def _distances(m, vec):
        n = HUE_BINS * 2
        col = np.abs(m[:, :n] - vec[:n]).sum(1)
        ach = np.abs(m[:, n:n + 3] - vec[n:n + 3]).sum(1)
        shape = np.abs(m[:, n + 3:] - vec[n + 3:]).mean(1)
        return W_COL * col + W_ACH * ach + W_SHAPE * shape * 4.0

    def identify(self, vec, style="keyboard"):
        """(name, kind, distance) of the item the fingerprint clearly is, or None when it isn't clearly one. An item
        counts as seen when it was recorded on a real slot of this style."""
        if vec is None or not len(self.items):
            return None
        d = self.distances(vec, style)
        order = np.argsort(d)
        best = order[0]
        # (a skill icon that looks like another skill icon is no doubt: only a potion or food near it is)
        rest = [k for k in order[1:] if self.kinds[best] != "skill" or self.kinds[k] != "skill"]
        second = d[rest[0]] if rest else float("inf")
        seen = bool(self.items[best].get("seen_controller" if style == "controller" else "seen"))
        if d[best] <= (ACCEPT_MAX if seen else UNSEEN_MAX) and second >= (MIN_RATIO if seen else UNSEEN_RATIO) * d[best]:
            return self.names[best], self.kinds[best], float(d[best])
        return None


# ---------------------------------------------------------------- potions used
MAX_STEP = 10               # a drop in one look bigger than this must be seen twice (a misread isn't use)
DRINK_RATE = 4              # the most one stack can lose in a second (measured: 2.4 spamming a key, 2.75 holding it)
RATE_SECONDS = (1.0, 60.0)  # a drop is judged over the time since the stack's last count: a look's worth at least, a minute at most
LOOK_GAP = 60               # no look at all for this long: tracking was cut short (game closed, computer asleep)


def allowed_drop(seconds):
    """The most potions one stack can lose to drinking in `seconds`."""
    low, high = RATE_SECONDS
    return int(DRINK_RATE * min(max(seconds, low), high))


class Stack:
    """One potion stack: its item, its count at the last look that counted it (and when that was), and the
    slot it sits in for each layout it has been seen in (none while it is out of sight)."""
    __slots__ = ("item", "count", "seen", "at", "pending", "last_use")

    def __init__(self, item, count, seen):
        self.item, self.count, self.seen = item, count, seen
        self.at = {}                          # layout -> slot index
        self.pending = None                   # a big drop waiting to be seen a second time
        self.last_use = None                  # (count after, count before) of the use at the last look


class PotionTracker:
    """Counts what's used from the bar: a stack's count going down is a use.

    A stack is told apart by its item and its count history, not by its place alone: in one layout each
    slot is its own stack; when the bar flips to another layout (or an item is moved) a stack is found again
    by its item and count, and when that is ambiguous nothing is counted for the item and it starts over.
    A stack out of sight keeps its count. A rise (buying, looting) is no use and becomes the new count.
    A drop bigger than drinking could explain since the stack was last counted (DRINK_RATE, at most a
    minute's worth) is a move, a trade or a misread, so it starts over without counting. A look with a
    shop, storage or trade window open starts everything over. Only looks made while tracking runs count;
    any others just set the counts, and a pause clears them, so town time never counts."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.used = {}                        # item name -> how many used since tracking began
        self.pause()

    def pause(self):
        self.stacks = []
        self.last_look = None

    def cost(self, prices):
        """Mesos spent on what's been used, given {name: price} (an item without a price costs 0)."""
        return sum(n * prices.get(name, 0) for name, n in self.used.items())

    def observe(self, look, counting, now, money_window=False):
        """Feeds a PotionLook: `counting` is whether tracking runs, `now` the time in seconds, `money_window`
        whether a shop, storage or trade window is open."""
        if self.last_look is not None and now - self.last_look > LOOK_GAP:
            self.stacks = []
        self.last_look = now
        if money_window:                      # counts move for other reasons there
            self.stacks, counting = [], False
        if not look.seen:
            return
        layout, here, fresh = look.layout, set(), []
        for s in look.slots:
            if s.kind in ("skill", "other", "empty"):
                continue
            if s.item is None:                # something is there that can't be named: leave its stack alone
                here.add(s.index)
                continue
            stack = self._at(layout, s.index)
            if stack is not None and stack.item == s.item:
                here.add(s.index)
                if s.count is not None:
                    self._count(stack, s.count, counting, now)
            elif s.count is not None:         # (a stack that was here is gone: found again below, by item and count)
                fresh.append(s)
        for stack in self.stacks:             # what left its slot has no place in this layout any more
            if stack.at.get(layout) not in here:
                stack.at.pop(layout, None)
        loose = [st for st in self.stacks if layout not in st.at]
        for item in dict.fromkeys(s.item for s in fresh):
            self._place(layout, [st for st in loose if st.item == item], [s for s in fresh if s.item == item],
                        counting, now)

    def _at(self, layout, index):
        return next((st for st in self.stacks if st.at.get(layout) == index), None)

    def _place(self, layout, old, new, counting, now):
        """Slots `new` hold an item that no stack in this layout sits in; `old` are its stacks out of sight."""
        pairs = []
        for s in list(new):                   # the same count: surely the same stack
            same = next((st for st in old if st.count == s.count), None)
            if same:
                pairs.append((same, s))
                old.remove(same)
                new.remove(s)
        if len(old) == 1 and len(new) == 1:   # one of each: the count moved by use (or a rise, or a misread)
            pairs.append((old.pop(), new.pop()))
        elif old and new:                     # which is which can't be told: start the item over
            self.stacks = [st for st in self.stacks if st not in old]
        for stack, s in pairs:
            self._count(stack, s.count, counting, now)
            self._claim(stack, layout, s.index)
        for s in new:
            stack = Stack(s.item, s.count, now)
            self.stacks.append(stack)
            self._claim(stack, layout, s.index)

    def _claim(self, stack, layout, index):
        for other in self.stacks:
            if other is not stack and other.at.get(layout) == index:
                del other.at[layout]
        stack.at[layout] = index

    def _count(self, stack, count, counting, now):
        before, undo = stack.count, stack.last_use
        stack.last_use = None
        if count < before and str(before).startswith(str(count)):
            # "224" read as "22" or "2": the mouse cursor or a popup hides the last digits (a drink that really ends
            # on such a count, 13 -> 1, is lost too: accepted); never a use, the count starts over from here
            stack.count, stack.seen, stack.pending = count, now, None
            return
        if counting and count < before:
            drop = before - count
            if drop <= allowed_drop(now - stack.seen):      # (more than drinking explains: it just starts over)
                if drop > MAX_STEP and stack.pending != count:
                    stack.pending, stack.last_use = count, undo
                    return
                self.used[stack.item] = self.used.get(stack.item, 0) + drop
                stack.last_use = (count, before)            # (undone if the next look is `before` again)
        elif counting and undo == (before, count):          # back to the count before a use: that was a misread
            self.used[stack.item] -= count - before
        stack.count, stack.seen, stack.pending = count, now, None


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
