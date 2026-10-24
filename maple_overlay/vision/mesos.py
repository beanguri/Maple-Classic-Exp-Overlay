"""Finding the mesos in the inventory, shops, storage and trade windows, and reading them."""
import re

import cv2
import numpy as np

from maple_overlay.config import log_error, MESOS_BOX_PATH, MESOS_PATH, MESOS_START_PATH
from maple_overlay.vision import engine
from maple_overlay.vision.exp_bar import _blobs, CANON, digit_patch, DigitTemplates, Layout
from maple_overlay.vision.text import _ocr_votes


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
    _, first, _ = templates.match(lay, (_mesos_spot(field, 0), 0.4 * h, "digit"))
    _, after, _ = templates.match(lay, (_mesos_spot(field, -1), 0.4 * h, "digit"))
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
        digit, score, lead = templates.match(lay, (cx, 0.4 * h, "digit"))
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
    if not engine.tesseract_ok:
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
