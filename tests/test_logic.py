"""Tests for the tracking and logging logic (no game or screen needed).

Run from the project folder:  py -3 tests/test_logic.py
"""
import json
import math
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402
assert Path(os.environ["MAPLE_EXP_DATA"]) == m.DATA_DIR, "tests must run on their own temp data folder, never on real data"

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def new_log():
    folder = Path(tempfile.mkdtemp())
    return m.AutoLog(15, folder / "log.json", folder / "log.txt"), folder / "log.json"


def farm(log, t0, seconds, party=1, map_name="Rocky Road II", level=27, every=3, gap_at=None, gap=0, job=None, hidden=False,
         mesos_at=None, window_at=None, misreads=(), estimated=False):
    """100 EXP every `every` seconds (optionally with a pause); returns (end time, logs written).
    mesos_at(t) gives the last inventory look at time t, as the MesoTracker hands it on;
    window_at(t) when a shop, storage or trade window was last seen (or None)."""
    t, nxt, logged = t0, t0, []
    while t < t0 + seconds:
        paused = gap_at is not None and gap_at <= t - t0 < gap_at + gap
        if t >= nxt:
            if not paused:
                log.gain(t, 100)
            nxt = t + every
        look = mesos_at(t) if mesos_at else None
        seen = window_at(t) if window_at else None
        entry = log.tick(t, map_name, level, party, look, job, hidden=hidden, window_at=seen, misreads=misreads,
                         estimated=estimated)
        log.settle(t, True, look, misreads=misreads)
        if entry:
            logged.append(round(t - t0))
        t += 0.5
    return t, logged


# ---- the log
# ---- the minimap's name is hidden: nothing is saved under a map name that may be stale
log, _ = new_log()
t, _ = farm(log, 0, 290)
t, due = farm(log, t, 50, hidden=True)                       # the 5-minute mark falls inside the hidden stretch
t, _ = farm(log, t, 10, map_name="The Swamp of Despair I")   # the name is back: a different map
check("a stretch that comes due while the name is hidden isn't saved under the old map",
      not due and not log.entries and log.start is not None and log.block_map == "The Swamp of Despair I")

log, _ = new_log()
t, _ = farm(log, 0, 290)
t, due = farm(log, t, 50, hidden=True)
t, later = farm(log, t, 10)                                  # the name is back: the same map
check("...on the same map it is saved once the name is back, and the hidden time counts",
      not due and later and len(log.entries) == 1 and log.entries[0]["seconds"] >= 335
      and log.entries[0]["map"] == "Rocky Road II")

log, _ = new_log()
t, _ = farm(log, 0, 880)                                     # two 5-minute stretches are saved on the way
n = len(log.entries)
t, due = farm(log, t, 40, hidden=True)                       # the 15-minute run comes due while hidden
t, _ = farm(log, t, 10, map_name="The Swamp of Despair I")
check("a 15-minute run that comes due while the name is hidden isn't saved if the map turns out different",
      n == 1 and not log.runs)

log, _ = new_log()
t, _ = farm(log, 0, 880)
t, _ = farm(log, t, 40, hidden=True)
t, _ = farm(log, t, 10)
check("...but on the same map it is saved once the name is back", len(log.runs) == 1 and log.runs[0]["map"] == "Rocky Road II")

log, path = new_log()
_, logged = farm(log, 0, 660)
check(f"logs after 5 min of non-stop EXP, then updates at 10 min ({logged})", logged == [300, 600])
rate = log.entries[0]["exp"] / log.entries[0]["seconds"] * 3600
check(f"EXP/hr is right (120,000, got {rate:,.0f})", abs(rate - 120000) < 2000)

log, _ = new_log()
_, logged = farm(log, 0, 540, gap_at=180, gap=20)
check(f"a 20 s break restarts the 5-minute clock ({logged})", logged and 495 <= logged[0] <= 505)

log, _ = new_log()
_, logged = farm(log, 0, 330, gap_at=100, gap=10)
check(f"a 10 s break doesn't ({logged})", logged and 300 <= logged[0] <= 301)

log, _ = new_log()
t, a = farm(log, 0, 200, party=1)
t, b = farm(log, t, 400, party=4)
check("changing party size restarts the clock", not a and b and 300 <= b[0] <= 305)

log, _ = new_log()
t, _ = farm(log, 0, 310, level=27)
farm(log, t + 60, 310, level=28)
check("a new level gets its own line", len(log.entries) == 2)

a, path = new_log()
b = m.AutoLog(15, path, path.with_suffix(".txt"))
farm(a, 0, 310, party=5)
farm(b, 1000, 310, party=4)
check("two copies of the app can't erase each other's lines",
      sorted(e["party"] for e in json.loads(path.read_text())["entries"]) == [4, 5])

e = {"date": "2026-10-11", "map": "The Land of Wild Boar II", "level": 29, "party": 4,
     "exp": 51862, "seconds": 3600, "mesos": 32713, "mesos_seconds": 3600}
check("full line format", m.format_entry(e) ==
      "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - 4ppl party - 51,862exp/hr - 32,713mesos/hr - 1 h 00 min")
check("table row format", m.log_row(e)[1:] == ("The Land of Wild Boar II", 29, "", "4ppl", "1h 00m", "51,862", "32,713", "", ""))
check("the class is in the line", m.format_entry({**e, "class": "Bandit"}).startswith(
    "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - Bandit - 4ppl party"))

# ---- party sizes of one map share a row in the log
five = {**e, "party": 5, "exp": 50000, "seconds": 1800, "mesos": 10000, "mesos_seconds": 1800}
solo = {**e, "party": 1}
other_level = {**e, "party": 3, "level": 30}
other_day = {**e, "party": 3, "date": "2026-10-12"}
old_line = {k: v for k, v in e.items() if k not in ("party", "mesos", "mesos_seconds")}  # from before party tracking
groups = m.party_groups([five, solo, e, other_level, other_day, old_line])
shape = [[x["party"] if "party" in x else "-" for x in g] for _, g in groups]
check(f"a map's party sizes share a row, smallest first; solo, other levels and days don't ({shape})",
      shape == [[4, 5], [1], [3], [3], ["-"]])
row = m.group_row(groups[0][1])
check(f"the shared row is over all their time ({row[5:]})",
      row[4:] == ("4–5ppl ▸", "1h 30m", f"{(51862 + 50000) / 5400 * 3600:,.0f}", f"{(32713 + 10000) / 5400 * 3600:,.0f}", "", ""))
check("open, its arrow points down", m.group_row(groups[0][1], True)[4] == "4–5ppl ▾")
check("a party size under it shows just its own numbers",
      m.log_row(five, part=True) == ("", "", "", "", "5ppl", "30m", "100,000", "20,000", "", ""))
check("the lines themselves aren't changed", five["exp"] == 50000 and e["seconds"] == 3600)

# ---- class, seals and 15-minute runs
log, _ = new_log()
t, _ = farm(log, 0, 310, job="Bandit")
farm(log, t + 60, 310, job="Hunter")
check("a different class gets its own line", sorted(e["class"] for e in log.entries) == ["Bandit", "Hunter"])

log, path = new_log()
farm(log, 0, 310)
check("a line the tracker wrote is sealed", log.sealed(log.entries[0]) and not log.edited(log.entries[0]))
data = json.loads(path.read_text())
data["entries"][0]["exp"] *= 2                                   # someone edits the file by hand
path.write_text(json.dumps(data))
log = m.AutoLog(15, path, path.with_suffix(".txt"))
check("a line edited by hand is marked as edited", log.edited(log.entries[0]))
farm(log, 1000, 310)
check("more EXP added to an edited line doesn't make it trusted again",
      len(log.entries) == 1 and log.edited(log.entries[0]))
data["entries"].append(dict(data["entries"][0], level=28, seal="0" * 24))   # a made-up line
path.write_text(json.dumps(data))
check("a line added by hand isn't trusted either", m.AutoLog(15, path, path.with_suffix(".txt")).edited(
    [e for e in json.loads(path.read_text())["entries"] if e["level"] == 28][0]))
log, _ = new_log()
farm(log, 0, 310)
log.rename(log.entries[0], "Renamed Map")
check("renaming a map in the app keeps the line sealed (the name as read stays on record)",
      log.sealed(log.entries[0]) and log.entries[0]["map_read"] == "Rocky Road II")

log, path = new_log()
farm(log, 0, 950, job="Bandit")
runs = log.runs
check(f"15 minutes of non-stop farming is saved as a sealed run ({len(runs)})",
      len(runs) == 1 and abs(runs[0]["seconds"] - 900) < 2 and log.sealed(runs[0], m._SEALED_RUN)
      and abs(runs[0]["exp"] / runs[0]["seconds"] * 3600 - 120000) < 2000)
check("the log line counts its runs", log.runs_done("Rocky Road II", 1, "Bandit") == 1)
data = json.loads(path.read_text())
data["runs"][0]["exp"] *= 3                                      # a run edited by hand
path.write_text(json.dumps(data))
check("an edited run isn't counted", m.AutoLog(15, path, path.with_suffix(".txt")).runs_done(
    "Rocky Road II", 1, "Bandit") == 0)
log, _ = new_log()
farm(log, 0, 3650)
check(f"an hour of farming makes 4 runs, one after another ({len(log.runs)})",
      len(log.runs) == 4 and all(abs(r["seconds"] - 900) < 2 for r in log.runs))
log, _ = new_log()
t, _ = farm(log, 0, 500, level=30)
farm(log, t, 500, level=31)
check("a level-up doesn't stop a run (the run records both levels)",
      len(log.runs) == 1 and (log.runs[0]["level_start"], log.runs[0]["level_end"]) == (30, 31))
log, _ = new_log()
t, _ = farm(log, 0, 600, party=1)
farm(log, t, 600, party=4)
check("a party-size change restarts the run", len(log.runs) == 0)
log, _ = new_log()
farm(log, 0, 1000, gap_at=500, gap=20)
check("a break restarts the run", len(log.runs) == 0)
log, _ = new_log()
t, _ = farm(log, 0, 600)
farm(log, t, 600, map_name="The Swamp of Despair I")
check("a new map restarts the run", len(log.runs) == 0)
log, _ = new_log()
t, _ = farm(log, 0, 600, job="Bandit")
farm(log, t, 600, job="Assassin")
check("a class change restarts the run", len(log.runs) == 0)
log, _ = new_log()
t, _ = farm(log, 0, 600)
log.reset_clock()                                                # Pause (the app resets the log's clock)
farm(log, t, 600)
check("a pause restarts the run", len(log.runs) == 0)
# ---- hotkeys
check("hotkeys: Shift+1 to 4 by default", m.DEFAULT_HOTKEYS == {"start": "Shift+1", "reset": "Shift+2",
                                                               "skip": "Shift+3", "compact": "Shift+4"})
check("a hotkey needs Ctrl, Alt or Shift (or an F key), so it can't block plain typing",
      m.parse_hotkey("Shift+1") == (4, 0x31) and m.parse_hotkey("F5") == (0, 0x74)
      and m.parse_hotkey("A") is None and m.parse_hotkey("Win+1") is None and m.parse_hotkey("Shift+!") is None)
check("keys pressed in Settings get the right names", m.hotkey_text(False, False, True, 0x31) == "Shift+1"
      and m.hotkey_text(True, True, False, 0x5A) == "Ctrl+Alt+Z" and m.hotkey_text(False, False, False, 0x41) is None)
m.CONFIG_PATH.write_text(json.dumps({"hotkeys": {"start": "Ctrl+Q", "reset": "", "skip": "nonsense", "compact": 5},
                                     "ui_scale": 3, "compact": "yes"}))
cfg = m.load_config()
check(f"hotkeys from the settings file: kept if valid or off, else the default ({cfg['hotkeys']})",
      cfg["hotkeys"] == {"start": "Ctrl+Q", "reset": "", "skip": "Shift+3", "compact": "Shift+4"})
check("a size or compact setting edited by hand can't break the window", cfg["ui_scale"] == 100 and cfg["compact"] is False)
m.CONFIG_PATH.write_text(json.dumps({"compact_shape": "triangle", "seen_version": 5}))
cfg = m.load_config()
check("a compact shape that doesn't exist falls back to Square, and a damaged seen version to none",
      cfg["compact_shape"] == "square" and cfg["seen_version"] == "")
m.CONFIG_PATH.write_text(json.dumps({"compact_shape": "bar", "seen_version": "0.9.9-beta"}))
cfg = m.load_config()
check("a chosen compact shape and the last seen version are kept", cfg["compact_shape"] == "bar" and cfg["seen_version"] == "0.9.9-beta")
m.CONFIG_PATH.write_text(json.dumps({"shadow_reader": "no", "exp_v2_spots": {"1920x1080/11": [10, 900, 400, 1000]}}))
cfg = m.load_config()
check("shadow_reader is on unless a real false is saved; a good v2 spot is kept",
      cfg["shadow_reader"] is True and cfg["exp_v2_spots"] == {"1920x1080/11": [10, 900, 400, 1000]})
m.CONFIG_PATH.write_text(json.dumps({"shadow_reader": False}))
check("shadow_reader false is kept (the switch the developer can tell a player to use)", m.load_config()["shadow_reader"] is False)
for name, bad in (("not a dict", [1, 2]), ("a bad key", {"big": [1, 2, 3, 4]}), ("a short box", {"1920x1080/11": [1, 2, 3]}),
                  ("floats", {"1920x1080/11": [1, 2, 3, 4.5]}), ("too many", {f"{1000 + i}x700/9": [1, 2, 3, 4] for i in range(9)})):
    m.CONFIG_PATH.write_text(json.dumps({"exp_v2_spots": bad}))
    check(f"a damaged exp_v2_spots ({name}) is dropped", "exp_v2_spots" not in m.load_config())
# A07-1: a null is not "answered": the one-time questions must come back, not be skipped for good
for key in ("share_runs", "share_health", "health_id", "reports_sent", "exp_v2_spots"):
    m.CONFIG_PATH.write_text(json.dumps({key: None, "ui_scale": 150, "share_health" if key != "share_health" else "share_runs": True}))
    cfg = m.load_config()
    check(f"a null {key} in the settings file counts as missing (the question is asked again), other keys stay",
          key not in cfg and cfg["ui_scale"] == 150)
for name, bad in (("a string", "x"), ("a 2 MB string", "a" * 2_000_000), ("a list", [1]), ("negative n", {"day": "d", "n": -1}),
                  ("n is a float", {"day": "d", "n": 1.5}), ("n is a bool", {"day": "d", "n": True})):
    for key in ("share_runs", "share_health", "health_id", "reports_sent"):
        m.CONFIG_PATH.write_text(json.dumps({key: bad, "ui_scale": 150}))
        cfg = m.load_config()
        check(f"{key} damaged as {name} is dropped, ui_scale kept", key not in cfg and cfg["ui_scale"] == 150)
