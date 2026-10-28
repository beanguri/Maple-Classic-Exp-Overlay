"""Runs the overlay's own health-report code (healthlog.py and post_health) against a LOCAL test server.

    wrangler d1 execute classicexp --local --persist-to .wrangler/test-state --file schema.sql
    wrangler d1 execute classicexp --local --persist-to .wrangler/test-state --file schema_reports.sql
    wrangler pages dev public --ip 127.0.0.1 --port 8791 --persist-to .wrangler/test-state \
        --binding IP_SALT=local-test --binding REPORTS_TEST={"healthSite":100000,"purgeEvery":1000000}
    python server/test_health_client.py http://127.0.0.1:8791

(Not part of server_tests: test_reports.py already checks the server side. This checks that what the overlay builds is what
the server takes, and what post_health makes of the answers, with the real limits of 3 reports per connection a day.)
"""
import calendar
import http.client
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
assert BASE.startswith("http://127.0.0.1:"), "only run this against a local test server"
os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()                   # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import healthlog  # noqa: E402
import maple_exp as m  # noqa: E402

# Sending is allowed only here, to this server, in this process (never from a shell with variables set).
m.STATS_SITE = BASE
assert m.STATS_SITE.startswith("http://127.0.0.1:")
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"

HOST = urlparse(BASE)
failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def post(body, ip):
    """One POST with its own made-up connection address (CF-Connecting-IP). Returns (status, answer)."""
    c = http.client.HTTPConnection(HOST.hostname, HOST.port, timeout=20)
    c.request("POST", "/api/health", body=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                                                                              "CF-Connecting-IP": ip})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data or b"{}")


now = time.time() // 86400 * 86400 + 12 * 3600     # midday UTC today: the hours the tests step through never cross into the next day
clock = [now - 86400]
rnd = random.Random(7)
folder = Path(tempfile.mkdtemp())


def new_log():
    return healthlog.HealthLog(folder / "health.json", clock=lambda: clock[0])


def a_day(log, looks):
    log.snapshot(rnd.choice(["0.9.14-beta", "0.9.14", "10.20.30-beta.2"]),
                 rnd.choice(["Windows 10.0.26200", "Windows éè 10", "", "x" * 200, "macOS 15.1"]),
                 [(rnd.choice([1920, 2560, 3840, 5120]), rnd.choice([1080, 1440, 2880]), rnd.choice([None, 100, 150, 500]))
                  for _ in range(rnd.choice([0, 1, 4, 7]))], rnd.choice(["screen", "window", "region"]),
                 rnd.choice(["ok", "missing", "no_eng"]))
    for _ in range(looks):
        log.look(rnd.choice(healthlog.EXP_KINDS), rnd.random() < .3,
                 [(p, rnd.random() < .5) for p in rnd.sample(list(healthlog.PAIRS), 2)], rnd.choice([None, 5.0, 25.5, 9999.0]),
                 rnd.random() * 9, rnd.choice([None, (1920, 1080, True, 14.0), (5120, 2880, False, 28.049999)]),
                 rnd.choice([None, ("agree", 8.1), ("disagree", None), ("errors", None), ("old_only", 5.0)]),
                 ui=rnd.choice([None, "default", "ideal"]), layout=rnd.choice([None, "keyboard", "controller-standard"]),
                 shadows=tuple((p, rnd.choice(healthlog.SHADOW_KEYS), rnd.choice([None, 2.5, 9999.0]))
                               for p in rnd.sample(list(healthlog.SHADOW_PARTS), rnd.choice([0, 1, 2]))),
                 why=rnd.choice([None, "not_found", "unclear"]))
    log.errors({"Err" + "ab"[j % 2] * (j + 1): rnd.randint(1, 5) for j in range(rnd.choice([0, 3, 12, 30]))})


# ---- 1. many made-up days, each with its own ID and connection: the server takes every body the overlay makes
taken = refused = 0
for i in range(60):
    (folder / "health.json").unlink(missing_ok=True)
    clock[0] = now - 86400
    log = new_log()
    a_day(log, rnd.choice([1, 5, 150]))
    clock[0] = now
    day = healthlog.utc_day(now - 86400)
    body = log.body(day, f"{i:016x}")
    if body is None:
        refused += 1
        continue
    status, answer = post(body, f"10.8.{i // 200}.{i % 200 + 1}")
    if status == 200 and answer == {"ok": True}:
        taken += 1
    else:
        refused += 1
        print("  refused:", status, answer)
check("60 made-up days: the server takes every body the overlay makes", taken == 60 and refused == 0)

