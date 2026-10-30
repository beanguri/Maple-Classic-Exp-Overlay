"""Reader v3 for the party size: counts the members in the party list by the exact shape of their HP-bar frames,
at any UI size (Default, Ideal, any window). It never reads text: other players' names are not looked at, kept or sent.

The party list is a see-through panel with one row per member: a bullet, the NAME, and an HP bar. Rows are 17 pixels
apart on the game's 1024 x 768 design area (ui_layout). Only the bars are used. The name area (the strip between the
bullet and the bar column) is never read, never compared and never kept: a selected row's highlight lies in it
and must not matter. The bullet and the colours are not used either (they change: suns, a star, grey rows).

How it reads a look:
 1. Find. A cheap first pass looks for the top edge of a bar: a dark run with a light run (the rim) right under it,
    as long as a rim is, with the bottom edge at the right distance. That works when the bar touches something dark
    behind it. Each such spot is then checked exactly: the frame is drawn as the game draws it at this UI scale and
    sub-pixel position (party_shapes.py) and nearly every pixel that is surely frame or rim must agree. The
    EXP bar's mode (scale_hint) and the last good scale are tried first; the last list's place is searched first,
    the whole window only when that finds nothing.
 2. Count. The bars found must stand in one column, at the exact row pitch. The panel's top line above the first
    row and bottom line below the last row must both be seen (the inner line is lighter than the outer one: true
    on every background measured). A row-shaped gap, a bar standing apart, more than six, a pure-black rectangle
    (the overlay's own window is blacked out in the grab) over the list: count None, "unclear". A member whose row
    is greyed (other map, far away) still counts.
 3. Hold. After a party of 2 or more an unclear look keeps the last count ("held"), and so does a look with no
    list at all, until there have been HOLD_LOOKS of those in a row (then you are on your own: 1). The state
    travels in `last` (the previous PartyLook); this module keeps nothing.

Pure: gets a picture, returns an answer. Keeps nothing, and the picture isn't kept.
"""
import math
from collections import namedtuple

import cv2
import numpy as np

import party_shapes
import ui_layout

# count: members (1 = on your own), or None when it can't be told.
# why: "ok" (read, or no list at all and nothing list-like), "unclear" (count None), "held" (the count of a
#      moment ago kept while the list can't be read or is gone).
# scale: the UI scale (hand it back as `last`), or None. box: where the list was (x, y, w, h) in window pixels, or
# None. gone: looks in a row without a list since the last party count (0 while a list is read).
PartyLook = namedtuple("PartyLook", "count why scale box gone")

HOLD_LOOKS = 5              # looks in a row without a list (about 15 s) a party count is kept for
MAX_MEMBERS = 6
PITCH = 17                  # design pixels from one row's bar to the next's

DARK = 80                   # first pass: a frame pixel is darker than this (V)...
BRIGHT = 140                # ...and the rim just inside it lighter than this (both looser where a line is thinner than two pixels)
RUN_SHARE = 0.7             # of the rim's length (63 design pixels) a dark-over-light run must have
SHARE_OK = 0.95             # a bar is found when this share of its trusted pixels agree with the drawing
SHARE_LIKE = 0.75           # "something list-like": a partial match this good (a bar half covered, a look-alike)
CORR_OK = 0.85              # a match counts only when the picture also follows the drawing's light and dark this well
EDGE_DIFF = 30              # the panel's inner line is this much lighter than the outer one...
EDGE_INSIDE = 4             # ...and it is lighter than the panel's inside by this much, or the outer line differs from the
EDGE_BEYOND = 8             # map beyond it by this much...
EDGE_SHARE = 0.75           # ...along this share of the bar's width
LEFT_MAX = 118              # the bar column lies 73..118 design pixels right of the panel's left line
BOX_W = 190                 # a list's box, from the widest left reach to the right line (design pixels)
PAD = 20                    # design pixels added round the last box when looking there first
MAX_STARTS = 24             # spots tried along one wide dark-over-light run at most
MAX_CANDIDATES = 400        # spots checked exactly per scale at most (a mostly dark picture); more: unclear
WEAK_SCALES = (1.01, 1.35)   # the frame is under about 1.35 pixels thick here, so a drawn bar says little (the game's own sizes start at 1.406)
COVER_SPAN = 3.0            # a pure-black block this many design pixels across can't be frame: it's a cover


