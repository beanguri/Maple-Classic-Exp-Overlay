"""Reading the character's class from the status bar."""
import re
from difflib import SequenceMatcher

import cv2
import numpy as np

from maple_overlay.vision.exp_bar import _blobs
from maple_overlay.vision.text import _ocr_votes, _whiteness


# Job names as the status bar shows them (old-style, as in Classic World). A reading is
# matched to the closest of these; a clear reading that matches none is kept as read.
JOBS = ["Beginner",
        "Swordman", "Fighter", "Crusader", "Hero", "Page", "White Knight", "Paladin",
        "Spearman", "Dragon Knight", "Dark Knight",
        "Magician", "Wizard (Fire,Poison)", "Mage (Fire,Poison)", "Arch Mage (Fire,Poison)",
        "Wizard (Ice,Lightning)", "Mage (Ice,Lightning)", "Arch Mage (Ice,Lightning)",
        "Cleric", "Priest", "Bishop",
        "Archer", "Hunter", "Ranger", "Bowmaster", "Crossbowman", "Sniper", "Marksman",
        "Rogue", "Assassin", "Hermit", "Night Lord", "Bandit", "Chief Bandit", "Shadower",
        "Pirate", "Brawler", "Marauder", "Buccaneer", "Gunslinger", "Outlaw", "Corsair",
        "Warrior", "Bowman", "Thief"]

def _clean_job(text):
    t = re.sub(r"[^A-Za-z(),/ ]", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 3 else ""

def read_job(bgr, x0, cy, g, debug=None):
    """The job name in the status bar (e.g. 'Bandit'), from a shot where the EXP number's
    first digit is at x0 and its middle at cy, or None if it can't be read clearly.
    debug: a dict to fill with what it looked at ("image", "boxes") and, if it gave up, "why".

    The job sits on the line of the HP bar's label ("HP"), right before it; the character
    name is on the line under it and is never read."""
    ry, rx = max(0, int(cy - 2.5 * g)), max(0, int(x0 - 64 * g))
    reg = bgr[ry:int(cy + 3 * g), rx:max(0, int(x0 - 20 * g))]
    if debug is not None:
        debug.clear()
        debug.update(image=reg, boxes=[], why="")

    def give_up(why):
        if debug is not None:
            debug["why"] = why
        return None

    if reg.size == 0:
        return give_up("the status bar is off screen here")
    white = _whiteness(reg)
    _, blobs = _blobs(white > 0.3)                    # thin letters: a gentle white check
    # The HP bar: 13-16 EXP digits wide, its left end 31-35 of them before the EXP number
    # (the region starts 64 before it). Not to be mixed up with the panels' light borders.
    bars = [b for b in blobs if 9 * g <= b.w <= 20 * g and 1.4 * g <= b.h <= 2.8 * g
            and 24 * g <= b.x <= 39 * g]
    if not bars:
        return give_up("can't find the HP bar next to the class name")
    hp = min(bars, key=lambda b: b.x)                 # the HP bar (the MP bar is right of it)
    if debug is not None:
        debug["boxes"].append((hp.x, hp.y, hp.x + hp.w, hp.y + hp.h, "mesos"))
    label = [b for b in blobs if hp.x - 0.5 * g <= b.x <= hp.x + 3 * g
             and b.y + b.h <= hp.y + 0.2 * g and 0.6 * g <= b.h <= 1.6 * g]
    if not label:
        return give_up("can't find the HP label")
    top, bottom = min(b.y for b in label), max(b.y + b.h for b in label)
    row = sorted((b for b in blobs if b.x + b.w <= hp.x - g and b.y >= top - 0.3 * g
                  and b.y + b.h <= bottom + 0.4 * g and b.h >= 0.3 * g), key=lambda b: b.x)
    if len(row) < 2:
        return give_up("no class name found left of the HP bar")
    words = [[row[0]]]
    for b in row[1:]:
        if b.x - max(c.x + c.w for c in words[-1]) > 2.0 * g:
            words.append([b])
        else:
            words[-1].append(b)
    word = words[-1]                                  # the letters right before "HP"
    x_a, x_b = max(0, min(b.x for b in word) - int(0.4 * g)), max(b.x + b.w for b in word) + int(0.4 * g)
    if debug is not None:
        debug["boxes"].append((x_a, top, x_b, bottom, "exp"))
    crop = white[max(0, top - int(0.4 * g)):bottom + int(0.4 * g), x_a:x_b]
    glyph_h = max(b.h for b in word)
    if glyph_h > 10:                                  # big screens: read at small-window size,
        k = 10.0 / glyph_h                            # where Tesseract reads this font best
        crop, glyph_h = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA), 10.0
    votes = _ocr_votes(crop.astype(np.float32), glyph_h, None, _clean_job)
    text, n = votes.most_common(1)[0] if votes else ("", 0)
    if n < 2:
        return give_up("the tries didn't agree on what it says" + (f" (best guess: {text})" if text else ""))
    score, best = max((SequenceMatcher(None, text.lower(), j.lower()).ratio(), j) for j in JOBS)
    if score >= 0.72:
        return best
    if n >= 3:
        return text.title()
    return give_up(f"it reads \"{text}\", which isn't a class it knows")
