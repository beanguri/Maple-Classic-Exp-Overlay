"""Builds assets/fonts_v2/badge.json: the shapes of the digits in the game's level badge (see level_ocr.py).

The badge is one orange tile per digit, 11 x 15 pixels on the 1024 x 768 design area, with the digit drawn in white
(and a darker orange shade beside it). The blue channel separates white from orange (orange 17, white 255), so it
is the digit's ink coverage. The game draws it block for block at 1x and Ideal 2x and smoothed (bilinear) at any other
size, so a digit's shape can be recovered from smoothed frames too.

Usage (from the repo root):
    python -I tools/make_badge_font.py <truth_tune.json> [out.json]
  truth_tune.json: the test library's tune truth ("badge" per frame: level, rect of the orange boxes, clean or not).
  Only TUNE frames. How: the digits found at exactly 1x are read off pixel for pixel; the smoothed pictures of
  those same digits give the sub-pixel place of a tile in the smoothed frame; with that place known, every other
  digit's shape is the black-and-white 11 x 15 picture that, drawn smoothed, matches its frames best.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import level_ocr                                           # noqa: E402

OUT = ROOT / "assets" / "fonts_v2" / "badge.json"
BG, WHITE = 17.0, 255.0


def pictures(truth):
    """{(level, scale): blue-channel alpha picture of the badge, margin 1 px} for clean full frames (one per kind)."""
    root, seen = Path(truth["root"]), {}
    for f in truth["frames"]:
        b = f.get("badge")
        if not b or not b["visible"] or b["quality"] != "clean" or f["kind"] != "full" or f["scale"] <= 0:
            continue
        key = (b["level"], f["scale"], tuple(b["rect"]))
        if key in seen:
            continue
        x, y, w, h = b["rect"]
        img = cv2.imread(str(root / f["path"]))
        seen[key] = np.clip((img[y - 1:y + h + 1, x - 1:x + w + 1, 0].astype(np.float32) - BG) / (WHITE - BG), 0, 1)
    return seen


def fit_phase(mask, pic, scale, x0):
    best = None
    for ox in np.arange(-1.5, 1.51, 0.0625):
        for oy in np.arange(-1.5, 1.51, 0.0625):
            e = level_ocr.tile_error(mask, pic, scale, x0, ox, oy)
            if best is None or e < best[0]:
                best = (e, ox, oy)
    return best


def fit_mask(pic, scale, x0, phase, seed=0):
    """The 15 x 11 black-and-white tile picture that, drawn at `scale` with `phase`, is closest to pic."""
    rng, best = np.random.default_rng(seed), None
    for _ in range(6):
        mask = (rng.random((15, 11)) < 0.2).astype(np.float32)
        mask[:2] = mask[-2:] = 0
        mask[:, 0] = mask[:, -1] = 0
        e = level_ocr.tile_error(mask, pic, scale, x0, *phase)
        for _ in range(8):
            changed = False
            for y in range(15):
                for x in range(11):
                    mask[y, x] = 1 - mask[y, x]
                    e2 = level_ocr.tile_error(mask, pic, scale, x0, *phase)
                    if e2 < e - 1e-9:
                        e, changed = e2, True
                    else:
                        mask[y, x] = 1 - mask[y, x]
            if not changed:
                break
        if best is None or e < best[0]:
            best = (e, mask.copy())
    return best


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    truth = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    pics = pictures(truth)
    masks = {}
    for (level, scale, rect), pic in pics.items():          # 1x: the tiles read off pixel for pixel
        if scale == 1.0:
            for i, ch in enumerate(str(level)):
                if ch not in masks:
                    masks[ch] = (pic[1:16, 1 + 12 * i:12 + 12 * i] > 0.5).astype(np.float32)
    print("read off at 1x:", "".join(sorted(masks)))
    smooth = sorted(k for k in pics if k[1] == 1.4062)
    phases = {}                                              # (tile count, tile index) -> phase at the smoothed size
    for key in smooth:
        level, scale, rect = key
        for i, ch in enumerate(str(level)):
            if ch in masks and (len(str(level)), i) not in phases:
                phases[(len(str(level)), i)] = fit_phase(masks[ch], pics[key], scale, 1 + 12 * i * scale)[1:]
    errors = {}
    for key in smooth:
        level, scale, rect = key
        for i, ch in enumerate(str(level)):
            ph = phases.get((len(str(level)), i))
            if ch not in masks and ph:
                e, masks[ch] = fit_mask(pics[key], scale, 1 + 12 * i * scale, ph)
                errors[ch] = e
    print("fitted from smoothed frames:", "".join(sorted(errors)), {c: round(float(e), 5) for c, e in errors.items()})
    missing = [c for c in "0123456789" if c not in masks]
    if missing:
        sys.exit(f"no frames for digits {missing}")
    data = {"name": "badge", "tile": [11, 15], "bg": BG, "white": WHITE, "glyphs": {}}
    for ch in "0123456789":
        rows = ["".join("X" if v else "." for v in r) for r in masks[ch]]
        data["glyphs"][ch] = {"rows": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
