"""Shadow mode: reader v2 (find the EXP bar by shape, read it by drawing the game's font) runs beside today's
reader and only notes whether the two agree. It never changes a Reading, the tracker, the log or what's shared.

The reader hands in the picture it just read, its own answer and how long it took; this keeps the counts,
the last few disagreements (with their pictures, in memory only) and guards the CPU: if v2 costs more than
all of today's reader it runs every other look, more than 8 times that and it is off for the session. Where today's
reader finds no bar (a menu, the login screen) v2's whole-strip search costs many times more, so there it searches
only every NOBAR_SECONDS and the guard leaves those looks out (it compares looks where both had the bar).

Every part of the overlay that has a reader v2 beside today's reader gets its own shadow (PARTS): `Shadow` for the
EXP bar (v2 finds and reads it on a picture), `PartShadow` for the others (mesos, party size: the part hands in a
function that runs v2). Each has its own counts, CPU guard, error pause and no-bar interval; the config switch
`shadow_reader` turns them all off together. A v2 reader says what it saw as `(answer, why)`, `why` one of WHY.
"""
import time
from collections import Counter, deque, namedtuple

CATEGORIES = ("agree", "disagree", "v2_only", "old_only", "neither", "skipped", "errors")
PARTS = ("exp", "mesos", "party")       # the parts that can have a shadow
WHY = ("ok", "not_found", "unclear")    # why v2 answered what it did: read it / found no bar or box / found one, can't be sure
WHY_COUNTED = ("not_found", "unclear")  # an old_only look is counted by these (the health report's shadow_why)
GUARD_LOOKS = 100           # the CPU guard compares the last this many looks...
GUARD_MIN = 20              # ...once it has at least this many
# The CPU guard (measured in T16: the EXP shadow costs 0.1-0.2 of today's EXP read and runs every look; the mesos shadow, when it
# can't read the box cheaply, 1-6 times today's mesos read, which is still well inside 2x the whole overlay):
THIN_RATIO, OFF_RATIO = 1.0, 8.0     # v2 costs more than today's reader: every other look; more than 8 times: off for the session
ERROR_PAUSE = 60            # seconds v2 is skipped after it raised
STRIP_SECONDS = 10          # v2 may grab the bottom strip itself this often at most
NOBAR_SECONDS = 10          # ...and search for a bar today's reader doesn't see this often at most
NOBAR_BY_PART = {"exp": 15, "mesos": NOBAR_SECONDS, "party": NOBAR_SECONDS}      # (each part can differ)
KEEP = 3                    # disagreements kept (pictures of the EXP bar only, in memory)

# category: what happened this look (one of CATEGORIES); ms: v2's time; spot: the EXP bar's box in the picture
# handed in, (x0, y0, x1, y1), when v2 read it with a number it trusts (room left for the "EXP." label, which the
# digit check needs, and for the bracket on the right); answer: v2's answer or None; why: one of WHY (None when
# v2 didn't answer at all: skipped, errors)
Result = namedtuple("Result", "category ms spot answer why", defaults=(None,))


def compare(old, new):
    """agree / disagree / v2_only / old_only / neither for two answers ((exp, pct) or (None, pct) or None).
    A percentage-only answer on either side is compared by its percentage alone."""
    if old is None and new is None:
        return "neither"
    if old is None:
        return "v2_only"
    if new is None:
        return "old_only"
    if old[0] is None or new[0] is None:
        return "agree" if old[1] == new[1] else "disagree"
    return "agree" if old == new else "disagree"


def compare_values(old, new):
    """compare() for a plain answer (an amount of mesos, a party size) or None."""
    if old is None and new is None:
        return "neither"
    if old is None:
        return "v2_only"
    if new is None:
        return "old_only"
    return "agree" if old == new else "disagree"


