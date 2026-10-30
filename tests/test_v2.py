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
bank_font = glyphs.Font.load("potion_count")
for sc in np.arange(4.0, 6.9, 0.005):                      # (a window dragged through many heights at 5K: every height is a new scale)
    for ch in "0123456789":
        glyphs._cell(bank_font, ch, float(sc), "bilinear", 3, 5)
check(f"glyph cells: the drawing bank stays within its memory cap through many scales ({glyphs._bank_bytes >> 20} MB held)",
      glyphs._bank_bytes <= glyphs.BANK_MAX_BYTES + (1 << 20) and glyphs._bank_bytes == sum(v[0].nbytes + v[1].nbytes + v[2].nbytes for v in glyphs._bank.values()))
check("mesos v2: nothing on the picture is not_found", mesos_v2.read_mesos_v2(np.full((200, 300, 3), 128, np.uint8), [1.0])[0].why == "not_found")
inv = mesos_v2.read_mesos_v2(255 - on_screen(game_box(24697), 1.0), [1.0])[0]
check(f"mesos v2: the Windows inverted-colours filter reads nothing, never a wrong number (decision #9): {inv.value} / {inv.why}", inv.value is None)
sliver = []
for hw in ((1, 1), (1, 200), (200, 1), (3, 3), (14, 143)):          # (a window squeezed to a sliver: no box, never an error)
    try:
        sliver.append(mesos_v2.read_mesos_v2(np.zeros(hw + (3,), np.uint8), [1.0, 1.5, 2.0])[0].why)
    except Exception as e:
        sliver.append(repr(e)[:60])
check(f"mesos v2: a picture a pixel or a few wide or tall is not_found, never an error ({sliver})", set(sliver) == {"not_found"})
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

# ---- the party list's bar frame (party v3, ticket P02): drawn at any scale and sub-pixel position, over made-up backgrounds
import party_shapes as psh  # noqa: E402

SH = psh.Shape.load()
BAR_W, BAR_H = SH.w, SH.h


def synth_party_bar(scale, kernel, tx, ty, shape, bg, fill=(40, 40, 200), seed=3, alien=None):
    """A party bar as the game would show it: the 67 x 11 design-size bar (black frame, white rim, `fill` inside, the
    four corner pixels left to the background) over a design-size background (`bg`: a grey level, or "busy" = dark
    noise that touches the frame), scaled by the game's way (kernel) with its top left design pixel at (tx, ty).
    alien=(w, h): a world HP bar look-alike of that size instead (the same frame, rim and dark inside)."""
    m = 5
    rng = np.random.default_rng(seed)
    w, h = alien if alien else (BAR_W, BAR_H)
    cw, ch = w + 2 * m, h + 2 * m
    canvas = (rng.integers(0, 50, (ch, cw, 1)).repeat(3, axis=2).astype(np.float32) if bg == "busy"
              else np.full((ch, cw, 3), float(bg), np.float32))
    if alien:
        canvas[m:m + h, m:m + w] = 30.0                                    # dark inside
        canvas[m:m + h, m] = canvas[m:m + h, m + w - 1] = 0.0
        canvas[m, m:m + w] = canvas[m + h - 1, m:m + w] = 0.0
        canvas[m + 1:m + h - 1, m + 1] = canvas[m + 1:m + h - 1, m + w - 2] = 255.0
        canvas[m + 1, m + 1:m + w - 1] = canvas[m + h - 2, m + 1:m + w - 1] = 255.0
    else:
        for r, row in enumerate(SH_FRAME):
            for c, ch_ in enumerate(row):
                if ch_ == "#":
                    canvas[m + r, m + c] = 0.0
                elif ch_ == "o":
                    canvas[m + r, m + c] = 255.0
                elif (r, c) not in ((0, 0), (0, BAR_W - 1), (BAR_H - 1, 0), (BAR_H - 1, BAR_W - 1)):
                    canvas[m + r, m + c] = fill
    wy = psh.weights(shape[0], scale, ty - m * scale, ch, kernel)
    wx = psh.weights(shape[1], scale, tx - m * scale, cw, kernel)
    return np.clip(np.stack([wy @ canvas[:, :, k] @ wx.T for k in range(3)], axis=2), 0, 255).astype(np.uint8)


