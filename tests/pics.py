"""Pictures the tests make from the real EXP bar frames (tests/frames/exp_*.png): no new screenshots needed.
Import it after maple_exp (the tests do that first, on their own temp data folder)."""
import cv2
import numpy as np
import appmod as m


def percent_only(bgr):
    """The same bar the way the game draws it past a million EXP: the number cut out, "EXP. [71.68%]"."""
    lay = m.find_exp(bgr)
    start, last = int(lay.box[0]), lay.exp[-1]
    return np.hstack([bgr[:, :start], bgr[:, int(last[0] + last[1] / 2) + 1:]])


def badge_scene(level, gh, label=True, box=True, name=None, shift=0, noise=0, label_width=2.4):
    """A status bar with an "LV." badge drawn the way the game lays it out, far left of the EXP text
    (digits gh px tall). Returns (picture, x0, cy): where the EXP number would start and its middle line.
    label/box: leave out the "LV." or the orange box; label_width: how wide the letters "LV" are, in digit heights (the
    game's are about 2.4). name: the character's name, drawn under the class
    (shift px to the right, noise: random grain, 0 for none)."""
    h, w = int(9 * gh), int(80 * gh)
    img = np.full((h, w, 3), (62, 56, 50), np.uint8)
    x0, cy = int(60 * gh), int(4 * gh)
    n = len(str(level))
    bh = int(1.75 * gh)
    cell = bh * 0.87                                  # one orange square per digit (as the game draws it)
    bx, by = int(x0 - 53.6 * gh), int(cy + 0.2 * gh)
    if box:
        for i, digit in enumerate(str(level)):
            left = bx + round(i * cell)
            right = bx + round((i + 1) * cell) - 1
            cv2.rectangle(img, (left, by), (right, by + bh), (59, 129, 203), -1)
            ink = (250, 240, 235)
            mid, top, low = (left + right) // 2, by + int(0.2 * bh), by + int(0.8 * bh)
            if digit == "1":                          # a narrow stroke, like the game's 1
                cv2.rectangle(img, (mid, top), (mid + max(0, int(0.08 * bh)), low), ink, -1)
            else:                                     # any other digit fills most of its square
                half = max(2, int(0.30 * cell))
                cv2.rectangle(img, (mid - half, top), (mid + half, low), ink, max(1, int(0.12 * bh)))
    if label:
        thick = max(1, round(gh / 5))
        scale = label_width * gh / cv2.getTextSize("LV", cv2.FONT_HERSHEY_SIMPLEX, 1, thick)[0][0]       # (the letters, not the dot)
        cv2.putText(img, "LV.", (int(bx - (1.7 + label_width) * gh), by + bh), cv2.FONT_HERSHEY_SIMPLEX, scale, (240, 240, 240), thick)
    if name:
        scale = 1.2 * gh / 22
        cv2.putText(img, name, (int(x0 - 49 * gh) + shift, int(cy + 2.35 * gh)), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (235, 235, 235), max(1, round(gh / 8)))
    if noise:
        grain = np.random.default_rng(1).normal(0, noise, img.shape)
        img = np.clip(img + grain, 0, 255).astype(np.uint8)
    return img, x0, cy


def relabelled(bgr, text):
    """The percentage-only bar with its "EXP." label wiped, and `text` (HP, MP...) drawn there instead."""
    pic = percent_only(bgr)
    lay = m.find_exp(pic)
    x1, y0, y1 = int(lay.box[0]), int(lay.cy - 1.6 * lay.gh), int(lay.cy + 1.6 * lay.gh)
    pic[y0:y1, :x1] = np.median(pic[y0:y1, :2].reshape(-1, 3), axis=0)
    if text:
        h = int(lay.gh * 1.3)
        cv2.putText(pic, text, (2, int(lay.cy + h / 2)), cv2.FONT_HERSHEY_SIMPLEX, h / 22, (255, 255, 255), 1)
    return pic
