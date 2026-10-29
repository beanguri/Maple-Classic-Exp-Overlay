"""Tests the daily budget rules (audit 2, B05b) against a LOCAL test server started with CACHE_LOCALLY and a small
BUDGET_TEST (see run_tests.py): the run cap that binds every install (weighted by trust), the cap on cache misses for
/api/stats and /api/ladder, and the ip_hash that is blanked once it is no longer needed.

    py -3 server/test_budget.py http://localhost:8787
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
assert "localhost" in BASE or "127.0.0.1" in BASE, "only run this against a local test server"
HERE = os.path.dirname(os.path.abspath(__file__))
failures = 0
NOW = datetime.now(timezone.utc)


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def call(path, body=None, later_hours=0.0):
    headers = {"content-type": "application/json"}
    if later_hours:
        headers["X-Test-Now"] = str(int((time.time() + later_hours * 3600) * 1000))
    if isinstance(body, dict) and isinstance(body.get("install"), str):
        n = int(hashlib.md5(body["install"].encode()).hexdigest()[:4], 16)
        headers["CF-Connecting-IP"] = f"10.1.{n >> 8}.{n & 255}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def sql(command):
    wrangler = os.path.join(HERE, "node_modules", ".bin", "wrangler.cmd" if os.name == "nt" else "wrangler")
    out = subprocess.run([wrangler, "d1", "execute", "classicexp", "--local", "--persist-to", ".wrangler/test-state", "--json",
                          "--command", command], cwd=HERE, capture_output=True, text=True, timeout=120).stdout
    return json.loads(out[out.index("["):])[0]["results"]


seq = [0]
ids = iter(range(1, 10**6))


def new_install():
    return f"{next(ids):016x}"


def run(at):
    """A plausible 15-minute run that started at `at` (a datetime, UTC)."""
    started = at.strftime("%Y-%m-%dT%H:%M")
    return {"date": started[:10], "started": started, "map": "The Land of Wild Boar II", "map_read": "The Land of Wild Boar II",
            "class": "Bandit", "level_start": 31, "level_end": 31, "party": 1, "exp": 12500, "seconds": 900.4,
            "mesos": 7500, "mesos_seconds": 900.4, "version": "0.9.14-beta", "seal": "x" * 24}


def post(who, at, later_hours=0.0):
    status, d = call("/api/runs", {"install": who, "runs": [run(at)]}, later_hours)
    r = d["results"][0]
    return r.get("ok", False), r.get("why"), r.get("retry", False)


def spaced(base, i):
    return base - timedelta(minutes=20 * (i + 1))


# ---- the run cap binds every install, and room is kept for the ones with a history (caps: 6 / 9 / 12 in this test)
established, vetted = new_install(), new_install()
for d in (5, 4, 3, 2):                    # established: first seen 5 days ago, runs on 4 days, 20 runs (5 a day: under the test's caps)
    for i in range(5):
        x = post(established, spaced(NOW - timedelta(days=d), i), -24 * d)
        assert x[0], x
for i in range(4):                        # vetted only: first seen 1 day ago, 4 runs
    x = post(vetted, spaced(NOW - timedelta(days=1), i), -24)
    assert x[0], x
check("the history is in place", sql("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 24)

fresh = [new_install() for _ in range(6)]
res = [post(w, spaced(NOW, 0)) for w in fresh]
check("new players post up to the first limit", all(r[0] for r in res))
r = post(new_install(), spaced(NOW, 0))
check(f"...and the next new player waits for tomorrow, told to retry ({r})", not r[0] and r[2])
r = post(fresh[0], spaced(NOW, 1))
check(f"a player first seen today is held to the same limit, not only new ones ({r})", not r[0] and r[2])
res = [post(vetted, spaced(NOW, i)) for i in range(3)]
check(f"a vetted player still has room above that ({[x[0] for x in res]})", all(x[0] for x in res))
r = post(vetted, spaced(NOW, 3))
check(f"...up to the second limit ({r})", not r[0] and r[2])
res = [post(established, spaced(NOW, i + 5)) for i in range(3)]
check(f"a player with a long history has room kept for them above that ({[x[0] for x in res]})", all(x[0] for x in res))
r = post(established, spaced(NOW, 9))
check(f"...and even they stop at the day's limit, told to retry ({r})", not r[0] and r[2])
check("the day's counter did not go past the limit", sql(f"SELECT runs FROM daily WHERE day = '{NOW.strftime('%Y-%m-%d')}'")[0]["runs"] == 12)
many = [run(spaced(NOW + timedelta(days=0), i + 20)) for i in range(5)]
status, d = call("/api/runs", {"install": established, "runs": many})
check("a refused post writes nothing", d["received"] == 0 and sql(f"SELECT COUNT(*) AS n FROM runs WHERE received_day = '{NOW.strftime('%Y-%m-%d')}'")[0]["n"] == 12)

# ---- one post can't run past the limit either
sql("DELETE FROM daily")
sql(f"INSERT INTO daily (day, runs, new_installs) VALUES ('{NOW.strftime('%Y-%m-%d')}', 10, 0)")
status, d = call("/api/runs", {"install": established, "runs": [run(spaced(NOW, i + 30)) for i in range(5)]})
check(f"a post of 5 runs at 10 of 12 saves 2 ({d['received']})", d["received"] == 2)

# ---- cache misses for /api/stats and /api/ladder have a daily cap (caps: 3 stats, 2 ladder); old answers are kept
def stats(level, later=0.0):
    return call(f"/api/stats?class=Thief&path=Shadower&level={level}&party=solo", None, later)


codes = [stats(40 + i)[0] for i in range(3)]
check(f"the first questions of the day are answered ({codes})", codes == [200, 200, 200])
status, d = stats(50)
check(f"a new question over the cap gets a short busy answer ({status} {d})", status == 503 and d.get("busy") is True)
time.sleep(2.5)                                          # (the test server keeps its edge copy for 1 second)
status, d = stats(40, later=1)                           # an hour later the saved answer is old: still served
check(f"a question already answered is served from the last saved answer ({status})", status == 200 and "maps" in d)
rows = sql("SELECT n FROM limits WHERE key = 'c:stats'")
check(f"the busy answers wrote nothing ({rows})", rows and rows[0]["n"] == 3)
codes = [call(f"/api/ladder?class=Thief&path=Shadower&party=solo&new={i % 2}")[0] for i in range(2)]
check(f"the ladder has its own cap ({codes})", codes == [200, 200])
status, d = call("/api/ladder?class=Warrior&party=solo")
check(f"...and answers busy over it ({status})", status == 503 and d.get("busy") is True)
check("options and recent are not capped", all(call(p)[0] == 200 for p in ("/api/options", "/api/recent")))

# ---- ip_hash goes once the day limits no longer need it
sql("DELETE FROM runs"); sql("DELETE FROM daily")
check("a run from 5 days ago and one from today are stored", post(new_install(), spaced(NOW - timedelta(days=5), 0), -24 * 5)[0]
      and post(new_install(), spaced(NOW, 0))[0])
check("both have an ip_hash at first", sql("SELECT COUNT(*) AS n FROM runs WHERE ip_hash != ''")[0]["n"] == 2)
status, d = call("/api/report", {"id": "0123456789abcdef", "version": "0.9.14-beta", "message": "hello", "details": "x"})
check(f"a stored report runs the purge ({status})", status == 200)
left = sql("SELECT received_day, ip_hash != '' AS has FROM runs ORDER BY received_day")
check(f"the old run's ip_hash is blank, today's is kept ({left})", len(left) == 2 and left[0]["has"] == 0 and left[1]["has"] == 1)
status, d = call("/api/report", {"id": "0123456789abcdef", "version": "0.9.14-beta", "message": "again", "details": "x"})
check("running it twice is fine", status == 200 and sql("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 2)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
