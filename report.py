"""What a bug report says about the overlay, so a problem can be worked out from the report alone.

The App collects the facts (a plain dict, see App._report_facts); this module keeps the
short history of what happened (Recent), keeps count of how the reading thread is doing
(Health), spots the usual causes (looks_wrong) and writes it all out (summary, details).
Nothing here reads the screen or the network. Nothing personal goes in: no character name,
and the Windows user name is taken out of anything that looks like a file path by the caller.
"""
import json
import re
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
    if t.get("state") == "no_eng":
        out.append("Tesseract has no usable English file (" + ("the folder's name has unusual characters"
                   if t.get("why") == "path" else "eng.traineddata is missing from its tessdata folder") + "), so map names and the class can't be read.")
    elif not t.get("found"):
        out.append("Tesseract wasn't found, so map names and the class can't be read.")
    if game.get("mode") == "window" and not game.get("found"):
        out.append("The MapleStory window wasn't found (is the game running, and not minimized?).")
    if game.get("minimized"):
        out.append("The game window is minimized.")
    mons = f.get("monitors") or []
    rect = game.get("rect")
    if rect and mons and not any(rect[2] <= m[0] + 8 and rect[3] <= m[1] + 8 for m in mons):     # (fits on no screen)
        out.append(f"The game window ({rect[2]}x{rect[3]}) is bigger than every screen "
                   f"({', '.join(f'{m[0]}x{m[1]}' for m in mons)}): Windows scaling may be making the overlay look at the wrong size.")
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


def potion_lines(p):
    """The "Potions" part of the details: whether the bar was seen, what each slot read and why a count was dropped.
    `p` is App._report_facts' potion facts. Item names come from the shipped table only (nothing the player typed)."""
    if not p:
        return [_kv("Potions", "no facts")]
    if not p.get("on"):
        return [_kv("Potions", "off in Settings")]
    since = p.get("unseen_for")
    seen = ("yes" if p.get("seen") else "no") + (f", not seen for {clock(since)[1:]}" if since else "")
    out = [_kv("Bar seen", seen),
           _kv("Layout", f"{p.get('layout') or '?'}, UI size {p.get('mode') or 'same in both'}, scale {p.get('scale') or '?'}"),
           _kv("Cost row shows", p.get("cost_text") or "?")]
    slots = p.get("slots") or []
    for s in slots:
        if s.get("kind") != "consumable":
            continue
        if s.get("dropped"):
            why = "count dropped (icon too far from the item)"
        elif s.get("count") is None:
            why = "count not read"
        else:
            why = f"count {s['count']}"
        dist = "?" if s.get("dist") is None else f"{s['dist']:.2f}"
        out.append(_kv(f"Slot {s.get('index')}", f"{s.get('item') or 'unknown item'}, {why}, icon distance {dist}"))
    others = {}
    for s in slots:
        if s.get("kind") != "consumable":
            others[s.get("kind") or "unknown"] = others.get(s.get("kind") or "unknown", 0) + 1
    out.append(_kv("Other slots", ", ".join(f"{k} {n}" for k, n in sorted(others.items())) or "none"))
    out.append(_kv("Shipped items with your own price", ", ".join(p.get("own_prices") or []) or "none"))
    return out


def potion_summary(p):
    """One line for the short GitHub summary."""
    if not p or not p.get("on"):
        return "off" if p else "no facts"
    if not p.get("seen"):
        return "bar not seen"
    cons = [s for s in p.get("slots") or [] if s.get("kind") == "consumable"]
    read = sum(s.get("count") is not None for s in cons)
    return f"bar seen, {p.get('layout')}, {read} of {len(cons)} counts read"


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
        _kv("Potions", potion_summary(f.get("potions"))),
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


def answer_text(a):
    """An EXP answer for the text: "68,601 [71.68%]", "[58.47%]" (percentage only) or "nothing"."""
    if not a:
        return "nothing"
    return f"[{a[1]:.2f}%]" if a[0] is None else f"{a[0]:,} [{a[1]:.2f}%]"


def part_answer_text(a):
    """A plain answer of a part's shadow (an amount of mesos, a party size) for the text."""
    return "nothing" if a is None else f"{a:,}" if isinstance(a, int) else str(a)


