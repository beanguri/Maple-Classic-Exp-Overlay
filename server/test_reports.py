"""Tests POST /api/health and POST /api/report (src/reports.js) against a LOCAL test server with a fresh database.

    wrangler d1 execute classicexp --local --persist-to .wrangler/test-state --file schema.sql
    wrangler d1 execute classicexp --local --persist-to .wrangler/test-state --file schema_reports.sql
    wrangler pages dev public --ip 127.0.0.1 --port 8791 --persist-to .wrangler/test-state \
        --binding IP_SALT=local-test --binding REPORTS_TEST={"healthSite":40,"reportSite":7,"purgeEvery":1}
    python server/test_reports.py http://127.0.0.1:8791

Then a short second run WITHOUT the IP_SALT binding (fresh database again):

    python server/test_reports.py http://127.0.0.1:8791 nosalt
"""
import http.client
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
NOSALT = len(sys.argv) > 2 and sys.argv[2] == "nosalt"
assert "localhost" in BASE or "127.0.0.1" in BASE, "only run this against a local test server"
HERE = os.path.dirname(os.path.abspath(__file__))
HOST = urlparse(BASE)
failures = 0

with open(os.path.join(HERE, "..", "tests", "health_contract_v2.json"), encoding="utf-8") as f:
    CONTRACT = json.load(f)
# The server's test clock: noon UTC on the contract's "today".
NOW = int(datetime.fromisoformat(CONTRACT["today"] + "T12:00:00+00:00").timestamp() * 1000)


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def sql(command):
    """Rows from a query on the LOCAL test database (the same one the test server uses)."""
    wrangler = os.path.join(HERE, "node_modules", ".bin", "wrangler.cmd" if os.name == "nt" else "wrangler")
    out = subprocess.run([wrangler, "d1", "execute", "classicexp", "--local", "--persist-to", ".wrangler/test-state", "--json",
                          "--command", command], cwd=HERE, capture_output=True, text=True, timeout=120).stdout
    return json.loads(out[out.index("["):])[0]["results"]


def post(path, body, ip="10.9.0.1", now=NOW, raw=None, host=None, method="POST", content_type="application/json",
         chunked=False):
    """Returns (status, answer). `raw` sends those bytes instead of JSON; `chunked` sends no Content-Length."""
    data = raw if raw is not None else json.dumps(body).encode()
    headers = {"Content-Type": content_type, "CF-Connecting-IP": ip, "X-Test-Now": str(now)}
    if host:
        headers["Host"] = host
    c = http.client.HTTPConnection(HOST.hostname, HOST.port, timeout=20)
    if chunked:
        c.request(method, path, body=iter([data[i:i + 512] for i in range(0, len(data), 512)]), headers=headers,
                  encode_chunked=True)
    else:
        c.request(method, path, body=data if method == "POST" else None, headers=headers)
    r = c.getresponse()
    text = r.read()
    c.close()
    try:
        return r.status, json.loads(text or b"{}")
    except ValueError:
        return r.status, {}


def n(table, where="1"):
    return sql(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")[0]["n"]


def everything():
    """A fingerprint of all three tables: if a request wrote anything, this changes."""
    return (sql("SELECT COUNT(*) AS n, COALESCE(SUM(n), 0) AS s FROM limits")[0], n("health_reports"), n("user_reports"))


def with_change(base, change):
    body = json.loads(json.dumps(base))
    for k, v in change.items():
        body[k] = "a" * int(v[5:]) if isinstance(v, str) and v.startswith("LONG:") else v
    return body


OLD_SHAPES = [c for c in CONTRACT["health_valid"] if not c["name"].startswith("v2:")]    # 0.9.13's examples, kept in the file
health_full = CONTRACT["health_valid"][0]["body"]
report_ok = CONTRACT["report_valid"][0]["body"]

if NOSALT:
    before = everything()
    check("no IP_SALT: /api/health answers 500", post("/api/health", health_full)[0] == 500)
    check("no IP_SALT: /api/report answers 500", post("/api/report", report_ok)[0] == 500)
    check("...and nothing was stored", everything() == before)
    print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)

ip_n = [0]


def fresh():
    """A new made-up internet address for each request that must not hit the connection cap."""
    ip_n[0] += 1
    return f"10.9.{ip_n[0] // 250}.{ip_n[0] % 250 + 1}"