class _Guarded:
    """What every part's shadow shares: the kill switch, the counts, the CPU guard, the error pause and the
    no-bar interval."""

    def __init__(self, on_error, timer=time.perf_counter, nobar_seconds=NOBAR_SECONDS):
        self.on_error, self.timer, self.nobar_seconds = on_error, timer, nobar_seconds
        self.on = True                  # the kill switch (config shadow_reader)
        self.counts = Counter()         # CATEGORIES, plus "runs"; for EXP also "grabs" (strips v2 grabbed itself) and
                                        # "ticks" (agreements found by the recheck: EXP changed between the two grabs)
        self.why = Counter()            # old_only looks by v2's reason (WHY_COUNTED)
        self.times = deque(maxlen=GUARD_LOOKS)      # (today's reader ms, v2 ms) of the looks v2 ran
        self.stopped = False            # the CPU guard turned v2 off for the session
        self.thinned = False            # ...or to every other look
        self.pause_until = 0.0
        self.nobar_at = -1e9            # when v2 last searched while today's reader saw no bar
        self.flip = False
        self.last = None                # (old answer, v2 answer) of the latest look
        self.diffs = deque(maxlen=KEEP)     # the latest disagreements as (old, v2)

    # ---- the CPU guard
    def _ratio(self):
        if len(self.times) < GUARD_MIN:
            return 0.0
        old = sum(t[0] for t in self.times)
        return sum(t[1] for t in self.times) / max(old, 1e-6)

    def _allowed(self):
        """Should v2 run at this look? (A look it skips for the CPU is counted as skipped.)"""
        if self.stopped:
            return False
        ratio = self._ratio()
        if ratio > OFF_RATIO:
            self.stopped = True
            return False
        self.thinned = ratio > THIN_RATIO
        if self.thinned:
            self.flip = not self.flip
            return self.flip
        return True

    def idle(self):
        """Is v2 switched off or resting after an error? (Then nothing of it runs and nothing is counted.)"""
        return not self.on or time.monotonic() < self.pause_until

    def failed(self, exc):
        """Something went wrong around v2 (outside look): counted as an error look, v2 pauses, on_error hears of it."""
        self.counts["errors"] += 1
        self.pause_until = time.monotonic() + ERROR_PAUSE
        self.on_error(exc)
        return Result("errors", None, None, None)

    def _count(self, kind, why):
        """Counts one look that ran."""
        self.counts[kind] += 1
        if kind == "old_only" and why in WHY_COUNTED:
            self.why[why] += 1

    def _facts(self):
        times = list(self.times)            # (the reading thread adds to these while this runs on the main thread)
        n = len(times)
        return {"on": self.on, "stopped": self.stopped, "thinned": self.thinned,
                "counts": {k: self.counts[k] for k in CATEGORIES + ("grabs", "runs", "ticks")},
                "why": {k: self.why[k] for k in WHY_COUNTED},
                "v2_ms": round(sum(t[1] for t in times) / n, 1) if n else None,
                "old_ms": round(sum(t[0] for t in times) / n, 1) if n else None,
                "last": self.last, "diffs": list(self.diffs)}


class PartShadow(_Guarded):
    """The shadow of a part that isn't found on a picture here (mesos, party size): the part hands in `run()`, which
    runs v2 on the picture today's reader already has and returns (answer, why)."""

    def __init__(self, name, on_error, compare=compare_values, timer=time.perf_counter, nobar_seconds=None):
        super().__init__(on_error, timer, NOBAR_BY_PART.get(name, NOBAR_SECONDS) if nobar_seconds is None else nobar_seconds)
        self.name, self.compare = name, compare

    def look(self, old, old_ms, run, searching=False):
        """`old`: today's confident answer or None; `old_ms`: what today's reader spent on this look; `run()` ->
        (answer, why). `searching`: today's reader found nothing to read (no box, no list): v2 then runs at most
        every nobar_seconds, and the CPU guard leaves the look out. Returns a Result, or None when v2 did not run
        for a reason that isn't counted (switched off, resting after an error, waiting for the next search)."""
        if self.idle():
            return None
        if searching:
            if time.monotonic() - self.nobar_at < self.nobar_seconds:
                return None
            self.nobar_at = time.monotonic()
        if not self._allowed():
            self.counts["skipped"] += 1
            return Result("skipped", None, None, None)
        t0 = self.timer()
        try:
            got, why = run()
            if why not in WHY:
                raise ValueError(f"unknown reason {why!r}")
        except Exception as e:
            return self.failed(e)
        ms = (self.timer() - t0) * 1000
        if not searching:                   # (the guard compares like with like: looks where both had something)
            self.times.append((float(old_ms), ms))
        self.counts["runs"] += 1
        kind = self.compare(old, got)
        self._count(kind, why)
        self.last = (old, got)
        if kind == "disagree":
            self.diffs.append((old, got))
        return Result(kind, ms, None, got, why)

    def facts(self):
        """The counts, the latest answers and the disagreements, as a plain dict (see report.details)."""
        return {**self._facts(), "name": self.name}


