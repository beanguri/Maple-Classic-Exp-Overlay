"""Builds the game's fonts for reader v2 (see glyphs.py for the asset format): potion_count and mesos.

The count digits are a 6 x 9 pixel font with a vertical colour gradient (white at the top, light blue at the
bottom) and a one pixel black outline, drawn on the 1024 x 768 design area. At UI Size Ideal (2x) the picture is
copied block for block and at a 1x window it isn't scaled at all, so every digit can be cut out of labelled
frames pixel for pixel. Other sizes show the same font smoothed up (glyphs.py draws that).

Usage (from the repo root):
    python -I tools/make_font.py potion_count <truth_tune.json> [out.json]
    python -I tools/make_font.py mesos <other_truth_tune.json> [out.json]
  truth_tune.json: the potion test library's tune truth (frames with each slot's rectangle, count and quality;
  the pictures are read from its "root" folder). Only TUNE frames: the gate is never looked at. Only frames at
  exactly 1x and 2x (UI Size Ideal) are used for the shapes; the text's place in each slot style is then found
  by reading every tune frame with the finished font. Re-run it after a game patch with new frames.

How the shapes are found (nothing is typed in by hand): every labelled count is cut out at 1x; the black outline
(exact 0, 0, 0) lines each text up in rows, then every digit is lined up with its own kind (sideways, one pixel
at a time, next to where the digit before it ends) until the shapes stop changing; a pixel belongs to a digit's
fill when it has the same colour in most cuts of that digit and that colour is the gradient's colour for its row.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import glyphs                                          # noqa: E402

OUT = ROOT / "assets" / "fonts_v2" / "potion_count.json"
DIGITS = "0123456789"
CELL_H, CELL_W, FILL_ROWS = 11, 8, 9
CUT_ROWS, CUT_COLS = 18, 48            # a cut-out: from 14 rows above the slot's bottom edge, 4 columns left of it
NOMINAL_ROW, NOMINAL_COL = 4, 4        # where the first cell starts in a cut-out, roughly (the slot rectangles are fits)
FILL_SHARE = 0.8                       # a fill pixel has its colour in this share of the cuts of its digit


def pair_parity(crop):
    """Which pixels of a block-for-block 2x picture start a pair: (row, column) offset of the 2 x 2 blocks."""
    best = None
    for py in (0, 1):
        for px in (0, 1):
            a = crop[py:py + 2 * ((crop.shape[0] - py) // 2):2]
            b = crop[py + 1:py + 1 + 2 * ((crop.shape[0] - py - 1) // 2):2]
            n = min(len(a), len(b))
            v = (a[:n] == b[:n]).mean()
            a = crop[:, px:px + 2 * ((crop.shape[1] - px) // 2):2]
            b = crop[:, px + 1:px + 1 + 2 * ((crop.shape[1] - px - 1) // 2):2]
            m = min(a.shape[1], b.shape[1])
            score = v + (a[:, :m] == b[:, :m]).mean()
            if best is None or score > best[0]:
                best = (score, py, px)
    return best[1], best[2]


def cut_outs(truth):
    """{(setup, slot, count): BGR cut-out at 1x} for every clean, labelled count at exactly 1x or at 2x."""
    root = Path(truth["root"])
    seen = {}
    for f in truth["frames"]:
        step = 1 if abs(f["scale"] - 1.0) < 0.01 else 2 if abs(f["scale"] - 2.0) < 0.01 else 0
        if f["kind"] != "full" or f["layout"] != "keyboard" or not step:
            continue
        img = None
        for s in f["slots"]:
            key = (f["setup"], s["index"], s["count"])
            if s["kind"] != "consumable" or s["count"] is None or s["quality"] != "clean" or key in seen:
                continue
            if img is None:
                img = cv2.imread(str(root / f["path"]))
            x, y, w, h = s["rect"]
            crop = img[y + h - 14 * step:y + h + 4 * step, x - 4 * step:x + 44 * step]
            if step == 2:
                crop = crop[:2 * CUT_ROWS, :2 * CUT_COLS]
                py, px = pair_parity(crop.mean(axis=2))
                crop = crop[py::2, px::2]
            if crop.shape[:2] == (CUT_ROWS, CUT_COLS):
                seen[key] = crop
    return seen


def line_up_rows(black, text):
    """The row where a text's cells start: the outline rows above and below the fill are black over the cells'
    columns, the rows just outside them are not."""
    cols = slice(NOMINAL_COL - 2, NOMINAL_COL + 8 * len(text) + 2)
    best = None
    for r in range(1, CUT_ROWS - CELL_H - 1):
        on = lambda row: int(black[row, cols].sum())
        score = on(r) + on(r + CELL_H - 1) - 2 * (on(r - 1) + on(r + CELL_H))
        if best is None or score > best[0]:
            best = (score, r)
    return best[1]


def line_up(cuts):
    """Where every digit's cell starts in its cut-out: {(key, i): (row, col)}, and each digit's advance."""
    black = {k: (c.max(axis=2) == 0) for k, c in cuts.items()}
    row = {k: line_up_rows(black[k], str(k[2])) for k in cuts}
    adv = {d: 8 for d in DIGITS}
    pos = {(k, i): (row[k], NOMINAL_COL + 8 * i) for k in cuts for i in range(len(str(k[2])))}

    def cell(k, r, c):
        z = black[k]
        return z[r:r + CELL_H, c:c + CELL_W] if r >= 0 and c >= 0 and r + CELL_H <= z.shape[0] and c + CELL_W <= z.shape[1] else None

    templates = {}
    for it in range(10):
        for k in cuts:                                     # sideways, one digit after the other
            text = str(k[2])
            prev = None
            for i, d in enumerate(text):
                base = pos[(k, 0)][1] if i == 0 else prev + adv[text[i - 1]]
                best = None
                for c in range(base - (2 if i == 0 else 3), base + (3 if i == 0 else 4)):
                    z = cell(k, row[k], c)
                    if z is None:
                        continue
                    if d in templates:
                        t = templates[d]
                        score = (z * t).sum() - (z * (1 - t)).sum()
                    else:
                        score = -abs(c - base)
                    if best is None or score > best[0]:
                        best = (score, c)
                pos[(k, i)] = (row[k], best[1])
                prev = best[1]
        templates = {}
        for d in DIGITS:
            cells = [cell(k, *pos[(k, i)]) for k in cuts for i, c in enumerate(str(k[2])) if c == d]
            cells = [c for c in cells if c is not None]
            templates[d] = np.mean(cells, axis=0) if cells else np.zeros((CELL_H, CELL_W))
        for d in DIGITS:
            gaps = [pos[(k, i + 1)][1] - pos[(k, i)][1] for k in cuts for i, c in enumerate(str(k[2])[:-1]) if c == d]
            if gaps:
                adv[d] = int(np.median(gaps))
    # a last, sturdier round: each digit's cells aligned (a pixel either way) to its template, the best 70% kept
    for _ in range(8):
        fresh = {}
        for d in DIGITS:
            found = []
            for k in cuts:
                for i, c in enumerate(str(k[2])):
                    if c != d:
                        continue
                    r0, c0 = pos[(k, i)]
                    best = None
                    for rr in range(r0 - 1, r0 + 2):
                        for cc in range(c0 - 2, c0 + 3):
                            z = cell(k, rr, cc)
                            if z is not None:
                                score = (z * templates[d]).sum() - (z * (1 - templates[d])).sum()
                                if best is None or score > best[0]:
                                    best = (score, rr, cc, z)
                    found.append((best[0], k, i, best[1], best[2], best[3]))
            found.sort(key=lambda f: -f[0])
            keep = found[:max(5, int(len(found) * 0.7))]
            fresh[d] = np.mean([f[5] for f in keep], axis=0)
            for f in found:
                pos[(f[1], f[2])] = (f[3], f[4])
        templates = fresh
    return pos, adv


def glyphs_from(cuts, pos, adv):
    """Fill colours per row, the outline colour and each digit's fill bitmap, from the lined-up cells."""
    stacks = {d: [] for d in DIGITS}
    for (k, i), (r, c) in pos.items():
        cell = cuts[k][r:r + CELL_H, c:c + CELL_W]
        if cell.shape[:2] == (CELL_H, CELL_W):
            stacks[str(k[2])[i]].append(cell)
    modes = {}
    for d, cells in stacks.items():
        a = np.stack(cells)                                # [n, 11, 8, 3]
        mode = np.zeros((CELL_H, CELL_W, 3), np.uint8)
        share = np.zeros((CELL_H, CELL_W))
        for r in range(CELL_H):
            for c in range(CELL_W):
                (col, n), = Counter(map(tuple, a[:, r, c].tolist())).most_common(1)
                mode[r, c], share[r, c] = col, n / len(a)
        modes[d] = (mode, share)
    # the gradient: for each fill row the colour that wins most often among the steady, non-black pixels
    fill = []
    for r in range(1, 1 + FILL_ROWS):
        votes = Counter()
        for mode, share in modes.values():
            for c in range(CELL_W):
                if share[r, c] >= FILL_SHARE and mode[r, c].any():
                    votes[tuple(int(v) for v in mode[r, c])] += 1
        fill.append(list(votes.most_common(1)[0][0]))
    out = {}
    for d, (mode, share) in modes.items():
        rows = []
        for r in range(1, 1 + FILL_ROWS):
            rows.append("".join("X" if (share[r, c] >= FILL_SHARE and list(mode[r, c]) == fill[r - 1]) else "."
                                for c in range(1, 1 + 6)))
        out[d] = {"rows": rows, "adv": int(adv[d])}
    return {"name": "potion_count", "fill": fill, "outline": {"rule": "cross", "color": [0, 0, 0]}, "glyphs": out}


