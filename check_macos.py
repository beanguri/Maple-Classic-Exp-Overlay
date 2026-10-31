"""Checks a Mac build (build_macos.py) on the Mac it was built on, the way a player would get it. Run it with
Homebrew out of the way (the GitHub build moves its Cellar aside first), so nothing can quietly come from it.

    python check_macos.py <the .app> <the .dmg>

Must pass, or it exits 1:
- every Mach-O file loads only from the app itself or from macOS;
- none needs a newer macOS than Info.plist promises (LSMinimumSystemVersion);
- the signature verifies (ad hoc);
- the overlay's own lookup finds the Tesseract inside the app, and it reads text the way the overlay calls it,
  also the class from real status-bar pictures;
- the app copied out of the .dmg (like dragging it to Applications) starts on empty data (sharing and updates off)
  and is still running 12 s later, with no errors.log.
Also reported: what Gatekeeper says (rejected: no notarization), sizes. A report goes to build/macos/check-<arch>.md
and the run's summary page.
"""
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MACHO = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"}
INSIDE = ("/System/", "/usr/lib/", "@rpath/", "@loader_path/", "@executable_path/")

lines, failed = [], []


def say(text=""):
    print(text, flush=True)
    lines.append(text)


def must(name, ok, detail=""):
    say(f"- {'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    if not ok:
        failed.append(name)


def sh(*args, **kw):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)


def version(v):
    return tuple(int(x) for x in v.split("."))


def macho_files(app):
    for p in app.rglob("*"):
        if p.is_file() and not p.is_symlink():
            with open(p, "rb") as f:
                if f.read(4) in MACHO:
                    yield p


def minos_of(p):
    """The macOS a Mach-O file needs: the highest over its architectures, read only from the LC_BUILD_VERSION
    (minos) and LC_VERSION_MIN_MACOSX (version) load commands. None if it says nothing."""
    found = []
    for block in sh("otool", "-arch", "all", "-l", p).stdout.split("Load command"):
        if "cmd LC_BUILD_VERSION" in block:
            found += re.findall(r"\n\s*minos (\d+(?:\.\d+)*)", block)
        elif "cmd LC_VERSION_MIN_MACOSX" in block:
            found += re.findall(r"\n\s*version (\d+(?:\.\d+)*)", block)
    return max(found, key=version, default=None)


