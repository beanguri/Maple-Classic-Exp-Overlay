"""Tests for session_stats.py: the Session numbers, the Solo / Party split, Reset Session and the CSV log.

Run from the project folder:  py -3 tests/test_session_stats.py   (no screen needed)"""
import csv
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from maple_overlay.core import session_stats as ss  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(path=None):
    clock = Clock()
    log = ss.CsvLog(path) if path else None
    return ss.SessionStats(log, clock=clock, wall=lambda: datetime(2026, 10, 24, 10, 0, 0)), clock


def step(s, clock, secs, running=True, party=1, exp=0, mesos=0, levels=0, deaths=0, map_name="Henesys", level=30):
    clock.t += secs
    s.update(running, party, exp, mesos, levels, deaths, map_name, level)


# solo then party: time and gains go to the right bucket
s, c = make()
step(s, c, 0, running=False)                       # baseline
step(s, c, 1, True, 1, exp=0)
for i in range(1, 21):
    step(s, c, 1, True, 1, exp=100 * i, mesos=50 * i)           # 20 s solo: 2000 EXP, 1000 mesos
for i in range(1, 11):
    step(s, c, 1, True, 3, exp=2000 + 300 * i, mesos=1000 + 10 * i)   # 10 s in a party of 3
check("solo time and EXP", round(s.solo.seconds) == 20 and s.solo.exp == 2000 and s.solo.mesos == 1000)
check("party time and EXP", round(s.party[3].seconds) == 10 and s.party[3].exp == 3000 and s.party[3].mesos == 100)
check("totals add up", s.exp == 5000 and round(s.seconds) == 30)
check("party (all) bucket", s.party_all().exp == 3000)
check("per hour", abs(s.solo.exp_hr - 2000 / 20 * 3600) < 1)
check("rows are Solo, Party and Total", [r[0] for r in s.rows()] == ["Solo", "Party", "Total"])
check("Total adds solo and party", s.total().exp == 5000 and round(s.total().seconds) == 30 and s.total().mesos == 1100)
for i in range(1, 6):
    step(s, c, 1, True, 4, exp=5000 + i, mesos=1100)
check("a second party size gets its own rows under Total", [r[0].strip() for r in s.rows()][-2:] == ["3 players", "4 players"])
s.party.pop(4)

# time is not counted while paused or across long gaps
before = s.seconds
step(s, c, 4, False, 1, exp=5000)
step(s, c, 4, False, 1, exp=5000)
check("paused time doesn't count", s.seconds == before)
step(s, c, 100, True, 1, exp=5000)                 # a long gap: not tracking time
check("a long gap isn't tracking time", s.seconds == before)

# a total that drops (another map, Reset) counts from zero again
s2, c2 = make()
step(s2, c2, 0, False)
step(s2, c2, 1, True, 1, exp=1000)
step(s2, c2, 1, True, 1, exp=0)
step(s2, c2, 1, True, 1, exp=250)
check("a reset total counts from zero (1000 before, 250 after)", s2.exp == 1250)

# Reset Session only clears the counters, and nothing gained before it comes back
s3, c3 = make()
step(s3, c3, 0, False)
step(s3, c3, 1, True, 1, exp=500, levels=1, deaths=1)
step(s3, c3, 1, True, 1, exp=900)
s3.reset()
check("reset clears everything", s3.exp == 0 and s3.seconds == 0 and s3.levels == 0 and s3.deaths == 0 and not s3.party)
step(s3, c3, 1, True, 1, exp=900)
check("what came before the reset doesn't leak back", s3.exp == 0)
step(s3, c3, 1, True, 1, exp=1000)
check("it counts again after the reset", s3.exp == 100)

# levels and deaths
s4, c4 = make()
step(s4, c4, 0, False)
step(s4, c4, 1, True, 1, levels=0)
step(s4, c4, 1, True, 1, levels=1)
step(s4, c4, 1, True, 1, levels=1, deaths=1)
check("levels and deaths", s4.levels == 1 and s4.deaths == 1)

