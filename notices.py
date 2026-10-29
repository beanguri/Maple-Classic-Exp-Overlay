"""Battle/loot notices on the right of the game: meso pickups and valued drops for mesos/hr.

Reads the floating yellow/white notice lines (not chat): things like
"You have gained mesos (+12345)" and "You have gained an item (Red Potion)".
EXP lines are ignored. A line that stays on screen is counted once; only newly
appeared lines add to mesos/hr.

Nothing here grabs the screen: read_strip() takes a crop and returns OCR text.
Pricing uses MeowDB Etc + potion/food NPC sell-back (assets/loot_prices.json),
with gaps filled by NPC sell ≈ half the potion-bar shop buy price.
"""
import json
import re
from collections import Counter, deque
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

import ocr

# Right-side strip relative to the game window (left, top, right, bottom as fractions).
# Tuned for Classic's floating battle notices; real screenshots may still want a nudge.
NOTICE_BOX = (0.58, 0.16, 0.98, 0.78)
ROLLING_WINDOW = 300
MATCH_MIN = 0.78                # how close an OCR item name must be to a known price name

# Classic / MapleBuddy-style notice lines (OCR may drop "You have", commas, etc.).
_MESO = re.compile(
    r"(?:gained|received)\s+mesos?\s*(?:\(?\s*\+?\s*([\d.,]+)\s*\)?|\s*\+?\s*([\d.,]+))",
    re.I,
)
# "You have gained mesos (+14)" with OCR gluing the number oddly, or just "+1,234 mesos"
_MESO_LOOSE = re.compile(r"\+?\s*([\d.,]{1,12})\s*\)?\s*mesos?\b", re.I)
_ITEM_PAREN = re.compile(
    r"gained\s+(?:an?\s+)?item\b(?:\s+in\s+the\s+\w+\s+tab)?\s*\(([^)]+)\)",
    re.I,
)
_ITEM_A = re.compile(r"gained\s+(?:an?\s+)(?!item\b)(.+?)(?:\.\s*\[|\s*\[\d|\s*$)", re.I)
_EXP = re.compile(r"(?:gained|received)\s+experience|\breceived\s+exp\b|\bexp\b\s*\(|\bfame\b", re.I)
_LOST = re.compile(r"\blost\b", re.I)
_BARE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9' .\-]{1,48}$")
_DIGITS = re.compile(r"[^\d]")


