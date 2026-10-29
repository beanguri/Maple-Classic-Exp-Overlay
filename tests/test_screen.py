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
import maple_exp as m  # noqa: E402
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
import potions  # noqa: E402

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

# ---- Classic World party list crop (red HP pills; smaller vs EXP digit height)
_classic = cv2.imread(str(FRAMES / "party_classic_4.png"))
if _classic is not None:
    _canvas = cv2.copyMakeBorder(_classic, 80, 80, 80, 80, cv2.BORDER_CONSTANT, value=(40, 40, 40))
    for _gh in (7.0, 11.0, 14.0):
        _n = len(m.find_party_bars(_canvas, _gh))
        check(f"Classic World party crop counts 4 members at EXP digits {_gh:.0f} px ({_n})", _n == 4)

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
import level_ocr  # noqa: E402
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
# ---- A13-7: a name row that is partly covered is judged by what shows
def covered(pr, left, right):
    pr = pr.copy()
    pr[:, int(left * pr.shape[1]):int(right * pr.shape[1])] = 0
    return pr


for gh in (11, 14):
    bean, soy = print_of("beanguri", gh), print_of("soymiso", gh)
    check(f"A13-7: the same name is 'same', nothing is 'hidden', digits {gh} px",
          level_ocr.name_match(bean, print_of("beanguri", gh)) == "same" and level_ocr.name_match(bean, None) == "hidden")
    got = [level_ocr.name_match(bean, covered(print_of("beanguri", gh), *c)) for c in ((0.5, 1.0), (0.0, 0.5), (0.3, 0.7))]
    check(f"...the same name with part of it covered is never 'different' (alike, or hidden when little shows) ({got})",
          "different" not in got and got.count("alike") >= 2)
    got = [level_ocr.name_match(bean, covered(print_of("beanguri", gh), *c)) for c in ((0.0, 0.8), (0.0, 0.95))]
    check(f"...and with most of it covered (too little shows to tell) it is 'hidden', not 'different' ({got})",
          got == ["hidden"] * 2)
    got = [level_ocr.name_match(bean, covered(soy, *c)) for c in ((0.5, 1.0), (0.0, 0.5), (0.3, 0.7))]
    check(f"...another name with part of it covered is still 'different' ({got})", got == ["different"] * 3)
    check("...and so is another name in full", level_ocr.name_match(bean, soy) == "different")
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

# ---- one failing part never stops the others (issues 5 and 9: Tesseract broke the map name and the EXP went with it)
class Boom:
    """A Tesseract that fails whatever it is asked."""
    def __getattr__(self, name):
        raise RuntimeError("Tesseract can't load eng.traineddata")


def part_reader(templates=True):
    r = m.ScreenReader(m.DigitTemplates.load() if templates else None)
    r.hwnd, r.mode, r.own_rect = 1, "window", (-5000, -5000, 10, 10)
    return r

# a capture that sees through windows: our window over the EXP bar changes nothing in the read, but the geometry still says "covering"
from capture import Capture, ScreenCapture  # noqa: E402


class SeeThrough(ScreenCapture):
    name = "window"
    sees_through_windows = True


frame = cv2.imread(str(FRAMES / "exp_win-1280x720.png"))
fh, fw = frame.shape[:2]
fscreen = FakeScreen(frame)
real_monotonic, real_tess, real_ok = m.time.monotonic, m.TESS, m.tesseract_ok
real_candidates, real_map = m.tesseract_candidates, m.read_map_name
clock = [1000.0]
m.time.monotonic = lambda: clock[0]
try:
    m.TESS, m.tesseract_ok = Boom(), True
    r = part_reader()
    r._game_rect = lambda: (0, 0, fw, fh)
    clear = r.read(fscreen)
    check("a clear EXP bar is read as usual while Tesseract is broken", clear.confident and clear.candidates)
    r = part_reader(templates=False)       # (no templates: the digits are never sure, so the Tesseract backup is asked)
    r._game_rect = lambda: (0, 0, fw, fh)
    got = r.read(fscreen)
    check("an unsure bar with a broken Tesseract still gives a look (no exception)", got.where and not got.confident)
    check("...and counts it as a Tesseract part failure",
          r.part_errors["Tesseract"] >= 1 and r.error_types["RuntimeError"] >= 1 and "Tesseract" in (got.note or ""))

    # the map name raising: the same look still has its EXP, a note, and one entry in errors.log
    m.TESS = real_tess
    m.read_map_name = lambda bgr, gh: (_ for _ in ()).throw(RuntimeError("no eng.traineddata"))
    r = part_reader()
    r._game_rect = lambda: (0, 0, fw, fh)
    r.read(fscreen)                                    # (the first look finds the bar)
    r.map_box, r.map_unread, r.next_map_read = (0, 0, 120, 30), True, 0.0
    log_before = m.ERROR_LOG_PATH.read_text() if m.ERROR_LOG_PATH.exists() else ""
    notes = []
    for _ in range(5):
        r.map_unread, r.next_map_read, r.map_print = True, 0.0, None
        clock[0] += 1.5
        got = r.read(fscreen)
        notes.append(got.note or "")
        assert got.confident and got.candidates
    log_after = m.ERROR_LOG_PATH.read_text() if m.ERROR_LOG_PATH.exists() else ""
    entries = log_after[len(log_before):].count("reading the map name failed")
    check("a failing map name keeps the EXP reading", got.confident and got.map is None)
    check("...says so in the status note", all("Map names can't be read right now" in n for n in notes))
    check(f"...and errors.log hears of it once, not at every look ({entries} entry for 5 looks)", entries == 1)
    check("...with every failure counted", r.part_errors["map name"] == 5 and r.diagnostics()["part_errors"]["map name"] == 5
          and r.diagnostics()["error_types"]["RuntimeError"] == 5)
    clock[0] += m.PART_LOG_SECONDS + 1
    r.map_unread, r.next_map_read, r.map_print = True, 0.0, None
    r.read(fscreen)
    tail = m.ERROR_LOG_PATH.read_text()[len(log_after):]
    check("...after 10 minutes it is written again, with the number skipped", "4 more failures skipped" in tail)
    m.read_map_name = lambda img, g: "Henesys"      # (works again; the real one needs Tesseract, which CI may not have)
    clock[0] += 1.5
    r.map_unread, r.next_map_read, r.map_print = True, 0.0, None
    got = r.read(fscreen)
    clock[0] += 1.5
    got = r.read(fscreen)
    m.read_map_name = real_map
    check("a part that works again loses its note", "Map names" not in (got.note or ""))

    # the potion bar and the mesos reader raising
    r = part_reader()
    r._game_rect = lambda: (0, 0, fw, fh)
    r.read(fscreen)
    def boom(*a, **k):
        raise ValueError("boom")
    r._read_potion_bar, r._read_mesos = boom, boom
    r.mesos_box, r.next_bar, r.next_scan = (10, 10, 100, 12), 0.0, float("inf")
    clock[0] += 1.5
    got = r.read(fscreen)
    check("a failing potion bar or mesos reader keeps the EXP too",
          got.confident and got.mesos is None and got.bar is None
          and r.part_errors["potions"] == 1 and r.part_errors["mesos"] == 1
          and "potion bar can't be read" in got.note and "Mesos can't be read" in got.note)
    check("...and diagnostics() shows the counts", r.diagnostics()["part_errors"] == {"potions": 1, "mesos": 1})
finally:
    m.time.monotonic, m.TESS, m.tesseract_ok = real_monotonic, real_tess, real_ok
    m.tesseract_candidates, m.read_map_name = real_candidates, real_map

# ---- A01: every reading part, failing with every kind of error, never costs the EXP reading
class Custom(Exception):
    pass


def raising(exc):
    def boom(*a, **k):
        raise exc
    return boom