# ---- 2. the biggest day there can be
(folder / "health.json").unlink(missing_ok=True)
clock[0] = now - 86400
log = new_log()
log.snapshot("10.20.30-beta.2", "Windows é" * 20, [(20000, 20000, 500)] * 6, "region", "no_eng")
rec = log.today()
for k in healthlog.EXP_KINDS:
    rec["exp"][k] = 30000
rec["exp"]["percent_only"], rec["looks"] = 60000, 180000
for name, pair in healthlog.PAIRS.items():
    rec[name] = {k: 10 ** 9 for k in pair}
rec["errors"] = {("E" * 39 + chr(97 + j)): 10 ** 9 for j in range(10)}
rec["tracking"], rec["ms_total"], rec["ms_n"], rec["v2_ms"], rec["v2_n"] = 10 ** 6, 10 ** 12, 1, 10 ** 12, 1
rec["hist"] = {str(k): 5 for k in range(1001)}
rec["games"] = {f"{20000 - j}x20000/1/63.9": 5 for j in range(12)}
rec["shadow"] = {k: 10 ** 9 for k in healthlog.SHADOW_KEYS}
for key in ("ui", "why", "shadow_mesos", "shadow_party"):
    rec[key] = {k: 10 ** 9 for k in rec[key]}
for part in healthlog.SHADOW_PARTS:
    rec[f"{part}_v2_ms"], rec[f"{part}_v2_n"] = 10 ** 12, 1
clock[0] = now
big = log.body(healthlog.utc_day(now - 86400), "ffffffffffffffff")
status, answer = post(big, "10.7.0.1")
check("the biggest possible day is taken by the server", big is not None and status == 200 and answer == {"ok": True})

# ---- 3. the real sender, the real limits (3 a day per connection; urllib sends no address, so one connection)
(folder / "health.json").unlink(missing_ok=True)
clock[0] = now - 5 * 86400
log = new_log()
for back in range(5, 1, -1):                    # four finished days, each with something in it
    clock[0] = now - back * 86400
    a_day(log, 3)
clock[0] = now
HID = "abcdef0123456789"
sent_days = []
for step in range(5):
    due = log.due(HID)
    if due is None:
        break
    day, body = due
    log.attempting()
    outcome = m.post_health(body)
    log.finish(day, outcome[0])
    sent_days.append((day, outcome[0]))
    clock[0] += 3601                            # (the overlay waits an hour between tries)
days = [d for d, _ in sent_days]
check("the oldest day goes first", days == sorted(days) and days[0] == healthlog.utc_day(now - 5 * 86400))
check("three days are taken, then the connection's daily limit says 'later'",
      [o for _, o in sent_days] == ["ok", "ok", "ok", "later"])
check("after 'later' the overlay waits for the next UTC day (no tight loop)", log.due(HID) is None)
clock[0] = log.retry_after + 60
check("...and then the left-over day is due again", (log.due(HID) or (None,))[0] == days[-1])
# the same day again: the server already has it
(folder / "health.json").unlink(missing_ok=True)
clock[0] = now - 86400
log = new_log()
a_day(log, 2)
clock[0] = now
day = healthlog.utc_day(now - 86400)
body = log.body(day, "1111111111111111")
status, answer = post(body, "10.6.0.1")
check("a day posted twice: the second answer is a repeat, which post_health takes as done",
      status == 200 and post(body, "10.6.0.2")[1] == {"ok": True, "dup": True})

# a day where every look broke: no counted look, only exception names (sent since the owner's decision, 2026-10-25)
(folder / "health.json").unlink(missing_ok=True)
clock[0] = now - 86400
log = new_log()
log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(1920, 1080, 100)], "screen", "ok")
log.errors({"ValueError": 40, "OSError": 2})
clock[0] = now
broken = log.body(healthlog.utc_day(now - 86400), "2222222222222222")
check("a day where every look broke (0 looks, only exception names) is built and the site takes it",
      broken is not None and broken["looks"] == 0 and post(broken, "10.7.0.1") == (200, {"ok": True}))

# ---- 4. what the server says about bad requests, as the overlay reads it
m.STATS_SITE = BASE
check("a body the server refuses comes back as 'bad' with the field named", m.post_health({**body, "looks": -1}) == ("bad", "looks"))