def crop_box(window_w, window_h, box=NOTICE_BOX):
    """(x, y, w, h) of the notice strip inside the game window."""
    l, t, r, b = box
    x0, y0 = int(l * window_w), int(t * window_h)
    x1, y1 = int(r * window_w), int(b * window_h)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def bright_mask(bgr):
    """Pixels that look like white or yellow notice text."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    white = (v >= 170) & (s <= 70)
    yellow = (v >= 150) & (s >= 40) & (h >= 12) & (h <= 45)
    return white | yellow


def find_line_boxes(mask, min_h=6, min_w=28, pad=2):
    """Horizontal text bands in a bright mask as (x, y, w, h), top to bottom."""
    if mask.size == 0 or not mask.any():
        return []
    rows = mask.any(axis=1)
    boxes, y = [], 0
    h = mask.shape[0]
    while y < h:
        while y < h and not rows[y]:
            y += 1
        y0 = y
        while y < h and rows[y]:
            y += 1
        y1 = y
        if y1 - y0 < min_h:
            continue
        band = mask[y0:y1]
        cols = band.any(axis=0)
        xs = np.flatnonzero(cols)
        if xs.size == 0:
            continue
        x0, x1 = int(xs[0]), int(xs[-1]) + 1
        if x1 - x0 < min_w:
            continue
        yy0, yy1 = max(0, y0 - pad), min(h, y1 + pad)
        xx0, xx1 = max(0, x0 - pad), min(mask.shape[1], x1 + pad)
        boxes.append((xx0, yy0, xx1 - xx0, yy1 - yy0))
    return boxes


def prep_line(mask_line):
    """One notice line as a padded inverted image for Tesseract (like map OCR)."""
    ink = mask_line.astype(np.float32)
    if ink.max() <= 0:
        return None
    # Target ~40 px tall glyphs: Classic notices are small on 1080p.
    h = max(1, ink.shape[0])
    scale = 40.0 / h
    big = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    img = (255 - np.clip(big, 0, 1) * 255).astype(np.uint8)
    return cv2.copyMakeBorder(img, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)


def clean_ocr(text):
    """One line of OCR: strip noise Tesseract often adds on notice text."""
    if not text:
        return ""
    text = text.replace("\n", " ").replace("|", "I").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _meso_amount(raw):
    digits = _DIGITS.sub("", raw or "")
    if not digits:
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    return n if 1 <= n <= 99_999_999 else None


def parse_line(text):
    """('meso', amount) or ('item', name) for a Classic notice, or None to ignore.

    Bare item-name lines (just 'Pig\\'s Ribbon' in yellow) are left as None here; the
    tracker prices them against the loot table so EXP/chat noise isn't treated as loot.
    """
    text = clean_ocr(text)
    if not text or _LOST.search(text) or _EXP.search(text):
        return None
    m = _MESO.search(text)
    if m:
        n = _meso_amount(m.group(1) or m.group(2))
        if n is not None:
            return ("meso", n)
    m = _MESO_LOOSE.search(text)
    if m and re.search(r"meso", text, re.I):
        n = _meso_amount(m.group(1))
        if n is not None:
            return ("meso", n)
    m = _ITEM_PAREN.search(text)
    if m:
        name = m.group(1).strip(" .")
        return ("item", name) if name else None
    m = _ITEM_A.search(text)
    if m:
        name = m.group(1).strip(" .")
        # Drop trailing "[N total]" style tails OCR sometimes glues on.
        name = re.sub(r"\s*\[\d.*$", "", name).strip(" .")
        return ("item", name) if len(name) >= 2 else None
    return None


def bare_item_name(text):
    """A line that is only an item name (no 'gained…'), or None."""
    text = clean_ocr(text)
    if not text or not _BARE_NAME.match(text):
        return None
    if _MESO.search(text) or _EXP.search(text) or _LOST.search(text):
        return None
    if re.search(r"\b(mesos?|exp|fame|level|map)\b", text, re.I):
        return None
    return text


def new_lines(prev, current):
    """Lines that appeared since the last look (Counter of normalized text → count).

    Returns (list of new line texts, Counter of what's on screen now).
    """
    now = Counter(current)
    fresh = []
    for line, n in now.items():
        for _ in range(max(0, n - prev.get(line, 0))):
            fresh.append(line)
    return fresh, now


def _price_map(raw):
    """Normalize a name→price object or [{name, price}, ...] list."""
    out = {}
    if isinstance(raw, dict):
        for name, price in raw.items():
            if isinstance(name, str) and type(price) is int and price >= 0:
                out[name] = price
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict) and isinstance(row.get("name"), str) and type(row.get("price")) is int:
                out[row["name"]] = row["price"]
    return out


def load_loot_tables(path):
    """(etc items, potions) name→NPC sell mesos from assets/loot_prices.json."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}
    except Exception:
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    if "items" in data or "potions" in data:
        return _price_map(data.get("items")), _price_map(data.get("potions"))
    # Legacy flat name → price map (pre-potions section).
    return _price_map(data), {}


def load_loot_prices(path):
    """{item name: NPC sell mesos} from assets/loot_prices.json (etc + potions)."""
    etc, potions = load_loot_tables(path)
    return {**etc, **potions}


def potion_sell_prices(potion_items, custom=None):
    """NPC sell ≈ half the shop buy price for potions/food that have a price.

    potion_items: {name: {"price", "unpriced", ...}} as PotionTable.prices.
    custom: player overrides of the buy price (sell is still half).
    """
    custom = custom or {}
    out = {}
    for name, info in (potion_items or {}).items():
        if not isinstance(info, dict):
            continue
        if name in custom:
            buy = custom[name]
        elif info.get("unpriced"):
            continue
        else:
            buy = info.get("price", 0)
        if type(buy) is int and buy > 0:
            out[name] = buy // 2
    for name, buy in custom.items():
        if name not in out and type(buy) is int and buy > 0:
            out[name] = buy // 2
    return out


def match_priced_name(raw, prices, aliases=None):
    """Best known price name for an OCR item string, or None if nothing is close enough."""
    if not raw or not prices:
        return None
    aliases = aliases or {}
    key = raw.strip()
    if key in prices:
        return key
    if key in aliases and aliases[key] in prices:
        return aliases[key]
    lower = {n.lower(): n for n in prices}
    if key.lower() in lower:
        return lower[key.lower()]
    best, score = None, 0.0
    for name in prices:
        s = SequenceMatcher(None, key.lower(), name.lower()).ratio()
        if s > score:
            best, score = name, s
    return best if score >= MATCH_MIN else None


def sell_value(name, prices, aliases=None):
    """NPC sell mesos for a dropped item name, or None if unpriced."""
    matched = match_priced_name(name, prices, aliases)
    return None if matched is None else prices[matched]


def read_strip(bgr, tess=None):
    """OCR the notice lines in a BGR crop. Returns a list of cleaned strings (may be empty).

    tess: an ocr.Tesseract (defaults to a module-level one only when tests pass one in).
    Raises ocr.TesseractError when Tesseract fails (caller decides).
    """
    if bgr is None or bgr.size == 0:
        return []
    mask = bright_mask(bgr)
    boxes = find_line_boxes(mask)
    images, kept = [], []
    for x, y, w, h in boxes:
        line = prep_line(mask[y:y + h, x:x + w])
        if line is not None:
            images.append(line)
            kept.append((x, y, w, h))
    if not images:
        return []
    reader = tess
    if reader is None:
        raise ocr.TesseractError("no Tesseract handed to read_strip")
    texts = reader.read_lines(images)
    return [clean_ocr(t) for t in texts if clean_ocr(t)]


class NoticeTracker:
    """Turns newly appeared notice lines into mesos gained per hour and per 5 minutes.

    value stays None (there is no inventory balance). gained is meso pickups plus the
    sell value of priced potions / common etc / common drops. Unpriced names are kept
    so the player can see what still needs a price.
    """

    def __init__(self, prices=None, aliases=None, potion_names=None):
        self.prices = dict(prices or {})
        self.aliases = dict(aliases or {})
        self.potion_names = set(potion_names or ())
        self.resets = 0
        self.reset()

    def reset(self):
        self.resets += 1
        self.value = None
        self.seen_at = None
        self.gained = 0
        self.mesos_gained = 0
        self.loot_gained = 0
        self.etc_gained = 0
        self.potion_gained = 0
        self.samples = deque()
        self.start = None
        self.read_elapsed = None
        self.counting = False
        self._visible = Counter()
        self.items = Counter()          # priced drop name → how many counted
        self.unpriced = []              # OCR names that had no sell price (unique, newest last)
        self.left_out = [0, 0]
        self.window_at = None
        self.misreads = []
        self.stale = False
        self.idle_pause = False
        self.bank_inventory = False
        self.bank_window = False
        self.last_lines = ()            # last OCR lines (for View capture / status)

    def pause(self, stale=True, bank_inventory=False):
        """Tracking paused: keep the visible-line set so a still-on-screen notice isn't recounted."""
        self.counting = False
        self.idle_pause = not stale
        self.stale = False
        self.bank_inventory = False
        self.bank_window = False

    def set_window(self, window, now):
        """No shop/storage/trade filtering in notice mode (pickups are already earnings)."""
        if window:
            self.window_at = now

    def stale_note(self, now, running):
        return None

    def ignored_note(self, now):
        return None

    def set_prices(self, prices, aliases=None, potion_names=None):
        self.prices = dict(prices or {})
        if aliases is not None:
            self.aliases = dict(aliases)
        if potion_names is not None:
            self.potion_names = set(potion_names)

    def observe_lines(self, lines, now, elapsed, running):
        """lines: OCR strings currently visible (empty list = strip looked empty)."""
        cleaned = [clean_ocr(t) for t in (lines or []) if clean_ocr(t)]
        self.last_lines = tuple(cleaned)
        fresh, self._visible = new_lines(self._visible, cleaned)
        self.seen_at = now
        if not running:
            return
        if self.start is None:
            self.start = elapsed
            if not self.samples:
                self.samples.append((elapsed, self.gained))
        self.counting = True
        self.read_elapsed = elapsed
        for text in fresh:
            ev = parse_line(text)
            if not ev:
                bare = bare_item_name(text)
                if bare:
                    ev = ("item", bare)
                else:
                    continue
            kind, payload = ev
            if kind == "meso":
                self._earn(payload, elapsed)
                self.mesos_gained += payload
            elif kind == "item":
                matched = match_priced_name(payload, self.prices, self.aliases)
                price = None if matched is None else self.prices[matched]
                if price is None:
                    if payload not in self.unpriced:
                        self.unpriced = (self.unpriced + [payload])[-20:]
                elif price > 0:
                    self._earn(price, elapsed)
                    self.loot_gained += price
                    if matched in self.potion_names:
                        self.potion_gained += price
                    else:
                        self.etc_gained += price
                    self.items[matched] += 1

    def _earn(self, amount, elapsed):
        self.gained += amount
        self.samples.append((elapsed, self.gained))
        while len(self.samples) > 2 and self.samples[1][0] <= elapsed - ROLLING_WINDOW:
            self.samples.popleft()

    def look(self):
        """(mesos gained so far, when the strip was last read), or None before the first look."""
        return None if self.seen_at is None else (self.gained, self.seen_at)

    def for_log(self):
        return dict(mesos=self.look(), window_at=self.window_at, misreads=self.misreads, meso_epoch=self.resets)

    def _rate(self, amount, elapsed):
        end = self.read_elapsed if self.read_elapsed is not None else elapsed
        if self.start is None or end - self.start < 30:
            return None
        return amount / (end - self.start) * 3600

    def per_hour(self, elapsed):
        """Total farm value/hr (meso pickups + valued etc)."""
        return self._rate(self.gained, elapsed)

    def mesos_per_hour(self, elapsed):
        """Liquid meso pickups only."""
        return self._rate(self.mesos_gained, elapsed)

    def loot_per_hour(self, elapsed):
        """NPC sell value of priced drops / hr (etc + potions)."""
        return self._rate(self.loot_gained, elapsed)

    def etc_per_hour(self, elapsed):
        return self._rate(self.etc_gained, elapsed)

    def potion_loot_per_hour(self, elapsed):
        return self._rate(self.potion_gained, elapsed)

    def per_5min(self, elapsed):
        if self.start is None:
            return None
        end = self.read_elapsed if self.read_elapsed is not None else elapsed
        t0 = max(self.start, end - ROLLING_WINDOW)
        if end - t0 < 60:
            return None
        return (self.gained - self._gained_at(t0)) / (end - t0) * ROLLING_WINDOW

    def _gained_at(self, t0):
        before = self.samples[0] if self.samples else (self.start, 0)
        for t, g in self.samples:
            if t > t0:
                if t == before[0]:
                    return g
                return before[1] + (g - before[1]) * (t0 - before[0]) / (t - before[0])
            before = (t, g)
        return before[1]
