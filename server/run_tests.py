"""Runs the server's Python tests against a LOCAL test server, each on a fresh database (what CI does).

    cd server && npm ci
    python server/run_tests.py [port]

It starts `wrangler pages dev` on 127.0.0.1 (never anything else), runs test_reports.py, test_server.py,
test_reports.py nosalt and test_health_client.py (the overlay's own sender), and stops the server after each.
The tests read the database folder .wrangler/test-state, so that is where it is made.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
BASE = f"http://127.0.0.1:{PORT}"
WRANGLER = os.path.join(HERE, "node_modules", "wrangler", "bin", "wrangler.js")
STATE = os.path.join(HERE, ".wrangler", "test-state")
CAPS = '{"healthSite":40,"reportSite":7,"purgeEvery":1}'
BUDGET = ["CACHE_LOCALLY=1", 'BUDGET_TEST={"runsFresh":6,"runsVetted":9,"runsAll":12,"statsMiss":3,"ladderMiss":2,"edgeSeconds":1}']
BIG = '{"healthSite":100000,"purgeEvery":1000000}'   # test_health_client sends a whole month of days


def wrangler(*args):
    return subprocess.run(["node", WRANGLER, *args], cwd=HERE, check=True, capture_output=True, encoding="utf-8", errors="replace")


def fresh_db():
    for _ in range(40):
        shutil.rmtree(STATE, ignore_errors=True)       # right after a server stops, its files stay locked a moment
        if not os.path.exists(STATE):
            break
        time.sleep(0.5)
    else:
        sys.exit("test-state is still locked: not a fresh database")
    for schema in ("schema.sql", "schema_reports.sql"):
        wrangler("d1", "execute", "classicexp", "--local", "--persist-to", ".wrangler/test-state", "--file", schema, "--yes")


def start_server(salt, caps, extra=()):
    cmd = ["node", WRANGLER, "pages", "dev", "public", "--ip", "127.0.0.1", "--port", str(PORT),
           "--inspector-port", str(PORT + 500), "--persist-to", ".wrangler/test-state"]
    if salt:
        cmd += ["--binding", "IP_SALT=local-test"]
    cmd += ["--binding", f"REPORTS_TEST={caps}"]
    for binding in extra:
        cmd += ["--binding", binding]
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/", timeout=2).close()
            return proc
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    stop_server(proc)
    sys.exit(f"the test server didn't answer on {PORT}")


def stop_server(proc):
    if os.name == "nt":                                 # wrangler starts child processes: stop the whole tree
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    proc.wait()


def main():
    if not os.path.exists(WRANGLER):
        sys.exit("server/node_modules is missing: run `npm ci` in server/ first")
    runs = [("test_reports.py", [], True, CAPS), ("test_server.py", [], True, CAPS),
            ("test_reports.py", ["nosalt"], False, CAPS), ("test_health_client.py", [], True, BIG),
            ("test_budget.py", [], True, CAPS, BUDGET)]
    failed = []
    for script, extra, salt, caps, *bindings in runs:
        fresh_db()
        proc = start_server(salt, caps, bindings[0] if bindings else ())
        try:
            code = subprocess.run([sys.executable, os.path.join(HERE, script), BASE, *extra]).returncode
        finally:
            stop_server(proc)
        name = " ".join([script, *extra])
        print(f"== {name}: {'ok' if code == 0 else 'FAILED'}", flush=True)
        if code:
            failed.append(name)
    sys.exit(f"failed: {', '.join(failed)}" if failed else 0)


if __name__ == "__main__":
    main()
