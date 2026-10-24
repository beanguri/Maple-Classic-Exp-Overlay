"""The game's EXP messages: "You received EXP (+7)" and "Bonus EXP for PARTY (+2)".

They are lines in the message area at the bottom right of the game. "You received EXP" is WHITE when
you made the kill (kind "own") and YELLOW when a party member did (kind "other"); "Bonus EXP for PARTY"
is yellow: what the party added on top. The EXP bar already says how much
EXP came in; these lines say how that splits. A split is only believed when it adds up to the
bar's gain exactly (split_gain), so a misread digit can never put a wrong number in the table.

Pure functions: they take a picture (and a function that reads text), never touch the screen.
"""
import re

import cv2
import numpy as np

OWN, BONUS, OTHER = "own", "bonus", "other"

# Yellow text: OpenCV hue 18-40, saturated, bright. (The party list's stars are yellow too; they are
# tiny and carry no text, so the line test and the text test drop them.)
HUE = (18, 40)
SAT_MIN, VAL_MIN = 90, 150


def yellow_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    return (((h >= HUE[0]) & (h <= HUE[1]) & (s >= SAT_MIN) & (v >= VAL_MIN)) * 255).astype(np.uint8)


def find_lines(mask, min_w=40):
    """Boxes (x, y, w, h) of the yellow text lines, top to bottom: rows of yellow joined into bands,
    each band as wide as its pixels. Bands too narrow to hold a message are dropped."""
    rows = (mask > 0).sum(axis=1)
    lines, start = [], None
    for y, n in enumerate(list(rows) + [0]):
        if n > 0 and start is None:
            start = y
        elif n == 0 and start is not None:
            # a one-row gap inside a line (the dot of an i, the bar of a t) doesn't end it
            if y + 1 < len(rows) and rows[y + 1] > 0:
                continue
            lines.append((start, y))
            start = None
    out = []
    for y0, y1 in lines:
        cols = np.where((mask[y0:y1] > 0).any(axis=0))[0]
        if len(cols) == 0 or y1 - y0 < 5 or y1 - y0 > 40:
            continue
        x0, x1 = int(cols.min()), int(cols.max()) + 1
        if x1 - x0 >= min_w:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def line_image(mask, box, pad=3, scale=4):
    """A line as black text on white, big enough for Tesseract."""
    x, y, w, h = box
    crop = mask[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (3, 3), 0)
    big = 255 - big
    return cv2.copyMakeBorder(big, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


_NUM = r"\(\s*[+—-]?\s*([0-9][0-9,. ]*)\s*[)\]1l]?\s*$"
_OWN = re.compile(r"(rec|eiv|ceiv|eived|received).*EXP.*" + _NUM, re.I)
_BONUS = re.compile(r"(bon|onus|nus).*(EXP|XP).*(PAR|ART|RTY|TY).*" + _NUM, re.I)


def parse(text):
    """(kind, value) for a line of text, or None. Tolerant of what OCR does to a small pixel font:
    the label may be damaged, but a line must have EXP (or XP), and a number in brackets at its end."""
    t = " ".join(text.split())
    for kind, rx in ((BONUS, _BONUS), (OWN, _OWN)):          # (bonus first: its text also contains "EXP")
        m = rx.search(t)
        if m:
            digits = re.sub(r"[^0-9]", "", m.group("n"))
            if digits:
                return kind, int(digits)
    return None


def read_lines(bgr, read_text):
    """The EXP messages in a picture of the message area: [(kind, value, y)], top to bottom.
    read_text(list of images) -> list of strings (one line of text each), e.g. ocr.Tesseract.read_lines."""
    mask = yellow_mask(bgr)
    boxes = find_lines(mask)
    if not boxes:
        return []
    texts = read_text([line_image(mask, b) for b in boxes])
    found = []
    for b, text in zip(boxes, texts):
        got = parse(text)
        if got:
            found.append((OTHER if got[0] == OWN else got[0], got[1], b[1]))     # (read from the yellow pixels only)
    return found


# ---------------------------------------------------------------------------------------------
# Reading the lines without OCR. The game draws these lines in one fixed pixel font, so every
# glyph looks exactly the same every time: the number is read by comparing each glyph with the
# glyphs below. A glyph that isn't one of them (a digit not seen yet, a faded line, something in
# front of the text) makes the whole line unreadable, and an unreadable line is just skipped.
_ART = {
    "(": "..#/.#./.#./#../#../#../#../#../.#./.#./..#",
    ")": "#../.#./.#./..#/..#/..#/..#/..#/.#./.#./#..",
    "+": "..#../..#../#####/..#../..#..",
    "0": ".###./#...#/#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "1": "..#/.##/#.#/..#/..#/..#/..#/..#/..#",
    "2": ".###./#...#/....#/....#/...#./...#./..#../.#.../#####",
    "3": ".###./#...#/....#/....#/..##./....#/....#/#...#/.###.",
    "5": ".####/.#.../#..../####./#...#/....#/....#/#...#/.###.",
    "6": ".###./#...#/#..../#.##./##..#/#...#/#...#/#...#/.###.",
    "7": "#####/...#./...#./..#../..#../..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/#...#/.###./#...#/#...#/#...#/.###.",
    "9": ".###./#...#/#...#/#...#/#..##/.##.#/....#/#...#/.###.",
}
_GLYPHS = {}
for _ch, _art in _ART.items():
    _g = np.array([[c == "#" for c in row] for row in _art.split("/")], bool)
    _GLYPHS[(_g.shape, _g.tobytes())] = _ch

_LABELS_ART = {
    "own": (
        "#.....#..........................................#..................#.....######.#.....#.#####......"
        ".#...#..............................................................#.....#.......#...#..#....#....."
        ".#...#...###...#...#.....#.#..###....##....###...#.#...#..###....##.#.....#.......#...#..#....#....."
        "..#.#...#...#..#...#.....##..#...#..#..#..#...#..#.#...#.#...#..#..##.....#........#.#...#....#....."
        "...#....#...#..#...#.....#...#...#..#.....#...#..#..#.#..#...#..#...#.....######....#....#####......"
        "...#....#...#..#...#.....#...#####..#.....#####..#..#.#..#####..#...#.....#........#.#...#.........."
        "...#....#...#..#...#.....#...#......#.....#......#..#.#..#......#...#.....#.......#...#..#.........."
        "...#....#...#..#...#.....#...#...#..#..#..#...#..#...#...#...#..#...#.....#.......#...#..#.........."
        "...#.....###....####.....#....###....##....###...#...#....###....####.....######.#.....#.#.........."
    ),
    "bonus": (
        "#####..................................######.#.....#.#####.......#...............#####.....#....######..########.....#...."
        "#....#.................................#.......#...#..#....#.....#................#....#...#.#...#.....#....#....#...#....."
        "#....#...###...#.##...#...#...###......#.......#...#..#....#....###..###...#.#....#....#...#.#...#.....#....#....#...#....."
        "#....#..#...#..##..#..#...#..#...#.....#........#.#...#....#.....#..#...#..##.....#....#...#.#...#.....#....#.....#.#......"
        "######..#...#..#...#..#...#..#.........######....#....#####......#..#...#..#......#####...#...#..######.....#......#......."
        "#....#..#...#..#...#..#...#...###......#........#.#...#..........#..#...#..#......#.......#####..#...#......#......#......."
        "#....#..#...#..#...#..#...#......#.....#.......#...#..#..........#..#...#..#......#.......#...#..#....#.....#......#......."
        "#....#..#...#..#...#..#...#..#...#.....#.......#...#..#..........#..#...#..#......#......#.....#.#....#.....#......#......."
        "#####....###...#...#...####...###......######.#.....#.#..........#...###...#......#......#.....#.#.....#....#......#......."
    ),
    "mesos": (
        "#.....#..................#..........................................#....................#..............................................."
        ".#...#...................#...............................................................#..............................................."
        ".#...#...###...#...#.....#.##....###..#...#..###.......##.#...###...#..#.##....###....##.#.....#.##..##....###....###....###....###......"
        "..#.#...#...#..#...#.....##..#..#...#.#...#.#...#.....#..##..#...#..#..##..#..#...#..#..##.....##..##..#..#...#..#...#..#...#..#...#....."
        "...#....#...#..#...#.....#...#......#..#.#..#...#.....#...#......#..#..#...#..#...#..#...#.....#...#...#..#...#..#......#...#..#........."
        "...#....#...#..#...#.....#...#...####..#.#..#####.....#...#...####..#..#...#..#####..#...#.....#...#...#..#####...###...#...#...###......"
        "...#....#...#..#...#.....#...#..#...#..#.#..#.........#...#..#...#..#..#...#..#......#...#.....#...#...#..#..........#..#...#......#....."
        "...#....#...#..#...#.....#...#..#..##...#...#...#.....#..##..#..##..#..#...#..#...#..#...#.....#...#...#..#...#..#...#..#...#..#...#....."
        "...#.....###....####.....#...#...##.#...#....###.......##.#...##.#..#..#...#...###....####.....#...#...#...###....###....###....###......"
    ),
}
_LABEL_W = {"own": 100, "bonus": 123, "mesos": 137}
_LABELS = {}
for _k, _rows in _LABELS_ART.items():
    _s = "".join(_rows)
    _w = _LABEL_W[_k]
    _LABELS[_k] = np.array([c == "#" for c in _s], bool).reshape(-1, _w)

TOPHAT_T, TOPHAT_MIN, TOPHAT_SIGMA = 14, 100, 2
RIGHT_PART = 0.78            # the lines end at the right edge of the picture: ")" is looked for in this right part
_PAREN_R = np.array([[c == "#" for c in row] for row in ")".join(["#../.#./.#./..#/..#/..#/..#/..#/.#./.#./#.."]).split("/")], bool)


def text_masks(bgr):
    """Two ways of picking the message text out of a picture: the yellow pixels (the party bonus line is
    saturated yellow) and the pixels brighter than their surroundings (the white and grey lines are see-through
    text over the game, so their colour changes with what is behind them)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = gray - cv2.GaussianBlur(gray, (0, 0), TOPHAT_SIGMA)
    bright = (diff > TOPHAT_T) & (gray >= TOPHAT_MIN)
    return [yellow_mask(bgr) > 0, bright]


def _mismatch(mask, glyph, x, y):
    h, w = glyph.shape
    if x < 0 or y < 0 or y + h > mask.shape[0] or x + w > mask.shape[1]:
        return 99
    return int((mask[y:y + h, x:x + w] != glyph).sum())


def _read_at(mask, xr, y):
    """Reads a line whose ")" ends at column xr (exclusive) and whose top is row y: (kind, value) or None."""
    x = xr - 3
    if _mismatch(mask, _PAREN_R_G, x, y) > 2:
        return None
    digits = ""
    while True:
        found = []
        for ch, g in _GLYPH_ARRAYS.items():
            if ch in "()":
                continue
            for gap in range(1, 5):                  # (the space between two glyphs varies a little: a 1 is narrow)
                m = _mismatch(mask, g, x - gap - g.shape[1], y + (2 if ch == "+" else 0))
                found.append((m, ch, g.shape[1] + gap))
        found.sort()
        if found[0][0] > 2 or any(f[0] - found[0][0] < 2 and f[1] != found[0][1] for f in found[1:]):
            return None
        _, ch, step = found[0]
        x -= step
        if ch == "+":
            break
        digits = ch + digits
        if len(digits) > 7:
            return None
    if not digits:
        return None
    px = None
    for gap in range(1, 4):
        if _mismatch(mask, _GLYPH_ARRAYS["("], x - gap - 3, y) <= 2:
            px = x - gap - 3
            break
    if px is None:
        return None
    for kind, lab in _LABELS.items():
        left = px - lab.shape[1]
        if left < 0 or lab.shape[0] > mask.shape[0] - y:
            continue
        window = mask[y:y + lab.shape[0], left:left + lab.shape[1]]
        ink = int(lab.sum())
        if int((lab & window).sum()) >= 0.88 * ink:
            return kind, int(digits)
    return None


_GLYPH_ARRAYS = {}
for _ch, _art2 in _ART.items():
    _GLYPH_ARRAYS[_ch] = np.array([[c == "#" for c in row] for row in _art2.split("/")], bool)
_PAREN_R_G = _GLYPH_ARRAYS[")"]


def read_messages(bgr):
    """The EXP and mesos message lines in a picture of the message area, read without OCR: [(kind, value, y)]
    (kind is "own" (white), "other" (a yellow "received" line), "bonus" or "mesos"), top to bottom. Every glyph and each line's label is compared with what
    the game draws; whatever doesn't match exactly enough is not read, never guessed."""
    found = {}
    h, w = bgr.shape[:2]
    for n, mask in enumerate(text_masks(bgr)):
        yellow = n == 0                                   # (the first mask is the yellow pixels)
        strip = mask[:, int(w * RIGHT_PART):].astype(np.float32)
        res = cv2.matchTemplate(strip, _PAREN_R_G.astype(np.float32), cv2.TM_SQDIFF)
        ys, xs = np.where(res <= 2)
        for y, x in zip(ys.tolist(), xs.tolist()):
            xr = int(w * RIGHT_PART) + x + 3
            got = _read_at(mask, xr, y)
            if got and not any(abs(y - fy) <= 3 for fy in found):
                found[y] = (OTHER if yellow and got[0] == OWN else got[0], got[1])   # (yellow "received" = a party member's kill)
    return [(k, v, y) for y, (k, v) in sorted(found.items())]


def new_lines(prev, cur):
    """The lines in `cur` (message lines on screen now, oldest first, as (kind, value)) that weren't in `prev`:
    old lines scroll up and fade away, new ones appear below. The same two values twice in a row are two lines
    when the older pair has moved up."""
    for k in range(len(prev) + 1):
        tail = prev[k:]
        if cur[:len(tail)] == tail:
            return cur[len(tail):]
    return cur


LINE_PITCH, FORGET_SECONDS = 14, 10.0


class LineTracker:
    """Tells which of the lines on screen are new. A line is the same line as one seen before when it has the
    same text and sits where that one was, or higher up by a whole number of lines (the lines only scroll up).
    A line that isn't seen for FORGET_SECONDS is forgotten, so the same text again later is a new line. Faded
    lines are read on some looks and not on others, which is why the text alone isn't enough."""

    def __init__(self):
        self.known = []                                  # [kind, value, y, last_seen]

    def update(self, lines, now):
        """lines: [(kind, value, y)] read now. Returns the new ones as [(kind, value)], oldest first."""
        self.known = [k for k in self.known if now - k[3] <= FORGET_SECONDS]
        taken, new = set(), []
        for kind, value, y in sorted(lines, key=lambda l: l[2]):          # top to bottom
            best = None
            for i, k in enumerate(self.known):
                if i in taken or k[0] != kind or k[1] != value or k[2] < y - 1:
                    continue
                gap = (k[2] - y) % LINE_PITCH
                if min(gap, LINE_PITCH - gap) > 1:
                    continue
                if best is None or k[2] < self.known[best][2]:
                    best = i                                               # (the one that moved least)
            if best is None:
                self.known.append([kind, value, y, now])
                taken.add(len(self.known) - 1)
                new.append((kind, value))
            else:
                self.known[best][2:] = [y, now]
                taken.add(best)
        return new


def split_gain(lines, gain, max_lines=8):
    """What the lines say about a gain of `gain` EXP on the bar: (own, bonus, other) or None.

    Walks the lines from the newest (bottom) up, adding their values, until the sum is exactly `gain`;
    then those lines are what happened since the last look. One kill is "received N" (the line above
    its bonus), so N + M is its whole gain. If no run of lines adds up to `gain` exactly the split
    is not believed."""
    total = own = bonus = other = 0
    for kind, value, _ in list(reversed(lines))[:max_lines]:
        total += value
        if kind == OWN:
            own += value
        elif kind == OTHER:
            other += value
        else:
            bonus += value
        if total == gain:
            return own, bonus, other
        if total > gain:
            return None
    return None