SH_FRAME = json.loads(psh.ASSET.read_text(encoding="utf-8"))["frame"]
check("party shapes: the frame is 67 x 11 and the same mirrored left to right and top to bottom",
      (BAR_W, BAR_H) == (67, 11) and SH_FRAME == [r[::-1] for r in SH_FRAME] and SH_FRAME == SH_FRAME[::-1])
check("party shapes: a one-pixel black frame, a white rim just inside it, and the fill left as don't care",
      set(SH_FRAME[0]) == {"#", "."} and set(SH_FRAME[1]) == {"#", "o"} and set(SH_FRAME[5]) == {"#", "o", "."}
      and SH_FRAME[5][0] == "#" and SH_FRAME[5][1] == "o" and SH_FRAME[5][2] == ".")
check("party shapes: the numbers the reader needs (row pitch 17; the panel's border is five design pixels above the first bar)",
      SH.pitch == 17 and SH.panel["top"] == -5 and SH.panel["bottom"] == 14 and SH.panel["right"] == 72)

bad = []
for scale in (1.0, 1.40625, 1.5625, 1.875, 2.0, 2.0833, 2.8125, 3.75):
    for kernel in psh.kernels_for(scale):
        for bg in (200, "busy"):
            for qx, qy in ((0, 0), (3, 5), (7, 1)):
                tx, ty = 12 + qx / 8, 12 + qy / 8
                shape = (int(np.ceil(BAR_H * scale)) + 30, int(np.ceil(BAR_W * scale)) + 30)
                img = synth_party_bar(scale, kernel, tx, ty, shape, bg)
                got = psh.agree(img, tx, ty, scale, kernel)
                if not got or got[1] < 100 or got[0] != got[1]:
                    bad.append((scale, kernel, bg, qx, qy, got))
check(f"party shapes: a bar drawn at 1x to 3.75x, at any phase, whole step both ways, over a light or a dark busy background "
      f"touching the frame, is matched on every trusted pixel ({bad[:2] or 'all'})", not bad)

bad = []
for scale in (1.0, 1.40625, 1.875, 2.0, 2.8125):
    kernel = psh.kernels_for(scale)[0]
    shape = (int(np.ceil(BAR_H * scale)) + 30, int(np.ceil(BAR_W * scale)) + 30)
    img = synth_party_bar(scale, kernel, 12.0, 12.0, shape, "busy")
    unit = 1.0 if kernel == "point" else 0.25 * scale        # the least shift that must show: a screen pixel, or a quarter design pixel
    for dx, dy, limit in ((unit, 0, 0.97), (-unit, 0, 0.97), (0, unit, 0.7), (0, -unit, 0.7), (0, 2 * unit, 0.7)):
        off = psh.agree(img, 12.0 + dx, 12.0 + dy, scale, kernel)
        if off and off[0] / off[1] > limit:
            bad.append((scale, round(dx, 2), round(dy, 2), round(off[0] / off[1], 2)))
check(f"party shapes: a drawing a screen pixel (smoothed: a quarter design pixel) off the real bar is not a full match: "
      f"sideways it loses the ends, up or down the whole top and bottom ({bad[:3] or 'none'})", not bad)

bad = []
for scale in (1.0, 1.40625, 2.0, 2.8125):
    kernel = psh.kernels_for(scale)[0]
    alien = (76, 14)                                                       # a world HP bar: wider and taller, same frame
    shape = (int(np.ceil(16 * scale)) + 16, int(np.ceil(78 * scale)) + 16)
    img = synth_party_bar(scale, kernel, 7.0, 6.0, shape, "busy", alien=alien)
    best = 0.0
    for dy in np.arange(-3, 3.01, 0.25):
        for dx in np.arange(-3, 3.01, 0.25):
            got = psh.agree(img, 7.0 + dx * scale, 6.0 + dy * scale, scale, kernel)
            if got and got[1]:
                best = max(best, got[0] / got[1])
    if best > 0.8:
        bad.append((scale, round(best, 2)))
check(f"party shapes: a world HP bar (same frame, rim and dark inside, 76 x 14) is far from matching at any spot ({bad or 'all below 0.8'})",
      not bad)

fr1, _ = psh.frame(1.0, "point", 0, 0)
fr2, _ = psh.frame(2.0, "point", 0, 0)
check("party shapes: at a whole step block for block, every trusted pixel is pure black or pure white; at 1x there are 296",
      len(fr1.ys) == 296 and set(np.unique(fr1.expect)) == {0.0, 255.0} and set(np.unique(fr2.expect)) == {0.0, 255.0})
