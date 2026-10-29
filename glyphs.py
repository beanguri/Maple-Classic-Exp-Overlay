"""Glyph engine for reader v2: reads text the game draws in a bitmap font, by drawing it the same way.

The game paints some numbers (potion counts, mesos, the level badge) with a small pixel font on its 1024 x 768
design area and then scales the whole picture to the window: copied block for block at a whole step (UI Size
Ideal), smoothed bilinearly at any other size (UI Size Default). So a number on screen can be drawn exactly
here and compared with the real pixels at any scale, with nothing learned from the player's screen.

How a part builds on this (potion counts are the first, see read_count at the bottom):
  1. A font asset (assets/fonts_v2/<name>.json), made by tools/make_font.py from the test frames:
       {"name": ..., "fill": [[b, g, r] for each bitmap row], "outline": {"rule": "cross", "color": [b, g, r]},
        "glyphs": {"7": {"rows": ["XXXXXX", ".....X", ...], "adv": 8}, ...},
        "styles": {"keyboard": {"origin": [dx, dy]}, ...}}
     A glyph is a bitmap of its fill pixels ("X"), the same number of rows for every glyph, each row drawn in
     its own colour. The outline is every pixel next to the fill (rule "cross": left, right, above, below; rule "none": no outline,
     as in the mesos digits).
     A glyph's cell starts one pixel left of and one above its fill; "adv" is how far the next cell starts.
     Colours are BGR, as the screen is read. "styles" says where the text sits in a part's box, in game pixels.
  2. Font.load(name). render() draws a text at a scale and a sub-pixel position: alpha plus colour.
  3. read_run() finds and reads a run of characters near a spot. A cheap look at the game's own size suggests
     where the text starts; single characters drawn at the real scale (ahead of time, 1/8 pixel apart) are fitted
     there, to a fraction of a pixel; then the text is read cell by cell: a character is only accepted when it is
     the single best explanation of its pixels, drawn in the right colours, and the cell after the last one holds
     nothing that could be more of the text. If the quick way reads nothing, every character is tried over a wider
     area. A spot that read before (the hint) is checked first and costs a fraction. Anything doubtful gives None,
     never a guess.
  4. The part adds its own wrapper (like read_count) with its box, character set and length limits.
Pure numpy and OpenCV: no screen, no files except the font asset.
"""
import json
import math
from collections import namedtuple
from pathlib import Path

import cv2
import numpy as np

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts_v2"
_fonts = {}

TOL = 40                    # a pixel agrees with the drawing when no channel is further off than this
F_MIN = 0.97                # share of a reading's sure-ink pixels that must agree
F_WEAK = 0.45               # a longer reading this well supported means the text may go on: no answer
MIN_SURE = 12               # fewest sure-ink pixels a drawing needs to be judged
MARGIN = 6                  # a character must explain this many more pixels than the next one that also fits
FIRST_TRIES = 3             # the quick way: characters taken from the cheap look at game size...
GRID = (-1.0, -0.5, 0.0, 0.5, 1.0)                   # ...each tried this far (game pixels) either side of where it was found
WIDE_GRID = (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5)   # the slow way: every character this far either side of the spot expected
DESCENTS = 2                # ...and this many of them (the best fitting) moved to where they fit best

Run = namedtuple("Run", "text tx ty why kernel")   # text None unless sure; (tx, ty) first cell's corner; why; kernel