check(f"the right OS backend is made for {sys.platform} ({type(m.PLATFORM).__name__})",
      type(m.PLATFORM).__name__ == {"win32": "WindowsPlatform", "darwin": "MacPlatform"}.get(sys.platform))
try:
    m.create_platform("plan9")
    unsupported = False
except RuntimeError:
    unsupported = True
check("an OS the overlay doesn't run on is turned down clearly", unsupported)
check("the game isn't found when it isn't running (no crash looking)",
      m.PLATFORM.find_game_window() is None or m.PLATFORM.client_rect(m.PLATFORM.find_game_window()) is not None)
if sys.platform == "win32":
    from platforms import windows
    pressed = []
    hk = m.PLATFORM.create_hotkeys(pressed.append)
    hk.in_front = lambda: True                               # as if the game were in front
    hk.set({"start": "Ctrl+Alt+Shift+F12"})
    import time as _time
    _time.sleep(0.8)                                         # it registers within a quarter second
    ctypes_ok = windows._user32.PostThreadMessageW(hk.thread_id, 0x0312, 1, 0)   # as if the keys were pressed
    _time.sleep(0.3)
    check(f"a hotkey press reaches the overlay ({pressed}, problems: {hk.problems})", ctypes_ok and pressed == ["start"])
    hk.in_front = lambda: False                              # another program in front: off
    _time.sleep(0.6)
    windows._user32.PostThreadMessageW(hk.thread_id, 0x0312, 1, 0)
    _time.sleep(0.3)
    check("hotkeys are off while another program is in front", pressed == ["start"])
if sys.platform == "darwin":
    from platforms import macos
    W = macos.MacWindow
    check("on macOS the game is told apart from browsers and the overlay itself",
          macos.is_game(W(1, 10, "wine64-preloader", "MapleStory", 0, (0, 0, 800, 600), True), 99)
          and macos.is_game(W(2, 11, "MapleStory.exe", "", 0, (0, 0, 800, 600), True), 99)
          and not macos.is_game(W(3, 12, "Google Chrome", "MapleStory Classic guide", 0, (0, 0, 800, 600), True), 99)
          and not macos.is_game(W(4, 99, "Python", "MapleStory", 0, (0, 0, 800, 600), True), 99)
          and not macos.is_game(W(5, 13, "wine64-preloader", "MapleStory", 25, (0, 0, 800, 600), True), 99))
    check("macOS windows can be listed", isinstance(macos.window_list(), list))
check("release notes are shown as plain text, keeping names like exp_log.txt",
      m._plain_text("**New**\n- **Time** in `exp_log.txt`, see [README](https://x)") == "New\n• Time in exp_log.txt, see README")
check("how long each line was measured over", [m.fmt_logged(s) for s in (300.4, 1500, 3900)] == ["5 min", "25 min", "1 h 05 min"]
      and [m.fmt_logged(s, short=True) for s in (300.4, 1500, 3900)] == ["5m", "25m", "1h 05m"])

log, _ = new_log()
log.entries = [{"date": "2026-10-08", "map": "Kerning City", "level": 27, "exp": 1, "seconds": 1},
               {"date": "2026-10-08", "map": "Rocky Road II", "level": 27, "exp": 1, "seconds": 1}]
check("a misread map name is matched to the known one", log.canonical("Ketning City") == "Kerning City")
check("Rocky Road I and II stay different maps", log.canonical("Rocky Road I") == "Rocky Road I")
check("'Il' at the end is read as 'II'", m._clean_map_name("Rocky Road Il") == "Rocky Road II")
check("the level comes from the EXP table", m.level_for_req(63666) == 27 and m.level_for_req(60000) is None)

# ---- updating: logs and settings from every released version open with nothing lost
import shutil
real_config = m.CONFIG_PATH
HEALTH_FROM = (0, 9, 13)        # the first version that asks about health reports (its fixture has the answer)
vt = lambda name: tuple(int(x) for x in name.split("-")[0].split("."))
for old in sorted((Path(__file__).parent / "upgrade").iterdir()):
    folder = Path(tempfile.mkdtemp())
    shutil.copy2(old / "exp_log.json", folder / "log.json")
    before = json.loads((old / "exp_log.json").read_text(encoding="utf-8"))
    log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
    same = [all(a.get(k) == b.get(k) for k in ("date", "map", "level", "party", "exp", "seconds", "mesos", "class", "note"))
            for a, b in zip(before["entries"], log.entries)]
    m.CONFIG_PATH = old / "config.json"
    cfg = m.load_config()
    m.CONFIG_PATH = real_config
    check(f"updating from {old.name}: every log line, run, rename and setting is kept",
          len(log.entries) == len(before["entries"]) and all(same) and not any(log.edited(e) for e in log.entries)
          and len(log.runs) == len(before.get("runs", []))
          and all(log.sealed(r, m._SEALED_RUN) or r.get("estimated") for r in log.runs)      # (an estimated run's seal is meant not to verify)
          and log.aliases == before["aliases"] and not list(folder.glob("exp_log.damaged-*"))
          and (cfg["pos"], cfg["transparency"], cfg["gap_seconds"], cfg["topmost"]) == ([5300, 800], 30, 20, False))
    # the health question is still to come (nothing answers it for them), and an answer, once saved, is kept
    m.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
    shutil.copy2(old / "config.json", m.CONFIG_PATH)
    asked_before = ("share_health" not in m.load_config()) == (vt(old.name) < HEALTH_FROM)    # (or answered, from then on)
    cfg["share_health"] = True
    m.save_config(cfg)
    again = m.load_config()
    m.CONFIG_PATH = real_config
    check(f"updating from {old.name}: the health question is asked only if that version never asked it, and an answer is kept",
          asked_before and again["share_health"] is True and again["gap_seconds"] == cfg["gap_seconds"] and again["pos"] == cfg["pos"])
    # ...and carrying on farming afterwards, with the old lines (and any notes on them) left as they were
    farm(log, 0, 310, map_name="Updated Map", level=41)
    after = json.loads((folder / "log.json").read_text(encoding="utf-8"))["entries"]
    old_notes = {(e["date"], e["map"], e["level"], e.get("party")): e.get("note") for e in before["entries"]}
    now_notes = {(e["date"], e["map"], e["level"], e.get("party")): e.get("note") for e in after}
    check(f"farming after updating from {old.name} keeps every old line and note",
          all(k in now_notes and now_notes[k] == v for k, v in old_notes.items())
          and len(after) == len(before["entries"]) + 1)
check("the newest upgrade fixture has notes in it (so keeping them is really tested)",
      sum("note" in e for e in json.loads((Path(__file__).parent / "upgrade" / "0.9.12-beta" / "exp_log.json")
                                          .read_text(encoding="utf-8"))["entries"]) >= 3)
# 0.9.12-beta's fixture holds an estimated line and run (a bar with only a %): they survive an update and stay on this PC
folder = Path(tempfile.mkdtemp())
shutil.copy2(Path(__file__).parent / "upgrade" / "0.9.12-beta" / "exp_log.json", folder / "log.json")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
check("an estimated line and run from 0.9.12-beta are still marked after loading, and the run is never offered for sharing",
      any(e.get("estimated") for e in log.entries) and any(r.get("estimated") for r in log.runs)
      and all(not r.get("estimated") for r in log.unshared_runs()) and len(log.unshared_runs()) >= 1)


# ---- personal notes on log lines: only on this PC, and never lost
import inspect
log, path = new_log()
t, _ = farm(log, 0, 310, party=4)
e1 = log.entries[0]
updated = e1["updated"]
check("a line starts with no note", m.note_of(e1) == "" and m.log_row(e1)[9] == "" and "note" not in e1)
check("writing a note says it changed", log.set_note([e1], "  Great with a Priest.\r\nBring 200 pots \n") is True)
on_disk = json.loads(path.read_text(encoding="utf-8"))["entries"][0]
check(f"the note is in the log file, tidied up ({on_disk.get('note')!r})", on_disk.get("note") == "Great with a Priest.\nBring 200 pots")
e1 = log.entries[0]
check("the line is as trusted as before (a note isn't one of the sealed fields)", log.sealed(e1) and not log.edited(e1))
check("a note doesn't move the line in the log (it isn't an update to it)", e1["updated"] == updated)
check("the Note column shows a mark", m.log_row(e1)[9] == m.NOTE_MARK)
check("notes aren't in the log's copy or in copied lines",
      "Priest" not in path.with_suffix(".txt").read_text(encoding="utf-8") and "Priest" not in m.format_entry(e1)
      and "Priest" not in log.text_path.read_text(encoding="utf-8"))
log2 = m.AutoLog(15, path, path.with_suffix(".txt"))
check("the note is there after closing and opening the overlay", m.note_of(log2.entries[0]) == "Great with a Priest.\nBring 200 pots")
check("writing the same note again changes nothing", log2.set_note(log2.entries, "Great with a Priest.\nBring 200 pots") is False)
t, _ = farm(log2, t + 60, 310, party=4)
check("more farming on that line keeps its note (and adds to the line)",
      len(log2.entries) == 1 and m.note_of(log2.entries[0]) == "Great with a Priest.\nBring 200 pots"
      and log2.entries[0]["exp"] > e1["exp"] and log2.sealed(log2.entries[0]))
check("an empty note takes it off", log2.set_note(log2.entries, "  \n ") is True and "note" not in log2.entries[0])
check("a note can't be longer than the limit", (log2.set_note(log2.entries, "x" * 9000), len(log2.entries[0]["note"]))[1] == m.NOTE_MAX)
check("control characters and lone surrogates are taken out of notes, new lines and tabs kept",
      m.clean_note("a\x00b\x07c\ud800d\r\ne\tf\x7f") == "abcd\ne\tf" and m.clean_note(None) == "" and m.clean_note(5) == "")

# a map's party sizes shown as one line share one note
log, path = new_log()
t, _ = farm(log, 0, 310, party=4)
t, _ = farm(log, t + 60, 310, party=5)
four, five_ = sorted(log.entries, key=lambda e: e["party"])
check("two party sizes get one note together", log.set_note([four, five_], "Pots: 60 red") is True
      and [m.note_of(e) for e in log.entries] == ["Pots: 60 red"] * 2)
check("a line's party sizes with different notes show each once; the same note shows once",
      m.notes_of([{"note": "A"}, {"note": "B"}, {"note": "A"}, {}]) == "A\n\nB" and m.notes_of([{}, {"note": ""}]) == "")
check("a mark on the shared line if any of its party sizes has a note",
      m.group_row([{**four, "note": ""}, {**five_, "note": "Only on this one"}])[9] == m.NOTE_MARK
      and m.group_row([{**four, "note": "Only on this one"}, {**five_}])[9] == m.NOTE_MARK)
log.set_note([log.entries[0]], "")
log.set_note([log.entries[1]], "")
check("...and none when none do", m.group_row(log.entries)[9] == "")

# another copy of the overlay (or an older one) can't erase a note
a, path = new_log()
farm(a, 0, 310, party=2)
b = m.AutoLog(15, path, path.with_suffix(".txt"))               # a second copy, which doesn't know about the note
a.set_note(a.entries, "Written in the first copy")
farm(b, 5000, 310, party=3, map_name="Ant Tunnel I")
kept = {e["party"]: m.note_of(e) for e in json.loads(path.read_text(encoding="utf-8"))["entries"]}
check(f"a note written while another copy had the log open is kept ({kept})",
      kept == {2: "Written in the first copy", 3: ""})

# renaming a map so two lines become one keeps both notes
log, path = new_log()
t, _ = farm(log, 0, 310, map_name="Ant Tunnel I")
t, _ = farm(log, t + 60, 310, map_name="Ant Tunel I")             # the same map, misread
log.set_note([x for x in log.entries if x["map"] == "Ant Tunnel I"], "Mobs drop etc")
log.set_note([x for x in log.entries if x["map"] == "Ant Tunel I"], "Crowded at night")
log.rename([x for x in log.entries if x["map"] == "Ant Tunel I"][0], "Ant Tunnel I")
check(f"renaming two lines into one keeps both notes ({[m.note_of(x) for x in log.entries]})",
      len(log.entries) == 1 and m.note_of(log.entries[0]) == "Mobs drop etc\n\nCrowded at night"
      and log.sealed(log.entries[0]))
before = log.entries[0]
t, _ = farm(log, t + 60, 310, map_name="Other Map")
log.rename(log.entries[0], "Renamed Map") if log.entries[0]["map"] == "Other Map" else log.rename(log.entries[1], "Renamed Map")
check("renaming a map keeps its note", any(m.note_of(x) == "Mobs drop etc\n\nCrowded at night" for x in log.entries))
log.delete([x for x in log.entries if x["map"] == "Renamed Map"][0])
check("deleting a line deletes its note, and only its note",
      [m.note_of(x) for x in log.entries] == ["Mobs drop etc\n\nCrowded at night"])

# a log edited by hand, or written by a future version, with odd notes: nothing crashes, nothing is thrown away
folder = Path(tempfile.mkdtemp())
odd = [123, ["a", "b"], None, "x" * 50000, "a\x00b\ud800", {"text": "later versions might"}, ""]
lines = [{"date": "2026-10-1%d" % i, "map": "Map %d" % i, "level": 30, "party": 1, "exp": 5000, "seconds": 300,
          "note": n} for i, n in enumerate(odd)]
(folder / "log.json").write_text(json.dumps({"version": 3, "entries": lines}), encoding="utf-8")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
shown = [(m.note_of(x), m.log_row(x)[9]) for x in log.entries]
check("odd notes load, and can be shown", len(log.entries) == len(odd) and all(isinstance(n, str) and len(n) < 60000 for n, _ in shown)
      and not list(folder.glob("exp_log.damaged-*")))
log._change(lambda: None)
t, _ = farm(log, 0, 310, map_name="Another Map")
after = {x["map"]: x for x in json.loads((folder / "log.json").read_text(encoding="utf-8"))["entries"]}
check("odd notes are left exactly as they were when the log is saved for another reason",
      all("note" in after[f"Map {i}"] and after[f"Map {i}"]["note"] == n for i, n in enumerate(odd)))

# what's shared with the stats site is never made from notes
log, path = new_log()
farm(log, 0, 910, party=3)                                        # more than a 15-minute run
log.set_note(log.entries, "MY-PRIVATE-NOTE do not share")
sent = []
real_urlopen = m.urllib.request.urlopen


class _Reply:                                                     # what the stats site would answer
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self, _=None):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def capture(request, *a, **k):
    sent.append((request.full_url, request.data))
    return _Reply({"results": [{"started": r.get("started"), "ok": True} for r in log.runs]})


