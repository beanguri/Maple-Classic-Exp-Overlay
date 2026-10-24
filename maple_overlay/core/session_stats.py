"""Session window numbers, the Solo / Party split, and the CSV event log.

This is a side counter: it never changes what the tracker, the log (exp_log.json) or the shared
runs do. App calls SessionStats.update() every half second with the trackers' running totals;
the stats work out what was gained since the last call and put it in the bucket for the party
size at that moment (1 = solo, 2 to 6 = a party of that size). Reset Session only clears these
counters, nothing else.

What it can and can't say: the game never shows who an EXP gain "came from". Solo / Party is
therefore what happened while you were alone in the party list versus while you were in a
party, not how much the other players gave you.

Nothing here reads the screen, the network or tkinter, so it is easy to test.
"""
import csv
import os
import time
from datetime import datetime

CSV_HEADER = ("time", "event", "map", "level", "party", "session_seconds", "exp", "mesos", "solo_exp",
              "party_exp", "levels", "deaths", "note")
MAX_CSV_BYTES = 5_000_000       # past this the file becomes <name>.old.csv and a new one starts
SUMMARY_EVERY = 300             # seconds of tracking between summary lines
MAX_STEP = 5.0                  # a gap between two updates longer than this isn't tracking time
PENDING_SECONDS = 20.0          # an EXP message line that no gain on the bar matched is forgotten after this
OWED_SECONDS = 8.0              # a gain on the bar waits this long for its message lines


class Bucket:
    """What one kind of play (solo, or a party size) gained."""

    def __init__(self):
        self.seconds, self.exp, self.mesos = 0.0, 0, 0
        self.own = self.bonus = self.other = 0       # (own: your kills, other: a party member's kills) the part of `exp` whose split the game's messages confirmed

    def per_hour(self, value):
        return value / self.seconds * 3600 if self.seconds >= 10 and value is not None else None

    @property
    def exp_hr(self):
        return self.per_hour(self.exp)

    @property
    def mesos_hr(self):
        return self.per_hour(self.mesos)