class Font:
    """A bitmap font as the game draws it (see the module text for the asset format)."""

    def __init__(self, data):
        self.name = data["name"]
        self.fill = np.array(data["fill"], np.float32)                 # [rows, 3]: colour of each bitmap row
        self.outline = np.array(data["outline"]["color"], np.float32)
        self.rule = data["outline"]["rule"]
        self.rows = len(self.fill)
        self.styles = data.get("styles", {})
        self.glyphs, self.adv = {}, {}
        for ch, g in data["glyphs"].items():
            bits = np.array([[c == "X" for c in row] for row in g["rows"]], np.uint8)
            assert bits.shape[0] == self.rows, (ch, bits.shape)
            self.glyphs[ch], self.adv[ch] = bits, g["adv"]
        self.chars = "".join(sorted(self.glyphs))
        self.height = self.rows + 2                                    # a cell: the fill plus an outline row each side
        self.max_adv = max(self.adv.values())
        self._layers, self._stack, self._ink = {}, {}, {}

    @classmethod
    def load(cls, name="potion_count"):
        if name not in _fonts:
            _fonts[name] = cls(json.loads((FONT_DIR / f"{name}.json").read_text()))
        return _fonts[name]

    def pens(self, text):
        """Where each character's cell starts (game pixels) and where the text ends."""
        out, x = [], 0
        for ch in text:
            out.append(x)
            x += self.adv[ch]
        return out, x

    def layers(self, text):
        """The text at game size (cached): ink coverage [h, w] (fill and outline, 0/1) and premultiplied BGR
        colour [h, w, 3]."""
        got = self._layers.get(text)
        if got is None:
            pens, _ = self.pens(text)
            width = max(p + 1 + self.glyphs[ch].shape[1] + 1 for p, ch in zip(pens, text))
            fill = np.zeros((self.height, width), np.uint8)
            for p, ch in zip(pens, text):
                g = self.glyphs[ch]
                fill[1:1 + self.rows, p + 1:p + 1 + g.shape[1]] |= g
            kernel = (np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], np.uint8) if self.rule == "cross"
                      else np.ones((3, 3), np.uint8))
            ring = np.zeros_like(fill) if self.rule == "none" else cv2.dilate(fill, kernel) & (1 - fill)
            colour = ring[:, :, None] * self.outline
            colour[1:1 + self.rows] += fill[1:1 + self.rows, :, None] * self.fill[:, None, :]
            got = self._layers[text] = ((fill | ring).astype(np.float32), colour.astype(np.float32))
        return got

    def stack(self, text):
        """layers() as one array [4, h, w]: coverage, then colour x coverage (b, g, r)."""
        got = self._stack.get(text)
        if got is None:
            cov, col = self.layers(text)
            got = self._stack[text] = np.concatenate([cov[None], np.transpose(col, (2, 0, 1))], axis=0)
        return got

    def ink(self, ch):
        """What the cheap look needs of one character: its ink mask, its grey ink centred on that mask, the
        mask's size."""
        got = self._ink.get(ch)
        if got is None:
            cov, col = self.layers(ch)
            grey = col.mean(axis=2) - 128.0
            n = float(cov.sum())
            got = self._ink[ch] = (cov, ((grey - (cov * grey).sum() / n) * cov).astype(np.float32), n)
        return got


def _weights(n, scale, t, nsrc, kernel):
    """[n, nsrc] weights that sample a row of nsrc game pixels onto n screen pixels, the first game pixel
    starting t screen pixels in. "point": every screen pixel takes the game pixel it lies in (a whole step);
    otherwise bilinear between pixel centres, as the game's filter does."""
    c = (np.arange(n, dtype=np.float32) + 0.5 - t) / scale
    w = np.zeros((n, nsrc), np.float32)
    rows = np.arange(n)
    if kernel == "point":
        i = np.floor(c).astype(int)
        ok = (i >= 0) & (i < nsrc)
        w[rows[ok], i[ok]] = 1.0
    else:
        c = c - 0.5
        i0 = np.floor(c).astype(int)
        f = c - i0
        for i, v in ((i0, 1.0 - f), (i0 + 1, f)):
            ok = (i >= 0) & (i < nsrc)
            w[rows[ok], i[ok]] += v[ok]
    return w


def draw(font, text, scale, tx, ty, shape, kernel="bilinear"):
    """The text as the game shows it on a screen crop of shape (rows, cols), its first cell's top left corner at
    (tx, ty) in crop pixels and `scale` screen pixels per game pixel. -> premultiplied [4, rows, cols]: coverage
    (1 where the pixel is entirely ink), then colour x coverage (b, g, r)."""
    planes = font.stack(text)
    wy = _weights(shape[0], scale, ty, planes.shape[1], kernel)
    wx = _weights(shape[1], scale, tx, planes.shape[2], kernel)
    return np.matmul(np.matmul(wy, planes), wx.T)


def render(font, text, scale, tx, ty, shape, kernel="bilinear"):
    """draw() as (alpha [h, w], colour [h, w, 3]): the colour is the ink's own where alpha is 1; where it is
    between 0 and 1 the real pixel is that ink mixed with whatever lies behind."""
    p = draw(font, text, scale, tx, ty, shape, kernel)
    return p[0], np.transpose(p[1:] / np.maximum(p[0], 1e-6), (1, 2, 0))


