"""Health reports: a short, anonymous daily summary of how well the overlay reads the game.

Numbers only. This module keeps one small record per UTC day (health.json in the data folder), builds
the report body for a finished day and checks it against the rules the site enforces (the same rules
as server/src/reports.js; tests/health_contract_v2.json holds the cases both sides test against).
It never holds a picture, a name, a map, the class, an amount or a path.
"""
import calendar
import json
import os
import re
import secrets
import time

KEEP_DAYS = 8               # days kept in health.json
SEND_DAYS = 7               # a finished day older than this is dropped unsent
SEND_EVERY = 3600           # at most one send attempt per hour (kept across restarts)
SAVE_EVERY = 300            # the record is written this often (and at exit)
EXP_KINDS = ("read", "tesseract", "unclear", "not_found", "covered", "no_window")
SHADOW_KEYS = ("agree", "disagree", "v2_only", "old_only", "neither", "skipped", "errors")     # shadow mode (shadow.py)
# Added for 0.9.14 (all optional in the body: an older overlay leaves them out and the site takes both shapes):
UI_KEYS = ("default", "ideal", "keyboard", "controller")        # looks by UI size (Default / Ideal) and by potion layout
WHY_KEYS = ("not_found", "unclear")                             # EXP's old_only looks by why reader v2 didn't read it
SHADOW_PARTS = ("mesos", "party")                               # a shadow of their own (EXP's is "shadow")
V2_KEYS = ("ui", "shadow_mesos", "shadow_party", "shadow_why")  # the new top-level keys (an old site refuses them by name)
V2_MS = ("mesos_v2_avg", "party_v2_avg")                        # ...and the new keys of "ms"
PAIRS = {"level": ("read", "unclear"), "map": ("read", "unread"), "class": ("read", "unread"),
         "mesos": ("read", "unclear"), "potions": ("read", "unclear")}
MAX_COUNT = 10_000_000
MAX_LOOKS = 200_000
BUCKET_MS = 10              # the look-time histogram (for the 95th percentile) has a bucket per 10 ms
MAX_BUCKET = 1000
MAX_LOOK_GAP = 5.0          # seconds of "tracking" added for one look is at most this

# Look categories (what `exp_kind` of a Reading means; set where the reader makes the Reading):
#   read       a number that fits the EXP table (or a clear percentage, on a percentage-only bar): reader v2's
#              (the main EXP reader since 0.9.15) or, when v2 has none, the digit templates'
#   tesseract  the templates weren't sure and the best candidate came from Tesseract
#   unclear    the bar was found but no number can be trusted
#   not_found  no EXP bar found (or its picture is off screen)
#   covered    this overlay's own window is over the bar
#   no_window  the game wasn't found, is minimized, no permission, or no area was selected


def kind_of(reading):
    """Which look category a Reading falls in: the reader's own exp_kind, or (for a Reading made without one)
    the best guess from what it holds."""
    kind = getattr(reading, "exp_kind", None)
    if kind in EXP_KINDS:
        return kind
    if getattr(reading, "candidates", None):
        return "read" if reading.confident else "unclear"
    return "not_found"


# --------------------------------------------------------------------------- the ID

def make_health_id():
    """The random ID used only for health and Send reports: 16 lowercase hex characters."""
    return secrets.token_hex(8)


def good_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{16}", value) is not None


def ensure_id(cfg):
    """Makes cfg["health_id"] if it has none. Returns True when it just made one (so the caller saves)."""
    if good_id(cfg.get("health_id")):
        return False
    cfg["health_id"] = make_health_id()
    return True


# --------------------------------------------------------------------------- checking a body

_IS_INT = lambda v, lo, hi: type(v) is int and lo <= v <= hi
_COUNT = lambda v: _IS_INT(v, 0, MAX_COUNT)


def day_number(day):
    """Days since 1970-01-01 for 'YYYY-MM-DD', or None if it is not a real date."""
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    try:
        t = time.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None
    return calendar.timegm(t) // 86400


