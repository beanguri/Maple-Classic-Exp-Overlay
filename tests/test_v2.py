"""Tests for reader v2's shared pieces: the UI-size rule (ui_layout.py), the potion bar's types (potion_bar.py)
and the second health report contract. No game or screen needed.

Run from the project folder:  python tests/test_v2.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exp_font  # noqa: E402
import potion_bar  # noqa: E402
import ui_layout as ul  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---- the UI size: Default grows with the window, Ideal in whole steps
for w, h in ul.GAME_SIZES + ((5120, 2880), (4096, 2160), (3440, 1440)):
    pairs = ul.candidate_scales(h)
    check(f"{w}x{h}: Default is tried first, at window height / 768 (never below 1x)",
          pairs[0][0] in ("default", None) and near(pairs[0][1], max(1.0, h / 768)))
    scales = [s for _, s in pairs]
    check(f"{w}x{h}: no scale is tried twice", all(abs(a - b) > 0.01 * a for i, a in enumerate(scales) for b in scales[i + 1:]))
    check(f"{w}x{h}: Ideal's own step is tried right after Default (when it differs)",
          len(pairs) > 1 and pairs[1][0] == "ideal" and pairs[1][1] == ul.ideal_step(h) or near(ul.default_scale(h), ul.ideal_step(h)))
    check(f"{w}x{h}: every whole step up to {ul.MAX_STEP} is tried",
          all(any(near(s, k) for s in scales) for k in range(1, ul.MAX_STEP + 1)))

# Measured on the 2026-10-26 recordings (ticket 04): Ideal 1x at 720, 1080, 1200; 2x at 1440 (windowed and
# fullscreen), 1600, 2160, 2880. Default never below 1x (800x600 and 1280x720 draw the same bar).
check("Ideal is 1x at 720, 1080p and 1200", ul.ideal_step(720) == ul.ideal_step(1080) == ul.ideal_step(1200) == 1)
check("Ideal is 2x at 1440, 1600, 2160 and 5K", all(ul.ideal_step(h) == 2 for h in (1440, 1600, 2160, 2880)))
check("Ideal never goes below 1x (800x600)", ul.ideal_step(600) == 1)
check("Default is 1x below 768 (800x600, 1280x720)", ul.default_scale(600) == ul.default_scale(720) == 1.0)
check("below 768 both modes draw 1x: one candidate, mode unknown, then 2x",
      ul.candidate_scales(720) == [(None, 1.0), ("ideal", 2.0)] and ul.candidate_scales(600) == [(None, 1.0), ("ideal", 2.0)])
check("at 1440 Default (1.875) and Ideal 2x are both tried, then 1x (Send report #5)",
      [(m, round(s, 3)) for m, s in ul.candidate_scales(1440)] == [("default", 1.875), ("ideal", 2.0), ("ideal", 1.0)])
check("at 768 both modes draw the same: one candidate, mode unknown", ul.candidate_scales(768)[0] == (None, 1.0)
      and sum(1 for _, s in ul.candidate_scales(768) if near(s, 1.0)) == 1)

# ---- which mode the EXP digit height says (only which: the scale comes from the window)
check("gh 11 at 1080p: Default", ul.mode_from_gh(11.0, 1080) == ("default", 1080 / 768))
check("gh 7 at 1080p: Ideal 1x", ul.mode_from_gh(7.0, 1080) == ("ideal", 1.0))
check("gh 14 at 1440p: can't tell (Default 1.875 and Ideal 2x both give 14)", ul.mode_from_gh(14.0, 1440) is None)
check("gh 7 at 1440p: Ideal 1x (Send report #5; the rule says 2x, the other step still fits)",
      ul.mode_from_gh(7.0, 1440) == ("ideal", 1.0))
check("gh 7 at 800x600: can't tell (both modes are 1x)", ul.mode_from_gh(7.0, 600) is None)
check("gh 20 at 4K: Default", ul.mode_from_gh(20.0, 2160) == ("default", 2160 / 768))
check("gh 14 at 4K: Ideal 2x", ul.mode_from_gh(14.0, 2160) == ("ideal", 2.0))
check("gh 14 at 5K fullscreen: Ideal 2x", ul.mode_from_gh(14.0, 2880) == ("ideal", 2.0))
check("at 768 it can't tell", ul.mode_from_gh(7.0, 768) is None)
check("at 1280x720 it can't tell (both modes give about 7)", ul.mode_from_gh(7.0, 720) is None)
check("no gh: can't tell", ul.mode_from_gh(None, 1080) is None and ul.mode_from_gh(0, 1080) is None)
check("a gh that fits neither mode: can't tell", ul.mode_from_gh(30.0, 1080) is None)
check("the gh table never goes down as windows grow",
      all(ul.default_gh(h) <= ul.default_gh(h + 10) for h in range(500, 3000, 10)))

# ---- design area <-> window
for w, h in ((1024, 768), (1920, 1080), (3840, 2160), (5120, 2880)):
    for s in (h / 768, 1.0, 2.0):
        x, y = ul.design_to_window(760.0, 573.0, w, h, s)
        bx, by = ul.window_to_design(x, y, w, h, s)
        check(f"{w}x{h} at {s:.3f}x: a point goes there and back", near(bx, 760.0, 1e-6) and near(by, 573.0, 1e-6))
    check(f"{w}x{h}: the design area's bottom centre is the window's", ul.design_to_window(512, 768, w, h, 2.0) == (w / 2, h))
# The same place the EXP font puts its text (exp_font.text_origin) at UI Size Default.
font = exp_font.load()
o = font["scale"]["origin"]
for w, h in ((1920, 1080), (2560, 1440)):
    check(f"{w}x{h}: the same rule as the EXP font's text origin",
          all(near(a, b) for a, b in zip(ul.design_to_window(o["x"], o["y"], w, h, h / 768), exp_font.text_origin(w, h))))
# Where today's potion reader puts the bar (5120x2880 reference, 930 px right of centre, 730 up), in design pixels.
x, y = ul.design_to_window(512 + 930 / 3.75, 768 - 730 / 3.75, 1920, 1080, 1.0)
check("Ideal at 1080p puts the potion bar where it was found on the Ideal frames (1208, 885)",
      near(x, 1208.0, 0.5) and near(y, 1080 - 730 / 3.75, 0.5))

# ---- the potion bar's types (other tickets build against these exact fields)
check("SlotSpot fields", potion_bar.SlotSpot._fields == ("index", "rect", "style"))
check("BarLook fields", potion_bar.BarLook._fields == ("layout", "mode", "scale", "origin", "slots"))
check("SlotRead fields", potion_bar.SlotRead._fields == ("index", "rect", "style", "item", "kind", "count", "run"))
check("PotionLook fields", potion_bar.PotionLook._fields == ("seen", "layout", "mode", "scale", "slots"))
check("Hint fields", potion_bar.Hint._fields == ("last", "mode_scale", "runs"))
check("layouts: keyboard and the two controller D-pad modes",
      potion_bar.LAYOUTS == ("keyboard", "controller-standard", "controller-cross"))
check("controller layouts are told apart from keyboard", [potion_bar.is_controller(x) for x in potion_bar.LAYOUTS]
      == [False, True, True] and not potion_bar.is_controller(None))
check("no picture: the reader says 'not seen', never a guess",
      potion_bar.read_potions(None, 1920, 1080) == potion_bar.NOT_SEEN and not potion_bar.NOT_SEEN.seen)

# ---- the health report contract (the site and the overlay are tested against it; 0.9.13's cases are the ones without "v2:")
here = Path(__file__).parent
new = json.loads((here / "health_contract_v2.json").read_text(encoding="utf-8"))
check("the old contract file is gone (its cases live on in the v2 file)", not (here / "health_contract.json").exists())
check("contract v2 still has 0.9.13's three valid bodies", [c["name"] for c in new["health_valid"][:3]]
      == ["full", "minimal, game never found", "mac, scaling unknown, bar never found, region"])
NEW = ("ui", "shadow_mesos", "shadow_party", "shadow_why")
for key in NEW:
    check(f"contract v2 has a valid case with {key}", any(key in c["body"] for c in new["health_valid"]))
    check(f"contract v2 has invalid cases for {key}", any(c["field"] == key for c in new["health_invalid"]))
biggest = max((c["body"] for c in new["health_valid"]), key=lambda b: len(json.dumps(b, separators=(",", ":"))))
check("the biggest valid body fits the site's 4,096-byte cap", len(json.dumps(biggest, separators=(",", ":")).encode()) <= 4096)
for c in new["health_valid"]:
    b = c["body"]
    check(f"'{c['name']}': every new count is at most 'looks'",
          all(v <= b["looks"] for k in ("ui", "shadow_why") if k in b for v in b[k].values()))

# ---- the glyph engine and the potion count font (ticket 05): counts drawn from the font itself over made-up slots
import numpy as np  # noqa: E402
import glyphs  # noqa: E402

COUNT_FONT = glyphs.Font.load("potion_count")
SCALES = (1.0, 1080 / 768, 1200 / 768, 1440 / 768, 2.0, 2160 / 768, 2880 / 768)     # every size of the game
PLATE, FRAME = (250, 249, 248), (50, 45, 40)


def fake_slot(text, scale, back=PLATE, dx=0.37, dy=0.61, font=None, kernel=None, style="keyboard", cover=None, window=None):
    """A picture with one potion slot (light plate, dark frame around it) and `text` drawn where the game puts a
    count, smoothed (or copied block for block at a whole step) as the game scales. -> (picture, rect)."""
    w, h = int(round(32 * scale)), int(round(31 * scale))
    x, y = int(round(10 * scale)), int(round(8 * scale))
    pic = np.zeros((int(y + h + 10 * scale), int(x + w + 36 * scale), 3), np.float32)
    pic[:] = FRAME
    pic[y:y + h, x:x + w] = PLATE
    pic[y + int(0.15 * h):y + int(0.95 * h), x:x + int(0.75 * w)] = back          # an icon behind the count
    ox, oy = COUNT_FONT.styles[style]["origin"]
    if text:
        p = glyphs.draw(font or COUNT_FONT, text, scale, x + (ox + dx) * scale, y + h + (oy + dy) * scale, pic.shape[:2],
                        kernel or ("point" if abs(scale - round(scale)) < 0.01 else "bilinear"))
        pic = pic * (1 - p[0])[:, :, None] + np.transpose(p[1:], (1, 2, 0))
    if cover:                                          # a dark box over the left part of the count
        pic[int(y + 0.5 * h):int(y + h), x:x + int(cover * w)] = 60
    if window:                                         # another window over the right part of the slot, its edge too
        pic[:, int(x + window * w):] = (215, 205, 200)
    return np.clip(np.round(pic), 0, 255).astype(np.uint8), (x, y, w, h)


def count_of(text, scale, **kw):
    pic, rect = fake_slot(text, scale, **kw)
    return glyphs.read_count(pic, rect, scale)[0]


backs = {"plate": PLATE, "dark icon": (30, 30, 120), "lemon": (20, 230, 250), "grey": (120, 118, 116)}
good = total = 0
for scale in SCALES:
    for text in ("0", "7", "23", "305", "4096", "9999", "1234", "888", "381", "1000"):
        for name, back in backs.items():
            got = count_of(text, scale, back=back)
            total += 1
            good += got == int(text)
            if got != int(text):
                print(f"   {text} at {scale:.4f} over {name}: read {got}")
check(f"counts of 1 to 4 digits are read exactly at every size, over any back ({good}/{total})", good == total)

wrong = 0
for scale in SCALES:
    for d in "0123456789":
        text = d * 2 if d != "0" else "10"
        got = count_of(text, scale)
        wrong += got is not None and got != int(text)
check("no digit is ever read as another (every digit twice, every size)", wrong == 0)
check("a whole step smoothed (UI Size Default at 1536 tall) reads too, not only block for block",
      count_of("306", 2.0, kernel="bilinear") == 306)


# Any size, any sub-pixel position, any count: a seeded handful of random cases (the exact answer, never another)
import random  # noqa: E402
rnd = random.Random(7)
icons = list(backs.values()) + [(200, 160, 90), (40, 60, 200)]
bad = unknown = 0
for _ in range(300):
    scale = rnd.choice(SCALES)
    digits = rnd.choice((1, 2, 3, 3, 4))
    text = str(rnd.randint(10 ** (digits - 1) if digits > 1 else 0, 10 ** digits - 1))
    got = count_of(text, scale, back=rnd.choice(icons), dx=rnd.uniform(-1, 1), dy=rnd.uniform(-1, 1))
    bad += got is not None and got != int(text)
    unknown += got is None
check(f"300 random counts at random positions and sizes: none read wrong, under 2% missed ({bad} wrong, {unknown} missed)",
      bad == 0 and unknown <= 6)

# What must never be read as a count
yellow = glyphs.Font({"name": "yellow", "fill": [[0, 255, 255]] * COUNT_FONT.rows,
                      "outline": {"rule": "cross", "color": [0, 0, 0]},
                      "glyphs": {d: {"rows": ["".join("X" if c else "." for c in r) for r in COUNT_FONT.glyphs[d]],
                                     "adv": COUNT_FONT.adv[d]} for d in COUNT_FONT.chars}})
check("a yellow skill cooldown, same shapes, is never taken for a count (every size)",
      all(count_of("22", s, font=yellow) is None for s in SCALES))
check("an empty slot gives no number", all(count_of("", s) is None for s in SCALES))
pic, rect = fake_slot("", 1.4)
pic[:] = np.random.RandomState(5).randint(0, 256, pic.shape)
check("noise gives no number", glyphs.read_count(pic, rect, 1.4)[0] is None)
for s in (1.0, 1080 / 768, 2.0, 3.75):
    check(f"a count with its left part covered gives no number (scale {s:.2f})", count_of("253", s, cover=0.6) is None)
    check(f"a window over the slot's right part, hiding the last digit, gives no number (scale {s:.2f})",
          count_of("253", s, window=0.57) is None and count_of("253", s) == 253)
check("a count can't start with a 0 (except 0 itself)", count_of("05", 1.4) is None and count_of("0", 1.4) == 0)

# A spot that read once is checked first next time (the slot didn't move): same answer, far less work
pic, rect = fake_slot("23", 1.4)
got, run = glyphs.read_count(pic, rect, 1.4)
got2, run2 = glyphs.read_count(fake_slot("24", 1.4)[0], rect, 1.4, hint=run)
check("a hint from the last look gives the new number at the same spot",
      got == 23 and got2 == 24 and (run2.tx, run2.ty) == (run.tx, run.ty))
pic3, rect3 = fake_slot("24", 1.4, dx=2.0, dy=-1.0)
got3, run3 = glyphs.read_count(pic3, rect3, 1.4, hint=run)
check("a hint that no longer fits is dropped and the slot is read afresh",
      got3 == 24 and (run3.tx, run3.ty) != (run.tx, run.ty))

# The font asset itself
check("the count font has the ten digits, 9 rows each, and only 1 is narrower than the others",
      COUNT_FONT.chars == "0123456789" and all(COUNT_FONT.glyphs[d].shape[0] == 9 for d in COUNT_FONT.chars)
      and [d for d in COUNT_FONT.chars if COUNT_FONT.adv[d] != 8] == ["1"])
check("the count font says where the text sits in both slot styles", set(COUNT_FONT.styles) == {"keyboard", "controller"})


# ---- reader v2 for the mesos box (ticket 13): a box drawn from the mesos font, found by its frame, read at any size
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import glyphs  # noqa: E402
import mesos_v2  # noqa: E402


def game_box(value, bg=255, noise=0, x0=40, y0=30, frame=True, coin=True, covered=0, seed=1):
    """A mesos box on the game's own pixels (300 x 80, BGR): frame, coin, scenery or white inside, the number right aligned."""
    rng = np.random.RandomState(seed)
    img = np.full((80, 300, 3), 150, np.uint8)
    if noise:
        img[:] = np.clip(150 + rng.randint(-noise, noise + 1, (80, 300, 1)), 0, 255)
    img[y0 + 1:y0 + 15, x0 + 1:x0 + 144] = bg if not noise else np.clip(bg + rng.randint(-noise, noise + 1, (14, 143, 1)), 0, 255)
    if frame:
        img[y0, x0:x0 + 145] = img[y0 + 15, x0:x0 + 145] = 83
        img[y0:y0 + 16, x0] = img[y0:y0 + 16, x0 + 144] = 83
    if coin:
        cv2.circle(img, (x0 - 9, y0 + 7), 5, (30, 170, 220), -1)
    font = glyphs.Font.load("mesos")
    right = x0 + 144 - 4                                   # the last digit's right column
    for ch in reversed(f"{value:,}"):
        bits = font.glyphs[ch]
        for r in range(bits.shape[0]):
            for c in range(bits.shape[1]):
                if bits[r, c]:
                    img[y0 + 4 + r, right - 4 + c] = 0
        right -= 4 if ch == "," else 6
    if covered:
        img[y0:y0 + 16, x0:x0 + covered] = 200
    return img