def matrix_reader(part):
    """A reader set up so that `part` runs at every look (and the bar is read cleanly), plus how to make it raise."""
    bar = percent_only(frame) if part == "level" else frame     # (the level is only read when the bar shows just a percentage)
    scene = np.full((720, 1280, 3), 25, np.uint8)               # (room around the bar for the class, name and level)
    scene[720 - bar.shape[0] - 6:720 - 6, 400:400 + bar.shape[1]] = bar
    screen, size = FakeScreen(scene), (1280, 720)
    r = part_reader(templates=part != "Tesseract")
    r._game_rect = lambda: (0, 0) + size
    r.read(screen)                                      # (the first look finds the bar)
    r.next_job_read = r.next_name_read = r.next_level_read = r.next_scan = 0.0
    if part == "map name":
        r.map_box, r.map_unread, r.next_map_read = (0, 0, 120, 30), True, 0.0
    return r, screen


def patch_part(part, r, exc):
    """Makes the part's own reader raise `exc`; returns what to undo."""
    boom = raising(exc)
    if part == "map name":
        return [(m, "read_map_name", boom)]
    if part == "class":
        return [(m, "read_job", boom)]
    if part == "name print":
        return [(level_ocr, "name_print", boom)]
    if part == "level":
        return [(level_ocr, "read_level", boom)]
    if part == "window scan":
        return [(m, "find_party_bars", boom)]
    if part == "Tesseract":
        return [(m, "tesseract_candidates", boom)]
    if part == "mesos":
        return [(r, "_read_mesos", boom)]
    return [(r, "_read_potion_bar", boom)]


def rearm(part, r):
    """Makes the part due again (its own timers would otherwise skip it)."""
    r.next_job_read = r.next_name_read = r.next_level_read = r.next_scan = r.next_bar = 0.0
    if part == "map name":
        r.map_unread, r.next_map_read, r.map_print = True, 0.0, None
    if part == "mesos":
        r.mesos_box = (10, 10, 100, 12)
        r.next_scan = float("inf")


def has_exp(part, got):
    if part == "Tesseract":                 # (no templates: the bar is unsure by design; the look must still come back)
        return bool(got.where)
    if part == "level":
        return got.percent_only and got.confident
    return got.confident and bool(got.candidates)

real_monotonic, real_tess, real_ok = m.time.monotonic, m.TESS, m.tesseract_ok
m.time.monotonic = lambda: clock[0]
m.TESS, m.tesseract_ok = Says("32"), True
bad = []
try:
    for part in m.PART_NOTES:
        for exc in (m.ocr.TesseractError("eng.traineddata is missing"), OSError("disk gone"), ValueError("odd"), Custom("mine")):
            r, screen = matrix_reader(part)
            undo = [(o, n, getattr(o, n)) for o, n, _ in patch_part(part, r, exc)]
            for o, n, f in patch_part(part, r, exc):
                setattr(o, n, f)
            try:
                log_before = m.ERROR_LOG_PATH.read_text() if m.ERROR_LOG_PATH.exists() else ""
                notes, kept = [], True
                for _ in range(4):
                    rearm(part, r)
                    clock[0] += 1.5
                    got = r.read(screen)
                    kept = kept and has_exp(part, got)
                    notes.append(got.note or "")
                entries = m.ERROR_LOG_PATH.read_text()[len(log_before):].count(f"reading the {part} failed")
                said = m.PART_NOTES[part] in notes[0]
                counted = r.part_errors[part] >= 4 and r.error_types[type(exc).__name__] >= 4
                if not (kept and said and counted and entries == 1):
                    bad.append(f"{part}/{type(exc).__name__}: exp={kept} note={said} counted={r.part_errors[part]} log={entries}")
                clock[0] += m.PART_LOG_SECONDS + 1
                rearm(part, r)
                log_mid = m.ERROR_LOG_PATH.read_text()
                r.read(screen)
                again = m.ERROR_LOG_PATH.read_text()[len(log_mid):].count(f"reading the {part} failed")
                if again != 1:
                    bad.append(f"{part}/{type(exc).__name__}: after 10 minutes the log got {again} entries")
            finally:
                for o, n, f in undo:
                    setattr(o, n, f)
finally:
    m.time.monotonic, m.TESS, m.tesseract_ok = real_monotonic, real_tess, real_ok
check(f"every reading part x TesseractError/OSError/ValueError/custom: the EXP still reads, the part is counted, noted, and "
      f"written to errors.log once per 10 minutes ({len(m.PART_NOTES) * 4} cases{': ' + '; '.join(bad[:6]) if bad else ''})", not bad)


# ---- a failed part's note stays for PART_NOTE_SECONDS, also on the looks where the part isn't due again yet
m.time.monotonic = lambda: clock[0]
m.TESS, m.tesseract_ok = Says("32"), True
try:
    for part, patched, word in (("map name", (m, "read_map_name"), "Map names"), ("name print", (level_ocr, "name_print"), "Telling characters")):
        r, screen = matrix_reader(part)
        real_fn = getattr(*patched)
        setattr(*patched, raising(OSError("gone")))
        seen = []
        try:
            for _ in range(4):                     # (the part fails at the first look; its next try is 5 s away)
                clock[0] += 1.5
                seen.append(word in (r.read(screen).note or ""))
        finally:
            setattr(*patched, real_fn)
        check(f"a failed {part} keeps its status note on the looks before it is due again ({seen})", all(seen))
finally:
    m.time.monotonic, m.TESS, m.tesseract_ok = real_monotonic, real_tess, real_ok


# ---- a class read that fails isn't tried again at every look (each try starts Tesseract), only after a pause
m.time.monotonic = lambda: clock[0]
m.TESS, m.tesseract_ok = Says("32"), True
try:
    r, screen = matrix_reader("class")
    tries = [0]

    def failing_job(*a, **k):
        tries[0] += 1
        raise OSError("gone")
    real_job = m.read_job
    m.read_job = failing_job
    try:
        r.next_job_read = 0.0
        for _ in range(6):                                  # 9 s of looks
            clock[0] += 1.5
            r.read(screen)
        in_nine = tries[0]
        clock[0] += 3
        r.read(screen)
    finally:
        m.read_job = real_job
    check(f"a failing class read is tried once, not at each of 6 looks ({in_nine}), and again after a pause ({tries[0]})",
          in_nine == 1 and tries[0] == 2)
finally:
    m.time.monotonic, m.TESS, m.tesseract_ok = real_monotonic, real_tess, real_ok

# ---- _covered is v0.9.12's geometry, unchanged; _blocked is the same answer for a screen copy and never true for one that sees through windows
def old_covered(own, x, y, w, h, margin=0):
    r = own
    return bool(r) and (x - margin < r[0] + r[2] and r[0] < x + w + margin
                        and y - margin < r[1] + r[3] and r[1] < y + h + margin)


rr = random.Random(7)
cover_bad = []
screen_reader, glass_reader = part_reader(), part_reader()
glass_reader.capture = SeeThrough(None)
for _ in range(4000):
    own = rr.choice([None, (rr.randint(-50, 400), rr.randint(-50, 300), rr.randint(0, 300), rr.randint(0, 300))])
    area = (rr.randint(-50, 500), rr.randint(-50, 400), rr.randint(0, 200), rr.randint(0, 200), rr.choice([0, 0, 2, 7]))
    screen_reader.own_rect = glass_reader.own_rect = own
    want = old_covered(own, *area[:4], margin=area[4])
    if (screen_reader._covered(*area[:4], margin=area[4]) != want or screen_reader._blocked(*area[:4], margin=area[4]) != want
            or glass_reader._covered(*area[:4], margin=area[4]) != want or glass_reader._blocked(*area[:4], margin=area[4])):
        cover_bad.append((own, area))
check(f"_covered is the old overlap test and _blocked is the same for a screen copy, never true through windows (4000 random areas, {len(cover_bad)} differ)",
      not cover_bad)