# ---- 5. 0.9.14's new keys, end to end (UI size, layout, shadow parts, EXP's reasons)
def v2_day(log, looks):
    log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(2560, 1440, 100)], "window", "ok")
    for i in range(looks):
        log.look(healthlog.EXP_KINDS[i % 3], ms=20.0, tracking=1.0, game=(2560, 1440, True, 14.0),
                 shadow=("old_only", 8.0) if i % 2 else ("agree", 8.0), why="not_found" if i % 4 == 1 else "unclear",
                 ui="ideal" if i % 2 else "default", layout="keyboard" if i % 3 else "controller-cross",
                 shadows=(("mesos", "agree", 3.0), ("party", "v2_only", 1.5)))


(folder / "health.json").unlink(missing_ok=True)
clock[0] = now - 86400
log = new_log()
v2_day(log, 12)
clock[0] = now
day = healthlog.utc_day(now - 86400)
v2_body = log.body(day, "3333333333333333")
status, answer = post(v2_body, "10.5.0.1")
check("a day with every new key is built and the site takes it",
      v2_body is not None and all(k in v2_body for k in healthlog.V2_KEYS) and v2_body["ui"]["ideal"] == 6
      and "mesos_v2_avg" in v2_body["ms"] and status == 200 and answer == {"ok": True})
check("...and due() hands the sender that same body (the connection's 3 reports a day are used up above, so it is posted with its own address)",
      (log.due("4444444444444444") or (None, {}))[1].get("shadow_mesos") == v2_body["shadow_mesos"]
      and post(log.due("4444444444444444")[1], "10.5.0.2") == (200, {"ok": True}))

# a site that doesn't know the new keys (0.9.13's server): made here, answering what its reports.js answers (the first key it
# doesn't know, by name, with a 400 and nothing stored). The day must wait, not be lost.
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

OLD_KEYS = {"id", "version", "day", "os", "screens", "game", "capture", "tesseract", "seconds_tracking", "looks", "exp",
            "level", "map", "class", "mesos", "potions", "errors", "ms", "shadow"}
OLD_MS = {"look_avg", "look_p95", "v2_avg"}
answered = []


class OldSite(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        unknown = next((k for k in body if k not in OLD_KEYS), None)
        if unknown is None and any(k not in OLD_MS for k in body.get("ms", {})):
            unknown = "ms"
        answered.append(unknown)
        data = json.dumps({"error": unknown} if unknown else {"ok": True}).encode()
        self.send_response(400 if unknown else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


old_site = HTTPServer(("127.0.0.1", 0), OldSite)
threading.Thread(target=old_site.serve_forever, daemon=True).start()
real_site = m.STATS_SITE
m.STATS_SITE = f"http://127.0.0.1:{old_site.server_port}"
assert m.STATS_SITE.startswith("http://127.0.0.1:")
try:
    (folder / "health.json").unlink(missing_ok=True)
    clock[0] = now - 86400
    log = new_log()
    v2_day(log, 12)
    clock[0] = now
    due = log.due("5555555555555555")
    log.attempting()
    outcome = m.post_health(due[1])
    log.finish(due[0], outcome[0])
    check("against a server that doesn't know the new keys: refused by name, which the overlay takes as 'later'",
          answered == ["shadow_mesos"] and outcome == ("later", "shadow_mesos"))   # (the first new key in the body's order)
    check("...so the day is kept, not dropped, and tried again from tomorrow (no tight loop)",
          not log.days[due[0]]["sent"] and log.due("5555555555555555") is None and log.retry_after > now)
    clock[0] = log.retry_after + 3601
    m.STATS_SITE = real_site                # the owner deploys the server
    again = log.due("5555555555555555")
    check("...and once the site knows them the same day goes out and is taken",
          again is not None and again[0] == due[0] and post(again[1], "10.5.0.3") == (200, {"ok": True}))
    # the same old server and a day with no new key: still taken (0.9.13's shape is what a day without them looks like)
    m.STATS_SITE = f"http://127.0.0.1:{old_site.server_port}"
    (folder / "health.json").unlink(missing_ok=True)
    clock[0] = now - 86400
    log = new_log()
    log.snapshot("0.9.14-beta", "Windows 10.0.26200", [(1920, 1080, 100)], "screen", "ok")
    for _ in range(3):
        log.look("read", ms=20.0, tracking=1.0, game=(1920, 1080, True, 14.0), shadow=("agree", 8.0))
    clock[0] = now
    plain = log.body(healthlog.utc_day(now - 86400), "6666666666666666")
    check("a day that counted none of the new things has none of the new keys, and the old server takes it",
          plain is not None and not any(k in plain for k in healthlog.V2_KEYS) and m.post_health(plain) == ("ok", "")
          and answered[-1] is None)
finally:
    m.STATS_SITE = real_site
    old_site.shutdown()

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
