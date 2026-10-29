"""Reader v2 for the mesos box (shadow only: today's reader is what the player sees and what is shared).

The box is a rectangle with a dark one-pixel frame on the game's 1024 x 768 design area (143 x 14 pixels inside
for the inventory, 91 x 14 for the shop's own box), scaled with the rest of the UI. The number in it is right
aligned and drawn in a small font (assets/fonts_v2/mesos.json, 5 x 8 pixels, one pixel strokes, no outline, the
comma hangs two rows lower). So:
  1. Find boxes by their frame: at the game's own size (the picture shrunk by the UI scale) a box is four dark
     lines at the right distances (find_boxes). Nothing about where the inventory sits is assumed, and nothing is
     learned from the player's screen.
  2. Read the number: the picture is turned into "how much darker than its surroundings", so the same digits read
     on a white box and on the see-through inventory over a busy scene, then drawn digits (glyphs.py) are
     compared pixel by pixel, cell by cell, from the last digit leftwards (read_box).
A number is accepted only when every digit is the single clear best fit and explains all the dark in its cell,
the commas are where they belong and nothing else is dark in the rest of the box (so a window or the cursor over
the number's start can't leave a shorter number). Anything doubtful is None, never a guess.
Which box is the player's is never guessed: more than one box in the picture = no answer.
Pure numpy and OpenCV: no screen, no files except the font asset.
"""
import math
from collections import namedtuple

import cv2
import numpy as np

import glyphs

FONT = "mesos"
INNER_H = 14                        # the box's height inside its frame (game pixels); the frame lines are 15 apart
LINE_GAP = INNER_H + 1
LINE_WIDTHS = (144, 92)             # the frame's width from line to line: the inventory's box, the shop's box
FRAME_MIN = 10                      # a frame line is this much darker (grey levels, mean along it) than the rows beside it
MAX_BOXES = 4                       # more frames than this in one picture: not looked at any further

Box = namedtuple("Box", "rect scale line_w")          # rect: (x, y, w, h) inside the frame, in picture pixels
MesosLook = namedtuple("MesosLook", "boxes value why")      # boxes: ((rect, value or None), ...); why: ok / not_found / unclear


# ---------------------------------------------------------------- finding the frame
def _contrast(small, axis):
    """How much darker each pixel is than the mean of its two neighbours along `axis` (0 rows, 1 columns)."""
    out = np.zeros_like(small)
    if axis == 0:
        out[1:-1] = 0.5 * (small[:-2] + small[2:]) - small[1:-1]
    else:
        out[:, 1:-1] = 0.5 * (small[:, :-2] + small[:, 2:]) - small[:, 1:-1]
    return out


COIN_LO, COIN_HI = (12, 70, 100), (35, 255, 255)   # the gold coin the game draws left of the box (HSV)
COIN_X, COIN_Y = (-15.5, -5.0), (1.0, 11.5)         # ...where it is, in game pixels from the box's inside corner
COIN_SHARE, COIN_AROUND = 0.30, 0.10                # gold must fill this much of that spot and no more than this much of the margin round it


def _coin_left_of(img, x, y, scale):
    """Is there the box's gold coin where it sits, left of a frame whose inside starts at (x, y)? Slots on the
    potion bar and other frames of the same size are not told apart by their frame alone."""
    x0, x1 = int(round(x + COIN_X[0] * scale)), int(round(x + COIN_X[1] * scale))
    y0, y1 = int(round(y + COIN_Y[0] * scale)), int(round(y + COIN_Y[1] * scale))
    m = int(round(3 * scale))
    if x0 - m < 0 or y0 - m < 0 or y1 + m > img.shape[0] or x1 > img.shape[1] or x1 <= x0:
        return False
    gold = cv2.inRange(cv2.cvtColor(img[y0 - m:y1 + m, x0 - m:x1 + m], cv2.COLOR_BGR2HSV), COIN_LO, COIN_HI) > 0
    inner = gold[m:-m, m:-m]
    around = (gold.sum() - inner.sum()) / max(gold.size - inner.size, 1)
    return inner.mean() >= COIN_SHARE and around <= COIN_AROUND


