"""Where the game's UI sits in its window, and how big it is: one rule for every reader v2 part.

The game lays its UI out on a 1024 x 768 design area, centred left to right on the window's bottom edge, then
draws it bigger or smaller:
- UI Size "Default": the UI grows with the window, by window height / 768, but never below 1x (800x600 and
  1280x720 draw it at 1x).
- UI Size "Ideal": the UI grows in whole steps only: 1x below 1440 tall, 2x from 1440 up (windowed and
  fullscreen, measured on the 2026-10-26 recordings up to 5K; never 3x). Send report #5 saw 1x at 1440p, so
  the other step is still tried.
The overlay can't see that setting, so every finder tries candidate_scales() and keeps what fits; the EXP
bar's digit height (gh), when known, says which one it is (mode_from_gh).

Pure: no screen, no files.
"""
DESIGN_W, DESIGN_H = 1024, 768

# Every size the game offers (windowed; fullscreen uses the desktop's size instead).
GAME_SIZES = ((800, 600), (1024, 768), (1280, 720), (1366, 768), (1920, 1080), (1920, 1200), (2560, 1440),
              (2560, 1600), (2732, 1536), (3840, 2160))

IDEAL_2X_H = 1440           # Ideal draws the UI at 2x from this window height up, 1x below it
MAX_STEP = 2                # no Ideal step above this is tried (never seen, up to 5K)
SAME = 0.01                 # two scales this close (as a share) are the same scale

# The EXP digit height (gh) measured with UI Size Default, by window height (A10 live check 2026-10-25; 600 and
# 720 from the recordings: the UI is 1x there). Between these, it's taken as a straight line.
DEFAULT_GH = ((600, 7.0), (768, 7.0), (1080, 11.0), (1200, 11.0), (1440, 14.0), (1536, 14.0), (1600, 15.0),
              (2160, 20.0), (2880, 26.0))
GH_PER_STEP = 7.0           # with UI Size Ideal, gh is this times the step
GH_NEAR = 1.5               # a measured gh this close to a mode's expected one fits that mode


def default_scale(window_h):
    """UI Size Default: screen pixels per design pixel."""
    return max(1.0, window_h / DESIGN_H)


def ideal_step(window_h):
    """UI Size Ideal: the whole step the UI is drawn at."""
    return 2 if window_h >= IDEAL_2X_H else 1


def candidate_scales(window_h):
    """The (mode, scale) pairs worth trying, most likely first: Default, then Ideal's step, then the other
    whole steps (in case the step rule is off). mode is "default" or "ideal", or None where both modes draw
    the UI at the same size (so it can't be told which one is set)."""
    others = [("ideal", float(s)) for s in range(1, MAX_STEP + 1) if s != ideal_step(window_h)]
    return _merge(_main_pairs(window_h) + others)


def _main_pairs(window_h):
    """Default and Ideal's own step."""
    return [("default", default_scale(window_h)), ("ideal", float(ideal_step(window_h)))]


def _merge(pairs):
    """pairs with the same scale made one (its mode None if the modes differ), first ones first."""
    out = []
    for mode, scale in pairs:
        same = next((i for i, (_, s) in enumerate(out) if abs(s - scale) <= SAME * scale), None)
        if same is None:
            out.append((mode, scale))
        elif out[same][0] != mode:
            out[same] = (None, out[same][1])
    return out


def default_gh(window_h):
    """The EXP digit height expected at this window height with UI Size Default."""
    pts = DEFAULT_GH
    if window_h <= pts[0][0]:
        return pts[0][1] * window_h / pts[0][0]
    for (h0, g0), (h1, g1) in zip(pts, pts[1:]):
        if window_h <= h1:
            return g0 + (g1 - g0) * (window_h - h0) / (h1 - h0)
    return pts[-1][1] * window_h / pts[-1][0]


def mode_from_gh(gh, window_h):
    """(mode, scale) the EXP digit height says is set, or None when it can't tell (no gh, both modes fit,
    or neither does). Only says WHICH: the scale itself comes from the window, never from gh."""
    if not gh:
        return None
    # Default and Ideal's own step first. Only when neither fits, the other step (Send report #5: gh 7 at 1440p,
    # where the rule says 2x); it counts only if it alone fits.
    for pairs in (_merge(_main_pairs(window_h)), candidate_scales(window_h)):
        fits = [(mode, scale) for mode, scale in pairs
                if abs(gh - (default_gh(window_h) if mode == "default" else GH_PER_STEP * scale)) <= GH_NEAR]
        if fits:
            return fits[0] if len(fits) == 1 and fits[0][0] is not None else None
    return None


def design_to_window(x, y, window_w, window_h, scale):
    """Where a design-area point (x, y) is in the window, at this UI scale."""
    return window_w / 2 + (x - DESIGN_W / 2) * scale, window_h - (DESIGN_H - y) * scale


def window_to_design(x, y, window_w, window_h, scale):
    """The design-area point at window point (x, y): the other way round."""
    return (x - window_w / 2) / scale + DESIGN_W / 2, DESIGN_H - (window_h - y) / scale
