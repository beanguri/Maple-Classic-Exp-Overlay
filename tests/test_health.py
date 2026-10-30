"""Tests for the daily health report (healthlog.py and how maple_exp.py sends it). No game or screen needed.

Run from the project folder:  python tests/test_health.py
"""
import calendar
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import healthlog  # noqa: E402
import maple_exp as m  # noqa: E402
assert Path(os.environ["MAPLE_EXP_DATA"]) == m.DATA_DIR, "tests must run on their own temp data folder, never on real data"

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


contract = json.loads((Path(__file__).parent / "health_contract_v2.json").read_text(encoding="utf-8"))
TODAY = contract["today"]
full = next(c["body"] for c in contract["health_valid"] if c["name"] == "full")

# ---- the same cases the site is tested with
for case in contract["health_valid"]:
    check(f"contract: '{case['name']}' passes the overlay's own check", healthlog.bad_field(case["body"], TODAY) is None)
for case in contract["health_invalid"]:
    body = {**copy.deepcopy(full), **case["change"]}
    got = healthlog.bad_field(body, TODAY)
    check(f"contract: a bad {case['field']} ({json.dumps(case['change'])[:60]}) is refused as {case['field']}",
          got == case["field"] or (case["field"] in ("map_name",) and got == "map_name"))

# ---- the ID
cfg = {}
check("the health ID is made once, 16 lowercase hex characters", healthlog.ensure_id(cfg) and healthlog.good_id(cfg["health_id"])
      and not healthlog.ensure_id(cfg))
first = cfg["health_id"]
cfg["health_id"] = "NOT-HEX"
check("a damaged ID is replaced", healthlog.ensure_id(cfg) and healthlog.good_id(cfg["health_id"]) and cfg["health_id"] != first)

# ---- counting
NOON = float(calendar.timegm((2026, 10, 25, 12, 0, 0)))                           # noon UTC on the 25th
clock = [NOON]
assert healthlog.utc_day(clock[0]) == "2026-10-25", healthlog.utc_day(clock[0])
folder = Path(tempfile.mkdtemp())


def fresh():
    """An empty record and the clock back at noon on the 25th."""
    (folder / "health.json").unlink(missing_ok=True)
    clock[0] = NOON


def new_log():
    return healthlog.HealthLog(folder / "health.json", clock=lambda: clock[0])


log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(5120, 2880, 150), (2560, 1440, None)], "screen", "ok")
script = [("read", False), ("read", True), ("tesseract", False), ("tesseract", True), ("unclear", False),
          ("not_found", False), ("covered", False), ("no_window", False), ("read", False)]
for kind, pct in script:
    log.look(kind, pct, tries=(("map", True), ("mesos", False)), ms=20.0, tracking=1.5, game=(5120, 2880, True, 28.0))
rec = log.today()
check("the categories add up to the number of looks", sum(rec["exp"][k] for k in healthlog.EXP_KINDS) == rec["looks"] == len(script))
check("percentage-only looks are counted as a part of read + tesseract", rec["exp"]["percent_only"] == 2)
check("the other readers' tries are counted", rec["map"] == {"read": 9, "unread": 0} and rec["mesos"] == {"read": 0, "unclear": 9})
log.look("nonsense")
check("an unknown kind counts nothing", rec["looks"] == len(script))
log.look("unclear", True)
check("a percentage-only look that wasn't read doesn't count as percentage-only", rec["exp"]["percent_only"] == 2)
log.errors({"TesseractError": 2, "Bad Name!": 5, "ValueError": 0})
check("only exception type names with letters are kept", rec["errors"] == {"TesseractError": 2})

# yesterday's record, finished, is what gets sent
clock[0] += 86400
tomorrow_log = log
body = tomorrow_log.body("2026-10-25", "0123456789abcdef")
check("a finished day's body passes the check", body is not None and healthlog.bad_field(body, "2026-10-26") is None)
allowed = {"id", "version", "day", "os", "screens", "game", "capture", "tesseract", "seconds_tracking", "looks", "exp",
           "level", "map", "class", "mesos", "potions", "errors", "ms", "shadow", "ui", "shadow_mesos", "shadow_party",
           "shadow_why"}
check("the body has only contract keys", set(body) <= allowed)
check("the body holds the day's game size, tracking time and speed", body["game"] == {"w": 5120, "h": 2880, "fullscreen": True, "gh": 28.0}
      and body["seconds_tracking"] == 13 and body["ms"]["look_avg"] == 20.0 and 20 <= body["ms"]["look_p95"] <= 30
      and body["screens"] == [[5120, 2880, 150], [2560, 1440, None]])


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from strings(v)


allowed_text = {body["id"], body["version"], body["day"], body["os"], body["capture"], body["tesseract"]} | set(body["errors"]) | set(body)
for sub in ("exp", "level", "map", "class", "mesos", "potions", "ms", "game"):
    allowed_text |= set(body[sub])
check("the only text in the body is the id, version, day, os, capture, tesseract and exception type names",
      set(strings(body)) <= allowed_text)
private = [str(m.DATA_DIR), os.environ.get("USERNAME", "zzzz-no-user"), "Henesys", "Mage", "Rocky Road"]
check("nothing in the body is the data folder, the user name, a map or the class",
      not any(p.lower() in json.dumps(body).lower() for p in private if p))

