"""Tests for finding things on screen: the real party list and inventory (cut from real
screenshots at three UI sizes) pasted over many kinds of background, solid and see-through.

Not finding something on a hard background is allowed (the tracker just waits for a
clearer look). A wrong answer, like a wrong mesos count or party size, is not.

Run from the project folder:  py -3 tests/test_screen.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import appmod as m  # noqa: E402
assert Path(os.environ["MAPLE_EXP_DATA"]) == m.DATA_DIR, "tests must run on their own temp data folder, never on real data"

FRAMES = Path(__file__).parent / "frames"
PARTS = json.loads((FRAMES / "screen_parts.json").read_text())
rnd = random.Random(1)
failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def backgrounds(h, w):
    """Plain, busy and tricky backgrounds (white like the box, gold like the coin, ...)."""
    g = np.random.default_rng(rnd.randint(0, 10 ** 6))
    yy, xx = np.mgrid[0:h, 0:w]
    blobs = cv2.GaussianBlur(g.integers(0, 256, (h, w, 3), dtype=np.uint8), (0, 0), 6)
    return {"black": np.zeros((h, w, 3), np.uint8), "white": np.full((h, w, 3), 255, np.uint8),
            "dark gray": np.full((h, w, 3), 45, np.uint8), "light gray": np.full((h, w, 3), 215, np.uint8),
            "gold": np.full((h, w, 3), (40, 190, 230), np.uint8),
            "noise": g.integers(0, 256, (h, w, 3), dtype=np.uint8),
            "dark noise": g.integers(0, 70, (h, w, 3), dtype=np.uint8),
            "blobs": cv2.normalize(blobs, None, 0, 255, cv2.NORM_MINMAX),
            "stripes": ((xx // 7 % 2) * 255).astype(np.uint8)[..., None].repeat(3, 2),
            "gradient": (xx * 255 // max(w, 1)).astype(np.uint8)[..., None].repeat(3, 2)}


# ---- mesos: found and read right (or not at all), solid and see-through
reader = m.ScreenReader(m.DigitTemplates.load())
templates = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz").templates()
for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    counts = {"solid read": [0, 0], "see-through read": [0, 0], "found see-through": [0, 0]}
    wrong = []
    for alpha in (1.0, 0.75, 0.6, 0.5, 0.4):
        for bg_name, bg in backgrounds(int(h * 1.6), int(w * 1.8)).items():
            oy, ox = rnd.randint(0, bg.shape[0] - h), rnd.randint(0, bg.shape[1] - w)
            img = bg.astype(np.float32)
            img[oy:oy + h, ox:ox + w] = alpha * inv + (1 - alpha) * img[oy:oy + h, ox:ox + w]
            img = img.astype(np.uint8)
            box = (ox + bx, oy + by, bw, bh)
            kind = "solid read" if alpha == 1 else "see-through read"
            got = m.read_mesos_positions(img, box, templates)
            counts[kind][0] += got is not None and got[0] == info["mesos"]
            counts[kind][1] += 1
            if got and got[0] != info["mesos"]:
                wrong.append(f"read {got[0]} ({bg_name}, {alpha:.0%} visible)")
            found = reader._find_seethrough_box(img, gh)
            if found:
                read = m.read_mesos_positions(img, found, templates)
                if not read or read[0] != info["mesos"]:
                    wrong.append(f"found a box that reads {read and read[0]} ({bg_name}, {alpha:.0%} visible)")
                counts["found see-through"][0] += 1
            counts["found see-through"][1] += 1
            solid = m.find_mesos_field(img, gh)
            if solid and reader._readable(img, solid):
                read = m.read_mesos_positions(img, solid, templates)
                if read and read[0] != info["mesos"]:
                    wrong.append(f"solid finder: read {read[0]} ({bg_name}, {alpha:.0%} visible)")
    size = f"EXP digits {gh:.0f} px"
    check(f"mesos never read wrong, UI size {size} ({', '.join(wrong) or 'no wrong reads'})", not wrong)
    ok, total = counts["solid read"]
    check(f"mesos read on a solid inventory, {size}: {ok}/{total}", ok == total)
    ok, total = counts["see-through read"]
    check(f"mesos read on a see-through inventory, {size}: {ok}/{total}", ok >= 0.85 * total)
    ok, total = counts["found see-through"]
    print(f"     (mesos box found by its outline, {size}: {ok}/{total})")

# ---- storage and trade windows show a second mesos box: never read the wrong one
class FakeScreen:
    """Stands in for mss: one monitor showing `image` (the game), grab() cuts out of it."""
    def __init__(self, image):
        self.image = image
        self.monitors = [{"left": 0, "top": 0, "width": image.shape[1], "height": image.shape[0]}]

    def grab(self, r):
        part = self.image[r["top"]:r["top"] + r["height"], r["left"]:r["left"] + r["width"]]
        return np.dstack([part, np.full(part.shape[:2], 255, np.uint8)])


for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    size = f"EXP digits {gh:.0f} px"
    one_ok = two_ok = total = 0
    for bg_name, bg in backgrounds(int(h * 1.3), int(w * 3.2)).items():
        total += 1
        alone, both = bg.copy(), bg.copy()
        alone[:h, :w] = inv
        both[:h, :w] = inv
        both[:h, w + 10:2 * w + 10] = inv                         # storage's box beside yours
        one_ok += len(m.find_mesos_fields(alone, gh)) == 1
        two_ok += len(m.find_mesos_fields(both, gh)) == 2
    check(f"one inventory shows one mesos box, {size}: {one_ok}/{total}", one_ok == total)
    check(f"an inventory beside a storage / trade window shows two, {size}: {two_ok}/{total}", two_ok == total)

    # the reader follows along: finds yours, lets go while a second box is there, finds it again
    ww, wh = 3 * w + 20, int(h * 1.3)
    scene = np.full((wh, ww, 3), 25, np.uint8)
    scene[:h, :w] = inv
    reader = m.ScreenReader(m.DigitTemplates.load())
    reader.gh = gh
    reader.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz")
    blank = m.Reading(*([None] * len(m.Reading._fields)))
    def look(image):
        reader.next_scan = 0.0
        return reader._read_extras(FakeScreen(image), (0, 0, ww, wh), blank)
    look(scene)
    check(f"finds the box with only the inventory open, {size}", reader.mesos_box == (bx, by, bw, bh))
    crowded = scene.copy()
    crowded[:h, w + 10:2 * w + 10] = inv
    look(crowded)
    r = look(crowded)
    check(f"drops it and reads nothing while a second box is there, {size}",
          reader.mesos_box is None and reader.two_boxes and r.mesos is None)
    look(scene)
    check(f"finds it again once the storage is closed, {size}", reader.mesos_box == (bx, by, bw, bh) and not reader.two_boxes)

# ---- the shop, storage and trade windows are told apart by their buttons: drawn here with the
# game's colours (cut from the real windows), at three screen sizes, on many backgrounds
ORANGE, LIME, BLUE = (58, 135, 233), (39, 199, 177), (228, 173, 71)       # BGR


def money_window_picture(kind, width, bg, scale=None, at=None):
    """A background with the buttons of one money window on it (kind None: no window).
    scale: buttons' size against a 5120 px wide screen; at: where their top left is."""
    s = scale or width / 5120
    img = bg.copy()
    x, y = at or (int(rnd.uniform(0.2, 0.4) * width), int(rnd.uniform(0.1, 0.3) * width * 9 / 16))
    bw, bh, pitch = int(246 * s), int(51 * s), int(75 * s)

    def button(bx, by, colour):
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), colour, -1)
        for k in range(3):                                      # the white label inside
            tx = bx + int((0.15 + 0.22 * k) * bw)
            cv2.rectangle(img, (tx, by + bh // 3), (tx + max(2, bw // 8), by + 2 * bh // 3), (255, 255, 255), -1)

    if kind in ("shop", "storage", "trade", "lonely", "mismatch"):    # the windows are solid panels
        cv2.rectangle(img, (x - bh, y - bh), (x + int(1100 * s), y + 3 * pitch + bh), (205, 215, 215), -1)
    if kind == "shop":
        button(x, y, (100, 200, 150)); button(x, y + pitch, ORANGE); button(x + int(860 * s), y + pitch, ORANGE)
    elif kind == "storage":
        button(x, y, LIME); button(x, y + pitch, ORANGE); button(x, y + 2 * pitch, BLUE)
        button(x + int(780 * s), y, ORANGE)
    elif kind == "trade":
        button(x, y, ORANGE); button(x, y + pitch, (74, 215, 198))
    elif kind == "chat":                                        # orange chat lines are not buttons
        for k in range(6):
            cv2.rectangle(img, (x, y + k * int(30 * s)), (x + int(100 * s), y + k * int(30 * s) + int(24 * s)), ORANGE, -1)
    elif kind == "lonely":                                      # one orange button and nothing else
        button(x, y, ORANGE)
    elif kind == "mismatch":                                    # orange over green, different widths
        button(x, y, ORANGE)
        cv2.rectangle(img, (x, y + pitch), (x + bw // 2, y + pitch + bh), LIME, -1)
    return img


for width in (5120, 1920, 1280):
    wrong, total = [], 0
    for kind in ("shop", "storage", "trade", None, "chat", "lonely", "mismatch"):
        for bg_name, bg in backgrounds(width * 9 // 16, width).items():
            total += 1
            got = m.find_money_window(money_window_picture(kind, width, bg))
            if got != (kind if kind in ("shop", "storage", "trade") else None):
                wrong.append(f"{kind} seen as {got} on {bg_name}")
    check(f"shop, storage and trade windows are told apart, {width} px wide: {total - len(wrong)}/{total} "
          f"({', '.join(wrong[:4]) or 'all right'})", not wrong)

# ---- in a storage window the reader reads the right-hand box (yours), and looks often while
# a money window is open
for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    size = f"EXP digits {gh:.0f} px"
    ww, wh = 3 * w + 20, int(h * 1.3)
    reader = m.ScreenReader(m.DigitTemplates.load())
    reader.gh = gh
    reader.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz")
    blank = m.Reading(*([None] * len(m.Reading._fields)))
    scene = np.full((wh, ww, 3), 25, np.uint8)
    scene[:h, :w] = inv
    scene[:h, w + 10:2 * w + 10] = inv                              # the storage's box and yours
    storage = money_window_picture("storage", ww, scene, scale=gh / 28, at=(2 * w + 40, int(0.1 * h)))
    clock = [1000.0]
    real_monotonic = m.time.monotonic
    m.time.monotonic = lambda: clock[0]
    try:
        reader.next_scan = 0.0
        r = reader._read_extras(FakeScreen(storage), (0, 0, ww, wh), blank)
        check(f"in a storage window the right-hand box is read as yours, {size}",
              r.window == "storage" and reader.mesos_box == (w + 10 + bx, by, bw, bh) and not reader.two_boxes)
        check(f"the status line says which window is open, {size}", "Storage window open" in (r.note or ""))
        check(f"a window's box is not remembered as the inventory's, {size}", r.mesos_at is None)
        check(f"it looks again within {m.WINDOW_SCAN_SECONDS} s while the window is open, {size}",
              reader.next_scan - clock[0] <= m.WINDOW_SCAN_SECONDS)
        clock[0] += 2
        r = reader._read_extras(FakeScreen(scene), (0, 0, ww, wh), blank)
        check(f"the window still counts for {m.WINDOW_GRACE} s after it closes, {size}", r.window == "storage")
        clock[0] += m.WINDOW_GRACE + 1
        reader.next_scan = 0.0
        r = reader._read_extras(FakeScreen(scene), (0, 0, ww, wh), blank)
        check(f"...and then it is gone, its box is dropped and the normal rate returns, {size}",
              r.window is None and reader.mesos_box is None and reader.next_scan - clock[0] > m.WINDOW_SCAN_SECONDS)
    finally:
        m.time.monotonic = real_monotonic

# ---- the trade window's chat panel can cover the coin: the box is found by its size
for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    scene = np.full((h + 40, w + 40, 3), 25, np.uint8)
    scene[:h, :w] = inv
    cut = scene.copy()
    cut[:, :bx + bw // 4] = 90                                      # the panel hides a quarter of the box and the coin
    offer = (int(12.5 * gh), bh)                                    # the small offer boxes of the trade window
    cv2.rectangle(cut, (5, h + 5), (5 + offer[0], h + 5 + offer[1]), (255, 255, 255), -1)
    found = m.find_trade_mesos_box(cut, gh)
    check(f"a trade window's covered mesos box is found and the offer boxes aren't, EXP digits {gh:.0f} px",
          found is not None and found[0] + found[2] == bx + bw and found[1] == by)

# ---- the potion bar: drawn with simple icons and the game's count glyphs, at three screen sizes
from maple_overlay.vision import potions  # noqa: E402

DIGITS = potions.load_digits(Path(m.ASSETS) / "potion_digits.npz")


def draw_icon(slot, kind):
    """A made-up icon in a slot (BGR), clear of the key label (top left) and the digits (bottom left)."""
    h, w = slot.shape[:2]
    c = (int(w * 0.62), int(h * 0.42))
    if kind == "red":
        cv2.ellipse(slot, c, (int(w * 0.28), int(h * 0.26)), 25, 0, 360, (30, 30, 200), -1)
    elif kind == "blue":
        cv2.ellipse(slot, c, (int(w * 0.28), int(h * 0.26)), -25, 0, 360, (200, 60, 30), -1)
    elif kind == "orange":
        cv2.circle(slot, c, int(w * 0.26), (20, 140, 240), -1)
    elif kind == "scroll":
        cv2.rectangle(slot, (int(w * 0.35), int(h * 0.15)), (int(w * 0.92), int(h * 0.72)), (120, 170, 190), -1)
    cv2.circle(slot, c, int(w * 0.28), (40, 40, 40), 1)


def draw_count(slot, count):
    """The count at the slot's bottom left, in the game's glyphs (grey, as at 1080 p)."""
    x = potions.DIGIT_X0
    for ch in str(count):
        t = DIGITS[ch].template
        slot[potions.BAND[0]:potions.BAND[1], x:x + t.shape[1]] = np.repeat(np.clip(t, 0, 255).astype(np.uint8)[..., None], 3, 2)
        x += t.shape[1]


def make_bar(layout, height, offset=(0, 0), rows=3, background=None):
    """A picture of the window's lower right at `height` p with the bar's slots as in layout
    {(col, row): (icon, count)}. Returns (picture, estimated top left of the bar in it, UI scale)."""
    s0 = 1080 / 2880
    bw, bh = int(potions.BAR_W * s0) + 16, int(potions.BAR_H * s0) + 16
    img = np.full((bh, bw, 3), (70, 62, 58), np.uint8) if background is None else background.copy()
    for row in range(3 - rows, 3):
        for col in range(4):
            x, y, w, h = potions.slot_rect(col, row, s0, 8 + offset[0], 8 + offset[1])
            slot = np.full((h, w, 3), (250, 249, 248), np.uint8)
            if (col, row) in layout:
                icon, count = layout[(col, row)]
                if icon == "skill":
                    slot[:] = (150, 130, 190)
                else:
                    draw_icon(slot, icon)
                    if count is not None:
                        draw_count(slot, count)
            img[y:y + h, x:x + w] = slot
    k = height / 1080
    if k != 1:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    return img, (8 * k, 8 * k), height / 2880


def test_table():
    """A table of the made-up icons (what assets/potion_table.json is for the real ones)."""
    items = []
    for name, icon, kind in (("Red Potion", "red", "consumable"), ("Blue Potion", "blue", "consumable"),
                             ("Orange", "orange", "consumable"), ("Return Scroll", "scroll", "other_ignore")):
        slot = np.full((44, 46, 3), (250, 249, 248), np.uint8)
        draw_icon(slot, icon)
        items.append({"name": name, "kind": kind,
                      "fingerprint": [float(v) for v in potions.fingerprint(cv2.cvtColor(slot, cv2.COLOR_BGR2RGB), True)]})
    return potions.PotionTable(items)


TABLE = test_table()
LAYOUT = {(0, 0): ("orange", 5), (1, 0): ("red", 18), (2, 0): ("blue", 100), (3, 0): ("red", 0),
          (0, 1): ("skill", None), (1, 1): ("blue", 37), (2, 1): ("scroll", 500), (3, 1): ("orange", 321),
          (0, 2): ("red", 17), (1, 2): ("blue", 85), (2, 2): ("orange", 9), (3, 2): ("red", 21)}
WANT = {(0, 0): ("Orange", 5), (1, 0): ("Red Potion", 18), (2, 0): ("Blue Potion", 100), (3, 0): ("Red Potion", 0),
        (1, 1): ("Blue Potion", 37), (3, 1): ("Orange", 321), (0, 2): ("Red Potion", 17), (1, 2): ("Blue Potion", 85),
        (2, 2): ("Orange", 9), (3, 2): ("Red Potion", 21)}
for height in (1080, 1200, 1440):
    for offset in ((0, 0), (2, -2), (-2, 1)):
        img, origin, scale = make_bar(LAYOUT, height, offset)
        slots, _ = potions.read_bar(img, scale, origin, TABLE, DIGITS)
        got = {(sl.col, sl.row): (sl.name, sl.count) for sl in slots}
        check(f"the potion bar is read right at {height} p, bar {offset} px off: potions and counts, skills and "
              f"other items left out ({len(got)} slots)", got == WANT)

img, origin, scale = make_bar({(1, 1): ("red", 12), (2, 1): ("blue", 3), (1, 2): ("orange", 40)}, 1080, rows=2)
slots, _ = potions.read_bar(img, scale, origin, TABLE, DIGITS)
check("a 2-row bar is read: only its two rows", {(s.col, s.row, s.name, s.count) for s in slots} ==
      {(1, 1, "Red Potion", 12), (2, 1, "Blue Potion", 3), (1, 2, "Orange", 40)})
slots, _ = potions.read_bar(*(lambda a: (a[0], a[2], a[1]))(make_bar({}, 1080)), TABLE, DIGITS)
check("a bar with nothing on it reads nothing", slots == [])
noise = np.random.default_rng(3).integers(0, 255, (199, 241, 3), dtype=np.uint8)
slots, _ = potions.read_bar(noise, 1080 / 2880, (8, 8), TABLE, DIGITS)
check("a screen with no bar reads nothing", slots == [])
img, origin, scale = make_bar({(0, 0): ("red", None), (1, 0): ("blue", None)}, 1080)
slots, _ = potions.read_bar(img, scale, origin, TABLE, DIGITS)
check("a known icon with no number is kept with no count (never a made-up one)",
      {(s.col, s.row, s.name, s.count) for s in slots} == {(0, 0, "Red Potion", None), (1, 0, "Blue Potion", None)})
wrong = []
for count in (0, 1, 2, 5, 7, 8, 9, 10, 11, 16, 20, 33, 44, 59, 60, 71, 88, 99, 100, 101, 123, 200, 321, 450, 666, 987):
    img, origin, scale = make_bar({(1, 0): ("blue", count)}, 1080)
    slots, _ = potions.read_bar(img, scale, origin, TABLE, DIGITS)
    if [(sl.name, sl.count) for sl in slots] != [("Blue Potion", count)]:
        wrong.append(count)
check(f"counts from 0 to 987 are read back right ({wrong or 'all'})", not wrong)


def draw_count_on_dark(slot, count):
    """The count over a dark patch (the bottom of a bottle's outline): the glyphs' steady pixels as the
    game draws them, the rest dark, as the background behind a digit varies from item to item."""
    x = potions.DIGIT_X0
    for ch in str(count):
        t, mask = DIGITS[ch]
        patch = np.where(mask > 0, t, 55).astype(np.uint8)
        slot[potions.BAND[0]:potions.BAND[1], x:x + t.shape[1]] = np.repeat(patch[..., None], 3, 2)
        x += t.shape[1]


wrong = []
for count in (21, 33, 49, 51, 18, 40, 98, 7):
    bar, origin, scale = make_bar({(3, 0): ("red", None)}, 1080)
    x, y, w, h = potions.slot_rect(3, 0, 1080 / 2880, 8, 8)
    draw_count_on_dark(bar[y:y + h, x:x + w], count)
    slots, _ = potions.read_bar(bar, scale, origin, TABLE, DIGITS)
    if [(s.name, s.count) for s in slots] != [("Red Potion", count)]:
        wrong.append((count, [s.count for s in slots]))
check(f"a count over a dark background (the red potion's bottle) is read right ({wrong or 'all'})", not wrong)
img, origin, scale = make_bar({(1, 0): ("blue", 37)}, 1080)
blur = cv2.GaussianBlur(img, (0, 0), 4)
slots, _ = potions.read_bar(blur, scale, origin, TABLE, DIGITS)
check("a smeared bar gives no wrong number", all(sl.count in (None, 37) for sl in slots))

# ---- the reader looks at the potion bar every few seconds, and says so when this window is in the way
scene = np.zeros((1080, 1920, 3), np.uint8)
bar_img, origin, scale = make_bar(LAYOUT, 1080)
ex, ey, _ = potions.bar_estimate(1920, 1080)
bx, by = int(round(ex - origin[0])), int(round(ey - origin[1]))
scene[by:by + bar_img.shape[0], bx:bx + bar_img.shape[1]] = bar_img
reader = m.ScreenReader(m.DigitTemplates.load())
reader.potion_table, reader.potion_digits = TABLE, DIGITS
reader.gh = 11
reader.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz")
blank = m.Reading(*([None] * len(m.Reading._fields)))
clock = [1000.0]
real_monotonic = m.time.monotonic
m.time.monotonic = lambda: clock[0]
try:
    r = reader._read_extras(FakeScreen(scene), (0, 0, 1920, 1080), blank)
    got = {(sl.col, sl.row): (sl.name, sl.count) for sl in (r.bar or ())}
    check(f"the reader hands over what the potion bar shows ({len(got)} slots)", got == WANT)
    r = reader._read_extras(FakeScreen(scene), (0, 0, 1920, 1080), blank)
    check("...and not again before the next few seconds are up", r.bar is None)
    clock[0] += m.POTION_SECONDS + 0.1
    r = reader._read_extras(FakeScreen(scene), (0, 0, 1920, 1080), blank)
    check("...but then again, from where the bar was found (a small search)", {(sl.col, sl.row) for sl in r.bar} == set(WANT))
    clock[0] += m.POTION_SECONDS + 0.1
    reader.own_rect = (bx, by, 200, 150)                     # this window on top of the bar
    r = reader._read_extras(FakeScreen(scene), (0, 0, 1920, 1080), blank)
    check("this window covering the potion bar is said, and nothing is read", r.bar == () and "covering the potion bar" in (r.note or ""))
finally:
    m.time.monotonic = real_monotonic

# ---- the costly outline search for a see-through mesos box doesn't run on every look
name, info = sorted(PARTS["inventory"].items())[1]
inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
h, w = inv.shape[:2]
empty = np.full((int(h * 1.3), int(w * 1.3), 3), 25, np.uint8)
see_through = empty.copy()
see_through[:h, :w] = (0.45 * inv + 0.55 * empty[:h, :w]).astype(np.uint8)
reader = m.ScreenReader(m.DigitTemplates.load())
reader.gh = gh
reader.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz")
blank = m.Reading(*([None] * len(m.Reading._fields)))
searches, clock = [], [1000.0]
real_find, real_monotonic = reader.box_finder.find, m.time.monotonic
reader.box_finder.find = lambda *a: searches.append(clock[0]) or real_find(*a)
m.time.monotonic = lambda: clock[0]
try:
    def look(image):
        reader.next_scan = 0.0
        return reader._read_extras(FakeScreen(image), (0, 0, image.shape[1], image.shape[0]), blank)
    for _ in range(12):                                         # 24 s of nothing to find
        look(empty)
        clock[0] += 2
    check(f"with no inventory open, the outline search runs about every {m.OUTLINE_SECONDS} s, not every look ({len(searches)} in 24 s)",
          2 <= len(searches) <= 24 // m.OUTLINE_SECONDS + 1)
    reader.next_outline = clock[0] + m.OUTLINE_SECONDS           # (just after a search that found nothing)
    found_after = None
    for i in range(1, 16):                                      # the inventory is opened
        look(see_through)
        clock[0] += 2
        if reader.mesos_box and found_after is None:
            found_after = 2 * i
    check(f"a see-through inventory is still found, within {m.OUTLINE_SECONDS + 4} s of opening it (took {found_after} s)",
          found_after is not None and found_after <= m.OUTLINE_SECONDS + 4)
finally:
    m.time.monotonic = real_monotonic

# ---- party: the right number of HP bars, from 1 to 6 members, on every background
for key, info in sorted(PARTS["party"].items(), key=lambda kv: kv[1]["gh"]):
    gh, pitch = info["gh"], info["pitch"]
    rows = [cv2.imread(str(FRAMES / r), cv2.IMREAD_UNCHANGED) for r in info["rows"]]
    rh, rw = rows[0].shape[:2]
    wrong, total = [], 0
    for members in range(1, 7):
        h = pitch * (members - 1) + rh
        for bg_name, bg in backgrounds(h + int(6 * gh), rw + int(8 * gh)).items():
            img = bg.copy()
            ox, oy = rnd.randint(0, img.shape[1] - rw), rnd.randint(0, img.shape[0] - h)
            for i in range(members):
                row = rows[i % len(rows)]
                region = img[oy + i * pitch:oy + i * pitch + rh, ox:ox + rw]
                a = row[..., 3:] / 255.0
                region[:] = (row[..., :3] * a + region * (1 - a)).astype(np.uint8)
            seen = max(1, len(m.find_party_bars(img, gh)))
            total += 1
            if seen != members:
                wrong.append(f"{members} as {seen} on {bg_name}")
    check(f"party size right, EXP digits {gh:.0f} px: {total - len(wrong)}/{total} "
          f"({', '.join(wrong[:5]) or 'all right'})", not wrong)
    clean = all(len(m.find_party_bars(bg, gh)) <= 1 for bg in backgrounds(900, 1400).values())
    check(f"no party list on screen means solo, EXP digits {gh:.0f} px", clean)

# ---- the class, from the status bar (the character name line is blanked in these)
_tess = m.find_tesseract()
m.TESS = m.ocr.Tesseract(_tess) if _tess else None
m.tesseract_ok = bool(_tess)
if m.tesseract_ok:
    for name, info in sorted(PARTS["status"].items()):
        got = m.read_job(cv2.imread(str(FRAMES / name)), info["x0"], info["cy"], info["gh"])
        check(f"reads the class at EXP digits {info['gh']:.0f} px: {got}", got == info["job"])
        look = {}
        m.read_job(cv2.imread(str(FRAMES / name)), info["x0"], info["cy"], info["gh"], look)
        check(f"...and shows what it looked at ({name}): a picture, boxes, no reason to give up",
              look["image"].size > 0 and len(look["boxes"]) == 2 and look["why"] == "")
    blank = np.full((300, 900, 3), 20, np.uint8)
    look = {}
    check("a picture with no status bar gives no class, and says why",
          m.read_job(blank, 600, 150, 11, look) is None and "HP bar" in look["why"])
    look = {}
    check("off the edge of the screen says so", m.read_job(blank, 10, 150, 11, look) is None and look["why"])
else:
    print("     (Tesseract isn't installed: class reading not tested)")

# ---- an EXP bar that shows only a percentage ("EXP. [58.47%]" past about a million EXP)
from pics import percent_only  # noqa: E402
for f in sorted(FRAMES.glob("exp_*.png")):
    bar = cv2.imread(str(f))
    size = f.stem[4:]
    reader = m.ScreenReader(m.DigitTemplates.load())
    got, lay = reader._read((percent_only(bar), 0, 0), "game")
    check(f"a percentage-only bar is read as one, {size}",
          got.percent_only and got.confident and got.candidates == [(None, 71.68)] and "71.68" in got.text)
    check(f"...and still shows the bar and its digits in View capture, {size}", got.image is not None and len(got.boxes) == 4)
    got, lay = reader._read((bar, 0, 0), "game")
    check(f"a bar with its number is not percentage-only, {size}", not got.percent_only and got.candidates == [(68601, 71.68)])

# ---- the level badge ("LV." and the level in an orange box), read only when the bar shows no EXP number
from maple_overlay.vision import level_ocr  # noqa: E402
from pics import badge_scene  # noqa: E402


class Says:
    """Stands in for Tesseract: answers `texts` for the pictures (as many as it was given), and counts runs."""
    def __init__(self, *texts):
        self.texts, self.runs = texts, 0

    def read_lines(self, images, chars=None):
        self.runs += 1
        return [self.texts[i % len(self.texts)] for i in range(len(images))]


for gh in (7, 11, 14, 22, 28):
    wrong = []
    for level in (7, 32, 58, 99, 100):
        pic, x0, cy = badge_scene(level, gh)
        look, tess = {}, Says(str(level))
        got = level_ocr.read_level(pic, x0, cy, gh, tess, look)
        if got != level or tess.runs != 1 or look["image"] is None:
            wrong.append(f"Lv {level}: {got}")
    check(f"a complete badge is found and read in one Tesseract run, digits {gh} px ({', '.join(wrong) or 'all'})", not wrong)
pic, x0, cy = badge_scene(32, 11)
for name, scene in (("no orange box", badge_scene(32, 11, box=False)), ("no LV label", badge_scene(32, 11, label=False))):
    look, tess = {}, Says("32")
    check(f"{name}: no level, and Tesseract isn't even started",
          level_ocr.read_level(scene[0], scene[1], scene[2], 11, tess, look) is None and tess.runs == 0 and look["why"])
class Once:
    """Answers the first six pictures with `texts` and every later picture with nothing (so only the first look votes)."""
    def __init__(self, texts):
        self.texts, self.calls = list(texts), 0

    def read_lines(self, images, chars=None):
        self.calls += 1
        return (self.texts if self.calls == 1 else [""] * len(images))


vote = lambda *t: level_ocr.read_level(pic, x0, cy, 11, Once(t), {})
check("6 of 6 sizes agreeing is a level", vote("32", "32", "32", "32", "32", "32") == 32)
check("4 of 6 agreeing, nothing against, is a level", vote("32", "32", "32", "32", "", "") == 32)
check("3 of 6 agreeing is not enough", vote("32", "32", "32", "", "", "") is None)
check("a rival level with 2 votes means no level", vote("32", "32", "32", "32", "52", "52") is None)
check("a stray digit string isn't a level (0, 3-digit zero, letters)", vote("0", "07", "x", "", "32", "32") is None)
check("a lone rival vote doesn't spoil 5 of 6", vote("32", "32", "32", "32", "32", "52") == 32)
check("cut-off readings of the same level ('3' for '37', seen on a real badge) don't count against it",
      vote("37", "37", "37", "37", "3", "3") == 37 and vote("32", "32", "32", "32", "2", "3") == 32)
check("...but a short answer with the longer one as its rival is no level", vote("3", "3", "3", "3", "37", "37") is None)
# when the first six readings don't settle it (blurry frames), a wide set of readings votes, under a stricter rule
class Staged:
    """Stands in for Tesseract: `first` answers the first call (repeated over its pictures), `second` the later
    ones exactly as listed (and nothing for the rest of the pictures)."""
    def __init__(self, first, second):
        self.answers, self.calls, self.sizes = [first, second], 0, []

    def read_lines(self, images, chars=None):
        texts = self.answers[min(self.calls, 1)]
        self.sizes.append(len(images))
        self.calls += 1
        if self.calls == 1:
            return [texts[i % len(texts)] for i in range(len(images))]
        return (list(texts) + [""] * len(images))[:len(images)]


def staged(first, second, level=32):
    pic, x0, cy = badge_scene(level, 11)
    tess = Staged(first, second)
    look = {}
    return level_ocr.read_level(pic, x0, cy, 11, tess, look), tess, look


got, tess, look = staged([""], [""] * 3 + ["36"] * 9)
check(f"a blurry badge that the first six readings can't settle goes on to the wide set (calls {tess.sizes})",
      got is None or got == 36)
got, tess, look = staged([""], ["36"] * 9 + [""] * 3)
check(f"...which settles it when one level has at least 4 votes and twice any other ({got}, pictures {tess.sizes})",
      got == 36 and tess.calls == 2 and tess.sizes[0] == 6 and tess.sizes[1] > 40)
got, tess, look = staged(["36", "36", "36", "36", "36", "36"], [""])
check("...and doesn't go on when the first six are clear (one Tesseract run)", got == 36 and tess.calls == 1)
check("...a rival with half the votes still leaves it (8 against 4)", staged([""], ["58"] * 8 + ["56"] * 4, level=58)[0] == 58)
check("...but a rival with more than half the votes means no level (8 against 5)", staged([""], ["58"] * 8 + ["56"] * 5, level=58)[0] is None)
check("...and fewer than 4 votes isn't enough however alone (3 against none)", staged([""], ["58"] * 3 + [""] * 8, level=58)[0] is None
      and staged([""], ["58"] * 4 + [""] * 8, level=58)[0] == 58)
check("...answers of the wrong length neither help nor count against it in the wide set",
      staged([""], ["5"] * 12 + ["58"] * 3, level=58)[0] is None and staged([""], ["5"] * 12 + ["58"] * 4, level=58)[0] == 58)

# an orange square near some other white marks isn't the badge: its "LV." has to be as wide as the game's
short, good = badge_scene(17, 14, label_width=1.3), badge_scene(17, 14)
check("a badge whose LV mark is too short is refused (other orange icons have short marks near them)",
      level_ocr.read_level(short[0], short[1], short[2], 14, Says("17"), {}) is None
      and level_ocr.read_level(good[0], good[1], good[2], 14, Says("17"), {}) == 17)

# the badge has one orange square per digit: an answer with fewer digits lost one (a real frame of Lv 62 read "2")
def reads(level, *texts):
    pic, x0, cy = badge_scene(level, 11)
    return level_ocr.read_level(pic, x0, cy, 11, type("S", (), {"read_lines": lambda self, images, chars=None: list(texts)})(), {})


check("a two-square badge read as one digit by most sizes is no level (62 seen as '2')",
      reads(62, "2", "2", "2", "2", "62", "") is None and reads(62, "2", "2", "2", "2", "2", "2") is None)
check("...the right two digits still are", reads(62, "62", "62", "62", "62", "2", "2") == 62)
check("a one-square badge read as two digits is no level", reads(7, "71", "71", "71", "71", "71", "71") is None
      and reads(7, "7", "7", "7", "7", "", "") == 7)
check("a three-square badge needs three digits", reads(100, "10", "10", "10", "10", "10", "10") is None
      and reads(100, "100", "100", "100", "100", "", "") == 100)
check("no Tesseract (None) gives no level", level_ocr.read_level(pic, x0, cy, 11, None, {}) is None)
# ---- telling characters apart: a small picture of the name (never read as text), compared, kept in memory
def print_of(name, gh=11, **kw):
    pic, x0, cy = badge_scene(32, gh, name=name, **kw)
    return level_ocr.name_print(pic, x0, cy, gh)

# "1" is a narrow digit in this font and no other digit is: blur can't turn a 1 into a 4 (or back) unseen
def narrows(level):
    pic, x0, cy = badge_scene(level, 14)
    row, box = level_ocr.find_badge(pic, x0, cy, 14)
    return level_ocr.narrow_squares(row, box, len(str(level)))


check("the squares holding a 1 are told from the others by how narrow the digit is",
      narrows(51) == [False, True] and narrows(17) == [True, False] and narrows(62) == [False, False]
      and narrows(11) == [True, True] and narrows(7) == [False] and narrows(100) == [True, False, False])
check("a reading that puts a 1 where the digit is wide, or a wide digit where there is a 1, doesn't count",
      reads(54, "51", "51", "51", "51", "51", "51") is None and reads(51, "54", "54", "54", "54", "54", "54") is None
      and reads(54, "54", "54", "54", "54", "", "") == 54 and reads(51, "51", "51", "51", "51", "", "") == 51)
got, tess, look = staged([""], ["51"] * 11 + ["54"] * 9, level=51)
check(f"...so in a tie-ish vote the one that fits the squares wins (51 against 54: {got})", got == 51)
got, tess, look = staged([""], ["51"] * 11 + ["54"] * 9, level=54)
check(f"...and the other way round ({got})", got == 54)


for gh in (7, 11, 14, 22):
    names = ("Fox", "Pip", "Pip2", "Pip3", "moss", "Moss", "Moss2", "Ziggy", "XxSlayerxX")
    wrong = [f"{a}/{b}" for a in names for b in names
             if level_ocr.same_name(print_of(a, gh), print_of(b, gh)) != (a == b)]
    check(f"the same name is the same and any other (even one letter off) isn't, digits {gh} px ({', '.join(wrong[:4]) or 'all right'})",
          not wrong)
    shifted = [n for n in names if not level_ocr.same_name(print_of(n, gh), print_of(n, gh, shift=1))]
    check(f"...also when the text sits a pixel over, digits {gh} px ({', '.join(shifted) or 'all right'})", not shifted)
check("a name row with no text (covered, empty) gives no print, and no print is never 'the same'",
      print_of(None) is None and not level_ocr.same_name(None, print_of("Fox")) and not level_ocr.same_name(None, None))
check("a print is a small grid of numbers, not the picture or the text",
      print_of("Fox").shape == level_ocr.PRINT_SIZE[::-1] and print_of("Fox").dtype.kind == "f")

# ... and the reader asks for it now and then, never reads it under its own window, and backs off when it fails
def level_reader(level, gh, says):
    pic, x0, cy = badge_scene(level, gh)
    r = m.ScreenReader(m.DigitTemplates.load())
    r.gh, r.exp_anchor, r.own_rect = gh, (x0, cy), None
    m.TESS, m.tesseract_ok = says, True
    return r, FakeScreen(pic), (0, 0, pic.shape[1], pic.shape[0])


real_monotonic, real_tess, real_ok = m.time.monotonic, m.TESS, m.tesseract_ok
clock = [100.0]
m.time.monotonic = lambda: clock[0]
try:
    says = Says("32")
    r, screen, rect = level_reader(32, 11, says)
    r._read_level(screen, rect)
    check("the first badge reading is only a candidate", r.level is None and r.level_failures == 0)
    clock[0] += 1.6
    r._read_level(screen, rect)
    check("a second capture that agrees makes it the level", r.level == 32 and r.level_failures == 0 and says.runs == 2)
    clock[0] += 10
    r._read_level(screen, rect)
    check("once known it isn't read again for a while (Tesseract isn't started)", says.runs == 2)
    r.recheck_level()
    r._read_level(screen, rect)
    check("...unless asked to look again now (the % wrapped: a level-up may have happened)", says.runs == 3)
    check("a new level has to be seen twice again, and the old one stays meanwhile", r.level == 32)

    says = Says("")
    r, screen, rect = level_reader(32, 11, says)
    waits = []
    for _ in range(8):
        r._read_level(screen, rect)
        waits.append(round(r.next_level_read - clock[0]))
        clock[0] = r.next_level_read + 0.01
    check(f"a failing badge read backs off: 2 s, then 5 s, then 30 s (waits {waits})",
          waits[:2] == [2, 2] and waits[2:5] == [5, 5, 5] and waits[5:] == [30, 30, 30] and r.level_failures == 8
          and r.level is None and r.level_look["why"])

    says = Says("32")
    r, screen, rect = level_reader(32, 11, says)
    r.own_rect = (r.exp_anchor[0] - 60 * 11, 0, 10 * 11, screen.image.shape[0])      # this window sits on the badge
    r._read_level(screen, rect)
    check("this window covering the badge: nothing is read, and it says so",
          says.runs == 0 and r.level is None and "covering" in r.level_look["why"])

    # the character's name: a print is taken every few seconds, even without Tesseract, and kept only in memory
    def named(name):
        pic, x0, cy = badge_scene(32, 11, name=name)
        r = m.ScreenReader(m.DigitTemplates.load())
        r.gh, r.exp_anchor, r.own_rect = 11, (x0, cy), None
        return r, FakeScreen(pic), (0, 0, pic.shape[1], pic.shape[0])
    r, screen, rect = named("Fox")
    r._look_at_name(screen, rect)
    check("the reader takes a print of the name row", level_ocr.same_name(r.name_print, print_of("Fox")))
    other = FakeScreen(badge_scene(32, 11, name="Fin")[0])
    r._look_at_name(other, rect)
    check("...but not again within a few seconds", level_ocr.same_name(r.name_print, print_of("Fox")))
    clock[0] += 6
    r._look_at_name(other, rect)
    check("...and a different name later gives a different print", not level_ocr.same_name(r.name_print, print_of("Fox")))
    r.own_rect = (r.exp_anchor[0] - 52 * 11, 0, 20 * 11, screen.image.shape[0])
    clock[0] += 6
    r._look_at_name(screen, rect)
    check("this window sitting on the name: no print (it never reads itself)", r.name_print is None)

    says = Says("32")
    r, screen, rect = level_reader(32, 11, says)
    m.tesseract_ok = False
    r._read_level(screen, rect)
    check("without Tesseract there is nothing to wait for: the typed-level fallback is open at once",
          says.runs == 0 and r.level is None and r.level_failures >= m.LEVEL_FAILS_FOR_FALLBACK)
finally:
    m.time.monotonic, m.TESS, m.tesseract_ok = real_monotonic, real_tess, real_ok

# the bar is found once, then only its spot is looked at: that spot has to keep the "EXP." label in it
for name, size in (("exp_win-1024x768", (1024, 768)), ("exp_win-1280x720", (1280, 720)), ("exp_win-1920x1080", (1920, 1080)),
                   ("exp_win-1920x1200", (1920, 1200))):
    bar = percent_only(cv2.imread(str(FRAMES / (name + ".png"))))
    scene = np.full((size[1], size[0], 3), 25, np.uint8)
    scene[size[1] - bar.shape[0] - 6:size[1] - 6, 40:40 + bar.shape[1]] = bar
    r = m.ScreenReader(m.DigitTemplates.load())
    rect = (0, 0, size[0], size[1])
    looks = [r._read_window(FakeScreen(scene), rect) for _ in range(6)]
    check(f"a %-only bar is still read on every look after it was first found, {name[4:]} "
          f"({sum(x.percent_only and x.confident for x in looks)}/6)",
          all(x.percent_only and x.confident and x.candidates == [(None, 71.68)] for x in looks) and r.misses == 0)

# something unexpected inside the badge read can't cost the whole look, and backs off like any failed read
class Breaks:
    def read_lines(self, images, chars=None):
        raise ValueError("something nobody expected")


clock2 = [500.0]
real_mono2 = m.time.monotonic
real_tess2, real_ok2 = m.TESS, m.tesseract_ok
m.time.monotonic = lambda: clock2[0]
try:
    r, screen2, rect2 = level_reader(32, 11, Breaks())
    try:
        r._read_level(screen2, rect2)
        raised = False
    except Exception:
        raised = True
    check("an unexpected error in the badge read is a failed read: nothing raised, backed off, failure counted",
          not raised and r.level_failures == 1 and r.next_level_read > clock2[0])
finally:
    m.time.monotonic, m.TESS, m.tesseract_ok = real_mono2, real_tess2, real_ok2

# Select area has no game window, so no badge to find: typing the level in is open at once, and it says why
from pics import percent_only  # noqa: E402,F811
bar = percent_only(cv2.imread(str(FRAMES / "exp_win-1280x720.png")))
r = m.ScreenReader(m.DigitTemplates.load())
r.use_region({"left": 0, "top": 0, "width": bar.shape[1], "height": bar.shape[0]})
r.level = 37                                      # (read earlier in window mode: a selected area has no badge of its own)
got = r.read(FakeScreen(bar))
check("a %-only bar in a selected area is read, with no level, and typing one in is open at once",
      got.percent_only and got.level is None and r.level_failures >= m.LEVEL_FAILS_FOR_FALLBACK
      and "Selected area" in r.level_look["why"])

if m.tesseract_ok:
    game = cv2.imread(str(Path(__file__).resolve().parent.parent / "docs" / "screenshot-game.jpg"))
    check("the real badge (Lv 32, a JPEG of the game at 1080p) reads as 32 with real Tesseract",
          level_ocr.read_level(game, 1052, 1044, 11.0, m.TESS, {}) == 32)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