def check_bundle(app, min_os):
    say("## Bundle")
    files = list(macho_files(app))
    bad, mins = [], {}
    for p in files:
        for r in sh("otool", "-L", p).stdout.splitlines()[1:]:
            ref = r.strip().split(" (")[0]
            if ref and not ref.startswith(INSIDE):
                bad.append(f"{p.relative_to(app)} -> {ref}")
        mins[str(p.relative_to(app))] = minos_of(p)
    must("no Mach-O file loads anything from outside the app or macOS", not bad, "; ".join(bad[:10]))
    over = {k: v for k, v in mins.items() if v and version(v) > version(min_os)}
    top = sorted(((k, v) for k, v in mins.items() if v), key=lambda kv: version(kv[1]), reverse=True)[:5]
    must(f"no file needs a newer macOS than {min_os}", not over, "; ".join(f"{k} needs {v}" for k, v in over.items()))
    say(f"- {len(files)} Mach-O files; highest: " + ", ".join(f"{v} ({k})" for k, v in top))
    tess = app / "Contents" / "Frameworks" / "tesseract" / "tesseract"
    say(f"- tesseract archs: {sh('lipo', '-archs', tess).stdout.strip()}; loads: "
        + ", ".join(r.strip().split(' (')[0] for r in sh('otool', '-L', tess).stdout.splitlines()[1:]))
    v = sh("codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
    must("codesign --verify --deep --strict", v.returncode == 0, (v.stderr.strip().splitlines() or [""])[-1])
    s = sh("spctl", "--assess", "--type", "execute", "-vv", app)
    say(f"- Gatekeeper (spctl): {(s.stderr or s.stdout).strip()} (expected: rejected, no notarization)")
    say(f"- .app size on disk: {sh('du', '-sh', app).stdout.split()[0]}")


def check_tesseract(app):
    say("## Tesseract inside the app")
    fw = app / "Contents" / "Frameworks"
    exe = fw / "tesseract" / "tesseract"
    v = sh(exe, "--version")
    must("tesseract --version runs", v.returncode == 0, ((v.stdout or v.stderr).splitlines() or [""])[0])
    os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp(prefix="mxp-check-")
    sys.path.insert(0, str(ROOT))
    import cv2
    import numpy as np
    import maple_exp as m
    import ocr
    m.APP_DIR, m.HOME_DIR = fw, app / "Contents" / "MacOS"
    path, arg, state, why = m.locate_tesseract()
    must("the overlay's own lookup finds it", state == "ok" and path == str(exe),
         f"path={path} tessdata={arg!r} state={state} why={why!r}")
    sample = np.full((60, 220), 255, np.uint8)
    cv2.putText(sample, "AB 12", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    try:
        got, err = ocr.Tesseract(str(exe), tessdata=arg or None).read_line(sample).strip(), ""
    except Exception as e:
        got, err = None, f"{type(e).__name__}: {e}"
    must("reads 'AB 12' the way the overlay calls it", got == "AB 12", f"got {got!r} {err}")
    try:
        m.TESS, m.tesseract_ok = ocr.Tesseract(str(exe), tessdata=arg or None), True
        frames = ROOT / "tests" / "frames"
        parts = json.loads((frames / "screen_parts.json").read_text())["status"]
        right = sum(m.read_job(cv2.imread(str(frames / n)), i["x0"], i["cy"], i["gh"]) == i["job"]
                    for n, i in parts.items())
        must("class read from real status-bar pictures", right == len(parts), f"{right}/{len(parts)}")
    except Exception as e:
        must("class read from real status-bar pictures", False, f"{type(e).__name__}: {e}")


def check_launch(dmg, seconds=12):
    say("## Started from the .dmg (empty data: sharing and updates off)")
    mnt = Path(tempfile.mkdtemp(prefix="mxp-mnt-"))
    a = sh("hdiutil", "attach", dmg, "-nobrowse", "-readonly", "-mountpoint", mnt)
    must("the .dmg mounts", a.returncode == 0, a.stderr.strip())
    if a.returncode:
        return
    try:
        link = mnt / "Applications"
        must("it holds the app and a link to Applications",
             link.is_symlink() and os.readlink(link) == "/Applications" and any(p.suffix == ".app" for p in mnt.iterdir()),
             str(sorted(p.name for p in mnt.iterdir())))
        apps = Path(tempfile.mkdtemp(prefix="mxp-apps-"))
        app = apps / next(p.name for p in mnt.iterdir() if p.suffix == ".app")
        sh("ditto", mnt / app.name, app)                          # "drag to Applications"
        exe = next((app / "Contents" / "MacOS").iterdir())
        data = Path(tempfile.mkdtemp(prefix="mxp-data-"))
        env = dict(os.environ, MAPLE_EXP_DATA=str(data))
        env.pop("MAPLE_EXP_ALLOW_SHARE", None)
        out = data.parent / f"{data.name}-stdout.txt"
        with open(out, "w") as log:
            p = subprocess.Popen([str(exe)], env=env, stdout=log, stderr=subprocess.STDOUT)
            time.sleep(seconds)
            alive = p.poll() is None
            shot = ROOT / "build" / "macos" / f"screen-{os.uname().machine}.png"
            sh("screencapture", "-x", shot)
            must(f"still running after {seconds} s", alive, "" if alive else f"exit code {p.returncode}")
            if alive:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        errors = data / "errors.log"
        must("no errors.log", not errors.exists(), errors.read_text(errors="replace")[:2000] if errors.exists() else "")
        say(f"- output: {out.read_text(errors='replace').strip()[:1500]}")
    finally:
        sh("hdiutil", "detach", mnt, "-force")


def main():
    app, dmg = Path(sys.argv[1]), Path(sys.argv[2])
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    min_os = info["LSMinimumSystemVersion"]
    say(f"# Mac build check: {os.uname().machine}, macOS {sh('sw_vers', '-productVersion').stdout.strip()}, "
        f"Python {sys.version.split()[0]}, app {info.get('CFBundleShortVersionString')} ({info.get('CFBundleIdentifier')}), "
        f"for macOS {min_os}+")
    must("Info.plist asks for Screen Recording with a reason", bool(info.get("NSScreenCaptureUsageDescription")))
    say(f"- {dmg.name}: {dmg.stat().st_size / 1e6:.1f} MB")
    check_bundle(app, min_os)
    check_tesseract(app)
    check_launch(dmg)
    say()
    say("FAILED: " + ", ".join(failed) if failed else "ALL MUST-HAVE CHECKS PASSED")
    report = ROOT / "build" / "macos" / f"check-{os.uname().machine}.md"
    report.write_text("\n".join(lines) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("\n".join(lines) + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