def text_places(font_data, truth):
    """Where the text sits in each slot style, in game pixels from the slot rectangle's left edge and bottom
    edge: the median over every tune frame the finished font reads right."""
    glyphs._fonts["potion_count"] = glyphs.Font(font_data)
    font = glyphs._fonts["potion_count"]
    root = Path(truth["root"])
    offsets, count = {}, {}
    for f in truth["frames"]:
        if f["kind"] != "full":
            continue
        count[f["setup"]] = count.get(f["setup"], 0) + 1
        if count[f["setup"]] % 4:                                  # every 4th full frame of each recording
            continue
        img = None
        for s in f["slots"]:
            if s["kind"] != "consumable" or s["count"] is None or s["quality"] != "clean":
                continue
            if img is None:
                img = cv2.imread(str(root / f["path"]))
            x, y, w, h = s["rect"]
            sc = f["scale"]
            run = glyphs.read_run(img, font, sc, (x - 0.3 * sc, y + h - 10.6 * sc), 6.0, 4)
            if run.text == str(s["count"]):
                offsets.setdefault(s["style"], []).append(((run.tx - x) / sc, (run.ty - (y + h)) / sc))
    return {style: {"origin": [round(float(np.median([o[0] for o in v])), 2), round(float(np.median([o[1] for o in v])), 2)]}
            for style, v in offsets.items()}