def kernels_for(scale):
    """The ways the game may have scaled by this much, most likely first: a whole step is copied block for block
    in UI Size Ideal but may be smoothed in Default; 1x is the same either way; any other scale is smoothed."""
    if abs(scale - 1.0) < 0.01:
        return ("point",)
    return ("point", "bilinear") if abs(scale - round(scale)) < 0.01 else ("bilinear",)


# ---------------------------------------------------------------- single cells, drawn ahead of time
Q = 8                       # sub-pixel positions per pixel kept for cells drawn ahead of time (1/8 pixel apart)
PAD = 2                     # spare pixels around a cell
_bank = {}
_bank_bytes = 0            # what the bank holds: a cell at 5K is 45 KB, and every window height is a new scale, so the count alone is no bound
BANK_MAX = 30000            # cells kept at most...
BANK_MAX_BYTES = 96 << 20   # ...and at most this much memory (the bank starts again when either is passed)


def _cell(font, ch, scale, kernel, qx, qy):
    """One character's cell drawn at the sub-pixel position (qx, qy)/Q (cached): the pixels that are entirely ink
    (ys, xs), the colour each must have, and the canvas size."""
    key = (font.name, ch, round(scale, 4), kernel, qx, qy)
    got = _bank.get(key)
    if got is None:
        global _bank_bytes
        if len(_bank) > BANK_MAX or _bank_bytes > BANK_MAX_BYTES:
            _bank.clear()
            _bank_bytes = 0
        h = int(math.ceil(font.height * scale)) + 2 * PAD + 2
        w = int(math.ceil(font.layers(ch)[0].shape[1] * scale)) + 2 * PAD + 2
        p = draw(font, ch, scale, PAD + qx / Q, PAD + qy / Q, (h, w), kernel)
        ys, xs = np.nonzero(p[0] >= 0.97)
        got = _bank[key] = (ys, xs, (p[1:, ys, xs] / p[0, ys, xs]).T.astype(np.float32), h, w)
        _bank_bytes += ys.nbytes + xs.nbytes + got[2].nbytes
    return got


def _eval(real, font, ch, ox, oy, scale, kernel):
    """How well character ch drawn with its cell's corner at (ox, oy) explains the picture `real` (BGR):
    (pixels that agree, pixels judged, mean error) over the cell's sure-ink pixels, None if it lies off the picture
    or too little of it is sure."""
    fx, fy = math.floor(ox), math.floor(oy)
    ys, xs, col, h, w = _cell(font, ch, scale, kernel, int(round((ox - fx) * Q)), int(round((oy - fy) * Q)))
    x, y = fx - PAD, fy - PAD
    if x < 0 or y < 0 or y + h > real.shape[0] or x + w > real.shape[1] or len(ys) < MIN_SURE:
        return None
    d = np.abs(real[y + ys, x + xs] - col).max(axis=1)
    return int((d <= TOL).sum()), len(ys), float(np.minimum(d, 120).mean())


def _descend(real, font, ch, scale, kernel, tx, ty):
    """Move the corner of a single cell to where character ch fits best (to about 1/8 pixel). -> (error, tx, ty)"""
    cur = _eval(real, font, ch, tx, ty, scale, kernel)
    if cur is None:
        return None
    err = cur[2]
    for st in (max(0.35 * scale, 0.5), max(0.15 * scale, 0.25), 1.0 / Q):
        for _ in range(6):
            best = None
            for dx, dy in ((st, 0), (-st, 0), (0, st), (0, -st)):
                e = _eval(real, font, ch, tx + dx, ty + dy, scale, kernel)
                if e and e[2] < err - 1e-3 and (best is None or e[2] < best[0]):
                    best = (e[2], dx, dy)
            if best is None:
                break
            err, tx, ty = best[0], tx + best[1], ty + best[2]
    return err, tx, ty


