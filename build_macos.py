"""Builds the macOS download: release/Maple-Classic-Exp-Overlay-v<version>-macOS-<AppleSilicon|Intel>.dmg

It packs the program, Python and everything it needs into "Maple Classic Exp Overlay.app",
adds Tesseract OCR (the program and the English language data), and puts it in a .dmg with a
link to Applications. The .app is signed ad hoc (no Apple Developer account), so the first
time it's opened macOS asks: System Settings > Privacy & Security > Open Anyway.

    python build_macos.py tesseract <prefix>   builds Tesseract (and Leptonica, libpng) from pinned sources
                                               for macOS MIN_OS and up, into <prefix>
    python build_macos.py                      builds the .app and the .dmg

The GitHub build makes its own Tesseract first (Homebrew's is built for the runner's own macOS, which can
be far newer than the players'). To build from Homebrew's on your own Mac instead:
    brew install tesseract python-tk@3.12
    python3.12 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-build-mac.txt
    MAC_MIN_OS=<your macOS, e.g. 15.0> .venv/bin/python build_macos.py
Set TESSERACT_PREFIX if Tesseract isn't in /opt/homebrew (Intel Homebrew: /usr/local).

The .app is built for the Mac it's built on (Apple Silicon or Intel). check_macos.py checks it.
"""
import hashlib
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Maple Classic Exp Overlay"                 # the .app
FILE_NAME = NAME.replace(" ", "-")
VERSION = re.search(r'^VERSION = "([^"]+)"', (ROOT / "maple_exp.py").read_text(encoding="utf-8"), re.M)[1]
ARCH = platform.machine()                          # arm64 or x86_64
KIND = {"arm64": "AppleSilicon", "x86_64": "Intel"}.get(ARCH, ARCH)
PREFIX = Path(os.environ.get("TESSERACT_PREFIX", "/opt/homebrew" if ARCH == "arm64" else "/usr/local"))
MIN_OS = os.environ.get("MAC_MIN_OS", "14.0")      # numpy 2.2.6's Mac wheels need 14.0; nothing in the app may need more
BUILD, RELEASE = ROOT / "build" / "macos", ROOT / "release"
BUNDLE_ID = "io.github.maple-classic-exp-overlay"  # never change it: macOS remembers the Screen Recording permission by it
SCREEN_TEXT = ("The overlay reads the EXP bar, mesos, party list and map name from the MapleStory window. "
               "Nothing leaves your Mac unless you turn on sharing.")

# Libraries that come with every Mac: never copied.
SYSTEM = ("/usr/lib/", "/System/")

# Tesseract's sources, pinned: a tarball by its SHA-256, a git tag by its commit (a tag can be moved, a commit can't).
SOURCES = {
    "libpng": ("git", "https://github.com/pnggroup/libpng", "v1.6.59", "cd952f49f95bb27154ae77dbb103032d95f6e580"),
    "leptonica": ("tar", "https://github.com/DanBloomberg/leptonica/releases/download/1.87.0/leptonica-1.87.0.tar.gz",
                  "leptonica-1.87.0", "c73363397f96eb1295602bf44d708a994ad42046c791bf03ea0505d829bdb6a7"),
    "tesseract": ("git", "https://github.com/tesseract-ocr/tesseract", "5.5.3", "db0ec62f81b0737fbbe184d8fea40af5738f8eef"),
}
ENG = ("https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata",
       "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2")   # (Homebrew's English data too)
LICENSES = {"libpng": "LICENSE", "leptonica": "leptonica-license.txt", "tesseract": "LICENSE"}