MESOS_OUT = ROOT / "assets" / "fonts_v2" / "mesos.json"
MESOS_ROWS, MESOS_W = 10, 5
MESOS_PITCH, MESOS_COMMA = 6, 4         # how far the next cell (to the left) starts after a digit / a comma
MESOS_RIGHT, MESOS_TOP = 3, 3           # the last digit's right column is 3 pixels in from the box's right edge; text starts 3 rows down
INK_DROP = 60                           # a pixel is ink when it is this much darker than the cell's lightest tenth


def mesos_font(truth):
    """The mesos digits: 5 x 8 pixels, one pixel strokes, no outline, plus a comma that hangs two rows lower.
    The number is right aligned in its box, so every cell's place is known from the box's right edge: the
    cells of every clean 1x inventory box (UI Size Ideal below 1440 tall is not scaled at all) are cut out, a
    pixel is ink when it is clearly darker than the lightest of its cell, and a pixel belongs to a glyph when
    most cuts of that glyph have it as ink."""
    root = Path(truth["root"])
    cuts = {}
    for f in truth["frames"]:
        m = f.get("mesos")
        if not m or abs(f["scale"] - 1.0) > 0.01:
            continue
        img = None
        for b in m["boxes"]:
            if b["kind"] != "inventory" or b["quality"] != "clean" or b["unreadable"] or b["value"] is None:
                continue
            if img is None:
                img = cv2.imread(str(root / f["path"])).mean(axis=2)
            x, y, w, h = b["rect"]
            right = x + w - MESOS_RIGHT
            for ch in reversed(f"{b['value']:,}"):
                cell = img[y + MESOS_TOP:y + MESOS_TOP + MESOS_ROWS, right - MESOS_W + 1:right + 1]
                cuts.setdefault(ch, []).append(cell < np.percentile(cell, 90) - INK_DROP)
                right -= MESOS_COMMA if ch == "," else MESOS_PITCH
    out = {}
    for ch in DIGITS + ",":
        a = np.stack(cuts[ch]).mean(axis=0)
        out[ch] = {"rows": ["".join("X" if v > 0.5 else "." for v in row) for row in a], "adv": MESOS_COMMA if ch == "," else MESOS_PITCH,
                   "cuts": len(cuts[ch])}
    return {"name": "mesos", "fill": [[0, 0, 0]] * MESOS_ROWS, "outline": {"rule": "none", "color": [0, 0, 0]}, "glyphs": out,
            "layout": {"pitch": MESOS_PITCH, "comma": MESOS_COMMA, "right": MESOS_RIGHT, "top": MESOS_TOP}}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "mesos":
        font = mesos_font(json.loads(Path(sys.argv[2]).read_text()))
        out = Path(sys.argv[3]) if len(sys.argv) > 3 else MESOS_OUT
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(font, indent=1) + "\n")
        print({ch: g["cuts"] for ch, g in font["glyphs"].items()})
        print("wrote", out)
        return
    if len(sys.argv) < 3 or sys.argv[1] != "potion_count":
        sys.exit(__doc__)
    truth = json.loads(Path(sys.argv[2]).read_text())
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else OUT
    cuts = cut_outs(truth)
    print(f"{len(cuts)} labelled counts cut out")
    pos, adv = line_up(cuts)
    font = glyphs_from(cuts, pos, adv)
    font["styles"] = {"keyboard": {"origin": [-0.3, -10.6]}}      # a first guess, then measured
    font["styles"] = text_places(font, truth)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(font, indent=1) + "\n")
    for d in DIGITS:
        print(d, font["glyphs"][d]["adv"], sum(r.count("X") for r in font["glyphs"][d]["rows"]), "fill pixels")
    print("fill", font["fill"])
    print("styles", font["styles"])
    print("wrote", out)


if __name__ == "__main__":
    main()