fb, _ = psh.frame(1.40625, "bilinear", 3, 5)
check("party shapes: smoothed, the pixels where frame meets rim keep an exact grey and the blurred outer ring is left out",
      (fb.expect > 1).any() and (fb.expect < 254).any() and len(fb.ys) > 150)
check("party shapes: the same scale and phase gives the same drawing from the cache",
      psh.frame(1.40625, "bilinear", 3, 5)[0] is fb)
known, expect = psh.draw(1.40625, 6.5, 6.5, (30, 100), "bilinear")
check("party shapes: draw() shows 0..1 of every pixel as frame or rim, grey values in 0..255",
      known.min() >= 0 and known.max() <= 1.0001 and expect.min() >= 0 and expect.max() <= 255.001)
check("party shapes: a drawing that would leave the picture is None, never an error",
      psh.agree(np.zeros((20, 20, 3), np.uint8), 2.0, 2.0, 1.0, "point") is None
      and psh.agree(np.zeros((100, 200, 3), np.uint8), 0.0, 0.0, 1.0, "point") is None)
edges = psh.panel_edges(100.0, 50.0, 50.0 + 4 * 17 * 1.5, 1.5)
check("party shapes: panel_edges puts the border above the first row, below the last and right of the column, at the scale",
      near(edges["top"], 50.0 - 5 * 1.5) and near(edges["bottom"], 50.0 + 4 * 17 * 1.5 + 14 * 1.5) and near(edges["right"], 100.0 + 72 * 1.5))

# ---- reader v3 for the party size (party v3, ticket P03): whole lists drawn the game's way, over made-up backgrounds
import party_v3 as p3  # noqa: E402

P3_SCALES = ((1.0, "point"), (1.40625, "bilinear"), (2.0, "point"), (2.8125, "bilinear"), (1.875, "bilinear"))
P3_CW, P3_CH = 290, 170


def fake_party(scale, kernel, n, bg=200, ph=(0.37, 0.61), x0=12, y0=30, x_left=98, covers=(), grey=(), alien=None,
               extra_bars=(), seed=5):
    """A party list as the game shows it, scaled the game's way (kernel) with the design area's top left `ph` screen
    pixels in. n rows, 17 design pixels apart from (x0 + x_left, y0); the panel behind them see-through with its
    two border lines (the inner lighter than the outer); a bullet and black 'name' strokes (pure 0) in every row;
    the bar frame from the asset, red inside (grey in the `grey` rows). bg: a grey level, or "busy" = dark noise and
    black blocks behind the list. alien=(x, y): a world HP bar look-alike (76 x 14). extra_bars: more real bars at
    (x, y). covers: opaque (x0, y0, x1, y1, grey) rectangles drawn over it all, in design pixels."""
    rng = np.random.default_rng(seed)
    cv = np.zeros((P3_CH, P3_CW, 3), np.float32)
    ox = x0 + x_left
    ys = [y0 + 17 * k for k in range(n)]
    if bg == "busy":
        cv[:] = rng.integers(0, 45, (P3_CH, P3_CW, 1))
        for _ in range(8):
            a, b = int(rng.integers(0, P3_CW - 60)), int(rng.integers(0, P3_CH - 20))
            cv[b:b + int(rng.integers(6, 20)), a:a + int(rng.integers(20, 60))] = 0.0
        if n:
            cv[ys[0] - 4:ys[-1] + 14, ox - 6:ox + 74] = 0.0                # dark right behind the bars, touching the frames
    else:
        cv[:] = float(bg)
    if n:
        top, bot, right = ys[0] - 5, ys[-1] + 14, ox + 72
        cv[top - 1:bot + 2, x0 - 1:right + 2] = 150.0                      # the outer line
        cv[top:bot + 1, x0:right + 1] = 235.0                              # the inner line (the inside is drawn over it)
        back = cv[top + 1:bot, x0 + 1:right]
        back = np.zeros_like(back) + (rng.integers(0, 45, (bot - top - 1, right - x0 - 1, 1)) if bg == "busy" else float(bg))
        cv[top + 1:bot, x0 + 1:right] = 0.62 * back + 0.38 * 205.0        # see-through, light grey at 38%
        for y in ys:
            cv[y + 3:y + 8, x0 + 7:x0 + 12] = (0, 150, 255)                 # the bullet
            for x in range(x0 + 16, ox - 8, 4):                              # the name: black strokes, pure 0
                cv[y + 2:y + 9, x:x + 2 + int(rng.integers(0, 2))] = 0.0

    def bar(x, y, fill):
        for r, row in enumerate(SH_FRAME):
            for c, ch_ in enumerate(row):
                if ch_ == "#":
                    cv[y + r, x + c] = 0.0
                elif ch_ == "o":
                    cv[y + r, x + c] = 255.0
                elif (r, c) not in ((0, 0), (0, BAR_W - 1), (BAR_H - 1, 0), (BAR_H - 1, BAR_W - 1)):
                    cv[y + r, x + c] = fill
    for k, y in enumerate(ys):
        bar(ox, y, (84, 76, 86) if k in grey else (0, 0, 200))
    for x, y in extra_bars:
        bar(x, y, (0, 0, 200))
    if alien:
        x, y = alien
        cv[y:y + 14, x:x + 76] = 30.0
        cv[y:y + 14, x] = cv[y:y + 14, x + 75] = 0.0
        cv[y, x:x + 76] = cv[y + 13, x:x + 76] = 0.0
        cv[y + 1:y + 13, x + 1] = cv[y + 1:y + 13, x + 74] = 255.0
        cv[y + 1, x + 1:x + 75] = cv[y + 12, x + 1:x + 75] = 255.0
    for a, b, c, d, g in covers:
        cv[b:d, a:c] = float(g)
    shape = (int(P3_CH * scale) + 2, int(P3_CW * scale) + 2)
    wy = psh.weights(shape[0], scale, ph[1], P3_CH, kernel)
    wx = psh.weights(shape[1], scale, ph[0], P3_CW, kernel)
    img = np.clip(np.stack([wy @ cv[:, :, k] @ wx.T for k in range(3)], axis=2), 0, 255).astype(np.uint8)
    return img, dict(ox=ox, ys=ys, scale=scale)


