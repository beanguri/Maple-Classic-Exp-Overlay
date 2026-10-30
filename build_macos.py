"""Builds the macOS download: release/Maple-Classic-Exp-Overlay-v<version>-macOS-<arch>.zip

It packs the program, Python and everything it needs into "Maple Classic Exp Overlay.app",
adds Tesseract OCR from Homebrew (the program, the libraries it needs and the English language
data), and zips it up. The .app is signed ad hoc (no Apple Developer account), so the first
time it's opened macOS asks: right-click it > Open, or System Settings > Privacy & Security >
Open Anyway.

Needs Python with Tk (Homebrew: brew install python-tk@3.13), the packages from
requirements.txt plus PyInstaller, and Tesseract (brew install tesseract):
    python3.13 -m venv .venv
    .venv/bin/pip install -r requirements.txt pyinstaller
    .venv/bin/python build_macos.py
Set TESSERACT_PREFIX if Tesseract isn't in /opt/homebrew (Intel Homebrew: /usr/local).

The .app is built for the Mac it's built on (Apple Silicon or Intel), like Homebrew's Tesseract.
"""
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Maple Classic Exp Overlay"                 # the .app
ZIP_NAME = NAME.replace(" ", "-")
VERSION = re.search(r'^VERSION = "([^"]+)"', (ROOT / "maple_exp.py").read_text(encoding="utf-8"), re.M)[1]
ARCH = platform.machine()                          # arm64 or x86_64
PREFIX = Path(os.environ.get("TESSERACT_PREFIX", "/opt/homebrew" if ARCH == "arm64" else "/usr/local"))
BUILD, RELEASE = ROOT / "build" / "macos", ROOT / "release"
BUNDLE_ID = "io.github.maple-classic-exp-overlay"

# Libraries that come with every Mac: never copied.
SYSTEM = ("/usr/lib/", "/System/")

# Tesseract is found by the overlay at <app>/Contents/Frameworks/tesseract/tesseract (see
# platforms/macos.py), with its data at Frameworks/share/tessdata. For that folder the overlay
# passes no --tessdata-dir (it's "Tesseract's own default"), but a Homebrew Tesseract's own
# default is Homebrew's folder, which other Macs don't have: this tells it where.
RUNTIME_HOOK = """import os, sys
os.environ["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(sys._MEIPASS), "Resources", "share", "tessdata")
"""


def main():
    tesseract = PREFIX / "bin" / "tesseract"
    tessdata = PREFIX / "share" / "tessdata"
    if not tesseract.exists() or not (tessdata / "eng.traineddata").exists():
        sys.exit(f"Tesseract wasn't found in {PREFIX}. Run: brew install tesseract (or set TESSERACT_PREFIX).")
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    hook = BUILD / "tessdata_hook.py"
    hook.write_text(RUNTIME_HOOK, encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--onedir", "--name", NAME, "--icon", str(ROOT / "assets" / "icon.ico"),
                    "--osx-bundle-identifier", BUNDLE_ID, "--runtime-hook", str(hook),
                    "--add-data", f"{ROOT / 'assets'}:assets",
                    "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"),
                    "--specpath", str(BUILD), str(ROOT / "maple_exp.py")], check=True)
    app = BUILD / "dist" / f"{NAME}.app"
    contents = app / "Contents"

    # Tesseract: the program and every Homebrew library it needs, next to each other.
    tess = contents / "Frameworks" / "tesseract"
    tess.mkdir()
    bundle_binary(tesseract.resolve(), tess)
    data = contents / "Resources" / "share" / "tessdata"
    data.mkdir(parents=True)
    shutil.copy2(tessdata / "eng.traineddata", data)
    for folder in ("configs", "tessconfigs"):
        if (tessdata / folder).is_dir():
            shutil.copytree(tessdata / folder, data / folder)
    # The overlay looks for the data beside the program, at tesseract/../share/tessdata.
    (contents / "Frameworks" / "share").symlink_to("../Resources/share")
    for notice in ("LICENSE", "AUTHORS"):
        f = PREFIX / "opt" / "tesseract" / notice
        if f.is_file():
            shutil.copy2(f, contents / "Resources" / "share" / f"tesseract-{notice}.txt")

    set_info(contents / "Info.plist")
    # Everything changed after PyInstaller signed it: sign the whole .app again (ad hoc).
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    check_tesseract(tess / "tesseract", data)
    make_zip(app)


