"""The game's own EXP-bar font (assets/exp_font.json) and a renderer for it.

The game draws the bar text in a 5 x 7 pixel font on a 1024 x 768 design area (centred in the window) and
smooths the whole picture up to the window with plain bilinear filtering. So the text on screen can be
rendered exactly: paint the glyphs at 1x, then sample them at the window's scale. T07 compares that with the
real pixels. Built by tools/make_exp_font.py, the scale model by tools/fit_exp_scale.py.
"""
import json
from pathlib import Path

import cv2
import numpy as np

import ui_layout

FONT_PATH = Path(__file__).resolve().parent / "assets" / "exp_font.json"
DESIGN_W, DESIGN_H = 1024, 768          # the game lays its UI out on this area, then scales it to the window
SPOT_LEFT, SPOT_RIGHT, SPOT_UP, SPOT_DOWN = 70, 150, 6, 14     # design px around the first digit that hold the whole line
_cache = {}


def load(path=FONT_PATH):
    """The font asset (cached)."""
    key = str(path)
    if key not in _cache:
        _cache[key] = json.loads(Path(path).read_text(encoding="utf-8"))
    return _cache[key]


def window_scale(window_h, ideal=False):
    """How many screen pixels one design pixel is: the window height over 768 (never below 1x). With UI Size
    "Ideal" it grows in whole steps instead (ui_layout holds that rule)."""
    return float(ui_layout.ideal_step(window_h)) if ideal else ui_layout.default_scale(window_h)


def text_origin(window_w, window_h, ideal=False, font=None):
    """Screen position of the first digit's top-left corner. The design area is centred left to right and
    sits on the bottom edge of the window (it fills the window when the UI scales with it)."""
    o = (font or load())["scale"]["origin"]
    return ui_layout.design_to_window(o["x"], o["y"], window_w, window_h, window_scale(window_h, ideal))


def expected_spots(window_w, window_h, font=None):
    """Where the EXP line should be in a window of this size, one box (x0, y0, x1, y1) for each UI scale the game
    might be drawing at (ui_layout.candidate_scales, most likely first). Boxes are generous: the first digit moves
    with the number's length. Used as hints, so a search starts there instead of over the whole strip."""
    o = (font or load())["scale"]["origin"]
    boxes = []
    for _, s in ui_layout.candidate_scales(window_h):
        x, y = ui_layout.design_to_window(o["x"], o["y"], window_w, window_h, s)
        boxes.append((int(x - SPOT_LEFT * s), int(y - SPOT_UP * s), int(x + SPOT_RIGHT * s), int(y + SPOT_DOWN * s)))
    return boxes


def glyph_positions(text, font=None):
    """Where each glyph of the text starts at 1x: [(char, pen, ink left, glyph)]. 'pen' is where its cell
    starts; the ink starts x0 later. Spaces are ignored (the gap before '[' is part of that glyph)."""
    font = font or load()
    pen, items = 0, []
    for ch in text.replace(" ", ""):
        g = font["glyphs"][ch]
        items.append((ch, pen, pen + g["x0"], g))
        pen += g["adv"]
    return items


def canvas(text, font=None):
    """The text at 1x as a 0..1 picture (7 rows). -> (picture, [(left, right) of each bracket])
    Spaces are ignored: the gap before '[' is part of that glyph's spacing."""
    font = font or load()
    items = [(ch, x, g) for ch, _, x, g in glyph_positions(text, font)]
    width = max(x + g["w"] for _, x, g in items)
    pic = np.zeros((font["digit_height"], width), np.float32)
    brackets = []
    for ch, x, g in items:
        y = g["y0"] + font["digit_height"] - 1
        pic[y:y + g["h"], x:x + g["w"]] = np.array(g["rows"], np.float32) / 255
        if ch in "[]":
            brackets.append((x, x + g["w"]))
    return pic, brackets


def sample(pic, scale, tx, ty, shape):
    """The 1x picture as the game shows it: bilinear, pixel centres, its top-left corner at (tx, ty) on a
    screen crop of shape (rows, cols). 0..1 coverage (1 = full white text)."""
    h, w = shape
    xs = ((np.arange(w) + 0.5 - tx) / scale - 0.5).astype(np.float32)
    ys = ((np.arange(h) + 0.5 - ty) / scale - 0.5).astype(np.float32)
    mx, my = np.meshgrid(xs, ys)
    return cv2.remap(pic, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def render(text, scale, font=None):
    """The text alone on a black background at the given scale: a 0..1 picture whose size is the text's
    1x size times the scale (rounded). Used to size things and to compare."""
    pic, _ = canvas(text, font)
    h, w = pic.shape
    return sample(pic, scale, 0.0, 0.0, (max(1, round(h * scale)), max(1, round(w * scale))))