# ---- health reports count each look once, by what happened to the EXP bar (exp_kind), and note which other readers tried
def kind_of_look(templates=True, tesseract=None, setup=None):
    r = part_reader(templates)
    r._game_rect = lambda: (0, 0, fw, fh)
    r.own_rect = (-5000, -5000, 10, 10)
    if setup:
        setup(r)
    saved = m.tesseract_candidates
    if tesseract is not None:
        m.tesseract_candidates = lambda img, lay: tesseract
    try:
        return r.read(fscreen)
    finally:
        m.tesseract_candidates = saved


before_tess = (m.TESS, m.tesseract_ok)
m.TESS, m.tesseract_ok = None, False
check("health: a clear bar is 'read'", kind_of_look().exp_kind == "read")
check("health: unsure digits with no Tesseract answer are 'unclear'", kind_of_look(templates=False, tesseract=[]).exp_kind == "unclear")
check("health: unsure digits that only Tesseract can give are 'tesseract'",
      kind_of_look(templates=False, tesseract=[(68601, 71.68)]).exp_kind == "tesseract")
check("health: a bar with nothing on it is 'not_found'",
      m.ScreenReader(m.DigitTemplates.load())._read((np.zeros((60, 400, 3), np.uint8), 0, 0), "game")[0].exp_kind == "not_found"
      and m.ScreenReader(m.DigitTemplates.load())._read(None, "game")[0].exp_kind == "not_found")
r = m.ScreenReader(m.DigitTemplates.load())
check("health: a percentage-only bar is 'read' and says it is percentage-only",
      all((lambda g: g.exp_kind == "read" and g.percent_only)(r._read((percent_only(cv2.imread(str(f))), 0, 0), "game")[0])
          for f in sorted(FRAMES.glob("exp_*.png"))))
real_find_game = m.PLATFORM.find_game_window
m.PLATFORM.find_game_window = lambda: None          # (the game really running on this PC must not count here)
r = part_reader()
r.hwnd = None
check("health: no game window is 'no_window'", r.read(fscreen).exp_kind == "no_window")
m.PLATFORM.find_game_window = real_find_game
r = m.ScreenReader(m.DigitTemplates.load())
r.use_region(None)
check("health: no selected area is 'no_window'", r.read(fscreen).exp_kind == "no_window")
r = part_reader()
r._game_rect = lambda: (0, 0, fw, fh)
r.box, r.own_rect = (0, 0, fw, fh), (0, 0, fw, fh)
check("health: our own window over the bar is 'covered'", r.read(fscreen).exp_kind == "covered")
r = part_reader()
r._game_rect = lambda: (0, 0, fw, fh)
blank_screen = FakeScreen(np.zeros_like(frame))
check("health: a screen with no bar is 'not_found'", r.read(blank_screen).exp_kind == "not_found")
got = kind_of_look(setup=lambda r: (setattr(r, "_read_potion_bar", lambda *a, **k: (_ for _ in ()).throw(ValueError("x"))),
                                    setattr(r, "next_bar", 0.0)))
check("health: a reader that failed is noted as tried and not worked", ("potions", False) in got.tries)
got = kind_of_look(setup=lambda r: (setattr(r, "_read_potion_bar", lambda *a, **k: ()), setattr(r, "next_bar", 0.0)))
check("health: a potion bar look that saw nothing is noted as tried and not read", got.tries == (("potions", False),))
got = kind_of_look(setup=lambda r: (setattr(r, "_read_potion_bar", lambda *a, **k: ("slot",)), setattr(r, "next_bar", 0.0)))
check("health: a potion bar look that read something is noted as read", got.tries == (("potions", True),))

# A04 audit: every way out of a look sets exp_kind itself, and the looks add up in the health record
r = part_reader()
r._game_rect = lambda: None
minimized = r.read(fscreen)
check("health: a minimized game is 'no_window'", minimized.exp_kind == "no_window")
r = part_reader()
r.hwnd = None
real_permission = m.PLATFORM.missing_permission
m.PLATFORM.missing_permission = lambda: "Allow screen recording for this app."
m.PLATFORM.find_game_window = lambda: None
no_permission = r.read(fscreen)
m.PLATFORM.missing_permission, m.PLATFORM.find_game_window = real_permission, real_find_game
check("health: a missing screen permission is 'no_window'", no_permission.exp_kind == "no_window")
import healthlog  # noqa: E402
looks = [kind_of_look(), kind_of_look(templates=False, tesseract=[]), kind_of_look(templates=False, tesseract=[(68601, 71.68)]),
         minimized, no_permission]
r = part_reader()
r.hwnd = None
looks.append(r.read(fscreen))
r = m.ScreenReader(m.DigitTemplates.load())
r.use_region(None)
looks.append(r.read(fscreen))
r = part_reader()
r._game_rect = lambda: (0, 0, fw, fh)
r.box, r.own_rect = (0, 0, fw, fh), (0, 0, fw, fh)
looks.append(r.read(fscreen))
r = part_reader()
r._game_rect = lambda: (0, 0, fw, fh)
looks.append(r.read(FakeScreen(np.zeros_like(frame))))
r = m.ScreenReader(m.DigitTemplates.load())
for f in sorted(FRAMES.glob("exp_*.png")):
    looks.append(r._read((percent_only(cv2.imread(str(f))), 0, 0), "game")[0])
record = healthlog.HealthLog(Path(tempfile.mkdtemp()) / "health.json")
for look in looks:
    record.look(look.exp_kind, look.percent_only, look.tries)
day = record.today()
check("health: every look from every path names its own kind (none is guessed afterwards)",
      all(look.exp_kind in healthlog.EXP_KINDS for look in looks))
check("health: the kinds add up to the looks, and percentage-only looks are read or Tesseract looks",
      day["looks"] == len(looks) == sum(day["exp"][k] for k in healthlog.EXP_KINDS)
      and 0 < day["exp"]["percent_only"] <= day["exp"]["read"] + day["exp"]["tesseract"])

m.TESS, m.tesseract_ok = before_tess

rect_f = (0, 0, fw, fh)
for cap_class, wanted in ((ScreenCapture, False), (SeeThrough, True)):
    r = m.ScreenReader(m.DigitTemplates.load())
    cap = cap_class(FakeScreen(frame))
    first = r._read_window(cap, rect_f)
    check(f"{cap.name} capture: the bar is found with nothing in the way", first.confident)
    bx, by, bw, bh = r.box
    r.own_rect = (bx, by, bw, bh)                       # this window right on top of the bar
    r.last_rect, r.gh = rect_f, r.gh
    again = r._read_window(cap, rect_f)
    check(f"{cap.name} capture, our window over the EXP bar: " + ("still read, no covering message" if wanted else "covering message"),
          again.confident if wanted else "covering" in again.where)
    check(f"{cap.name} capture: diagnostics() names the capture and still lists the EXP bar as covered",
          r.diagnostics()["capture"] == cap.name and "EXP bar" in r.diagnostics()["covering"])
check("the bare fake screen is wrapped as a screen copy", isinstance(m.as_capture(FakeScreen(frame)), ScreenCapture)
      and isinstance(m.as_capture(SeeThrough(None)), Capture))

if m.tesseract_ok:
    game = cv2.imread(str(Path(__file__).resolve().parent.parent / "docs" / "screenshot-game.jpg"))
    check("the real badge (Lv 32, a JPEG of the game at 1080p) reads as 32 with real Tesseract",
          level_ocr.read_level(game, 1052, 1044, 11.0, m.TESS, {}) == 32)

# The game's own EXP-bar font and how it scales (exp_font.py, assets/exp_font.json)
import exp_font  # noqa: E402
font = exp_font.load()
check("the EXP font has every glyph the bar uses", all(c in font["glyphs"] for c in "0123456789,.%[]")
      and font["version"] == 1 and font["label"]["w"] > 0)
