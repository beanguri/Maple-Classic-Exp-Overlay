"""Finding the EXP bar in the game window and reading its digits by matching them with the game's font."""
import re
from collections import namedtuple

import cv2
import numpy as np

from maple_overlay.config import TEMPLATE_PATH
from maple_overlay.vision import engine


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
    return [re.sub(r"\D", "", text) for text in engine.TESS.read_lines(images, "0123456789,.")]

def tesseract_candidates(bgr, lay):
    """Fallback: (exp, pct) guesses from Tesseract. The tracker picks one that adds up."""
    if not engine.tesseract_ok:
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