def on_screen(img, scale):
    """The game picture the way the game scales it: copied block for block at a whole step, smoothed otherwise."""
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST if abs(scale - round(scale)) < 0.01 else cv2.INTER_LINEAR)


for sc in (1.0, 1.40625, 1.875, 2.0, 3.0):
    for v in (0, 7, 1234, 24697, 9999999, 123456789):
        look, _ = mesos_v2.read_mesos_v2(on_screen(game_box(v), sc), [sc])
        check(f"mesos v2: {v:,} read at {sc}x", look.value == v and look.why == "ok")
    look, _ = mesos_v2.read_mesos_v2(on_screen(game_box(21890, bg=170, noise=12), sc), [sc])
    check(f"mesos v2: the see-through box over scenery reads or says nothing at {sc}x", look.value in (None, 21890))
    check(f"mesos v2: a number whose start a window covers is never a shorter number at {sc}x",
          mesos_v2.read_mesos_v2(on_screen(game_box(24697, covered=110), sc), [sc])[0].value is None)
    check(f"mesos v2: a frame without the coin isn't the mesos box at {sc}x", mesos_v2.read_mesos_v2(on_screen(game_box(24697, coin=False), sc), [sc])[0].why == "not_found")
look, found = mesos_v2.read_mesos_v2(on_screen(game_box(1234), 1.0), [2.0, 1.0])
check("mesos v2: tries every scale it is given", look.value == 1234)
look2, _ = mesos_v2.read_mesos_v2(on_screen(game_box(1234), 1.0), [2.0, 1.0], found)
check("mesos v2: a spot that read once is checked first and gives the same answer", look2.value == 1234)
pair = np.concatenate([game_box(1234), game_box(5678)], axis=0)
look, _ = mesos_v2.read_mesos_v2(pair, [1.0])
check("mesos v2: two boxes = the player's can't be told: no value", look.value is None and look.why == "unclear" and len(look.boxes) == 2)
check("mesos v2: nothing on the picture is not_found", mesos_v2.read_mesos_v2(np.full((200, 300, 3), 128, np.uint8), [1.0])[0].why == "not_found")
check("mesos v2: the font has the ten digits and the comma", sorted(glyphs.Font.load("mesos").chars) == sorted("0123456789,"))

