"""Tests for ocr.py: Tesseract reading many pictures in one run must give the same text as
reading each picture on its own, and fail loudly (never with a wrong answer) when it can't.

Run from the project folder:  py -3 tests/test_ocr.py
The Tesseract parts need Tesseract installed (the overlay's own is found the same way)."""
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import maple_exp as m  # noqa: E402
assert Path(os.environ["MAPLE_EXP_DATA"]) == m.DATA_DIR, "tests must run on their own temp data folder, never on real data"
import ocr  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


# ---- with a fake Tesseract: what the wrapper asks for, and how it fails
calls = []
real_run = subprocess.run


def fake_run(output="", code=0, err=b""):
    def run(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, code, output.encode(), err)
    return run


two = [np.full((20, 60), 255, np.uint8)] * 2
t = ocr.Tesseract(r"C:\somewhere\tesseract.exe")
ocr.subprocess.run = fake_run("12\r\n\f34\r\n")
check("two pictures come back as two texts (split at the page break)",
      [x.strip() for x in t.read_lines(two, "0123456789")] == ["12", "34"])
args, kw = calls[-1]
check("it runs the given program, one line of text per picture, on a list of files",
      args[0] == r"C:\somewhere\tesseract.exe" and args[2] == "stdout" and "--psm" in args and "7" in args)
check("the allowed characters are passed on", "tessedit_char_whitelist=0123456789" in args)
check("Tesseract's own threads are off, and it can't wait forever or ask for input",
      kw["env"]["OMP_THREAD_LIMIT"] == "1" and kw["timeout"] > 0 and kw["stdin"] == subprocess.DEVNULL)
check("it ran once for both pictures", t.runs == 1)
ocr.subprocess.run = fake_run("12\r\n")
try:
    t.read_lines(two)
    check("fewer texts than pictures is an error, not a guess", False)
except ocr.TesseractError:
    check("fewer texts than pictures is an error, not a guess", True)
ocr.subprocess.run = fake_run("", 1, b"Error opening data file")
try:
    t.read_lines(two)
    check("a failing Tesseract is an error", False)
except ocr.TesseractError as e:
    check("a failing Tesseract is an error that says why", "data file" in str(e))
n = len(calls)
check("no pictures means no run", t.read_lines([]) == [] and len(calls) == n)

# a Tesseract that hangs ends each run at the timeout: the wait is paid once, then it is left alone for a while
# (every look asks for it: the class, the map name, the badge; 30 s each would freeze the whole reading)
clock = [1000.0]
real_monotonic = time.monotonic
time.monotonic = lambda: clock[0]
hang_runs = []


def hangs(args, **kw):
    hang_runs.append(kw["timeout"])
    raise subprocess.TimeoutExpired(args, kw["timeout"])


def asks(h):
    try:
        return h.read_lines(two)
    except ocr.TesseractError as e:
        return str(e)
    except Exception as e:
        return type(e).__name__


try:
    ocr.subprocess.run = hangs
    h = ocr.Tesseract(r"C:\somewhere\tesseract.exe", timeout=30)
    got = asks(h)
    check(f"a run that times out is a TesseractError saying so ({got})", "30" in got and len(hang_runs) == 1)
    for _ in range(5):
        clock[0] += 10
        asks(h)
    check(f"...and for the next minute Tesseract isn't started again ({len(hang_runs)} run in 50 s)", len(hang_runs) == 1)
    clock[0] += 20
    asks(h)
    check("...then it gets another try", len(hang_runs) == 2)
    ocr.subprocess.run = fake_run("12\r\n\f34\r\n")
    clock[0] += 61
    check("...and once a run works it is used as usual", [x.strip() for x in h.read_lines(two)] == ["12", "34"])
    ocr.subprocess.run = hangs
    clock[0] += 1
    asks(h)
    check("...a fresh hang pauses it again", len(hang_runs) == 3)
finally:
    time.monotonic = real_monotonic
ocr.subprocess.run = real_run
ocr.subprocess.run = real_run