# ---- the shared contract: every valid case is taken, every invalid one is named
for i, case in enumerate(CONTRACT["health_valid"]):
    # The v2 examples share the first one's id and day, which the server (rightly) answers as a repeat: give each its own id.
    body = case["body"] if i == 0 else dict(case["body"], id="%016x" % (0xa00 + i))
    status, a = post("/api/health", body, ip=fresh())
    check(f"health valid: {case['name']}", status == 200 and a == {"ok": True})
for case in CONTRACT["health_invalid"]:
    before = everything()
    status, a = post("/api/health", with_change(health_full, case["change"]), ip=fresh())
    check(f"health invalid {case['field']}: {list(case['change'])[0]}", status == 400 and a.get("error") == case["field"])
check("invalid health reports wrote nothing", everything() == before)
for case in OLD_SHAPES:
    body = dict(case["body"], id="e" + case["body"]["id"][1:])   # a new id: the shared examples above are already stored
    status, a = post("/api/health", body, ip=fresh())
    check(f"0.9.13 shape still accepted: {case['name']}", status == 200 and a == {"ok": True})
v2_all = next(dict(c["body"], id="%016x" % (0xa00 + i)) for i, c in enumerate(CONTRACT["health_valid"])
              if c["name"] == "v2: every new key")
check("a v2 body is stored whole (new keys kept)", json.loads(sql(
    "SELECT body FROM health_reports WHERE id = '%s'" % v2_all["id"])[0]["body"]) == v2_all)
# The read-only deploy check (ticket 18): a body with "ui" and a bad id. An old server names "ui"; this one names "id".
before = everything()
status, a = post("/api/health", dict(health_full, id="BAD", ui=CONTRACT["health_valid"][3]["body"]["ui"]), ip=fresh())
check("deploy check: new server names the bad id, not ui", status == 400 and a.get("error") == "id")
check("...and stores nothing", everything() == before)
check("the stored body is the one sent", json.loads(sql(
    "SELECT body FROM health_reports WHERE id = '0123456789abcdef'")[0]["body"]) == health_full)

# ---- a repeat of the same id and day: dup answer, nothing written
before = everything()
status, a = post("/api/health", health_full, ip=fresh())
check("same id and day again: dup", status == 200 and a == {"ok": True, "dup": True})
check("...and it wrote nothing", everything() == before)

# ---- connection cap (3 a day), then the refused one writes nothing
one_ip = "10.8.0.1"
ids = ["a%015x" % i for i in range(4)]
answers = [post("/api/health", dict(health_full, id=i), ip=one_ip)[0] for i in ids]
check(f"health: 3 per connection per day, the 4th is refused ({answers})", answers == [200, 200, 200, 429])
before = everything()
status, a = post("/api/health", dict(health_full, id="b" + "0" * 15), ip=one_ip)
check("...a refused request answers {retry: true}", status == 429 and a == {"retry": True})
check("...and writes nothing", everything() == before)
check("a refused report isn't stored", n("health_reports", "id = 'b000000000000000'") == 0)

# ---- site cap (40 in this test run): fill what is left, then the next one is refused
left = 40 - sql("SELECT n FROM limits WHERE key = 'h:site'")[0]["n"]
check(f"health: site cap lets the last {left} in", all(
    post("/api/health", dict(health_full, id="c%015x" % i), ip=fresh())[0] == 200 for i in range(left)))
before = everything()
status, a = post("/api/health", dict(health_full, id="d" + "0" * 15), ip="10.7.0.1")
check("health: over the site cap is refused", status == 429)
check("...nothing written, not even that connection's counter", everything() == before)
status, a = post("/api/health", health_full, ip=fresh())
check("...but a dup is still answered over the cap", a == {"ok": True, "dup": True})

# ---- 413: too big, sent chunked with no Content-Length
big = json.dumps(dict(health_full, os="x" * 5000)).encode()
status, a = post("/api/health", None, raw=big, ip=fresh(), chunked=True)
check("health: 5,000 bytes sent chunked: 413", status == 413)
status, a = post("/api/report", None, raw=b'{"details":"' + b"x" * 17000 + b'"}', ip=fresh(), chunked=True)
check("report: 17,000 bytes sent chunked: 413", status == 413)
status, a = post("/api/health", None, raw=big, ip=fresh())
check("health: too big with a Content-Length: 413", status == 413)

