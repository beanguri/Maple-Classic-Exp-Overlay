"""The log keeps kills, deaths and the like per 5-minute stretch (AutoLog.tick extras).  Run: py -3 tests/test_log_extras.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appmod as m  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


d = Path(tempfile.mkdtemp())
log = m.AutoLog(15, path=d / "exp_log.json", text_path=d / "exp_log.txt")
t = 0.0
log.gain(t, 1)                                   # the stretch starts with a gain
entry = None
for i in range(1, 700):
    t += 0.5
    if i % 4 == 0:
        log.gain(t, 10)
    extras = {"kills": i // 4, "own_exp": 8 * (i // 4), "bonus_exp": 2 * (i // 4), "mesos_drops": 0, "mesos_text": 0,
              "deaths": 1 if i > 100 else 0, "levels": 0, "lvl_total": 100000, "remaining": 90000}
    got = log.tick(t, "Ant Tunnel II", 34, 4, job="Bowman", extras=extras)
    entry = got or entry
check("a stretch was logged", entry is not None)
e = log.entries[0]
check("it has the kills of the stretch", e.get("kills", 0) > 0 and e["kills"] == e["own_exp"] // 8)
check("deaths are counted over the stretch", e.get("deaths") == 1)
check("the level's size is kept", e.get("lvl_total") == 100000 and e.get("remaining") == 90000)
check("the stretch is a point of the pace chart", len(e.get("blocks", [])) == 1 and e["blocks"][0][0] == e["exp"])
check("the extras don't break the seal", log.sealed(e))
check("party sizes merged add the counters", m.merged([e, {**e, "party": 5}]).get("kills") == 2 * e["kills"])
print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
