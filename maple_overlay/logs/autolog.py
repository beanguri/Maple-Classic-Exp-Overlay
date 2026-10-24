"""The EXP log: five-minute stretches turned into one line per day, map, level, class and party size."""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import time
from datetime import date
from difflib import SequenceMatcher

from maple_overlay.config import (
    BACKUPS_KEPT, BLOCKS_KEPT, END_LOOK, EXTRA_KEYS, LOG_BLOCK, log_error, LOG_PATH, LOG_TEXT_PATH, LOG_VERSION,
    MAX_LOOKS, MAX_MESOS_HR, NOTE_MAX, PAUSED_WAIT, RUN_ALERT, RUN_SECONDS, SITE_GAP, START_GRACE, VERSION,
)
from maple_overlay.logs.entries import clean_note, format_entry, notes_of
from maple_overlay.vision.maps import _numbers


# Each line the tracker writes is sealed: a code worked out from its numbers. A line edited
# (or added) by hand doesn't match its seal any more, so it's known not to be the tracker's
# own. (Someone who digs into the program could still forge one, which is why anything
# shared will also be checked against what other players get.)
# (The old name stays in the key: changing it would unseal every log written so far.)
_SEAL_KEY = hashlib.sha256(b"Maple EXP/hr log seal: keep this log honest").digest()

_SEALED_LINE = ("date", "map_read", "level", "party", "class", "exp", "seconds", "mesos", "mesos_seconds")

_SEALED_LINE_POTIONS = _SEALED_LINE + ("potion_cost",)     # lines written with a potion cost (older lines keep their seal as it was)

_SEALED_RUN = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
               "seconds", "mesos", "mesos_seconds", "version")