def _decode(real, font, scale, kernel, tx, ty, chars, max_chars):
    """Read the text whose first cell's corner is at (tx, ty): one cell at a time, each must be the single best
    explanation of its pixels and then the next one is looked for. -> (text or None, why)."""
    text, pen = "", 0.0
    for k in range(max_chars + 1):
        scored = []
        for ch in chars:
            e = _eval(real, font, ch, tx + pen * scale, ty, scale, kernel)
            if e:
                scored.append((e[0], e[0] / e[1], ch))
        if not scored:
            return None, "edge" if not text else "unsure"
        fits = sorted((s for s in scored if s[1] >= F_MIN), reverse=True)
        if not fits:
            if max(s[1] for s in scored) >= F_WEAK:       # something is there that doesn't read
                return None, "unsure"
            return (text, "ok") if text else (None, "none")
        if k == max_chars or (len(fits) > 1 and fits[0][0] - fits[1][0] < MARGIN):
            return None, "unsure"
        text += fits[0][2]
        pen += font.adv[fits[0][2]]
    return None, "unsure"


def _coarse(gray, font, chars):
    """How well each character's ink matches every position of `gray`, the picture at game size: {char: map} of
    correlations (only the ink counts: what lies behind a character differs from item to item)."""
    img = gray.astype(np.float32) - 128.0
    img2 = img * img
    out = {}
    for ch in chars:
        mask, centred, n = font.ink(ch)
        s1 = cv2.matchTemplate(img, mask, cv2.TM_CCORR)
        s2 = cv2.matchTemplate(img2, mask, cv2.TM_CCORR)
        num = cv2.matchTemplate(img, centred, cv2.TM_CCORR)
        var = s2 - s1 * s1 / n
        score = num / np.sqrt(np.maximum(var, 1.0) * float((centred * centred).sum()))
        out[ch] = np.where(var < 4.0 * n, 0.0, np.clip(score, -1.0, 1.0)).astype(np.float32)
    return out


def _peak(m, i0, i1, j0, j1):
    """Best value of map m within columns i0..i1 and rows j0..j1 (inclusive, clipped), refined to a fraction of
    a pixel: (value, i, j)."""
    i0, j0 = max(int(i0), 0), max(int(j0), 0)
    i1, j1 = min(int(i1), m.shape[1] - 1), min(int(j1), m.shape[0] - 1)
    if i1 < i0 or j1 < j0:
        return -1.0, 0.0, 0.0
    win = m[j0:j1 + 1, i0:i1 + 1]
    j, i = np.unravel_index(int(win.argmax()), win.shape)
    v, i, j = float(win[j, i]), int(i) + i0, int(j) + j0

    def refine(a, b, c):
        den = a - 2 * b + c
        return 0.0 if den >= -1e-6 else float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    di = refine(m[j, i - 1], m[j, i], m[j, i + 1]) if 0 < i < m.shape[1] - 1 else 0.0
    dj = refine(m[j - 1, i], m[j, i], m[j + 1, i]) if 0 < j < m.shape[0] - 1 else 0.0
    return v, i + di, j + dj