os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"                  # (the network is stubbed here)
m.urllib.request.urlopen = capture
done = m.post_runs("0" * 16, log.unshared_runs())
m.urllib.request.urlopen = real_urlopen
os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
body = sent[0][1].decode() if sent else ""
fields = {k for r in json.loads(body)["runs"] for k in r} if body else set()
check(f"what's sent to the stats site has the run and no note ({sorted(fields)})",
      len(sent) == 1 and "MY-PRIVATE-NOTE" not in body and "note" not in fields and len(json.loads(body)["runs"]) >= 1)
check("runs never carry a note, even with notes on the log lines",
      all("note" not in r for r in log.runs) and "MY-PRIVATE-NOTE" not in json.dumps(log.runs))
src = inspect.getsource(m)
check("privacy guard: the overlay reaches the internet in exactly these places (a new one needs a look at what it sends)",
      src.count("urlopen(") == 4 and src.count("webbrowser.open(") == 3 and "requests." not in src and "socket." not in src)
check("Send report never reads the log, notes or the map",
      not any(w in inspect.getsource(x) for x in (m.post_report, m.App.send_report, m.App._submit_report, m.App._report_details)
              for w in ("entries", "autolog", "note_of", "notes_of", '"note"', "map_name")))
check("the health report sender never reads the log, notes or the map", "entries" not in inspect.getsource(m.post_health)
      and "autolog" not in inspect.getsource(m.App._count_health) and "map" not in inspect.getsource(m.App._count_health).lower().replace("healthlog", ""))
check("what's sent never reads the log's lines, and the bug report never includes notes",
      "entries" not in inspect.getsource(m.post_runs) and "note" not in inspect.getsource(m.post_runs).lower()
      and "entries" not in inspect.getsource(m.check_for_update)
      and not any(w in inspect.getsource(m.App._report_text) for w in ("note_of", "notes_of", '"note"', "autolog.entries")))
runs = [dict(r, note="SNEAKY") for r in log.runs]
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
m.urllib.request.urlopen = capture
sent.clear()
m.post_runs("0" * 16, runs)
m.urllib.request.urlopen = real_urlopen
os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
check("even a run that somehow had a note field wouldn't send it", "SNEAKY" not in sent[0][1].decode())

# testing must never put made-up runs on the real site (it happened once, with a test launch)
sent.clear()
m.urllib.request.urlopen = capture
try:
    m.post_runs("0" * 16, log.runs)
    blocked = False
except RuntimeError:
    blocked = True
m.urllib.request.urlopen = real_urlopen
check("while testing (MAPLE_EXP_DATA is set) nothing can be sent to the real site", blocked and not sent)
check("no test fixture has sharing switched on (a launch on one would send its made-up runs)",
      not any(json.loads(f.read_text(encoding="utf-8")).get("share_runs") for f in (Path(__file__).parent / "upgrade").glob("*/config.json")))

check("no test fixture has health reports switched on (a launch on one would send made-up numbers)",
      not any(json.loads(f.read_text(encoding="utf-8")).get("share_health") for f in (Path(__file__).parent / "upgrade").glob("*/config.json")))
check("every upgrade fixture from before health reports is asked about them (none has an answer yet)",
      not any("share_health" in json.loads(f.read_text(encoding="utf-8")) for f in (Path(__file__).parent / "upgrade").glob("*/config.json")
              if vt(f.parent.name) < HEALTH_FROM))

# a copy of the log is kept for each day, so a recent one is always there
folder = Path(tempfile.mkdtemp())
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
t, _ = farm(log, 0, 310)
check("the first save has nothing to back up yet", not (folder / "backups").exists())
log.set_note(log.entries, "kept in the backup too")
today = folder / "backups" / f"exp_log-{m.date.today().isoformat()}.json"
check("the first save of a day keeps a copy of the log as it was", today.exists()
      and json.loads(today.read_text(encoding="utf-8"))["entries"][0].get("note") is None)
size = today.stat().st_size
log.set_note(log.entries, "changed again")
check("...only once a day", len(list((folder / "backups").glob("*.json"))) == 1 and today.stat().st_size == size)
for d in range(1, 10):                                            # nine older days
    (folder / "backups" / f"exp_log-2026-01-{d:02d}.json").write_text(json.dumps({"entries": [{"x": d}]}), encoding="utf-8")
today.unlink()
log.set_note(log.entries, "and again")
kept = sorted(x.name for x in (folder / "backups").glob("*.json"))
check(f"only the latest {m.BACKUPS_KEPT} days are kept ({len(kept)} files)",
      len(kept) == m.BACKUPS_KEPT and kept[-1] == today.name and "exp_log-2026-01-01.json" not in kept)
folder = Path(tempfile.mkdtemp())
(folder / "log.json").write_text('{"entries": [{"date": "2026', encoding="utf-8")        # damaged
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
farm(log, 0, 310)
check("a damaged log isn't taken for a backup (it has its own copy)",
      not (folder / "backups").exists() and len(list(folder.glob("exp_log.damaged-*.json"))) == 1)

# ---- damaged files
folder = Path(tempfile.mkdtemp())
(folder / "log.json").write_text('{"entries": [{"date": "2026-10-1', encoding="utf-8")   # cut off
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
farm(log, 0, 310)
check("a damaged log is copied before anything is saved over it",
      len(list(folder.glob("exp_log.damaged-*.json"))) == 1 and len(log.entries) == 1)
folder = Path(tempfile.mkdtemp())
good = {"date": "2026-10-11", "map": "Kerning City", "level": 27, "party": 1, "exp": 100, "seconds": 300}
(folder / "log.json").write_text(json.dumps({"version": 2, "entries": [good, {"map": 5}]}), encoding="utf-8")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
check("a damaged line is left out (with a copy kept), the rest is kept",
      log.entries == [good] and len(list(folder.glob("exp_log.damaged-*.json"))) == 1)
m.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": "15", "transparency": 400, "pos": "x",
                                     "log_size": [5], "mode": "region"}), encoding="utf-8")
cfg = m.load_config()
check("damaged settings fall back to the defaults",
      cfg["gap_seconds"] == 15 and cfg["transparency"] == m.MAX_TRANSPARENCY and "pos" not in cfg
      and "log_size" not in cfg and cfg["mode"] == "window")
m.CONFIG_PATH.write_text("[1, 2", encoding="utf-8")
check("an unreadable settings file gives the defaults", m.load_config()["topmost"] is True)

# ---- mesos
t = m.MesoTracker()
for v in (9820, 9820):
    t.observe(v, 0, 0, True)
check("the first count needs 3 readings that agree exactly", t.value is None)
for v in (9820, 9850, 9850):
    t.observe(v, 0, 0, True)
check("mesos picked up are counted", t.gained == 30)
t.observe(9999, 0, 0, True)
check("a one-off reading isn't counted yet", t.value == 9850)
for v in (820, 820, 820, 9880, 9880, 9880):
    t.observe(v, 0, 0, True)
check("a lost digit and its correction don't count as mesos gained", t.gained == 30 and t.value == 9880)
for v in (25000, 25000, 25000):
    t.observe(v, 0, 0, True)
check("selling loot (a big jump) doesn't count as farming", t.gained == 30)
for v in (250000,) * 7:
    t.observe(v, 0, 0, True)
check("an extra digit (10x) needs 8 readings in a row, not 3", t.value == 25000)
t = m.MesoTracker()
for v in (20000, 20000, 20000, 15001, 15001, 20000, 20000, 20040, 20040):
    t.observe(v, 0, 0, True)
check(f"a misread twice in a row and its correction cancel out (gained {t.gained})", t.gained == 40)