def find_boxes(img, scale, line_widths=LINE_WIDTHS, thr=FRAME_MIN):
    """Every mesos box frame in `img` at this UI scale: [Box]. The picture is shrunk to the game's own size, where
    a frame is a rectangle of dark one pixel lines: top and bottom 15 apart, left and right line_w apart; its
    four lines' mean contrast is the score and the weakest line decides."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) if img.ndim == 3 else img.astype(np.float32)
    if min(gray.shape[:2]) < scale:                       # (a picture squeezed to a sliver holds no box, and can't be shrunk)
        return []
    small = gray if abs(scale - 1.0) < 0.01 else cv2.resize(gray, None, fx=1.0 / scale, fy=1.0 / scale, interpolation=cv2.INTER_AREA)
    rows, cols = small.shape
    ch, cv = _contrast(small, 0), _contrast(small, 1)
    found = []
    for width in line_widths:
        best = None
        for w in (width - 1, width, width + 1):
            if cols <= w + 2 or rows <= LINE_GAP + 2:
                continue
            th = cv2.boxFilter(ch, -1, (w + 1, 1), anchor=(0, 0), normalize=True, borderType=cv2.BORDER_CONSTANT)
            tv = cv2.boxFilter(cv, -1, (1, LINE_GAP + 1), anchor=(0, 0), normalize=True, borderType=cv2.BORDER_CONSTANT)
            nr, nx = rows - LINE_GAP, cols - w
            score = np.minimum(np.minimum(th[:nr, :nx], th[LINE_GAP:LINE_GAP + nr, :nx]), np.minimum(tv[:nr, :nx], tv[:nr, w:w + nx]))
            if best is None:
                best = np.full(score.shape, -1e9, np.float32), np.zeros(score.shape, np.int16)
            if score.shape != best[0].shape:
                score = score[:best[0].shape[0], :best[0].shape[1]]
            better = score > best[0][:score.shape[0], :score.shape[1]]
            best[0][:score.shape[0], :score.shape[1]][better] = score[better]
            best[1][:score.shape[0], :score.shape[1]][better] = w
        if best is None:
            continue
        score, ws = best
        work = score.copy()
        for _ in range(3 * MAX_BOXES):                    # the best spot, then the ones away from it
            i = int(work.argmax())
            r, x = divmod(i, work.shape[1])
            if work[r, x] < thr or len(found) >= MAX_BOXES:
                break
            w = int(ws[r, x])
            work[max(r - LINE_GAP, 0):r + LINE_GAP + 1, max(x - 8, 0):x + w + 9] = -1e9
            if _coin_left_of(img, int(round((x + 1) * scale)), int(round((r + 1) * scale)), scale):
                found.append((float(work[r, x]), x, r, w))
    out, taken = [], []
    for _, x, r, w in sorted(found, reverse=True):
        if any(x < tx + tw and tx < x + w and r < ty + LINE_GAP and ty < r + LINE_GAP for tx, ty, tw in taken):
            continue                                      # (a stroke of the digits inside a frame looks like a shorter frame)
        taken.append((x, r, w))
        # frame lines at game column x and x + w, rows r and r + 15: the inside starts one pixel in
        out.append(Box((int(round((x + 1) * scale)), int(round((r + 1) * scale)), int(round((w - 1) * scale)), int(round(INNER_H * scale))),
                       scale, w))
    return out


# ---------------------------------------------------------------- reading the number
E_OK = 0.45                         # a character is a fit when this share (or less) of its ink is missing or has extra beside it
E_FIRST = 0.30                      # a cell that fits this well where the grid puts it is not moved
E_GAP = 0.12                        # ...and the next best character must be this much worse
E_COMMA = 0.65                      # a comma is small: it counts as there at this error
INK_MIN = 8                         # (a character's ink mass must be this big for that share to mean anything)
SOLID = 0.6                         # ink this strong anywhere (even one pixel) means the part is not empty
EMPTY = 0.06                        # the rest of the box is empty when its mean ink is below this
NEAR_STEPS = (0.25, 0.125)           # ...a cell on the grid needs less
STEPS = (0.5, 0.25, 0.125)          # sub-pixel refinement: game pixels moved at a time


def ink_picture(gray, scale):
    """How much of each pixel is ink, about 0..1: how much darker it is than the lightest of its surroundings, as a
    share of that (the ink is black; on the see-through box it is less than fully there). The same digits then read
    on a white box and on the see-through inventory over a scene, and a smoothed (scaled up) stroke shows its
    partial coverage."""
    r = int(math.ceil(2.5 * scale))
    behind = cv2.dilate(gray, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return np.clip((behind - gray) / np.maximum(behind, 25.0), 0.0, 1.5).astype(np.float32)


def clean_ink(ink, x_from, x_to, y_top, y_text, scale):
    """The ink picture with the scenery's own texture taken out. The strip of the box above the text holds
    only what lies behind it (white on the solid box, the scene on the see-through one), so how much "ink" they
    show is the noise level; everything under it is dropped and the rest stretched so the text's own strokes
    reach 1. x_from..x_to: the box's text part; y_top: where the empty strip above the text starts (just inside the frame); y_text:
    where the text rows start (picture pixels)."""
    xa, xb = max(int(x_from), 0), min(int(x_to), ink.shape[1])
    top = ink[max(int(y_top), 0):int(y_text - 0.5 * scale), xa:xb]
    bg = top.ravel()
    if bg.size < 8:
        return ink
    tau = float(np.clip(bg.mean() + 2.0 * bg.std(), 0.0, 0.45))
    peak = float(np.percentile(ink[int(y_text):int(y_text + 11 * scale), xa:xb], 99.5))
    return np.clip((ink - tau) / max(peak - tau, 0.25), 0.0, 1.2).astype(np.float32)


def _window(ch, cx, cy, scale):
    """The pixels that belong to the cell whose corner is (cx, cy): its fill and half a pixel around. (A comma is
    only its two ink columns: the digit before it ends in the comma cell's first column.)"""
    if ch == ",":
        return (int(math.floor(cx + 2.5 * scale)), int(math.floor(cy + 6.5 * scale)),
                int(math.ceil(3.0 * scale)) + 1, int(math.ceil(5.0 * scale)) + 1)
    return (int(math.floor(cx + 0.5 * scale)), int(math.floor(cy + 0.5 * scale)),
            int(math.ceil(6.0 * scale)) + 1, int(math.ceil(11.0 * scale)) + 1)


def _error(font, ch, ink, cx, cy, scale, kernel):
    """How badly character ch drawn with its cell's corner at (cx, cy) explains the ink picture: the share of its
    own ink that is missing or has something extra beside it (0 = exactly), or None when it lies off the picture."""
    x0, y0, w, h = _window(ch, cx, cy, scale)
    if x0 < 0 or y0 < 0 or y0 + h > ink.shape[0] or x0 + w > ink.shape[1]:
        return None
    a = glyphs.draw(font, ch, scale, cx - x0, cy - y0, (h, w), kernel)[0]
    mass = float(a.sum())
    if mass < (3.0 if ch == "," else INK_MIN):
        return None
    real = ink[y0:y0 + h, x0:x0 + w]
    return float(np.abs(a - real * float(np.clip(mass / max(float(real.sum()), 1e-6), 0.7, 1.6))).sum()) / mass


def _cell_corner(ox, oy, scale, k):
    """Where the k-th cell from the right (0 = last digit) starts, given the last digit's cell corner: digits are
    6 game pixels apart and each comma slot (after every 3 digits) takes 4 more."""
    return ox - scale * (6 * k + 4 * (k // 3)), oy


def _best(font, chars, ink, cx, cy, scale, kernel, near=False):
    """[(error, char)] best first, for every character in chars that can be drawn there. near: the three best are
    each moved a little (up to a game pixel) to where they fit best first (the cells sit where the grid says, to a
    fraction of a pixel)."""
    out = [(e, ch, cx, cy) for ch in chars for e in [_error(font, ch, ink, cx, cy, scale, kernel)] if e is not None]
    out.sort()
    if near and not (len(out) > 1 and out[0][0] <= E_FIRST and out[1][0] - out[0][0] >= E_GAP):    # (a clear fit as it stands needs no moving)
        out = sorted((r[0], ch, r[1], r[2]) for e, ch, x, y in out[:3] for r in [_refine(font, ch, ink, x, y, scale, kernel, NEAR_STEPS)] if r)
    return [(e, ch) for e, ch, _, _ in out]


def _sure(got):
    """The one character that clearly fits, or None."""
    if not got or got[0][0] > E_OK or (len(got) > 1 and got[1][0] - got[0][0] < E_GAP):
        return None
    return got[0][1]


def _comma_part(cx, cy, scale):
    """The picture part a comma's two ink columns fall in (x0, x1, y0, y1)."""
    return cx + 2 * scale, cx + 6 * scale, cy + 6 * scale, cy + 12 * scale


def _empty(ink, x0, x1, y0, y1):
    """Is there no ink in this part of the picture?"""
    x0, y0 = max(int(x0), 0), max(int(y0), 0)
    x1, y1 = min(int(x1), ink.shape[1]), min(int(y1), ink.shape[0])
    part = ink[y0:y1, x0:x1]
    return x1 > x0 and y1 > y0 and float(part.mean()) < EMPTY and not (part > SOLID).any()


def _refine(font, ch, ink, cx, cy, scale, kernel, steps=STEPS):
    """Moves a cell's corner to where character ch fits best (to 1/8 game pixel): (error, cx, cy) or None."""
    err = _error(font, ch, ink, cx, cy, scale, kernel)
    if err is None:
        return None
    for st in steps:
        for _ in range(8):
            best = None
            for dx, dy in ((st, 0), (-st, 0), (0, st), (0, -st)):
                e = _error(font, ch, ink, cx + dx * scale, cy + dy * scale, scale, kernel)
                if e is not None and e < err - 1e-4 and (best is None or e < best[0]):
                    best = (e, dx * scale, dy * scale)
            if best is None:
                break
            err, cx, cy = best[0], cx + best[1], cy + best[2]
    return err, cx, cy


def _decode(font, ink, scale, kernel, ox, oy, left):
    """The number whose last digit's cell has its corner at (ox, oy): one cell at a time to the left, each the
    single clear best fit, until the number ends (nothing where a digit or comma would be) and the rest of the
    box (to `left`, past the coin) holds no ink. -> (value or None, why)."""
    chars = []
    for k in range(12):
        cx, cy = _cell_corner(ox, oy, scale, k)
        band = (cy + scale, cy + 11 * scale)
        if k and k % 3 == 0:                            # a comma first: it sits one slot to the right of this digit's cell
            comma_x = _cell_corner(ox, oy, scale, k - 1)[0] - 6 * scale
            got = _best(font, ",", ink, comma_x, cy, scale, kernel, near=True)
            if got and got[0][0] <= E_COMMA and not _empty(ink, *_comma_part(comma_x, cy, scale)):
                chars.append(",")
            elif _empty(ink, left, comma_x + 6 * scale, *band):
                break                                    # no comma: the number ended with k digits
            else:
                return None, "unsure"
        got = _best(font, "0123456789", ink, cx, cy, scale, kernel, near=True)
        ch = _sure(got)
        if ch is None:
            if k and k % 3 and _empty(ink, left, cx + 6 * scale, *band):
                break                                    # nothing left: the number ended
            return None, "unsure" if chars or (got and got[0][0] < 0.8) else "none"
        chars.append(ch)
    else:
        return None, "unsure"
    text = "".join(reversed(chars))
    digits = text.replace(",", "")
    if not digits or text[0] == "," or text[-1] == "," or f"{int(digits):,}" != text:
        return None, "unsure"
    return int(digits), "ok"


def _spots(font, ink, scale, nom_x, nom_y):
    """Where the last digit's cell may be: a quick look at the game's own size over the places it can be, best
    first: [(error, char, cx, cy)] in picture pixels."""
    small = cv2.resize(ink, None, fx=1.0 / scale, fy=1.0 / scale, interpolation=cv2.INTER_AREA)
    gx, gy = nom_x / scale, nom_y / scale
    x0, y0 = max(int(gx) - 4, 0), max(int(gy) - 3, 0)
    x1, y1 = min(int(gx) + 12, small.shape[1]), min(int(gy) + 14, small.shape[0])
    band = np.ascontiguousarray(small[y0:y1, x0:x1])
    out = []
    for ch in "0123456789":
        tpl = font.layers(ch)[0][1:1 + font.rows, 1:6].astype(np.float32)
        if band.shape[0] < tpl.shape[0] or band.shape[1] < tpl.shape[1]:
            continue
        m = cv2.matchTemplate(band, tpl, cv2.TM_SQDIFF) / float(tpl.sum())
        j, i = np.unravel_index(int(m.argmin()), m.shape)
        out.append((float(m[j, i]), ch, (x0 + i - 1) * scale, (y0 + j - 1) * scale))
    return sorted(out)[:3]


def read_box(img, box, hint=None):
    """The number in one mesos box (find_boxes): (value or None, (why, cx, cy, kernel)). img: BGR picture holding
    the box; hint: the (cx, cy, kernel) an earlier look read at (the same box: checked first, and costs little)."""
    font = glyphs.Font.load(FONT)
    x, y, w, h = box.rect
    s = box.scale
    pad = int(math.ceil(4 * s))
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + w + pad, img.shape[1]), min(y + h + pad, img.shape[0])
    ink = ink_picture(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32), s)
    ink = clean_ink(ink, (x - x0) + 14.0 * s, (x - x0) + w - 1.0 * s, (y - y0) + 0.3 * s, (y - y0) + 3.0 * s, s)
    left = (x - x0) + 14.0 * s                          # the number never reaches the coin on the left
    nom_x, nom_y = (x - x0) + w - 9.0 * s, (y - y0) + 2.0 * s     # the last digit's cell, nominally
    why = "none"
    if hint:
        value, _ = _decode(font, ink, s, hint[2], hint[0] - x0, hint[1] - y0, left)
        if value is not None:
            return value, ("ok", hint[0], hint[1], hint[2])
    for kernel in glyphs.kernels_for(s):
        tried = []
        for _, _, cx, cy in _spots(font, ink, s, nom_x, nom_y):
            for ch in "0123456789":
                got = _refine(font, ch, ink, cx, cy, s, kernel)
                if got is None or got[0] > E_OK or any(abs(got[1] - a) < 0.3 * s and abs(got[2] - b) < 0.3 * s for a, b in tried):
                    continue
                tried.append((got[1], got[2]))
                value, w2 = _decode(font, ink, s, kernel, got[1], got[2], left)
                if value is not None:
                    return value, ("ok", got[1] + x0, got[2] + y0, kernel)
                if w2 == "unsure":
                    why = "unsure"
    return None, (why, 0, 0, None)


def still_there(img, box):
    """Is the box found before still there, as it was? (Its coin and its four frame lines, where they were: much
    cheaper than finding it again, and a window drawn over part of it takes them away.)"""
    x, y, w, h = box.rect
    s = box.scale
    if x < 1 or y < 1 or y + h + 2 > img.shape[0] or x + w + 2 > img.shape[1]:
        return False
    if not _coin_left_of(img, x, y, s):
        return False
    gray = cv2.cvtColor(img[y - 2:y + h + 3, x - 2:x + w + 3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    top, bottom = gray[1:3].min(axis=0), gray[-3:-1].min(axis=0)             # (the line is a pixel or two thick, somewhere in there)
    inner = gray[3:-3, 3:-3]
    left, right = gray[3:-3, 1:3].min(axis=1), gray[3:-3, -3:-1].min(axis=1)
    mid = float(np.median(inner))
    return all(float(np.median(line)) < mid - FRAME_MIN * 0.6 or float(np.median(line)) < 100 for line in (top, bottom, left, right))


def read_mesos_v2(img, scales, last=None):
    """The player's mesos in a picture of the game window: MesosLook(boxes, value, why). `scales`: the UI scales
    worth trying, most likely first (ui_layout.candidate_scales); `last`: the Box and spot an earlier look read at (tried
    first). boxes: every frame found, each with its number or None. value: the number when exactly one box is
    there and it reads; with two or more boxes the player's can't be told: None, why "unclear". why: "ok",
    "not_found" (no box on the picture), "unclear" (a box that doesn't read)."""
    if last is not None and last[0].scale in [s for s in scales] and still_there(img, last[0]):
        value, spot = read_box(img, last[0], last[1])
        if value is not None:
            return MesosLook(((last[0].rect, value),), value, "ok"), (last[0], spot[1:])
    scales = [s for s in scales]
    order = ([last[0].scale] if last else []) + [s for s in scales if not last or abs(s - last[0].scale) > 0.01 * s]
    seen_any = False
    for s in order:
        boxes = find_boxes(img, s)
        if not boxes:
            continue
        seen_any = True
        got = []
        for b in boxes:
            value, spot = read_box(img, b, last[1] if last and last[0].scale == s else None)
            got.append((b, value, spot))
        if any(v is not None for _, v, _ in got):
            if len(got) == 1:
                return MesosLook(((got[0][0].rect, got[0][1]),), got[0][1], "ok"), (got[0][0], got[0][2][1:])
            return MesosLook(tuple((b.rect, v) for b, v, _ in got), None, "unclear"), None
        last_look = MesosLook(tuple((b.rect, None) for b, _, _ in got), None, "unclear")
    if seen_any:
        return last_look, None
    return MesosLook((), None, "not_found"), None
