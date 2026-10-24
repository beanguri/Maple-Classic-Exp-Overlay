"""One tracking session: the EXP and mesos trackers together."""
from maple_overlay.core.exp_tracker import ExpTracker
from maple_overlay.core.meso_tracker import MesoTracker
from maple_overlay.vision import potions


class Session:
    """What's on screen: EXP and mesos since Start, and when tracking pauses by itself.

    After Start it tracks until Pause is pressed, except that it pauses by itself when no
    EXP comes in for `gap` seconds or you leave the map, and carries on at the next EXP
    gain: from zero if that's on a different map, so the numbers are always for the map
    you're on. Time spent paused never counts, and an idle pause rewinds the clock to the
    last EXP gain. The gain that ends a pause starts the clock again rather than counting,
    just like a log stretch's first gain.
    """

    def __init__(self, gap):
        self.exp, self.meso = ExpTracker(), MesoTracker()
        self.potions = potions.PotionTracker()
        self.gap = gap
        self.on = False             # Start was pressed (and Pause wasn't)
        self.waiting = None         # paused by itself: "idle" or "map"
        self.map = None             # the map these numbers are for
        self.here = None            # the map you're on now
        self.last_gain = None

    def start(self, now):
        self.on, self.waiting, self.last_gain = True, None, now
        self.map = self.map or self.here
        self.exp.start()

    def pause(self):
        self.on, self.waiting = False, None
        self.exp.pause()
        # (the inventory can bank what was earned only while still on the grinding map)
        self.meso.pause(bank_inventory=self.map is None or self.here is None or self.here == self.map)
        self.potions.pause()

    def reset(self):
        self.exp.reset()
        self.meso.reset()
        self.potions.reset()
        self.on, self.waiting, self.map, self.last_gain = False, None, None, None

    def _wait(self, why):
        self.exp.pause(at=self.last_gain)
        self.meso.pause(stale=why != "idle")      # (leaving the map: a shop or a trade may follow)
        self.potions.pause()
        self.waiting = why

    def observe(self, candidates, confident, now):
        """Feeds an EXP reading to the tracker. Returns the tracker's (accepted, note)."""
        return self._feed(lambda t: t.observe(candidates, confident), now)

    def observe_percent(self, pct, level, source, now):
        """Feeds the % of a bar that shows no EXP number (see ExpTracker.observe_percent)."""
        return self._feed(lambda t: t.observe_percent(pct, level, source), now)

    def _feed(self, observe, now):
        t = self.exp
        exp_before, gained_before = t.exp, t.gained
        result = observe(t)
        rose = exp_before is not None and t.exp is not None and (
            t.exp > exp_before or result[1] == "LEVEL UP!")
        if t.running and t.gained > gained_before:
            self.last_gain = now
        elif self.on and self.waiting and self.waiting != "blind" and rose:
            if self.map and self.here and self.here != self.map:
                t.new_session()             # a different map: its own numbers
                self.meso.reset()
                self.potions.reset()
                self.map = self.here
            self.waiting = None
            self.last_gain = now
            t.start()
        return result

    def look_at_bar(self, slots):
        """Feeds a look at the potion bar: what went down since the last look was used."""
        self.potions.observe(slots, self.exp.running)

    def map_blind(self):
        """The minimap's name has been out of sight for a while (hidden, covered, or the game
        closed it): you may have changed maps without it noticing, so it pauses instead of
        carrying on under the old name. It carries on once the name can be read again."""
        self.here = None
        if self.waiting == "blind" or not self.on:
            return
        if self.exp.running:
            self._wait("blind")
        elif self.waiting:
            self.waiting = "blind"

    def map_seen(self, name):
        """The map name was read (already matched to known names, see AutoLog.canonical)."""
        if self.waiting == "blind":
            self.waiting = "map"            # visible again: the usual rule decides (same map: carry on; another: new numbers)
        self.here = name
        if self.map and name != self.map and not self.on:
            self.meso.bank_inventory = False            # walked off after pausing: a read here isn't just earnings
        if not self.on:
            return
        if self.map is None:
            self.map = name
        elif name != self.map:              # left the map
            if self.exp.running:
                self._wait("map")
            elif self.waiting:
                if self.waiting == "idle":
                    self.meso.pause()       # (an idle pause kept the count; off the map it goes stale)
                self.waiting = "map"

    def tick(self, now):
        """Call regularly: pauses after `gap` seconds without EXP."""
        if self.on and self.exp.running and now - self.last_gain > self.gap:
            self._wait("idle")
