"""Reading the character's level from the status bar's "LV." badge: white digits on orange tiles, one tile per digit,
far left of the EXP bar. It is only used when the bar shows no EXP number, so the level can't come from
number and % (see ExpTracker.observe_percent).

The level is never guessed: the label "LV" has to be there, a complete box has to be found, and every digit is read
by drawing the game's own badge digits (assets/fonts_v2/badge.json) at the window's UI scale and comparing them with
the tile's pixels. A digit counts only when one digit fits the pixels and every other digit clearly doesn't; a box
that is covered, cut off or blurred past that gives nothing. The character's name is never read as text
(name_print below keeps only a small picture of it, in memory, to tell characters apart).
"""
import json
from pathlib import Path

import cv2
import numpy as np

FONT_FILE = Path(__file__).resolve().parent / "assets" / "fonts_v2" / "badge.json"
TILE_W, TILE_H, TILE_STEP = 11, 15, 12      # a digit's orange tile on the design area, and how far the next one starts
BOX_H = 13                                   # the orange tile itself is 13 rows tall (the tile's 15-row picture has 2 rows of shade below)
MIN_RANGE = 120                              # the tile's white must be at least this much brighter than its orange
SIDE = 3                                     # how many design pixels right of the badge must be plain panel (nothing covering a tile)
SIDE_FLAT = 10                               # ...each row of that strip varying no more than this
GAP_2 = 15.0                                 # from the "LV" letters to the first tile, in design pixels, for a two-digit badge (measured
GAP_TOL = 2.0                                # at every size; the badge is centred, so one more digit starts 6 px further left) +- this
E_OK = 0.006                                 # the best digit's mean squared error (of 0..1 ink) can be at most this...
E_RATIO, E_GAP = 3.0, 0.012                  # ...and the next digit's at least this many times and this much more
COARSE = (-1.0, -0.5, 0.0, 0.5, 1.0)         # where the tile sits against the box found (pixels), first tried at these...
FINE = (-0.25, -0.125, 0.0, 0.125, 0.25)     # ...then this far around the best of them
_font = {}


def font():
    """{digit: 15 x 11 float picture (1 = white)} from the badge font asset."""
    if not _font:
        data = json.loads(FONT_FILE.read_text())
        for ch, g in data["glyphs"].items():
            _font[ch] = np.array([[c == "X" for c in row] for row in g["rows"]], np.float32)
    return _font