for before, after, gained in ((9, 10, 1), (99, 100, 1), (9999, 10000, 1),
                              (9000, 10000, 1000), (10000, 9000, -1000),
                              (9980, 10020, 40), (999999, 1000000, 1)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 2:
        t.observe(v, 0, 0, True)
    check(f"a digit rollover still needs 3 exact readings ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 60, 60, True)
    check(f"a small digit rollover counts the gain ({before} to {after}: {t.gained})",
          t.value == after and t.gained == gained and abs(t.per_hour(60) - gained * 60) < 1e-6
          and t.per_5min(60) == gained * 5)
    for v in (before,) * 3:
        t.observe(v, 60, 60, True)
    check(f"a digit rollover and its correction cancel out ({after} to {before})",
          t.value == before and t.gained == 0)

for before, after, nearby, gained in ((9980, 10020, 10010, 40), (10020, 9980, 9990, -40)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (nearby, after, nearby, after, after):
        t.observe(v, 0, 0, True)
    check(f"fluctuating readings across a digit boundary aren't enough ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 0, 0, True)
    check(f"a digit rollover counts once the readings agree exactly ({before} to {after})",
          t.value == after and t.gained == gained)

for before, after in ((0, 1), (0, 10), (9, 19), (99, 199), (9999, 19999),
                      (19999, 9999), (8, 10), (89, 100), (8999, 10000)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 3:
        t.observe(v, 0, 0, True)
    check(f"zero, extra or lost digits, and wider jumps still rebase ({before} to {after})",
          t.value == after and t.gained == 0)
    for v in (before,) * 3:
        t.observe(v, 0, 0, True)
    check(f"correcting a rebased count doesn't add mesos ({after} to {before})",
          t.value == before and t.gained == 0)

for before, after in ((9, 90), (99, 990), (9999, 99990), (10, 1), (100, 10), (10000, 1000)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 7:
        t.observe(v, 0, 0, True)
    check(f"a tenfold change still waits for 8 exact readings ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 0, 0, True)
    check(f"a confirmed tenfold change still rebases ({before} to {after})",
          t.value == after and t.gained == 0)

t = m.MesoTracker()
for v in (9980,) * 3:
    t.observe(v, 0, 0, True)
t.pause()
for v in (10020,) * 3:
    t.observe(v, 30, 0, False)
check("a digit rollover while paused isn't counted", t.value == 10020 and t.gained == 0)
t.observe(10020, 60, 0, True)
for v in (10030,) * 2:
    t.observe(v, 90, 30, True)
check("mesos picked up after resuming still count", t.value == 10030 and t.gained == 10)

# ---- mesos with the inventory closed most of the run: the first look is the baseline, and
# a look much later adds everything earned since, however big. Times are (seen, run) seconds.
def looks(t, value, when, n=3):
    for _ in range(n):
        t.observe(value, when, when, True)

t = m.MesoTracker()
looks(t, 100000, 0)
check("the first look sets the baseline and counts nothing", t.value == 100000 and t.gained == 0)
looks(t, 400000, 1800)
check(f"a look half an hour later adds all that was earned, even a big gain ({t.gained})",
      t.value == 400000 and t.gained == 300000)
check("mesos/hr covers the run up to the last look, not the time since", abs(t.per_hour(5400) - 600000) < 1)
check("mesos/hr stays put while the inventory stays closed", abs(t.per_hour(7200) - 600000) < 1)
check(f"the last 5 minutes is the steady rate between the looks ({t.per_5min(5400):.0f})",
      abs(t.per_5min(5400) - 50000) < 1)

t = m.MesoTracker()
looks(t, 99000, 0)
looks(t, 140000, 1200)
check(f"a gain that adds a digit counts after a long wait too ({t.gained})", t.value == 140000 and t.gained == 41000)

t = m.MesoTracker()
looks(t, 1234567, 0)
looks(t, 7234567, 1800, 2)
check("a big rise after a long wait still needs 3 identical looks (one digit misread twice)", t.value == 1234567)
looks(t, 7234567, 1800, 1)
check("...and counts once the third agrees", t.value == 7234567 and t.gained == 6000000)

t = m.MesoTracker()
looks(t, 400000, 0)
looks(t, 150000, 1800)
check("a big drop after a long wait is still not counted (a shop visit)", t.value == 150000 and t.gained == 0)

t = m.MesoTracker()
looks(t, 100000, 0)
looks(t, 1000000, 1800, 7)
check("a tenfold change after a long wait still needs 8 looks", t.value == 100000 and t.gained == 0)

t = m.MesoTracker()
looks(t, 100000, 0)
looks(t, 400000, 5)
check("a big jump seen at once (inventory open all the time) is still a shop visit",
      t.value == 400000 and t.gained == 0)

t = m.MesoTracker()
looks(t, 100000, 0)
looks(t, 400000, m.MESO_GAP + 1)
check("a big rise after the inventory was closed briefly is earned (not a shop visit)",
      t.value == 400000 and t.gained == 300000)
t = m.MesoTracker()
looks(t, 100000, 0)
looks(t, 400000, max(1, m.MESO_GAP - 1))
check("a big jump just before that wait is still a shop visit",
      t.value == 400000 and t.gained == 0)

# ---- mesos box remembered across window sizes, and scaled on a resize
check("scale_box moves a mesos box from 1024x768 to 2560x1440",
      m.scale_box((801, 473, 143, 14), (1024, 768), (2560, 1440)) == (2002, 887, 358, 26))
check("scale_box leaves bad sizes alone", m.scale_box((10, 10, 20, 12), (0, 768), (2560, 1440)) is None)
check("fmt_ago: under 3 s is 'just now'", m.fmt_ago(1.2) == "just now")
check("fmt_ago: seconds and minutes", m.fmt_ago(12) == "12s ago" and m.fmt_ago(125) == "2m ago")
rows = m.feature_read_status(
    exp={"ok": True}, mesos={"value": 100000, "seen_at": 1000}, map_name={"name": "Rocky Road II"},
    potions={"on": True, "ok": True}, now=1005)
check("feature_read_status: all green when everything is fresh",
      [r[1] for r in rows] == ["ok", "ok", "ok", "ok"])
rows = m.feature_read_status(
    exp={"looking": True}, mesos={"value": None}, map_name={}, potions={"on": False}, now=0)
check("feature_read_status: waiting / off when nothing is ready",
      [r[1] for r in rows] == ["wait", "wait", "wait", "off"])
rows = m.feature_read_status(
    exp={"ok": True}, mesos={"value": 100000, "seen_at": 0}, map_name={"name": "X"},
    potions={"on": True, "looking": True}, now=m.MESO_STALE_SECONDS + 5)
check("feature_read_status: mesos go stale after a minute closed",
      dict(r[:2] for r in rows)["Mesos"] == "stale")
check("good_mesos_boxes accepts a few WxH keys",
      m.good_mesos_boxes({"1024x768": [801, 473, 143, 14], "2560x1440": [2000, 880, 350, 26]}))
check("good_mesos_boxes rejects a bad key", not m.good_mesos_boxes({"1024": [1, 2, 3, 4]}))

m.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
m.CONFIG_PATH.write_text(json.dumps({
    "mesos_box": [1024, 768, 801, 473, 143, 14],
    "mesos_boxes": {"2560x1440": [2000, 880, 350, 26]},
}), encoding="utf-8")
cfg = m.load_config()
check("load_config keeps legacy mesos_box and mesos_boxes together",
      cfg["mesos_box"] == [1024, 768, 801, 473, 143, 14]
      and cfg["mesos_boxes"] == {"2560x1440": [2000, 880, 350, 26]})

# A ScreenReader remaps the mesos and EXP boxes when the game window changes size.
class FakePlatform:
    def __init__(self):
        self.rect = (0, 0, 1024, 768)
    def window_exists(self, hwnd):
        return True
    def client_rect(self, hwnd):
        return self.rect
    def find_game_window(self):
        return 1

fake = FakePlatform()
old_plat = m.PLATFORM
m.PLATFORM = fake
try:
    reader = m.ScreenReader(None)
    reader.hwnd = 1
    reader.size = (1024, 768)
    reader.mesos_box = (801, 473, 143, 14)
    reader.box = (400, 720, 200, 30)
    reader.saved_mesos_boxes = {"2560x1440": (2000, 880, 350, 26)}
    fake.rect = (0, 0, 2560, 1440)
    got = reader._game_rect()
    check("resize to a remembered size restores that mesos box",
          got == fake.rect and reader.mesos_box == (2000, 880, 350, 26)
          and reader.mesos_provisional is False
          and reader.box == m.scale_box((400, 720, 200, 30), (1024, 768), (2560, 1440)))
    fake.rect = (0, 0, 1920, 1080)
    reader.size = (2560, 1440)
    reader.mesos_box = (2000, 880, 350, 26)
    reader.box = (1000, 1350, 500, 56)
    reader.saved_mesos_boxes = {"2560x1440": (2000, 880, 350, 26)}
    got = reader._game_rect()
    check("resize to an unknown size scales the last mesos and EXP boxes provisionally",
          got == fake.rect
          and reader.mesos_box == m.scale_box((2000, 880, 350, 26), (2560, 1440), (1920, 1080))
          and reader.mesos_provisional is True
          and reader.box == m.scale_box((1000, 1350, 500, 56), (2560, 1440), (1920, 1080)))
finally:
    m.PLATFORM = old_plat

# ---- mesos moved at a shop, storage or trade window aren't earned: the change between the
# window opening and closing is left out, in both directions
def at_window(kind, start, steps):
    """A tracker with a good count of 150,000 at time 0, then a window opened at `start`, its
    first look at the same 150,000 and then the given counts (one per second). Returns it
    with the window still open."""
    t = m.MesoTracker()
    looks(t, 150000, 0)
    t.set_window(kind, start)
    looks(t, 150000, start, 2)                              # the first look in the window: nothing changed yet
    when = start + 1
    for value in steps:
        looks(t, value, when, 2)
        when += 1
    return t, when


for kind, steps, what in (("shop", (153000,), "a shop sale"), ("shop", (147000,), "a shop purchase"),
                          ("storage", (149000,), "a storage deposit"), ("storage", (160000,), "a storage withdrawal"),
                          ("trade", (145000,), "a completed trade")):
    t, when = at_window(kind, 600, steps)
    t.set_window(None, when + 4)
    check(f"{what} isn't counted ({t.gained})", t.gained == 0 and t.value == steps[-1])
    looks(t, steps[-1] + 250, when + 30, 2)
    check(f"{what}: mesos picked up afterwards still count", t.gained == 250)

t, when = at_window("trade", 600, (149000, 149000, 150000))
t.set_window(None, when + 4)
check("a trade offered then cancelled nets to nothing", t.gained == 0 and t.value == 150000)

t2 = m.MesoTracker()                                      # 400 was earned before the window opened
looks(t2, 100000, 0)
t2.set_window("shop", 600)
looks(t2, 100400, 600, 2)
check("what was earned before the window opened still counts", t2.gained == 400)
looks(t2, 103400, 601, 2)
t2.set_window(None, 700)
check("...and a sale in the window right after doesn't", t2.gained == 400 and t2.value == 103400)

t = m.MesoTracker()
looks(t, 100000, 0)
t.set_window("shop", 600)
t.set_window(None, 640)                                  # opened and closed without a single readable look
looks(t, 120000, 700, 3)
check("a window never read: the whole difference since the last look is dropped", t.gained == 0 and t.value == 120000)
looks(t, 120300, 760, 2)
check("...and counting goes on afterwards", t.gained == 300)

t, when = at_window("shop", 600, (153000,))
note = t.ignored_note(when + 5)
check(f"a note says why a jump was ignored ({note!r})", note and "3,000" in note and "shop" in note.lower())
check("...and it goes away after a while", t.ignored_note(when + 500) is None)

# ---- a pause makes the count stale: whatever changed meanwhile (a sale in town, a trade) can't
# be told from earnings, so the first look after resuming is the new baseline and counts nothing
t = m.MesoTracker()
looks(t, 100000, 0)
t.pause()
check("pausing makes the baseline stale", t.stale)
looks(t, 112000, 900, 3)                                  # sold things in town; the inventory was only seen now
check("a count taken after a pause counts nothing", t.gained == 0 and t.value == 112000 and not t.stale)
looks(t, 112400, 960, 2)
check("mesos picked up after that count again", t.gained == 400)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause()
t.set_window("shop", 600)
looks(t, 100000, 600, 2)                                  # seen in the shop while paused: first look
looks(t, 103000, 601, 2)                                  # sold something
t.set_window(None, 640)
check("a shop read exactly while paused keeps the baseline good", not t.stale and t.value == 103000)
looks(t, 103000, 700, 2)
looks(t, 103400, 760, 2)
check("...and only that sale is left out afterwards", t.gained == 400)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause()
check("no note while the baseline is stale but paused", t.stale_note(10, False) is None)
check("no note at first after resuming", t.stale_note(20, True) is None)
check("a note asks for the inventory once it has been stale a while",
      "inventory" in (t.stale_note(30, True) or "").lower())
looks(t, 100000, 31, 2)
check("...and goes away once it is read", t.stale_note(32, True) is None)

# ---- an idle pause (no EXP for a while, same map) isn't a town trip: with the inventory closed,
# what was earned before and after it is all still there to be counted at the next look
t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=False)
check("an idle pause doesn't make the count stale", not t.stale)
for _ in range(3):
    t.observe(130000, 600, 600, False)                    # looked at while paused: nothing is decided yet
check("a look while idly paused leaves the count alone", t.value == 100000 and t.gained == 0)
looks(t, 130000, 900, 3)                                  # tracking has carried on
check("the first look after an idle pause adds everything earned", t.gained == 30000 and t.value == 130000)

# ---- earnings before a trip to town are banked, not lost: reading the inventory right after a
# manual Pause on the grinding map, or the first window opened in town, counts what came before
def paused_reads(t, value, when, n=3):
    for _ in range(n):
        t.observe(value, when, when, False)                  # (tracking is paused)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=True, bank_inventory=True)
check("pausing with the inventory closed asks to open it now",
      "save your mesos" in (t.stale_note(60, False) or ""))
paused_reads(t, 130000, 61)
check(f"the inventory read right after pausing banks what was earned ({t.gained})", t.gained == 30000 and t.value == 130000)
check("...and the hint goes away", t.stale_note(70, False) is None)
paused_reads(t, 150000, 900)                              # in town the inventory shows a sale
check("a later read while paused is not earned", t.gained == 30000 and t.value == 150000)
looks(t, 150000, 1000, 2)
looks(t, 150300, 1060, 2)
check("after resuming only new mesos count again", t.gained == 30300)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=True, bank_inventory=False)                 # left the map
paused_reads(t, 130000, 900)
check("an inventory read in town does not bank anything", t.gained == 0 and t.value == 130000)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=True, bank_inventory=False)
t.set_window("shop", 900)
paused_reads(t, 130000, 900, 3)                           # the first look in the shop: 30,000 earned on the way
paused_reads(t, 133000, 901, 2)                           # a sale
t.set_window(None, 940)
check(f"the first window after a trip banks what came before it, not its own changes ({t.gained})",
      t.gained == 30000 and t.value == 133000 and not t.stale)
t.set_window("trade", 960)
paused_reads(t, 135000, 960, 2)
paused_reads(t, 134000, 961, 2)
t.set_window(None, 990)
check("a second window banks nothing", t.gained == 30000)
looks(t, 134000, 1000, 2)
looks(t, 134400, 1060, 2)
check("...and earnings after resuming count", t.gained == 30400)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=True, bank_inventory=True)
paused_reads(t, 130000, 61)
t.set_window("shop", 900)
paused_reads(t, 130000, 900, 2)
paused_reads(t, 160000, 901, 2)
t.set_window(None, 940)
check("a window after the inventory was banked banks nothing more", t.gained == 30000)

# what was left out is kept for the Details line
t, when = at_window("shop", 600, (153000,))
looks(t, 151000, when + 1, 2)
check(f"sold and spent are kept apart ({t.left_out})", t.left_out == [3000, 2000])
t.reset()
check("...and a reset clears them", t.left_out == [0, 0])

# resuming while a window is open must not leave the rates without a starting point
t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=True)
t.set_window("shop", 900)
paused_reads(t, 100000, 900, 3)
looks(t, 103000, 905, 2)                                  # tracking carried on in the shop; a sale
t.set_window(None, 940)
looks(t, 103000, 950, 2)
looks(t, 103600, 1100, 2)
try:
    rate = t.per_5min(1100)
    ok = True
except ZeroDivisionError:
    ok = False
check(f"the rates cope with resuming inside a window ({ok})", ok and t.gained == 600)

# mesos never go down by grinding: a drop at or right after a window is something bought there that
# wasn't read in time (the shop closed before the last look confirmed it), not negative earnings
t, when = at_window("shop", 600, (149000,))               # a purchase that was read
t.set_window(None, when + 4)
looks(t, 147000, when + 20, 2)                            # ...and one more that was only seen after closing
check(f"a drop right after a window isn't counted ({t.gained})", t.gained == 0 and t.value == 147000)
check("...and it is kept with what was left out", t.left_out[1] == 3000)
looks(t, 147300, when + 80, 2)
check("earnings after that count as usual", t.gained == 300)

t = m.MesoTracker()
looks(t, 150000, 0)
t.set_window("shop", 600)
looks(t, 140000, 600, 2)                                  # the first confirmed look is already after a purchase
check(f"a first look in a window that shows less isn't negative earnings ({t.gained})", t.gained == 0 and t.value == 140000)

t = m.MesoTracker()
looks(t, 100000, 0)
t.pause(stale=True)
t.set_window("shop", 900)
paused_reads(t, 130000, 900, 3)                           # (not banked: the first look shows a drop below)
t.set_window(None, 940)
t2 = m.MesoTracker()
looks(t2, 100000, 0)
t2.pause(stale=True)
t2.set_window("shop", 900)
paused_reads(t2, 95000, 900, 2)                           # bought something before the first confirmed look
check(f"a banked look that shows less than before isn't negative earnings ({t2.gained})", t2.gained == 0)

# while paused after a trip, every change is left out and shown, even a purchase that was already
# done by the time the window's first look was confirmed
t = m.MesoTracker()
looks(t, 150000, 0)
t.pause(stale=True, bank_inventory=False)
t.set_window("shop", 900)
paused_reads(t, 140100, 900, 3)                           # the first look: already 9,900 spent
check(f"a purchase seen at the first look while paused is shown as left out ({t.left_out})",
      t.gained == 0 and t.left_out[1] == 9900)
t.set_window(None, 940)
paused_reads(t, 139100, 960, 3)                           # something else bought later, in town
check("so is any other change while paused", t.left_out[1] == 10900 and t.gained == 0)

t = m.MesoTracker()
looks(t, 150000, 0)
t.pause(stale=True, bank_inventory=True)
paused_reads(t, 152000, 30)                               # the inventory right after pausing banks 2,000
t.set_window("shop", 900)
paused_reads(t, 142100, 900, 3)                           # the shop's first look is already 9,900 lower
check(f"after banking, a purchase at the shop's first look is still shown ({t.left_out})",
      t.gained == 2000 and t.left_out[1] == 9900)

# before tracking has started, the count just follows the inventory
t = m.MesoTracker()
for _ in range(3):
    t.observe(100000, 0, 0, False)
for _ in range(3):
    t.observe(100500, 10, 0, False)
check("mesos seen before Start aren't 'left out'", t.left_out == [0, 0] and t.value == 100500)

# ---- EXP outliers
def exp_tracker(e=47850, p=50.0):
    tr = m.ExpTracker(); tr.start()
    for _ in range(3):
        tr.observe([(e, p)], True)                       # Lv 30, 50%
    return tr

tr = exp_tracker()
for e in (86130, 86130, 47850, 47850, 47950, 47950):     # too high twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"the real number coming back after a misread isn't taken for a level-up "
      f"(gained {tr.gained:.0f}, levels {tr.levels})", tr.levels == 0 and tr.gained == 100)
tr = exp_tracker(95500, 99.79)
for e in (150, 150, 270):                                # a real level-up into Lv 31 (108,480)
    tr.observe([(e, int(e / 108480 * 10000) / 100)], True)
check(f"a real level-up counts, and the new level's EXP is known right away "
      f"(gained {tr.gained:.0f}, Lv {m.level_for_req(tr.req)})",
      tr.levels == 1 and tr.gained == 200 + 150 + 120 and tr.req == 108480)
tr = exp_tracker()
tr.observe([(0, 0.0)], True)                             # a misread 0
check("a misread 0 doesn't crash or count", tr.gained == 0)
tr = exp_tracker(95680, 99.97)
for e, p in ((32, 0.06), (32, 0.06), (32, 0.02), (32, 0.02), (152, 0.14)):   # 0.02% misread as 0.06%, twice
    tr.observe([(e, p)], True)
check(f"a level-up whose first readings are misread still counts right (gained {tr.gained:.0f}, levels {tr.levels})",
      tr.levels == 1 and tr.gained == 20 + 32 + 120)
tr = exp_tracker(76560, 80.0)
for e in (19140, 19140, 76660, 76660):                   # 80% misread as 20% twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"EXP never drops by more than a death costs: a big drop is ignored (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.levels == 0)
tr = exp_tracker()
for _ in range(10):
    tr.observe([(47850, 70.0)], False)                   # a misread that suggests another level
check("misreads can't change the level's EXP once it's known from the table", tr.req == 95700)

# ---- dying: EXP drops a few % of the level (Lv 30 needs 95,700)
def read(tr, e):
    return tr.observe([(e, int(e / 95700 * 10000) / 100)], True)

tr = exp_tracker()
for e in (48850, 48850):                                 # +1,000
    read(tr, e)
for e in (39280, 39280):                                 # dies: -10% (9,570)
    note = read(tr, e)[1]
check(f"dying isn't taken off the EXP gained (gained {tr.gained:.0f}, deaths {tr.deaths}, lost {tr.last_loss:.1f}%)",
      note == "died" and tr.gained == 1000 and tr.deaths == 1 and abs(tr.last_loss - 10) < 0.1)
read(tr, 39780)
check(f"after dying, EXP gained counts as usual (gained {tr.gained:.0f})", tr.gained == 1500)
tr = exp_tracker(479, 0.5)
for e in (0, 0):                                         # dies at 0.5%: EXP goes to 0
    read(tr, e)
check(f"dying with less EXP than it costs (down to 0) isn't counted either (gained {tr.gained:.0f})",
      tr.gained == 0 and tr.deaths == 1)
tr = exp_tracker()
for e in (43065, 47850):                                 # 5% lower once (a misread), then the real number
    read(tr, e)
check("a single low reading is ignored", tr.gained == 0 and tr.deaths == 0)
tr = exp_tracker()
for e in (43065, 43065, 47850, 47950):                   # the same low misread twice, then the real number
    read(tr, e)
check(f"a misread that looks like dying, then the real number: no EXP made up (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.deaths == 0)
tr = exp_tracker()
for e in (50721, 50721, 47850, 47950):                   # 3% too high twice, then the real number
    read(tr, e)
check(f"a misread a bit too high and the real number after it cancel out (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.deaths == 0)
log, _ = new_log()
log.gain(0, 100)
log.gain(1, -2000)                                       # more taken back than gained
for s in range(2, 1000):
    if s % 3 == 0:
        log.gain(s, 1)
    log.tick(s, "Rocky Road II", 27, 1)
check(f"no log line or run is ever made of EXP lost (lines: {[e['exp'] for e in log.entries]}, runs: {len(log.runs)})",
      [e["exp"] for e in log.entries] == [200] and log.runs == [])   # the 5-minute blocks after the loss


# ---- a bar that shows only a percentage: EXP is estimated from the level's EXP and the % (Lv 30 needs 95,700)
def pct_tracker(level=30, p=50.0, source="badge"):
    tr = m.ExpTracker(); tr.start()
    for _ in range(3):
        tr.observe_percent(p, level, source)
    return tr


tr = m.ExpTracker(); tr.start()
res = tr.observe_percent(50.0, None, None)
check(f"with no level there is nothing to estimate from, and it says so ({res})",
      res[0] is False and "level" in res[1] and tr.exp is None and tr.req is None)
check("a level out of the EXP table isn't guessed",
      m.ExpTracker().observe_percent(50.0, 150, "badge")[0] is False)
tr = pct_tracker()
check(f"a read level and a % give a baseline and an estimated EXP (EXP {tr.exp}, Lv {m.level_for_req(tr.req)})",
      tr.exp == 47850 and m.level_for_req(tr.req) == 30 and tr.estimated)
tr.observe_percent(51.0, 30, "badge")
tr.observe_percent(51.0, 30, "badge")
check(f"a % that moves up counts as the EXP it is worth (gained {tr.gained})", tr.gained == 957)
check("an estimate is marked as one, and an exact tracker isn't", exp_tracker().estimated is False)

tr = exp_tracker()                                       # the number was on the bar, then it goes (Lv 30, 50%)
for p in (51.0, 51.0, 52.0):
    note = tr.observe_percent(p, None, None)
check(f"when the number goes, the level it showed carries on, with no jump (gained {tr.gained}, EXP {tr.exp})",
      tr.gained == 957 and tr.exp == 49764 and tr.estimated)
tr.observe([(49800, 52.04)], True)
tr.observe([(49900, 52.14)], True)
check(f"...and when the number comes back, its rounding isn't EXP either (gained {tr.gained})",
      tr.gained == 957 + 100 and tr.estimated)

tr = pct_tracker()
for p in (38.0, 38.0):                                   # dies: -12% of the level
    note = tr.observe_percent(p, 30, "badge")[1]
check(f"dying on a %-only bar isn't taken off the EXP gained (note {note!r}, gained {tr.gained}, deaths {tr.deaths})",
      note == "died" and tr.gained == 0 and tr.deaths == 1)

# a level-up: the % wraps AND the next level is read; a wrap alone never makes one
tr = pct_tracker(30, 99.79)                              # 95,499 of 95,700
note = tr.observe_percent(0.3, 30, "badge")[1]
check(f"a % that wraps with the same level still showing waits for the new level (note {note!r})",
      "wrapped" in note and tr.levels == 0 and tr.exp == 95499)
check("...and with no level read at all it waits too", "wrapped" in tr.observe_percent(0.3, None, None)[1])
tr.observe_percent(0.3, 31, "badge")
res = tr.observe_percent(0.3, 31, "badge")
check(f"the next level read twice makes a level-up (gained {tr.gained}, levels {tr.levels}, EXP {tr.exp}, {res})",
      res == (True, "LEVEL UP!") and tr.levels == 1 and tr.gained == 201 + 325 and tr.exp == 325
      and tr.req == 108480)
tr.observe_percent(0.8, 31, "badge")
check(f"...and counting carries on in the new level (gained {tr.gained})", tr.gained == 201 + 325 + 868 - 325)
tr = pct_tracker(30, 99.79)
tr.observe_percent(0.3, 31, "badge")
tr.observe_percent(0.3, 30, "badge")
check("a new level seen only once isn't a level-up", tr.levels == 0)
tr = pct_tracker(30, 50.0)
tr.observe_percent(50.0, 32, "badge")
tr.observe_percent(50.0, 32, "badge")
check("a level that jumps by two without the % wrapping is no level-up", tr.levels == 0)

# a level that disagrees with the one in use, and forgetting it
tr = pct_tracker(30, 50.0, "typed")
tr.observe_percent(50.0, 31, "badge")
check(f"a read level replaces a typed one when nothing was counted yet (Lv {m.level_for_req(tr.req)}, {tr.level_source})",
      m.level_for_req(tr.req) == 31 and tr.level_source == "badge" and tr.exp == 54240)
tr = pct_tracker(30, 50.0)
tr.observe_percent(51.0, 30, "badge")
tr.observe_percent(51.0, 30, "badge")
res = tr.observe_percent(51.0, 31, "badge")
check(f"...but not once EXP was counted: it says the level changed (Lv {m.level_for_req(tr.req)}, {res})",
      res[0] is False and "level changed" in res[1] and m.level_for_req(tr.req) == 30 and tr.gained == 957)
tr.forget_level()
check("forgetting the level (another character) leaves nothing to estimate from",
      tr.req is None and tr.exp is None and tr.observe_percent(50.0, None, None)[0] is False)
tr = pct_tracker(30, 99.79)                              # the % wrapped, and the badge never shows the new level
for _ in range(45):
    note = tr.observe_percent(0.3, 30, "badge")[1]
check(f"a wrap the badge never explains doesn't stop tracking for good: it becomes the new start (note {note!r}, EXP {tr.exp})",
      tr.exp == 287 and tr.gained == 0 and tr.levels == 0)
tr.observe_percent(0.4, 30, "badge")
check("...and counting carries on from there", tr.gained == 96)
tr = exp_tracker()
tr.observe_percent(50.0, 30, "badge")
check("a read level that agrees with the carried-over one becomes its source", tr.level_source == "badge")
tr = m.ExpTracker()
tr.set_level(30, "typed")
tr.observe_percent(50.0, 30, "badge")
check("...and one that agrees with a typed one too", tr.level_source == "badge")
# ---- found by the audit
check("a big estimated number is shortened instead of overflowing, and a small one isn't",
      m.approx_num(99_999_999, True) == chr(0x2248) + "100.00M" and m.approx_num(1_234_567, True) == chr(0x2248) + "1,234,567"
      and m.approx_num(5000, False) == "5,000" and m.approx_num(None, True) == "\u2014")
tr = pct_tracker(10, 90.0)
for p in (95.0, 95.0):
    tr.observe_percent(p, 10, "badge")
for _ in range(2):
    tr.observe_percent(2.0, 11, "badge")
gained = tr.gained
tr.set_level(50, "typed")
for _ in range(3):
    tr.observe_percent(10.0, 50, "typed")
check(f"changing the level by hand right after a level-up counts nothing (gained {tr.gained}, was {gained})",
      tr.levels == 1 and tr.gained == gained)
tr = pct_tracker(10, 90.0)
for p in (95.0, 95.0):
    tr.observe_percent(p, 10, "badge")
for _ in range(2):
    tr.observe_percent(2.0, 11, "badge")
tr.forget_level()
try:
    for _ in range(3):
        tr.observe_percent(5.0, 12, "badge")
    tr.observe([(1000, 5.0)], True)
    survived = True
except TypeError:
    survived = False
check("forgetting the level right after a level-up never crashes the next readings", survived)
tr = pct_tracker(10, 96.0)
for p in (98.0, 98.0):
    tr.observe_percent(p, 10, "badge")
for _ in range(45):
    tr.observe_percent(3.0, None, None)
note = ""
for _ in range(3):
    note = tr.observe_percent(3.0, 11, "badge")[1]
check(f"a wrap the badge only explains late (after the wait) starts the new level, no 'level changed' for ever (Lv {m.level_for_req(tr.req)}, {note!r})",
      m.level_for_req(tr.req) == 11 and "level changed" not in note)
tr = pct_tracker(30, 99.79)
tr.observe_percent(0.3, 31, "badge")
tr.observe_percent(0.3, None, None)
res = tr.observe_percent(0.3, 31, "badge")
check(f"a badge that drops out for a look in between still confirms a level-up ({res}, levels {tr.levels})",
      res == (True, "LEVEL UP!") and tr.levels == 1)
tr = pct_tracker(30, 99.79)
tr.observe_percent(0.3, 31, "badge")
tr.observe_percent(0.3, 30, "badge")
tr.observe_percent(0.3, 31, "badge")
check("...but the old level shown in between does cancel it", tr.levels == 0)

tr = m.ExpTracker()
check("a typed level that has no EXP table is refused", tr.set_level(100, "typed") is False and tr.req is None)
check("a typed level is used like a read one (Lv 30, 'typed')",
      tr.set_level(30, "typed") and tr.observe_percent(50.0, None, None)[0] is True and tr.exp == 47850
      and tr.level_source == "typed" and tr.percent_mode)


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self, _=None):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


real_urlopen = m.urllib.request.urlopen
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"                  # (the network is stubbed here)
m.urllib.request.urlopen = lambda *a, **k: FakeResponse({"results": [
    {"started": "a", "ok": True}, {"started": "b", "ok": False, "why": "overlaps another run"},
    {"started": "c", "ok": False, "why": "too many runs today", "retry": True}]})
done = m.post_runs("0" * 16, [{"started": x} for x in "abc"])
m.urllib.request.urlopen = real_urlopen
os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
check(f"runs the site has, or turned down for good, aren't sent again; 'try tomorrow' ones are ({done})",
      done == ["a", "b"])

# ---- EXP tracking with noisy readings: misreads must never inflate the gains
random.seed(1)
reqs, worst = [54900, 63666, 73080], 0.0
for trial in range(60):
    tr = m.ExpTracker()
    tr.start()
    lvl, e, true_gain, counting = 0, random.randint(20000, 50000), 0, False
    for step in range(500):
        if random.random() < 0.3:
            g = random.choice([50, 100, 150])
            e += g
            if e >= reqs[lvl]:
                e, lvl = e - reqs[lvl], lvl + 1
            true_gain += g if counting else 0
        p = int(e / reqs[lvl] * 10000) / 100
        bad = int(str(e)[:-1] or 0) if random.random() < 0.1 else None       # a dropped digit
        ok, note = tr.observe([(bad, p)] if bad is not None else [(e, p)], bad is None)
        if note == "baseline":
            counting, true_gain = True, 0
    worst = max(worst, (tr.gained - true_gain) / max(true_gain, 1))
check(f"misread EXP never inflates the gain (worst over-count {worst:.1%})", worst <= 0.02)

# ---- reading the real EXP bar, captured at different game resolutions
import cv2  # noqa: E402
import numpy as np  # noqa: E402
tpl = m.DigitTemplates.load()
for f in sorted((Path(__file__).parent / "frames").glob("exp_*.png")):
    lay = m.find_exp(cv2.imread(str(f)))
    got = m.read_layout(tpl, lay) if lay else None
    check(f"reads the EXP bar at {f.stem[4:]} ({got})", got == (68601, 71.68))

from pics import percent_only, relabelled  # noqa: E402

for f in sorted((Path(__file__).parent / "frames").glob("exp_*.png")):
    pic = percent_only(cv2.imread(str(f)))
    lay = m.find_exp(pic)
    got = m.read_layout(tpl, lay) if lay else None
    check(f"finds the bar with only a percentage at {f.stem[4:]} ({got})", got == (None, 71.68))


for f in sorted((Path(__file__).parent / "frames").glob("exp_win*.png")):
    for text in ("", "HP", "MP", "LV"):        # (the game's real label always ends in a dot: "EXP.")
        check(f"a bare percentage labelled '{text}' is not the EXP bar at {f.stem[4:]}",
              m.find_exp(relabelled(cv2.imread(str(f)), text)) is None)
check("a reading that fits a level is trusted", m.fits_exp_table(68601, 71.68))
tr = m.ExpTracker()
tr.observe([(68601, 71.68)], True)
tr.observe([(68601, 71.68)], True)
check("knows the level from two clean readings, without waiting for EXP gains",
      tr.req == 95700 and m.level_for_req(tr.req) == 30)
tr = m.ExpTracker()
tr.observe([(1, 71.68)], False)
tr.observe([(1, 71.68)], False)
check("an untrusted reading doesn't set the level", tr.req is None)
check("'1 [71.68%]' (digits cut off) fits no level, so it isn't trusted", not m.fits_exp_table(1, 71.68))

check("this version has a What's new entry (it is shown once after updating)", m.VERSION in m.WHATS_NEW)

# ---- the window tests open their windows on the second screen, never on the main one
from screens import pick_second  # noqa: E402
SCREENS = [{"left": 5120, "top": 679, "width": 2560, "height": 1440}, {"left": 0, "top": 0, "width": 5120, "height": 2880}]
check("the second screen is the one that isn't the main screen (the one holding the origin)",
      pick_second(SCREENS) == SCREENS[0] and pick_second(SCREENS[::-1]) == SCREENS[0])
check("with one screen only there is no second one", pick_second(SCREENS[1:]) is None and pick_second([]) is None)
THIRD = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
check("with several others, a 1440p screen is preferred", pick_second([THIRD] + SCREENS) == SCREENS[0])
os.environ["MAPLE_TEST_SCREEN"] = "100,200,1280,720"
check("MAPLE_TEST_SCREEN (left,top,width,height) picks the screen by hand",
      pick_second(SCREENS) == {"left": 100, "top": 200, "width": 1280, "height": 720})
del os.environ["MAPLE_TEST_SCREEN"]

# ---- pausing by itself (a fake clock, so these run instantly)
clock = [10000.0]
m.time.monotonic = lambda: clock[0]
EXP0 = 60000


def session(gap=15):
    s = m.Session(gap)
    s.map_seen("Rocky Road II")
    for _ in range(2):                                  # learns the level (Lv 30)
        s.observe([(EXP0, round(EXP0 / 95700 * 100, 2))], True, clock[0])
    return s


def gains(s, seconds, every=3, map_name=None):
    """Kills worth 100 EXP every few seconds; the clock moves in half-second steps."""
    end, nxt = clock[0] + seconds, clock[0] + every
    while clock[0] < end:
        clock[0] += 0.5
        if clock[0] >= nxt:
            nxt += every
            e = s.exp.exp + 100
            s.observe([(e, int(e / 95700 * 10000) / 100)], True, clock[0])
        s.tick(clock[0])


def idle(s, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        clock[0] += 0.5
        s.tick(clock[0])


s = session()
s.start(clock[0])
gains(s, 30)
looks_at = lambda v: [s.meso.observe(v, clock[0], s.exp.elapsed(), s.exp.running) for _ in range(3)]
looks_at(150000)
idle(s, 40)
check("going idle doesn't make the mesos count stale", s.waiting == "idle" and not s.meso.stale)
check(f"no EXP for 15 s pauses by itself, without counting the idle time "
      f"({s.waiting}, {s.exp.elapsed():.0f} s)", s.waiting == "idle" and abs(s.exp.elapsed() - 30) < 0.6)
gains(s, 3.5)
check(f"the next EXP gain carries on (the gain that restarts it isn't counted: {s.exp.gained})",
      s.waiting is None and s.exp.running and s.exp.gained == 1000)
# a long AFK pause: next resume is a fresh run (totals from zero)
gained = s.exp.gained
idle(s, 40)
check("still idle after another short gap", s.waiting == "idle")
s.paused_at = clock[0] - m.PAUSE_RESTART_SECONDS - 1
before = s.exp.gained
gains(s, 3.5)
check(f"paused over 10 min: resume starts a new run (was {before}, now {s.exp.gained})",
      s.waiting is None and s.exp.running and s.exp.gained == 0 and s.fresh_restart is True
      and s.meso.gained == 0)
s.fresh_restart = False
# manual Pause for a long time, then Start: also a new run
gains(s, 12)
looks_at(180000)
s.pause(clock[0])
s.paused_at = clock[0] - m.PAUSE_RESTART_SECONDS - 5
clock[0] += 1
check("should_fresh_run after a long manual pause", s.should_fresh_run(clock[0]))
s.begin_fresh_run()
s.start(clock[0])
check("long manual pause + Start clears totals for a new run",
      s.exp.gained == 0 and s.exp.running and s.paused_at is None)
gained = s.exp.gained
s.map_seen("The Swamp of Despair I")
check("leaving the map pauses by itself", s.waiting == "map" and not s.exp.running)
s.map_seen("Rocky Road II")
gains(s, 6.5)
check("back on the same map, it carries on adding up", s.exp.running and s.exp.gained == gained + 100)

# a shop room has its own map name: going in pauses, coming back carries on, and the mesos stay put
s = session()
s.start(clock[0])
gains(s, 10)
s.meso.observe(150000, clock[0], s.exp.elapsed(), s.exp.running)
s.meso.observe(150000, clock[0], s.exp.elapsed(), s.exp.running)
s.meso.observe(150000, clock[0], s.exp.elapsed(), s.exp.running)
s.map_seen("Kerning City Pharmacy")
check("entering a shop room pauses and makes the count stale", s.waiting == "map" and s.meso.stale)
check("...and what was earned before leaving is only banked by a window, not the inventory", not s.meso.bank_inventory)
s.map_seen("Rocky Road II")
gains(s, 6.5)
check("coming out carries on without resetting the mesos", s.exp.running and s.meso.value == 150000)

# the same pausing and carrying on when the bar shows only a % (Lv 30 at 50%, +0.1% every 3 s)
sp = m.Session(15)
sp.map_seen("Rocky Road II")
for _ in range(2):
    sp.observe_percent(50.0, 30, "badge", clock[0])
sp.start(clock[0])
pct = 50.0
for step in range(60):                                   # 30 s
    clock[0] += 0.5
    if step % 6 == 5:
        pct = round(pct + 0.1, 2)
        sp.observe_percent(pct, 30, "badge", clock[0])
    sp.tick(clock[0])
check(f"a %-only bar is tracked like any other (gained {sp.exp.gained}, running {sp.exp.running})",
      sp.exp.running and sp.exp.gained == 957)
idle(sp, 20)
check("...pauses by itself when the % stops moving", sp.waiting == "idle" and not sp.exp.running)
pct = round(pct + 0.1, 2)
sp.observe_percent(pct, 30, "badge", clock[0])
check("...and carries on when it moves again", sp.waiting is None and sp.exp.running)

s2 = session()
s2.start(clock[0])
gains(s2, 10)
for _ in range(3):
    s2.meso.observe(150000, clock[0], s2.exp.elapsed(), s2.exp.running)
s2.pause()
check("a manual Pause on the grinding map lets the inventory bank what came before", s2.meso.bank_inventory)
s2.map_seen("Kerning City")
check("...but not after walking to another map", not s2.meso.bank_inventory)

s.map_seen("The Swamp of Despair I")
gains(s, 6.5)
check(f"EXP on a different map starts from zero, still knowing the level "
      f"(gained {s.exp.gained}, Lv {m.level_for_req(s.exp.req)})",
      s.map == "The Swamp of Despair I" and s.exp.gained == 100 and s.exp.req == 95700)
s.pause()
gains(s, 6.5)
check("after pressing Pause, it stays paused", not s.exp.running and s.on is False)
s = session(gap=60)
s.start(clock[0])
gains(s, 9.5)
idle(s, 40)
check("the pause time can be changed in Settings (60 s: a 40 s break doesn't pause)", s.exp.running)


# ---- the minimap's name can't be seen: pause rather than log under an old map name
s = session()
s.start(clock[0])
gains(s, 12)
gained = s.exp.gained
s.map_blind()
check("when the minimap's name has been out of sight, it pauses", s.waiting == "blind" and not s.exp.running)
gains(s, 12)
check("...and EXP that comes in meanwhile doesn't restart it (it could be another map)",
      s.waiting == "blind" and not s.exp.running and s.exp.gained == gained)
s.map_seen("Rocky Road II")
check("...seeing the same map again lets the usual rule carry on", s.waiting == "map")
gains(s, 6.5)
check("...at the next EXP gain", s.exp.running and s.waiting is None and s.map == "Rocky Road II")
s.map_blind()
s.map_seen("The Swamp of Despair I")
gains(s, 6.5)
check("...but a different map starts from zero", s.map == "The Swamp of Despair I" and s.exp.gained == 100)
s = session()
check("with Start never pressed, nothing pauses", (s.map_blind(), s.waiting)[1] is None)
s = session()
s.start(clock[0])
gains(s, 12)
idle(s, 40)
s.map_blind()
check("paused by being idle, then blind: it waits for the minimap too", s.waiting == "blind")

# ---- map names: the game's spelling, OCR slips, towns
check(f"the game's map list is bundled ({len(m.map_list().entries)} maps)", len(m.map_list().entries) > 300)
slips = {"ist Accompaniment <Bonus>": "1st Accompaniment <Bonus>", "1st Accompaniment <Bonus>": "1st Accompaniment <Bonus>",
         "The Land of Wild Boar Il": "The Land of Wild Boar II", "Rocky Road lI": "Rocky Road II",
         "Tree Dungeon Monkey Forest II": "Tree Dungeon Monkey Forest II", "Ant Tunnel l": "Ant Tunnel I"}
bad = {k: m.snap_map_name(m._clean_map_name(k)) for k, v in slips.items() if m.snap_map_name(m._clean_map_name(k)) != v}
check(f"OCR slips like 'ist' for '1st' and 'Il' for 'II' are put right ({bad})", not bad)
check("a map that isn't on the list (an event map) is kept as read", m.snap_map_name("Halloween Event Field") == "Halloween Event Field")
check("a different Roman numeral is a different map (not snapped)", m.snap_map_name("Rocky Road IV") == "Rocky Road IV")
check("towns are known", m.is_town("Perion") and m.is_town("Henesys") and m.is_town("Free Market <3>"))
check("...a hunting map with a town's name in it isn't a town", not m.is_town("Henesys Hunting Ground II") and not m.is_town("Perion Street Corner"))
check("...and an unknown name isn't", not m.is_town("Halloween Event Field") and not m.is_town(None))
check("a short, unclear name isn't forced onto a map", m.snap_map_name("Perlon X") == "Perlon X")


# ---- Roman numerals misread by OCR ("Vil" is VII), in the overlay's own cleaning
for read, want in {"Tree Dungeon Forest Up North Vil": "Tree Dungeon Forest Up North VII", "Tree Dungeon Forest Up North Vill": "Tree Dungeon Forest Up North VIII",
                   "Ant Tunnel lV": "Ant Tunnel IV", "Ant Tunnel il": "Ant Tunnel II", "Rocky Road iil": "Rocky Road III",
                   "Free Market 11": "Free Market 11", "Maple Mill": "Maple Mill", "Cave VX": "Cave VX"}.items():
    got = m._clean_map_name(read)
    check(f"'{read}' reads as '{want}' ({got})", got == want)

# ---- potion use: a slot's count going down while tracking runs
import potions  # noqa: E402


def bar(*items):
    """What the potion bar shows: (name, count) in slot order, four to a row."""
    return [potions.Slot(i % 4, i // 4, name, count) for i, (name, count) in enumerate(items)]


s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Lemon", 326)))
s.look_at_bar(bar(("Lemon", 325)))
check(f"Lemon 326 to 325 is exactly one Lemon used ({s.potions.used})", s.potions.used == {"Lemon": 1})

s = session()
s.start(clock[0])
gains(s, 30)
for n in (40, 39, 90, 89):
    s.look_at_bar(bar(("Red Potion", n)))
check(f"a count going up (buying, looting) isn't a use, and the slot starts from the new count ({s.potions.used})",
      s.potions.used == {"Red Potion": 2})

s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Orange", 20), ("Blue Potion", 100), ("Red Potion", 50)))
s.look_at_bar(bar(("Orange", 19), ("Blue Potion", 97), ("Red Potion", 50)))
check(f"several slots changing between two looks are each counted ({s.potions.used})",
      s.potions.used == {"Orange": 1, "Blue Potion": 3})

s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Red Potion", 100)))
s.pause()
s.look_at_bar(bar(("Red Potion", 90)))
s.start(clock[0])
gains(s, 3.5)
s.look_at_bar(bar(("Red Potion", 90)))
check(f"potions used while paused aren't counted ({s.potions.used})", not s.potions.used)
s.look_at_bar(bar(("Red Potion", 89)))
check(f"...and counting carries on from the count when tracking resumed ({s.potions.used})", s.potions.used == {"Red Potion": 1})

s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Red Potion", 100)))
s.map_seen("Henesys")                               # a town trip: tracking waits
s.look_at_bar(bar(("Red Potion", 300)))             # (bought a lot)
s.map_seen("Rocky Road II")
gains(s, 6.5)
s.look_at_bar(bar(("Red Potion", 300)))
s.look_at_bar(bar(("Red Potion", 298)))
check(f"a trip to town doesn't count: only what's used after carrying on ({s.potions.used})", s.potions.used == {"Red Potion": 2})

s = session()
gains(s, 10)
s.look_at_bar(bar(("Red Potion", 100)))
s.look_at_bar(bar(("Red Potion", 99)))
check("nothing counts before Start", not s.potions.used)
s.start(clock[0])
s.look_at_bar([])
s.look_at_bar(bar(("Scroll", None)))
check("a bar with no consumables (or no readable count) does nothing", not s.potions.used)

s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Red Potion", 100)))
s.look_at_bar(bar(("Red Potion", 6)))               # a misread, or a real flood of use?
s.look_at_bar(bar(("Red Potion", 100)))
check(f"a big drop that doesn't last is a misread, not use ({s.potions.used})", not s.potions.used)
s.look_at_bar(bar(("Red Potion", 60)))
s.look_at_bar(bar(("Red Potion", 60)))
check(f"a big drop that is seen again is use ({s.potions.used})", s.potions.used == {"Red Potion": 40})

s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Red Potion", 100), ("Orange Potion", 50), ("Blue Potion", 80), ("Mystery Food", 9)))
s.look_at_bar(bar(("Red Potion", 97), ("Orange Potion", 48), ("Blue Potion", 79), ("Mystery Food", 8)))
PRICES = {"Red Potion": 50, "Orange Potion": 150, "Blue Potion": 220}
check(f"mesos spent on potions: each kind at its own price, an item with no price costs 0 ({s.potions.cost(PRICES)})",
      s.potions.cost(PRICES) == 3 * 50 + 2 * 150 + 220)
check("no prices at all costs nothing", s.potions.cost({}) == 0)

# ---- potion prices: the shipped defaults, the player's own prices on top
ITEMS = {"Red Potion": {"price": 50, "unpriced": False}, "Blue Potion": {"price": 220, "unpriced": False},
         "Sap of Ancient Tree": {"price": 0, "unpriced": True}}
check("a price comes from the shipped table", potions.Prices(ITEMS, {}).get("Blue Potion") == 220)
check("an item that isn't in the table costs nothing", potions.Prices(ITEMS, {}).get("Mystery Food") == 0)
check("the player's own price wins", potions.Prices(ITEMS, {"Blue Potion": 180}).get("Blue Potion") == 180)
check("...and without it (cleared) the default is back", potions.Prices(ITEMS, {}).get("Blue Potion") == 220)
check("the player can price an item no NPC sells", potions.Prices(ITEMS, {"Sap of Ancient Tree": 900}).get("Sap of Ancient Tree") == 900)
check("prices as a name -> mesos map, for the cost", potions.Prices(ITEMS, {"Red Potion": 60}).mapping() ==
      {"Red Potion": 60, "Blue Potion": 220, "Sap of Ancient Tree": 0})
check("saved prices keep only whole, non-negative amounts for names",
      potions.clean_prices({"Red Potion": 70, "Blue Potion": "80", "A": -5, "B": 1.5, "C": True, "D": 0, 5: 3})
      == {"Red Potion": 70, "D": 0})
check("a damaged prices entry is nothing", potions.clean_prices([1, 2]) == {} and potions.clean_prices(None) == {})

used = {"Red Potion": 3, "Sap of Ancient Tree": 2}
check("a price of 0 the player set on an item NPCs sell, once it's used, asks for a price",
      potions.Prices(ITEMS, {"Red Potion": 0}).note(used) == "Set a price for Red Potion")
check("...but not before it's used", potions.Prices(ITEMS, {"Red Potion": 0}).note({}) is None)
check("an item with no NPC price used at 0 never asks (default or typed 0)",
      potions.Prices(ITEMS, {}).note(used) is None and potions.Prices(ITEMS, {"Sap of Ancient Tree": 0}).note(used) is None)
check("two items at 0 are both named",
      potions.Prices(ITEMS, {"Red Potion": 0, "Blue Potion": 0}).note({"Red Potion": 1, "Blue Potion": 1})
      == "Set a price for Red Potion, Blue Potion")

# ---- the settings that go with them (old config files still load)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": 20}))
cfg = m.load_config()
check("an old config has potions on and no prices of its own", cfg["potions_on"] is True and cfg["potion_prices"] == {})
m.CONFIG_PATH.write_text(json.dumps({"potions_on": False, "potion_prices": {"Red Potion": 70, "Blue Potion": "x", "A": -1}}))
cfg = m.load_config()
check("saved potion settings load, with unusable prices dropped", cfg["potions_on"] is False and cfg["potion_prices"] == {"Red Potion": 70})
m.CONFIG_PATH.write_text(json.dumps({"potions_on": "yes", "potion_prices": [1]}))
cfg = m.load_config()
check("damaged potion settings fall back to the defaults", cfg["potions_on"] is True and cfg["potion_prices"] == {})

# ---- potion cost in the local log
def farm_potions(log, t0, seconds, per_min=6, price=50, name="Red Potion", **kw):
    """Like farm, using per_min potions a minute (the tracker's running total goes to tick)."""
    t, nxt, logged = t0, t0, []
    while t < t0 + seconds:
        if t >= nxt:
            log.gain(t, 100)
            nxt = t + 3
        n = int((t - t0) / 60 * per_min)
        entry = log.tick(t, "Rocky Road II", 27, 1, None, None, potions=({name: n}, n * price), **kw)
        if entry:
            logged.append(round(t - t0))
        t += 0.5
    return t, logged


log, path = new_log()
farm_potions(log, 0, 305)
e = log.entries[0]
check(f"a log line has the potions used in it and what they cost ({e.get('potions')}, {e.get('potion_cost')})",
      e.get("potions") == {"Red Potion": 30} and e.get("potion_cost") == 1500)
check("...and is still sealed (the cost is part of the seal)", log.sealed(e) and not log.edited(e))
log2 = m.AutoLog(15, path, path.with_suffix(".txt"))
check("...also after saving and loading", log2.sealed(log2.entries[0]) and log2.entries[0]["potion_cost"] == 1500)
data = json.loads(path.read_text())
data["entries"][0]["potion_cost"] = 0                              # someone lowers the cost by hand
path.write_text(json.dumps(data))
check("a cost changed by hand is caught", m.AutoLog(15, path, path.with_suffix(".txt")).edited(
    json.loads(path.read_text())["entries"][0]))

# old logs (version 3, no potion cost) open, keep their seals, and take new stretches with a cost
log, path = new_log()
farm(log, 0, 305)
data = json.loads(path.read_text())
data["version"] = 3
check("(an old line has no potion cost)", "potion_cost" not in data["entries"][0])
path.write_text(json.dumps(data))
old_seal = data["entries"][0]["seal"]
log = m.AutoLog(15, path, path.with_suffix(".txt"))
check("an old line's seal still verifies after the update", log.sealed(log.entries[0]) and not log.edited(log.entries[0]))
check("...and the file is saved as the new version, the seal unchanged",
      json.loads(path.read_text())["version"] == m.LOG_VERSION and json.loads(path.read_text())["entries"][0]["seal"] == old_seal)
farm(log, 1000, 305)                                                # more of the same, no potions counted
check("a stretch without a cost leaves an old line as it was, sealed",
      len(log.entries) == 1 and "potion_cost" not in log.entries[0] and log.sealed(log.entries[0]))
farm_potions(log, 2000, 305)
check("a stretch with a cost joins the old line, which stays sealed",
      len(log.entries) == 1 and log.entries[0]["potion_cost"] == 1500 and log.sealed(log.entries[0]))
data = json.loads(path.read_text())
data["entries"][0]["exp"] *= 2
path.write_text(json.dumps(data))
check("an edited line with a cost is still caught", m.AutoLog(15, path, path.with_suffix(".txt")).edited(
    json.loads(path.read_text())["entries"][0]))

# potions stay on this PC
log, path = new_log()
farm_potions(log, 0, 910)
check("the 15-minute run has no potion data", log.runs and all(not any("potion" in k for k in r) for r in log.runs))
sent = []
m.urllib.request.urlopen = capture
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
m.post_runs("0" * 16, log.unshared_runs())
m.urllib.request.urlopen = real_urlopen
os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
body = sent[0][1].decode() if sent else ""
check("nothing about potions is sent to the stats site", len(sent) == 1 and "potion" not in body.lower()
      and "Red Potion" not in body)

# net mesos/hr in the log: mesos/hr minus what the potions cost per hour
pe = {"date": "2026-10-08", "map": "Rocky Road II", "level": 27, "party": 1, "class": "Bandit", "exp": 120000,
      "seconds": 3600, "mesos": 32713, "mesos_seconds": 3600, "potion_cost": 10000, "potions": {"Red Potion": 200}}
check(f"a log line with a cost says its net mesos/hr ({m.format_entry(pe)})", "32,713mesos/hr - 22,713net/hr" in m.format_entry(pe))
check("...and the table has a Net/hr cell", m.log_row(pe)[7:9] == ("32,713", "22,713"))
plain = {k: v for k, v in pe.items() if k not in ("potion_cost", "potions")}
check("a line without a cost has no net", "net/hr" not in m.format_entry(plain) and m.log_row(plain)[8] == "")
check("...a cost with no mesos reading has an unknown net", m.log_row({**pe, "mesos": 0, "mesos_seconds": 0})[8] == "?")
second = {**pe, "level": 28, "seconds": 1800, "mesos": 10000, "mesos_seconds": 1800, "potion_cost": 2000}
row = m.group_row([{**pe, "party": 4}, {**second, "party": 5}])
check(f"a map's party sizes together: net is over all their time ({row[8]})",
      row[8] == f"{(32713 + 10000 - 12000) / 5400 * 3600:,.0f}")

# ---- mesos in the log from sparse looks: the log gets every look, however old, and takes the
# mesos gained between the first and the last look of each stretch
t = m.MesoTracker()
check("no look yet: nothing to hand on", t.look() is None)
looks(t, 100000, 10)
looks(t, 105000, 200)
check(f"the last look is handed on however old it is ({t.look()})", t.look() == (5000, 200))


def sparse(first=(0, 1), last=(5000, 200)):
    """The inventory looked at twice, then closed: (gained, when read) as the log is given them."""
    return lambda t: None if t < first[1] else first if t < last[1] else last


log, _ = new_log()
_, logged = farm(log, 0, 302, mesos_at=sparse())
e = log.entries[0] if log.entries else {}
check(f"a log stretch with two looks minutes apart has the mesos between them ({e.get('mesos')}, {e.get('mesos_seconds')})",
      logged and e.get("mesos") == 5000 and e.get("mesos_seconds") == 199)
log, _ = new_log()
farm(log, 0, 302, mesos_at=sparse(last=(5000, 290)))
e = log.entries[0] if log.entries else {}
check("...the same as before when the last look is recent", e.get("mesos") == 5000 and e.get("mesos_seconds") == 289)
log, _ = new_log()
farm(log, 0, 302)
e = log.entries[0] if log.entries else {}
check("...and no look at all is no mesos", e.get("mesos") == 0 and e.get("mesos_seconds") == 0)
check("a line from sparse looks keeps its seal", log.sealed(e))

# ---- from the audit (2026-10-21)
t = m.MesoTracker()
looks(t, 100000, 0)
t.set_window("shop", 600)
looks(t, 100000, 600, 2)
looks(t, 400000, 601, 3)                                   # sold a run's loot: more than half the count at once
check(f"a big sale at a shop is left out, and said ({t.left_out}, {t.ignored_note(602)})",
      t.gained == 0 and t.left_out == [300000, 0] and "+300,000" in (t.ignored_note(602) or ""))
t, when = at_window("shop", 600, ())
looks(t, 153000, when, 1)                                  # a sale seen once, not yet confirmed...
t.set_window(None, when + 1)                               # ...when the window closes
looks(t, 153000, when + 2, 3)
check(f"a sale confirmed only after the window closed isn't earned ({t.gained})", t.gained == 0 and t.value == 153000)
looks(t, 153250, when + 40, 2)
check("...and what's picked up after it still counts", t.gained == 250)
s = session()
s.start(clock[0])
looks(s.meso, 100000, clock[0])
gains(s, 30)
clock[0] += 20
s.tick(clock[0])
s.map_seen("Perion")
check(f"an idle pause and then leaving the map makes the mesos count stale ({s.waiting}, {s.meso.stale})",
      s.waiting == "map" and s.meso.stale)

s = session()
s.start(clock[0])
gains(s, 30)
s.look_at_bar(bar(("Lemon", 100)))
s.look_at_bar([potions.Slot(1, 0, "Lemon", 100)])          # the stack dragged one slot to the right
s.look_at_bar([potions.Slot(1, 0, "Lemon", 95)])
s.look_at_bar(bar(("Lemon", 95)))                          # ...and back
s.look_at_bar(bar(("Lemon", 95)))
check(f"a stack moved and moved back counts only what was used ({s.potions.used})", s.potions.used == {"Lemon": 5})
s = session()
s.start(clock[0])
gains(s, 30)
for n in (326, 320, 326):
    s.look_at_bar(bar(("Lemon", n)))
check(f"a count misread lower for one look, then back, is no use ({s.potions.used})", sum(s.potions.used.values()) == 0)
s.look_at_bar(bar(("Lemon", 325)))
check("...a real use afterwards still counts", sum(s.potions.used.values()) == 1)

log, _ = new_log()
log._add("Rocky Road II", 27, 1, 1000, 305, 3050, 305, "Bandit", None)                 # potions off for this stretch
e = log._add("Rocky Road II", 27, 1, 1000, 305, 3050, 305, "Bandit", None, 1500, {"Red Potion": 30})
check(f"a line with a stretch without potions spreads the cost over the counted time only ({m.net_text(e)})",
      e["potion_seconds"] == 305 and m.net_text(e) == f"{6100 / 610 * 3600 - 1500 / 305 * 3600:,.0f}")
check("...and keeps its seal", log.sealed(e))
plain = {"date": "2026-10-08", "map": "Rocky Road II", "level": 27, "party": 1, "class": "Bandit", "exp": 1,
         "seconds": 1800, "mesos": 9000, "mesos_seconds": 1800}
costly = dict(plain, seconds=3600, mesos=36000, mesos_seconds=3600, potion_cost=3600)
check(f"a group of lines with and without a cost: the cost is per hour of the lines that had one ({m.group_row([costly, plain])[8]})",
      m.group_row([costly, plain])[8] == f"{45000 / 5400 * 3600 - 3600:,.0f}")

# ---- clean runs for the website: a run with a shop, storage or trade window, or a doubtful
# mesos number, never takes mesos to the site (the local log keeps them)
def steady(per_s=10, every=1, since=0, until=None):
    """The inventory open from `since` (to `until`): a look every `every` s, per_s mesos a second."""
    def at(t):
        if t < since:
            return None
        seen = min(math.floor(t / every) * every, until if until is not None else math.inf)
        return (round(per_s * seen), seen) if seen >= since else None
    return at


def sent_body(runs):
    sent.clear()
    m.urllib.request.urlopen = capture
    os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
    try:
        m.post_runs("0" * 16, runs)
    finally:
        m.urllib.request.urlopen = real_urlopen
        os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
    return sent[0][1].decode() if sent else ""


# 15 s without EXP ends a run, whatever the gap setting; the log lines still use the setting
folder = Path(tempfile.mkdtemp())
log = m.AutoLog(600, folder / "log.json", folder / "log.txt")
farm(log, 0, 1000, gap_at=300, gap=16)
check(f"with a 10-minute gap, 16 s without EXP ends the run ({len(log.runs)} runs)", len(log.runs) == 0)
check(f"...but the log lines carry on ({sum(e['seconds'] for e in log.entries):.0f} s logged)",
      sum(e["seconds"] for e in log.entries) >= 899)
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady())
r = log.runs[0] if log.runs else {}
check(f"a clean run with the inventory open is shared with its mesos ({r.get('mesos')}, {r.get('mesos_seconds')})",
      log.unshared_runs() == [r] and abs(r.get("mesos", 0) - 9000) <= 10 and r.get("mesos_seconds") == 900)
body = sent_body(log.unshared_runs())
check("...and what is sent has the same fields as before", json.loads(body)["runs"][0].keys() == {
    "date", "started", "map_read", "class", "level_start", "level_end", "party", "exp", "seconds", "mesos",
    "mesos_seconds", "version", "seal"})

# a shop, storage or trade window during the run
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(), window_at=lambda t: 400 if t >= 400 else None)
r = log.runs[0] if log.runs else {}
check(f"a run with a window seen is kept with its mesos ({r.get('mesos')}) and never shared",
      r.get("window") is True and r.get("mesos", 0) > 8000 and log.unshared_runs() == [])
check("...the flag isn't part of its seal", log.sealed(r, m._SEALED_RUN) and "window" not in m._SEALED_RUN)
check("...and isn't sent even if the run were", '"window"' not in sent_body([r]))
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(), window_at=lambda t: -5)
check("a window seen before the run started doesn't count", len(log.unshared_runs()) == 1)
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(), window_at=lambda t: 920 if t >= 920 else None)
r = log.runs[0] if log.runs else {}
check("...nor one seen after it ended", not r.get("window") and log.unshared_runs() == [r])
folder = Path(tempfile.mkdtemp())
data = {"version": 4, "install": "0123456789abcdef", "entries": [], "runs": []}
old_log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
old_log.install = data["install"]
old = {"date": "2026-10-01", "started": "2026-10-01T10:00", "map_read": "Rocky Road II", "class": "Bandit",
       "level_start": 27, "level_end": 27, "party": 1, "exp": 30000, "seconds": 900.0, "mesos": 9000,
       "mesos_seconds": 900.0, "version": "0.9.10-beta"}
old["seal"] = old_log._seal(old, m._SEALED_RUN)
data["runs"] = [old]
(folder / "log.json").write_text(json.dumps(data))
loaded = m.AutoLog(15, folder / "log.json", folder / "log.txt")
check("an old run (no flags) loads, keeps its seal and can still be shared", loaded.unshared_runs() == [old])

# ---- A13-1: the first %-only level (57) after the last numbered one (56), or a level-up from under 30%
tr = m.ExpTracker(); tr.start()
req56 = m.EXP_TABLE[55]
for f in (0.90, 0.91, 0.92, 0.93):
    tr.observe([(round(req56 * f), round(f * 100, 2))], True)
res = [tr.observe_percent(p, 57, "badge") for p in (0.5, 0.5, 0.6)]
check(f"the bar goes %-only at the level-up 56 -> 57: it is a level-up, not 'level changed' ({res}, Lv {m.level_for_req(tr.req)})",
      tr.levels == 1 and m.level_for_req(tr.req) == 57 and not any("level changed" in n for _, n in res))
tr = pct_tracker(56, 25.0)
tr.observe_percent(26.0, 56, "badge")
tr.observe_percent(26.0, 56, "badge")
res = [tr.observe_percent(5.0, 57, "badge") for _ in range(3)]    # (a big EXP gain: the % only fell by 21 points)
check(f"a level-up that leaves a % less than 30 points lower still counts ({res[-1]}, levels {tr.levels})",
      tr.levels == 1 and m.level_for_req(tr.req) == 57)
tr = pct_tracker(56, 25.0)
tr.observe_percent(26.0, 56, "badge")
res = tr.observe_percent(10.0, 56, "badge")
check(f"...but a % that falls with the same level is still no level-up ({res})", tr.levels == 0)

# ---- EXP estimated from a % alone stays on this PC: marked in the log, never shared
log, path = new_log()
t, _ = farm(log, 0, 1000, estimated=True)
check(f"runs of estimated EXP are saved, marked, and never shared ({len(log.runs)} runs)",
      log.runs and all(r.get("estimated") for r in log.runs) and log.unshared_runs() == [])
check("...an older overlay can't share them either (their seal doesn't verify)",
      all(not log.sealed(r, m._SEALED_RUN) for r in log.runs))
check("...and the 5-minute log lines say they are estimates", log.entries and all(e.get("estimated") for e in log.entries))
log.save()
again = m.AutoLog(15, path, path.with_suffix(".txt"))
check("...all of it survives saving and loading", again.runs and all(r.get("estimated") for r in again.runs)
      and again.entries and all(e.get("estimated") for e in again.entries) and again.unshared_runs() == [])
log, _ = new_log()
t, _ = farm(log, 0, 200)
t, _ = farm(log, t, 800, estimated=True)                     # exact for a while, then only a %
check("a run that is estimated for even part of its time isn't shared", log.runs and log.unshared_runs() == [])
log, _ = new_log()
t, _ = farm(log, 0, 1000)
check("exact runs are still shared as before", log.runs and len(log.unshared_runs()) == len(log.runs)
      and not any(e.get("estimated") for e in log.entries))
log, _ = new_log()
t, _ = farm(log, 0, 1100, estimated=True, mesos_at=lambda t: (round(t * 10), t))
check("an estimated run never waits for a mesos look, and keeps the seal an older overlay can't use",
      log.runs and all(not r.get("pending") and r["seal"] == "estimated" for r in log.runs) and log.unshared_runs() == [])
log, _ = new_log()
t, _ = farm(log, 0, 1000, estimated=True)
r = log.runs[0]
check(f"A13-3: estimated runs still count as done runs ({log.runs_done(r['map'], r['party'], r['class'])} of {len(log.runs)})",
      log.runs_done(r["map"], r["party"], r["class"]) == len(log.runs))
line = {"date": "2026-10-08", "map": "Rocky Road II", "level": 60, "party": 1, "class": "Bandit", "exp": 4000,
        "seconds": 1800, "mesos": 0, "mesos_seconds": 0}
check("an estimated line says so in its row and in the copied text",
      m.log_row(dict(line, estimated=True))[6] == "≈" + m.log_row(line)[6]
      and "≈8,000exp/hr" in m.format_entry(dict(line, estimated=True)))
check("an exact line shows no approx sign", "≈" not in m.log_row(line)[6] and "≈" not in m.format_entry(line))
check("party sizes merged into one line stay marked if any of them was estimated",
      m.log_row(m.merged([dict(line, party=3), dict(line, party=4, estimated=True)]))[6].startswith("≈")
      and not m.log_row(m.merged([dict(line, party=3), dict(line, party=4)]))[6].startswith("≈"))

# mesos for the site only from a look at the start and one within 30 s after the end
log, _ = new_log()
farm(log, 0, 935, mesos_at=sparse(first=(0, 0), last=(9150, 915)))
r = log.runs[0] if log.runs else {}
check(f"looks at the start and 15 s after the end: mesos spread evenly ({r.get('mesos')})",
      r.get("mesos") == 9000 and r.get("mesos_seconds") == 900)
log, _ = new_log()
farm(log, 0, 935, mesos_at=sparse(first=(0, 0), last=(9400, 940)))
r = log.runs[0] if log.runs else {}
check(f"no look within 30 s after the end: EXP only ({r.get('mesos')})",
      r.get("mesos") == 0 and r.get("mesos_seconds") == 0 and log.unshared_runs() == [r])
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(since=300))
r = log.runs[0] if log.runs else {}
check("no look at the start: EXP only", r.get("mesos") == 0 and r.get("mesos_seconds") == 0 and log.unshared_runs() == [r])
check("...but the log lines still show the mesos between the looks", sum(e["mesos"] for e in log.entries) > 0)
log, _ = new_log()
farm(log, 0, 2735, mesos_at=sparse(first=(0, 0), last=(27020, 2702)))
mesos = [r.get("mesos") for r in log.runs]
check(f"45 minutes on with looks only at the start and the end: the last run gets its share ({mesos})",
      len(mesos) == 3 and mesos[:2] == [0, 0] and abs(mesos[2] - 9000) <= 10)
