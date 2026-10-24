"""Tests for log_details.py (what the log's Details window shows).  Run:  py -3 tests/test_log_details.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from maple_overlay.logs import log_details as ld  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def get(sections, title, label):
    return dict(dict(sections)[title])[label]


a = {"date": "2026-10-24", "map": "Ant Tunnel II", "level": 34, "party": 1, "exp": 10000, "seconds": 3600,
     "mesos": 5000, "mesos_seconds": 3600, "potion_cost": 1000, "potion_seconds": 3600}
b = {**a, "date": "2026-10-23", "party": 4, "exp": 30000, "seconds": 3600, "mesos": 9000}
sec = ld.line_sections([a])
check("totals and rates of one line", get(sec, "EXP", "EXP gained") == "10,000" and get(sec, "EXP", "EXP per hour") == "10,000")
check("mesos, potions and net", get(sec, "Mesos", "Mesos per hour") == "5,000" and get(sec, "Mesos", "Net mesos per hour") == "4,000")
check("5-minute stretches", get(sec, "Line", "5-minute stretches (about)") == "12.0")
c = ld.line_sections([{**a, "mesos_seconds": 0, "mesos": 0}])
check("mesos not read is said so", get(c, "Mesos", "Mesos") == "not read for this line")
two = ld.line_sections([a, b])
check("party sizes shown as one line add up", get(two, "EXP", "EXP gained") == "40,000" and "Party sizes" in dict(two))
cmp_ = ld.compare_sections([a], [a, b])
check("compared with all logs on the map", get(cmp_, "On this map, all your logs", "Your average EXP per hour here") == "20,000"
      and get(cmp_, "On this map, all your logs", "This line against your average") == "-50%")
check("solo and party averages", get(cmp_, "On this map, all your logs", "Solo, EXP per hour (all days)") == "10,000"
      and get(cmp_, "On this map, all your logs", "Party, EXP per hour (all days)") == "30,000")
check("an old line without fields still works", ld.line_sections([{"date": "2026-01-01", "map": "X", "level": None, "exp": 5, "seconds": 0}]) is not None)
check("text for copying", "EXP gained: 10,000" in ld.as_text(sec))

# the counters the log keeps per stretch
x = {**a, "party": 4, "kills": 600, "own_exp": 8000, "bonus_exp": 2000, "mesos_drops": 300, "mesos_text": 3000,
     "deaths": 2, "levels": 1, "x_seconds": 3600, "lvl_total": 100000, "remaining": 40000, "updated": 5,
     "blocks": [[1000, 300]] * 9 + [[500, 300]]}
sx = ld.line_sections([x])
check("kills, rate and EXP per kill", get(sx, "Kills and drops (from the game's messages)", "Kills per hour") == "600"
      and get(sx, "Kills and drops (from the game's messages)", "EXP per kill") == "17")
check("mesos per drop", get(sx, "Kills and drops (from the game's messages)", "Mesos per drop") == "10")
check("the party's bonus share", get(sx, "Your EXP and the party's bonus", "Party bonus share") == "20.0%")
check("deaths per hour", get(sx, "Deaths and level-ups", "Deaths per hour") == "2.0")
check("level progress and time to the next level", get(sx, "Level progress", "Level progress per hour") == "10.0%"
      and get(sx, "Level progress", "To the next level (at the last stretch)").startswith("4 h 00 min"))
check("the pace chart has a point per stretch", len(ld.pace([x])) == 10 and ld.pace([x])[-1] == 6000)
check("pace trend: the last stretches are slower", ld.pace_trend([x]) < 0)
check("lines without the counters have no such sections", "Level progress" not in dict(ld.line_sections([a])))
print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
