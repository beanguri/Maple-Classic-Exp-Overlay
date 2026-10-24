"""Counting the party bars (how many players are in the party)."""
import cv2
import numpy as np


def find_party_bars(bgr, gh, hsv=None):
    """HP bars of the party list's members: the longest evenly spaced column of identical
    HP-bar outlines. A single bar (or none) means you're on your own."""
    val = (cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2]
    n, _, stats, _ = cv2.connectedComponentsWithStats((val < 70).astype(np.uint8), connectivity=8)
    bars = sorted((tuple(map(int, stats[i][:4])) for i in range(1, n)
                   if 7.5 * gh <= stats[i][2] <= 10.5 * gh and 1.3 * gh <= stats[i][3] <= 1.85 * gh),
                  key=lambda b: b[1])
    best = []
    for i, first in enumerate(bars):
        same = [b for b in bars[i:] if abs(b[0] - first[0]) <= 0.5 * gh
                and abs(b[2] - first[2]) <= 0.5 * gh and abs(b[3] - first[3]) <= 0.3 * gh]
        column, pitch = [first], None
        for b in same[1:]:
            step = b[1] - column[-1][1]
            if pitch is None and 1.8 * gh <= step <= 3.0 * gh:
                pitch = step
            if pitch is None or abs(step - pitch) > 0.3 * gh:
                break
            column.append(b)
        if len(column) > len(best):
            best = column
    return best if len(best) >= 2 else best[:1]