def _scales(window_h, scale_hint, last):
    """The (UI scale, kernels) to try, most likely first: where the list was last time, what the EXP bar says,
    the rest. A whole step is copied block for block in UI Size Ideal, smoothed in Default."""
    order, ideal = [], bool(scale_hint and isinstance(scale_hint, (tuple, list)) and scale_hint[0] == "ideal")
    if last is not None and last.scale:
        order.append(last.scale)
    if scale_hint:
        order.append(scale_hint[1] if isinstance(scale_hint, (tuple, list)) else float(scale_hint))
    order += [s for _, s in ui_layout.candidate_scales(window_h)]
    out = []
    for s in order:
        if not any(abs(s - t) <= ui_layout.SAME * t for t in out):
            out.append(s)
    res = []
    for s in out:
        ks = party_shapes.kernels_for(s)
        res.append((s, tuple(reversed(ks)) if ideal and len(ks) == 2 else ks))
    return res


# ---------------------------------------------------------------- first pass
def _dark_for(s):
    """A frame pixel is darker than this at scale s. A line one design pixel thick is s pixels thick on screen: under
    two pixels no pixel need be wholly on it, and the smoothing makes its darkest one as light as 0.5 / s of white
    (a little more for safety). Only at exactly 1x is it pure."""
    return DARK if abs(s - 1.0) < 0.01 else max(DARK, int(255 * 0.55 / s))


def _bright_for(s):
    """The same for the rim: its lightest pixel is at least this light at scale s."""
    return BRIGHT if abs(s - 1.0) < 0.01 else min(BRIGHT, int(255 * (1 - 0.55 / s) * 0.9))


def _runs(mask):
    """(y, x0, x1) of every maximal run of set pixels in a 2-D 0/1 mask (x1 exclusive)."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return []
    brk = np.nonzero((np.diff(ys) != 0) | (np.diff(xs) != 1))[0] + 1
    starts, ends = np.concatenate(([0], brk)), np.concatenate((brk, [len(ys)]))
    return [(int(ys[a]), int(xs[a]), int(xs[b - 1]) + 1) for a, b in zip(starts, ends)]


def _top_runs(val, min_len, dark=DARK, bright=BRIGHT):
    """Runs of at least min_len pixels where a dark pixel has a light one right under it (one or two rows down):
    the top edge of a bar over its rim. -> [(y, x0, x1)] with y the last dark row. (Cost: ~3 ms at 4K.)"""
    dk = val < dark
    cnt = cv2.reduce(dk.view(np.uint8), 1, cv2.REDUCE_SUM, dtype=cv2.CV_32S).ravel()
    rows = np.flatnonzero(cnt[:-2] >= min_len)
    if len(rows) == 0:
        return []
    t = dk[rows] & ((val[rows + 1] >= bright) | (val[rows + 2] >= bright))
    keep = np.flatnonzero(np.count_nonzero(t, axis=1) >= min_len)
    if len(keep) == 0:
        return []
    e = cv2.erode(np.ascontiguousarray(t[keep]).view(np.uint8), np.ones((1, min_len), np.uint8), anchor=(0, 0),
                  borderType=cv2.BORDER_CONSTANT, borderValue=0)
    return [(int(rows[keep[r]]), x0, x1 + min_len - 1) for r, x0, x1 in _runs(e)]


def _has_bottom(val, x0, x1, y, s):
    """Is there a bar's bottom edge (a dark row with a light one just above) at the distance a bar has, under a
    top edge whose last dark row is y, over the columns x0..x1?"""
    a, b = x0 + int(0.1 * (x1 - x0)), x0 + int(0.9 * (x1 - x0))
    for yb in range(int(y + 9 * s - 1), int(y + 10 * s + 2.5)):
        if yb + 1 > val.shape[0] or yb - 2 < 0:
            continue
        seg = val[yb, a:b]
        if seg.size and np.count_nonzero(seg < _dark_for(s)) >= 0.9 * seg.size:
            above = np.maximum(val[yb - 1, a:b], val[yb - 2, a:b])
            if np.count_nonzero(above >= _bright_for(s)) >= 0.8 * seg.size:
                return True
    return False


def _ends_dark(val, y, xs, s):
    """For bar left edges xs (screen x of design column 0) under a top edge whose last dark row is y: do the
    frame's two side columns (66 design pixels apart) show dark at mid height? -> bool array. Cheap: it rules out
    most spots in a wide dark-over-light run before any drawing is made."""
    ra = int(round(y + 2.5 * s))
    rb = max(ra + 1, int(round(y + 6 * s)))
    if ra < 0 or rb > val.shape[0]:
        return np.zeros(len(xs), bool)
    w = val.shape[1]
    left = np.round(xs).astype(int)
    right = np.round(xs + 66 * s).astype(int)
    lo, hi = int(left.min()) - 2, int(right.max()) + 3
    if lo < 0 or hi > w:
        ok = (left >= 2) & (right <= w - 3)
        if not ok.any():
            return ok
        lo, hi = max(lo, 0), min(hi, w)
    else:
        ok = np.ones(len(xs), bool)
    dark = val[ra:rb, lo:hi].max(axis=0) < _dark_for(s)
    near = dark[:-2] | dark[1:-1] | dark[2:]                    # dark within a pixel of x (near[i] is x = lo + 1 + i)
    li, ri = left - lo - 1, right - lo - 1
    inside = (li >= 0) & (ri < len(near)) & ok
    out = np.zeros(len(xs), bool)
    out[inside] = near[li[inside]] & near[ri[inside]]
    return out


def _spots(runs, val, s):
    """Where to check for a bar's top left design pixel at scale s: [(x, y)] screen estimates."""
    rim = 63 * s
    out = []
    for y, x0, x1 in runs:
        ln = x1 - x0
        if ln < RUN_SHARE * rim or ln > 3 * 67 * s:
            continue
        starts = np.array([x0 - 2 * s]) if ln <= 1.1 * rim else np.arange(x0, x1 - 0.9 * rim, 1.0) - 2 * s
        starts = starts[_ends_dark(val, y, starts, s)]
        for xb in starts[:MAX_STARTS]:
            if _has_bottom(val, int(xb + 2 * s), int(xb + 2 * s + rim), y, s):
                out.append((float(xb), y + 1 - s))
    return out