pic, brackets = exp_font.canvas("12,345 [67.89%]")
check("the sample text is 80 x 7 px at 1x, with its two brackets", pic.shape == (7, 80) and len(brackets) == 2 and pic.max() == 1)
big = exp_font.render("12,345 [67.89%]", 2.0)
check("...and 160 x 14 at 2x, smoothed (grey between black and white)", big.shape == (14, 160) and 0.1 < big.mean() < 0.5
      and ((big > 0.05) & (big < 0.95)).any())
check("the window scale is height / 768 (1 with UI Size Ideal)", exp_font.window_scale(1440) == 1.875
      and exp_font.window_scale(1440, ideal=True) == 1.0)
ox, oy = exp_font.text_origin(2560, 1440)
check("the first digit of the bar lands where it was measured at 1440p (about 1403.7, 1387.5)",
      abs(ox - 1403.65) < 0.3 and abs(oy - 1387.5) < 0.3)

# ---- finder v2: finds the bar by shape and brightness, not by colour
from pics import relabelled  # noqa: E402
TPL = m.DigitTemplates.load()


def v2_read(pic):
    lay = m.find_exp_v2(pic)
    return (lay, m.read_layout(TPL, lay)) if lay else (None, None)


def tinted(bgr, scale):
    return np.clip(bgr.astype(np.float32) * np.array(scale, np.float32), 0, 255).astype(np.uint8)


for f in sorted(FRAMES.glob("exp_*.png")):
    bar, size = cv2.imread(str(f)), f.stem[4:]
    old = m.find_exp(bar)
    lay, got = v2_read(bar)
    check(f"v2 finds the bar and reads 68601 [71.68%], {size}", lay is not None and got == (68601, 71.68))
    check(f"...in the same place and size as the colour finder, {size}",
          abs(lay.cy - old.cy) <= 1 and abs(lay.gh - old.gh) <= 1 and len(lay.exp) == len(old.exp) and len(lay.pct) == len(old.pct))
    grey = cv2.cvtColor(cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    check(f"a greyscale screen is still read, {size} (the colour finder gives up)",
          v2_read(grey)[1] == (68601, 71.68) and m.find_exp(grey) is None)
    check(f"a warm night-light tint is still read, {size}", v2_read(tinted(bar, (0.55, 0.85, 1.0)))[1] == (68601, 71.68))
    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)
    green = (hsv[..., 1] >= 90) & (hsv[..., 2] >= 110) & (hsv[..., 0] >= 26) & (hsv[..., 0] <= 50)
    hsv[..., 0] = (hsv[..., 0].astype(int) + 30) % 180                      # +60 degrees: the brackets are no longer green
    check(f"brackets of another colour are still found, {size}", v2_read(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))[1] == (68601, 71.68))
    pale = bar.copy()
    pale[green] = (225, 232, 225)                                           # a bracket as white as a digit
    check(f"a bracket as white as a digit is not read as a digit (the fake '1'), {size}", v2_read(pale)[1] == (68601, 71.68))
    lay_p, got_p = v2_read(percent_only(bar))
    check(f"a percentage-only bar gives a layout with no number, {size}", lay_p is not None and lay_p.exp == [] and len(lay_p.pct) >= 5)
    for text in ("HP", "MP"):
        check(f"a bar with a % but the label {text} instead of EXP. is not the EXP bar, {size}", m.find_exp_v2(relabelled(bar, text)) is None)
    x0, x1 = int(old.box[0] - 4 * old.gh), int(old.box[2] + 2 * old.gh)
    blank = bar.copy()
    blank[int(old.cy - 1.6 * old.gh):int(old.cy + 1.6 * old.gh), x0:x1] = np.median(bar[:, :2].reshape(-1, 3), axis=0)
    check(f"with the EXP block wiped out, HP and MP are not taken for it, {size}", m.find_exp_v2(blank) is None)
    covered = bar.copy()
    covered[int(old.cy - old.gh):int(old.cy + old.gh), int(old.box[0]):int(old.box[0] + 0.4 * (old.exp[-1][0] - old.box[0]))] = 60
    c_lay, c_got = v2_read(covered)
    c_old = m.find_exp(covered)
    check(f"a half-covered number reads no better than with the colour finder (the tracker's level check does the rest), {size}",
          c_got is None or c_got == (m.read_layout(TPL, c_old) if c_old else None))
    b = old.box
    hinted = m.find_exp_v2(bar, (b[0] - 30, old.cy - 3 * old.gh, b[2] + 30, old.cy + 3 * old.gh))
    check(f"the remembered spot finds the same bar, {size}", hinted is not None and m.read_layout(TPL, hinted) == (68601, 71.68))
    away = m.find_exp_v2(bar, (0, 0, 40, 40))
    check(f"a wrong remembered spot falls back to the whole picture, {size}", away is not None and m.read_layout(TPL, away) == (68601, 71.68))
check("a blank picture has no bar", m.find_exp_v2(np.full((200, 1000, 3), 40, np.uint8)) is None)
check("noise has no bar", m.find_exp_v2(np.random.default_rng(3).integers(0, 255, (200, 1000, 3), dtype=np.uint8)) is None)

# ---- digit reader v2: draws the game's own font at the screen's scale and compares pixels
import digits_v2  # noqa: E402


def fake_layout(exp, pct, scale, noise=0.0, blur=0.0, cover=None, seed=1):
    """A Layout of a bar made from the font alone (grey: the label "EXP.", the number, the brackets and the
    percentage, scaled the way the game scales them), as the finder would hand it over."""
    text = ("{:,}".format(exp) + " " if exp else "") + "[" + "{:.2f}".format(pct) + "%]"
    h, w = round(40 * scale) + 20, round(150 * scale) + 20
    tx, ty = 40 + 30 * scale, 10 + 5 * scale
    pic, _ = exp_font.canvas(text)
    ink = exp_font.sample(pic, scale, tx, ty, (h, w))
    lab = np.array(font["label"]["rows"], np.float32) / 255
    at = (tx + font["label"]["dx"] * scale, ty + font["label"]["dy"] * scale)
    label = exp_font.sample(lab, scale, at[0], at[1], (h, w))
    inside = exp_font.sample(np.ones_like(lab), scale, at[0], at[1], (h, w))
    grey = np.full((h, w), 70.0, np.float32) * (1 - inside) + label * 255 * inside
    grey = grey + (255 - grey) * ink
    if blur:
        grey = cv2.GaussianBlur(grey, (0, 0), blur)
    if noise:
        grey = grey + np.random.default_rng(seed).normal(0, noise, grey.shape)
    grey = np.clip(grey, 0, 255).astype(np.uint8)
    if cover:                                                               # a rectangle over part of the number
        grey[int(ty):int(ty + 7 * scale), int(tx + cover[0] * scale):int(tx + cover[1] * scale)] = 60
    items = exp_font.glyph_positions(text, font)
    cell = lambda it: (tx + scale * (it[1] + 2.5), 5 * scale, "digit")
    digits = [it for it in items if it[0].isdigit()]
    n_exp = len(digits) - len(("{:.2f}".format(pct)).replace(".", ""))
    last = items[-1]
    return m.Layout(7 * scale * 1.05, ty + 3.5 * scale, [cell(it) for it in digits[:n_exp]],
                    [cell(it) for it in digits[n_exp:]] + [(tx + scale * (items[-2][1] + 3.5), 7 * scale, "wide")],
                    (tx + scale * items[0][2], ty, tx + scale * (last[2] + last[3]["w"]), ty + 7 * scale), grey.astype(np.float32))


def v2_digits(lay):
    return digits_v2.read_layout_v2(font, lay)