def bad_field(body, today):
    """The name of the first field the site would refuse, or None if the body is fine. `today` is the UTC
    day, 'YYYY-MM-DD'. (The same checks as badHealthField in server/src/reports.js.)"""
    if not isinstance(body, dict):
        return "body"
    required = ["id", "version", "day", "os", "screens", "game", "capture", "tesseract", "seconds_tracking",
                "looks", "exp"]
    optional = ["level", "map", "class", "mesos", "potions", "errors", "ms", "shadow"] + list(V2_KEYS)
    for k in body:
        if k not in required and k not in optional:
            return str(k)
    for k in required:
        if k not in body:
            return k
    if not good_id(body["id"]):
        return "id"
    v = body["version"]
    if not isinstance(v, str) or len(v) > 20 or not re.fullmatch(r"\d+\.\d+\.\d+(-[a-z0-9.]+)?", v):
        return "version"
    d, t = day_number(body["day"]), day_number(today)
    if d is None or t is None or d > t or d < t - 8:
        return "day"
    if not isinstance(body["os"], str) or not re.fullmatch(r"[\x20-\x7e]{1,60}", body["os"]):
        return "os"
    s = body["screens"]
    if not isinstance(s, list) or len(s) > 4 or not all(
            isinstance(x, list) and len(x) == 3 and _IS_INT(x[0], 100, 20000) and _IS_INT(x[1], 100, 20000)
            and (x[2] is None or _IS_INT(x[2], 50, 500)) for x in s):
        return "screens"
    g = body["game"]
    if g is not None:
        gh = g.get("gh") if isinstance(g, dict) else None
        if (not isinstance(g, dict) or set(g) != {"w", "h", "fullscreen", "gh"} or not _IS_INT(g["w"], 100, 20000)
                or not _IS_INT(g["h"], 100, 20000) or type(g["fullscreen"]) is not bool
                or not (gh is None or (type(gh) in (int, float) and 0 <= gh <= 64
                                       and abs(gh * 10 - round(gh * 10)) < 1e-9))):
            return "game"
    if body["capture"] not in ("screen", "window", "region"):
        return "capture"
    if body["tesseract"] not in ("ok", "missing", "no_eng"):
        return "tesseract"
    if not _IS_INT(body["seconds_tracking"], 0, 86400):
        return "seconds_tracking"
    if not _IS_INT(body["looks"], 0, MAX_LOOKS):
        return "looks"
    e = body["exp"]
    keys = EXP_KINDS + ("percent_only",)
    if not isinstance(e, dict) or set(e) != set(keys) or not all(_COUNT(e[k]) for k in keys):
        return "exp"
    if sum(e[k] for k in EXP_KINDS) != body["looks"] or e["percent_only"] > e["read"] + e["tesseract"]:
        return "exp"
    for name, pair in PAIRS.items():
        if name in body:
            p = body[name]
            if not isinstance(p, dict) or set(p) != set(pair) or not all(_COUNT(p[k]) for k in pair):
                return name
    if "errors" in body:
        er = body["errors"]
        if (not isinstance(er, dict) or len(er) > 10
                or not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z]{1,40}", k) and _COUNT(n)
                           for k, n in er.items())):
            return "errors"
    if "ms" in body:
        m = body["ms"]
        ok = lambda x: type(x) in (int, float) and x == x and 0 <= x <= 10000
        if (not isinstance(m, dict) or any(k not in ("look_avg", "look_p95", "v2_avg") + V2_MS for k in m)
                or not ok(m.get("look_avg")) or not ok(m.get("look_p95"))
                or any(k in m and not ok(m[k]) for k in ("v2_avg",) + V2_MS)):
            return "ms"
    if "shadow" in body:
        keys = ("agree", "disagree", "v2_only", "old_only", "neither", "skipped", "errors")
        sh = body["shadow"]
        if not isinstance(sh, dict) or set(sh) != set(keys) or not all(_COUNT(sh[k]) for k in keys):
            return "shadow"
    for name in ("shadow_mesos", "shadow_party"):
        if name in body:
            sh = body[name]
            if not isinstance(sh, dict) or set(sh) != set(SHADOW_KEYS) or not all(_COUNT(sh[k]) for k in SHADOW_KEYS):
                return name
    # (ui and shadow_why are also at most 'looks'; no sums across them are checked, so a counting slip never loses a day)
    for name, keys in (("ui", UI_KEYS), ("shadow_why", WHY_KEYS)):
        if name in body:
            c = body[name]
            if not isinstance(c, dict) or set(c) != set(keys) or not all(_IS_INT(c[k], 0, body["looks"]) for k in keys):
                return name
    return None