log, _ = new_log()
farm(log, 0, 935, mesos_at=lambda t: (-round(t * 10), math.floor(t)))
r = log.runs[0] if log.runs else {}
check("negative mesos: EXP only", r.get("mesos") == 0 and r.get("mesos_seconds") == 0)
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(per_s=6000))
r = log.runs[0] if log.runs else {}
check(f"more than 20M mesos/hr: EXP only ({r.get('mesos')})", r.get("mesos") == 0)
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(per_s=5000))
r = log.runs[0] if log.runs else {}
check(f"...18M/hr still goes ({r.get('mesos')})", abs(r.get("mesos", 0) - 4500000) <= 5000)
log, path = new_log()
farm(log, 0, 935, mesos_at=steady(), level=None)
check("an unknown level: kept on this PC, never offered for sharing",
      len(log.runs) == 1 and log.unshared_runs() == [] and m.AutoLog(15, path, path.with_suffix(".txt")).unshared_runs() == [])

# snap back: a count that goes back to the one before it proves it was a misread
t = m.MesoTracker()
looks(t, 100000, 0)
looks(t, 105000, 10)
looks(t, 100000, 20)
check(f"100,000 -> 105,000 -> 100,000 with no window: the 105,000 was a misread ({t.misreads})",
      len(t.misreads) == 1 and t.misreads[0][0] <= 10 <= t.misreads[0][1] < 20)
