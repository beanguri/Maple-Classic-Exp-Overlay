"""What a bug report says about the overlay, so a problem can be worked out from the report alone.

The App collects the facts (a plain dict, see App._report_facts); this module keeps the
short history of what happened (Recent), keeps count of how the reading thread is doing
(Health), spots the usual causes (looks_wrong) and writes it all out (summary, details).
Nothing here reads the screen or the network. Nothing personal goes in: no character name,
and the Windows user name is taken out of anything that looks like a file path by the caller.
"""
import time
from collections import deque


def clock(seconds):
    """+m:ss for a time since the overlay started."""
    seconds = max(0, int(seconds))
    return f"+{seconds // 60}:{seconds % 60:02d}"


class Recent:
    """The last things that happened, newest last, with repeats counted instead of listed."""

    def __init__(self, size=40, start=None):
        self.start = time.monotonic() if start is None else start
        self.items = deque(maxlen=size)             # [first time, text, how many times in a row]

    def add(self, text, now=None):
        now = time.monotonic() if now is None else now
        text = " ".join(str(text).split())[:160]
        if not text:
            return
        if self.items and self.items[-1][1] == text:
            self.items[-1][2] += 1
        else:
            self.items.append([now, text, 1])

    def lines(self):
        return [f"{clock(t - self.start)}  {text}" + (f"  (x{n})" if n > 1 else "") for t, text, n in self.items]


class Health:
    """How the reading thread is doing: is it looping, how long each look takes, did it fail."""

    def __init__(self):
        self.polls = self.errors = 0
        self.last_error = ""
        self.last_at = None                         # when the last look finished (monotonic)
        self.last_ms = self.total_ms = self.max_ms = 0.0
        self.alive = True

    def finished(self, started, now=None):
        now = time.monotonic() if now is None else now
        ms = (now - started) * 1000
        self.polls += 1
        self.last_at, self.last_ms = now, ms
        self.total_ms += ms
        self.max_ms = max(self.max_ms, ms)

    def failed(self, exc, now=None):
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        self.last_at = time.monotonic() if now is None else now

    def average_ms(self):
        return self.total_ms / self.polls if self.polls else 0.0