def read_run(img, font, scale, origin, spread=3.0, max_chars=4, chars=None, hint=None):
    """The run of characters whose first cell sits near `origin` in img, or None when it isn't certain.

    img: BGR picture; scale: screen pixels per game pixel; origin: where the first cell's top left corner is
    expected (img pixels); spread: how far off that may be (game pixels); hint: the Run a look at this spot
    gave before (the spot is the same, so it is tried first and costs far less).
    -> Run(text, tx, ty, why, kernel): (tx, ty) where the first cell was found (img pixels), kernel how the
    game scaled it; why: "ok", "none" (no text there), "unsure" (something is there but it doesn't explain
    itself), "edge" (the box is cut off by the picture's edge)."""
    chars = chars or font.chars
    if hint is not None and hint.text is not None:
        text, why = _decode(img, font, scale, hint.kernel, hint.tx, hint.ty, chars, max_chars)
        if text:
            return Run(text, hint.tx, hint.ty, "ok", hint.kernel)
    ih, iw = img.shape[:2]
    pad = spread + 1.0
    x0, y0 = max(int(math.floor(origin[0] - pad * scale)), 0), max(int(math.floor(origin[1] - pad * scale)), 0)
    x1 = min(int(math.ceil(origin[0] + (pad + max_chars * font.max_adv + 1) * scale)), iw)
    y1 = min(int(math.ceil(origin[1] + (pad + font.height + 1) * scale)), ih)
    dw, dh = int(round((x1 - x0) / scale)), int(round((y1 - y0) / scale))
    if dw < font.max_adv + 2 or dh < font.height + 2:
        return Run(None, 0.0, 0.0, "edge", None)
    small = cv2.resize(img[y0:y1, x0:x1], (dw, dh), interpolation=cv2.INTER_AREA)
    fx, fy = dw / (x1 - x0), dh / (y1 - y0)
    maps = _coarse(small.mean(axis=2), font, chars)
    ex, ey, r = (origin[0] - x0) * fx, (origin[1] - y0) * fy, spread + 0.5
    spots = sorted(((v, ch, x0 + i / fx, y0 + j / fy) for ch in chars
                    for v, i, j in [_peak(maps[ch], ex - r, ex + r, ey - r, ey + r)]), reverse=True)[:FIRST_TRIES]
    why = "none"
    for wide in (False, True):                        # the quick way first; if it reads nothing, search everywhere
        for kernel in kernels_for(scale):
            if wide:
                places = [(ch, origin[0], origin[1]) for ch in chars]
                steps = WIDE_GRID
            else:
                places = [(ch, tx, ty) for _, ch, tx, ty in spots]
                steps = GRID
            best = {}                                 # per character: where around its place it fits best
            for ch, px, py in places:
                for gy in steps:
                    for gx in steps:
                        e = _eval(img, font, ch, px + gx * scale, py + gy * scale, scale, kernel)
                        if e and (ch not in best or e[2] < best[ch][0]):
                            best[ch] = (e[2], px + gx * scale, py + gy * scale)
            for ch, (_, tx, ty) in sorted(best.items(), key=lambda kv: kv[1][0])[:DESCENTS]:
                got = _descend(img, font, ch, scale, kernel, tx, ty)
                if not got:
                    continue
                text, why2 = _decode(img, font, scale, kernel, got[1], got[2], chars, max_chars)
                if text:
                    return Run(text, got[1], got[2], "ok", kernel)
                if why2 == "unsure":
                    why = "unsure"
    return Run(None, 0.0, 0.0, why, None)




# ---------------------------------------------------------------- potion counts
COUNT_FONT = "potion_count"
MAX_DIGITS = 4
RIM_MIN = 40                # a slot's right edge must stand out this much (grey levels) from what lies outside it


def rim_visible(img, rect, scale):
    """Is the right edge of the slot there? A window drawn over the slot's right part (the shop, the inventory)
    takes the edge with it and can hide a last digit that would otherwise show. The slot's plate is lighter than
    the frame around it; where the plate meets the window's own edge it isn't. rect: (x, y, w, h) in img pixels."""
    x, y, w, h = rect
    x0, x1 = int(round(x + w - 3.5 * scale)), int(round(x + w - 1.5 * scale))
    x2, x3 = int(round(x + w + 0.5 * scale)), int(round(x + w + 2.5 * scale))
    y0, y1 = int(round(y + 0.25 * h)), int(round(y + 0.75 * h))
    if x0 < 0 or y0 < 0 or x3 > img.shape[1] or y1 > img.shape[0] or x1 <= x0 or x3 <= x2 or y1 <= y0:
        return False
    return float(img[y0:y1, x0:x1].mean() - img[y0:y1, x2:x3].mean()) >= RIM_MIN


def read_count(img, rect, scale, style="keyboard", hint=None, spread=3.0):
    """The number shown at the bottom left of a potion slot, or None when there is none or it isn't certain.

    img: BGR picture holding the slot; rect: (x, y, w, h) of the slot in img pixels; scale: screen pixels per game
    pixel (ui_layout); style: how the slot is drawn ("keyboard" or "controller": where the font asset puts the
    text in it); hint: the Run an earlier look at the same slot returned. -> (count or None, Run). Keep the Run to
    pass back: a spot that read once is checked first next time."""
    font = Font.load(COUNT_FONT)
    x, y, w, h = rect
    ox, oy = font.styles.get(style, font.styles["keyboard"])["origin"]
    run = read_run(img, font, scale, (x + ox * scale, y + h + oy * scale), spread, MAX_DIGITS, "0123456789", hint)
    text = run.text
    if text is None or (len(text) > 1 and text[0] == "0") or not rim_visible(img, rect, scale):
        return None, Run(None, 0.0, 0.0, run.why if text is None else "unsure", None)
    return int(text), run