# the CSV
with tempfile.TemporaryDirectory() as d:
    path = str(Path(d) / "session_log.csv")
    s5, c5 = make(path)
    step(s5, c5, 0, False)
    step(s5, c5, 1, True, 1, exp=0)
    step(s5, c5, 1, True, 2, exp=100)
    step(s5, c5, 1, True, 2, exp=200, levels=1, map_name="Ant Tunnel")
    step(s5, c5, 1, False, 2, exp=200)
    s5.reset()
    raw = Path(path).read_bytes()
    check("the CSV starts with a BOM (Excel)", raw.startswith(b"\xef\xbb\xbf"))
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    events = [r["event"] for r in rows]
    check("the CSV has a header and the events", rows and list(rows[0].keys()) == list(ss.CSV_HEADER))
    check("start, party, map, level_up, pause, reset_session are logged",
          all(e in events for e in ("start", "party", "map", "level_up", "pause", "reset_session")))
    check("a party change says from and to", any(r["event"] == "party" and "from 1 to 2" in r["note"] for r in rows))
    # a file that can't be written to must not stop tracking
    errors = []
    bad = ss.SessionStats(ss.CsvLog(str(Path(d)), on_error=lambda *a: errors.append(a)))
    bad.update(False, 1, 0, 0, 0, 0, "x", 1)
    bad.update(True, 1, 0, 0, 0, 0, "x", 1)
    check("a failing log reports the error and carries on", errors and bad.seconds == 0)
    # a big file is rotated
    big = Path(d) / "big.csv"
    big.write_bytes(b"x" * (ss.MAX_CSV_BYTES + 1))
    ss.CsvLog(str(big)).write({"event": "start"})
    check("a big log is rotated to .old.csv", (Path(d) / "big.old.csv").exists() and big.stat().st_size < 1000)

# summary every 5 minutes of tracking
with tempfile.TemporaryDirectory() as d:
    path = str(Path(d) / "s.csv")
    s6, c6 = make(path)
    step(s6, c6, 0, False)
    for i in range(1, 700):
        step(s6, c6, 1, True, 1, exp=i)
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    check("a summary line is written every 5 minutes", sum(r["event"] == "summary" for r in rows) == 2)

# own EXP vs the party's bonus, from the game's message lines (kept only when they add up to the bar's gain)
def split_run(seq, party=3):
    s, c = make()
    for exp, lines in seq:
        c.t += 0.5
        s.update(True, party, exp, 0, 0, 0, "m", 50, msg_lines=lines)
    return s


A = [("own", 8, 1), ("bonus", 2, 2)]
s7 = split_run([(1000, []), (1000, A), (1010, A)])
b = s7.party_all()
check("lines that add up to the bar's gain split it", (b.exp, b.own, b.bonus) == (10, 8, 2))
s8 = split_run([(0, []), (0, A), (10, A), (10, A + A), (20, A + A)])
b = s8.party_all()
check("the same two lines twice are two kills", (b.exp, b.own, b.bonus) == (20, 16, 4))
B = [("own", 8, 1), ("bonus", 3, 2)]
s9 = split_run([(0, []), (0, B), (10, B)] + [(10, B)] * 20)
b = s9.party_all()
check("lines that don't add up leave the gain unsplit", (b.exp, b.own, b.bonus) == (10, 0, 0))
s10 = split_run([(0, []), (0, [("own", 8, 1)]), (8, [("own", 8, 1)])], party=1)
check("solo is never split", (s10.solo.own, s10.solo.bonus) == (0, 0))
s11 = split_run([(0, []), (0, A), (10, A), (15, A)] + [(15, A)] * 20)
names = [r[0].strip() for r in s11.rows()]
check("the table shows Solo, Party bonus and Other", names == ["Solo", "Party", "Solo", "Party bonus", "Other", "Total"]
      and dict((r[0].strip(), r[1].exp) for r in s11.rows())["Other"] == 5)
C = [("other", 10, 1), ("bonus", 3, 2)]
s12 = split_run([(0, []), (0, C), (13, C)] + [(13, C)] * 5)
b = s12.party_all()
check("a party member's kill goes to Party kills", (b.exp, b.own, b.bonus, b.other) == (13, 0, 3, 10))
check("the table shows Party kills", dict((r[0].strip(), r[1].exp) for r in s12.rows()).get("Party kills") == 10)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