class Shadow(_Guarded):
    """The EXP bar's shadow. find(img, hint) -> Layout or None; read(font, lay) -> answer or None; fits(exp, pct) ->
    bool; font() -> the font asset; view(img, lay) -> (picture, boxes); on_error(exc) is told about every exception."""

    def __init__(self, find, read, fits, font, view, on_error, timer=time.perf_counter, nobar_seconds=NOBAR_SECONDS):
        super().__init__(on_error, timer, nobar_seconds)
        self.find, self.read, self.fits, self.load_font, self.view = find, read, fits, font, view
        self.strip_at = -1e9
        self.seen = False               # did v2 find the bar at its latest run
        self.font = None
        self.kept = deque(maxlen=KEEP)      # {"kind", "old", "v2", "image", "boxes"} for disagree / old_only / v2_only

    # ---- one look
    def look(self, img, old, old_ms, old_lay=None, hint=None, more=None, old_img=None, own=None, searching=False,
             recheck=None, spots=()):
        """Runs v2 on `img` (the picture today's reader just read) and compares. `old`: today's confident
        answer or None. `hint`: a box (x0, y0, x1, y1) in img where v2 saw the bar last time. `more()`: a
        bigger picture as (img, dx, dy) (the bottom strip, dx/dy where it starts relative to img) or None.
        `spots`: boxes in img where the bar should be at each UI scale the game might use; tried after the hint.
        `old_img`: the picture today's reader read, if v2 was given another (for the crop kept in a report).
        `own()`: v2's own small picture (when `img` doesn't hold the remembered spot) or None; called only once v2
        really runs, so a look v2 skips (off, paused, stopped, thinned) grabs nothing, and its time counts as v2's.
        `searching`: today's reader found no bar in this look. Then v2 runs only if it has the bar at a spot it just
        read (cheap), or else at most every NOBAR_SECONDS (its whole-strip search is the slow part).
        `recheck(picture)`: today's reader on another picture (answer or None). When v2 read its own picture and the
        two disagree, today's reader reads v2's picture too: the same answer there means EXP changed between the two
        grabs (a kill in those few ms), so it is counted as agree (and as a tick), not a disagreement.
        Returns a Result, or None if v2 did not run for a reason that isn't counted (switched off, paused, waiting
        for its next search while there's no bar)."""
        if self.idle():
            return None
        quick = False
        if searching and not (self.seen and (hint is not None or own is not None)):
            if time.monotonic() - self.nobar_at < self.nobar_seconds:
                if not spots:
                    return None
                quick = True            # (between whole-picture searches, only the cheap boxes where the bar should be)
            else:
                self.nobar_at = time.monotonic()
        if not self._allowed():
            if quick:
                return None             # (a quick look is only a look when it finds the bar: one not taken isn't counted)
            self.counts["skipped"] += 1
            return Result("skipped", None, None, None)
        t0 = self.timer()
        try:
            if self.font is None:
                self.font = self.load_font()
            mine = own() if own is not None else None
            if mine is not None:
                img, hint = mine, None
            lay, dx, dy = self._find(img, hint, spots, quick), 0, 0
            if quick and lay is None:
                return None             # (nothing in the boxes: not a look, costs nothing but the boxes)
            if lay is None and more is not None and time.monotonic() - self.strip_at >= STRIP_SECONDS:
                self.strip_at = time.monotonic()
                big = more()
                if big is not None:
                    self.counts["grabs"] += 1
                    lay = self._find(big[0], None, [(a - big[1], b - big[2], c - big[1], d - big[2]) for a, b, c, d in spots])
                    dx, dy = big[1], big[2]
            got = self.read(self.font, lay) if lay is not None else None
            if got is None and lay is not None and (hint is not None or spots) and not quick and mine is None and not (dx or dy):
                # (a small box can judge contrast differently from the whole picture: read what a full search finds too)
                whole = self.find(img, None)
                if whole is not None:
                    lay, got = whole, self.read(self.font, whole)
            if got is not None and got[0] is not None and not self.fits(*got):
                got = None
            if got is not None and got[0] is None and not 0 <= got[1] <= 100:
                got = None
        except Exception as e:
            self.counts["errors"] += 1
            self.pause_until = time.monotonic() + ERROR_PAUSE
            self.on_error(e)
            return Result("errors", None, None, None)
        ms = (self.timer() - t0) * 1000
        if not searching:                   # (the guard compares like with like: looks where both had the bar)
            self.times.append((float(old_ms), ms))
        self.counts["runs"] += 1
        self.seen = lay is not None
        kind = compare(old, got)
        if kind == "disagree" and mine is not None and recheck is not None:
            try:
                if compare(recheck(mine), got) == "agree":
                    kind = "agree"
                    self.counts["ticks"] += 1
            except Exception as e:
                self.on_error(e)
        why = "ok" if got is not None else "not_found" if lay is None else "unclear"
        self._count(kind, why)
        self.last = (old, got)
        if kind == "disagree":
            self.diffs.append((old, got))
        if kind in ("disagree", "old_only", "v2_only"):
            self._keep(kind, old, got, old_img if old_img is not None else img, old_lay, lay, dx, dy, img)
        spot = None
        if got is not None and lay is not None:
            g = lay.gh
            spot = (int(lay.box[0] - 6 * g) + dx, int(lay.cy - 2 * g) + dy, int(lay.box[2] + 3 * g) + dx, int(lay.cy + 2 * g) + dy)
        return Result(kind, ms, spot, got, why)

    def _find(self, img, hint, spots, quick=False):
        if quick:
            return self.find(img, hint, spots, False)
        return self.find(img, hint, spots) if spots else self.find(img, hint)

    def _keep(self, kind, old, new, img, old_lay, lay, dx, dy, v2_img):
        """The EXP bar's crop (no class or name row), with both answers, for a bug report. `img` is today's picture,
        `v2_img` the one v2 read (its own small grab, or the same)."""
        try:
            if old_lay is not None:
                pic, boxes = self.view(img, old_lay)
            elif lay is not None and not (dx or dy):
                pic, boxes = self.view(v2_img, lay)
            else:
                return
            self.kept.append({"kind": kind, "old": old, "v2": new, "image": pic, "boxes": boxes})
        except Exception as e:
            self.on_error(e)

    # ---- for the bug report
    def facts(self):
        """The counts, the latest answers and the disagreements, as a plain dict (see report.details)."""
        return {**self._facts(), "name": "exp"}
