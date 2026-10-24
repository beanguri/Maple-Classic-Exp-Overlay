"""Numbers and times as they are shown."""



APPROX = "≈"           # shown before a number worked out from a % alone: "≈15,429"

# --------------------------------------------------------------------------- UI helpers
def fmt_num(n):
    return "—" if n is None else f"{n:,.0f}"

def fmt_short(n):
    if n is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.2f}{suffix}"
    return f"{n:,.0f}"

def approx_num(n, estimated):
    """A number as shown: with an approx sign in front when it was worked out from a % alone. A very big one is
    shortened (99,999,999 -> 100.00M) so the sign never makes it run out of its box."""
    if n is None or not estimated:
        return fmt_num(n)
    return APPROX + (fmt_num(n) if abs(n) < 10_000_000 else fmt_short(n))

def fmt_eta(seconds):
    """A time to wait, the way people say it: '36 min', '2 h 05 min'. None if too far off."""
    if seconds is None or seconds < 0 or seconds >= 100 * 3600:
        return None
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"

def fmt_dur(seconds):
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