def shadow_lines(s):
    """The "Reader v2 (test)" part of the details: what the new reader did beside the old one. `s` is one part's
    shadow facts (the EXP bar's, or a part's from shadow_parts)."""
    c = s.get("counts") or {}
    text = answer_text if s.get("name", "exp") == "exp" else part_answer_text
    state = ("off in Settings" if not s.get("on") else "stopped (too slow here)" if s.get("stopped")
             else "every other look (slow here)" if s.get("thinned") else "on")
    out = [_kv("State", state),
           _kv("Looks", ", ".join(f"{k} {c.get(k, 0)}" for k in ("agree", "disagree", "v2_only", "old_only", "neither", "skipped", "errors")))]
    if s.get("name", "exp") == "exp":
        out += [_kv("Own strip grabs", c.get("grabs", 0)),
                _kv("EXP changed between the two pictures", c.get("ticks", 0))]
    why = s.get("why") or {}
    out += [_kv("Old reader only, by why v2 didn't read", ", ".join(f"{k} {why.get(k, 0)}" for k in ("not_found", "unclear"))),
            _kv("Time per look", f"v2 {s.get('v2_ms')} ms, old reader {s.get('old_ms')} ms" if s.get("v2_ms") is not None else "not measured yet")]
    last = s.get("last")
    out.append(_kv("Last answers", f"old {text(last[0])}, v2 {text(last[1])}" if last else "none yet"))
    for old, new in s.get("diffs") or []:
        out.append(_kv("Disagreed", f"old {text(old)}, v2 {text(new)}"))
    return out


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
            _kv("Tesseract state", f"{t.get('state') or '?'}{' (' + t['why'] + ')' if t.get('why') else ''}"),
            _kv("Tesseract program", t.get("exe") or "none"),
            _kv("Tesseract data folder", t.get("tessdata") or "none"),
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
    out += ["== Potions =="] + potion_lines(f.get("potions")) + [""]
    out += ["== Reader v2 (test) =="] + shadow_lines(f.get("shadow") or {}) + [""]
    for part, title in (("mesos", "Mesos"), ("party", "Party size")):
        facts = (f.get("shadow_parts") or {}).get(part)
        if facts:
            out += [f"== Reader v2 (test): {title} =="] + shadow_lines(facts) + [""]
    out += ["== Settings ==", _kv("Settings", ", ".join(f"{k} {v}" for k, v in s.items())), ""]
    wrong = looks_wrong(f)
    out += ["== Things that look wrong =="] + ([f"- {w}" for w in wrong] if wrong else ["- Nothing stands out."]) + [""]
    out += ["== What happened (+m:ss since the overlay started) =="] + (f.get("timeline") or ["(nothing yet)"]) + [""]
    out += ["== errors.log (the end of it) ==", (f.get("errors") or "(no errors)").strip(), ""]
    return "\n".join(out)


# ---- sending a report to the developer (Send report)

MAX_MESSAGE = 2000                  # what the site takes (server/src/reports.js), counted like JavaScript: UTF-16 units
MAX_DETAILS = 12000
MAX_BODY = 16000                    # the site refuses a body over 16,384 bytes; this leaves a little room
MAX_MESSAGE_BYTES = 8000            # the typed message may take at most half of that, so the details always have room
# What JavaScript's trim() removes (the site trims the message with it): not Python's strip()
JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_LINK = re.compile(r"http|www\.|discord\.gg|\.com/", re.I)
_PATHS = (
    # Python writes the backslashes of a path inside an error message twice ('C:\\Users\\Me\\x.py'), so a
    # separator is a run of backslashes or one slash.
    re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:(?:\\+|/(?!/))(?:[^\\/:*?"<>|\r\n]+(?:\\+|/))*[^\s\\/:*?"<>|]*'),      # C:\Users\Me\OneDrive - Co\x.py
    re.compile(r'\\{2,}(?:[^\\/:*?"<>|\r\n]+[\\/]+)*[^\s\\/:*?"<>|]*'),             # \\server\share\x
    re.compile(r"(?<![\w:./])/(?:Users|home)/(?:[^/\r\n]+/)*[^\s/]*"),              # /Users/me/Library/x
    re.compile(r"(?<![\w:./])/(?:[\w.\-~@+]+/)+[\w.\-~@+]*"),                       # /Applications/X.app/y
)


def _last_part(match):
    return re.split(r"[\\/]+", match.group(0).rstrip("\\/"))[-1] or "<folder>"


def scrub(text, names=(), limit=MAX_DETAILS):
    """The text with nothing personal left: every absolute path shortened to its last part, each of `names`
    (Windows or macOS user name, computer name) replaced, and no longer than `limit` characters (None: whole).
    The cut comes last, so it can never leave half a path or name."""
    text = str(text)
    for pattern in _PATHS:
        text = pattern.sub(_last_part, text)
    for name in {n for n in names if n and len(n) > 2}:
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", "<you>", text, flags=re.I)
    return text[:limit]


def units(text):
    """How long the site thinks a text is: JavaScript counts UTF-16 units, so an emoji is 2."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def encode_body(body):
    """The bytes that are posted: JSON as UTF-8 (a Chinese character is 3 bytes, not the 6 of \\uXXXX)."""
    return json.dumps(body, ensure_ascii=False).encode("utf-8", "replace")


def clean_message(message):
    """The typed message trimmed the way the site trims it."""
    return message.strip(JS_SPACE)


def message_problem(message):
    """Why the typed message can't be sent (in the player's words), or None."""
    message = clean_message(message)
    if not message:
        return "Please write a sentence about what went wrong."
    if units(message) > MAX_MESSAGE:
        return f"Please keep it under {MAX_MESSAGE:,} characters."
    if len(encode_body({"message": message})) > MAX_MESSAGE_BYTES:
        return "Please keep it shorter."
    if _LINK.search(message):
        return "Please leave out links."
    return None


def report_body(health_id, version, message, details):
    """What /api/report takes: exactly these four fields. The details are cut (from the end) until they are
    within the site's 12,000 characters and the whole body fits its 16 KB, whatever language was typed."""
    body = {"id": health_id, "version": version, "message": clean_message(message), "details": str(details)}

    def fits(n):
        body["details"] = str(details)[:n]
        return units(body["details"]) <= MAX_DETAILS and len(encode_body(body)) <= MAX_BODY
    low, high = 0, len(str(details))
    while low < high:                           # the longest start of the details that still fits
        mid = (low + high + 1) // 2
        low, high = (mid, high) if fits(mid) else (low, mid - 1)
    fits(low)
    return body