# ---- saving and loading
log.save()
again = new_log()
check("the record survives a restart", again.days.get("2026-10-25", {}).get("looks") == rec["looks"])
(folder / "health.json").write_text('{"days": {"2026-10-25": {"looks": "lots", "exp": 5}}, "last_attempt": "x"}', encoding="utf-8")
damaged = new_log()
check("a damaged record is cleaned up, not crashed on", damaged.days["2026-10-25"]["looks"] == 0 and damaged.last_attempt == 0.0)
(folder / "health.json").write_text("{not json", encoding="utf-8")
check("a broken file is started again", new_log().days == {})

# ---- when it sends
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [], "screen", "ok")
log.look("read", ms=10.0, game=(1920, 1080, False, 11.0))
check("today isn't sent (it isn't finished)", log.due("0123456789abcdef") is None)
clock[0] += 86400
got = log.due("0123456789abcdef")
check("the next day the finished one is due", got is not None and got[0] == "2026-10-25" and got[1]["looks"] == 1)
log.attempting()
check("...but not again within the hour", log.due("0123456789abcdef") is None)
restarted = new_log()
check("...even after a restart", restarted.due("0123456789abcdef") is None)
clock[0] += 3601
check("...and an hour later it is", restarted.due("0123456789abcdef") is not None)
restarted.finish("2026-10-25", "later")
clock[0] += 3601
check("a network error waits until the next day", restarted.due("0123456789abcdef") is None)
clock[0] += 86400
check("...then tries again", restarted.due("0123456789abcdef") is not None)
restarted.finish("2026-10-25", "bad")
clock[0] += 3601
check("a refused body is never sent again", restarted.due("0123456789abcdef") is None and restarted.last_sent == "")
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [], "screen", "ok")
log.look("read", ms=10.0)
clock[0] += 86400 * 3
log.finish("2026-10-25", "ok")
check("an accepted day is marked sent and remembered", log.last_sent == "2026-10-25" and log.due("0123456789abcdef") is None)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [], "screen", "ok")
log.look("read")
log.days["2026-10-01"] = copy.deepcopy(log.days["2026-10-25"])
clock[0] += 86400 * 10
check("days older than 7 are dropped unsent", log.due("0123456789abcdef") is None and log.days.get("2026-10-01", {}).get("sent"))
log.save()
check("...and old days leave the file", "2026-10-01" not in json.loads((folder / "health.json").read_text(encoding="utf-8"))["days"])

# ---- never while testing
sent = []


def fake_urlopen(request, timeout=0):
    sent.append(request.full_url)
    raise AssertionError("must not be called")


real_urlopen = m.urllib.request.urlopen
m.urllib.request.urlopen = fake_urlopen
try:
    m.post_health(full)
    blocked = False
except RuntimeError:
    blocked = True
m.urllib.request.urlopen = real_urlopen
check("while testing (MAPLE_EXP_DATA is set) nothing can be sent to the real site", blocked and not sent)


class Answer:
    def __init__(self, data, code=200):
        self.data, self.code = json.dumps(data).encode(), code

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.data


def answering(data=None, code=200):
    def urlopen(request, timeout=0):
        sent.append((request.full_url, request.data))
        if code == 200:
            return Answer(data)
        raise m.urllib.error.HTTPError(request.full_url, code, "x", {}, Answer(data))
    return urlopen


os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
try:
    for code, data, wanted in ((200, {"ok": True}, ("ok", "")), (200, {"ok": True, "dup": True}, ("ok", "")),
                               (400, {"error": "exp"}, ("bad", "exp")), (413, {}, ("big", "")),
                               (429, {"retry": True}, ("later", "")), (500, {}, ("later", "")), (404, {}, ("later", ""))):
        sent.clear()
        m.urllib.request.urlopen = answering(data, code)
        check(f"the site's answer {code} {data} is understood as {wanted}", m.post_health(full) == wanted
              and sent[0][0] == m.STATS_SITE + "/api/health" and json.loads(sent[0][1]) == full)
    m.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    check("being offline means try later", m.post_health(full) == ("later", ""))
finally:
    m.urllib.request.urlopen = real_urlopen
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE")

# ---- the settings file
for key, good, bad in (("share_health", True, "yes"), ("health_id", "0123456789abcdef", "ABC")):
    m.CONFIG_PATH.write_text(json.dumps({key: good}), encoding="utf-8")
    kept = key in m.load_config()
    m.CONFIG_PATH.write_text(json.dumps({key: bad}), encoding="utf-8")
    check(f"config: {key} is kept when valid and dropped when damaged", kept and key not in m.load_config())
m.CONFIG_PATH.write_text("{}", encoding="utf-8")
check("config: share_health missing means not asked yet", "share_health" not in m.load_config())

# ---- Send report: the sender, the daily count, the settings
report_body = {"id": "0123456789abcdef", "version": "0.9.14-beta", "message": "It stopped.", "details": "d"}
real_urlopen = m.urllib.request.urlopen
m.urllib.request.urlopen = fake_urlopen
os.environ.pop("MAPLE_EXP_ALLOW_SHARE", None)
sent.clear()
try:
    m.post_report(report_body)
    blocked = False
except RuntimeError:
    blocked = True
m.urllib.request.urlopen = real_urlopen
check("while testing (MAPLE_EXP_DATA is set) a Send report can't reach the real site", blocked and not sent)