def looks_wrong(f, now=None):
    """The usual causes, as plain sentences, from the facts. Empty if nothing stands out."""
    out = []
    t = f.get("tesseract") or {}
    game = f.get("game") or {}
    over = f.get("overlay") or {}
    rd = f.get("reader") or {}
    h = f.get("health") or {}
    if not t.get("found"):
        out.append("Tesseract wasn't found, so map names and the class can't be read.")
    if game.get("mode") == "window" and not game.get("found"):
        out.append("The MapleStory window wasn't found (is the game running, and not minimized?).")
    if game.get("minimized"):
        out.append("The game window is minimized.")
    mons = f.get("monitors") or []
    rect = game.get("rect")
    if rect and mons:
        big = max(mons, key=lambda m: m[0] * m[1])
        if rect[2] > big[0] + 8 or rect[3] > big[1] + 8:
            out.append(f"The game window ({rect[2]}x{rect[3]}) is bigger than the biggest screen "
                       f"({big[0]}x{big[1]}): Windows scaling may be making the overlay look at the wrong size.")
    if f.get("dpi_awareness") in ("not aware", "system"):
        out.append(f"The overlay isn't fully aware of Windows scaling ({f['dpi_awareness']}): "
                   "pictures of the screen may be the wrong size on a scaled or second screen.")
    if f.get("dpi") and f["dpi"] != 96:
        out.append(f"Windows scaling on the game's screen is {round(f['dpi'] / 96 * 100)}%.")
    for name in over.get("covering") or []:
        out.append(f"The overlay's own window is covering the {name}: move it away.")
    if game.get("found") and not rd.get("exp_box") and game.get("mode") == "window":
        out.append("The EXP bar hasn't been found in the game window.")
    if rd.get("exp_misses"):
        out.append(f"The EXP bar was missed {rd['exp_misses']} look(s) in a row.")
    if (f.get("rejected") or 0) >= 5:
        out.append("EXP readings keep not adding up (a dialog or effect over the bar, or digits misread).")
    if game.get("mode") == "window" and rd.get("exp_box") and rd.get("level_unknown"):
        out.append("The level is still unknown: it shows after the next EXP gain.")
    if rd.get("two_boxes"):
        out.append("Two mesos boxes are on screen (a storage or trade window is open), so mesos aren't read.")
    elif rd.get("exp_box") and not rd.get("mesos_box"):
        out.append("No mesos box found: is the inventory open?")
    if rd.get("mesos_box") and (rd.get("mesos_fails") or 0) >= 5:
        out.append(f"Mesos digits couldn't be read {rd['mesos_fails']} times in a row.")
    if t.get("found") and rd.get("exp_box") and rd.get("map_box") is None and game.get("mode") == "window":
        out.append("The minimap's name box wasn't found: is the minimap open?")
    if rd.get("job_why") and not f.get("job") and rd.get("exp_box"):
        out.append(f"Class not read: {rd['job_why']}.")
    if game.get("mode") == "region":
        out.append("'Select area' mode is on: the class, map name, mesos and party aren't read in it.")
    if h.get("alive") is False:
        out.append("The reading thread has stopped.")
    elif h.get("polls") and h.get("age") is not None and h["age"] > 10:
        out.append(f"The last look finished {int(h['age'])} s ago: the reading thread is stuck or very slow.")
    if h.get("errors"):
        out.append(f"{h['errors']} look(s) failed with an error (last: {h.get('last_error')}).")
    if h.get("average_ms") and h["average_ms"] > 400:
        out.append(f"Each look takes {int(h['average_ms'])} ms on average (slow).")
    return out


def _kv(label, value):
    return f"- {label}: {value}"


def _box(b):
    return "not found" if not b else "x{} y{} {}x{}".format(*(int(v) for v in b))


def summary(f):
    """The short version that goes into the GitHub issue's text (the address has a size limit)."""
    g, h = f.get("game") or {}, f.get("health") or {}
    rect = g.get("rect")
    screens = ", ".join("x".join(map(str, m)) for m in f.get("monitors") or []) or "?"
    lines = [
        _kv("Version", f"{f.get('version')} ({'download' if f.get('frozen') else 'from source'})"),
        _kv("System", f"{f.get('system')}, screens {screens}" + (f", scaling {round(f['dpi'] / 96 * 100)}%" if f.get("dpi") else "")),
        _kv("Game window", f"{'x'.join(map(str, rect[2:])) if rect else 'not found'}"
                           f" (EXP digits {g.get('gh') or '?'} px, {g.get('mode')} mode"
                           f"{', fullscreen' if g.get('fullscreen') else ''})"),
        _kv("Last EXP read", f.get("last_exp") or "nothing"),
        _kv("Level / class / map / party", f"{f.get('level') or '?'} / {f.get('job') or 'not read'} / "
                                           f"{f.get('map') or 'not read'} / {f.get('party')}"),
        _kv("Mesos", f"{f.get('mesos') if f.get('mesos') is not None else 'not read'}"),
        _kv("Tracking", f"{'on' if f.get('tracking') else 'off'}, {f.get('tracker')}"),
        _kv("Status", f.get("status") or ""),
        _kv("Reading thread", f"{h.get('polls', 0)} looks, {h.get('errors', 0)} failed, "
                              f"{int(h.get('average_ms') or 0)} ms each, last {int(h.get('age')) if h.get('age') is not None else '?'} s ago, "
                              f"overlay CPU {f.get('cpu_pct', '?')}%"),
    ]
    wrong = looks_wrong(f)
    block = ["", "**Things that look wrong**"] + ([f"- {w}" for w in wrong] if wrong else ["- Nothing stands out."])
    recent = (f.get("timeline") or [])[-12:]
    tail = ["", "**What happened last** (+m:ss since the overlay started)", "```", *recent, "```"] if recent else []
    return "\n".join(lines + block + tail)


