"""Tests for report.py: what a bug report says, and how it spots the usual causes.

Run from the project folder:  py -3 tests/test_report.py   (no screen needed)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import report  # noqa: E402

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
      says("bigger than every screen", game={"rect": (0, 0, 3840, 2160)}, monitors=[(2560, 1440)]))
check("a 4K game on a 4K screen is fine", not says("bigger than", game={"rect": (0, 0, 3840, 2160)}, monitors=[(3840, 2160)]))
check("a 2560x1440 game on a portrait 2160x3840 screen beside a 2560x1440 one is fine (it fits the second)",
      not says("bigger than", game={"rect": (0, 0, 2560, 1440)}, monitors=[(2160, 3840), (2560, 1440)]))
check("a game that fits only the portrait screen is fine too",
      not says("bigger than", game={"rect": (0, 0, 1080, 1920)}, monitors=[(2160, 3840), (2560, 1440)]))
check("a game wide for one screen and tall for the other fits neither: pointed out",
      says("bigger than every screen", game={"rect": (0, 0, 2560, 2000)}, monitors=[(2160, 3840), (2560, 1440)]))
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

# the potion section
POT = {"on": True, "seen": True, "unseen_for": None, "layout": "keyboard", "mode": "ideal", "scale": 1.5,
       "cost_text": "1,200", "own_prices": ["Lemon"],
       "slots": [{"index": 4, "item": "Lemon", "kind": "consumable", "count": 236, "dist": 0.12, "dropped": False},
                 {"index": 5, "item": "Blue Potion", "kind": "consumable", "count": None, "dist": 0.47, "dropped": True},
                 {"index": 6, "item": None, "kind": "consumable", "count": None, "dist": 0.2, "dropped": False},
                 {"index": 7, "item": None, "kind": "skill", "count": None, "dist": 0.1, "dropped": False},
                 {"index": 8, "item": None, "kind": "empty", "count": None, "dist": 1.0, "dropped": False}]}
pot = report.details(facts(potions=POT))
check("the details have a Potions part", "== Potions ==" in pot and "Bar seen: yes" in pot and "keyboard, UI size ideal" in pot)
check("...with the cost row as shown and each consumable slot's count and icon distance",
      "Cost row shows: 1,200" in pot and "Slot 4: Lemon, count 236, icon distance 0.12" in pot)
check("...and why a count was dropped, or just not read",
      "Slot 5: Blue Potion, count dropped (icon too far from the item), icon distance 0.47" in pot
      and "Slot 6: unknown item, count not read" in pot)
check("...and the other slots and the shipped items with an own price",
      "empty 1, skill 1" in pot and "Shipped items with your own price: Lemon" in pot)
check("the potion part comes before the timeline and the errors",
      pot.index("== Potions ==") < pot.index("== What happened") < pot.index("== errors.log"))
unseen = report.details(facts(potions={**POT, "seen": False, "unseen_for": 125}))
check("a bar not seen says since when", "Bar seen: no, not seen for 2:05" in unseen)
check("potions off in Settings is said", "Potions: off in Settings" in report.details(facts(potions={"on": False})))
check("the short summary gets one potion line",
      "- Potions: bar seen, keyboard, 1 of 3 counts read" in report.summary(facts(potions=POT))
      and report.summary(facts(potions=POT)).count("Potions") == 1)

# ---- Send report: the scrubber, the typed message, the body
import json  # noqa: E402

NAMES = ["Bob", "BOB-PC", "bsmith"]
nasty = {
    "a Windows path with the user name": (r"Can't open C:\Users\Bob\AppData\Local\x\eng.traineddata now", "eng.traineddata"),
    "OneDrive with a company name and spaces": (r"saved C:\Users\Bob\OneDrive - Contoso Ltd\Desktop\Maple EXP\log.json ok", "log.json"),
    "a path outside the home folder": (r"tessdata at D:\Games\tools\tess\tessdata missing", "tessdata"),
    "forward slashes on Windows": ("opened C:/Users/Bob/stuff/a.txt", "a.txt"),
    "a UNC path": (r"read \\FILESRV\home\bsmith\notes.txt failed", "notes.txt"),
    "a macOS home path": ("log in /Users/bsmith/Library/Application Support/Maple/x.json bad", "x.json"),
    "a Linux home path": ("see /home/bsmith/.config/maple/c.json", "c.json"),
    "a macOS path outside the home folder": ("running /Applications/Maple.app/Contents/MacOS/maple here", "maple"),
}
for name, (text, kept) in nasty.items():
    out = report.scrub(text, NAMES)
    leaks = ["Bob", "bsmith", "Users", "Contoso", "OneDrive", "FILESRV", "Applications", "Library", "Games", "home"]
    check(f"scrub: {name} is cut to its last part", kept in out and not any(x in out for x in leaks))
out = report.scrub("Error on BOB-PC for user bob: TesseractError('C:\\Users\\Bob\\x\\y.dll')", NAMES)
check("scrub: the computer name and user name go, in any case, even inside an exception message",
      "BOB-PC" not in out and "bob" not in out.lower() and "y.dll" in out)
plain = "EXP bar not found 3 times. Status: Tracking | Last read: 3,060 [1.04%] on 10/24/2026, see https://mcwo.app/x and/or ask"
check("scrub: ordinary text, numbers, dates, links and 'and/or' are kept", report.scrub(plain, NAMES) == plain)
check("scrub: a short name (under 3 letters) isn't hunted through the text", report.scrub("go to the bo", ["bo"]) == "go to the bo")
check("scrub: the text is capped at 12,000 characters", len(report.scrub("x" * 20000, NAMES)) == 12000)
check("scrub: a name inside another word is left alone", report.scrub("Bobcat Bob", ["Bob"]) == "Bobcat <you>")

check("a message must have words", report.message_problem("   ") and report.message_problem("") )
check("a message of 2,000 characters is fine, 2,001 is not", report.message_problem("x" * 2000) is None and report.message_problem("x" * 2001))
check("links are turned down, like the site does", all(report.message_problem(t) for t in
      ("see http://a.b", "www.x.org", "join discord.gg/abc", "go to evil.com/x", "HTTPS://X")))
check("a plain sentence is fine", report.message_problem("It stopped reading after I changed maps.") is None)

contract = json.loads((Path(__file__).parent / "health_contract_v2.json").read_text(encoding="utf-8"))
for case in contract["report_valid"]:
    b = case["body"]
    body = report.report_body(b["id"], b["version"], "  " + b["message"] + " \n", b["details"])
    check(f"report body: the contract's valid '{case['name']}' is built exactly",
          body == b and list(body) == ["id", "version", "message", "details"] and report.message_problem(b["message"]) is None)
base = contract["report_valid"][0]["body"]
for case in contract["report_invalid"]:
    change = {k: ("x" * int(v[5:]) if isinstance(v, str) and v.startswith("LONG:") else v) for k, v in case["change"].items()}
    field = case["field"]
    if field == "message":
        ok = report.message_problem(change["message"]) is not None
    elif field == "details":
        ok = len(report.report_body(base["id"], base["version"], base["message"], change["details"])["details"]) == 12000
    else:
        ok = True                       # id and extra fields can't come from the dialog (the body has exactly four fields)
    check(f"report body: the contract's invalid '{field}' ({list(change.values())[0] if len(str(change)) < 60 else 'long'}) can't reach the site", ok)

# ---- A05-1: Python writes Windows paths in error messages with doubled backslashes
for name, text in {
    "a FileNotFoundError message": repr(r"C:\Users\Bob\OneDrive\Desktop\Maple EXP\nope.json"),
    "a path inside a list repr": repr([r"C:\Users\Bob\x.py"]),
    "a UNC path in a repr": repr(r"\\FILESRV\home\bsmith\notes.txt"),
    "an exception message holding such a path": "[Errno 13] Permission denied: " + repr(r"C:\Users\Bob\AppData\Roaming\MapleEXP\log.json"),
}.items():
    out = report.scrub(text, NAMES)
    check(f"scrub: {name} keeps no folder or user name",
          not any(x in out for x in ("Bob", "Users", "OneDrive", "Desktop", "FILESRV", "bsmith", "AppData", "Roaming")) and "\\\\" not in out)
check("scrub: a doubled-backslash path still ends with its file name", report.scrub(repr(r"C:\Users\Bob\x\y.dll"), NAMES).strip("'") == "y.dll")
check("scrub: limit=None keeps the whole text", len(report.scrub("x" * 20000, NAMES, limit=None)) == 20000)

# ---- A05-2: what the site counts (UTF-16 units for the text, bytes for the whole body)
WORDS = {"emoji": "\U0001F600", "Chinese": "\u4e2d", "backslashes": "\\", "quotes": '"', "newlines": "\n", "plain": "x"}
for label, ch in WORDS.items():
    for msg in ("It stopped.", "It stopped. " + ch * 900):
        body = report.report_body("0123456789abcdef", "0.9.14-beta", msg, ch * 12000)
        size = len(report.encode_body(body))
        check(f"Send report: {label} details (message {len(msg)} chars) make a body of {size} bytes, within the site's 16,384",
              size <= 16384 and body["message"] == msg.strip(report.JS_SPACE) and report.message_problem(msg) is None)
        check(f"Send report: {label} details are cut to what fits, never emptied ({len(body['details'])} chars kept)",
              len(body["details"]) > 1000)
check("Send report: a message of 1,500 emoji (3,000 units on the site) is turned down", report.message_problem("\U0001F600" * 1500) is not None)
check("Send report: 1,000 emoji (2,000 units) is fine", report.message_problem("\U0001F600" * 1000) is None)
check("Send report: a message of only a zero-width no-break space is empty, like the site says",
      report.message_problem("\ufeff") is not None and report.message_problem(" \ufeff\u00a0 ") is not None)
check("Send report: a message the site would turn down for its size is turned down here first",
      report.message_problem("\x01" * 2000) is not None)
body = report.report_body("0123456789abcdef", "0.9.14-beta", "\ufeff It stopped.\u3000", "d")
check("Send report: the message is trimmed the way the site trims it", body["message"] == "It stopped.")
body = report.report_body("0123456789abcdef", "0.9.14-beta", "ok", "\U0001F600" * 7000)
check("Send report: details are never over 12,000 UTF-16 units", len(body["details"].encode("utf-16-le")) // 2 <= 12000)
body = report.report_body("0123456789abcdef", "0.9.14-beta", "ok", "e\u0301" * 5000)
check("Send report: the body is real UTF-8 that decodes back to the same text",
      json.loads(report.encode_body(body).decode("utf-8")) == body)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