os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
try:
    for code, data, wanted in ((200, {"ok": True}, "ok"), (400, {"error": "message"}, "message"),
                               (400, {"error": "details"}, "bad"), (413, {}, "bad"), (429, {"retry": True}, "limit"),
                               (500, {}, "later"), (404, {}, "later")):
        sent.clear()
        m.urllib.request.urlopen = answering(data, code)
        check(f"Send report: answer {code} {data} is '{wanted}'", m.post_report(report_body) == wanted)
    for data, want in (({"ok": True, "n": 17}, 17), ({"ok": True}, None), ({"ok": True, "n": "x"}, None), ({"ok": True, "n": 0}, None)):
        m.urllib.request.urlopen = answering(data, 200)
        got = m.post_report(report_body)
        check(f"Send report: answer {data} gives report number {want}", got == "ok" and getattr(got, "n", "missing") == want)
    check("Send report posts to /api/report with exactly the four fields",
          sent[0][0].endswith("/api/report") and set(json.loads(sent[0][1])) == {"id", "version", "message", "details"})

    def offline(request, timeout=0):
        raise m.urllib.error.URLError("no internet")
    m.urllib.request.urlopen = offline
    check("Send report: no internet is 'later', not a crash", m.post_report(report_body) == "later")
finally:
    m.urllib.request.urlopen = real_urlopen
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE", None)

today = healthlog.utc_day(NOON)
check("reports sent today: none recorded", m.reports_sent_today({}, NOON) == 0)
check("reports sent today: counted for the same UTC day", m.reports_sent_today({"reports_sent": {"day": today, "n": 2}}, NOON) == 2)
check("reports sent today: yesterday's count is forgotten", m.reports_sent_today({"reports_sent": {"day": "2000-01-01", "n": 3}}, NOON) == 0)
for good, bad in (({"day": "2026-10-25", "n": 2}, {"day": "2026-10-25", "n": "2"}), ({"day": "2026-10-25", "n": 0}, {"n": 1}),
                  ({"day": "2026-10-25", "n": 1}, {"day": "2026-10-25", "n": -1}), ({"day": "x", "n": 3}, "3")):
    m.CONFIG_PATH.write_text(json.dumps({"reports_sent": good}), encoding="utf-8")
    kept = "reports_sent" in m.load_config()
    m.CONFIG_PATH.write_text(json.dumps({"reports_sent": bad}), encoding="utf-8")
    check(f"config: reports_sent {good} is kept and {bad} is dropped", kept and "reports_sent" not in m.load_config())
m.CONFIG_PATH.write_text("{}", encoding="utf-8")

# Send report: the end of errors.log (A05)
me, B = m.Path.home().name, chr(92)
old = f"[2026-01-01] error with C:{B}Users{B}{me}{B}old.py and {me}\n" * 400
m.ERROR_LOG_PATH.write_text(old + f"[2026-10-25] the last error: {m.Path.home() / 'x.py'!r}\nEND-MARK\n", encoding="utf-8")
tail = m.App._errors_tail()
check("Send report: the errors part is the real end of errors.log, even when the file is long", tail.endswith("END-MARK\n") and len(tail) == 2500)
check("Send report: it holds no user name or folder, also from a doubled-backslash repr",
      me.lower() not in tail.lower() and "Users" not in tail and "x.py" in tail)
m.ERROR_LOG_PATH.unlink()
check("Send report: no errors.log gives an empty errors part", m.App._errors_tail() == "")

os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
m.urllib.request.urlopen = answering({"ok": True}, 200)
sent.clear()
try:
    import report
    wide = report.report_body("0123456789abcdef", "0.9.14-beta", "中" * 1500, "中" * 12000)
    m.post_report(wide)
finally:
    m.urllib.request.urlopen = real_urlopen
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE", None)
check("Send report: a Chinese report goes out as UTF-8 under the site's 16,384 bytes, and reads back the same",
      len(sent[0][1]) <= 16384 and json.loads(sent[0][1].decode("utf-8")) == wide)

# The two IDs never meet: the install ID goes only with shared runs, the health ID only with health and Send report
INSTALL, HEALTH = "installidinstallid0123456789", "0123456789abcdef"
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
m.urllib.request.urlopen = answering({"ok": True, "results": []}, 200)
sent.clear()
try:
    m.post_runs(INSTALL, [{"started": 1, "exp": 5, "level_start": 3, "level_end": 3}])
    m.post_health(full)
    m.post_report(report.report_body(HEALTH, "0.9.14-beta", "It stopped.", "d"))
finally:
    m.urllib.request.urlopen = real_urlopen
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE", None)
runs_body, health_body, report_body_sent = (d.decode("utf-8") for _, d in sent)
check("the install ID is only in the run upload, never in a health report or Send report",
      INSTALL in runs_body and INSTALL not in health_body + report_body_sent)
check("the health ID is never in a run upload", HEALTH not in runs_body and HEALTH in report_body_sent)
src = Path(m.__file__).read_text(encoding="utf-8")
check("every request built in maple_exp.py (4 of them) says only the program name and version",
      src.count("urllib.request.Request(") == 4 and src.count('"User-Agent": f"MapleClassicExpOverlay/{VERSION}"') == 4)

# shadow mode (shadow.py): reader v2's counts and time go in the report once there are any
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [], "screen", "ok")
log.look("read", ms=20.0)
check("no shadow part in the body until shadow mode counted something", "shadow" not in log.body("2026-10-25", "0123456789abcdef"))
for cat, ms in (("agree", 8.0), ("agree", 10.0), ("v2_only", 9.0), ("old_only", 9.0), ("neither", 12.0), ("skipped", None), ("errors", None), ("nonsense", 1.0)):
    log.look("read", ms=20.0, shadow=(cat, ms))
body = log.body("2026-10-25", "0123456789abcdef")
check("shadow counts go in the body by category (an unknown one counts nothing)",
      body and body["shadow"] == {"agree": 2, "disagree": 0, "v2_only": 1, "old_only": 1, "neither": 1, "skipped": 1, "errors": 1})
