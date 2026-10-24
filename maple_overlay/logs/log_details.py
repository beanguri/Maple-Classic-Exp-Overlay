"""What the log's Details window shows for a line: numbers worked out from the log lines alone.

Pure functions (no screen, no tkinter) so they can be tested. A log line (entry) has: date, map, level, class,
party, seconds (time of non-stop EXP gain), exp, mesos, mesos_seconds (time the mesos were read for) and, when
potions were counted, potion_cost and potion_seconds. A "merged" entry is several party sizes added together.
"""


def hours(seconds):
    hh, mm = divmod(int(round((seconds or 0) / 60)), 60)
    return f"{hh} h {mm:02d} min" if hh else f"{mm} min"


def num(value):
    return f"{value:,.0f}"


def per_hour(value, seconds):
    return value / seconds * 3600 if seconds else None


def _rate(value, seconds):
    r = per_hour(value, seconds)
    return "—" if r is None else num(r)


def totals(entries):
    """One set of totals for log lines added together."""
    t = {"seconds": 0, "exp": 0, "mesos": 0, "mesos_seconds": 0, "potion_cost": 0, "potion_seconds": 0,
         "potions": False, "estimated": False}
    for e in entries:
        t["seconds"] += e.get("seconds") or 0
        t["exp"] += e.get("exp") or 0
        t["mesos"] += e.get("mesos") or 0
        t["mesos_seconds"] += e.get("mesos_seconds") or 0
        if "potion_cost" in e:
            t["potions"] = True
            t["potion_cost"] += e.get("potion_cost") or 0
            t["potion_seconds"] += e.get("potion_seconds", e.get("seconds") or 0) or 0
        t["estimated"] = t["estimated"] or bool(e.get("estimated"))
    return t


def line_sections(entries):
    """[(section title, [(label, value)])] for one line, or a map's party sizes shown as one."""
    t = totals(entries)
    first = entries[0]
    parties = sorted({e.get("party") or 1 for e in entries})
    party_text = ("Solo" if parties == [1] else
                  ", ".join("Solo" if p == 1 else f"{p} players" for p in parties))
    approx = "≈" if t["estimated"] else ""
    where = [("Date", first.get("date", "")), ("Map", first.get("map", "")),
             ("Level", first.get("level") if first.get("level") is not None else "?"),
             ("Class", first.get("class") or "—"), ("Party", party_text),
             ("Time of non-stop EXP gain", hours(t["seconds"])),
             ("5-minute stretches (about)", f"{t['seconds'] / 300:.1f}")]
    exp = [("EXP gained", approx + num(t["exp"])),
           ("EXP per hour", approx + _rate(t["exp"], t["seconds"])),
           ("EXP per 5 minutes", approx + (num(t["exp"] / t["seconds"] * 300) if t["seconds"] else "—"))]
    if t["mesos_seconds"]:
        covered = t["mesos_seconds"] / t["seconds"] * 100 if t["seconds"] else 0
        mesos = [("Mesos gained", num(t["mesos"])), ("Mesos per hour", _rate(t["mesos"], t["mesos_seconds"])),
                 ("Mesos were read for", f"{hours(t['mesos_seconds'])} ({covered:.0f}% of the time)")]
    else:
        mesos = [("Mesos", "not read for this line")]
    picked = _sum(entries, "mesos_text")
    if picked:
        mesos.append(("Mesos from the message lines", num(picked)))
    if t["potions"] and t["potion_seconds"]:
        mesos.append(("Potions used (cost)", num(t["potion_cost"])))
        mesos.append(("Potion cost per hour", _rate(t["potion_cost"], t["potion_seconds"])))
        if t["mesos_seconds"]:
            net = per_hour(t["mesos"], t["mesos_seconds"]) - per_hour(t["potion_cost"], t["potion_seconds"])
            mesos.append(("Net mesos per hour", num(net)))
    sections = [("Line", where), ("EXP", exp), ("Mesos", mesos)] + extra_sections(entries, t)
    if len(entries) > 1:
        rows = []
        for e in sorted(entries, key=lambda x: x.get("party") or 1):
            p = e.get("party") or 1
            rows.append(("Solo" if p == 1 else f"{p} players",
                         f"{hours(e.get('seconds'))} · {_rate(e.get('exp') or 0, e.get('seconds'))} EXP/hr"))
        sections.append(("Party sizes", rows))
    return sections


def _sum(entries, key):
    return sum(e.get(key) or 0 for e in entries)


