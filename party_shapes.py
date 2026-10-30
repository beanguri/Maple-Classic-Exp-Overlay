"""The shape of the HP-bar frame in the game's party list, drawn at any UI scale and sub-pixel position, for the
party reader to compare with the real pixels. It reads nothing from the screen itself.

The game draws each bar on its 1024 x 768 design area as 67 x 11 pixels: an opaque black frame one pixel thick, a
white rim one pixel inside it, and the fill inside that (red, or dark grey; it changes with HP, so it is never
used). The corner pixels show what is behind the bar. At 1x the frame is copied block for block; at any other
scale it is scaled like every other part of the UI (glyphs.py: a whole step is copied block for block in UI Size
Ideal and may be smoothed in Default, anything else is smoothed bilinearly).

The asset (assets/party_shapes.json, made by tools/make_party_shapes.py from 1x pictures of real bars):
    {"name": "party_bar", "bar": [67, 11],
     "frame": ["#" dark, "o" light, "." don't care: 11 rows of 67 characters],
     "pitch": 17,                                   rows are this many design pixels apart
     "panel": {...}}                                see panel_edges()
Colours are fixed (dark = 0, light = 255 on every channel): nothing is learned from the player's screen.

A screen pixel is "trusted" when it is made almost wholly of frame or rim at this scale and phase (dark and light
share of it at least TRUST): where the frame meets the rim it is a smoothed mix of both and its value is known
exactly; where it meets the outside or the fill it is not trusted and left out, so is the whole blurred ring.

Pure numpy: no screen, no files except the asset.
"""
import json
import math
from collections import namedtuple
from pathlib import Path

import numpy as np

import glyphs

ASSET = Path(__file__).resolve().parent / "assets" / "party_shapes.json"
TRUST = 0.97                # a pixel is trusted when at least this share of it is frame or rim
TOL = 28                    # a pixel agrees with the drawing when no channel is further off than this (real bars: at most 18)
DARK, LIGHT = 0.0, 255.0    # the colours of the frame and the rim
MARGIN = 2                  # design pixels of "don't care" drawn round the bar (what the smoothing mixes in)
Q = glyphs.Q                # sub-pixel positions per pixel drawn ahead of time (1/8 pixel apart)
PAD = 2                     # spare screen pixels round a drawing

Frame = namedtuple("Frame", "ys xs expect dark h w")    # trusted pixels (row, col, value, is pure dark) in an h x w canvas

_shape = None


class Shape:
    """The bar frame as the asset gives it."""

    def __init__(self, data):
        self.w, self.h = data["bar"]
        self.pitch = data["pitch"]
        self.panel = data["panel"]
        rows = data["frame"]
        assert len(rows) == self.h and all(len(r) == self.w for r in rows), "frame is not bar-sized"
        pad = ((MARGIN, MARGIN), (MARGIN, MARGIN))
        self.dark = np.pad(np.array([[c == "#" for c in r] for r in rows], np.float32), pad)
        self.light = np.pad(np.array([[c == "o" for c in r] for r in rows], np.float32), pad)
        self.planes = np.stack([self.dark, self.light])    # [2, h + 2 MARGIN, w + 2 MARGIN]

    @classmethod
    def load(cls):
        global _shape
        if _shape is None:
            _shape = cls(json.loads(ASSET.read_text(encoding="utf-8")))
        return _shape


def weights(n, scale, t, nsrc, kernel):
    """[n, nsrc] weights that sample a row of nsrc design pixels onto n screen pixels, the first design pixel
    starting t screen pixels in (the game's scaling: kernel "point" or "bilinear", see glyphs.kernels_for)."""
    return glyphs._weights(n, scale, t, nsrc, kernel)


kernels_for = glyphs.kernels_for


def draw(scale, tx, ty, shape, kernel="bilinear"):
    """The frame as the game shows it on a screen crop of `shape` (rows, cols), the bar's top left design pixel
    starting at (tx, ty) in crop pixels and `scale` screen pixels per design pixel. -> (known, expect), both
    [rows, cols]: known = the share of the pixel that is frame or rim (0..1), expect = the value (grey, 0..255)
    that pixel has where known is 1; trust only where known >= TRUST."""
    s = Shape.load()
    planes = s.planes
    wy = weights(shape[0], scale, ty - MARGIN * scale, planes.shape[1], kernel)
    wx = weights(shape[1], scale, tx - MARGIN * scale, planes.shape[2], kernel)
    d, li = (wy @ planes[i] @ wx.T for i in (0, 1))
    known = d + li
    return known, np.where(known > 1e-6, LIGHT * li / np.maximum(known, 1e-6) + DARK * d / np.maximum(known, 1e-6), 0.0)


_bank = {}
BANK_MAX = 4000             # drawings kept at most (a drawing at 5K is a few hundred KB of indexes: the bank starts again)


def frame(scale, kernel, qx, qy):
    """The trusted pixels of the frame at sub-pixel position (qx, qy)/Q (cached): where the bar's top left design
    pixel lies in a canvas that starts PAD + MARGIN pixels before it. -> Frame."""
    key = (round(scale, 4), kernel, qx, qy)
    got = _bank.get(key)
    if got is None:
        if len(_bank) > BANK_MAX:
            _bank.clear()
        s = Shape.load()
        off = PAD + int(math.ceil(MARGIN * scale))
        h = int(math.ceil(s.h * scale)) + 2 * off + 1
        w = int(math.ceil(s.w * scale)) + 2 * off + 1
        known, expect = draw(scale, off + qx / Q, off + qy / Q, (h, w), kernel)
        ys, xs = np.nonzero(known >= TRUST)
        got = _bank[key] = Frame(ys, xs, expect[ys, xs].astype(np.float32), expect[ys, xs] <= 1.0, h, w), off
    return got


def agree(real, ox, oy, scale, kernel):
    """How well the frame drawn with its top left design pixel at (ox, oy) in `real` (BGR) explains it, over the
    trusted pixels: (pixels that agree, pixels judged, mean error), None when the drawing leaves the picture.
    A pixel agrees when no channel is further off than TOL."""
    fx, fy = math.floor(ox), math.floor(oy)
    fr, off = frame(scale, kernel, int(round((ox - fx) * Q)), int(round((oy - fy) * Q)))
    x, y = fx - off, fy - off
    if x < 0 or y < 0 or y + fr.h > real.shape[0] or x + fr.w > real.shape[1]:
        return None
    d = np.abs(real[y + fr.ys, x + fr.xs].astype(np.float32) - fr.expect[:, None]).max(axis=1)
    return int((d <= TOL).sum()), len(fr.ys), float(np.minimum(d, 120).mean())


def panel_edges(first_x, first_y, last_y, scale, shape=None):
    """Where the panel's border lies on screen for a column of bars whose first bar's top left design pixel is
    at (first_x, first_y) and whose last bar's top row starts at last_y (screen pixels, scale per design pixel).
    The border is two lines: the inner one (the brighter) and the outer one a pixel (design) further out; the panel
    is see-through, so only their shape is known, never their colour. -> {"top": y, "bottom": y, "right": x}
    of the INNER line's first screen pixel (the outer one lies one scale step beyond it: above, below, right)."""
    p = (shape or Shape.load()).panel
    return {"top": first_y + p["top"] * scale, "bottom": last_y + p["bottom"] * scale, "right": first_x + p["right"] * scale}
