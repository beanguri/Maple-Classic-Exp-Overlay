"""Looking for a newer release, and sharing a run anonymously (only when Settings says so)."""
import json
import os
import re
import urllib.request

from maple_overlay.config import LATEST_RELEASE_API, STATS_SITE, VERSION


# --------------------------------------------------------------------------- updates
#
# At start (unless turned off in Settings) it asks GitHub for the latest release. The only
# other time it goes online is to share finished 15-minute runs, if the player agreed to that.
def version_key(version):
    """'v0.9.1-beta' -> something to compare versions by. A full release beats its betas."""
    main, _, pre = version.strip().lstrip("vV").partition("-")
    nums = [int(x) for x in re.findall(r"\d+", main)[:3]]
    return tuple(nums + [0] * (3 - len(nums))), 0 if pre else 1, pre

def _plain_text(markdown):
    """Release notes from GitHub, readable as plain text."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", markdown)          # [text](link) -> text
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)                       # **bold** (keeps exp_log.txt)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t.replace("\r", ""))
    return t.strip()[:5000]

def check_for_update(timeout=8):
    """(newer version, its release notes) if GitHub has a newer release, else None.
    Raises if GitHub can't be reached."""
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": f"MapleClassicExpOverlay/{VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    if not re.fullmatch(r"v?\d+(\.\d+){0,2}(-[\w.]+)?", tag):
        raise ValueError(f"unexpected release name {tag[:40]!r}")
    if version_key(tag) <= version_key(VERSION):
        return None
    return tag.lstrip("vV"), _plain_text(str(data.get("body") or ""))

def post_runs(install, runs, timeout=15):
    """Sends finished runs to the stats site (only if the player agreed to share).
    Returns the start times of the runs it's done with: the ones the site has (now or from
    before), and ones it turned down for good. Runs turned down only for today ("retry", too
    many sent today) are left to be sent again later."""
    if os.environ.get("MAPLE_EXP_DATA") and not os.environ.get("MAPLE_EXP_ALLOW_SHARE"):
        # MAPLE_EXP_DATA moves the data folder, which only tests do. What they have in their logs is
        # made up, and made-up runs must never reach the real site: so not while testing.
        raise RuntimeError("sharing is off while testing (MAPLE_EXP_DATA is set)")
    shared = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
              "seconds", "mesos", "mesos_seconds", "version", "seal")
    body = json.dumps({"install": install, "runs": [{k: r.get(k) for k in shared} for r in runs]}).encode()
    request = urllib.request.Request(f"{STATS_SITE}/api/runs", data=body, headers={
        "content-type": "application/json", "User-Agent": f"MapleClassicExpOverlay/{VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    return [r.get("started") for r in data.get("results", []) if r.get("ok") or not r.get("retry")]
