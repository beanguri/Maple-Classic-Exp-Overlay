"""EXP per hour, the rate of the last 5 minutes and the time to the next level, from the EXP readings."""
import time
from collections import deque

from maple_overlay.config import ROLLING_WINDOW
from maple_overlay.core.levels import EXP_TABLE, level_for_req, levels_for


# --------------------------------------------------------------------------- tracking
class ExpTracker:
    """Turns noisy readings into trustworthy EXP gains.

    Every level needs a fixed amount of EXP (req), and the bar shows exp = pct% * req.
    req comes from the EXP table as soon as two clean readings point at the same level
    (otherwise it's learned from several *different* readings that agree with each other),
    then a reading only counts if its EXP and % agree with req. Misread digits break that,
    so they are ignored instead of being counted as gains.
    """

    WRAP_WAIT_READS = 40                      # %-only readings (about a minute) to wait for the level after a wrap

    def __init__(self):
        self.reset()

    def reset(self):
        self.running = False
        self._accum = 0.0
        self._resumed = None
        self._wrap_reads = 0                  # readings in a row spent waiting for a level after the % wrapped
        self.exp = None                       # last trusted EXP
        self.pct = None
        self.last_read = None                 # latest reading, trusted or not (for display)
        self.req = None                       # EXP required for the current level
        self._req_exact = False               # req came straight from the EXP table
        self._table_level = None              # level the last clean reading pointed at
        self._seen = {}                       # distinct (exp, pct) readings this level -> weight
        self._pending = None                  # big change waiting for confirmation (e, p, reads)
        self._recent = deque(maxlen=40)       # (when, EXP) of the latest trusted readings
        self._reads = 0                       # readings seen, as a clock for _recent
        self._misfits = 0                     # readings in a row that didn't fit req
        self._pending_lvl = None              # level-up waiting for confirmation
        self._level_start = None              # (exp, pct) right after a level-up, unverified
        self._prev_req = None                 # previous level's req, bounds the new one
        self._before_death = None             # (reads, EXP) just before the last death
        self.deaths = 0
        self.last_loss = None                 # % of the level's EXP the last death cost
        self.gained = 0
        self.levels = 0
        self.samples = deque()                # (elapsed, gained)
        self._format = None                   # what the bar showed last: "exact" (number and %) or "percent"
        self.estimated = False                # some EXP here came from a % alone, so it is an estimate
        self.level_source = None              # where a %-only level came from: "badge", "typed"

    def start(self):
        if not self.running:
            self.running = True
            self._resumed = time.monotonic()

    def pause(self, at=None):
        """Stops the clock (as of `at`, e.g. the last EXP gain, so idle time doesn't count)."""
        if self.running:
            end = time.monotonic() if at is None else min(time.monotonic(), at)
            self._accum += max(0.0, end - self._resumed)
            self.running = False

    def new_session(self):
        """Numbers from zero again, keeping what's known about the level and current EXP."""
        self.running = False
        self._accum, self._resumed = 0.0, None
        self.gained, self.levels, self.deaths = 0, 0, 0
        self.samples = deque()
        self.estimated = False

    def elapsed(self):
        if self.running:
            return self._accum + time.monotonic() - self._resumed
        return self._accum

    # ---- level requirement
    @staticmethod
    def _rel_err(pct):
        # % is shown with 2 decimals, so exp / pct is only this precise
        return 0.002 + 0.01 / pct

    def _learn_req(self, candidates, confident):
        weight = 2 if confident else 1        # font-matched readings are rarely wrong
        for e, p in candidates:
            if p >= 0.2 and e > 0:                # (a misread 0 says nothing about req)
                self._seen[(e, p)] = max(self._seen.get((e, p), 0), weight)
        while len(self._seen) > 60:
            del self._seen[next(iter(self._seen))]
        ests = [(e / (p / 100), self._rel_err(p), w) for (e, p), w in self._seen.items()]
        if self._prev_req:                    # the next level needs about as much or more
            ests = [x for x in ests if 0.98 <= x[0] / self._prev_req <= 2.5]
        group_of = lambda c, ce: [x for x in ests if abs(x[0] - c) / c <= ce + x[1]]
        weight_of = lambda g: sum(x[2] for x in g)
        center = lambda g: sum(x[0] * x[2] / x[1] ** 2 for x in g) / sum(x[2] / x[1] ** 2 for x in g)
        groups = sorted((group_of(c, ce) for c, ce, _ in ests), key=weight_of, reverse=True)
        if not groups or weight_of(groups[0]) < 3:
            return
        best = groups[0]
        mid = center(best)
        if self.req is None:
            # Lock in only once one value clearly beats any other (misreads can agree too).
            rivals = [g for g in groups if not 0.99 <= center(g) / mid <= 1.01]
            if not rivals or weight_of(best) >= 2 * weight_of(rivals[0]):
                self.req = mid
            return
        if self._req_exact:
            return                            # straight from the EXP table: misreads can't move it
        current = group_of(self.req, 0.003)
        if 0.99 <= mid / self.req <= 1.01 or weight_of(best) < 1.5 * weight_of(current) + 1:
            if current:
                self.req = center(current)
            return
        # A different value now clearly has more support: the old one came from misreads.
        # Switch, and restart the baseline so the change isn't counted as a gain.
        self.req, self._req_exact = mid, False
        self.exp = self.pct = None
        self._pending = None

    def _req_from_table(self, reading):
        """Takes the level's EXP straight from the EXP table once two clean readings in a
        row point at the same single level, so there's no wait for EXP gains."""
        e, p = reading
        levels = levels_for(e, p) if p >= 0.5 else []
        level = levels[0] if len(levels) == 1 else None
        if level and self._prev_req and EXP_TABLE[level - 1] < 0.98 * self._prev_req:
            level = None                      # after a level-up, the next level needs more
        if level and level == self._table_level:
            self.req, self._req_exact = EXP_TABLE[level - 1], True
        self._table_level = level

    def _consistent(self, e, p):
        return abs(e / self.req * 100 - p) <= 0.02 + 0.003 * p

    def _add_gain(self, amount):
        if self.running:
            self.gained += amount
            now = self.elapsed()
            self.samples.append((now, self.gained))
            while self.samples and now - self.samples[0][0] > ROLLING_WINDOW:
                self.samples.popleft()

    # ---- main entry
    def observe(self, candidates, confident=False):
        """Feed the (exp, pct) guesses for one screenshot. Returns (accepted, note)."""
        self._switch_format("exact")
        return self._observe(candidates, confident)

    def _switch_format(self, kind):
        """A bar that went from showing its number to only a %, or back: the estimate and the
        real EXP differ by rounding, so the next reading starts a fresh baseline (not a gain)."""
        if self._format not in (None, kind):
            self.exp = self.pct = self._pending = self._before_death = None
            self._pending_lvl = self._level_start = None
            self._recent.clear()                  # (the other format's numbers are off by rounding)
            self._wrap_reads = 0
        self._format = kind

    def observe_percent(self, pct, level=None, source=None):
        """Feed the % of a bar that shows no number ("EXP. [58.47%]"), with the level when it was read
        (source: "badge" or "typed"). The EXP is an estimate: the level's EXP from the table times pct.
        Returns (accepted, note), like observe."""
        if pct is None or not 0 <= pct < 100:
            return False, "unreadable"
        if level is not None and not 1 <= level <= len(EXP_TABLE):
            return False, f"level {level} isn't supported yet (no EXP table for it)"
        cur = level_for_req(self.req) if self.req else None
        if cur is None:
            if level is None:
                return False, "waiting to read your level"
            self._use_level(level, source)
            cur = level
        if level is not None and level == cur and source:
            self.level_source = source           # (a level read from the badge beats a carried or typed one)
        # A % that wrapped around (the level went up, or a misread): only the next level, read, says which.
        wrapped = self.exp is not None and self.pct is not None and self._format == "percent" and self.pct - pct > 30
        if level is not None and level != cur:
            if wrapped and level == cur + 1:
                last = self._pending_lvl
                n = last[2] + 1 if last and last[0] == level else 1
                self._pending_lvl = (level, pct, n)
                if n < 2:
                    return False, "level up? waiting to confirm"
                self.estimated = True
                self._enter_level(round(EXP_TABLE[level - 1] * pct / 100), pct, EXP_TABLE[level - 1])
                self.level_source = source
                return True, "LEVEL UP!"
            if self.gained:
                return False, "level changed: Reset to start again on the new level"
            self._use_level(level, source)      # nothing counted yet: the newer reading wins
            cur = level
        elif wrapped:
            if level is not None:                 # (a look where the badge wasn't read doesn't cancel a level-up seen before it)
                self._pending_lvl = None
            self._wrap_reads += 1
            if self._wrap_reads < self.WRAP_WAIT_READS:
                return False, "percentage wrapped: waiting to read the new level"
            self.forget_level()                   # nothing explained it for a minute: the level is unknown again, and the
            self._wrap_reads = 0                  # next one read (even a new one) is taken as it is, with a fresh start
            return False, "waiting to read your level"
        self._wrap_reads = 0
        self._pending_lvl = None
        self._switch_format("percent")
        self.estimated = True
        return self._observe([(round(EXP_TABLE[cur - 1] * pct / 100), pct)], True)

    def _use_level(self, level, source):
        """Starts estimating from this level (a fresh baseline: nothing is counted for the change)."""
        self.req, self._req_exact, self.level_source = EXP_TABLE[level - 1], True, source
        self.exp = self.pct = self._pending = self._pending_lvl = self._level_start = None

    @property
    def percent_mode(self):
        """The bar shows only a % right now (so the EXP coming in is an estimate)."""
        return self._format == "percent"

    def set_level(self, level, source):
        """The level, given from outside the bar (typed in). False if there is no EXP table for it."""
        if not 1 <= level <= len(EXP_TABLE):
            return False
        self._use_level(level, source)
        return True

    def forget_level(self):
        """Another character, or a level that can't be trusted: nothing to estimate from until read again."""
        self.req, self._req_exact, self._table_level, self.level_source = None, False, None, None
        self.exp = self.pct = self._pending = self._pending_lvl = self._level_start = None
        self._seen.clear()

    def _observe(self, candidates, confident=False):
        candidates = [(e, p) for e, p in candidates if p is not None and 0 <= p < 100]
        if not candidates:
            return False, "unreadable"
        self._reads += 1
        self.last_read = candidates[0]

        if self._check_level_up(candidates):
            return True, "LEVEL UP!"
        if self._pending_lvl:
            # Looks like a level-up, not confirmed yet: don't also take it as EXP lost.
            return False, "level up? waiting to confirm"

        if self.req is None and confident:
            self._req_from_table(candidates[0])
        self._learn_req(candidates, confident)
        if self.req is None:
            return False, "calibrating: learns your level's EXP after your next EXP gain"

        good = [c for c in candidates if self._consistent(*c)]
        if not good:
            self._misfits += 1
            if self._req_exact and self._misfits >= 30:
                # Nothing has fitted this level for a long while: the level itself may be
                # wrong. Work it out again.
                self.req, self._req_exact, self._table_level, self._misfits = None, False, None, 0
                self.exp = self.pct = self._pending = None      # and start counting afresh
            return False, "misread, ignored"
        self._misfits = 0
        e, p = good[0]
        self.last_read = good[0]

        if self._level_start:
            # First trusted reading after a level-up: fix the start value if it was misread.
            e0, p0 = self._level_start
            self._level_start = None
            if not self._consistent(e0, p0):
                fixed = round(p0 / 100 * self.req)
                self._add_gain(fixed - e0)
                self.exp = fixed

        if self.exp is None:
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"

        delta = e - self.exp
        if (0.01 * self.req <= -delta or (e == 0 and delta < 0)) and -delta <= 0.15 * self.req                 and not self._was_near(e):
            # EXP went down a few % of the level (or to 0): dying (it costs up to about 10%). Once a
            # second reading agrees, carry on from the new number without counting the loss,
            # so a death doesn't drag EXP/hr down (or below zero).
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.005 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "EXP went down, waiting to confirm"
            self._pending = None
            self._before_death = (self._reads, self.exp)
            self.last_loss = -delta / self.req * 100
            self.deaths += 1
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "died"
        if self._before_death and self._reads - self._before_death[0] <= 40 and delta > 0 \
                and e >= self._before_death[1] - 0.005 * self.req:
            # Right back to where it was before "dying", in one go: those low readings were a
            # misread, not a death (EXP lost on dying comes back only slowly, from monsters).
            self._before_death, self.deaths = None, max(0, self.deaths - 1)
            self._pending = None
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"
        if abs(delta) > 0.15 * self.req:
            # Unusually big change in one tick: only believe it if the next read agrees.
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.15 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "big jump, waiting to confirm"
            if delta < 0 and not self._undoes_jump(e):
                # EXP never drops this much (dying costs at most 10%; level-ups are checked
                # separately), so this is a misread, even twice in a row. If it keeps
                # showing up, carry on from it, without counting the drop.
                if n < 8:
                    return False, "misread, ignored"
                self._pending, self.exp, self.pct = None, e, p
                self._recent.append((self._reads, e))
                return True, "baseline"
        self._pending = None
        self._add_gain(delta)
        self.exp, self.pct = e, p
        self._recent.append((self._reads, e))
        return True, ""

    def _was_near(self, e):
        """Was EXP about e a moment ago? Then going down to it is the right number coming
        back after a misread that was a bit too high (and counts, to cancel that out)."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.005 * self.req for r, x in self._recent)

    def _undoes_jump(self, e):
        """Was EXP around e a moment ago? Then a big drop to it is the right number coming
        back after a misread that was too high, and must count, to cancel that out."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.15 * self.req for r, x in self._recent)

    def _check_level_up(self, candidates):
        """% dropped a lot and EXP reset low: a level-up, once readings in a row agree on it.

        If the level is known, readings that fit the next level's EXP from the table count,
        and two in a row are enough. Otherwise a reading that still fits this level's EXP is
        never a level-up (e.g. the right number coming back after a misread that was too
        high), and as the next level's EXP is only known to be bigger, it takes five.
        """
        if self.exp is None or self.pct is None or self.req is None:
            return False
        level = level_for_req(self.req)
        nxt = EXP_TABLE[level] if level and level < len(EXP_TABLE) else None
        hits = []
        for e, p in candidates:
            if not (self.pct - p > 30 and e < self.exp):
                continue
            if nxt and abs(e / nxt * 100 - p) <= 0.02 + 0.003 * p:
                hits.append((e, p, 2))        # fits the next level exactly
            elif self._consistent(e, p):
                continue                      # still this level: not a level-up
            elif p >= 0.2 and 0.98 * self.req <= e / (p / 100) <= 2.5 * self.req:
                hits.append((e, p, 5))        # next level's EXP is similar or bigger
        if not hits:
            self._pending_lvl = None
            return False
        e, p, need = hits[0]
        last = self._pending_lvl
        # Each reading must carry on from the one before (EXP only goes up from there).
        n = last[2] + 1 if last and 0 <= e - last[0] <= 0.15 * (nxt or self.req) else 1
        self._pending_lvl = (e, p, n)
        if n < need:
            return False
        self._enter_level(e, p, nxt if need == 2 else None)     # (2: the new level's EXP is known, no relearning)
        return True

    def _enter_level(self, e, p, new_req=None):
        """A confirmed level-up: the rest of the old level and e count as gained. new_req is the new
        level's EXP when the table gives it (otherwise it is learned again)."""
        self._pending_lvl = self._pending = None
        if self.running:
            self.levels += 1
        self._add_gain(self.req - self.exp + e)
        self.exp, self.pct = e, p
        self._prev_req = self.req
        self.req, self._req_exact = new_req, new_req is not None
        self._table_level = None
        self._seen.clear()
        self._recent.clear()                   # the old level's numbers mean nothing now
        self._recent.append((self._reads, e))
        self._level_start = (e, p)

    def rate(self):
        t = self.elapsed()
        return self.gained / t * 3600 if t >= 10 else None

    def rolling_rate(self):
        if len(self.samples) < 2:
            return None
        (t0, g0), (t1, g1) = self.samples[0], self.samples[-1]
        return (g1 - g0) / (t1 - t0) * 3600 if t1 - t0 >= 30 else None