def main():
    tesseract = PREFIX / "bin" / "tesseract"
    tessdata = PREFIX / "share" / "tessdata"
    if not tesseract.exists() or not (tessdata / "eng.traineddata").exists():
        sys.exit(f"Tesseract wasn't found in {PREFIX}. Run: python build_macos.py tesseract <prefix> (and set "
                 "TESSERACT_PREFIX), or brew install tesseract.")
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--onedir", "--name", NAME, "--icon", str(ROOT / "assets" / "icon.ico"),
                    "--osx-bundle-identifier", BUNDLE_ID,
                    "--add-data", f"{ROOT / 'assets'}:assets",
                    "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"),
                    "--specpath", str(BUILD), str(ROOT / "maple_exp.py")], check=True,
                   env=dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MIN_OS))
    app = BUILD / "dist" / f"{NAME}.app"
    contents = app / "Contents"

    # Tesseract: the program and every non-system library it needs, next to each other.
    tess = contents / "Frameworks" / "tesseract"
    tess.mkdir()
    bundle_binary(tesseract.resolve(), tess)
    data = contents / "Resources" / "share" / "tessdata"
    data.mkdir(parents=True)
    shutil.copy2(tessdata / "eng.traineddata", data)
    for folder in ("configs", "tessconfigs"):
        if (tessdata / folder).is_dir():
            shutil.copytree(tessdata / folder, data / folder)
    # The overlay looks for the data beside the program, at tesseract/../share/tessdata (and always passes it on).
    (contents / "Frameworks" / "share").symlink_to("../Resources/share")
    notices = contents / "Resources" / "licenses"
    notices.mkdir()
    for name in LICENSES:
        for f in (PREFIX / "share" / "licenses" / name).glob("*"):        # (from `tesseract`, below)
            shutil.copy2(f, notices / f"{name}-{f.name}")
    for notice in ("LICENSE", "AUTHORS"):                                   # (Homebrew's, on your own Mac)
        f = PREFIX / "opt" / "tesseract" / notice
        if f.is_file() and not (notices / f"tesseract-{notice}").exists():
            shutil.copy2(f, notices / f"tesseract-{notice}")
    shutil.copy2(ROOT / "LICENSE", notices / "maple-classic-exp-overlay-LICENSE")

    set_info(contents / "Info.plist")
    # Everything changed after PyInstaller signed it: sign the whole .app again (ad hoc).
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    make_dmg(app)


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
    and points every reference at the copy beside it (@loader_path), then signs each ad hoc.
    (The GitHub build's Tesseract is linked statically: only the program itself is copied.)"""
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
        if len(args) > 1:
            subprocess.run(args + [str(dest)], check=True, capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dest)], check=True, capture_output=True)
    print(f"Tesseract: {len(done)} files bundled ({', '.join(sorted(d.name for d in done.values()))})")


def set_info(path):
    with open(path, "rb") as f:
        info = plistlib.load(f)
    info.update({"CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                 "CFBundleDisplayName": NAME, "NSHighResolutionCapable": True,
                 "LSMinimumSystemVersion": MIN_OS, "NSScreenCaptureUsageDescription": SCREEN_TEXT})
    with open(path, "wb") as f:
        plistlib.dump(info, f)


def make_dmg(app):
    """release/<name>-v<version>-macOS-<kind>.dmg: the .app and a link to Applications (drag one onto the other)."""
    RELEASE.mkdir(exist_ok=True)
    dmg = RELEASE / f"{FILE_NAME}-v{VERSION}-macOS-{KIND}.dmg"
    dmg.unlink(missing_ok=True)
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)      # (keeps links and signatures)
    (stage / "Applications").symlink_to("/Applications")
    subprocess.run(["hdiutil", "create", "-volname", NAME, "-srcfolder", str(stage), "-fs", "HFS+",
                    "-format", "UDZO", "-ov", str(dmg)], check=True)
    print(f"\nDone: {dmg} ({dmg.stat().st_size / 1e6:.0f} MB)")


# ---- Tesseract from pinned sources (the GitHub build)
def fetch(name, into):
    kind, url, ref, pin = SOURCES[name]
    if kind == "git":
        src = into / name
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", ref, url, str(src)], check=True)
        got = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
        if got != pin:
            sys.exit(f"{name} {ref} is commit {got}, not the pinned {pin}: stopping")
        return src
    tar = into / url.rsplit("/", 1)[1]
    download(url, tar, pin)
    subprocess.run(["tar", "-xzf", str(tar), "-C", str(into)], check=True)
    return into / ref