# ---------------------------------------------------------------- exact check
def _search(img, x, y, s, kernel, xs, ys, early=0.0):
    """Best (agree, judged, ox, oy, mean error) of the drawing placed at x + dx, y + dy for dx in xs, dy in ys. The
    best is the one with the smallest mean error (a share can sit at 1.0 over a wide span of positions where the
    frame is thin); y first (it decides most), then x, then y again at the better x (the two lean on each other)."""
    top = None

    def better(a):
        return a and a[1] and (top is None or a[2] < top[4])
    for dy in ys:
        a = party_shapes.agree(img, x, y + dy, s, kernel)
        if better(a):
            top = (a[0], a[1], x, y + dy, a[2])
    if top is None or top[0] < early * top[1]:
        return None
    y = top[3]
    for dx in xs:
        a = party_shapes.agree(img, x + dx, y, s, kernel)
        if better(a):
            top = (a[0], a[1], x + dx, y, a[2])
    x, y = top[2], top[3]
    for dy in ys[abs(ys) <= 0.26]:                               # y again, near its best, at the better x
        if dy:
            a = party_shapes.agree(img, x, y + dy, s, kernel)
            if better(a):
                top = (a[0], a[1], x, y + dy, a[2])
    return top


def _fit(img, x, y, s, kernels):
    """The best drawing of the frame near the estimate (x, y) of a bar's top left screen pixel: coarse then fine.
    -> (agree, judged, ox, oy, kernel) or None."""
    reach = max(1.5, 0.6 * s)
    best = None
    for kernel in kernels:
        c = _search(img, x, y, s, kernel, np.arange(-reach, reach + 0.01, 0.5), np.arange(-1.0, 1.01, 0.25), 0.3)
        if c is None or c[0] < 0.6 * c[1]:
            continue
        f = _search(img, c[2], c[3], s, kernel, np.arange(-0.25, 0.26, 0.125), np.arange(-0.25, 0.26, 0.125))
        f = f if f and f[4] <= c[4] else c
        if best is None or f[4] < best[5]:
            best = (f[0], f[1], f[2], f[3], kernel, f[4])
        if best[0] >= SHARE_OK * best[1] and best[5] < 20:
            break
    return best[:5] if best else None