rng = random.Random(7)
for scale in (1.0, 1.5, 2.0, 3.0, 4.0):
    wrong = unknown = 0
    for _ in range(6):
        exp, pct = rng.randint(1, 9_999_999), rng.randint(0, 9999) / 100
        got = v2_digits(fake_layout(exp, pct, scale))
        wrong += got is not None and got != (exp, pct)
        unknown += got is None
    check(f"v2 digits: random numbers drawn at {scale}x read back exactly (no wrong, at most 1 unknown of 6)", wrong == 0 and unknown <= 1)
    check(f"v2 digits: 38,849 [61.02%] drawn at {scale}x reads", v2_digits(fake_layout(38849, 61.02, scale)) == (38849, 61.02))
    check(f"v2 digits: a blurred, noisy copy at {scale}x still reads or is unknown, never another number",
          v2_digits(fake_layout(38849, 61.02, scale, noise=14, blur=0.8 * scale)) in (None, (38849, 61.02)))
    check(f"v2 digits: a number with its first digits covered is unknown, {scale}x", v2_digits(fake_layout(38849, 61.02, scale, cover=(0, 14))) is None)
    check(f"v2 digits: a number with a middle digit covered is unknown, {scale}x", v2_digits(fake_layout(38849, 61.02, scale, cover=(16.5, 23))) is None)
for f in sorted(FRAMES.glob("exp_*.png")):
    bar, size = cv2.imread(str(f)), f.stem[4:]
    check(f"v2 digits read the real bar as 68601 [71.68%], {size}", v2_digits(m.find_exp_v2(bar)) == (68601, 71.68))
    check(f"...also from the colour finder's layout, {size}", m.find_exp(bar) is not None
          and digits_v2.read_layout_v2(font, m.find_exp(bar)) == (68601, 71.68))

# ---- shadow mode: reader v2 runs beside today's reader and only notes whether they agree
import types  # noqa: E402
import shadow as shadow_mod  # noqa: E402
import report  # noqa: E402


class FakeTime:
    """Stands in for the stopwatch: v2's fake reader moves it forward by its cost."""
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def shadow_unit(answer, cost_ms=5.0, finds=True, errors=None):
    clk = FakeTime()
    lay = types.SimpleNamespace(gh=10, box=(100, 20, 300, 30), cy=25)

    def read(font, lay):
        clk.now += cost_ms / 1000
        if isinstance(answer, Exception):
            raise answer
        return answer
    return shadow_mod.Shadow(lambda img, hint: lay if finds else None, read, m.fits_exp_table, lambda: {},
                             lambda img, lay: (img, []), (errors if errors is not None else []).append, timer=clk)


img = np.zeros((40, 400, 3), np.uint8)
LAY = types.SimpleNamespace(gh=10, box=(100, 20, 300, 30), cy=25)
GOOD, OTHER = (68601, 71.68), (68601, 70.0)
for old, new, want in ((GOOD, GOOD, "agree"), (GOOD, None, "old_only"), (None, GOOD, "v2_only"), (None, None, "neither"),
                       ((None, 58.47), (None, 58.47), "agree"), ((None, 58.47), (123456, 58.47), "agree"), ((None, 58.47), (None, 58.4), "disagree")):
    unit = shadow_unit(new)
    unit.fits = lambda e, p: True
    res = unit.look(img, old, 5.0)
    check(f"shadow: old {old} and v2 {new} is '{want}'", res.category == want and unit.counts[want] == 1)
unit = shadow_unit(GOOD)
unit.fits = lambda e, p: True
res = unit.look(img, OTHER, 5.0, LAY)
check("shadow: both sure but different is a disagreement, kept with both answers (the crop only)",
      res.category == "disagree" and list(unit.diffs) == [(OTHER, GOOD)] and len(unit.kept) == 1 and unit.kept[0]["old"] == OTHER)
for _ in range(5):
    unit.look(img, OTHER, 5.0, LAY)
check("shadow: only the last 3 disagreements are kept", len(unit.kept) == 3 and len(unit.diffs) == 3)
check("shadow: a number that fits no level is not 'sure' (v2 stays unknown)",
      not m.fits_exp_table(68601, 99.0) and shadow_unit((68601, 99.0)).look(img, None, 5.0).category == "neither")

errs = []
unit = shadow_unit(ValueError("boom"), errors=errs)
real_mono = m.time.monotonic
clock = [5000.0]
m.time.monotonic = lambda: clock[0]
try:
    res = unit.look(img, GOOD, 5.0)
    check("shadow: v2 raising is counted, reported once, and doesn't escape", res.category == "errors" and unit.counts["errors"] == 1 and len(errs) == 1)
    check("shadow: ...and v2 is skipped for 60 s (not counted)", unit.look(img, GOOD, 5.0) is None and len(errs) == 1)
    clock[0] += 61
    unit.read = lambda font, lay: GOOD
    check("shadow: ...then it runs again", unit.look(img, GOOD, 5.0).category == "agree")

    unit = shadow_unit(GOOD, cost_ms=6)          # v2 costs 60% of today's reader: every other look
    cats = [unit.look(img, GOOD, 10.0).category for _ in range(shadow_mod.GUARD_MIN + 10)]
    tail = cats[shadow_mod.GUARD_MIN + 2:]
    check("shadow CPU guard: more than half of today's cost runs v2 every other look (the rest counted as skipped)",
          unit.thinned and not unit.stopped and tail.count("skipped") >= 3 and tail.count("agree") >= 3 and unit.counts["skipped"] > 0)
    unit = shadow_unit(GOOD, cost_ms=12)         # v2 costs more than today's reader: off for the session
    cats = [unit.look(img, GOOD, 10.0).category for _ in range(shadow_mod.GUARD_MIN + 10)]
    check("shadow CPU guard: costing more than today's reader turns v2 off for the session", unit.stopped and cats[-1] == "skipped")

    # Today's reader sees no bar (a menu, the login screen): v2's whole-strip search costs many times today's there
    # (0.15-1 s on a busy 4K/5K picture), so it searches at most every NOBAR_SECONDS and the CPU guard leaves it out.
    unit = shadow_unit(None, finds=False)
    searches = []

    def slow_find(img, hint):
        unit.timer.now += 0.7
        searches.append(hint)
        return None
    unit.find = slow_find
    got = []
    for _ in range(60):                             # 90 s of looks with no bar
        got.append(unit.look(img, None, 30.0, searching=True))
        clock[0] += 1.5
    ran = [r for r in got if r is not None]
    check("shadow: with no bar on today's side v2 searches at most every 10 s, the looks between aren't counted",
          8 <= len(searches) <= 10 and len(ran) == len(searches) and all(r.category == "neither" for r in ran)
          and sum(unit.counts[k] for k in shadow_mod.CATEGORIES) == len(ran))
    check("shadow: ...and those slow searches never turn v2 off (the guard compares looks where both had the bar)",
          not unit.stopped and not unit.thinned and len(unit.times) == 0)
    unit = shadow_unit(GOOD)
    unit.fits = lambda e, p: True
    first = unit.look(img, None, 30.0, searching=True)
    after = [unit.look(img, None, 30.0, hint=(90, 10, 310, 35), searching=True) for _ in range(5)]
    check("shadow: ...but once v2 has the bar (at a spot it just read), it reads it at every look",
          first.category == "v2_only" and all(r is not None and r.category == "v2_only" for r in after))

    # v2 reads its own small picture, grabbed a moment after today's: a kill in between makes the numbers differ though
    # neither misread (A10-1, seen live). Today's reader then reads v2's picture: the same answer there = EXP ticked.
    pic = np.ones((40, 400, 3), np.uint8)
    unit = shadow_unit(OTHER)
    unit.fits = lambda e, p: True
    res = unit.look(img, GOOD, 5.0, LAY, own=lambda: pic, recheck=lambda p: OTHER if p is pic else None)
    check("shadow: a difference only because EXP ticked between today's picture and v2's own is 'agree' (counted as a tick)",
          res.category == "agree" and unit.counts["ticks"] == 1 and unit.counts["disagree"] == 0 and not unit.diffs)
    unit = shadow_unit(OTHER)
    unit.fits = lambda e, p: True
    res = unit.look(img, GOOD, 5.0, LAY, own=lambda: pic, recheck=lambda p: GOOD)
    check("shadow: ...but if today's reader reads v2's picture differently, it stays a disagreement",
          res.category == "disagree" and unit.counts["ticks"] == 0)
    unit = shadow_unit(OTHER)
    unit.fits = lambda e, p: True
    res = unit.look(img, GOOD, 5.0, LAY, recheck=lambda p: OTHER)
    check("shadow: ...and when both read the same picture nothing is rechecked", res.category == "disagree")
    errs = []
    unit = shadow_unit(OTHER, errors=errs)
    unit.fits = lambda e, p: True

    def broken(p):
        raise ValueError("recheck")
    res = unit.look(img, GOOD, 5.0, LAY, own=lambda: pic, recheck=broken)
    check("shadow: ...a recheck that fails leaves the disagreement and is reported", res.category == "disagree" and len(errs) == 1)
    unit = shadow_unit(GOOD)
    unit.fits = lambda e, p: True
    res = unit.look(img, None, 5.0, None, old_img=img, own=lambda: pic)
    check("shadow: a v2-only look on v2's own picture keeps the crop of THAT picture (not today's, where v2's box isn't)",
          res.category == "v2_only" and len(unit.kept) == 1 and unit.kept[0]["image"] is pic)
    unit = shadow_unit(GOOD)
    unit.on = False
    check("shadow: shadow_reader false runs nothing and counts nothing", unit.look(img, GOOD, 5.0) is None and not any(unit.counts.values()))

    unit = shadow_unit(None, finds=False)
    grabs = []
    more = lambda: (grabs.append(1), (img, 0, 0))[1]
    unit.look(img, GOOD, 5.0, more=more)
    unit.look(img, GOOD, 5.0, more=more)
    clock[0] += 11
    unit.look(img, GOOD, 5.0, more=more)
    check("shadow: v2 grabs the bottom strip itself at most once every 10 s, and counts it", len(grabs) == 2 and unit.counts["grabs"] == 2)
