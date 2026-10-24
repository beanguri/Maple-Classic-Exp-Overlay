"""Reading the map's name from the minimap and snapping it to the game's own spelling."""
import re
from pathlib import Path

import cv2
import numpy as np

from maple_overlay.config import MAPS_PATH
from maple_overlay.vision.exp_bar import _blobs
from maple_overlay.vision.text import _ocr_votes, _whiteness


def find_map_box(bgr, gh):
    """(x, y, w, h) of the minimap's info box in a shot of the window's top-left, or None."""
    hue, sat, val = [c.astype(np.int16) for c in cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))]
    box = (abs(hue - 100) <= 8) & (abs(sat - 64) <= 30) & (abs(val - 204) <= 30)
    n, _, stats, _ = cv2.connectedComponentsWithStats(box.astype(np.uint8), connectivity=8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = map(int, stats[i][:4])
    h = min(h, int(round(7.3 * gh)))            # just the info box, not the whole minimap
    return (x, y, w, h) if w >= 10 * gh and h >= 4 * gh else None

_ROMAN = re.compile(r"^(?:X{0,2})(?:IX|IV|V?I{0,3})$")

def _fix_roman(name):
    """A last word that is a Roman numeral misread ("Vil" -> VII, "lV" -> IV, "il" -> II, "V1" -> VI). Plain numbers
    ("Free Market 11") and ordinary words are left alone. The site does the same (server/src/mapnames.js)."""
    def fix(m):
        token = m.group(2)
        if not re.search(r"[IVXivxl|!]", token):
            return m.group(0)                          # only digits: a number
        numeral = re.sub(r"[li1|!]", "I", token, flags=re.I).upper()
        return m.group(1) + numeral if numeral and _ROMAN.match(numeral) else m.group(0)
    return re.sub(r"(\s)([IVXivxl1|!]{1,6})$", fix, name)

def _clean_map_name(text):
    t = text.replace("|", "I")                         # a lone "I" (as in "... I") reads as "|"
    t = re.sub(r"(?<=\s)[!\[\]](?=\s*$)", "I", t)
    t = re.sub(r"[^A-Za-z0-9 '<>:.&()\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    t = _fix_roman(t)                                  # Roman numerals read as l's, 1's, i's ("Vil" is VII)
    t = re.sub(r"(^|\s)[IiLl]st(?=\s|$)", r"\g<1>1st", t)     # "ist Accompaniment" is "1st Accompaniment"
    t = re.sub(r"(^|\s)[Zz]nd(?=\s|$)", r"\g<1>2nd", t)
    t = re.sub(r"(^|\s)[SsBb5]rd(?=\s|$)", r"\g<1>3rd", t)
    return t if len(t) >= 3 else ""

def _map_key(name):
    """Only letters and digits, lower case: names that differ in punctuation are the same map."""
    return re.sub(r"[^a-z0-9]", "", name.lower())

def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = row
    return prev[-1]

class MapList:
    """The game's own map names (assets/maps.txt, from meowdb.com), to spell a map the way the game
    does and to know the towns, where nobody farms. It helps; it isn't a gate: a name that isn't
    in it (an event map, a new map) is used as it was read. The site applies the same rules."""

    def __init__(self, path=MAPS_PATH):
        self.entries, self.by_key = [], {}
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            name, _, town = line.partition("\t")
            style = _clean_map_name(name)               # as the overlay writes names (no commas)
            if style:
                entry = (style, _map_key(name), town.strip() == "1", _numbers(style))
                self.entries.append(entry)
                self.by_key[entry[1]] = entry

    def find(self, read):
        """The list's entry (name, key, is_town, numbers) for a name as read, or None."""
        key = _map_key(read)
        if key in self.by_key:
            return self.by_key[key]
        nums, best, second = _numbers(read), None, float("inf")
        for e in self.entries:
            if e[3] != nums or abs(len(e[1]) - len(key)) > 2:
                continue
            d = _edit_distance(key, e[1])
            if best is None or d < best[0]:
                second = best[0] if best else second
                best = (d, e)
            elif d < second:
                second = d
        # one clear match: a letter misread (two in a long name). Two names about as close, or too far, are left alone.
        allowed = 2 if len(key) >= 24 else 1
        return best[1] if best and best[0] <= allowed and second - best[0] >= 1 else None

_MAP_LIST = None

def map_list():
    global _MAP_LIST
    if _MAP_LIST is None:
        _MAP_LIST = MapList()
    return _MAP_LIST

def snap_map_name(name):
    """The game's spelling of a map name as read (or the name as it is, if it isn't on the list)."""
    e = map_list().find(name)
    return e[0] if e else name

def is_town(name):
    """Is this one of the game's towns? (A name not on the list isn't.)"""
    e = map_list().by_key.get(_map_key(name or ""))
    return bool(e and e[2])

def read_map_name(bgr, gh):
    """The map name (the lower line of white text) from a shot of the minimap's info box."""
    white = _whiteness(bgr[:, int(0.85 * bgr.shape[0]):])      # skip the map's icon
    _, blobs = _blobs(white > 0.5)
    glyphs = sorted((b for b in blobs if 0.3 * gh <= b.h <= 1.6 * gh and b.w <= 2.5 * gh),
                    key=lambda b: b.y + b.h / 2)
    lines = []
    for b in glyphs:
        last = lines[-1][-1] if lines else None
        if last and abs(b.y + b.h / 2 - (last.y + last.h / 2)) <= 0.5 * gh:
            lines[-1].append(b)
        else:
            lines.append([b])
    lines = [ln for ln in lines if len(ln) >= 3]
    if not lines:
        return None
    ln = lines[-1]
    x0, x1 = max(0, min(b.x for b in ln) - 2), max(b.x + b.w for b in ln) + 2
    y0, y1 = max(0, min(b.y for b in ln) - 2), max(b.y + b.h for b in ln) + 2
    votes = _ocr_votes(white[y0:y1, x0:x1], max(b.h for b in ln), None, _clean_map_name)
    name, n = votes.most_common(1)[0] if votes else (None, 0)
    return snap_map_name(name) if n >= 2 else None

def _numbers(name):
    """Roman numerals and numbers in a map name. Maps that differ in these are different maps."""
    return re.findall(r"\b(?:[IVX]+|\d+)\b", name)