def download(url, path, sha256):
    with urllib.request.urlopen(url, timeout=120) as r:
        path.write_bytes(r.read())
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != sha256:
        sys.exit(f"{path.name}: SHA-256 {got}, not the pinned {sha256}: stopping")


def build_tesseract(prefix):
    """libpng, Leptonica (PNG only: the overlay hands Tesseract PNG pictures) and Tesseract, linked statically, for
    macOS MIN_OS and up, without anything from Homebrew; then the English data. Installs into `prefix`."""
    prefix = Path(prefix).resolve()
    work = Path(tempfile.mkdtemp(prefix="tess-src-"))
    common = [f"-DCMAKE_INSTALL_PREFIX={prefix}", f"-DCMAKE_PREFIX_PATH={prefix}", "-DCMAKE_BUILD_TYPE=Release",
              f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MIN_OS}", "-DBUILD_SHARED_LIBS=OFF",
              "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local;/opt/local",     # (never Homebrew's or MacPorts')
              "-DCMAKE_FIND_FRAMEWORK=LAST"]
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MIN_OS, PKG_CONFIG_LIBDIR=str(prefix / "lib" / "pkgconfig"))
    env.pop("PKG_CONFIG_PATH", None)

    def cmake(src, *options):
        b = src / "_build"
        subprocess.run(["cmake", "-S", str(src), "-B", str(b), *common, *options], check=True, env=env)
        subprocess.run(["cmake", "--build", str(b), "--parallel", str(os.cpu_count() or 2)], check=True, env=env)
        subprocess.run(["cmake", "--install", str(b)], check=True, env=env)

    srcs = {name: fetch(name, work) for name in SOURCES}
    cmake(srcs["libpng"], "-DPNG_SHARED=OFF", "-DPNG_STATIC=ON", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF",
          "-DPNG_FRAMEWORK=OFF")
    cmake(srcs["leptonica"], "-DSW_BUILD=OFF", "-DBUILD_PROG=OFF", "-DENABLE_ZLIB=ON", "-DENABLE_PNG=ON",
          "-DENABLE_GIF=OFF", "-DENABLE_JPEG=OFF", "-DENABLE_TIFF=OFF", "-DENABLE_WEBP=OFF", "-DENABLE_OPENJPEG=OFF")
    cmake(srcs["tesseract"], "-DBUILD_TRAINING_TOOLS=OFF", "-DBUILD_TESTS=OFF", "-DDISABLE_ARCHIVE=ON",
          "-DDISABLE_CURL=ON", "-DDISABLE_TIFF=ON", "-DGRAPHICS_DISABLED=ON", "-DOPENMP_BUILD=OFF",
          "-DENABLE_NATIVE=OFF", "-DENABLE_LTO=OFF",
          f"-DCMAKE_EXE_LINKER_FLAGS={prefix / 'lib' / 'libpng16.a'} -lz")   # (a static Leptonica's own PNG need)
    data = prefix / "share" / "tessdata"
    data.mkdir(parents=True, exist_ok=True)
    download(ENG[0], data / "eng.traineddata", ENG[1])
    for name, file in LICENSES.items():
        d = prefix / "share" / "licenses" / name
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(srcs[name] / file, d / file)
    exe = prefix / "bin" / "tesseract"
    left = [ref for ref in linked(exe) if not ref.startswith(SYSTEM)]
    if left:
        sys.exit(f"tesseract still loads {left}")
    print(subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout)
    print(f"Tesseract built into {prefix} for macOS {MIN_OS}+")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("build_macos.py builds the Mac .app, on a Mac. On Windows use build.py.")
    if sys.argv[1:2] == ["tesseract"]:
        build_tesseract(sys.argv[2])
    else:
        main()