check("v2's average time is the mean of the looks it ran (skipped and error looks have none)", body["ms"]["v2_avg"] == 9.6)
check("the body still passes the contract check", healthlog.bad_field(body, "2026-10-26") is None)
log.save()
check("shadow counts survive a restart", new_log().body("2026-10-25", "0123456789abcdef")["shadow"]["agree"] == 2)


# ---- A04 audit: bodies always pass the site's rules, days and times, damaged files, the sender's guards
import random
import threading
import types

HID = "0123456789abcdef"
DAY_AFTER = lambda: healthlog.utc_day(clock[0])

# Many made-up days: whatever the mix, a body that is made passes the overlay's check and fits 4,096 bytes.
rnd = random.Random(4)
bad_bodies, biggest, made = [], 0, 0
for i in range(150):
    fresh()
    log = new_log()
    log.snapshot(rnd.choice(["0.9.14-beta", "0.9.14", "10.20.30-beta.2"]),
                 rnd.choice(["Windows 10.0.26200", "Windows éè 10", "", "x" * 200, "macOS 15.1"]),
                 [(rnd.choice([1920, 2560, 3840, 5120]), rnd.choice([1080, 1440, 2880]), rnd.choice([None, 100, 150, 500]))
                  for _ in range(rnd.choice([0, 1, 4, 7]))], rnd.choice(["screen", "window", "region"]),
                 rnd.choice(["ok", "missing", "no_eng"]))
    for _ in range(rnd.choice([1, 5, 120])):
        log.look(rnd.choice(healthlog.EXP_KINDS), rnd.random() < .3, [(p, rnd.random() < .5) for p in rnd.sample(list(healthlog.PAIRS), 2)],
                 rnd.choice([None, 5.0, 25.5, 9999.0, 1e6]), rnd.random() * 9,
                 rnd.choice([None, (1920, 1080, True, 14.0), (5120, 2880, False, 28.049999), (2560, 1440, False, 18.3333333)]),
                 rnd.choice([None, ("agree", 8.1), ("disagree", None), ("errors", None)]))
    log.errors({"Err" + "ab"[j % 2] * (j + 1): rnd.randint(1, 5) for j in range(rnd.choice([0, 3, 12, 30]))})
    log.errors({"Weird_Name": 1, "A2": 1, "": 1})
    b = log.body("2026-10-25", HID)
    clock[0] += 86400
    if b is None:
        bad_bodies.append(i)
        continue
    made += 1
    biggest = max(biggest, len(json.dumps(b).encode()))
    if healthlog.bad_field(b, DAY_AFTER()) is not None:
        bad_bodies.append(i)
check("150 made-up days: every body that is made passes the check (and none is dropped)", not bad_bodies and made == 150)
# the biggest day there can be: all counts at the top, 4 screens, 12 game sizes, 10 long error names, non-ASCII OS
fresh()
log = new_log()
log.snapshot("10.20.30-beta.2", "Windows é" * 20, [(20000, 20000, 500)] * 6, "region", "no_eng")
rec = log.today()
for k in healthlog.EXP_KINDS:
    rec["exp"][k] = 30000
rec["exp"]["percent_only"] = 60000
rec["looks"] = 180000
for name, pair in healthlog.PAIRS.items():
    rec[name] = {k: 10 ** 9 for k in pair}
rec["errors"] = {("E" * 39 + chr(97 + j)): 10 ** 9 for j in range(10)}
rec["tracking"], rec["ms_total"], rec["ms_n"], rec["v2_ms"], rec["v2_n"] = 10 ** 6, 10 ** 12, 1, 10 ** 12, 1
rec["hist"] = {str(k): 5 for k in range(1001)}
rec["games"] = {f"{20000 - j}x20000/1/63.9": 5 for j in range(12)}
rec["shadow"] = {k: 10 ** 9 for k in healthlog.SHADOW_KEYS}
clock[0] += 86400
big = log.body("2026-10-25", HID)
check("the biggest possible day still makes a valid body of at most 4,096 bytes",
      big is not None and healthlog.bad_field(big, DAY_AFTER()) is None and len(json.dumps(big).encode()) <= 4096)

# ---- days and times
fresh()
clock[0] = float(calendar.timegm((2026, 10, 25, 23, 59, 59)))
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [(1920, 1080, 100)], "screen", "ok")
log.look("read", game=(1920, 1080, True, 14.0))
clock[0] += 2
log.look("read", game=(1920, 1080, True, 14.0))
check("looks across UTC midnight go to the right days", log.days["2026-10-25"]["looks"] == 1 and log.days["2026-10-26"]["looks"] == 1)
clock[0] += 86400
check("the new day has its own snapshot at once (the overlay only takes one every 5 minutes)",
      log.body("2026-10-26", HID) is not None and log.body("2026-10-25", HID) is not None)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
log.look("read")
clock[0] += 86400 * 3                   # the PC slept (or the overlay was closed) for days: nothing to count for those
log.look("read")
check("days the overlay didn't run have no record", sorted(log.days) == ["2026-10-25", "2026-10-28"])
clock[0] += 86400
got = log.due(HID)
check("several unsent days are sent oldest first", got is not None and got[0] == "2026-10-25")
log.attempting()
log.finish("2026-10-25", "ok")
clock[0] += 3601
got = log.due(HID)
check("...then the next one an hour later", got is not None and got[0] == "2026-10-28")
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
log.look("read")
clock[0] -= 86400                    # the clock is put back to the day before: its looks start a new record
log.look("read")
check("a clock put back counts into that earlier day", log.days["2026-10-24"]["looks"] == 1)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
log.look("read")
log.save()
clock[0] += 86400
closed = new_log()                      # closed before sending, opened the next day
got = closed.due(HID)
check("closing before a send loses nothing: the day is due after a restart", got is not None and got[1]["looks"] == 1)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
log.look("read")
for day in range(1, 11):                # 10 more days of use: the file keeps 8
    clock[0] += 86400
    log.look("read")
    log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
    log.save()
