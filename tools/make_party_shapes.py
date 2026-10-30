"""Builds assets/party_shapes.json: the shape of the HP-bar frame in the game's party list, and the numbers around it
(see party_shapes.py).

The game draws the party list on its 1024 x 768 design area and copies it block for block at 1x. Seen at exactly 1x,
every bar's frame is the same pixels on every bar: so the shape is read off bars found at 1x. A pixel that is pure
black (0, 0, 0) on at least 98% of the bars is "dark" (#), pure white (255, 255, 255) "light" (o); the rest (the corners,
which show what is behind the bar, and the fill, which changes with HP) is "don't care" (.). Nothing else is kept: no
colours, no crops, no names.

Usage (from the repo root):
    python -I tools/make_party_shapes.py <recording folder> [<recording folder> ...] [-o out.json]
  Each folder holds the recording's full pictures in "full/". Give it the TUNE recordings that are at 1x only
  (UI Size Default at 768 tall, UI Size Ideal below 1440 tall). A bar is a dark blob exactly 67 x 11 pixels; one
  that touches something dark behind it is simply not found, which costs nothing here.

The numbers around the frame are measured on the same pictures (the most common value; a few pictures differ because a
bar was not found): the pitch between rows, where the panel's border lies, where the bullet starts.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "party_shapes.json"
BAR_W, BAR_H = 67, 11
SURE = 0.98                          # share of bars a pixel must be pure black / pure white on
MIN_BARS = 300                       # fewest bars the shape may be read from


def bars_in(img):
    """The (x, y) of every dark blob exactly 67 x 11 in a BGR picture."""
    n, _, st, _ = cv2.connectedComponentsWithStats((img.max(axis=2) < 70).astype(np.uint8), connectivity=8)
    return [(int(st[i][0]), int(st[i][1])) for i in range(1, n) if (st[i][2], st[i][3]) == (BAR_W, BAR_H)]


def column(bars):
    """The bars of the one column that stands at a pitch of 17, top to bottom (None unless there is exactly one)."""
    by_x = {}
    for x, y in sorted(bars):
        by_x.setdefault(x, []).append(y)
    runs = []
    for x, ys in by_x.items():
        run = [ys[0]]
        for y in ys[1:]:
            if y - run[-1] == 17:
                run.append(y)
            else:
                if len(run) >= 2:
                    runs.append((x, run))
                run = [y]
        if len(run) >= 2:
            runs.append((x, run))
    return runs[0] if len(runs) == 1 else None


def frame_rows(pats):
    """pats [n, 11, 67, 3] -> the 11 rows of 67 characters, and how many pixels were sure."""
    black = (pats.max(axis=3) == 0).mean(axis=0)
    white = (pats.min(axis=3) == 255).mean(axis=0)
    rows = ["".join("#" if black[r, c] >= SURE else "o" if white[r, c] >= SURE else "." for c in range(BAR_W))
            for r in range(BAR_H)]
    return rows, int((black >= SURE).sum()), int((white >= SURE).sum())


def measure(img, x0, ys):
    """Where the panel's border and the bullet lie around a column of bars (design pixels = picture pixels at 1x):
    (top, bottom, right, from_left, bullet), each None when the picture doesn't show it clearly."""
    g = img.mean(axis=2)
    y1, yl = ys[0], ys[-1]
    top = bottom = right = left = bullet = None
    strip = np.median(g[:, x0 + 68:x0 + 72], axis=1)         # just right of the bars: nothing but the panel
    if y1 - 9 >= 0 and yl + 20 <= g.shape[0]:
        top = int(np.argmax(strip[y1 - 9:y1 - 1])) - 9       # the inner border is the brightest line there
        bottom = int(np.argmax(strip[yl + 12:yl + 20])) + 12
    band = np.median(g[y1 + 12:y1 + 15, :], axis=0)          # the gap between two rows: no text
    if x0 + 80 <= g.shape[1]:
        right = int(np.argmax(band[x0 + 66:x0 + 80])) + 66
    ya, yb = y1 - 3, yl + 13
    if x0 - 200 >= 0:
        xs = np.arange(x0 - 200, x0 - 30)
        score = np.array([(g[ya:yb, x] - g[ya:yb, x + 2]).mean() for x in xs])
        k = int(np.argmax(score))
        if score[k] >= 25:                                   # the border is a clear step, not busy ground
            left = int(xs[k])
            sub = img[y1:y1 + BAR_H, left:x0].astype(int)      # the bullet is orange: only to find where it starts
            orange = (sub[..., 2] > 200) & (sub[..., 0] < 90) & (sub[..., 1] > 100)
            cs = np.nonzero(orange.any(axis=0))[0]
            bullet = int(cs.min()) if len(cs) else None
            left = x0 - left
    return top, bottom, right, left, bullet


def mode(counter, what):
    """The most common value, and its share (printed so a doubtful number is seen)."""
    if not counter:
        sys.exit(f"nothing measured for {what}")
    v, n = counter.most_common(1)[0]
    print(f"  {what}: {v}  ({n} of {sum(counter.values())} pictures)")
    return int(v)


def main():
    args = sys.argv[1:]
    out = OUT
    if "-o" in args:
        i = args.index("-o")
        out = Path(args[i + 1])
        del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    pats, pitch = [], Counter()
    top, bottom, right, left, bullet = Counter(), Counter(), Counter(), Counter(), Counter()
    for rec in args:
        for f in sorted((Path(rec) / "full").glob("*.png")):
            img = cv2.imread(str(f))
            if img is None:
                continue
            bars = bars_in(img)
            for x, y in bars:
                pats.append(img[y:y + BAR_H, x:x + BAR_W].copy())
            col = column(bars)
            if col:
                x0, ys = col
                pitch.update([b - a for a, b in zip(ys, ys[1:])])
                for c, v in zip((top, bottom, right, left, bullet), measure(img, x0, ys)):
                    if v is not None:
                        c[v] += 1
    if len(pats) < MIN_BARS:
        sys.exit(f"only {len(pats)} bars found (need {MIN_BARS}): are these pictures at 1x?")
    rows, n_dark, n_light = frame_rows(np.array(pats))
    print(f"{len(pats)} bars; frame: {n_dark} dark + {n_light} light pixels")
    data = {
        "name": "party_bar",
        "bar": [BAR_W, BAR_H],
        "frame": rows,
        "pitch": mode(pitch, "row pitch"),
        "panel": {
            "top": mode(top, "inner border above the first bar's top row (rows)"),
            "bottom": mode(bottom, "inner border below the last bar's top row (rows)"),
            "right": mode(right, "inner border right of the bar's left column (columns)"),
            "from_left_min": min(k for k in left if left[k] >= 5),
            "from_left_max": max(k for k in left if left[k] >= 5),
            "bullet": mode(bullet, "bullet's first column right of the panel's left border"),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