def read3(img, scale, last=None):
    return p3.read_party_v3(img, ("default", scale), last)


check("party v3: the answer's fields", p3.PartyLook._fields == ("count", "why", "scale", "box", "gone"))
rng3 = np.random.default_rng(11)
good = total = 0
bad3 = []
for scale, kernel in P3_SCALES:
    for bg in (200, 235, 20, "busy"):
        for n in (2, 3, 4, 5, 6):
            ph = (float(rng3.random()), float(rng3.random())) if kernel == "bilinear" else (float(rng3.integers(0, 2)), float(rng3.integers(0, 2)))
            img, info = fake_party(scale, kernel, n, bg, ph=ph, grey=(n - 1,) if n % 2 else ())
            look = read3(img, scale)
            total += 1
            ok = look.count == n and look.why == "ok" and abs(look.scale - scale) < 0.01 and look.gone == 0
            good += ok
            if not ok:
                bad3.append((scale, bg, n, look.count, look.why))
check(f"party v3: lists of 2 to 6 rows are counted exactly at 1x, 1.406x, 1.875x, 2x and 2.8125x, over light, dark and busy "
      f"backgrounds that touch the frames, in any sub-pixel position, grey rows too ({good}/{total}) {bad3[:3] or ''}", good == total)
img, info = fake_party(1.40625, "bilinear", 4)
keep = img.copy()
look = read3(img, 1.40625)
check("party v3: the picture is not changed and the same picture gives the same answer", (img == keep).all() and look == read3(img, 1.40625))
bx, by, bw, bh = look.box
check("party v3: the box is where the list is (its bars and panel lie inside it)",
      bx <= info["ox"] * 1.40625 and bx + bw >= (info["ox"] + 72) * 1.40625 and by <= info["ys"][0] * 1.40625 and by + bh >= (info["ys"][-1] + 14) * 1.40625)
for scale, kernel in ((1.0, "point"), (2.0, "point")):
    img, _ = fake_party(scale, kernel, 5)
    look = p3.read_party_v3(img)                                              # no hint at all: the scales of the window are tried
    check(f"party v3: no hint needed at {scale}x (the window's own scales are tried)", look.count == 5 and look.why == "ok")


