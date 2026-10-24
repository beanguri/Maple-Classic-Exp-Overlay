"""Tests for ocr.py: Tesseract reading many pictures in one run must give the same text as
reading each picture on its own, and fail loudly (never with a wrong answer) when it can't.

Run from the project folder:  py -3 tests/test_ocr.py
The Tesseract parts need Tesseract installed (the overlay's own is found the same way)."""
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import appmod as m  # noqa: E402
assert Path(os.environ["MAPLE_EXP_DATA"]) == m.DATA_DIR, "tests must run on their own temp data folder, never on real data"
from maple_overlay.vision import ocr  # noqa: E402

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

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