# --------------------------------------------------------------------------- the daily record

def utc_day(seconds):
    return time.strftime("%Y-%m-%d", time.gmtime(seconds))


def _ascii(text, limit):
    return "".join(c for c in str(text) if " " <= c <= "~")[:limit].strip()


def _new_day():
    return {"looks": 0, "exp": {k: 0 for k in EXP_KINDS + ("percent_only",)},
            **{name: {k: 0 for k in pair} for name, pair in PAIRS.items()},
            "errors": {}, "tracking": 0.0, "ms_total": 0.0, "ms_n": 0, "hist": {}, "games": {}, "snap": {},
            "shadow": {k: 0 for k in SHADOW_KEYS}, "v2_ms": 0.0, "v2_n": 0,
            "ui": {k: 0 for k in UI_KEYS}, "why": {k: 0 for k in WHY_KEYS},
            **{f"shadow_{p}": {k: 0 for k in SHADOW_KEYS} for p in SHADOW_PARTS},
            **{f"{p}_v2_ms": 0.0 for p in SHADOW_PARTS}, **{f"{p}_v2_n": 0 for p in SHADOW_PARTS}, "sent": False}


class HealthLog:
    """The per-day counts, saved to `path` (health.json). `clock` gives the time in seconds (tests pass their own)."""

    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        self.days = {}
        self.last_attempt = 0.0         # when a report was last tried (an hour has to pass before the next)
        self.retry_after = 0.0          # after a network or server problem: not before this time
        self.last_sent = ""             # the day of the latest report the site took
        self._saved_at = 0.0
        self._load()

    # ---- the file
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            days = data.get("days")
            if isinstance(days, dict):
                for day, rec in days.items():
                    if day_number(day) is not None and isinstance(rec, dict):
                        self.days[day] = self._clean(rec)
            for key in ("last_attempt", "retry_after"):
                if type(data.get(key)) in (int, float):
                    setattr(self, key, float(data[key]))
            if day_number(data.get("last_sent")) is not None:
                self.last_sent = data["last_sent"]
        except FileNotFoundError:
            pass
        except Exception:
            self.days = {}              # (a damaged file is just started again)

    @staticmethod
    def _clean(rec):
        """A day record read from the file, with anything of the wrong shape replaced by zeros."""
        out = _new_day()
        num = lambda v: v if type(v) in (int, float) and v == v and 0 <= v <= 1e12 else 0
        out["looks"] = int(num(rec.get("looks")))
        for key in EXP_KINDS + ("percent_only",):
            out["exp"][key] = int(num((rec.get("exp") or {}).get(key) if isinstance(rec.get("exp"), dict) else 0))
        for name, pair in PAIRS.items():
            src = rec.get(name) if isinstance(rec.get(name), dict) else {}
            for key in pair:
                out[name][key] = int(num(src.get(key)))
        if isinstance(rec.get("errors"), dict):
            out["errors"] = {k: int(num(v)) for k, v in list(rec["errors"].items())[:10]
                             if isinstance(k, str) and re.fullmatch(r"[A-Za-z]{1,40}", k)}
        out["tracking"] = float(num(rec.get("tracking")))
        out["ms_total"], out["ms_n"] = float(num(rec.get("ms_total"))), int(num(rec.get("ms_n")))
        if isinstance(rec.get("hist"), dict):
            out["hist"] = {str(k): int(num(v)) for k, v in rec["hist"].items()
                           if str(k).isdigit() and len(str(k)) <= 4 and int(k) <= MAX_BUCKET}     # (only the buckets look() makes)
        if isinstance(rec.get("games"), dict):
            out["games"] = {str(k): int(num(v)) for k, v in list(rec["games"].items())[:12]}
        out["snap"] = HealthLog._clean_snap(rec.get("snap"))
        src = rec.get("shadow") if isinstance(rec.get("shadow"), dict) else {}
        for key in SHADOW_KEYS:
            out["shadow"][key] = int(num(src.get(key)))
        out["v2_ms"], out["v2_n"] = float(num(rec.get("v2_ms"))), int(num(rec.get("v2_n")))
        for name, keys in (("ui", UI_KEYS), ("why", WHY_KEYS), ("shadow_mesos", SHADOW_KEYS), ("shadow_party", SHADOW_KEYS)):
            src = rec.get(name) if isinstance(rec.get(name), dict) else {}
            for key in keys:
                out[name][key] = int(num(src.get(key)))
        for p in SHADOW_PARTS:
            out[f"{p}_v2_ms"], out[f"{p}_v2_n"] = float(num(rec.get(f"{p}_v2_ms"))), int(num(rec.get(f"{p}_v2_n")))
        out["sent"] = rec.get("sent") is True
        return out

    @staticmethod
    def _clean_snap(snap):
        """A snapshot read from the file: only the right kinds of values are kept, or none at all."""
        if not isinstance(snap, dict):
            return {}
        screens = snap.get("screens")
        if (not isinstance(screens, list) or not all(isinstance(s, list) and len(s) == 3 for s in screens[:4])
                or not all(isinstance(snap.get(k), str) for k in ("version", "os", "capture", "tesseract"))):
            return {}
        return {k: snap[k] for k in ("version", "os", "capture", "tesseract")} | {"screens": [list(s) for s in screens[:4]]}

    def save(self, force=True):
        """Writes the record (atomically). Without `force` it only does so every SAVE_EVERY seconds."""
        now = self.clock()
        if not force and now - self._saved_at < SAVE_EVERY:
            return
        self._saved_at = now
        self._drop_old(utc_day(now))
        data = {"days": self.days, "last_attempt": self.last_attempt, "retry_after": self.retry_after,
                "last_sent": self.last_sent}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass                        # (health reports must never get in the way)

    def _drop_old(self, today):
        t = day_number(today)
        for day in list(self.days):
            if day_number(day) < t - (KEEP_DAYS - 1):
                del self.days[day]

    # ---- counting
    def today(self):
        day = utc_day(self.clock())
        if day not in self.days:
            self.days[day] = _new_day()
            earlier = [d for d in self.days if d < day and self.days[d]["snap"]]
            if earlier:                 # (the overlay only takes a snapshot every few minutes: don't lose a day to that gap)
                self.days[day]["snap"] = json.loads(json.dumps(self.days[max(earlier)]["snap"]))
        return self.days[day]

    def look(self, kind, percent_only=False, tries=(), ms=None, tracking=0.0, game=None, shadow=None,
             ui=None, layout=None, shadows=(), why=None):
        """Counts one look. `kind`: one of EXP_KINDS. `tries`: (part, worked) pairs for the other readers.
        `ms`: how long the look took. `tracking`: seconds the tracker has been running since the last look.
        `game`: (w, h, fullscreen, gh) or None. `shadow`: (what reader v2 did, its milliseconds) or None.
        `ui`: "default" / "ideal" when the look knew the UI size, else None. `layout`: the potion bar's layout
        (potion_bar.LAYOUTS) when it was found, else None. `shadows`: (part, what v2 did, its milliseconds) for the
        other parts (SHADOW_PARTS). `why`: why EXP's reader v2 didn't read it (counted when `shadow` is old_only)."""
        rec = self.today()
        if kind not in EXP_KINDS:
            return
        rec["looks"] += 1
        rec["exp"][kind] += 1
        if percent_only and kind in ("read", "tesseract"):
            rec["exp"]["percent_only"] += 1
        for part, worked in tries or ():
            if part in PAIRS:
                rec[part][PAIRS[part][0 if worked else 1]] += 1
        if ms is not None and 0 <= ms < 1e7:
            rec["ms_total"] += ms
            rec["ms_n"] += 1
            b = str(min(MAX_BUCKET, int(ms // BUCKET_MS)))
            rec["hist"][b] = rec["hist"].get(b, 0) + 1
        if shadow and shadow[0] in SHADOW_KEYS:
            rec["shadow"][shadow[0]] += 1
            if shadow[1] is not None and 0 <= shadow[1] < 1e7:
                rec["v2_ms"] += shadow[1]
                rec["v2_n"] += 1
            if shadow[0] == "old_only" and why in WHY_KEYS:
                rec["why"][why] += 1
        if ui in ("default", "ideal"):
            rec["ui"][ui] += 1
        if isinstance(layout, str) and (layout == "keyboard" or layout.startswith("controller")):
            rec["ui"]["controller" if layout.startswith("controller") else "keyboard"] += 1
        for part, what, took in shadows or ():
            if part in SHADOW_PARTS and what in SHADOW_KEYS:
                rec[f"shadow_{part}"][what] += 1
                if took is not None and 0 <= took < 1e7:
                    rec[f"{part}_v2_ms"] += took
                    rec[f"{part}_v2_n"] += 1
        rec["tracking"] += max(0.0, min(MAX_LOOK_GAP, tracking))
        if game:
            w, h, fs, gh = game
            key = f"{int(w)}x{int(h)}/{1 if fs else 0}/{'' if gh is None else round(float(gh), 1)}"
            if key in rec["games"] or len(rec["games"]) < 12:
                rec["games"][key] = rec["games"].get(key, 0) + 1

    def errors(self, counts):
        """Adds exception type names (a dict name -> how many more). Only plain letter names are kept."""
        rec = self.today()
        for name, n in counts.items():
            if not (isinstance(name, str) and re.fullmatch(r"[A-Za-z]{1,40}", name)) or n <= 0:
                continue
            if name in rec["errors"] or len(rec["errors"]) < 10:
                rec["errors"][name] = rec["errors"].get(name, 0) + int(n)

    def snapshot(self, version, system, screens, capture, tesseract):
        """What the day looked like (recorded during the day, since the game may be closed when it is sent)."""
        self.today()["snap"] = {"version": str(version), "os": _ascii(system, 60), "capture": capture,
                                "tesseract": tesseract,
                                "screens": [list(s) for s in list(screens)[:4]]}

    # ---- the body
    def body(self, day, health_id):
        """The report body for `day`, or None if that day has nothing worth sending. Everything is clamped to
        what the site accepts; if it still would not pass, None."""
        rec = self.days.get(day)
        # (a day with no counted look is still sent when it has exception names: every look broke, which is
        # exactly when a report helps)
        if not rec or (rec["looks"] <= 0 and not rec["errors"]) or not rec["snap"]:
            return None
        snap = rec["snap"]
        exp = {k: min(MAX_COUNT, rec["exp"][k]) for k in EXP_KINDS}
        looks = sum(exp.values())
        exp["percent_only"] = min(rec["exp"]["percent_only"], exp["read"] + exp["tesseract"])
        body = {
            "id": health_id, "version": snap.get("version"), "day": day, "os": snap.get("os") or "unknown",
            "screens": [s for s in snap.get("screens", [])][:4], "game": self._game(rec),
            "capture": snap.get("capture"), "tesseract": snap.get("tesseract"),
            "seconds_tracking": min(86400, int(rec["tracking"])), "looks": min(MAX_LOOKS, looks), "exp": exp,
        }
        for name, pair in PAIRS.items():
            body[name] = {k: min(MAX_COUNT, rec[name][k]) for k in pair}
        if rec["errors"]:
            body["errors"] = {k: min(MAX_COUNT, n) for k, n in sorted(rec["errors"].items(), key=lambda kv: -kv[1])[:10]}
        if rec["ms_n"]:
            body["ms"] = {"look_avg": round(min(10000.0, rec["ms_total"] / rec["ms_n"]), 1),
                          "look_p95": round(min(10000.0, self._p95(rec)), 1)}
            if rec["v2_n"]:
                body["ms"]["v2_avg"] = round(min(10000.0, rec["v2_ms"] / rec["v2_n"]), 1)
        if any(rec["shadow"].values()):
            body["shadow"] = {k: min(MAX_COUNT, rec["shadow"][k]) for k in SHADOW_KEYS}
        for p in SHADOW_PARTS:
            if any(rec[f"shadow_{p}"].values()):
                body[f"shadow_{p}"] = {k: min(MAX_COUNT, rec[f"shadow_{p}"][k]) for k in SHADOW_KEYS}
            if rec[f"{p}_v2_n"] and "ms" in body:
                body["ms"][f"{p}_v2_avg"] = round(min(10000.0, rec[f"{p}_v2_ms"] / rec[f"{p}_v2_n"]), 1)
        for name, key, keys in (("ui", "ui", UI_KEYS), ("shadow_why", "why", WHY_KEYS)):
            if any(rec[key].values()):                  # (never more than the looks: the site checks that, and only that)
                body[name] = {k: min(body["looks"], rec[key][k]) for k in keys}
        return body if bad_field(body, utc_day(self.clock())) is None else None

    @staticmethod
    def _game(rec):
        if not rec["games"]:
            return None
        key = max(rec["games"], key=rec["games"].get)
        try:
            size, fs, gh = key.split("/")
            w, h = (int(x) for x in size.split("x"))
            return {"w": w, "h": h, "fullscreen": fs == "1", "gh": float(gh) if gh else None}
        except ValueError:
            return None

    @staticmethod
    def _p95(rec):
        total = sum(rec["hist"].values())
        if not total:
            return 0.0
        seen = 0
        for b in sorted(int(k) for k in rec["hist"]):
            seen += rec["hist"][str(b)]
            if seen >= 0.95 * total:
                return float((b + 1) * BUCKET_MS)
        return 0.0

    # ---- sending
    def due(self, health_id):
        """(day, body) of the oldest finished day to send now, or None. Sends at most one a hour, and not
        before `retry_after`. Days with nothing to send or older than SEND_DAYS are marked done on the way."""
        now = self.clock()
        # (a time saved while the PC's clock was ahead must not block sending for years once the clock is right again:
        # 'later' never waits past the next UTC midnight, and an attempt is never in the future)
        self.last_attempt, self.retry_after = min(self.last_attempt, now), min(self.retry_after, (now // 86400 + 1) * 86400)
        if now - self.last_attempt < SEND_EVERY or now < self.retry_after:
            return None
        today = utc_day(now)
        t = day_number(today)
        for day in sorted(self.days):
            rec = self.days[day]
            if rec["sent"] or day_number(day) >= t:
                continue
            if day_number(day) < t - SEND_DAYS:
                rec["sent"] = True      # (too old: dropped unsent)
                continue
            body = self.body(day, health_id)
            if body is None:
                rec["sent"] = True      # (nothing in it, or it can't be made valid: never sent)
                continue
            return day, body
        return None

    def attempting(self):
        """Call right before a send: an hour has to pass before the next try, even after a restart."""
        self.last_attempt = self.clock()
        self.save()

    def finish(self, day, outcome):
        """Records what the site said: 'ok' / 'dup' (taken), 'bad' / 'big' (refused for good), or 'later'
        (a network or server problem or a limit: try again from tomorrow)."""
        rec = self.days.get(day)
        if outcome in ("ok", "dup", "bad", "big"):
            if rec:
                rec["sent"] = True
            if outcome in ("ok", "dup") and day > self.last_sent:
                self.last_sent = day
        else:
            now = self.clock()
            self.retry_after = (now // 86400 + 1) * 86400
        self.save()