def details(f):
    """Everything, for the text file that's dragged into the report."""
    g, rd, over, h, t = (f.get(k) or {} for k in ("game", "reader", "overlay", "health", "tesseract"))
    s = f.get("settings") or {}
    out = [f"Maple Classic Exp Overlay bug report details ({f.get('version')})", ""]
    out += ["== Computer ==",
            _kv("System", f.get("system")),
            _kv("Python", f.get("python")),
            _kv("Screens", ", ".join("x".join(map(str, m)) for m in f.get("monitors") or []) or "?"),
            _kv("Windows scaling at the game", f"{round(f['dpi'] / 96 * 100)}%" if f.get("dpi") else "?"),
            _kv("Scaling awareness", f.get("dpi_awareness") or "?"),
            _kv("Tesseract", f"{t.get('where') or 'not found'}, {t.get('version') or 'version unknown'}"),
            _kv("Overlay running for", clock(f.get("uptime_s") or 0)[1:]),
            _kv("Overlay CPU", f"{f.get('cpu_pct', '?')}% of one core since it started"), ""]
    out += ["== Game window ==",
            _kv("Mode", g.get("mode")),
            _kv("Found", g.get("found")),
            _kv("Window (x y w h)", g.get("rect") or "none"),
            _kv("Fullscreen", g.get("fullscreen")),
            _kv("EXP digit height", g.get("gh")),
            _kv("Selected area", g.get("region") or "none"),
            _kv("Overlay window", over.get("rect") or "?"),
            _kv("Overlay covers", ", ".join(over.get("covering") or []) or "nothing it reads"), ""]
    out += ["== What it found ==",
            _kv("EXP bar", f"{_box(rd.get('exp_box'))}, missed in a row: {rd.get('exp_misses')}, rejected: {f.get('rejected')}"),
            _kv("Level known", not rd.get("level_unknown")),
            _kv("Minimap name box", f"{_box(rd.get('map_box'))}, name read: {f.get('map') or 'no'}"),
            _kv("Mesos box", f"{_box(rd.get('mesos_box'))}, failed reads in a row: {rd.get('mesos_fails')}, "
                             f"two boxes: {rd.get('two_boxes')}, digits learned: {rd.get('mesos_learned')}"),
            _kv("Party list", f"{_box(rd.get('party_area'))}, party: {f.get('party')}"),
            _kv("Class", f"{f.get('job') or 'not read'}" + (f" ({rd.get('job_why')})" if rd.get("job_why") else "")), ""]
    out += ["== Reading thread ==",
            _kv("Alive", h.get("alive")),
            _kv("Looks", f"{h.get('polls')} ({h.get('errors')} failed)"),
            _kv("Time per look", f"{int(h.get('average_ms') or 0)} ms average, {int(h.get('max_ms') or 0)} ms slowest, last {int(h.get('last_ms') or 0)} ms"),
            _kv("Last look finished", f"{int(h['age'])} s ago" if h.get("age") is not None else "never"),
            _kv("Last error", h.get("last_error") or "none"), ""]
    out += ["== Settings ==", _kv("Settings", ", ".join(f"{k} {v}" for k, v in s.items())), ""]
    wrong = looks_wrong(f)
    out += ["== Things that look wrong =="] + ([f"- {w}" for w in wrong] if wrong else ["- Nothing stands out."]) + [""]
    out += ["== What happened (+m:ss since the overlay started) =="] + (f.get("timeline") or ["(nothing yet)"]) + [""]
    out += ["== errors.log (the end of it) ==", (f.get("errors") or "(no errors)").strip(), ""]
    return "\n".join(out)