t = m.MesoTracker()
for i, v in enumerate((100000, 105000, 110000, 104000)):
    looks(t, v, i * 10)
check("earning (and a drop that isn't back to the count before) is no snap back", t.misreads == [])
t = m.MesoTracker()
looks(t, 100000, 0)
t.set_window("shop", 5)
looks(t, 90000, 10)
looks(t, 100000, 20)
check("buying and selling back at a shop is no snap back", t.misreads == [])
check("the tracker hands the log its looks, windows, misreads and resets",
      set(t.for_log()) == {"mesos", "window_at", "misreads", "meso_epoch"} and t.for_log()["window_at"] == 5)
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(), misreads=[(899.5, 903)])
r = log.runs[0] if log.runs else {}
check(f"a run whose end look was a misread: EXP only ({r.get('mesos')})", r.get("mesos") == 0)
check("...the log lines keep their mesos", sum(e["mesos"] for e in log.entries) > 0)
log, _ = new_log()
farm(log, 0, 935, mesos_at=steady(), misreads=[(400, 403)])
r = log.runs[0] if log.runs else {}
check(f"a misread the run doesn't use leaves it alone ({r.get('mesos')})", abs(r.get("mesos", 0) - 9000) <= 10)
log, _ = new_log()
log.tick(0, "Rocky Road II", 27, 1, (500, 0), meso_epoch=1)
log.tick(1, "Rocky Road II", 27, 1, (0, 1), meso_epoch=2)
check("after the mesos count starts over, older looks are forgotten", log.looks == [(1, 0)])