check("health.json keeps at most 8 days", len(json.loads((folder / "health.json").read_text(encoding="utf-8"))["days"]) == healthlog.KEEP_DAYS)
check("...and stays small", (folder / "health.json").stat().st_size < 20000)

# ---- a damaged health.json
def with_snap(snap):
    fresh()
    log = new_log()
    log.look("read")
    log.days["2026-10-25"]["snap"] = snap
    log.save()
    clock[0] += 86400
    return new_log()


for snap in ({"screens": 5, "version": "0.9.14", "os": "x", "capture": "screen", "tesseract": "ok"}, {"screens": None},
             {"screens": [1, 2], "version": 5}, {"os": {"a": 1}}):
    try:
        got = with_snap(snap).due(HID)
        ok = got is None or healthlog.bad_field(got[1], DAY_AFTER()) is None
    except Exception:
        ok = False
    check(f"a damaged snapshot in health.json ({json.dumps(snap)[:40]}) can't break sending", ok)
for text in ("", "[]", "null", "5", '{"days": 5}', '{"days": {"2026-10-25": 5}}', '{"days": {"x": {}}}',
             '{"days": {"2026-10-25": {"exp": 7, "hist": 3, "games": [], "errors": 5, "snap": 9}}, "last_sent": 5}', "\x00" * 50):
    fresh()
    (folder / "health.json").write_text(text, encoding="utf-8")
    try:
        new_log().today()
        ok = True
    except Exception:
        ok = False
    check(f"a health.json that holds {text[:30]!r} loads without an error", ok)
fresh()
(folder / "health.json").write_bytes(b"\xff\xfe\x00 not utf-8")
check("a health.json that isn't text loads without an error", new_log().days == {})
fresh()
(folder / "health.json").write_text(json.dumps({"days": {"2026-10-25": {"looks": 10 ** 30, "tracking": float("inf"), "exp": {"read": -5}}}}),
                                    encoding="utf-8")
check("huge, infinite or negative numbers in health.json become zeros", new_log().days["2026-10-25"]["looks"] == 0
      and new_log().days["2026-10-25"]["tracking"] == 0.0 and new_log().days["2026-10-25"]["exp"]["read"] == 0)
fresh()
log = new_log()
(folder / "health.json").mkdir()           # a folder where the file should be: saving fails quietly
try:
    log.look("read")
    log.save()
    ok = True
except Exception:
    ok = False
(folder / "health.json").rmdir()
check("a health.json that can't be written doesn't raise", ok)

# ---- what the site can answer, as post_health sees it
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
real_urlopen = m.urllib.request.urlopen
try:
    class Raw:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return self.data[:n] if n and n > 0 else self.data

    def http_error(code, data=b""):
        def urlopen(request, timeout=0):
            raise m.urllib.error.HTTPError(request.full_url, code, "x", {}, Raw(data))
        return urlopen

    m.urllib.request.urlopen = lambda request, timeout=0: Raw(b"<html><body>Log in to the Wi-Fi</body></html>")
    check("a captive portal's page (200, HTML) means try later", m.post_health(full) == ("later", ""))
    m.urllib.request.urlopen = lambda request, timeout=0: Raw(b'{"ok": false}')
    check("an answer that isn't ok means try later", m.post_health(full) == ("later", ""))
    m.urllib.request.urlopen = lambda request, timeout=0: Raw(b"[1]")
    check("an answer that isn't an object means try later", m.post_health(full) == ("later", ""))
    m.urllib.request.urlopen = lambda request, timeout=0: (_ for _ in ()).throw(TimeoutError("timed out"))
    check("a timeout means try later", m.post_health(full) == ("later", ""))
    m.urllib.request.urlopen = http_error(400, b"<html>proxy says no</html>")
    check("a 400 that isn't the site's own answer (a proxy, a hotel Wi-Fi page) means try later", m.post_health(full) == ("later", ""))
    m.urllib.request.urlopen = http_error(400, b'{"message": "no"}')
    check("...also JSON without the site's error field", m.post_health(full) == ("later", ""))
    m.urllib.request.urlopen = http_error(400, b'{"error": "Bad Field!<script>"}')
    check("the field name from a 400 is cut down to letters", m.post_health(full) == ("bad", "BadFieldscript"))
    for code in (301, 403, 404, 405, 415, 429, 500, 502, 503):
        m.urllib.request.urlopen = http_error(code, b'{"retry": true}')
        check(f"a {code} means try later", m.post_health(full) == ("later", ""))
finally:
    m.urllib.request.urlopen = real_urlopen
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE")

# ---- the sending schedule in the app (App._health_tick on a stand-in, so no window is opened)
class StandIn:
    pass


def stand_in(cfg):
    s = StandIn()
    s.cfg, s._health_sending, s._health_snap_at = cfg, False, 10 ** 12
    s._health_errors = {}
    s.reader = types.SimpleNamespace(error_types={})
    s.q = __import__("queue").Queue()
    fresh()
    s.healthlog = new_log()
    s.healthlog.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
    s.healthlog.look("read")
    clock[0] += 86400
    return s


