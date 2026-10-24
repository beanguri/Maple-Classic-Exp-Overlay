"""Helpers for reading words (map and class names) with Tesseract."""
from collections import Counter

import cv2
import numpy as np

from maple_overlay.vision import engine


def _whiteness(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    return np.clip((hsv[..., 2] - 150) / 90, 0, 1) * np.clip((80 - hsv[..., 1]) / 50, 0, 1)

def _ocr_votes(whiteness, glyph_h, chars, clean):
    """Tesseract on white text at a few sizes, with and without smoothing (all in one run).
    Counts answers. chars: the only characters it may answer with, or None."""
    images = []
    for target in (36, 48, 60):
        s = target / glyph_h
        big = cv2.resize(whiteness, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for img in ((big > 0.5) * 255.0, np.clip(big, 0, 1) * 255):
            images.append(cv2.copyMakeBorder((255 - img).astype(np.uint8), 24, 24, 24, 24,
                                             cv2.BORDER_CONSTANT, value=255))
    votes = Counter()
    for text in engine.TESS.read_lines(images, chars):
        text = clean(text)
        if text:
            votes[text] += 1
    return votes