# not a whole list: never a number
def none_cases():
    out = {}
    for scale, kernel in ((1.0, "point"), (1.40625, "bilinear"), (2.8125, "bilinear")):
        ox = 12 + 98
        ys = [30 + 17 * k for k in range(5)]

        def mk(n=5, **kw):
            return fake_party(scale, kernel, n, **kw)[0]
        out[scale] = {
            "first row covered": mk(covers=((ox - 4, ys[0] - 3, ox + 76, ys[0] + 14, 120),)),
            "last row covered": mk(covers=((ox - 4, ys[4] - 3, ox + 76, ys[4] + 22, 120),)),
            "a middle row covered": mk(covers=((ox - 4, ys[2] - 3, ox + 76, ys[2] + 14, 120),)),
            "the bottom rows covered": mk(covers=((ox - 20, ys[3] - 6, ox + 80, ys[4] + 30, 90),)),
            "a black block (the overlay's window) between the rows": mk(covers=((ox + 8, ys[1] + 12, ox + 46, ys[1] + 16, 0),)),
            "a black block across the bars": mk(covers=((ox - 4, ys[2] - 3, ox + 76, ys[2] + 14, 0),)),
            "seven rows": mk(n=7),
            "a lone bar": mk(n=1),
            "a bar standing apart": mk(extra_bars=((20, 140),)),
        }
    return out


bad = []
for scale, cases in none_cases().items():
    for name, img in cases.items():
        look = read3(img, scale)
        if look.count is not None or look.why != "unclear":
            bad.append((scale, name, look.count, look.why))
check(f"party v3: a row covered (first, last or middle), a pure-black block over the column, seven rows, a lone bar, a stray bar: "
      f"None, 'unclear', never a number ({bad or 'all'})", not bad)
bad = []
for scale, kernel in ((1.40625, "bilinear"), (1.875, "bilinear"), (2.0, "point"), (2.8125, "bilinear"), (3.75, "bilinear")):
    for seed in range(12):
        img, info = fake_party(scale, kernel, 5, seed=seed)
        g = np.random.default_rng(seed)
        y = int((info["ys"][3] - 3) * scale)                     # rows 4 and 5 hidden by something opaque and busy (screen noise)
        x0, x1 = int((info["ox"] - 30) * scale), int((info["ox"] + 80) * scale)
        img[y:, x0:x1] = g.integers(0, 256, (img.shape[0] - y, x1 - x0, 3))
        look = read3(img, scale)
        if look.count is not None:
            bad.append((scale, seed, look.count))
check(f"party v3: the bottom rows hidden by something busy (pixel noise) at 1.406x-3.75x: never a number, not even the rows above "
      f"({bad or 'all'})", not bad)
bad = []
for scale, kernel in ((1.0, "point"), (1.40625, "bilinear"), (2.8125, "bilinear")):
    img, info = fake_party(scale, kernel, 5)
    ox, ys = info["ox"], info["ys"]
    cuts = {"the bars cut by the picture's right edge": img[:, :int((ox + 40) * scale)],
            "the bars ending just short of the picture's right edge": img[:, :int((ox + 69) * scale)],
            "the list cut by the picture's bottom edge": img[:int((ys[3] + 5) * scale)]}
    party = p3.PartyLook(5, "ok", scale, (0, 0, 10, 10), 0)
    for name, cut in cuts.items():
        alone, held = read3(np.ascontiguousarray(cut), scale), read3(np.ascontiguousarray(cut), scale, party)
        if alone.count is not None or (held.count, held.why) != (5, "held"):
            bad.append((scale, name, alone, held))
check(f"party v3: a list partly off the picture (the owner can drag it there) is unclear, or the party count held; never "
      f"alone or fewer ({bad or 'all'})", not bad)
bad = []
for scale, kernel in P3_SCALES[:4]:
    img, info = fake_party(scale, kernel, 4, covers=((0, 100, 8, 130, 0),))   # a black block far from the list: no matter
    if read3(img, scale).count != 4:
        bad.append(("far black block", scale))
    img, info = fake_party(scale, kernel, 4, bg="busy", alien=(12 + 98 + 80, 30 + 17))   # a world HP bar beside the list, level with row 2
    if read3(img, scale).count != 4:
        bad.append(("world bar beside", scale))
    img, info = fake_party(scale, kernel, 4, alien=(150, 140))
    if read3(img, scale).count != 4:
        bad.append(("world bar below", scale))
    img, info = fake_party(scale, kernel, 0, bg="busy", alien=(60, 60))
    look = read3(img, scale)
    if look.count != 1 or look.why != "ok":
        bad.append(("world bar alone", scale, look.count, look.why))