real_post, real_save = m.post_health, m.save_config
posts = []
m.post_health = lambda body, timeout=15: posts.append(body) or ("ok", "")
m.save_config = lambda cfg: None
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
try:
    def run_tick(s):
        m.App._health_tick(s)
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon and t.is_alive():
                t.join(2)

    s = stand_in({"share_health": False})
    run_tick(s)
    check("health off: nothing is sent and no health ID is made", not posts and "health_id" not in s.cfg and s.q.empty())
    s = stand_in({})
    run_tick(s)
    check("not asked yet: nothing is sent and no health ID is made", not posts and "health_id" not in s.cfg)
    s = stand_in({"share_health": "yes"})
    run_tick(s)
    check("a share_health that isn't exactly true sends nothing", not posts)
    del os.environ["MAPLE_EXP_ALLOW_SHARE"]
    s = stand_in({"share_health": True})
    run_tick(s)
    check("while testing (MAPLE_EXP_DATA is set) the app sends nothing and makes no ID", not posts and "health_id" not in s.cfg)
    os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
    s = stand_in({"share_health": True})
    run_tick(s)
    check("health on: the ID is made at the first send, 16 hex characters", healthlog.good_id(s.cfg.get("health_id")))
    check("...one request goes out with that ID", len(posts) == 1 and posts[0]["id"] == s.cfg["health_id"] and posts[0]["day"] == "2026-10-25")
    check("...and its answer is queued for the main thread", s.q.get_nowait() == ("health_sent", ("2026-10-25", "ok", "")))
    s._health_sending = False
    m.App._health_done(s, "2026-10-25", "ok", "")
    posts.clear()
    run_tick(s)
    check("the same tick again sends nothing more (the day is done)", not posts)
    s = stand_in({"share_health": True, "health_id": HID})
    s._health_sending = True
    run_tick(s)
    check("a send still in flight blocks a second one", not posts)
    s = stand_in({"share_health": True, "health_id": HID})
    run_tick(s)
    s._health_sending = False
    run_tick(s)
    check("two ticks in a row make one request (an hour has to pass)", len(posts) == 1)
    posts.clear()
    s = stand_in({"share_health": True, "health_id": HID})
    s.healthlog.finish("2026-10-25", "later")
    run_tick(s)
    check("after 'later' nothing is sent until the next UTC day", not posts)
    clock[0] += 86400
    run_tick(s)
    check("...then one try", len(posts) == 1)
    posts.clear()
    s = stand_in({"share_health": True, "health_id": HID})
    m.post_health = lambda body, timeout=15: (_ for _ in ()).throw(OSError("boom"))
    run_tick(s)
    check("a send that raises is queued as 'later', not lost or repeated",
          s.q.get_nowait() == ("health_sent", ("2026-10-25", "later", "")))
finally:
    m.post_health, m.save_config = real_post, real_save
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE", None)

# the health counting must never get in the way of the reading itself
class Boom:
    def look(self, *a, **k):
        raise ValueError("boom")


s = StandIn()
s.healthlog, s._health_at, s.tracker = Boom(), None, types.SimpleNamespace(running=True)
s.reader = types.SimpleNamespace(last_rect=(0, 0, 1920, 1080), gh=14.0)
s._health_monitors = []
try:
    m.App._count_health(s, m.Reading("x", exp_kind="read"), 1.0)
    ok = True
except Exception:
    ok = False
check("a failure while counting a look doesn't stop the look from being handled", ok)

# Decision 1 (owner, 2026-10-25): a day where every look broke has no counted look, but its exception names are
# what a report is for
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [(1920, 1080, 100)], "screen", "ok")
log.errors({"ValueError": 40})
broken_day = log.body("2026-10-25", "0123456789abcdef")
check("a day with exception names but no counted look is still sent (looks 0)",
      broken_day is not None and broken_day["looks"] == 0 and broken_day["errors"] == {"ValueError": 40}
      and healthlog.bad_field(broken_day, "2026-10-26") is None)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [(1920, 1080, 100)], "screen", "ok")
check("...a day with neither looks nor exceptions still isn't", log.body("2026-10-25", "0123456789abcdef") is None)

# ---- 0.9.14: UI size and layout, the mesos and party shadows, EXP's old_only reasons (PLAN 4.7, decision 25)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(2560, 1440, 100)], "window", "ok")
log.look("read", ms=20.0)
body = log.body("2026-10-25", HID)
check("no new key in the body until something new was counted (an old overlay's shape)",
      not any(k in body for k in healthlog.V2_KEYS) and set(body.get("ms", {})) <= {"look_avg", "look_p95", "v2_avg"})
looks_in = (("default", "keyboard"), ("default", "keyboard"), ("ideal", "controller-standard"), ("ideal", "controller-cross"),
            (None, None), ("sideways", "pages"), ("default", None))
for ui, layout in looks_in:
    log.look("read", ms=20.0, ui=ui, layout=layout)
body = log.body("2026-10-25", HID)
check("looks are counted by UI size and by layout (both kinds of controller layout are 'controller'; unknown counts nothing)",
      body["ui"] == {"default": 3, "ideal": 2, "keyboard": 2, "controller": 2} and healthlog.bad_field(body, "2026-10-26") is None)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(2560, 1440, 100)], "window", "ok")
for i, (part, cat, ms) in enumerate((("mesos", "agree", 4.0), ("mesos", "agree", 6.0), ("mesos", "v2_only", None), ("party", "old_only", 1.0),
                                     ("party", "neither", 3.0), ("party", "skipped", None), ("party", "nonsense", 1.0),
                                     ("map", "agree", 1.0))):
    log.look("read", ms=20.0, shadows=((part, cat, ms),))