def _fit_near(img, ox, oy, s, kernel):
    """The same, at a spot known to within 0.25 pixel (a row of a column whose other bars were found)."""
    r = _search(img, ox, oy, s, kernel, np.arange(-0.25, 0.26, 0.125), np.arange(-0.375, 0.376, 0.125))
    return r[:4] if r else None


def _corr(img, ox, oy, s, kernel):
    """How well the picture follows the drawing's light and dark over the trusted pixels (Pearson correlation, -1..1;
    0 for a flat patch). Where the frame is thinner than two pixels most trusted pixels are mid-grey blends, and a
    flat mid-grey patch agrees with them by level alone: this tells it from a real bar."""
    fx, fy = math.floor(ox), math.floor(oy)
    fr, off = party_shapes.frame(s, kernel, int(round((ox - fx) * party_shapes.Q)), int(round((oy - fy) * party_shapes.Q)))
    x, y = fx - off, fy - off
    if x < 0 or y < 0 or y + fr.h > img.shape[0] or x + fr.w > img.shape[1]:
        return 0.0
    real = img[y + fr.ys, x + fr.xs].mean(axis=1)
    if real.std() < 1e-6 or fr.expect.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(real, fr.expect)[0, 1])


Bar = namedtuple("Bar", "ox oy share kernel")


def _bar_of(img, r, s):
    """A Bar from a search result (agree, judged, ox, oy, kernel), or None. Where the picture does not follow the
    drawing's light and dark (see _corr) the match is only a near miss, whatever its share: a flat patch, or a real
    bar where the frame is too thin to draw exactly."""
    if r is None or not r[1]:
        return None
    share = r[0] / r[1]
    if share >= SHARE_OK and _corr(img, r[2], r[3], s, r[4]) < CORR_OK:
        share = SHARE_OK - 0.01
    return Bar(r[2], r[3], share, r[4])


def _find_bars(img, val, runs, s, kernels):
    """Every bar of this scale that the frame drawing explains: [Bar], and how many near misses (a bar-like spot
    that agrees at least SHARE_LIKE but not SHARE_OK), or None when the picture is too busy to check them all."""
    spots = _spots(runs, val, s)
    if len(spots) > MAX_CANDIDATES:
        return None, 0
    bars, near = [], 0
    pitch = PITCH * s
    for x, y in sorted(spots, key=lambda p: p[1]):
        if any(abs(b.ox - x) <= 2 + s and abs(b.oy - y) <= 1.5 + s for b in bars):
            continue                                        # the same bar again (a second dark row)
        r = None
        ref = bars[0] if bars else None
        if ref is not None and abs(ref.ox - x) <= 2 + s:
            k = round((y - ref.oy) / pitch)
            if k and abs(y - ref.oy - k * pitch) <= 1.5 + s:
                r = _fit_near(img, ref.ox, ref.oy + k * pitch, s, ref.kernel)
                if r:
                    r = (r[0], r[1], r[2], r[3], ref.kernel)
        if r is None:
            r = _fit(img, x, y, s, kernels)
        b = _bar_of(img, r, s)
        if b is None:
            continue
        if b.share >= SHARE_OK:
            bars.append(b)
        elif b.share >= SHARE_LIKE:
            near += 1
    return bars, near


# ---------------------------------------------------------------- the column
def _column(bars, s):
    """(rows, stray): the bars standing in the biggest evenly spaced column as {row index: Bar} (the first bar
    is row 0), and how many bars are not in it."""
    pitch = PITCH * s
    tol_x, tol_y = 0.75 + 0.25 * s, 0.75 + 0.25 * s
    best = {}
    bars = sorted(bars, key=lambda b: b.oy)
    for ref in bars:
        col = {}
        for b in bars:
            k = round((b.oy - ref.oy) / pitch)
            if abs(b.ox - ref.ox) <= tol_x and abs(b.oy - ref.oy - k * pitch) <= tol_y and k not in col:
                col[k] = b
        if len(col) > len(best):
            best = col
    if not best:
        return {}, 0
    lo = min(best)
    return {k - lo: b for k, b in best.items()}, len(bars) - len(best)


