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
check("SlotRead fields", potion_bar.SlotRead._fields == ("index", "rect", "style", "item", "kind", "count"))
check("PotionLook fields", potion_bar.PotionLook._fields == ("seen", "layout", "mode", "scale", "slots"))
check("Hint fields", potion_bar.Hint._fields == ("last", "mode_scale"))
check("layouts: keyboard and the two controller D-pad modes",
      potion_bar.LAYOUTS == ("keyboard", "controller-standard", "controller-cross"))
check("controller layouts are told apart from keyboard", [potion_bar.is_controller(x) for x in potion_bar.LAYOUTS]
      == [False, True, True] and not potion_bar.is_controller(None))
check("not built yet: the reader says 'not seen', never a guess",
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

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
