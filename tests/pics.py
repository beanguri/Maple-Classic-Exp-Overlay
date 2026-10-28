"""Pictures the tests make from the real EXP bar frames (tests/frames/exp_*.png): no new screenshots needed.
Import it after maple_exp (the tests do that first, on their own temp data folder)."""
import cv2
import numpy as np
import maple_exp as m
import level_ocr


def percent_only(bgr):
    """The same bar the way the game draws it past a million EXP: the number cut out, "EXP. [71.68%]"."""
    lay = m.find_exp(bgr)
    start, last = int(lay.box[0]), lay.exp[-1]
    return np.hstack([bgr[:, :start], bgr[:, int(last[0] + last[1] / 2) + 1:]])


def badge_scene(level, gh, label=True, box=True, name=None, shift=0, noise=0, label_width=2.4, scale=None):
    """A status bar with an "LV." badge drawn the way the game lays it out, far left of the EXP text
    (digits gh px tall). Returns (picture, x0, cy): where the EXP number would start and its middle line.
    The badge is the game's own: one 11 x 15 orange tile per digit with the digit in white (level_ocr's font), drawn at the
    UI scale gh / 7 (or `scale`): block for block at a whole scale, smoothed at any other.
    label/box: leave out the "LV." or the orange box; label_width: how wide the letters "LV" are, in digit heights (the
    game's are about 2.4). name: the character's name, drawn under the class
    (shift px to the right, noise: random grain, 0 for none)."""
    h, w = int(9 * gh), int(80 * gh)
    img = np.full((h, w, 3), (62, 56, 50), np.uint8)
    x0, cy = int(60 * gh), int(4 * gh)
    s = scale or gh / 7
    bh = int(15 * s)
    bx, by = int(x0 - 53.6 * gh), int(cy + 0.2 * gh)
    if box:
        n = len(str(level))
        design = np.zeros((level_ocr.TILE_H, level_ocr.TILE_STEP * n - 1, 3), np.float32)
        for i, digit in enumerate(str(level)):
            tile = design[:, level_ocr.TILE_STEP * i:level_ocr.TILE_STEP * i + level_ocr.TILE_W]
            tile[:level_ocr.BOX_H] = (17, 136, 255)                   # (BGR orange: the tile is 13 rows tall, 2 rows of shade below)
            tile[level_ocr.BOX_H:] = (62, 56, 50)
            tile[level_ocr.font()[digit] > 0] = (255, 255, 255)
        gap = [i for i in range(design.shape[1]) if i % level_ocr.TILE_STEP == level_ocr.TILE_W]
        design[:, gap] = (62, 56, 50)
        ow, oh = int(np.ceil(design.shape[1] * s)), int(np.ceil(level_ocr.TILE_H * s))
        if abs(s - round(s)) < 0.01:
            drawn = np.kron(design, np.ones((int(round(s)), int(round(s))) + (1,), np.float32))
        else:
            mat = np.array([[1 / s, 0, 0.5 / s - 0.5], [0, 1 / s, 0.5 / s - 0.5]], np.float32)
            drawn = cv2.warpAffine(design, mat, (ow, oh), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        img[by:by + drawn.shape[0], bx:bx + drawn.shape[1]] = np.clip(drawn, 0, 255).astype(np.uint8)
    if label:
        thick = max(1, round(gh / 5))
        scale = label_width * gh / cv2.getTextSize("LV", cv2.FONT_HERSHEY_SIMPLEX, 1, thick)[0][0]       # (the letters, not the dot)
        gap = (level_ocr.GAP_2 + 6 * (2 - len(str(level)))) * s          # (the badge is centred: its distance from the "LV" depends on the digits)
        cv2.putText(img, "LV.", (int(bx - gap - label_width * gh), by + bh), cv2.FONT_HERSHEY_SIMPLEX, scale, (240, 240, 240), thick)
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