def render_tile(mask, scale, x0, y0, ox, oy, h, w):
    """The tile's white at `scale` in an h x w picture, its top left at (x0 + ox, y0 + oy): copied block for block at a whole
    scale (the game does that at 1x and Ideal 2x), smoothed at any other."""
    if abs(scale - round(scale)) < 0.01:
        k = int(round(scale))
        big, out = np.kron(mask, np.ones((k, k), np.float32)), np.zeros((h, w), np.float32)
        px, py = int(round(x0 + ox)), int(round(y0 + oy))
        sx0, sy0 = max(0, -px), max(0, -py)
        ex, ey = min(big.shape[1], w - px), min(big.shape[0], h - py)
        if ex > sx0 and ey > sy0:
            out[py + sy0:py + ey, px + sx0:px + ex] = big[sy0:ey, sx0:ex]
        return out
    m = np.array([[1 / scale, 0, 0.5 / scale - 0.5 - (x0 + ox) / scale],
                  [0, 1 / scale, 0.5 / scale - 0.5 - (y0 + oy) / scale]], np.float32)
    return cv2.warpAffine(mask, m, (w, h), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def tile_error(mask, pic, scale, x0, ox, oy, y0=1):
    """How far the drawn tile is from pic (white coverage 0..1), over the tile's inside, as a mean squared error."""
    drawn = render_tile(mask, scale, x0, y0, ox, oy, *pic.shape)
    # (only the tile's inside: the digit never comes near the edge, and what is outside the tile isn't the same everywhere)
    c0, c1 = max(0, int(x0 + scale)), int(x0 + (TILE_W - 1) * scale)
    r0, r1 = max(0, int(y0 + 2 * scale)), int(y0 + (BOX_H - 1) * scale)
    return float(((drawn[r0:r1, c0:c1] - pic[r0:r1, c0:c1]) ** 2).mean())


def best_fit(mask, pic, scale, x0, y0=1):
    """The smallest tile_error over where the tile may sit against the box found."""
    whole = abs(scale - round(scale)) < 0.01
    coarse = (-2.0, -1.0, 0.0, 1.0, 2.0) if whole else COARSE
    e, ox, oy = min((tile_error(mask, pic, scale, x0, ox, oy, y0), ox, oy) for ox in coarse for oy in coarse)
    if whole:
        return e
    return min(tile_error(mask, pic, scale, x0, ox + dx, oy + dy, y0) for dx in FINE for dy in FINE)


def find_badge(bgr, x0, cy, gh):
    """(crop of the badge row, (x, y, w, h) of the orange box in it), or None.
    x0, cy, gh: where the EXP number starts, the bar's middle line, the digit height."""
    if bgr is None or not bgr.size or gh < 5:
        return None
    # (the badge sits a little lower than the EXP text, in the middle of the status panel)
    left, top = max(0, int(x0 - 72 * gh)), max(0, int(cy - 1.6 * gh))
    right, bottom = max(0, int(x0 - 38 * gh)), min(bgr.shape[0], int(cy + 3.6 * gh))
    roi = bgr[top:bottom, left:right]
    if roi.shape[0] < 3 * gh or roi.shape[1] < 6 * gh:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    orange = ((hsv[..., 0] >= 4) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 100) & (hsv[..., 2] >= 130)).astype(np.uint8)
    # (the digits' white strokes cut the box up: close the gaps, or it falls apart into pieces)
    orange = cv2.morphologyEx(orange, cv2.MORPH_CLOSE, np.ones((max(2, round(0.5 * gh)),) * 2, np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(orange, connectivity=8)
    boxes = [(int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:]
             if 1.2 * gh <= h <= 2.7 * gh and 0.6 * gh <= w <= 6 * gh and area >= 0.3 * w * h]
    # A box touching the edge of what was looked at is cut off (covered by the overlay or a window).
    boxes = [b for b in boxes if b[1] > 0 and b[1] + b[3] < roi.shape[0] and b[0] > 0 and b[0] + b[2] < roi.shape[1]]
    pad = max(1, round(0.35 * gh))

    def crop(x, y, w, h):
        row = roi[max(0, y - pad):y + h + pad, max(0, x - int(7 * gh)):x + w + max(pad, int(0.7 * gh) + 1)]
        return row.copy(), (min(x, int(7 * gh)), min(y, pad), w, h)

    if len(boxes) > 1:          # (another orange icon in reach: only the one with the "LV." beside it can be the badge)
        boxes = [b for b in boxes if has_label(*crop(*b), gh)]
    return crop(*boxes[0]) if len(boxes) == 1 else None


def label_letters(row, box, gh):
    """(left, right) of the white "LV" letters left of the box, in row pixels, or None: white letters about as tall as the digits."""
    bx = box[0]
    hsv = cv2.cvtColor(row[:, :max(1, bx - round(0.25 * gh))], cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 100) & (hsv[..., 2] >= 170)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    letters = [(x, w) for x, _, w, h, _ in stats[1:] if 0.9 * gh <= h <= 2.2 * gh and w <= 2.5 * gh]
    return (min(x for x, _ in letters), max(x + w for x, w in letters)) if letters else None


def label_span(row, box, gh):
    """How wide the "LV" letters left of the box are, in digit heights (0 if there are none). The game's measure about 2.4;
    other orange icons with a mark beside them 1.3-1.7."""
    got = label_letters(row, box, gh)
    return (got[1] - got[0]) / gh if got else 0.0


def has_label(row, box, gh):
    """Is there an "LV." left of the box? Checked by shape, not by reading it: at small window sizes the letters are
    too few pixels to read, and touch each other."""
    return label_span(row, box, gh) >= 2.0


def sides_clear(row, box, scale, gh, n):
    """Is nothing covering a tile? A window or cursor over the first or last tile leaves the tiles still showing looking like a
    shorter badge ("3" for "32"). The badge is centred, so with n tiles the first starts a known distance from the "LV"
    letters (a covered first tile moves it 12 px, a covered last one changes n); and the panel right of the last tile is plain."""
    bx, by, w, h = box
    letters = label_letters(row, box, gh)
    if letters is None or abs((bx - letters[1]) / scale - (GAP_2 + 6 * (2 - n))) > GAP_TOL:
        return False
    strip = row[by:by + h, bx + w + 1:bx + w + 1 + max(2, int(round(SIDE * scale)))].astype(np.float32)
    return strip.shape[1] >= 2 and bool((strip.max(axis=1) - strip.min(axis=1)).max() <= SIDE_FLAT)


def read_digits(row, box, scale, gh):
    """The digits of the badge at one scale: (text, errors) or None when the box doesn't fit that scale. text has "?"
    where a tile's digit isn't certain."""
    bx, by, w, h = box
    n = int(round((w / scale + 1) / TILE_STEP))
    if not 1 <= n <= 3 or abs((TILE_STEP * n - 1) * scale - w) > 1.5 or abs(BOX_H * scale - h) > 1.5:
        return None
    blue = row[by - 1:by + h + 1, bx - 1:bx + w + 1, 0].astype(np.float32)
    if blue.shape != (h + 2, w + 2):
        return None
    if not sides_clear(row, box, scale, gh, n):
        return "?" * n, ()
    lo, hi = float(np.percentile(blue, 10)), float(np.percentile(blue, 99.5))
    if hi - lo < MIN_RANGE:
        return "?" * n, ()
    pic = np.clip((blue - lo) / (hi - lo), 0, 1)
    text, errors = "", []
    for i in range(n):
        got = sorted((best_fit(mask, pic, scale, 1 + TILE_STEP * i * scale), ch) for ch, mask in font().items())
        (e1, d), (e2, _) = got[0], got[1]
        ok = e1 <= E_OK and e2 >= E_RATIO * e1 and e2 - e1 >= E_GAP
        text += d if ok else "?"
        errors.append(round(e1, 4))
    return text, tuple(errors)


def read_level(bgr, x0, cy, gh, scales, look=None):
    """The level (1-250) read from the badge, or None. scales: the UI scales worth trying (ui_layout.candidate_scales).
    look (a dict) gets the badge picture and why."""
    if look is not None:
        look.clear()
        look.update(image=None, why="")

    def fail(why):
        if look is not None:
            look["why"] = why
        return None

    found = find_badge(bgr, x0, cy, gh)
    if found is None:
        return fail("no complete level badge in sight")
    row, box = found
    if look is not None:
        look["image"] = row
    if not has_label(row, box, gh):
        return fail("the LV label wasn't there")
    reads = {}
    for scale in sorted(set(round(float(s), 4) for s in scales)):
        got = read_digits(row, box, scale, gh)
        if got:
            reads[scale] = got[0]
    if not reads:
        return fail("the level badge doesn't fit any UI size")
    texts = set(reads.values())
    if len(texts) != 1:
        return fail("the badge reads differently at different UI sizes")
    text = texts.pop()
    if "?" in text:
        return fail("the badge's digits aren't clear enough")
    level = int(text)
    if text[0] == "0" or not 1 <= level <= 250:
        return fail("the badge doesn't show a level")
    return level


# ---- telling characters apart (never by reading the name: only a small picture of it, kept in memory)
PRINT_SIZE = (96, 12)        # the name row's white pixels, shrunk to this many columns and rows
MIN_INK = 8.0                # a name row with less white than this (in shrunk pixels) isn't there
SAME_NAME = 0.75             # how alike every part of two prints must be (correlation) to be the same name
CHUNK = 12                   # columns of a print compared at a time: one different letter must show


def name_print(bgr, x0, cy, gh):
    """A small picture of the character's name (the white text under the class), or None when there
    is no text to see (covered, off screen). x0, cy, gh as for find_badge. It is only ever compared
    with another print from this session: it is not saved, shown or sent, and the name isn't read."""
    if bgr is None or not bgr.size or gh < 5:
        return None
    left, right = int(x0 - 50.5 * gh), int(x0 - 38 * gh)
    top, bottom = int(cy + 0.9 * gh), int(cy + 2.6 * gh)
    if left < 0 or top < 0 or right > bgr.shape[1] or bottom > bgr.shape[0] or right - left < 4 * gh:
        return None
    hsv = cv2.cvtColor(bgr[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 90) & (hsv[..., 2] >= 170)).astype(np.float32)
    small = cv2.resize(white, PRINT_SIZE, interpolation=cv2.INTER_AREA) * 4
    return small if small.sum() >= MIN_INK else None


def same_name(a, b):
    """Are two prints of the same name? (False for any print that isn't there.) Strict on purpose: a wrong
    "same" would carry one character's level over to another, a wrong "different" only asks again. Every
    part of the row has to match, so "Bean2" and "Bean3" differ even though most of the row is alike."""
    if a is None or b is None:
        return False
    best = 0.0
    for shift in (-2, -1, 0, 1, 2):          # (the text may sit a pixel or so to either side)
        x, y = (a[:, shift:], b[:, :b.shape[1] - shift]) if shift >= 0 else (a[:, :shift], b[:, -shift:])
        worst = 1.0
        for c in range(0, x.shape[1] - CHUNK + 1, CHUNK // 2):
            xc, yc = x[:, c:c + CHUNK], y[:, c:c + CHUNK]
            nx, ny = np.linalg.norm(xc), np.linalg.norm(yc)
            if nx >= 1.0 or ny >= 1.0:         # (nothing to compare where neither has any text)
                worst = min(worst, float((xc * yc).sum()) / (max(nx, 1e-6) * max(ny, 1e-6)))
        best = max(best, worst)
    return best >= SAME_NAME


def name_match(old, new):
    """How a new print of the name row compares with the one kept: "same" (every part matches, as same_name),
    "alike" (what is visible in the new one matches, but some of the old one's text isn't visible: something
    covers part of the name), "different" (something visible doesn't match), or "hidden" (no new print, or so
    little of the name shows that it can't tell, with nothing that shows contradicting it)."""
    if new is None:
        return "hidden"
    if old is None:
        return "different"
    best, few = "", False
    for shift in (-2, -1, 0, 1, 2):
        x, y = (old[:, shift:], new[:, :new.shape[1] - shift]) if shift >= 0 else (old[:, :shift], new[:, -shift:])
        kinds = []                            # per part: its match (a number), or None = text before and none now
        for c in range(0, x.shape[1] - CHUNK + 1, CHUNK // 2):
            xc, yc = x[:, c:c + CHUNK], y[:, c:c + CHUNK]
            nx, ny = np.linalg.norm(xc), np.linalg.norm(yc)
            if ny >= 1.0:
                kinds.append(float((xc * yc).sum()) / (max(nx, 1e-6) * ny))
            elif nx >= 1.0:
                kinds.append(None)            # (hidden, or a shorter name)
            else:
                kinds.append(-1.0)            # (empty both: not counted)
        good = sum(1 for k in kinds if k is not None and k >= SAME_NAME)
        missing = [i for i, k in enumerate(kinds) if k is None]
        # (a part cut through by the cover only half matches: it is let off when it sits right next to a hidden one)
        bad = [i for i, k in enumerate(kinds) if k is not None and 0 <= k < SAME_NAME]
        if any(not any(abs(i - j) <= 2 for j in missing) for i in bad) or (bad and not missing):
            continue                          # (something that shows doesn't match)
        if good < 3:
            few = few or (bool(missing) and good + len(bad) < 3)    # (almost nothing shows: can't tell)
            continue
        found = "alike" if missing else "same"
        if found == "same" or not best:
            best = found
    return best or ("hidden" if few else "different")