finally:
    m.time.monotonic = real_mono

# through the real reader (a real bar, a fake screen): the Reading is the same with shadow mode on or off
before_tess = (m.TESS, m.tesseract_ok)
m.TESS, m.tesseract_ok = None, False


def shadow_reader(on=True):
    r = part_reader()
    r._game_rect = lambda: (0, 0, fw, fh)
    r.shadow.on = on
    return r


def same_input(a, b):
    """What the tracker is fed: the same fields, whatever shadow mode added."""
    keep = lambda x: (x.where, x.candidates, x.confident, x.text, x.exp_kind, x.percent_only, x.level, x.note)
    return keep(a) == keep(b)


on, off = shadow_reader(True), shadow_reader(False)
looks_on = [on.read(fscreen) for _ in range(5)]
looks_off = [off.read(fscreen) for _ in range(5)]
check("shadow: the tracker gets exactly the same readings with shadow mode on or off (5 looks, whole bar then remembered crop)",
      all(same_input(a, b) for a, b in zip(looks_on, looks_off)) and looks_on[0].confident and looks_on[4].confident)
check("shadow: a clear bar is 'agree' at every look, and nothing is added with the switch off",
      all(x.shadow and x.shadow[0] == "agree" and x.shadow[1] is not None for x in looks_on) and all(x.shadow is None for x in looks_off))
check("shadow: reader v2's spot is handed over once, as (window size/digit height, box)",
      looks_on[0].v2_at and looks_on[0].v2_at[0].startswith(f"{fw}x{fh}/") and all(x.v2_at is None for x in looks_on[1:]) and on.v2_spot)
x0, y0, x1, y1 = looks_on[0].v2_at[1]
check("shadow: ...and the box holds the bar", 0 <= x0 < x1 <= fw and 0 <= y0 < y1 <= fh and (x1 - x0) > 100)

fresh = shadow_reader(True)
fresh.saved_v2 = {looks_on[0].v2_at[0]: looks_on[0].v2_at[1]}
check("shadow: a spot saved in the config is used as the first hint (and found at once)", fresh.read(fscreen).shadow[0] == "agree")

grey = FakeScreen(cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR))
g_on, g_off = shadow_reader(True), shadow_reader(False)
got_on, got_off = g_on.read(grey), g_off.read(grey)
check("shadow: a greyscale picture today's reader can't find but v2 reads is 'v2_only', and today's Reading is untouched",
      got_on.shadow and got_on.shadow[0] == "v2_only" and not got_on.confident and same_input(got_on, got_off) and got_on.exp_kind == "not_found")

bad = shadow_reader(True)
bad.shadow.read = lambda font, lay: 1 / 0
got = bad.read(fscreen)
check("shadow: v2 raising leaves today's reading as it was, counted as an error and a failed part",
      got.confident and got.shadow[0] == "errors" and bad.shadow.counts["errors"] == 1 and bad.part_errors["reader v2"] == 1)

glue, plain = shadow_reader(True), shadow_reader(False)
glue.shadow.look = lambda *a, **k: 1 / 0               # (a failure in the code around v2, outside its own guard)
got, want = glue.read(fscreen), plain.read(fscreen)
check("shadow: a failure around v2 leaves today's reading exactly as it was, counted as a failed part and a shadow error, v2 rests",
      got.confident and same_input(got, want) and got.shadow == ("errors", None) and glue.part_errors["reader v2"] == 1
      and glue.shadow.counts["errors"] == 1 and glue.read(fscreen).shadow is None)

dis = shadow_reader(True)
dis.shadow.read = lambda font, lay: (1234, 12.34)
dis.shadow.fits = lambda e, p: True
got = dis.read(fscreen)
facts = dis.shadow.facts()
check("shadow: a disagreement keeps the EXP bar crop and shows up in the bug report",
      got.shadow[0] == "disagree" and len(dis.shadow.kept) == 1 and dis.shadow.kept[0]["image"].size > 0 and facts["diffs"]
      and "Disagreed" in "\n".join(report.shadow_lines(facts)) and "1,234 [12.34%]" in "\n".join(report.shadow_lines(facts)))
check("shadow: the details text has the 'Reader v2 (test)' part", "== Reader v2 (test) ==" in report.details({"shadow": facts}))

# ---- shadow mode, audit A03: the counts add up, v2's own grab is timed, an idle v2 grabs nothing
import threading  # noqa: E402
import time as real_time  # noqa: E402
CAT = shadow_mod.CATEGORIES
full = cv2.imread(str(FRAMES / "exp_full-1920x1080.png"))


class SlowScreen(FakeScreen):
    """Every grab takes `lag` seconds (a real screen grab costs time); remembers what was grabbed."""
    def __init__(self, image, lag=0.0):
        super().__init__(image)
        self.lag, self.grabbed = lag, []

    def grab(self, r):
        self.grabbed.append((r["left"], r["top"], r["width"], r["height"]))
        real_time.sleep(self.lag)
        return super().grab(r)


def full_reader(on=True):
    r = part_reader()
    r._game_rect = lambda: (0, 0, full.shape[1], full.shape[0])
    r.shadow.on = on
    return r


# a bar that goes dull for good: the 3rd unclear look makes today's reader search again, and the Reading handed
# on must carry the one shadow look of that call (and its counts match what health reports count from Readings)
dull = FakeScreen(np.full_like(full, 40))
seq = full_reader()
reads = [seq.read(FakeScreen(full)) for _ in range(3)] + [seq.read(dull) for _ in range(6)]
check("shadow counts: every shadow look is in exactly one Reading (a bar that goes dull, 9 looks)",
      sum(seq.shadow.counts[k] for k in CAT) == sum(1 for x in reads if x.shadow) and reads[0].shadow and reads[4].shadow)
check("shadow counts: ...and the sum of the categories is the number of runs + skipped + errors",
      sum(seq.shadow.counts[k] for k in CAT) == seq.shadow.counts["runs"] + seq.shadow.counts["skipped"] + seq.shadow.counts["errors"])