log.look("read", ms=20.0, shadows=(("mesos", "disagree", 5.0), ("party", "errors", None)))
body = log.body("2026-10-25", HID)
check("the mesos and party shadows are counted by category in their own keys (an unknown category or part counts nothing)",
      body["shadow_mesos"] == {"agree": 2, "disagree": 1, "v2_only": 1, "old_only": 0, "neither": 0, "skipped": 0, "errors": 0}
      and body["shadow_party"] == {"agree": 0, "disagree": 0, "v2_only": 0, "old_only": 1, "neither": 1, "skipped": 1, "errors": 1}
      and "shadow" not in body)
check("...and their times go in ms (mean over the looks that ran), but only next to the usual look times",
      body["ms"]["mesos_v2_avg"] == 5.0 and body["ms"]["party_v2_avg"] == 2.0 and "v2_avg" not in body["ms"]
      and healthlog.bad_field(body, "2026-10-26") is None)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [], "screen", "ok")
for shadow_, why in ((("old_only", 5.0), "not_found"), (("old_only", 5.0), "not_found"), (("old_only", 5.0), "unclear"),
                     (("old_only", 5.0), "ok"), (("old_only", 5.0), None), (("agree", 5.0), "not_found"), (("v2_only", 5.0), "unclear")):
    log.look("read", ms=20.0, shadow=shadow_, why=why)
body = log.body("2026-10-25", HID)
check("EXP's old_only looks are counted by why v2 didn't read (other categories and other reasons count nothing)",
      body["shadow_why"] == {"not_found": 2, "unclear": 1} and body["shadow"]["old_only"] == 5)
log.save()
again = new_log().body("2026-10-25", HID)
check("the new counts survive a restart", again["shadow_why"] == body["shadow_why"] and "ui" not in again)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [], "screen", "ok")
log.look("read", ui="ideal", layout="keyboard", shadow=("old_only", 1.0), why="unclear")
log.today()["ui"]["ideal"] = 10 ** 6
log.today()["why"]["unclear"] = 10 ** 6
body = log.body("2026-10-25", HID)
check("a count above the looks is cut to the looks (the site refuses it, and a day must never be lost to a slip)",
      body is not None and body["ui"]["ideal"] == 1 and body["shadow_why"]["unclear"] == 1)
rec = log.today()
rec["ui"] = {"default": "x", "ideal": -3, "keyboard": "1.5", "controller": None}
rec["shadow_mesos"] = "not a dict"
fresh_rec = healthlog.HealthLog._clean(json.loads(json.dumps(rec)))
check("a damaged record (a file edited by hand) is cleaned back to zeros", fresh_rec["ui"] == {k: 0 for k in healthlog.UI_KEYS}
      and fresh_rec["shadow_mesos"] == {k: 0 for k in healthlog.SHADOW_KEYS})
old_day = {"looks": 3, "exp": {"read": 3}, "snap": {}, "shadow": {"agree": 3}, "sent": False}
cleaned = healthlog.HealthLog._clean(old_day)
check("a day saved by 0.9.13 (none of the new keys) loads with them at zero",
      cleaned["ui"] == {k: 0 for k in healthlog.UI_KEYS} and cleaned["shadow_party"]["agree"] == 0 and cleaned["shadow"]["agree"] == 3)

# every body the overlay builds passes the contract, however the counts fall (made-up days)
rnd_ = random.Random(11)
bad_bodies, sizes = 0, []
for n in range(300):
    fresh()
    log = new_log()
    log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(1920, 1080, 100)], "screen", "ok")
    for _ in range(rnd_.choice([1, 5, 60])):
        log.look(rnd_.choice(healthlog.EXP_KINDS), rnd_.random() < .3, ms=rnd_.choice([None, 5.0, 40.0]),
                 shadow=rnd_.choice([None, ("old_only", 4.0), ("agree", 4.0), ("errors", None)]),
                 ui=rnd_.choice([None, "default", "ideal"]), layout=rnd_.choice([None, "keyboard", "controller-cross"]),
                 shadows=tuple((p, rnd_.choice(healthlog.SHADOW_KEYS), rnd_.choice([None, 2.0, 7.5])) for p in rnd_.sample(["mesos", "party"], rnd_.choice([0, 1, 2]))),
                 why=rnd_.choice([None, "not_found", "unclear", "ok"]))
    got = log.body("2026-10-25", HID)
    if got is None or healthlog.bad_field(got, "2026-10-26") is not None:
        bad_bodies += 1
    else:
        sizes.append(len(json.dumps(got, separators=(",", ":")).encode()))
check("300 made-up days with every new count: every body passes the contract's rules and the size cap", bad_bodies == 0 and max(sizes) <= 4096)
fresh()
log = new_log()
log.snapshot("10.20.30-beta.2", "Windows é" * 20, [(20000, 20000, 500)] * 4, "region", "no_eng")
rec = log.today()
for k in healthlog.EXP_KINDS:
    rec["exp"][k] = 30000
rec["looks"] = 180000
rec["ms_total"], rec["ms_n"] = 10 ** 9, 1
rec["games"] = {"20000x20000/1/63.9": 5}
for key in ("ui", "why", "shadow_mesos", "shadow_party"):
    rec[key] = {k: 10 ** 9 for k in rec[key]}
for p in healthlog.SHADOW_PARTS:
    rec[f"{p}_v2_ms"], rec[f"{p}_v2_n"] = 10 ** 12, 1