def _rows(a, b):
    """The screen rows [a, b) covers, at least one: (first, last)."""
    r0 = int(math.floor(a + 1e-6))
    return r0, max(r0, int(math.ceil(b - 1e-6)) - 1)


def _edge(val, ox, y0, s, top):
    """Share of the bar's width along which the panel's border is seen at its inner line (first pixel row y0): the
    inner line is lighter than the outer line one design pixel further out (above for the top line, below for the
    bottom one), AND it stands out from the panel's inside next to it or the outer line from the map beyond it
    (so the edge of a window that ends right there, flat on both sides, is not a border)."""
    x0, x1 = int(round(ox + 2 * s)), int(round(ox + 65 * s))
    inner = _rows(y0, y0 + s)
    outer = _rows(y0 - s, y0) if top else _rows(y0 + s, y0 + 2 * s)
    inside = _rows(y0 + s, y0 + 2 * s) if top else _rows(y0 - 2 * s, y0 - s)
    beyond = _rows(y0 - 2 * s, y0 - s) if top else _rows(y0 + 2 * s, y0 + 3 * s)
    lo, hi = min(r[0] for r in (inner, outer, inside, beyond)), max(r[1] for r in (inner, outer, inside, beyond))
    if lo < 0 or hi >= val.shape[0] or x0 < 0 or x1 > val.shape[1] or x1 <= x0:
        return 0.0
    pick = lambda r: val[r[0]:r[1] + 1, x0:x1].astype(np.int16)
    line, out = pick(inner).max(axis=0), pick(outer).min(axis=0)
    ins, far = pick(inside).mean(axis=0), pick(beyond).mean(axis=0)
    ok = (line - out >= EDGE_DIFF) & ((line - ins >= EDGE_INSIDE) | (np.abs(out - far) >= EDGE_BEYOND))
    return float(np.mean(ok))