# the rules, with many mixed looks: every look that ran is in exactly one category
mix = shadow_unit(GOOD)
mix.fits = lambda e, p: True
rr = random.Random(7)
results = []
for i in range(300):
    mix.read = lambda font, lay, v=rr.choice([GOOD, OTHER, None, (None, 71.68), (None, 5.0)]): v
    result = mix.look(img, rr.choice([GOOD, None, OTHER, (None, 71.68)]), 5.0, LAY)
    results.append(result)
check("shadow counts: 300 mixed looks are counted exactly once each",
      sum(mix.counts[k] for k in CAT) == 300 == sum(1 for x in results if x is not None)
      and all(x.category == "skipped" or mix.counts[x.category] > 0 for x in results))
# v2's own small grab (the remembered spot is not inside the picture today's reader holds) is part of v2's time
lag = 0.02
slow = full_reader()
scr = SlowScreen(full, lag)
slow.read(scr)
steady = [slow.read(scr) for _ in range(3)]
check("shadow time: v2's own screen grab is counted in its milliseconds (grab 20 ms, v2 time >= 20)",
      all(x.shadow and x.shadow[1] >= lag * 1000 * 0.9 for x in steady))

# v2 that is off, paused, stopped or skipping this look grabs nothing
for how in ("stopped", "paused", "skip"):
    idle = full_reader()
    scr = SlowScreen(full)
    idle.read(scr)
    if how == "stopped":
        idle.shadow.stopped = True
    elif how == "paused":
        idle.shadow.pause_until = real_time.monotonic() + 100
    else:
        idle.shadow._allowed = lambda: False
    scr.grabbed.clear()
    got = idle.read(scr)
    check(f"shadow: v2 {how} makes no screen grab of its own (only today's one)", len(scr.grabbed) == 1)
off = full_reader(False)
scr = SlowScreen(full)
off.read(scr)
scr.grabbed.clear()
off.read(scr)
check("shadow: shadow_reader false makes no screen grab of its own", len(scr.grabbed) == 1 and not any(off.shadow.counts.values()))

# a failure in the glue around v2 (its own grab raising) is counted as a shadow error and pauses v2 like any other
boom = full_reader()
boom_scr = SlowScreen(full)
boom.read(boom_scr)
real_grab = boom_scr.grab


def grab_fails_for_small(r):
    if (r["left"], r["top"], r["width"], r["height"]) != tuple(boom.box):
        raise OSError("grab failed")
    return real_grab(r)


boom_scr.grab = grab_fails_for_small
got = boom.read(boom_scr)
check("shadow: v2's own grab raising is a counted shadow error, today's reading stays, v2 pauses",
      got.confident and got.shadow and got.shadow[0] == "errors" and boom.shadow.counts["errors"] == 1
      and boom.part_errors["reader v2"] == 1 and boom.read(boom_scr).shadow is None)

# facts() for the bug report is read on the main thread while the reading thread adds looks
race = shadow_unit(GOOD, cost_ms=0.0)
race.fits = lambda e, p: True
stop_flag = []


def hammer():
    while not stop_flag:
        race.look(img, GOOD, 5.0)


worker = threading.Thread(target=hammer)
old_switch = sys.getswitchinterval()
sys.setswitchinterval(1e-6)
worker.start()
race_error = None
try:
    for _ in range(3000):
        race.facts()
except Exception as e:
    race_error = e
finally:
    stop_flag.append(1)
    worker.join()
    sys.setswitchinterval(old_switch)
check(f"shadow: facts() for the bug report is safe while the reading thread adds looks ({race_error!r})", race_error is None)

m.TESS, m.tesseract_ok = before_tess

# ---- reader v2 on bars drawn from the font itself (exact truth), including ones with a digit lost
def font_bar(exp, pct, s, scene_w=None):
    """"EXP. 12,345 [67.89%]" (or "EXP. [67.89%]" when exp is None) drawn from the font at scale s on a dark background."""
    text = (f"{exp:,} " if exp is not None else "") + f"[{pct:.2f}%]"
    cv, _ = exp_font.canvas(text)
    lab = np.array(font["label"]["rows"], np.float32) / 255
    h, w = cv.shape
    H, W = int(h * s + 60), int(w * s + 60 * s)
    img = np.zeros((H, W), np.float32)
    tx, ty = 34 * s, H / 2 - 3.5 * s
    x0, y0, ww, hh = int(tx) - 2, int(ty) - 2, int(w * s) + 6, int(h * s) + 6
    img[y0:y0 + hh, x0:x0 + ww] = exp_font.sample(cv, s, tx - x0, ty - y0, (hh, ww))
    left, top = tx + font["label"]["dx"] * s, ty + font["label"]["dy"] * s
    lx, ly, lw, lh = int(left) - 2, int(top) - 2, int(font["label"]["w"] * s) + 6, int(font["label"]["h"] * s) + 6
    img[ly:ly + lh, lx:lx + lw] = np.maximum(img[ly:ly + lh, lx:lx + lw], exp_font.sample(lab, s, left - lx, top - ly, (lh, lw)))
    g = (img * 255).astype(np.uint8)
    return cv2.merge([g, g, g])


def read_v2(img):
    lay = m.find_exp_v2(img)
    return digits_v2.read_layout_v2(font, lay) if lay else None


for exp, pct, s in ((68601, 71.68, 3.0), (None, 36.62, 7.0), (None, 36.62, 3.0), (None, 36.62, 1.875)):
    bar = font_bar(exp, pct, s)
    check(f"a bar drawn from the font reads back exactly, {exp} [{pct}%] at {s}x", read_v2(bar) == (exp, pct))
# a black strip over the first digit of the percentage must not turn 36.62 into 6.62 (the rest still looks like a clean line)
lost = []
bar = font_bar(None, 36.62, 7.0)
for x in range(0, bar.shape[1] - 30, 6):
    for wd in (7, 14, 20, 28):
        cut = bar.copy()
        cut[:, x:x + wd] = 0
        got = read_v2(cut)
        if got not in (None, (None, 36.62)):
            lost.append((x, wd, got))
check(f"a %-only bar with a strip over the first percentage digit is never read as another number {lost[:2]}", not lost)

# hostile inputs: v2 gives a result or None, never raises, never a number that isn't the truth (fixed seed)
fz = np.random.default_rng(5)
fz_frame = cv2.imread(str(FRAMES / "exp_win-1280x720.png"))
fz_h, fz_w = fz_frame.shape[:2]
fz_cases = {"1x1": np.zeros((1, 1, 3), np.uint8), "10x10000": fz.integers(0, 256, (10, 10000, 3), dtype=np.uint8),
            "20000x10": fz.integers(0, 256, (20000, 10, 3), dtype=np.uint8), "all white": np.full((60, 400, 3), 255, np.uint8),
            "noise": fz.integers(0, 256, (120, 600, 3), dtype=np.uint8), "mirrored": fz_frame[:, ::-1].copy(),
            "two bars": np.vstack([fz_frame, fz_frame]), "gray 2-d": cv2.cvtColor(fz_frame, cv2.COLOR_BGR2GRAY),
            "gray 1 channel": cv2.cvtColor(fz_frame, cv2.COLOR_BGR2GRAY)[:, :, None], "BGRA": cv2.cvtColor(fz_frame, cv2.COLOR_BGR2BGRA),
            "not contiguous": np.asfortranarray(fz_frame), "a view": np.repeat(fz_frame, 2, axis=1)[:, ::2]}
for cut in range(0, 40, 3):
    fz_cases[f"cut left {cut}"], fz_cases[f"cut right {cut}"] = fz_frame[:, cut:], fz_frame[:, :fz_w - cut]
    fz_cases[f"cut top {cut}"], fz_cases[f"cut bottom {cut}"] = fz_frame[cut:], fz_frame[:fz_h - cut]