rec["shadow"] = {k: 10 ** 9 for k in healthlog.SHADOW_KEYS}
rec["v2_ms"], rec["v2_n"] = 10 ** 12, 1
big = log.body("2026-10-25", HID)
check("the biggest possible day with every new key is valid and within 4,096 bytes",
      big is not None and healthlog.bad_field(big, "2026-10-26") is None and len(json.dumps(big, separators=(",", ":")).encode()) <= 4096)

# the contract's v2 bodies each have their own id here (the site answers a repeat with the same id and day as "dup")
ids = {c["body"]["id"] for c in contract["health_valid"]}
check("the contract's valid bodies share ids (so a test that sends them has to give each its own)", len(ids) < len(contract["health_valid"]))

# ---- decision 25: a site that doesn't know the new keys yet makes the day wait, never dropped
v2_body = next(c["body"] for c in contract["health_valid"] if c["name"].startswith("v2: every new key"))
plain_body = next(c["body"] for c in contract["health_valid"] if c["name"] == "full")
ms_body = {**copy.deepcopy(plain_body), "ms": {**plain_body["ms"], "mesos_v2_avg": 3.5}} if "ms" in plain_body else None
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
real_urlopen = m.urllib.request.urlopen
try:
    def old_site(field):
        def urlopen(request, timeout=0):
            raise m.urllib.error.HTTPError(request.full_url, 400, "x", {}, Answer({"error": field}))
        return urlopen
    for key in healthlog.V2_KEYS:
        m.urllib.request.urlopen = old_site(key)
        check(f"an older site refusing the new key '{key}' by name is 'later' (the day waits)",
              m.post_health({**copy.deepcopy(plain_body), key: v2_body[key]}) == ("later", key))
    m.urllib.request.urlopen = old_site("ui")
    check("a refusal of 'ui' on a body that has no 'ui' is a real refusal", m.post_health(plain_body) == ("bad", "ui"))
    m.urllib.request.urlopen = old_site("id")
    check("a refusal of another field is still 'bad' (new keys or not)", m.post_health(v2_body) == ("bad", "id"))
    m.urllib.request.urlopen = old_site("shadow")
    check("...even one that is an old key", m.post_health(v2_body) == ("bad", "shadow"))
    if ms_body:
        m.urllib.request.urlopen = old_site("ms")
        check("an older site refusing 'ms' because it holds the new mesos_v2_avg is 'later'", m.post_health(ms_body) == ("later", "ms"))
        check("...but 'ms' on a body with only the old ms keys is 'bad'", m.post_health(plain_body) == ("bad", "ms"))
finally:
    m.urllib.request.urlopen = real_urlopen
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
fresh()
clock[0] = float(calendar.timegm((2026, 10, 25, 12, 0, 0)))
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(1920, 1080, 100)], "screen", "ok")
log.look("read", ui="default", layout="keyboard", shadows=(("mesos", "agree", 3.0),))
clock[0] += 86400
day, body = log.due(HID)
log.attempting()
log.finish(day, "later")
check("'later' from an older site keeps the day (not sent, not dropped) and waits for tomorrow",
      not log.days[day]["sent"] and log.due(HID) is None and log.body(day, HID) is not None)
clock[0] = log.retry_after + 3601
check("...and the day is due again, with the new keys, once the site is updated", (log.due(HID) or (None, {}))[1].get("ui") is not None)
fake = types.SimpleNamespace(_health_sending=True, healthlog=log)
m.App._health_done(fake, day, "later", "ui")
check("the overlay notes in errors.log that the site doesn't know the key yet, and the day still waits",
      not fake._health_sending and not log.days[day]["sent"] and "doesn't know the health field ui" in m.ERROR_LOG_PATH.read_text(encoding="utf-8"))

# ---- B04: a clock that was far ahead once can't stop the reports for good
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
log.look("read")
clock[0] += 86400
log.attempting()                                    # (the clock was 3 years ahead when this send was tried...)
log.last_attempt = clock[0] + 3 * 365 * 86400
log.finish("2026-10-25", "later")
log.retry_after = clock[0] + 3 * 365 * 86400
log.save()
clock[0] += 3601                                    # ...and now it shows the right time again
back = new_log()
wait = back.due(HID) is None                        # (a 'later' still waits for the next UTC midnight, no longer)
clock[0] = (clock[0] // 86400 + 1) * 86400 + 1
check("a last_attempt and retry_after from a clock that was years ahead hold sending back at most until the next UTC midnight",
      wait and back.due(HID) is not None)
fresh()
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
log.look("read")
log.attempting()
clock[0] += 86400
log.last_attempt = 1e308
first = log.due(HID)                                # (counts as an attempt just now: an hour from here it goes)
clock[0] += 3601
check("...also an absurd last_attempt (held back an hour at most)", first is None and log.due(HID) is not None)
fresh()
(folder / "health.json").write_text(json.dumps({"days": {"2026-10-24": {"looks": 5, "exp": {"read": 5}, "hist": {"9" * 5000: 3, "7": 2, "99999": 1},
                                    "snap": {"version": "0.9.14-beta", "os": "x", "capture": "screen", "tesseract": "ok", "screens": []}}}}),
                                   encoding="utf-8")
hand = new_log()
check("the look-time histogram read from the file keeps only the buckets the overlay makes (0..%d)" % healthlog.MAX_BUCKET,
      hand.days["2026-10-24"]["hist"] == {"7": 2})

# ---- the Reading carries the new fields (old code that builds a Reading without them keeps working)
rd = m.Reading("x")
check("a Reading has no UI size, layout or part shadows until a reader sets them",
      rd.ui is None and rd.layout is None and rd.shadows == () and rd.shadow_why is None)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