def linked(binary):
    """The libraries a Mach-O file loads, as written in it (paths, @rpath/... or @loader_path/...)."""
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, check=True).stdout
    return [line.split(" (compatibility")[0].strip() for line in out.splitlines()[1:] if line.strip()]


def rpaths(binary):
    out = subprocess.run(["otool", "-l", str(binary)], capture_output=True, text=True, check=True).stdout
    return re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.+?) \(offset", out)


def resolve(ref, binary):
    """Where a library reference of `binary` (its real path) points to on this Mac, or None."""
    loader = str(binary.parent)
    if ref.startswith("@loader_path/") or ref.startswith("@executable_path/"):
        return Path(loader, ref.split("/", 1)[1]).resolve()
    if ref.startswith("@rpath/"):
        for rp in rpaths(binary):
            p = Path(rp.replace("@loader_path", loader).replace("@executable_path", loader), ref[len("@rpath/"):])
            if p.exists():
                return p.resolve()
        return None
    return Path(ref).resolve()


def bundle_binary(source, into):
    """Copies a program and all the non-system libraries it loads (and they load) into `into`,
    and points every reference at the copy beside it (@loader_path), then signs each ad hoc."""
    todo, done = [source], {}
    while todo:
        real = todo.pop()
        if real in done:
            continue
        dest = into / real.name
        shutil.copy2(real, dest)
        dest.chmod(0o755)
        done[real] = dest
        for ref in linked(real):
            if ref.startswith(SYSTEM):
                continue
            target = resolve(ref, real)
            if target is None:
                sys.exit(f"{real.name} needs {ref}, which wasn't found")
            todo.append(target)
    for real, dest in done.items():
        args = ["install_name_tool"]
        if dest.suffix == ".dylib":
            args += ["-id", f"@loader_path/{dest.name}"]
        for ref in linked(real):
            if not ref.startswith(SYSTEM) and (target := resolve(ref, real)) and target != real:
                args += ["-change", ref, f"@loader_path/{done[target].name}"]
        for rp in rpaths(real):
            args += ["-delete_rpath", rp]
        subprocess.run(args + [str(dest)], check=True, capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dest)], check=True, capture_output=True)
    print(f"Tesseract: {len(done)} files bundled ({', '.join(sorted(d.name for d in done.values()))})")


def set_info(path):
    with open(path, "rb") as f:
        info = plistlib.load(f)
    info.update({"CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                 "CFBundleDisplayName": NAME, "NSHighResolutionCapable": True,
                 "LSMinimumSystemVersion": "11.0"})
    with open(path, "wb") as f:
        plistlib.dump(info, f)


def check_tesseract(exe, data):
    """The bundled Tesseract runs with nothing from Homebrew: no library is still taken from there."""
    for f in exe.parent.iterdir():
        left = [ref for ref in linked(f) if not ref.startswith(SYSTEM) and not ref.startswith("@loader_path/")]
        if left:
            sys.exit(f"{f.name} still loads {left}")
    run = subprocess.run([str(exe), "--list-langs"], capture_output=True, text=True,
                         env=dict(os.environ, TESSDATA_PREFIX=str(data)))
    if run.returncode != 0 or "eng" not in run.stdout.split():
        sys.exit(f"The bundled Tesseract doesn't work: {run.stdout}{run.stderr}")
    print("The bundled Tesseract works (English data found)")


def make_zip(app):
    """release/<name>-v<version>-macOS-<arch>.zip with the .app in it (ditto keeps its links and signature)."""
    RELEASE.mkdir(exist_ok=True)
    zip_path = RELEASE / f"{ZIP_NAME}-v{VERSION}-macOS-{ARCH}.zip"
    zip_path.unlink(missing_ok=True)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(zip_path)], check=True)
    print(f"\nDone: {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("build_macos.py builds the Mac .app, on a Mac. On Windows use build.py.")
    main()
