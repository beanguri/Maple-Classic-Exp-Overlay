"""Tests for exp_messages.py: reading the game's EXP / mesos message lines from a picture, and matching
them to the EXP bar's gain.  Run from the project folder:  py -3 tests/test_exp_messages.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from maple_overlay.vision import exp_messages as em  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def draw(img, kind, value, y, xr=608, color=(235, 235, 235), gap_plus=2):
    """Draws a message line the way the game does: ")" at the right edge, digits, "+", "(" and the label."""
    def put(g, x, yy):
        h, w = g.shape
        img[yy:yy + h, x:x + w][g] = color
    x = xr - 3
    put(em._GLYPH_ARRAYS[")"], x, y)
    for i, ch in enumerate(reversed(str(value))):
        g = em._GLYPH_ARRAYS[ch]
        x -= 1 + g.shape[1]
        put(g, x, y)
    g = em._GLYPH_ARRAYS["+"]
    x -= gap_plus + g.shape[1]
    put(g, x, y + 2)
    x -= 1 + 3
    put(em._GLYPH_ARRAYS["("], x, y)
    lab = em._LABELS[kind]
    put(lab, x - lab.shape[1], y)


img = np.zeros((422, 614, 3), np.uint8)
draw(img, "own", 8, 324)
draw(img, "bonus", 2, 338, color=(0, 230, 230))
draw(img, "mesos", 12, 310)
got = em.read_messages(img)
check("reads own, bonus and mesos lines", got == [("mesos", 12, 310), ("own", 8, 324), ("bonus", 2, 338)])
img = np.zeros((422, 614, 3), np.uint8)
draw(img, "own", 1058, 338)
check("reads a four-digit number", em.read_messages(img) == [("own", 1058, 338)])
img = np.zeros((422, 614, 3), np.uint8)
noise = np.random.default_rng(1).integers(0, 255, img.shape, dtype=np.uint8)
check("noise reads as nothing", em.read_messages(noise) == [])
check("an empty picture reads as nothing", em.read_messages(np.zeros((422, 614, 3), np.uint8)) == [])

check("split: own + bonus equals the gain", em.split_gain([("own", 8, 1), ("bonus", 2, 2)], 10) == (8, 2, 0))
check("split: a sum that doesn't match is refused", em.split_gain([("own", 8, 1), ("bonus", 3, 2)], 10) is None)
check("split: a party member's kill is Party kills", em.split_gain([("other", 10, 1), ("bonus", 3, 2)], 13) == (0, 3, 10))
img2 = np.zeros((422, 614, 3), np.uint8)
draw(img2, "own", 19, 324)                                  # white: your own kill
draw(img2, "own", 10, 338, color=(0, 230, 230))             # yellow: a party member's kill
draw(img2, "bonus", 3, 352, color=(0, 230, 230))
import cv2  # noqa: E402
real = cv2.imread(str(Path(__file__).resolve().parent / "frames" / "messages_own_other.png"))
check("a real screenshot: white 'received' is yours, yellow is a party member's",
      [(k, v) for k, v, _ in em.read_messages(real)] == [("mesos", 7), ("own", 19), ("bonus", 6), ("other", 10), ("bonus", 3)])
check("a yellow 'received' line is a party kill, a white one Own",
      em.read_messages(img2) == [("own", 19, 324), ("other", 10, 338), ("bonus", 3, 352)])
check("new lines: scrolled screen", em.new_lines([("own", 8), ("bonus", 2)], [("bonus", 2), ("own", 5)]) == [("own", 5)])
check("new lines: same pair twice", em.new_lines([("own", 8), ("bonus", 2)],
                                                 [("own", 8), ("bonus", 2), ("own", 8), ("bonus", 2)]) == [("own", 8), ("bonus", 2)])

# which lines are new: the same lines read on every look, scrolling up, flickering, and a real repeat
lt = em.LineTracker()
frame1 = [("own", 8, 324), ("bonus", 2, 338)]
check("first look: both are new", lt.update(frame1, 0.0) == [("own", 8), ("bonus", 2)])
check("same look again: nothing new", lt.update(frame1, 0.25) == [])
check("a faded line not read this time: nothing new", lt.update([("bonus", 2, 338)], 0.5) == [])
check("it is read again: still nothing new", lt.update(frame1, 0.75) == [])
frame2 = [("own", 8, 296), ("bonus", 2, 310), ("own", 8, 324), ("bonus", 2, 338)]      # one kill later, same values
check("a second kill with the same numbers is new", lt.update(frame2, 1.0) == [("own", 8), ("bonus", 2)])
check("and is not new again", lt.update(frame2, 1.25) == [])
check("a flickering mesos line is counted once",
      [lt.update([("mesos", 7, 282)] if i % 2 == 0 else [], 2.0 + i * 0.25) for i in range(6)].count([("mesos", 7)]) == 1)
check("the same text 12 seconds later is new", lt.update([("mesos", 7, 282)], 20.0) == [("mesos", 7)])

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