class AutoLog:
    """EXP/hr and mesos/hr per day, map, level, class and party size, measured over 5-minute
    stretches of non-stop EXP gain, plus 15-minute runs (shared with the stats site, if agreed).

    A stretch starts with an EXP gain and is thrown away if no EXP comes in for `gap`
    seconds, or the map or party size changes. Each finished stretch is added to that day's
    entry for the map, level, class and party size, so the entry gets more accurate the
    longer you play there. Mesos are counted for a stretch when the mesos were read during it.

    Alongside, a run goes on as long as the stretches do (a level-up doesn't stop it, a new
    map or party size does). Every full 15 minutes is saved as a run of its own.

    Every change re-reads the log file first and saves straight away, so lines written by
    anyone else (another copy of the app, or by hand) are never overwritten.
    """

    def __init__(self, gap_seconds, path=LOG_PATH, text_path=LOG_TEXT_PATH):
        self.gap = gap_seconds
        self.path, self.text_path = path, text_path
        self.entries, self.aliases, self.runs = [], {}, []   # aliases: misread/renamed map -> name
        self.install = secrets.token_hex(8)           # this log's own id, part of its seals
        self._kept = set()                            # problems a copy of the file was kept for
        self.looks, self.epoch = [], None             # inventory looks (when, mesos gained so far)
        self.pending = None                           # a finished run waiting for a look after its end
        self._misreads = ()                           # the MesoTracker's misreads, as last given
        version = self._load()
        if version < 2:
            def clear_old_mesos():                    # logged by the old, unreliable mesos counting
                for e in self.entries:
                    if "party" in e:
                        e["mesos"] = e["mesos_seconds"] = 0
            self._change(clear_old_mesos)
        elif version < LOG_VERSION:
            self._change(lambda: None)                # saves this log's id with it
        if any(r.get("pending") for r in self.runs):
            # Closed while a run waited for a look: it goes as it was saved, with EXP only.
            self._change(lambda: [r.pop("pending", None) for r in self.runs])
        self.reset_clock()

    @staticmethod
    def key(e):
        return e["date"], e["map"], e["level"], e.get("party"), e.get("class")

    # ---- seals
    def _seal(self, record, fields):
        data = json.dumps([record.get(k) for k in fields], separators=(",", ":"))
        return hmac.new(_SEAL_KEY + self.install.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]

    @staticmethod
    def _line_fields(e):
        return _SEALED_LINE_POTIONS if "potion_cost" in e else _SEALED_LINE

    def sealed(self, e, fields=None):
        """True for a line exactly as the tracker wrote it."""
        fields = fields or self._line_fields(e)
        return isinstance(e.get("seal"), str) and hmac.compare_digest(e["seal"], self._seal(e, fields))

    def edited(self, e):
        """True for a line that was changed outside the tracker (older lines have no seal)."""
        return "seal" in e and not self.sealed(e)

    def _reseal(self, e, ok):
        e["seal"] = self._seal(e, self._line_fields(e)) if ok else "edited"

    # ---- the file
    def _load(self):
        """Reads the log file into memory (keeps what's there if it can't be read).
        Returns the file's format version."""
        if not self.path.exists():
            return LOG_VERSION
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries, aliases = data.get("entries", []), data.get("aliases", {})
            runs = data.get("runs", [])
            if not isinstance(entries, list) or not isinstance(aliases, dict) or not isinstance(runs, list):
                raise ValueError("unexpected contents")
        except Exception as e:
            self._keep_copy("reading the EXP log", e)        # damaged, e.g. edited by hand
            return LOG_VERSION
        good = [e for e in entries if self._valid(e)]
        if len(good) < len(entries):
            self._keep_copy(f"reading the EXP log: left out {len(entries) - len(good)} damaged lines")
        self.entries = good
        self.aliases = {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}
        self.runs = [r for r in runs if isinstance(r, dict)]   # (only sealed ones are ever shared)
        if isinstance(data.get("install"), str) and re.fullmatch(r"[0-9a-f]{16}", data["install"]):
            self.install = data["install"]
        version = data.get("version", 1)
        return version if type(version) is int else LOG_VERSION

    def _keep_copy(self, what, exc=None):
        """Keeps a copy of the log file before it's saved over (once per problem)."""
        backup = self.path.with_name(f"exp_log.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json")
        if what not in self._kept:
            self._kept.add(what)
            try:
                shutil.copy2(self.path, backup)
            except Exception:
                pass
            log_error(f"{what} (the file was copied to {backup.name})", exc)

    @staticmethod
    def _valid(e):
        """An entry with everything the log needs, each of the right kind, as the overlay writes
        them. Anything else is left out, not crashed on: the file may have been edited by hand."""
        def number(x):
            return type(x) in (int, float) and math.isfinite(x)

        def optional(key, ok, empty=False):           # missing (or empty), or of the right kind
            return key not in e or (empty and e[key] is None) or ok(e[key])
        try:
            date.fromisoformat(e["date"])
            return (isinstance(e["map"], str) and e["map"].strip() != "" and len(e["map"]) <= 200
                    and number(e["exp"]) and number(e["seconds"]) and e["seconds"] >= 0
                    and (e["level"] is None or (type(e["level"]) is int and 1 <= e["level"] <= 300))
                    and optional("party", lambda v: type(v) is int and 1 <= v <= 6, empty=True)
                    and optional("class", lambda v: isinstance(v, str), empty=True)
                    and optional("map_read", lambda v: isinstance(v, str), empty=True)
                    and optional("seal", lambda v: isinstance(v, str))
                    and optional("updated", number)
                    and optional("mesos", number) and optional("mesos_seconds", number)
                    and optional("potion_cost", lambda v: number(v) and v >= 0)
                    and optional("potion_seconds", lambda v: number(v) and v >= 0)
                    and optional("potions", lambda v: isinstance(v, dict))
                    and optional("estimated", lambda v: type(v) is bool)
                    and (not e.get("mesos_seconds") or number(e.get("mesos"))))
        except Exception:
            return False

    def _change(self, change):
        """Applies change() to the latest log on disk and saves it."""
        self._load()
        result = change()
        self.save()
        return result

    # ---- tracking
    def reset_clock(self):
        self.start = self.last_gain = self.block_map = self.block_party = self.meso0 = self.pot0 = None
        self.x0 = None                                   # the extras (kills, deaths...) as the stretch began
        self.block_exp = 0
        self.run = None                                  # the 15-minute run going on, if any
        self.chain = None                                # when the runs going on straight after each other began
        self.block_estimated = False                     # some of the stretch's EXP was estimated from a % alone

    def gain(self, now, amount):
        if amount > 0:
            starts = self.start is None
            if starts:
                self.start, self.block_exp = now, 0          # this gain starts the clock
            else:
                self.block_exp += amount
            if self.run is None:                             # ...and a run's
                self.run = {"start": now, "exp": 0, "map": None}
                self.chain = now if self.chain is None else self.chain
            elif not starts:
                self.run["exp"] += amount
            self.last_gain = now
        elif self.start is not None:
            self.block_exp += amount                         # a misread too high, taken back
            if self.run:
                self.run["exp"] += amount

    def tick(self, now, map_name, level, party=1, mesos=None, job=None, map_read=None, hidden=False, potions=None,
             window_at=None, misreads=(), meso_epoch=0, estimated=False, extras=None):
        """Call regularly while tracking. mesos is the MesoTracker's last look (mesos gained so
        far, when read), however old, or None: a stretch's mesos are those gained between its
        first and last look (a run's: see _site_mesos). job is the class; map_read the map name exactly as read;
        potions ({name: used so far}, their cost so far) or None when potions aren't counted.
        window_at, misreads and meso_epoch: see MesoTracker.for_log. estimated: the EXP now coming in is
        estimated from a % alone (the line and run say so, and such a run is never shared).
        extras: counters so far (EXTRA_KEYS: kills, deaths...) plus "lvl_total" / "remaining" (the EXP the level
        needs, and what is left of it); a stretch's are the change over it.
        Returns the entry when one was logged."""
        self._note_look(mesos, meso_epoch)
        self._misreads = misreads
        if estimated and self.start is not None:
            self.block_estimated = True
        if self.start is None:
            return None
        if now - self.last_gain > self.gap:
            self.reset_clock()                               # no EXP for too long: start over
            return None
        if self.run and now - self.last_gain > SITE_GAP:
            self.run = self.chain = None                     # ...for a run, 15 s is too long
        if map_name:
            if self.block_map and map_name != self.block_map:
                self.reset_clock()                           # changed maps: start over
                return None
            self.block_map = map_name
        if self.block_party is not None and party != self.block_party:
            self.reset_clock()                               # party size changed: start over
            return None
        self.block_party = party
        if self.meso0 is None and mesos and mesos[1] >= self.start - 2:
            self.meso0 = mesos                               # mesos as the stretch began
        if self.x0 is None and extras is not None:
            self.x0 = dict(extras)                           # kills, deaths... as the stretch began
        if self.pot0 is None and potions is not None:
            self.pot0 = (dict(potions[0]), potions[1])       # potions used as the stretch began
        self._run_tick(now, map_name, level, party, job, map_read, hidden, window_at, estimated)
        if hidden or now - self.start < LOG_BLOCK:       # (hidden: the map may have changed, so nothing is saved yet)
            return None
        if self.block_exp <= 0:                              # nothing gained after all
            self.start, self.block_exp = now, 0
            return None
        meso_gain = meso_seconds = 0
        if self.meso0 and mesos and mesos[1] > self.meso0[1]:
            meso_gain, meso_seconds = mesos[0] - self.meso0[0], mesos[1] - self.meso0[1]
        used = cost = None
        if potions is not None and self.pot0 is not None:
            cost = max(0, potions[1] - self.pot0[1])
            used = {n: c - self.pot0[0].get(n, 0) for n, c in potions[0].items() if c > self.pot0[0].get(n, 0)}
        entry = self._add(self.block_map or "Unknown map", level, party, self.block_exp,
                          now - self.start, meso_gain, meso_seconds, job, map_read or self.block_map, cost, used,
                          self.block_estimated, self._extras_since(extras, now - self.start))
        self.start, self.block_exp, self.block_estimated = now, 0, False       # the next 5 minutes start now
        self.x0 = dict(extras) if extras is not None else None
        self.meso0 = mesos if mesos and now - mesos[1] < 10 else None
        self.pot0 = (dict(potions[0]), potions[1]) if potions is not None else None
        return entry

    def _extras_since(self, extras, seconds):
        """What the extras counted over the stretch that just ended ({} when they weren't given)."""
        if extras is None or self.x0 is None:
            return {}
        out = {k: max(0, extras.get(k, 0) - self.x0.get(k, 0)) for k in EXTRA_KEYS if k != "x_seconds"}
        out["x_seconds"] = seconds
        for k in ("lvl_total", "remaining"):
            if extras.get(k):
                out[k] = extras[k]
        return out

    def _run_tick(self, now, map_name, level, party, job, map_read, hidden=False, window_at=None, estimated=False):
        r = self.run
        if r is None:
            return
        if r["map"] is None:                                 # what the run is about, once known
            if not map_name:
                r["start"] = now                             # no map yet: the run can't start
                r["exp"] = 0
                return
            r.update(map=map_name, map_read=map_read or map_name, party=party, job=job,
                     level_start=level, wall=time.time() - (now - r["start"]),
                     chain=min(self.chain if self.chain is not None else now, r["start"]))
        if job and r["job"] and job != r["job"]:
            self.run = {"start": now, "exp": 0, "map": None}  # a different class: a new run
            self.chain = now
            return
        r["job"] = r["job"] or job
        r["level"] = level
        if estimated:
            r["estimated"] = True                            # (a % alone: kept on this PC, never shared)
        if window_at is not None and window_at >= r["start"]:
            r["window"] = True                               # a shop, storage or trade: not a clean run
        if hidden or now - r["start"] < RUN_SECONDS:
            return
        if r["exp"] > 0:
            self._end_run(r, now)
        self.run = {"start": now, "exp": 0, "map": None}     # straight on into the next run
        self._prune()
        self._run_tick(now, map_name, level, party, job, map_read, hidden, window_at, estimated)

    # ---- what a run takes to the website (fail closed: any doubt and it goes with EXP only)
    def _note_look(self, mesos, epoch):
        if epoch != self.epoch:                              # the mesos count started over: older looks don't compare
            self.epoch, self.looks = epoch, []
            if self.pending:
                self.pending["no_mesos"] = True              # (its looks are gone: it goes with EXP only)
        if mesos and mesos[1] is not None and (not self.looks or mesos[1] > self.looks[-1][0]):
            self.looks.append((mesos[1], mesos[0]))
            if self.run is None and self.pending is None:
                self._prune()
            elif len(self.looks) > MAX_LOOKS:                # (a run waiting or going on for hours: forget the oldest)
                del self.looks[:len(self.looks) - MAX_LOOKS]

    def _prune(self):
        """Forgets the looks no run going on or waiting needs (keeps the last one before them)."""
        live = [r["start"] for r in (self.run, self.pending) if r]
        t = min(live) if live else math.inf
        before = [k for k in self.looks if k[0] <= t]
        self.looks = before[-1:] + [k for k in self.looks if k[0] > t]

    def _end_run(self, r, now):
        """Saves a finished run. One with a shop, storage or trade window seen, or an unknown
        level, is never shared: it keeps the mesos between its first and last look. A clean one
        is saved with EXP only and, given a look at its start, waits for one after its end (settle)."""
        seconds = now - r["start"]
        if r.get("window") or r.get("estimated") or r["level_start"] is None or r["level"] is None:
            inside = [k for k in self.looks if r["start"] - 2 <= k[0] <= now]
            gain = gain_s = 0
            if len(inside) >= 2:
                gain, gain_s = inside[-1][1] - inside[0][1], round(inside[-1][0] - inside[0][0], 1)
            self._add_run(r, seconds, gain, gain_s, local={"window": True} if r.get("window") else {})
            return
        if self.pending:
            self._settle(now)                                # (can't still be waiting, but never lose one)
        waits = self._gained_at(r["start"], r["chain"]) is not None
        run = self._add_run(r, seconds, 0, 0, local={"pending": True} if waits else {})
        if waits:
            self.pending = dict(r, end=now, started=run["started"], paused_at=None)

    def _gained_at(self, t, chain=None, upto=None):
        """Mesos gained by time t, spread evenly between the looks around it: (gained, looks
        used), or None. Needs a look at or before t (not from before `chain`'s runs; or just
        after t when there's none), or with `upto` a look at or after t, no later than upto."""
        before = [k for k in self.looks if k[0] <= t]
        after = [k for k in self.looks if k[0] >= t]
        a = before[-1] if before else None
        b = after[0] if after else None
        if upto is not None:
            if a is None or b is None or b[0] > upto:
                return None
        elif a is None or a[0] < chain - END_LOOK:
            if b is None or b[0] > t + START_GRACE:
                return None
            return b[1], (b,)                                # (a little less than it was: never more)
        if b is None or b[0] == a[0]:
            return a[1], (a,)
        return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]), (a, b)

    def _site_mesos(self, p, upto, misreads):
        """A waiting run's (mesos, mesos seconds) for the website, or (0, 0) when in doubt: no
        look at its start or after its end, a look later proven a misread, below zero, or
        above the site's ceiling."""
        start, end = self._gained_at(p["start"], p["chain"]), self._gained_at(p["end"], upto=upto)
        if start is None or end is None or p.get("no_mesos"):
            return 0, 0
        if any(t0 <= k[0] <= t1 for k in start[1] + end[1] for t0, t1 in misreads):
            return 0, 0
        mesos, seconds = round(end[0] - start[0]), round(p["end"] - p["start"], 1)
        if mesos < 0 or seconds <= 0 or mesos / seconds * 3600 > MAX_MESOS_HR:
            return 0, 0
        return mesos, seconds

    def _end_look(self, p):
        """A waiting run's mesos at its end, given a look soon enough after it (see _gained_at)."""
        return self._gained_at(p["end"], upto=self._deadline(p))

    @staticmethod
    def _deadline(p):
        return p["end"] + END_LOOK if p["paused_at"] is None else p["paused_at"] + PAUSED_WAIT

    def settle(self, now, running, mesos=None, window_at=None, misreads=(), meso_epoch=0):
        """Call regularly, also while paused. A finished run waits for a look after its end:
        while tracking goes on it waits END_LOOK seconds (so a misread can still snap back);
        paused, it goes as soon as the look comes, or after PAUSED_WAIT seconds (see alert).
        Returns True when a run is ready to be shared."""
        self._note_look(mesos, meso_epoch)
        self._misreads = misreads
        p = self.pending
        if p is None:
            return False
        if window_at is not None and window_at >= p["end"]:
            p["no_mesos"] = True                             # a shop, storage or trade before its end look
        if running:
            p["paused_at"] = None
        elif p["paused_at"] is None:
            p["paused_at"] = now
        found = self._end_look(p) is not None
        if now < self._deadline(p) and (running or not found) and not p.get("no_mesos"):
            return False
        self._settle(now, misreads)
        return True

    def _settle(self, now, misreads=None):
        p, self.pending = self.pending, None
        misreads = self._misreads if misreads is None else misreads
        mesos, seconds = self._site_mesos(p, self._deadline(p), misreads)

        def apply():
            for r in self.runs:
                if r.get("started") == p["started"] and r.get("pending"):
                    r.pop("pending")
                    r["mesos"], r["mesos_seconds"] = mesos, seconds
                    r["seal"] = self._seal(r, _SEALED_RUN)
        self._change(apply)
        self._prune()

    def alert(self, now, running):
        """Asks for the inventory when tracking stops while a run waits for a look."""
        p = self.pending
        if (p is None or running or p.get("no_mesos") or self._end_look(p) is not None
                or (self.looks and now - self.looks[-1][0] <= 3)):     # (open already: the look is on its way)
            return None
        return RUN_ALERT

    def run_progress(self, now):
        """Seconds into the 15-minute run going on, or None."""
        r = self.run
        return None if r is None or r["map"] is None else max(0.0, now - r["start"])

    def runs_done(self, map_name, party, job, day=None):
        """How many runs were finished (sealed) on a day for a map, party size and class."""
        day = day or date.today().isoformat()
        return sum(1 for r in self.runs if r.get("date") == day and r.get("map") == map_name
                   and r.get("party") == party and r.get("class") == job and self.sealed(r, _SEALED_RUN))

    def unshared_runs(self):
        """Finished runs (exactly as the overlay saved them) not shared yet: only clean ones
        (no shop, storage or trade window seen, no EXP estimated from a % alone), with a known level, not
        waiting for a look."""
        return [r for r in self.runs if not r.get("shared") and not r.get("window") and not r.get("pending")
                and not r.get("estimated") and r.get("level_start") is not None and r.get("level_end") is not None
                and self.sealed(r, _SEALED_RUN)][:50]

    def mark_shared(self, started):
        def apply():
            for r in self.runs:
                if r.get("started") in started:
                    r["shared"] = True
        self._change(apply)

    def seconds_left(self, now):
        return None if self.start is None else max(0.0, LOG_BLOCK - (now - self.start))

    def _add(self, map_name, level, party, exp, seconds, mesos, meso_seconds, job=None, map_read=None,
             potion_cost=None, potions=None, estimated=False, extras=None):
        def add():
            key = (date.today().isoformat(), map_name, level, party, job)
            for e in self.entries:
                if self.key(e) == key:
                    ok = self.sealed(e)                      # still exactly the tracker's own?
                    if potion_cost is not None or "potion_cost" in e:     # (the time potions were counted over)
                        e["potion_seconds"] = (e.get("potion_seconds", e["seconds"] if "potion_cost" in e else 0)
                                               + (seconds if potion_cost is not None else 0))
                    e["exp"] += exp
                    e["seconds"] += seconds
                    e["mesos"] = e.get("mesos", 0) + mesos
                    e["mesos_seconds"] = e.get("mesos_seconds", 0) + meso_seconds
                    if potion_cost is not None:
                        e["potion_cost"] = e.get("potion_cost", 0) + potion_cost
                        used = dict(e.get("potions") or {})
                        for n, c in (potions or {}).items():
                            used[n] = used.get(n, 0) + c
                        e["potions"] = used
                    if estimated:
                        e["estimated"] = True                # (not sealed: it only says how the EXP was got)
                    self._add_extras(e, extras, exp, seconds)
                    if "seal" in e:
                        self._reseal(e, ok)
                    break
            else:
                e = {"date": key[0], "map": map_name, "map_read": map_read or map_name, "level": level,
                     "party": party, "class": job, "exp": exp, "seconds": seconds, "mesos": mesos,
                     "mesos_seconds": meso_seconds}
                if potion_cost is not None:
                    e["potion_cost"], e["potions"], e["potion_seconds"] = potion_cost, dict(potions or {}), seconds
                if estimated:
                    e["estimated"] = True
                self._add_extras(e, extras, exp, seconds)
                self._reseal(e, True)
                self.entries.append(e)
            e["updated"] = time.time()
            return e
        return self._change(add)

    @staticmethod
    def _add_extras(e, extras, exp, seconds):
        """The stretch's extra counters go on the line (they are not sealed: they only describe it), and the stretch
        itself is kept as one point of the line's pace chart."""
        blocks = list(e.get("blocks") or [])
        blocks.append([exp, round(seconds, 1)])
        e["blocks"] = blocks[-BLOCKS_KEPT:]
        for k, v in (extras or {}).items():
            if k in ("lvl_total", "remaining"):
                e[k] = v
            elif k in EXTRA_KEYS:
                e[k] = e.get(k, 0) + v

    def _add_run(self, r, seconds, mesos, meso_seconds, local=None):
        run = {"date": date.fromtimestamp(r["wall"]).isoformat(),
               "started": time.strftime("%Y-%m-%dT%H:%M", time.localtime(r["wall"])),
               "map": r["map"], "map_read": r["map_read"], "class": r["job"],
               "level_start": r["level_start"], "level_end": r["level"], "party": r["party"],
               "exp": r["exp"], "seconds": round(seconds, 1), "mesos": mesos,
               "mesos_seconds": round(meso_seconds, 1), "version": VERSION}
        run["seal"] = self._seal(run, _SEALED_RUN)
        local = dict(local or {})
        if r.get("estimated"):
            # An older overlay doesn't know the flag and would share a sealed run: a seal that never verifies stops it.
            run["seal"], local["estimated"] = "estimated", True
        run.update(local)                                    # (kept on this PC only: not sealed, never sent)
        self._change(lambda: self.runs.append(run))
        return run

    def canonical(self, name, also=()):
        """The name to log a map under: applies renames and fixes small misreads (matching
        the maps in the log, and any names in `also`, e.g. the map you were just on)."""
        name = self.aliases.get(name, name)
        best, score = name, 0.0
        for known in {e["map"] for e in self.entries} | set(self.aliases.values()) | {n for n in also if n}:
            if _numbers(known) == _numbers(name):
                s = SequenceMatcher(None, known.lower(), name.lower()).ratio()
                if s > score:
                    best, score = known, s
        return best if score >= 0.85 else name

    def rename(self, entry, new_name):
        """Renames a map (and remembers it for future reads). The map name as read stays on
        record (map_read), so renamed lines keep their seal."""
        old, key = entry["map"], self.key(entry)

        def apply():
            if old == "Unknown map":
                for e in self.entries:                       # just this one
                    if self.key(e) == key:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
            else:
                self.aliases[old] = new_name                 # and future reads of the old name
                for k, v in self.aliases.items():
                    if v == old:
                        self.aliases[k] = new_name
                for e in self.entries:
                    if e["map"] == old:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
                for r in self.runs:
                    if r.get("map") == old:
                        r["map"] = new_name                  # (runs are sealed by map_read)
            merged = {}
            for e in self.entries:                           # combine entries that now match
                if self.key(e) in merged:
                    m = merged[self.key(e)]
                    ok = self.sealed(m) and self.sealed(e)
                    for field in ("exp", "seconds", "mesos", "mesos_seconds", "potion_cost"):
                        if field in e:
                            m[field] = m.get(field, 0) + e[field]
                    if e.get("potions"):
                        m["potions"] = {n: (m.get("potions") or {}).get(n, 0) + (e["potions"] or {}).get(n, 0)
                                        for n in {*(m.get("potions") or {}), *e["potions"]}}
                    m["updated"] = max(m.get("updated", 0), e.get("updated", 0))
                    if "seal" in m or "seal" in e:
                        self._reseal(m, ok)
                    both = notes_of([m, e])                   # a note on either line is kept
                    if both and both != m.get("note"):
                        m["note"] = both
                else:
                    merged[self.key(e)] = e
            self.entries = list(merged.values())

        self._change(apply)
        if old != "Unknown map" and self.block_map == old:
            self.block_map = new_name
        if old != "Unknown map" and self.run and self.run.get("map") == old:
            self.run["map"] = new_name

    def set_note(self, entries, text):
        """Writes a personal note on these log lines (the same one on each: e.g. a map's party
        sizes shown as one line). An empty note takes it off. Returns True if anything changed.

        A note only ever lives in this log file, on this PC. It isn't one of the sealed fields,
        so a line stays as trusted as it was, and it's never part of a run: what's shared with
        the stats site is made from the runs alone (see post_runs)."""
        keys = {self.key(e) for e in entries}
        text = clean_note(text)[:NOTE_MAX]

        def apply():
            changed = False
            for e in self.entries:
                if self.key(e) not in keys:
                    continue
                if text and e.get("note") != text:
                    e["note"], changed = text, True
                elif not text and "note" in e:
                    del e["note"]
                    changed = True
            return changed

        return self._change(apply)

    def delete(self, entry):
        key = self.key(entry)

        def apply():
            self.entries = [e for e in self.entries if self.key(e) != key]

        self._change(apply)

    def newest_first(self):
        return sorted(self.entries, key=lambda e: e.get("updated") or 0, reverse=True)

    def _back_up(self):
        """Before the first save of each day: a copy of the log as it was, in backups/ (the last
        few days are kept), so that whatever goes wrong, a recent copy of it all, notes
        included, is still there. To go back to one, close the overlay and copy it over
        exp_log.json."""
        try:
            if not self.path.exists():
                return
            folder = self.path.parent / "backups"
            copy = folder / f"exp_log-{date.today().isoformat()}.json"
            if copy.exists():
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))     # (a damaged file has its own copy)
            if not (data.get("entries") or data.get("runs")):
                return                                                  # nothing in it worth a backup
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, copy)
            for old in sorted(folder.glob("exp_log-*.json"))[:-BACKUPS_KEPT]:
                old.unlink()
        except Exception as e:
            log_error("backing up the EXP log", e)

    def save(self):
        self._back_up()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": LOG_VERSION, "install": self.install, "entries": self.entries,
                                       "aliases": self.aliases, "runs": self.runs}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self.text_path.write_text("".join(format_entry(e) + "\n" for e in self.newest_first()),
                                      encoding="utf-8")
        except Exception as e:
            log_error("saving the EXP log", e)