# ---- host, method, type
status, a = post("/api/health", health_full, ip=fresh(), host="maple-classic-exp-overlay.pages.dev")
check(f"pages.dev host: 404 ({status})", status == 404)
status, a = post("/api/report", report_ok, ip=fresh(), host="maple-classic-exp-overlay.pages.dev")
check("pages.dev host: 404 for reports too", status == 404)
check("GET /api/health: 405", post("/api/health", None, method="GET")[0] == 405)
check("GET /api/report: 405", post("/api/report", None, method="GET")[0] == 405)
check("wrong content type: 415", post("/api/health", health_full, ip=fresh(), content_type="text/plain")[0] == 415)
check("not JSON: 400", post("/api/health", None, raw=b"{nope", ip=fresh())[0] == 400)

# ---- player reports
for case in CONTRACT["report_invalid"]:
    status, a = post("/api/report", with_change(report_ok, case["change"]), ip=fresh())
    check(f"report invalid {case['field']}: {list(case['change'])[0]}", status == 400 and a.get("error") == case["field"])
check("report valid: normal", post("/api/report", report_ok, ip=fresh()) == (200, {"ok": True}))
check("report valid: no details", post("/api/report", CONTRACT["report_valid"][1]["body"], ip=fresh()) == (200, {"ok": True}))
check("report: the same id's 3rd today is taken", post("/api/report", report_ok, ip=fresh())[0] == 200)
before = everything()
check("report: the 4th from that id is refused", post("/api/report", report_ok, ip=fresh())[0] == 429)
check("...and wrote nothing", everything() == before)
rows = sql("SELECT message, details, version FROM user_reports ORDER BY n LIMIT 1")
check("a stored report keeps what was sent", rows and rows[0]["message"] == report_ok["message"]
      and rows[0]["details"] == report_ok["details"])
other = [post("/api/report", dict(report_ok, id="e%015x" % i), ip="10.6.0.1")[0] for i in range(4)]
check(f"report: 3 per connection per day ({other})", other == [200, 200, 200, 429])
check("report: site cap (7 here) lets one more in", post("/api/report", dict(report_ok, id="f" + "0" * 15), ip=fresh())[0] == 200)
before = everything()
check("report: over the site cap is refused", post("/api/report", dict(report_ok, id="f" + "1" + "0" * 14), ip=fresh())[0] == 429)
check("...and wrote nothing", everything() == before)
check("no way to read these back", post("/api/health", None, method="GET", host="mcwo.app")[0] in (404, 405))

# ---- retention: a stored report 100 days later clears the old rows (this test server clears on every store)
check("old reports are still there before", n("health_reports") > 0 and n("user_reports") > 0 and n("limits") > 0)
later = NOW + 100 * 86400000
later_day = datetime.fromtimestamp(later / 1000, timezone.utc).strftime("%Y-%m-%d")
status, a = post("/api/health", dict(health_full, id="9" * 16, day=later_day), ip=fresh(), now=later)
check("a report 100 days later is stored", status == 200 and a == {"ok": True})
check("...and the old health reports, player reports and counters are gone",
      n("health_reports") == 1 and n("user_reports") == 0 and n("limits", f"day < '{later_day}'") == 0)
check("...the same id and day is no longer a dup",
      post("/api/health", health_full, ip=fresh(), now=NOW) == (200, {"ok": True}))

# ---- what is stored is the checked body in compact form, not the sender's padding (bounds storage per row)
padded = json.dumps(dict(health_full, id="8" * 16, day=later_day), indent=1).replace("\n", "\n" + " " * 1000, 3)
padded = '{"looks": 1, ' + padded.lstrip().lstrip("{")           # a repeated key: the later one wins, the first is junk
status, a = post("/api/health", None, raw=padded.encode(), ip=fresh(), now=later)
check("a padded body with a repeated key is accepted", status == 200)
stored = sql("SELECT body FROM health_reports WHERE id = '" + "8" * 16 + "'")[0]["body"]
check(f"...and stored compact ({len(stored)} bytes, sent {len(padded)})",
      json.loads(stored) == dict(health_full, id="8" * 16, day=later_day) and stored == json.dumps(json.loads(stored), separators=(",", ":")))

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