class CsvLog:
    """Appends events to a CSV file (UTF-8 with a BOM, so Excel shows it right). Never raises:
    a failed write is passed to `on_error` and the log carries on."""

    def __init__(self, path, on_error=None):
        self.path, self.on_error = path, on_error

    def write(self, row):
        try:
            self._rotate()
            new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
            with open(self.path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(CSV_HEADER)
                w.writerow([row.get(k, "") for k in CSV_HEADER])
        except Exception as e:                      # (a full disk or a file open in Excel must not stop tracking)
            if self.on_error:
                self.on_error("writing the session log", e)

    def _rotate(self):
        if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_CSV_BYTES:
            old = os.path.splitext(self.path)[0] + ".old.csv"
            os.replace(self.path, old)


class SessionStats:
    def __init__(self, csv_log=None, clock=time.monotonic, wall=datetime.now):
        self.csv, self.clock, self.wall = csv_log, clock, wall
        self.solo, self.party = Bucket(), {}        # party: size (2 to 6) -> Bucket
        self.levels = self.deaths = 0
        self.started = None                         # wall-clock time of the first tracked second
        self._last = None                           # (clock, running) at the previous update
        self._tot = None                            # the trackers' totals at the previous update
        self._state = (None, None, None)            # map, party, running at the previous update
        self._next_summary = SUMMARY_EVERY
        self.map, self.level, self.now_party = None, None, 1
        self._prev_lines, self._pending = [], []    # EXP message lines: those on screen at the last look; those not yet matched
        self._owed, self._owed_since = 0, 0.0       # EXP the bar gained that its message lines haven't explained yet
        self.on_split = None                        # f(own, bonus): told when a gain was split (the log counts them too)
        self.on_debug = None                        # f(text): told what the split saw (App writes it to split_debug.log)

    # ---- numbers
    @property
    def seconds(self):
        return self.solo.seconds + sum(b.seconds for b in self.party.values())

    @property
    def exp(self):
        return self.solo.exp + sum(b.exp for b in self.party.values())

    @property
    def mesos(self):
        return self.solo.mesos + sum(b.mesos for b in self.party.values())

    def party_all(self):
        """All the party sizes together, as one bucket."""
        b = Bucket()
        for x in self.party.values():
            b.seconds += x.seconds
            b.exp += x.exp
            b.mesos += x.mesos
            b.own += x.own
            b.bonus += x.bonus
            b.other += x.other
        return b

    def total(self):
        """Solo and every party together, as one bucket."""
        b = self.party_all()
        b.seconds += self.solo.seconds
        b.exp += self.solo.exp
        b.mesos += self.solo.mesos
        b.own += self.solo.own
        b.bonus += self.solo.bonus
        b.other += self.solo.other
        return b

    def rows(self):
        """[(label, bucket)] for the Solo / Party table: Solo, Party, Total. When more than one party size
        has been played, each size follows underneath."""
        party = self.party_all()
        rows = [("Solo", self.solo), ("Party", party)]
        if party.own or party.bonus or party.other:           # (only once the messages have explained some of it)
            for label, value in (("   Solo", party.own), ("   Party bonus", party.bonus), ("   Party kills", party.other),
                                 ("   Other", party.exp - party.own - party.bonus - party.other)):
                if label.strip() in ("Party kills", "Other") and value <= 0:
                    continue
                part = Bucket()
                part.seconds, part.exp, part.mesos = party.seconds, value, None   # (mesos: not split)
                rows.append((label, part))
        rows.append(("Total", self.total()))
        if len(self.party) > 1:
            rows += [(f"   {n} players", self.party[n]) for n in sorted(self.party)]
        return rows

    # ---- feeding
    @staticmethod
    def _delta(total, before):
        """What a running total gained. A total that went down was reset (another map, Reset): it
        counts from zero again."""
        if total is None:
            return 0
        if before is None:
            return 0
        return total - before if total >= before else total

    def update(self, running, party, exp_total, mesos_total, levels_total, deaths_total, map_name, level,
               msg_lines=None, new_lines=None):
        now = self.clock()
        party = max(1, int(party or 1))
        totals = (exp_total or 0, mesos_total or 0, levels_total or 0, deaths_total or 0)
        d = [0, 0, 0, 0] if self._tot is None else [self._delta(a, b) for a, b in zip(totals, self._tot)]
        self._tot = totals
        was = self._last
        self._last = (now, running)
        self.map, self.level, self.now_party = map_name, level, party
        self._events(running, party, map_name, level, d)
        if not running:
            return
        if self.started is None:
            self.started = self.wall()
        bucket = self.solo if party == 1 else self.party.setdefault(party, Bucket())
        if was and was[1]:
            step = now - was[0]
            if 0 < step <= MAX_STEP:
                bucket.seconds += step
        bucket.exp += max(0, d[0])
        self._split(now, party, bucket, msg_lines, max(0, d[0]), new_lines)
        bucket.mesos += max(0, d[1])
        self.levels += max(0, d[2])
        self.deaths += max(0, d[3])
        if self.seconds >= self._next_summary:
            self._next_summary = (int(self.seconds // SUMMARY_EVERY) + 1) * SUMMARY_EVERY
            self._write("summary", note="every 5 minutes of tracking")

    # ---- own EXP vs the party's bonus, from the game's messages
    @staticmethod
    def _new_lines(prev, cur):
        """The lines in `cur` (the EXP message lines on screen now, oldest first) that weren't in `prev`: the
        old ones scroll up and fade, new ones appear below."""
        for k in range(len(prev) + 1):
            tail = prev[k:]
            if cur[:len(tail)] == tail:
                return cur[len(tail):]
        return cur

    def _split(self, now, party, bucket, msg_lines, gain, new_lines=None):
        """msg_lines: [(kind, value, y)] read from the screen this time, or None when it wasn't looked at. A gain
        on the EXP bar is split only when the new lines add up to it exactly; otherwise it stays unsplit."""
        if new_lines:                                        # (already the new ones: read several times a second)
            self._debug(f"party={party} new={list(new_lines)}")
            self._pending += [(k, v, now) for k, v in new_lines]
        if msg_lines is not None:
            cur = [(k, v) for k, v, _ in msg_lines]
            new = self._new_lines(self._prev_lines, cur)
            if new:
                self._debug(f"party={party} screen={cur} new={new}")
            self._prev_lines = cur
            self._pending += [(k, v, now) for k, v in new]
        self._pending = [p for p in self._pending if now - p[2] <= PENDING_SECONDS]
        if party < 2:
            return                                           # (a one-off misread of the party list must not lose the lines)
        if gain > 0:
            self._debug(f"party={party} bar +{gain} owed={self._owed + gain} pending={[(k, v) for k, v, _ in self._pending]}")
            if self._owed == 0:
                self._owed_since = now
            self._owed += gain
        if self._owed <= 0:
            return
        # The lines come first-in first-out: the oldest ones are what the bar's gain was made of. The gain is
        # split when the oldest lines add up to it exactly (a gain may hold several kills).
        # (a line or two left over from a kill whose gain was never seen may sit in front: a few starts are tried)
        for start in range(min(4, len(self._pending))):
            total = own = bonus = other = 0
            for n, (kind, value, _) in enumerate(self._pending[start:], start=1):
                total += value
                if kind == "own":
                    own += value
                elif kind == "other":
                    other += value
                else:
                    bonus += value
                if total == self._owed:
                    bucket.own += own
                    bucket.bonus += bonus
                    bucket.other += other
                    self._debug(f"matched own={own} bonus={bonus} other={other} skipped={start}")
                    if self.on_split:
                        self.on_split(own, bonus)
                    del self._pending[:start + n]
                    self._owed = 0
                    return
                if total > self._owed:
                    break
        if now - self._owed_since > OWED_SECONDS:
            self._debug(f"gave up on {self._owed} EXP, pending={[(k, v) for k, v, _ in self._pending]}")
            self._owed = 0                                   # never explained: the gain stays unsplit

    def _debug(self, text):
        if self.on_debug:
            try:
                self.on_debug(text)
            except Exception:
                self.on_debug = None

    def _events(self, running, party, map_name, level, d):
        prev_map, prev_party, prev_running = self._state
        self._state = (map_name, party, running)
        if prev_running is None:
            return                                   # the first look only sets the baseline
        if running != prev_running:
            self._write("start" if running else "pause")
        if running and map_name != prev_map and map_name:
            self._write("map", note=f"from {prev_map or '?'}")
        if running and party != prev_party:
            self._write("party", note=f"from {prev_party} to {party}")
        if running and d[2] > 0:
            self._write("level_up", note=f"+{d[2]}")
        if running and d[3] > 0:
            self._write("death", note=f"+{d[3]}")

    def reset(self):
        """Reset Session: the counters start from zero. The trackers' totals are kept as the new
        baseline, so what was gained before this moment can't leak back in."""
        self._write("reset_session", note=f"was {self.exp} EXP in {int(self.seconds)} s")
        self.solo, self.party = Bucket(), {}
        self.levels = self.deaths = 0
        self.started = None
        self._next_summary = SUMMARY_EVERY
        self._last = None
        self._owed, self._pending, self._prev_lines = 0, [], []

    # ---- the CSV
    def _write(self, event, note=""):
        if not self.csv:
            return
        self.csv.write({
            "time": self.wall().isoformat(timespec="seconds"), "event": event, "map": self.map or "",
            "level": self.level or "", "party": self.now_party, "session_seconds": int(self.seconds),
            "exp": self.exp, "mesos": self.mesos, "solo_exp": self.solo.exp,
            "party_exp": self.party_all().exp, "levels": self.levels, "deaths": self.deaths, "note": note})