# a finished run waits for a look after its end
log, _ = new_log()
farm(log, 0, 905, mesos_at=steady())
r = log.runs[0] if log.runs else {}
check("a run waiting for its look isn't offered for sharing yet", r.get("pending") and log.unshared_runs() == [])
check("...nor while tracking goes on is anything asked", log.alert(905, True) is None)
farm(log, 905, 30, mesos_at=steady())
check("...after 30 s it goes, with its mesos", not log.runs[0].get("pending") and log.unshared_runs()[0]["mesos"] == 9000)


def paused_after_run(last_look=(0, 0)):
    """15 minutes with one look at the start, then Pause right after the run ended."""
    log, path = new_log()
    t, _ = farm(log, 0, 902, mesos_at=lambda t: last_look)
    log.reset_clock()
    return log, path, t


log, _, t = paused_after_run()
check("paused while a run waits: asks for the inventory", log.alert(t, False) == m.RUN_ALERT)
log.settle(t, False, (0, 0))
done = log.settle(t + 3, False, (9030, t + 3))
r = log.runs[0]
check(f"...a look within 7 s sends it with mesos ({r.get('mesos')}) and the message goes",
      done and r.get("mesos") > 8900 and log.alert(t + 3, False) is None)
log, _, t = paused_after_run()
log.settle(t, False, (0, 0))
early = log.settle(t + 6, False, (0, 0))
done = log.settle(t + 7.5, False, (0, 0))
check("...no look within 7 s: it goes with EXP only and the message goes",
      not early and done and log.runs[0]["mesos"] == 0 and log.unshared_runs() and log.alert(t + 8, False) is None)