# ---- the potion table at the controller's slot style (numbers only: no pictures needed)
import potions  # noqa: E402

table = potion_bar.load_assets()
real = [k for k, kind in enumerate(table.kinds) if kind in ("consumable", "other_ignore")]
check("every potion, food and known other item has a controller drawing in the table",
      all(table.items[k].get("fingerprint_controller") for k in real))
for style, matrix in (("controller", table.matrix_controller), ("keyboard", table.matrix)):
    swapped = [(table.names[k], found[0]) for k in real
               for found in [table.identify(matrix[k], style)] if found is not None and found[0] != table.names[k]]
    check(f"an item drawn at the {style} style is itself or refused, never another item ({swapped or 'none swapped'})", not swapped)
close = sorted(float(table._distances(table.matrix_controller[a][None, :], table.matrix_controller[b])[0])
               for i, a in enumerate(real) for b in real[i + 1:])
check(f"no two items' controller drawings are closer than 0.3 (the closest pair: {close[0]:.2f})", close[0] >= 0.3)
leaks = []
for k, kind in enumerate(table.kinds):
    if kind != "skill":
        continue
    for key, style in (("seen_controller", "controller"), ("seen", "keyboard")):
        for fp in table.items[k].get(key, ()):
            found = table.identify(np.array(fp, np.float32), style)
            if found is not None and found[1] != "skill":
                leaks.append((table.items[k]["name"], style, found[0]))
check(f"no skill icon the table knows (either slot style, damaged copies too) reads as a potion ({leaks or 'none'})", not leaks)
check("the controller has its own skill entries, and controller-seen items", any(n.startswith("Controller skill icon") for n in table.names)
      and sum(1 for i in table.items if i.get("seen_controller") and i["kind"] == "consumable") >= 4)
check("the controller's light-background rule is looser than the keyboard's, and still refuses the pink tiles",
      potions.CONTROLLER_RING_BG_MIN < potions.RING_BG_MIN and potions.CONTROLLER_RING_BG_MIN > 0.18)
shown = potion_bar.controller_rects(1920, 1080, 1.40625)
check("the controller's cluster has 8 cells numbered 0-7, none overlapping",
      sorted(shown) == list(range(8)) and all(not (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                                               for i, a in shown.items() for j, b in shown.items() if i < j))

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