check(f"party v3: a black block far from the list and a world HP bar (same frame and rim, another shape) change nothing ({bad or 'all'})", not bad)
bad = []
for scale, kernel in P3_SCALES:
    for bg in (200, "busy", 20):
        look = read3(fake_party(scale, kernel, 0, bg)[0], scale)
        if look != p3.PartyLook(1, "ok", None, None, 0):
            bad.append((scale, bg, look))
check(f"party v3: no list is count 1, 'ok', no box ({bad or 'all'})", not bad)
img, _ = fake_party(1.0, "point", 3, x0=12, y0=30)
a = read3(img, 1.0)
img2, _ = fake_party(1.0, "point", 5, x0=61, y0=17)
b = read3(img2, 1.0, a)
check("party v3: a list that moved and grew between two looks is found again (the last box is looked at first, then the whole window)",
      a.count == 3 and b.count == 5 and b.gone == 0)
img3, _ = fake_party(1.0, "point", 5, x0=12, y0=30)
c = read3(img3, 1.0, b)
check("party v3: and moved back", c.count == 5)

# sizes the game does not offer but a fullscreen desktop can give (1.5625 = 1920x1200, 2.083 = 2560x1600, 3.0, 3.75 = 5K)
bad = []
for scale in (1.5625, 2.083, 3.0, 3.75):
    for bg in (200, "busy"):
        for n in (2, 4, 6):
            img, _ = fake_party(scale, "bilinear", n, bg, ph=(0.31, 0.77))
            look = read3(img, scale)
            if look.count != n:
                bad.append((scale, bg, n, look.count))
check(f"party v3: lists are counted exactly at 1.5625x, 2.083x, 3x and 3.75x too ({bad or 'all'})", not bad)
# a size just above 1x (a 1440 x 900 or 1600 x 900 desktop): the frame is under 1.35 pixels thick, a drawn bar says little.
# It may say "unclear" there, never another number, and never "no list" for a picture that has one.
bad = []
for scale in (1.04, 1.1, 1.17, 1.25, 1.3):
    for bg in (200, 120, "busy"):
        for n in (0, 2, 4, 6):
            img, _ = fake_party(scale, "bilinear", n, bg, ph=(0.55, 0.2))
            look = read3(img, scale)
            if look.count not in (n, None) and not (n == 0 and look.count == 1):
                bad.append((scale, bg, n, look.count))
            if n == 0 and look.count == 1:
                bad.append((scale, bg, "no list at a weak size is claimed", look.count))
check(f"party v3: just above 1x a picture is read right or called unclear, never a wrong number, and 'no list' is not claimed ({bad or 'all'})", not bad)

# holding a party count: the state travels in `last`
party = p3.PartyLook(4, "ok", 1.0, (10, 10, 190, 100), 0)
nolist, _ = fake_party(1.0, "point", 0)
covered, _ = fake_party(1.0, "point", 4, covers=((106, 78, 186, 103, 120),))       # the last row (and the panel line under it) hidden
seq, last = [], party
for i in range(p3.HOLD_LOOKS + 1):
    last = read3(nolist, 1.0, last)
    seq.append((last.count, last.why, last.gone))
check(f"party v3: after a party of 4, no list at all keeps the 4 ('held') for {p3.HOLD_LOOKS - 1} looks, then you are alone: {seq}",
      seq[:p3.HOLD_LOOKS - 1] == [(4, "held", i + 1) for i in range(p3.HOLD_LOOKS - 1)] and seq[p3.HOLD_LOOKS - 1:] == [(1, "ok", 0)] * 2)
last, seq = party, []
for i in range(3 * p3.HOLD_LOOKS):
    last = read3(covered, 1.0, last)
    seq.append((last.count, last.why))
check("party v3: a list that can't be read (covered) keeps the count for as long as it lasts, however many looks",
      set(seq) == {(4, "held")} and last.gone == 0)
real, _ = fake_party(1.0, "point", 5)
after = read3(real, 1.0, read3(nolist, 1.0, party))
check("party v3: a list that comes back is read afresh and the wait starts over", after.count == 5 and after.why == "ok" and after.gone == 0)
once = read3(covered, 1.0, None)
check("party v3: unreadable with no party count before: None, 'unclear', not a guess", once.count is None and once.why == "unclear")
check("party v3: after a count of 1 (alone) no list stays 1",
      read3(nolist, 1.0, p3.PartyLook(1, "ok", None, None, 0)) == p3.PartyLook(1, "ok", None, None, 0))

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