for x in range(0, fz_w, 7):
    masked = fz_frame.copy()
    masked[:, x:x + 5] = 0
    fz_cases[f"strip at {x}"] = masked
fz_bad, fz_raised = [], []
for name, pic in fz_cases.items():
    try:
        lay = m.find_exp_v2(pic)
        got = digits_v2.read_layout_v2(font, lay) if lay else None
    except Exception as e:
        fz_raised.append((name, repr(e)))
        continue
    if got not in (None, (68601, 71.68)):
        fz_bad.append((name, got))
for hint in ((-100, -100, 5, 5), (10 ** 9, 10 ** 9, 10 ** 9 + 50, 10 ** 9 + 50), (50, 50, 10, 10), (-1e9, -1e9, 1e9, 1e9), (float("nan"), 0, 10, 10),
             (0, 0, float("inf"), float("inf")), "garbage", (1, 2, 3), (3.7, 2.2, fz_w - 1.5, fz_h - 0.4)):
    try:
        lay = m.find_exp_v2(fz_frame, hint)
        got = digits_v2.read_layout_v2(font, lay) if lay else None
    except Exception as e:
        fz_raised.append((f"hint {hint!r}", repr(e)))
        continue
    if got not in (None, (68601, 71.68)):
        fz_bad.append((f"hint {hint!r}", got))
check(f"v2 on {len(fz_cases) + 9} hostile pictures and hints: never raises {fz_raised[:3]}", not fz_raised)
check(f"...and never gives a number that isn't the truth {fz_bad[:3]}", not fz_bad)
check("...a plain grayscale or BGRA copy of the bar reads like the colour one",
      all(read_v2(fz_cases[k]) == (68601, 71.68) for k in ("gray 2-d", "gray 1 channel", "BGRA", "not contiguous", "a view")))

# the whole-width search must find the bar whatever the window's width (the blur that tells text from background grows with it)
wide_bar = cv2.imread(str(FRAMES / "exp_win-1024x768.png"))
for scale, width in ((1.875, 2560), (1.875, 2500), (1.875, 1920), (2.8125, 3840), (3.75, 5120)):
    bar = cv2.resize(wide_bar, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    wide = np.full((317, width, 3), 30, np.uint8)
    wide[15:15 + bar.shape[0], 12:12 + bar.shape[1]] = bar
    check(f"a {scale}x bar in a strip {width} px wide is found and read", read_v2(wide) == (68601, 71.68))

# ---- the reading thread never dies for good: a screen that can't be opened (or anything else outside a look) is retried
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402


class GoodScreen:
    monitors = [{"left": 0, "top": 0, "width": 100, "height": 100}]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_worker(opens, reader, seconds=1.5):
    """The real App._worker on a stand-in app: `opens` is what mss.mss() does at each try.
    -> (still alive after `seconds`, the kinds of message queued, looks done, still alive after being asked to stop)."""
    app = types.SimpleNamespace(stop_evt=threading.Event(), q=queue.Queue(), health=m.Health(), reader=reader)
    real_mss, real_wait = m.mss.mss, m.WORKER_RETRY_SECONDS
    m.mss.mss, m.WORKER_RETRY_SECONDS = opens, 0.2
    try:
        t = threading.Thread(target=m.App._worker, args=(app,), daemon=True)
        t.start()
        time.sleep(seconds)
        alive, kinds = t.is_alive(), [k for k, _ in list(app.q.queue)]
        app.stop_evt.set()
        t.join(3)
        return alive, kinds, app.health.polls, t.is_alive()
    finally:
        m.mss.mss, m.WORKER_RETRY_SECONDS = real_mss, real_wait


class OneLook:
    def read(self, capture):
        return m.Reading(*([None] * len(m.Reading._fields)))


tries = []


def flaky_open():
    tries.append(1)
    if len(tries) <= 2:
        raise OSError("cannot open the screen (test)")
    return GoodScreen()


alive, kinds, polls, still = run_worker(flaky_open, OneLook())
check("the reading thread tries again when the screen can't be opened, and then reads", alive and len(tries) >= 3 and polls >= 1)
check("...the player is told meanwhile (an error reaches the window) and the thread ends when asked",
      "error" in kinds and "reading" in kinds and not still)


# the small UI (UI Size "Ideal", or a small window) on a very wide screen: the text is far under 0.3% of the strip,
# so the finder asks each stretch alone and, if that is not enough, uses a smaller blur (A02-4)
for scale, width, bg in ((1.0, 2560, 30), (1.0, 3840, 30), (1.0, 5120, 30), (1.25, 3840, 30), (1.25, 5120, 30),
                         (1.5, 5120, 30), (1.0, 3840, 60), (1.25, 3840, 60)):
    bar = cv2.resize(wide_bar, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR) if scale != 1 else wide_bar
    wide = np.full((317, width, 3), bg, np.uint8)
    wide[15:15 + bar.shape[0], 12:12 + bar.shape[1]] = bar
    check(f"a {scale}x bar on a {bg} grey strip {width} px wide is found and read", read_v2(wide) == (68601, 71.68))


def box_window(width, height, text, ideal=False):
    """A window-sized picture with the text drawn the way the model says (hard 1x pixels, each screen pixel
    averaging the ones it covers). SYNTHETIC: it can't prove the game draws it this way, A10 checks that live."""
    s = exp_font.window_scale(height, ideal)
    cv, _ = exp_font.canvas(text)
    tx, ty = exp_font.text_origin(width, height, ideal)
    x0, y0, ww, hh = int(tx) - 3, int(ty) - 3, int(cv.shape[1] * s) + 8, int(cv.shape[0] * s) + 8
    cov = np.zeros((hh, ww), np.float32)
    for i in range(6):
        for j in range(6):
            xs = ((np.arange(ww) + (i + 0.5) / 6 + x0 - tx) / s - 0.5).astype(np.float32)
            ys = ((np.arange(hh) + (j + 0.5) / 6 + y0 - ty) / s - 0.5).astype(np.float32)
            mx, my = np.meshgrid(xs, ys)
            cov += cv2.remap(cv, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0) / 36
    lab = np.array(font["label"]["rows"], np.float32) / 255
    lab = np.clip((lab - lab.min()) / (1 - lab.min()), 0, 1)
    left, top = tx + font["label"]["dx"] * s, ty + font["label"]["dy"] * s
    lx, ly = int(left) - 3, int(top) - 3
    lw, lh = int(font["label"]["w"] * s) + 8, int(font["label"]["h"] * s) + 8
    lcov = np.zeros((lh, lw), np.float32)
    for i in range(6):
        for j in range(6):
            xs = ((np.arange(lw) + (i + 0.5) / 6 + lx - left) / s - 0.5).astype(np.float32)
            ys = ((np.arange(lh) + (j + 0.5) / 6 + ly - top) / s - 0.5).astype(np.float32)
            mx, my = np.meshgrid(xs, ys)
            lcov += cv2.remap(lab, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0) / 36
    img = np.full((height, width), 74, np.float32)
    img[ly:ly + lh, lx:lx + lw] = 74 + 181 * lcov
    img[y0:y0 + hh, x0:x0 + ww] = 74 + 181 * cov
    return cv2.merge([img.astype(np.uint8)] * 3)


for (w_, h_, ideal) in ((2560, 1440, False), (2560, 1600, False), (2732, 1536, False), (3840, 2160, False),
                        (5120, 2880, False), (1920, 1080, True), (2560, 1440, True), (3440, 1440, True),
                        (3840, 2160, True), (5120, 2880, True)):
    win = box_window(w_, h_, "68,601 [71.68%]", ideal)
    strip = win[int(h_ * (1 - m.SEARCH_STRIP)):]
    check(f"synthetic {w_}x{h_}, UI {'Ideal' if ideal else 'Default'}: the bar is found in the bottom strip and read",
          read_v2(strip) == (68601, 71.68))
print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