log, _ = new_log()
t, _ = farm(log, 0, 902, mesos_at=steady())
check("paused with the inventory open: no message", log.alert(t, False) is None)
log, _ = new_log()
t, _ = farm(log, 0, 902, mesos_at=steady(), window_at=lambda t: 400 if t >= 400 else None)
check("never asked for a run that wasn't clean", log.pending is None and log.alert(t, False) is None)
log, path, t = paused_after_run()
reopened = m.AutoLog(15, path, path.with_suffix(".txt"))
r = reopened.runs[0] if reopened.runs else {}
check("closed while a run waits: it isn't lost, it goes with EXP only",
      reopened.unshared_runs() == [r] and r.get("mesos") == 0 and "pending" not in r)

# ---- clean runs, from the audit
log, _ = new_log()
t, _ = farm(log, 0, 905, mesos_at=lambda t: (0, 0), window_at=lambda t: 903 if t >= 903 else None)
log.settle(910, True, (500000, 910), window_at=903)     # sold at a shop, then the first look after the run
farm(log, 910, 30, mesos_at=lambda t: (500000, 910))
r = log.runs[0] if log.runs else {}
check(f"a shop seen while a run waits for its end look: that look isn't believed, EXP only ({r.get('mesos')})",
      r.get("mesos") == 0 and not r.get("pending") and log.unshared_runs() == [r])
log, _, t = paused_after_run()
log.settle(t, False, None, meso_epoch=5)                 # Reset (or a new map): the mesos count starts over
check("a run waiting when the mesos count starts over goes at once, EXP only, without asking",
      log.pending is None and log.runs[0]["mesos"] == 0 and log.alert(t, False) is None)
log, _ = new_log()
for i in range(m.MAX_LOOKS + 500):
    log.gain(i, 100)
    log.tick(i, None, 27, 1, (i, i))                     # (no map yet: the run can't start, looks pile up)
check(f"the looks kept are capped ({len(log.looks)})", len(log.looks) <= m.MAX_LOOKS + 1)
log, _ = new_log()
farm(log, 0, 935, mesos_at=lambda t: None if t < 6 else (round(t * 10), math.floor(t)))
r = log.runs[0] if log.runs else {}
check(f"a first look a few seconds after the start (a new map) still gives mesos, a little under ({r.get('mesos')})",
      8900 <= r.get("mesos", 0) < 9000)
log, _ = new_log()
farm(log, 0, 935, mesos_at=lambda t: None if t < 12 else (round(t * 10), math.floor(t)))
r = log.runs[0] if log.runs else {}
check("...but not one more than 10 s after it", r.get("mesos") == 0)
log, _ = new_log()
farm(log, 0, 935, mesos_at=lambda t: (0, -40) if t < 900 else (9050, 905))
r = log.runs[0] if log.runs else {}
check("a start look from over 30 s before the farming began isn't used", r.get("mesos") == 0)
log, _, t = paused_after_run()
log.settle(t, False, (0, 0))
log.settle(t + 2, True, (0, 0))                           # tracking again before the 7 s ran out
check("resumed while a run waits: back to waiting the full 30 s, no message",
      log.pending and log.pending["paused_at"] is None and log.alert(t + 2, True) is None)
early = log.settle(t + 10, True, (9100, t + 10))
done = not early and log.settle(931, True, (9100, t + 10))
check("...and a look within 30 s of the end still gives it mesos", done and log.runs[0]["mesos"] > 8900)

# errors.log: an error whose message changes every time (a number, a path) is still written once a minute per kind,
# and the memory of what was written doesn't grow with every message (A08-2)
m._last_errors.clear()
before = m.ERROR_LOG_PATH.read_text(encoding="utf-8") if m.ERROR_LOG_PATH.exists() else ""
for i in range(500):
    m.log_error("reading the screen", ValueError(f"bad value {i}"))
after = m.ERROR_LOG_PATH.read_text(encoding="utf-8")[len(before):]
check("500 errors of one kind with changing messages: one errors.log entry, one remembered key",
      after.count("reading the screen") == 1 and len(m._last_errors) == 1)
m.log_error("reading the screen", OSError("other kind"))
check("...an error of another kind is still written", len(m._last_errors) == 2)

m._last_errors.clear()
for i in range(50):
    m.log_error(f"reading the mesos failed ({i} more failures skipped since the last entry)", ValueError("x"))
check("...nor when what was being done carries a changing count (one remembered key)", len(m._last_errors) == 1)

# the update check stays quiet on a test data folder, like every sender (owner, 2026-10-25)
real_urlopen = m.urllib.request.urlopen
m.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("went online"))
try:
    m.check_for_update()
    quiet = False
except RuntimeError:
    quiet = True
except AssertionError:
    quiet = False
m.urllib.request.urlopen = real_urlopen
check("the update check doesn't go online while MAPLE_EXP_DATA is set", quiet)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
