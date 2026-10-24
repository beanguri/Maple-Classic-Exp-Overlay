"""Tests for report.py: what a bug report says, and how it spots the usual causes.

Run from the project folder:  py -3 tests/test_report.py   (no screen needed)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from maple_overlay.logs import report  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def facts(**over):
    """A healthy 1080p game, with anything overridden."""
    f = {"version": "0.9.9-beta", "frozen": True, "system": "Windows 10.0.26200", "monitors": [(1920, 1080)],
         "dpi": 96, "dpi_awareness": "per-monitor", "uptime_s": 120, "cpu_pct": 1.5,
         "tesseract": {"found": True, "version": "tesseract v5.5.0", "where": "included"},
         "game": {"mode": "window", "found": True, "rect": (0, 0, 1920, 1080), "gh": 11, "fullscreen": True},
         "overlay": {"rect": (60, 60, 360, 600), "covering": []},
         "reader": {"exp_box": (700, 1000, 200, 30), "exp_misses": 0, "map_box": (10, 10, 200, 60), "mesos_box": (30, 200, 220, 20),
                    "mesos_fails": 0, "two_boxes": False, "mesos_learned": True, "party_area": None, "job_why": "",
                    "level_unknown": False},
         "health": {"alive": True, "polls": 80, "errors": 0, "average_ms": 20, "max_ms": 60, "last_ms": 18, "age": 1.0},
         "rejected": 0, "status": "Tracking", "last_exp": "3,060 [1.04%]", "level": 40, "job": "Bandit", "map": "Henesys",
         "party": "Solo", "mesos": 185938, "tracking": True, "tracker": "paused: no, gained 0 EXP in 0:00:00",
         "settings": {"gap_seconds": 15}, "timeline": ["+0:01  Tracking"], "errors": ""}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(f.get(k), dict):
            f[k] = {**f[k], **v}
        else:
            f[k] = v
    return f


check("a healthy setup has nothing that looks wrong", report.looks_wrong(facts()) == [])


def says(text, **over):
    return any(text in w for w in report.looks_wrong(facts(**over)))


check("no Tesseract is pointed out", says("Tesseract wasn't found", tesseract={"found": False}))
check("no game window is pointed out", says("window wasn't found", game={"found": False, "rect": None}))
check("a minimized game is pointed out", says("minimized", game={"minimized": True}))
check("a game bigger than the screen (scaling) is pointed out",
      says("bigger than the biggest screen", game={"rect": (0, 0, 3840, 2160)}, monitors=[(2560, 1440)]))
check("a 4K game on a 4K screen is fine", not says("bigger than", game={"rect": (0, 0, 3840, 2160)}, monitors=[(3840, 2160)]))
check("a process unaware of scaling is pointed out", says("aware of Windows scaling", dpi_awareness="not aware"))
check("150% scaling is mentioned", says("scaling on the game's screen is 150%", dpi=144))
check("the overlay covering the EXP bar is pointed out", says("covering the EXP bar", overlay={"covering": ["EXP bar"]}))
check("the overlay covering the class name is pointed out", says("covering the class name", overlay={"covering": ["class name"]}))
check("an EXP bar that wasn't found is pointed out", says("EXP bar hasn't been found", reader={"exp_box": None}))
check("EXP readings that don't add up are pointed out", says("keep not adding up", rejected=6))
check("a second mesos box (storage) is pointed out", says("Two mesos boxes", reader={"two_boxes": True}))
check("a closed inventory is pointed out", says("inventory open", reader={"mesos_box": None}))
check("a class that couldn't be read says why",
      says("Class not read: can't find the HP bar", job=None, reader={"job_why": "can't find the HP bar next to the class name"}))
check("Select area mode says what it doesn't read", says("aren't read in it", game={"mode": "region"}))
check("a stopped reading thread is pointed out", says("has stopped", health={"alive": False}))
check("a stuck reading thread is pointed out", says("stuck or very slow", health={"age": 42}))
check("reading errors are pointed out with the last one",
      says("failed with an error (last: ValueError: x)", health={"errors": 2, "last_error": "ValueError: x"}))
check("slow looks are pointed out", says("slow", health={"average_ms": 900}))

# the history
r = report.Recent(5, start=100.0)
for i, text in enumerate(["a", "b", "b", "b", "c"]):
    r.add(text, now=100.0 + i * 5)
check("repeats are counted, not listed", r.lines() == ["+0:00  a", "+0:05  b  (x3)", "+0:20  c"])
r = report.Recent(3, start=0.0)
for i in range(6):
    r.add(f"line {i}", now=i)
check("only the last few are kept", [x.split("  ")[1] for x in r.lines()] == ["line 3", "line 4", "line 5"])
r.add("   ", now=9)
check("blank lines are ignored", len(r.items) == 3)
r.add("x" * 500, now=10)
check("a long line is cut", len(r.lines()[-1]) < 200)

# the reading thread's counters
h = report.Health()
h.finished(10.0, now=10.05)
h.finished(20.0, now=20.15)
h.failed(ValueError("boom"), now=30)
check("looks are counted and timed", h.polls == 2 and abs(h.average_ms() - 100) < 1 and abs(h.max_ms - 150) < 1)
check("a failure is kept with its kind", h.errors == 1 and h.last_error == "ValueError: boom")

# what's written
text = report.summary(facts(overlay={"covering": ["EXP bar"]}))
check("the summary has the version, the game window and what looks wrong",
      "0.9.9-beta" in text and "1920x1080" in text and "covering the EXP bar" in text and "Things that look wrong" in text)
check("the summary is short enough for a web address", len(text) < 2500)
long = facts(timeline=[f"+0:{i:02d}  something happened" for i in range(60)])
check("the summary keeps only the end of a long history", report.summary(long).count("something happened") == 12)
full = report.details(facts(errors="Traceback...\nValueError: x"))
check("the details have every part", all(x in full for x in ("== Computer ==", "== Game window ==", "== What it found ==",
                                                              "== Reading thread ==", "== Settings ==", "== errors.log")))
check("the details include the errors and the history", "ValueError: x" in full and "+0:01  Tracking" in full)
check("empty facts don't crash it", bool(report.summary({})) and bool(report.details({})) and report.looks_wrong({}) != [None])

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
