"""A log line's text, and the rows of the log table."""
from datetime import date

from maple_overlay.config import BLOCKS_KEPT, EXTRA_KEYS, NOTE_MARK
from maple_overlay.ui.format import APPROX


# --------------------------------------------------------------------------- auto log
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"

def party_label(n):
    return "Solo" if not n or n <= 1 else f"{n}ppl party"

def fmt_logged(seconds, short=False):
    """How long a log line was measured over: '25 min' / '1 h 05 min', or short '25m' / '1h 05m'."""
    hours, minutes = divmod(int(round((seconds or 0) / 60)), 60)
    if short:
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"

def net_text(e):
    """Net mesos/hr of a log line ('' if no potion cost was counted, '?' if the mesos weren't read):
    mesos/hr minus what the potions cost per hour."""
    if "potion_cost" not in e:
        return ""
    ms, ps = e.get("mesos_seconds", 0), e.get("potion_seconds", e["seconds"])
    if not ms or not ps:
        return "?"
    return f"{e['mesos'] / ms * 3600 - e['potion_cost'] / ps * 3600:,.0f}"

def format_entry(e):
    """e.g. 'Oct 8th, 2026 - Rocky Road II - Lv 27 - Bandit - Solo - 15,429exp/hr - 3,210mesos/hr - 25 min'"""
    d = date.fromisoformat(e["date"])
    level = e["level"] if e["level"] is not None else "?"
    parts = [f"{MONTHS[d.month - 1]} {_ordinal(d.day)}, {d.year}", e["map"], f"Lv {level}"]
    if e.get("class"):
        parts.append(e["class"])
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    if tracked:
        parts.append(party_label(e["party"]))
    parts.append(f"{APPROX if e.get('estimated') else ''}{e['exp'] / e['seconds'] * 3600 if e['seconds'] else 0:,.0f}exp/hr")
    if tracked:
        ms = e.get("mesos_seconds", 0)
        parts.append(f"{e['mesos'] / ms * 3600:,.0f}mesos/hr" if ms else "?mesos/hr")
        if net_text(e):
            parts.append(f"{net_text(e)}net/hr")
    parts.append(fmt_logged(e["seconds"]))
    return " - ".join(parts)

def clean_note(text):
    """A note as it's kept: plain text with \\n line breaks, no control characters or lone
    surrogates (neither can be shown or saved), without blanks at either end."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or (ch >= " " and ch != "\x7f"))
    return text.encode("utf-8", "ignore").decode("utf-8").strip()

def note_of(e):
    """The personal note on a log line, ready to show ('' if none, or if what's there isn't text)."""
    n = e.get("note")
    return clean_note(n) if isinstance(n, str) else ""

def notes_of(entries):
    """One note for log lines shown as one (a map's party sizes): each different note once."""
    return "\n\n".join(dict.fromkeys(n for n in map(note_of, entries) if n))

def log_row(e, part=False):
    """The log table's cells for an entry: Date, Map, Lv, Class, Party, Time, EXP, Mesos (the totals),
    EXP/hr, Mesos/hr, Net/hr, Note. `part`: one party size under a merged party line, so just its own numbers."""
    d = date.fromisoformat(e["date"])
    day = f"{MONTHS[d.month - 1]} {d.day}" + ("" if d.year == date.today().year else f" '{d.year % 100:02d}")
    level = e["level"] if e["level"] is not None else "?"
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    party = ("Solo" if not e["party"] or e["party"] <= 1 else f"{e['party']}ppl") if tracked else ""
    exp = f"{APPROX if e.get('estimated') else ''}{e['exp'] / e['seconds'] * 3600:,.0f}" if e["seconds"] else "—"
    ms = e.get("mesos_seconds", 0)
    mesos = f"{e['mesos'] / ms * 3600:,.0f}" if ms else ("?" if tracked else "")
    note = NOTE_MARK if note_of(e) else ""
    exp_all = f"{APPROX if e.get('estimated') else ''}{e['exp']:,}"          # the totals: what was gained in all
    mesos_all = f"{e['mesos']:,}" if ms else ("?" if tracked else "")
    if part:
        return ("", "", "", "", party, fmt_logged(e["seconds"], short=True), exp_all, mesos_all, exp, mesos,
                net_text(e), note)
    return (day, e["map"], level, e.get("class") or "", party, fmt_logged(e["seconds"], short=True), exp_all, mesos_all,
            exp, mesos, net_text(e), note)

def party_groups(entries):
    """Log lines as the log shows them, newest first: the party sizes (2 to 6) of one map on
    one day, at one level and class, together (smallest party first); everything else, solo
    play included, on its own. (Party sizes come and go as people join and leave.)"""
    groups = {}
    for e in entries:
        party = e.get("party")
        key = (e["date"], e["map"], e["level"], e.get("class"), "party") if party and party >= 2 else id(e)
        groups.setdefault(key, []).append(e)
    return [(key, sorted(g, key=lambda e: e["party"]) if len(g) > 1 else g) for key, g in groups.items()]

def merged(entries):
    """Log lines added together, so EXP/hr and mesos/hr are over all of their time."""
    m = dict(entries[0])
    if any(e.get("estimated") for e in entries):
        m["estimated"] = True                    # (one estimated part makes the whole line an estimate)
    for k in ("exp", "seconds", "mesos", "mesos_seconds"):
        m[k] = sum(e.get(k) or 0 for e in entries)
    for k in EXTRA_KEYS:
        if any(k in e for e in entries):
            m[k] = sum(e.get(k) or 0 for e in entries)
    if any("blocks" in e for e in entries):
        m["blocks"] = [b for e in entries for b in e.get("blocks", [])][-BLOCKS_KEPT:]
    if any("lvl_total" in e for e in entries):
        last = max((e for e in entries if "lvl_total" in e), key=lambda e: e.get("updated", 0))
        m["lvl_total"], m["remaining"] = last["lvl_total"], last.get("remaining")
    if any("potion_cost" in e for e in entries):
        m["potion_cost"] = sum(e.get("potion_cost") or 0 for e in entries)
        m["potion_seconds"] = sum(e.get("potion_seconds", e["seconds"]) for e in entries if "potion_cost" in e)
    else:
        m.pop("potion_cost", None)
    return m

def group_row(entries, is_open=False):
    """The log table's cells for a map's party sizes together, e.g. Party '4–5ppl ▸'."""
    row = list(log_row(merged(entries)))
    sizes = [e["party"] for e in entries]
    row[4] = f"{sizes[0]}–{sizes[-1]}ppl {'▾' if is_open else '▸'}"
    row[11] = NOTE_MARK if notes_of(entries) else ""
    return tuple(row)