def _covered(img, x0, y0, x1, y1, s):
    """Is there a pure-black block (the overlay's own window is blacked out in the grab) inside the rectangle?
    The rectangle is the bar column inside the panel (the panel is a see-through light grey: never pure black),
    never the name area (text is black there)."""
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(img.shape[1], int(x1)), min(img.shape[0], int(y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    z = (img[y0:y1, x0:x1].max(axis=2) == 0).view(np.uint8)
    k = max(3, int(round(COVER_SPAN * s)))
    if z.shape[0] < k or z.shape[1] < k:
        return False
    return bool(cv2.erode(z, np.ones((k, k), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0).any())


def _box(rows, s):
    """The list's box (x, y, w, h) in window pixels: from the widest left reach to the right line, one line above
    the top edge to one line under the bottom one."""
    first, last = rows[0], rows[max(rows)]
    top = first.oy - 6 * s
    return (int(round(first.ox - LEFT_MAX * s)), int(round(top)), int(round(BOX_W * s)),
            int(round(last.oy + 15 * s - top)))


def _extend(img, rows, s):
    """Look where the column's other rows would be (one row above the first, one below the last, and any gap) with
    the exact check, so a bar the first pass missed is still counted and a row that is half there is not skipped.
    -> (rows re-numbered from 0, number of spots that match partly: a row half hidden, a look-alike)."""
    ref = rows[0]
    pitch, kernel = PITCH * s, ref.kernel
    cache = {}

    def probe(k):
        if k not in cache:
            r = _fit_near(img, ref.ox, ref.oy + k * pitch, s, kernel)
            cache[k] = _bar_of(img, r + (kernel,) if r else None, s)
        return cache[k]
    rows = dict(rows)
    grown = True
    while grown and len(rows) <= MAX_MEMBERS:
        grown = False
        lo, hi = min(rows), max(rows)
        for k in [lo - 1, hi + 1] + [k for k in range(lo + 1, hi) if k not in rows]:
            b = probe(k)
            if k not in rows and b and b.share >= SHARE_OK:
                rows[k], grown = b, True
    lo, hi = min(rows), max(rows)
    near = sum(1 for k in [lo - 1, hi + 1] + [k for k in range(lo + 1, hi) if k not in rows]
               if probe(k) and probe(k).share >= SHARE_LIKE)
    return {k - lo: b for k, b in rows.items()}, near


def _read_column(img, val, bars, near, s):
    """What a set of bars at scale s says: (count, box) when a whole list is seen, ("unclear", box) when something
    list-like is there but can't be trusted."""
    rows, stray = _column(bars, s)
    if not rows:
        return "unclear", None
    rows, more = _extend(img, rows, s)
    box = _box(rows, s)
    n = max(rows) + 1
    if stray or near or more or len(rows) != n or n > MAX_MEMBERS or n < 2:
        return "unclear", box
    first, last = rows[0], rows[n - 1]
    if _edge(val, first.ox, first.oy - 5 * s, s, True) < EDGE_SHARE or             _edge(val, last.ox, last.oy + 14 * s, s, False) < EDGE_SHARE:
        return "unclear", box
    if _covered(img, first.ox - 3 * s, first.oy - 4 * s, first.ox + 70 * s, last.oy + 13 * s, s):
        return "unclear", box
    return n, box


# ---------------------------------------------------------------- the reader
def _region(last, shape):
    """The part of the picture round the last box to look in first (x0, y0, x1, y1), or None."""
    if last is None or not last.box or not last.scale:
        return None
    x, y, w, h = last.box
    s = last.scale
    x0, y0 = max(0, int(x - PAD * s)), max(0, int(y - PAD * s))
    x1, y1 = min(shape[1], int(x + w + PAD * s)), min(shape[0], int(y + h + 2 * PAD * s))
    return (x0, y0, x1, y1) if x1 - x0 > 70 * s and y1 - y0 > 11 * s else None


def _look(img, val, scales, region):
    """The best look in a region (or the whole picture): (answer, box, scale) with answer a count, "unclear" or
    None (nothing list-like). The scales are tried in order; the first that has a bar decides."""
    if region:
        x0, y0, x1, y1 = region
        sub = np.ascontiguousarray(val[y0:y1, x0:x1])
    else:
        x0 = y0 = 0
        sub = val
    min_len = max(6, int(RUN_SHARE * 63 * min(s for s, _ in scales)))
    runs = [(y + y0, a + x0, b + x0) for y, a, b in
            _top_runs(sub, min_len, max(_dark_for(s) for s, _ in scales), min(_bright_for(s) for s, _ in scales))]
    like = None
    for s, kernels in scales:
        found = _find_bars(img, val, runs, s, kernels)
        if found[0] is None:
            return "unclear", None, s
        bars, near = found
        if bars:
            ans, box = _read_column(img, val, bars, near, s)
            return ans, box, s
        if near and like is None:
            like = s
    weak = next((s for s, _ in scales if WEAK_SCALES[0] <= s < WEAK_SCALES[1]), None)
    if like or weak:
        return "unclear", None, like or weak                  # something list-like, or a size where "no list" can't be told
    return None, None, None


def read_party_v3(img, scale_hint=None, last=None, hsv=None):
    """The party size seen on `img` (the game window, BGR) as a PartyLook.

    scale_hint: (mode, scale) from ui_layout.mode_from_gh, or None. last: the previous PartyLook, or None (hand
    back every answer). hsv: the same picture as HSV, if it's at hand already.
    A whole list read gives its count. Nothing like a list means you're on your own (1), unless you were in a
    party a moment ago: then that count is kept for HOLD_LOOKS looks ("held"). A list that can't be read (covered,
    a row missing, more than six) is None, "unclear", or the held count. Never a guess."""
    h = img.shape[0]
    val = (cv2.cvtColor(img, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2]
    scales = _scales(h, scale_hint, last)
    prev = last.count if last is not None and last.count and last.count >= 2 else None
    gone = last.gone if last is not None else 0
    region = _region(last, img.shape)
    ans, box, s = _look(img, val, scales, region) if region else (None, None, None)
    if ans is None:
        ans, box, s = _look(img, val, scales, None)
    if isinstance(ans, int):
        return PartyLook(ans, "ok", s, box, 0)
    scale = s or (last.scale if last is not None else None)
    if ans == "unclear":
        if prev:
            return PartyLook(prev, "held", scale, box, gone)
        return PartyLook(None, "unclear", scale, box, 0)
    # nothing like a list: the box is only where a list was SEEN, so there is none to give
    if prev and gone + 1 < HOLD_LOOKS:
        return PartyLook(prev, "held", scale, None, gone + 1)
    return PartyLook(1, "ok", None, None, 0)
