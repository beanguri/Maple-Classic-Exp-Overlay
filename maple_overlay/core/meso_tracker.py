"""Mesos per hour from the inventory readings and the game's messages."""
from collections import deque

from maple_overlay.config import MESO_GAP, ROLLING_WINDOW


def _agrees(value, last):
    """Two mesos readings that could both be right: same number of digits and close together."""
    return last is not None and len(str(value)) == len(str(last)) and abs(value - last) <= max(1000, 0.02 * last)

class MesoTracker:
    """Turns mesos readings into mesos gained per hour and per 5 minutes.

    The first count is the baseline; every later one adds what changed since. The
    inventory doesn't have to stay open: after a long wait (MESO_GAP) a big rise is
    believed, and the rates cover the run up to the last look, so they hold still
    while the inventory is closed.

    A new count only counts once two readings in a row agree on it (three for a big jump),
    so a one-off misread never shows up as mesos gained. A change by more than half,
    or a digit-count change bigger than 10%, starts counting afresh from the new number
    instead: that's a shop visit or a corrected misread, not mesos picked up. Small digit
    rollovers count after three identical readings.
    """

    def __init__(self):
        self.resets = 0                       # times reset(): looks from before one don't compare with looks after
        self.reset()

    def reset(self):
        self.resets += 1
        self.value = None                     # confirmed mesos count
        self.seen_at = None                   # when it was last confirmed
        self._candidate = (None, 0, 0)
        self.gained = 0
        self.samples = deque()                # (elapsed, gained)
        self.start = None                     # elapsed time when counting began
        self.read_elapsed = None              # elapsed time of the last confirmed count
        self.counting = False
        self.window = None                    # "shop", "storage" or "trade" while one is open
        self.window_reads = 0                 # confirmed counts seen in it so far
        self.drop_next = False                # a window was never read: the next change isn't earned
        self.after_window = False             # the next count follows a window that just closed
        self.late_change = False              # ...and a new count was still being confirmed as it closed
        self.idle_pause = False               # paused only because EXP stopped (same map): nothing is left out
        self.ignored = None                   # (amount, window, when) of the last change left out
        self.stale = False                    # paused since the last count: what changed meanwhile is unknown
        self.stale_since = None               # when the status line first had to ask about it
        self.bank_window = False              # the first window opened after a trip banks what came before it
        self.bank_inventory = False           # ...and so does the inventory, right after pausing on the map
        self.left_out = [0, 0]                # mesos gained and lost at windows (kept apart, for Details)
        self.window_at = None                 # when a shop, storage or trade window was last seen open
        self.misreads = []                    # (from, to): when a count later proven wrong was shown
        self._shown = None                    # [count, first seen, last seen] of the count shown now
        self._before = None                   # ...and of the one before it
        self._window_since = False            # a window was seen since the count shown now came in

    def pause(self, stale=True, bank_inventory=False):
        """Tracking paused. Unless it's only an idle pause on the same map, what changes now
        (a sale in town, a trade...) can't be told from earnings: the count goes stale.

        What was earned before the pause hasn't been counted yet if the inventory was closed.
        It is banked by the first look at the inventory right after pausing on the grinding
        map (bank_inventory), or by the first shop, storage or trade window that is read."""
        self.counting = False                 # don't count what changes while paused
        self.idle_pause = not stale
        self.stale = stale and self.value is not None
        self.bank_window = self.stale
        self.bank_inventory = self.stale and bank_inventory

    def stale_note(self, now, running):
        """Asks for the inventory: right after pausing (to keep what was earned), or once
        tracking has carried on with the count still stale."""
        if not running and self.bank_inventory and (self.seen_at is None or now - self.seen_at > 8):
            return "Open the inventory now to save your mesos."
        if not (self.stale and running):
            self.stale_since = None
            return None
        if self.stale_since is None:
            self.stale_since = now
        if now - self.stale_since >= 5:       # (not at once: the inventory may be open already)
            return "Open the inventory to set the mesos start."
        return None

    def set_window(self, window, now):
        """Which money window is open (or None). Mesos change there without being earned:
        the first count in a window is still earned (it shows what came before), every
        later change is left out. A window closed without a single count leaves the next
        change unexplained, so that is left out too."""
        if window:
            self.window_at = now
            self._window_since = True
        if window == self.window:
            return
        if self.window and not window:        # (a sale or purchase seen, not yet confirmed: not earned either)
            self.late_change = self._candidate[0] is not None and self._candidate[0] != self.value
        if window and not self.window:
            self.window_reads = 0
        elif self.window and not window and self.window_reads == 0:
            self.drop_next = True
            self.after_window = True
        elif self.window and not window:
            self.after_window = True
            if not self.counting:
                self.stale = False            # read exactly while paused: the count is still good
        self.window = window

    def ignored_note(self, now):
        """Why the last change was left out, for the status line, or None after a while."""
        if not self.ignored or now - self.ignored[2] > 30:
            return None
        amount, window, _ = self.ignored
        where = {"shop": "the NPC shop", "storage": "storage", "trade": "a trade"}.get(window, "a shop or trade")
        return f"Left out {amount:+,} mesos ({where}, not earned)."

    def observe(self, value, now, elapsed, running):
        jump = False
        if value == self.value:
            self._candidate = (None, 0, 0)        # unchanged: nothing new to confirm
        else:
            # How many readings in a row back the new number: close to it, and exactly it.
            last, n, same = self._candidate
            n = n + 1 if _agrees(value, last) else 1
            same = same + 1 if value == last else 1
            self._candidate = (value, n, same)
            old = self.value
            # Measured against the bigger of the two, so a misread and its correction are
            # always judged the same way (both counted, or both not), and cancel out.
            top = max(value, old) if old is not None else 0
            digits_changed = old is not None and len(str(value)) != len(str(old))
            jump = old is not None and abs(value - old) > (0.1 if digits_changed else 0.5) * top
            # Not looked at for a while, so a big rise is more likely mesos earned than a shop
            # visit (selling is seen as it happens, and a drop is still treated as a shop).
            big_jump = jump
            if jump and value > old and self.seen_at is not None and now - self.seen_at > MESO_GAP:
                jump = False
            big = old is not None and abs(value - old) > max(10000, 0.05 * top)
            # The first count, a digit change and a jump need the very same number 3 times.
            # First counts and jumps aren't gained, so a misread there would never cancel
            # out; digit changes can also be different, nearby misreads of an extra digit.
            # About 10x (or a tenth): almost always a digit read too many (or lost).
            tenfold = old is not None and old > 0 and (9 <= value / old <= 11 or 0.09 <= value / old <= 0.11)
            if old is None or big_jump or digits_changed:
                if same < (8 if tenfold else 3):
                    return
            elif n < (3 if big else 2):
                return
            self._candidate = (None, 0, 0)
        if (not running and self.idle_pause and not self.window
                and self.value is not None and self.start is not None):
            if value == self.value:
                self.seen_at = now                # (still a look, for a run waiting for one)
            return                                # an idle pause: counted once tracking carries on
        self.seen_at = now
        self.read_elapsed = elapsed
        resumed = running and not self.counting
        first = self.value is None
        if resumed:
            self.counting = True                  # count changes from this reading on
            self.start = elapsed if self.start is None else self.start
            if not self.samples:                  # the rates always have a starting point
                self.samples.append((elapsed, self.gained))
        moved = value != self.value and not jump
        if jump and self.window and self.value is not None:
            self._leave_out(value - self.value, now)          # a big sale or purchase: a new start, and said so
        # Mesos never go down by grinding. A drop at a window's first look, or at the first look
        # after one, is a purchase that wasn't read in time, not negative earnings.
        unread_spend = (moved and self.value is not None
                        and ((self.window and self.window_reads == 0 or self.after_window) and value < self.value
                             or self.after_window and self.late_change))
        banking = (not running and self.stale and self.value is not None
                   and ((self.window and self.window_reads == 0 and self.bank_window)
                        or (not self.window and self.bank_inventory)))
        if banking:                               # what was earned before the trip, seen at last
            self.bank_window = self.bank_inventory = False
            if moved and unread_spend:
                self._leave_out(value - self.value, now)
            elif moved:
                self._earn(value - self.value, elapsed)
        elif moved and self.window and self.window_reads:
            self._leave_out(value - self.value, now)                        # exact change at a window
        elif moved and not running and not self.idle_pause and self.value is not None and self.start is not None:
            self._leave_out(value - self.value, now)                        # anything while paused
        elif resumed and (self.stale or first):
            self.stale = False                    # a first or stale count is only a new baseline
            self.samples.append((elapsed, self.gained))
        elif running and moved:
            if self.drop_next or unread_spend:
                self._leave_out(value - self.value, now)
            else:
                self._earn(value - self.value, elapsed)
        self.drop_next = False
        if not self.window:
            self.after_window = self.late_change = False
        if self.window:
            self.window_reads += 1
        self._snap_back(value, now)
        self.value = value

    def add_text(self, amount, elapsed, now=None, running=True):
        """Mesos picked up, from the game's "You have gained mesos (+N)" line. Counted only while tracking, after the
        inventory has set the start, and not while a shop, storage or trade window is open. The next look at the
        inventory corrects any line that wasn't seen: what it finds beyond this count is added then. True if counted,
        otherwise why not (a text)."""
        if self.value is None:
            # No inventory look yet: count the lines from now anyway. The first look only sets the start (what was
            # picked up before it isn't counted twice), and every later one corrects what the lines missed.
            if not running or self.window or amount <= 0:
                return "not tracking" if not running else "a window is open" if self.window else "zero"
            if self.start is None:
                self.start = elapsed
                self.samples.append((elapsed, self.gained))
            self._earn(amount, elapsed)
            self.read_elapsed = elapsed
            return True
        why = ("not tracking" if not self.counting else "a shop/storage/trade window is open" if self.window else
               "waiting for the inventory (stale)" if self.stale else None)
        if amount <= 0 or why:
            return why or "zero"
        self.value += amount
        self._earn(amount, elapsed)
        self.read_elapsed = elapsed             # (the rates follow the lines, not only the inventory looks)
        if now is not None:
            self.seen_at = now                  # (the log can trust these mesos like a look at the inventory)
        return True

    def _snap_back(self, value, now):
        """A count that goes back to the one before it (A, B, A) with no window seen meanwhile
        is a snap back: grinding can't do that, so B was a misread. When B was shown is kept
        (misreads), so nothing that used it goes to the website with mesos."""
        if self._shown and value == self._shown[0]:
            self._shown[2] = now
            return
        if self._shown and self._before and value == self._before[0] and not self._window_since:
            self.misreads = self.misreads[-49:] + [(self._shown[1], self._shown[2])]
        self._before, self._shown = self._shown, [value, now, now]
        self._window_since = bool(self.window)

    def _earn(self, amount, elapsed):
        self.gained += amount
        self.samples.append((elapsed, self.gained))
        while len(self.samples) > 2 and self.samples[1][0] <= elapsed - ROLLING_WINDOW:
            self.samples.popleft()

    def _leave_out(self, amount, now):
        self.ignored = (amount, self.window, now)
        self.left_out[amount < 0] += abs(amount)

    def look(self):
        """The last inventory look as the log is given it: (mesos gained so far, when read),
        however old (the log decides what is too old), or None before the first count."""
        return None if self.value is None else (self.gained, self.seen_at)

    def for_log(self):
        """What AutoLog.tick and AutoLog.settle are given about mesos, as keyword arguments."""
        return dict(mesos=self.look(), window_at=self.window_at, misreads=self.misreads, meso_epoch=self.resets)

    def per_hour(self, elapsed):
        # Up to the last look, not up to now: the number holds still while nobody looks.
        end = self.read_elapsed if self.read_elapsed is not None else elapsed
        if self.start is None or end - self.start < 30:
            return None
        return self.gained / (end - self.start) * 3600

    def per_5min(self, elapsed):
        if self.start is None:
            return None
        end = self.read_elapsed if self.read_elapsed is not None else elapsed
        t0 = max(self.start, end - ROLLING_WINDOW)
        if end - t0 < 60:
            return None
        return (self.gained - self._gained_at(t0)) / (end - t0) * ROLLING_WINDOW

    def _gained_at(self, t0):
        """Mesos gained by run time t0, assuming a steady rate between two looks."""
        before = self.samples[0] if self.samples else (self.start, 0)
        for t, g in self.samples:
            if t > t0:
                if t == before[0]:                # (t0 is before the first look we have)
                    return g
                return before[1] + (g - before[1]) * (t0 - before[0]) / (t - before[0])
            before = (t, g)
        return before[1]