def extra_sections(entries, t):
    """Sections from the counters the log keeps per stretch (kills, drops, the party's bonus, deaths, level progress).
    Lines logged before those counters existed have none, and the sections that need them are left out. Rates are
    over the time the counters were kept (x_seconds), not the whole line."""
    xs = _sum(entries, "x_seconds")
    out = []
    if xs:
        kills, drops = _sum(entries, "kills"), _sum(entries, "mesos_drops")
        rows = []
        if kills:
            exp_there = t["exp"] * xs / t["seconds"] if t["seconds"] else 0     # (the EXP over the time kills were counted)
            rows += [("Kills (EXP messages)", num(kills)), ("Kills per hour", _rate(kills, xs)),
                     ("EXP per kill", num(exp_there / kills))]
        if drops:
            rows += [("Mesos drops picked up", num(drops)), ("Drops per hour", _rate(drops, xs)),
                     ("Mesos per drop", num(_sum(entries, "mesos_text") / drops))]
        if rows:
            out.append(("Kills and drops (from the game's messages)", rows))
        own, bonus = _sum(entries, "own_exp"), _sum(entries, "bonus_exp")
        if own + bonus:
            rows = [("Your own EXP", num(own)), ("Party bonus EXP", num(bonus)),
                    ("Party bonus share", f"{bonus / (own + bonus) * 100:.1f}%"),
                    ("Split known for", f"{(own + bonus) / t['exp'] * 100:.0f}% of the EXP" if t["exp"] else "\u2014")]
            out.append(("Your EXP and the party's bonus", rows))
        deaths, levels = _sum(entries, "deaths"), _sum(entries, "levels")
        out.append(("Deaths and level-ups", [("Deaths", str(deaths)), ("Deaths per hour", f"{deaths / xs * 3600:.1f}"),
                                             ("Levels gained", str(levels))]))
    total = max((e.get("lvl_total") or 0 for e in entries), default=0)
    if total and t["seconds"] and t["exp"]:
        hr = t["exp"] / t["seconds"] * 3600
        rows = [("EXP this level needs", num(total)), ("Level progress per hour", f"{hr / total * 100:.1f}%"),
                ("Hours for a whole level at this pace", f"{total / hr:.1f}")]
        last = max((e for e in entries if e.get("remaining") is not None and e.get("lvl_total")),
                   key=lambda e: e.get("updated", 0), default=None)
        if last is not None:
            rows.append(("To the next level (at the last stretch)", f"{hours(last['remaining'] / hr * 3600)} "
                                                                    f"({num(last['remaining'])} EXP)"))
        out.append(("Level progress", rows))
    trend = pace_trend(entries)
    if trend is not None:
        out.append(("Pace", [("Last three stretches against the whole line", f"{trend:+.0f}%")]))
    return out


def pace(entries):
    """EXP per hour of each kept 5-minute stretch, oldest first, as the Details window's chart."""
    out = []
    for e in sorted(entries, key=lambda x: (x.get("date") or "", x.get("updated") or 0)):
        out += [b[0] / b[1] * 3600 for b in e.get("blocks", []) if len(b) == 2 and b[1]]
    return out


def pace_trend(entries):
    """How the last three stretches compare with the average of all of them, in %, or None with too few."""
    p = pace(entries)
    if len(p) < 5:
        return None
    avg = sum(p) / len(p)
    return (sum(p[-3:]) / 3 / avg - 1) * 100 if avg else None


def compare_sections(entries, all_entries):
    """How the line compares with the player's other log lines on the same map (any day, any class)."""
    first = entries[0]
    name = first.get("map")
    same = [e for e in all_entries if e.get("map") == name and e.get("seconds")]
    if not same:
        return []
    mine = totals(entries)
    allt = totals(same)
    days = sorted({e.get("date") for e in same})
    rows = [("Days logged on this map", str(len(days))), ("Total time on this map", hours(allt["seconds"])),
            ("Your average EXP per hour here", _rate(allt["exp"], allt["seconds"]))]
    avg = per_hour(allt["exp"], allt["seconds"])
    this = per_hour(mine["exp"], mine["seconds"])
    if avg and this is not None:
        rows.append(("This line against your average", f"{(this / avg - 1) * 100:+.0f}%"))
    best = max(same, key=lambda e: e["exp"] / e["seconds"])
    rows.append(("Best EXP per hour here", f"{_rate(best['exp'], best['seconds'])} ({best.get('date')}, "
                                           f"{hours(best['seconds'])})"))
    solo = totals([e for e in same if (e.get("party") or 1) <= 1])
    party = totals([e for e in same if (e.get("party") or 1) > 1])
    if solo["seconds"]:
        rows.append(("Solo, EXP per hour (all days)", _rate(solo["exp"], solo["seconds"])))
    if party["seconds"]:
        rows.append(("Party, EXP per hour (all days)", _rate(party["exp"], party["seconds"])))
    return [("On this map, all your logs", rows)]


def as_text(sections):
    """The sections as plain text (for copying)."""
    out = []
    for title, rows in sections:
        out.append(title)
        out += [f"  {k}: {v}" for k, v in rows]
        out.append("")
    return "\n".join(out).strip()
