"""Tests for battle/loot notice parsing and NoticeTracker (no game or screen needed).

Run from the project folder:  py -3 tests/test_notices.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402
import notices  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


# ---- parse_line
check("parse meso with parentheses",
      notices.parse_line("You have gained mesos (+15000)") == ("meso", 15000))
check("parse meso without You have",
      notices.parse_line("gained mesos (+320)") == ("meso", 320))
check("parse meso with commas",
      notices.parse_line("You have gained mesos (+1,250)") == ("meso", 1250))
check("parse item in parentheses",
      notices.parse_line("You have gained an item (Red Potion)") == ("item", "Red Potion"))
check("parse item in Etc tab",
      notices.parse_line("You have gained an item in the Etc tab (Pig's Ribbon)") == ("item", "Pig's Ribbon"))
check("parse MapleBuddy-style received mesos",
      notices.parse_line("You have gained Mesos (+14)") == ("meso", 14))
check("parse loose +N mesos",
      notices.parse_line("+1,250 mesos") == ("meso", 1250))
check("parse item a Name",
      notices.parse_line("You have gained a Pig's Head") == ("item", "Pig's Head"))
check("ignore EXP lines",
      notices.parse_line("You have gained experience (+500)") is None)
check("ignore received EXP",
      notices.parse_line("You received EXP (+89)") is None)
check("bare item name helper",
      notices.bare_item_name("Pig's Ribbon") == "Pig's Ribbon")
check("bare helper rejects meso noise",
      notices.bare_item_name("gained mesos (+3)") is None)
check("ignore lost items",
      notices.parse_line("You have lost items (10 Screw)") is None)
check("ignore blank", notices.parse_line("  ") is None)
check("OCR noise still parses meso",
      notices.parse_line("You have gained mesos (+ 840 )") == ("meso", 840))

# ---- dedup / new_lines
prev = notices.Counter()
fresh, prev = notices.new_lines(prev, ["gained mesos (+100)", "gained mesos (+100)"])
check("two identical new lines both count once each",
      fresh == ["gained mesos (+100)", "gained mesos (+100)"] and prev["gained mesos (+100)"] == 2)
fresh2, prev = notices.new_lines(prev, ["gained mesos (+100)", "gained mesos (+100)"])
check("same lines still visible are not recounted", fresh2 == [])
fresh3, prev = notices.new_lines(prev, ["gained mesos (+100)", "gained an item (Leaf)"])
check("a newly appeared line is counted",
      fresh3 == ["gained an item (Leaf)"] and prev["gained an item (Leaf)"] == 1)
fresh4, _ = notices.new_lines(prev, [])
check("empty strip clears visible without new events", fresh4 == [])

# ---- pricing
loot = {"Pig's Head": 7, "Leaf": 11}
potion_sell = notices.potion_sell_prices(
    {"Red Potion": {"price": 50, "unpriced": False},
     "Mana Elixir": {"price": 0, "unpriced": True}},
    custom={"Mana Elixir": 1000})
check("potion sell is half shop buy", potion_sell.get("Red Potion") == 25)
check("custom buy becomes half sell", potion_sell.get("Mana Elixir") == 500)
check("match exact loot name", notices.sell_value("Pig's Head", loot) == 7)
check("fuzzy match OCR name", notices.sell_value("Pigs Head", loot) == 7)
check("unpriced returns None", notices.sell_value("Mystery Scroll", loot) is None)
check("load_loot_prices reads assets",
      notices.load_loot_prices(m.ASSETS / "loot_prices.json").get("Pig's Ribbon") == 10)

# ---- NoticeTracker rates
prices = {**loot, **potion_sell}
t = notices.NoticeTracker(prices, potion_names=set(potion_sell))
t.observe_lines(["You have gained mesos (+1000)", "You have gained an item (Red Potion)"],
                now=10, elapsed=0, running=True)
check("first look counts meso and loot sell value",
      t.mesos_gained == 1000 and t.loot_gained == 25 and t.gained == 1025
      and t.potion_gained == 25 and t.etc_gained == 0)
t.observe_lines(["You have gained mesos (+1000)", "You have gained an item (Red Potion)"],
                now=11, elapsed=1, running=True)
check("same visible lines are not double-counted", t.gained == 1025)
t.observe_lines(["You have gained mesos (+500)", "You have gained an item (Unknown Ore)"],
                now=12, elapsed=2, running=True)
check("new meso adds; unpriced item is listed",
      t.mesos_gained == 1500 and t.gained == 1525 and "Unknown Ore" in t.unpriced)
t.observe_lines(["You have gained experience (+99)"], now=13, elapsed=3, running=True)
check("EXP notice adds nothing", t.gained == 1525)
t.observe_lines(["Pig's Head", "You have gained an item in the Etc tab (Leaf)"],
                now=14, elapsed=4, running=True)
check("bare name and Etc-tab item add loot",
      t.items.get("Pig's Head") == 1 and t.items.get("Leaf") == 1
      and t.loot_gained == 25 + 7 + 11
      and t.etc_gained == 7 + 11 and t.potion_gained == 25)
_etc, _pots = notices.load_loot_tables(m.ASSETS / "loot_prices.json")
check("load_loot_tables splits etc and potions",
      _etc.get("Pig's Ribbon") == 10 and _pots.get("Red Potion") == 5)
total = 1500 + 25 + 7 + 11
# rates need 30 s of elapsed since start
check("per_hour is None before 30 s", t.per_hour(20) is None)
t.read_elapsed = 60
t.start = 0
check("per_hour after a minute", abs(t.per_hour(60) - total / 60 * 3600) < 1)
look = t.look()
check("look hands gained and seen_at", look == (total, 14))
check("value stays None (no inventory balance)", t.value is None)

t.pause()
t.observe_lines(["You have gained mesos (+999)"], now=20, elapsed=70, running=False)
check("paused looks don't earn", t.gained == total)
t.observe_lines(["You have gained mesos (+999)"], now=21, elapsed=71, running=True)
check("line still visible after pause isn't recounted", t.gained == total)

t.reset()
check("reset clears gained and unpriced", t.gained == 0 and t.unpriced == [] and t.seen_at is None)

# ---- feature_read_status tips for notices mode
rows = m.feature_read_status(
    exp={"ok": True},
    mesos={"mode": "notices", "seen_at": None},
    map_name={"name": "X"}, potions={"on": False}, now=0)
check("notices mode waits before first look",
      dict(r[:2] for r in rows)["Mesos"] == "wait"
      and "gain notices" in dict((r[0], r[2]) for r in rows)["Mesos"].lower())
rows = m.feature_read_status(
    exp={"ok": True},
    mesos={"mode": "notices", "seen_at": 1000, "value": None},
    map_name={"name": "X"}, potions={"on": False}, now=1005)
check("notices mode ok when fresh", dict(r[:2] for r in rows)["Mesos"] == "ok")
rows = m.feature_read_status(
    exp={"ok": True},
    mesos={"mode": "inventory", "value": None},
    map_name={}, potions={"on": False}, now=0)
check("inventory mode still asks to open inventory",
      "inventory" in dict((r[0], r[2]) for r in rows)["Mesos"].lower())

# ---- config default
cfg = m.load_config()
check("meso_mode defaults to gain notices", cfg.get("meso_mode") == "notices")

print()
if failures:
    print(f"{failures} failed")
    sys.exit(1)
print("all passed")