# ---- with the real Tesseract: one run for many pictures gives the same text as one run each
tess = m.find_tesseract()
if not tess:
    print("     (Tesseract isn't installed: the real-Tesseract tests are skipped)")
else:
    real = ocr.Tesseract(tess)
    rnd = random.Random(5)
    words = ["Rocky Road II", "Ant Tunnel I", "The Land of Wild Boar II", "Bandit", "Night Lord",
             "Hidden Street <Henesys>", "", "123,456,789", "0", "12", "x"]
    pictures = []
    for i in range(40):
        text = rnd.choice(words) if i % 5 else "".join(rnd.choice("0123456789,.") for _ in range(rnd.randint(1, 12)))
        img = np.full((70, 520), 255, np.uint8)
        cv2.putText(img, text, (10, 45), cv2.FONT_HERSHEY_SIMPLEX, rnd.choice([0.6, 0.9, 1.3]), 0, rnd.choice([1, 2]))
        if i % 3 == 0:                                       # noisy
            img = np.clip(img.astype(int) + np.random.default_rng(i).integers(-70, 70, img.shape), 0, 255).astype(np.uint8)
        pictures.append(img)
    pictures += [np.full((40, 200), 255, np.uint8), np.zeros((40, 200), np.uint8)]   # blank, black
    for chars in (None, "0123456789,."):
        together = real.read_lines(pictures, chars)
        alone = [real.read_line(p, chars) for p in pictures]
        check(f"{len(pictures)} pictures read together = read one by one (characters: {chars})",
              [x.strip() for x in together] == [x.strip() for x in alone])
    check("a picture's text doesn't depend on which others are read with it",
          [x.strip() for x in real.read_lines(pictures[::-1])][::-1] == [x.strip() for x in real.read_lines(pictures)])

    # a user name with letters outside a Western PC's code page (so the temp folder has them too)
    odd = Path(tempfile.mkdtemp()) / "Jösé 名前"
    odd.mkdir()
    old_tempdir, tempfile.tempdir = tempfile.tempdir, str(odd)
    try:
        sample = np.full((60, 300), 255, np.uint8)
        cv2.putText(sample, "AB 12", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
        check("reads fine when the temp folder's name has accents and non-Latin letters",
              [x.strip() for x in real.read_lines([sample, sample])] == ["AB 12", "AB 12"])
    finally:
        tempfile.tempdir = old_tempdir
    check("...and leaves nothing behind in it", not any(odd.iterdir()))

    # the readers: the class from the real status bars costs one Tesseract run, not six
    import json
    m.TESS, m.tesseract_ok = real, True
    frames = Path(__file__).parent / "frames"
    parts = json.loads((frames / "screen_parts.json").read_text())["status"]
    for name, info in sorted(parts.items()):
        before = real.runs
        got = m.read_job(cv2.imread(str(frames / name)), info["x0"], info["cy"], info["gh"])
        check(f"{name}: class {got!r} read in {real.runs - before} Tesseract run", got == info["job"] and real.runs - before == 1)

# ---- which Tesseract is used: it needs a usable English file, and is told where it is
import ast
import types
big = b"x" * (m.ENG_DATA_MIN + 1)
saved_cands, saved_data = m.PLATFORM.tesseract_candidates, m.PLATFORM.tessdata_candidates


def fake_install(root, data=None):
    """A folder with a tesseract program (an empty file) and, if `data` is given, tessdata/eng.traineddata."""
    root.mkdir(parents=True, exist_ok=True)
    exe = root / "tesseract.exe"
    exe.write_bytes(b"")
    exe.chmod(0o755)                    # (a program must be allowed to run: on macOS a new file isn't)
    if data is not None:
        (root / "tessdata").mkdir(exist_ok=True)
        (root / "tessdata" / "eng.traineddata").write_bytes(data)
    return exe


try:
    base = Path(tempfile.mkdtemp())
    bad, cut, good = fake_install(base / "bad"), fake_install(base / "cut", b"x" * 1000), fake_install(base / "good", big)
    m.PLATFORM.tessdata_candidates = lambda exe: [exe.parent / "tessdata"]
    m.PLATFORM.tessdata_arg = lambda exe, folder: str(folder)
    m.PLATFORM.tesseract_candidates = lambda home, app: [bad, cut, good]
    exe, arg, state, why = m.locate_tesseract()
    check("a program without eng.traineddata (or with a cut-off one) is skipped for the next good one",
          exe == str(good) and state == "ok" and why == "" and Path(arg) == good.parent / "tessdata")
    m.PLATFORM.tesseract_candidates = lambda home, app: [bad, cut]
    exe, arg, state, why = m.locate_tesseract()
    check("programs but no usable English file: state no_eng (file), nothing to use",
          exe is None and state == "no_eng" and why == "file")
    m.PLATFORM.tesseract_candidates = lambda home, app: [base / "nowhere" / "tesseract.exe"]
    exe, arg, state, why = m.locate_tesseract()
    check("no program at all: state missing", exe is None and state == "missing" and why == "")
    m.PLATFORM.tessdata_arg = lambda exe, folder: None            # a folder name that can't be given to Tesseract
    m.PLATFORM.tesseract_candidates = lambda home, app: [good]
    exe, arg, state, why = m.locate_tesseract()
    check("a data folder that can't be passed on: no_eng with the path reason", exe is None and state == "no_eng" and why == "path")
    m.tesseract_state, m.tesseract_why = "no_eng", "file"
    check("the 'file is missing' message names tessdata\\eng.traineddata and says EXP still works",
          "tessdata\\eng.traineddata" in m.tesseract_note() and "EXP and mesos still work" in m.tesseract_note())
    m.tesseract_why = "path"
    check("the 'unusual characters' message says to move the folder",
          "unusual characters" in m.tesseract_note() and "C:\\Games\\" in m.tesseract_note())
    m.tesseract_state, m.tesseract_why = "missing", ""
    check("the missing message keeps today's wording",
          m.tesseract_note() == "Map names can't be read: Tesseract OCR is missing (see the README).")
    m.tesseract_state = "ok"
    check("no message when Tesseract works", m.tesseract_note() == "")
finally:
    del m.PLATFORM.tessdata_arg                                   # (back to the class's own)
    m.PLATFORM.tesseract_candidates, m.PLATFORM.tessdata_candidates = saved_cands, saved_data
    m.tesseract_state, m.tesseract_why = "missing", ""

# a program Windows won't let us look at (a PATH folder we have no access to) is skipped, not a crash at start-up
class Locked:
    def is_file(self):
        raise PermissionError(13, "Access is denied")


try:
    base2 = Path(tempfile.mkdtemp())
    good2 = fake_install(base2 / "good", big)
    m.PLATFORM.tessdata_candidates = lambda exe: [exe.parent / "tessdata"]
    m.PLATFORM.tessdata_arg = lambda exe, folder: str(folder)
    m.PLATFORM.tesseract_candidates = lambda home, app: [Locked(), good2]
    try:
        exe, arg, state, why = m.locate_tesseract()
        check("a candidate that can't be looked at is skipped for the next good one", exe == str(good2) and state == "ok")
    except Exception as e:
        check(f"a candidate that can't be looked at is skipped for the next good one (it raised {type(e).__name__})", False)
    m.PLATFORM.tesseract_candidates = lambda home, app: [Locked()]
    try:
        exe, arg, state, why = m.locate_tesseract()
        check("...and when it is the only one there is no Tesseract (missing), not a crash", exe is None and state == "missing")
    except Exception as e:
        check(f"...and when it is the only one there is no Tesseract (it raised {type(e).__name__})", False)
finally:
    del m.PLATFORM.tessdata_arg
    m.PLATFORM.tesseract_candidates, m.PLATFORM.tessdata_candidates = saved_cands, saved_data
    m.tesseract_state, m.tesseract_why = "missing", ""

# --tessdata-dir is passed, and a TESSDATA_PREFIX of ours can't redirect it
os.environ["TESSDATA_PREFIX"] = r"C:\wrong\place"
try:
    calls.clear()
    ocr.subprocess.run = fake_run("12\r\n")
    ocr.Tesseract(r"C:\somewhere\tesseract.exe", tessdata=r"C:\data\tessdata").read_lines(two[:1])
    args, kw = calls[-1]
    check("--tessdata-dir <folder> is passed to every read", args[args.index("--tessdata-dir") + 1] == r"C:\data\tessdata")
    check("TESSDATA_PREFIX is removed from the child's environment", "TESSDATA_PREFIX" not in kw["env"])
    ocr.Tesseract(r"C:\somewhere\tesseract.exe").read_lines(two[:1])
    check("without a data folder nothing is added", "--tessdata-dir" not in calls[-1][0])
finally:
    ocr.subprocess.run = real_run
    os.environ.pop("TESSDATA_PREFIX", None)

# the real program with the real data
real_exe, real_arg, real_state, real_why = m.locate_tesseract()
if real_exe:
    check("the Tesseract that is installed is found, with its data folder", real_state == "ok" and m.tesseract_where[1])
    sample = np.full((60, 300), 255, np.uint8)
    cv2.putText(sample, "AB 12", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    os.environ["TESSDATA_PREFIX"] = r"C:\wrong\place"
    try:
        check("a wrong TESSDATA_PREFIX in our own environment doesn't break a real read",
              ocr.Tesseract(real_exe, tessdata=real_arg or None).read_line(sample).strip() == "AB 12")
    finally:
        os.environ.pop("TESSDATA_PREFIX", None)

    # a data folder under Cyrillic and Chinese letters (outside a Western code page; an accent is not enough)
    odd = Path(tempfile.mkdtemp()) / "\u041f\u0440\u0438\u0432\u0435\u0442 \u540d\u524d"
    (odd / "tessdata").mkdir(parents=True)
    (odd / "tessdata" / "eng.traineddata").write_bytes((Path(m.tesseract_where[1]) / "eng.traineddata").read_bytes())
    m.PLATFORM.tessdata_candidates = lambda exe: [odd / "tessdata"]
    m.PLATFORM.tesseract_candidates = lambda home, app: [Path(real_exe)]
    try:
        exe, arg, state, why = m.locate_tesseract()
        if state == "ok":
            check("Cyrillic/CJK data folder: a real read works through the folder as given or its short name",
                  ocr.Tesseract(exe, tessdata=arg or None).read_line(sample).strip() == "AB 12")
        else:
            check("Cyrillic/CJK data folder with no short name: no_eng with the path reason, no crash",
                  state == "no_eng" and why == "path")
    finally:
        m.PLATFORM.tesseract_candidates, m.PLATFORM.tessdata_candidates = saved_cands, saved_data
        m.tesseract_state, m.tesseract_why = "missing", ""

# Windows: a data folder under letters outside the PC's code page needs its 8.3 short name; without one it is refused
# (a fake eng.traineddata is enough: the choice is made before Tesseract is ever started)
if sys.platform == "win32":
    import ctypes
    kernel = ctypes.windll.kernel32
    odd_root = Path(tempfile.mkdtemp()) / "Привет 名前"
    (odd_root / "tessdata").mkdir(parents=True)
    (odd_root / "tessdata" / "eng.traineddata").write_bytes(big)
    odd_exe = odd_root / "tesseract.exe"
    odd_exe.write_bytes(b"")
    saved_cands, saved_data = m.PLATFORM.tesseract_candidates, m.PLATFORM.tessdata_candidates
    m.PLATFORM.tesseract_candidates = lambda home, app: [odd_exe]
    probe = ctypes.create_unicode_buffer(1024)
    has_short = kernel.GetShortPathNameW(str(odd_root / "tessdata"), probe, 1024) > 0 and probe.value != str(odd_root / "tessdata")
    try:
        exe, arg, state, why = m.locate_tesseract()
        if has_short:
            check("odd letters, 8.3 names on: the data folder is handed over by a short name that only has code-page letters",
                  state == "ok" and arg and arg != str(odd_root / "tessdata") and arg.encode("mbcs").decode("mbcs") == arg
                  and (Path(arg) / "eng.traineddata").is_file())
        else:
            print("     (this PC has no 8.3 names for that folder: the short-name case is only tested with a fake answer)")

        def short_name(answer):
            def fake(path, buf, size):
                if answer:
                    buf.value = answer if answer != "same" else path
                    return len(buf.value)
                return 0
            return fake

        for what, answer in (("8.3 names switched off (the long name comes back)", "same"), ("the call failing", "")):
            kernel.GetShortPathNameW = short_name(answer)
            try:
                exe, arg, state, why = m.locate_tesseract()
            finally:
                del kernel.GetShortPathNameW
            m.tesseract_state, m.tesseract_why = state, why
            check(f"odd letters and {what}: no Tesseract, state no_eng, the path reason, and the player is told to move the folder",
                  exe is None and state == "no_eng" and why == "path" and "unusual characters" in m.tesseract_note())
        check("...the real call is back afterwards", kernel.GetShortPathNameW is not None and not hasattr(kernel.GetShortPathNameW, "__code__"))
    finally:
        m.PLATFORM.tesseract_candidates, m.PLATFORM.tessdata_candidates = saved_cands, saved_data
        m.tesseract_state, m.tesseract_why = "missing", ""

# macOS rules, run on any OS: the methods are cut out of platforms/macos.py (it can only be imported on a Mac)
tree = ast.parse((Path(m.__file__).parent / "platforms" / "macos.py").read_text(encoding="utf-8"))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MacPlatform")
wanted = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("tessdata_candidates", "tessdata_arg")]
space = {"Path": Path}
exec(compile(ast.Module(body=wanted, type_ignores=[]), "macos.py", "exec"), space)
mac = types.SimpleNamespace(**{k: (lambda f: lambda *a: f(None, *a))(v) for k, v in space.items() if k != "Path" and callable(v)})
brew = Path("/opt/homebrew/bin/tesseract")
check("macOS looks next to the program (Homebrew layout) first, then in Homebrew's prefixes",
      [p.as_posix() for p in mac.tessdata_candidates(brew)]
      == ["/opt/homebrew/share/tessdata", "/opt/homebrew/share/tessdata", "/usr/local/share/tessdata"])
check("macOS always passes --tessdata-dir (Tesseract looks where it was compiled for, not beside itself)",
      Path(mac.tessdata_arg(brew, Path("/opt/homebrew/share/tessdata"))).as_posix() == "/opt/homebrew/share/tessdata"
      and Path(mac.tessdata_arg(brew, Path("/usr/local/share/tessdata"))).as_posix() == "/usr/local/share/tessdata")
inapp = Path("/Applications/Maple Classic Exp Overlay.app/Contents/Frameworks/tesseract/tesseract")
check("macOS: the Tesseract inside the downloaded app gets the app's own data folder (it can't find it alone: M01)",
      Path(mac.tessdata_arg(inapp, mac.tessdata_candidates(inapp)[0])).as_posix()
      == "/Applications/Maple Classic Exp Overlay.app/Contents/Frameworks/share/tessdata")
cellar = Path("/opt/homebrew/Cellar/tesseract/5.5.0/bin/tesseract")      # (a versioned Homebrew folder, if that is the program found)
check("macOS: a program inside a versioned Cellar folder looks in that version's own share/tessdata first, and passes it",
      mac.tessdata_candidates(cellar)[0].as_posix() == "/opt/homebrew/Cellar/tesseract/5.5.0/share/tessdata"
      and Path(mac.tessdata_arg(cellar, mac.tessdata_candidates(cellar)[0])).as_posix()
      == "/opt/homebrew/Cellar/tesseract/5.5.0/share/tessdata")
macports = Path("/opt/local/bin/tesseract")
check("macOS: MacPorts' program finds /opt/local/share/tessdata beside it",
      mac.tessdata_candidates(macports)[0].as_posix() == "/opt/local/share/tessdata")

check("Tesseract is given 15 s before it counts as stuck (a real run takes well under 1 s)",
      ocr.Tesseract("tesseract").timeout == 15)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
