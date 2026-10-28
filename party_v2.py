"""Reader v2 for the party size: counts the members in the party list by the shape of their HP bars, at any UI
size (Default, Ideal, any window). It never reads text: other players' names are not looked at, kept or sent.

The party list is one HP bar per member in an evenly spaced column. A bar is a dark rounded frame with a white
rim just inside it and the fill (red, or grey where it's empty) inside that. It is a fixed size on the game's
1024 x 768 design area (ui_layout), so each candidate UI scale is tried and the one with the longest column of
bars wins; the EXP bar's mode (scale_hint) and the last good scale are tried first.

Pure: gets a picture, returns an answer. Keeps nothing, and the picture isn't kept.
"""
from collections import namedtuple

import cv2
import numpy as np

import ui_layout

# count: members (1 = on your own), or None when it can't be told; why: "ok" or "unclear" (see shadow.WHY);
# scale: the UI scale the bars were found at (hand it back as `last`), or None.
PartyLook = namedtuple("PartyLook", "count why scale")

BAR_W = (62.0, 80.0)         # a bar's outer size on the design area (measured 66.7-74 x 11-12)
BAR_H = (10.0, 13.8)
PITCH = (15.0, 19.5)         # the gap from one bar to the next (measured 16-17.8)
MAX_MEMBERS = 6
DARK = 70                    # the frame is darker than this (HSV value)
RIM = 150                    # the rim just inside the frame is lighter than this
RIM_SHARE = 0.6              # of the rim's pixels (a bar partly covered or faded isn't taken)
DARK_SHARE = 0.2             # inside the frame, no more than this share of dark pixels


def _shape_ok(val, x, y, w, h, s):
    """Is the dark blob at (x, y, w, h) an HP bar: a hollow frame, a light rim inside it, a lighter inside."""
    k = int(np.ceil(2.2 * s)) + 1               # the frame and its rim are about this thick
    if h <= 2 * k + 2:
        return False
    inner = val[y + k:y + h - k, x + k:x + w - k]
    if inner.size == 0 or np.count_nonzero(inner < DARK) > DARK_SHARE * inner.size:
        return False
    cols = slice(x + w // 4, x + w - w // 4)
    rim = np.concatenate((val[y + 1:y + k, cols].max(axis=0), val[y + h - k:y + h - 1, cols].max(axis=0)))
    return np.count_nonzero(rim >= RIM) >= RIM_SHARE * rim.size


def _column(bars, s):
    """The longest evenly spaced column of bars as a list, and how many bars stand in the same column
    without belonging to it (a gap: a member covered or faded)."""
    tol_x, tol_p = max(2.0, 2 * s), max(2.0, 2 * s)
    best, bars = [], sorted(bars, key=lambda b: b[1])
    for i, first in enumerate(bars):
        same = [b for b in bars[i:] if abs(b[0] - first[0]) <= tol_x and abs(b[2] - first[2]) <= tol_x]
        column, pitch = [first], None
        for b in same[1:]:
            step = b[1] - column[-1][1]
            if pitch is None and PITCH[0] * s <= step <= PITCH[1] * s:
                pitch = step
            if pitch is None or abs(step - pitch) > tol_p:
                break
            column.append(b)
        if len(column) > len(best):
            best = column
    if not best:
        return [], 0
    stray = [b for b in bars if abs(b[0] - best[0][0]) <= tol_x and b not in best]
    return best, len(stray)


def _scales(window_h, scale_hint, last):
    """The UI scales to try, most likely first: where it was found last time, what the EXP bar says, the rest."""
    order = [last.scale] if last is not None and last.scale else []
    if scale_hint:
        order.append(scale_hint[1] if isinstance(scale_hint, (tuple, list)) else float(scale_hint))
    order += [s for _, s in ui_layout.candidate_scales(window_h)]
    out = []
    for s in order:
        if not any(abs(s - t) <= ui_layout.SAME * t for t in out):
            out.append(s)
    return out


def read_party_v2(img, scale_hint=None, last=None, hsv=None):
    """The party size seen on `img` (the game window, BGR) as a PartyLook.

    scale_hint: (mode, scale) from ui_layout.mode_from_gh, or None. last: the previous PartyLook, or None.
    hsv: the same picture as HSV, if it's at hand already.
    No list on screen, or a single bar, means you're on your own (count 1). A column that can't be trusted
    (a bar missing from the middle of it, more than six) is count None, "unclear", never a guess."""
    h, w = img.shape[:2]
    val = (cv2.cvtColor(img, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2]
    n, _, stats, _ = cv2.connectedComponentsWithStats((val < DARK).astype(np.uint8), connectivity=8)
    blobs = [tuple(map(int, stats[i][:4])) for i in range(1, n) if stats[i][2] > 3 * stats[i][3]]
    found = {}
    for s in _scales(h, scale_hint, last):
        bars = [b for b in blobs if BAR_W[0] * s <= b[2] <= BAR_W[1] * s and BAR_H[0] * s <= b[3] <= BAR_H[1] * s
                and _shape_ok(val, *b, s)]
        if not bars:
            continue
        column, stray = _column(bars, s)
        if len(column) >= 2 or stray:
            found[s] = (len(column), stray)
    if not found:
        return PartyLook(1, "ok", None)
    sizes = {n for n, _ in found.values()}
    s, (n, stray) = next(iter(found.items()))
    if len(sizes) > 1 or stray or n > MAX_MEMBERS or any(st for _, st in found.values()):
        return PartyLook(None, "unclear", None)
    return PartyLook(n if n >= 2 else 1, "ok", s)
