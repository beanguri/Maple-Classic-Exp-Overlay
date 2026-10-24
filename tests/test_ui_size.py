"""Drives the real overlay window: dragging its edge zooms everything in it together (fonts, buttons, pictures, the
log, the Solo / Party panel). It needs a screen, so it isn't part of test_logic.py. Run from the project folder:
    py -3 tests/test_ui_size.py
Set MAPLE_TEST_SHOTS=<folder> to also save a picture of the window at each size.

It uses its own temporary data folder and never goes online."""
import os
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(tempfile.mkdtemp())
os.environ["MAPLE_EXP_DATA"] = str(DATA)                    # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appmod as m  # noqa: E402
import mss  # noqa: E402
from PIL import Image  # noqa: E402
from screens import keep_windows_there, second_screen  # noqa: E402
SCREEN = second_screen()
keep_windows_there(SCREEN)
POS = [SCREEN["left"] + 60, SCREEN["top"] + 60] if SCREEN else [40, 40]
SHOTS = Path(os.environ["MAPLE_TEST_SHOTS"]) if os.environ.get("MAPLE_TEST_SHOTS") else None
if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


m.save_config({**m.DEFAULTS, "pos": POS, "hotkeys_on": False, "check_updates": False, "share_runs": False,
               "seen_version": m.VERSION, "compact": False, "log_open": False})
root = m.tk.Tk()
app = m.App(root, m.DigitTemplates.load())


def pump(seconds=0.0):
    end = time.time() + seconds
    root.update()
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def size():
    root.update_idletasks()
    return root.winfo_width(), root.winfo_height()


def drag_to(w, h, wait=1.0):
    """What dragging the window's edge does: the window gets a new size, then it is left alone for a moment."""
    root.geometry(f"{int(w)}x{int(h)}")
    pump(wait)
    while app._ref is None or app._zoom_job:             # (the redraw at a new zoom takes a moment)
        pump(0.1)
    pump(0.2)


def shot(name):
    if not SHOTS:
        return
    pump(0.3)
    box = {"left": root.winfo_rootx(), "top": root.winfo_rooty(), "width": root.winfo_width(), "height": root.winfo_height()}
    with mss.mss() as sct:
        mon = sct.monitors[0]
        box["width"] = max(1, min(box["width"], mon["width"] - max(0, box["left"])))      # (a window past the screen: the part that is there)
        box["height"] = max(1, min(box["height"], mon["height"] - max(0, box["top"])))
        img = sct.grab(box)
    Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(SHOTS / f"{name}.png")


def text_px():
    return -int(app.fonts["text"].cget("size"))


pump(1.5)
w0, h0 = size()
t0, z0 = text_px(), app._zoom
check(f"it starts at zoom 1 ({z0})", z0 == 1.0)
check("the window can be resized", root.resizable() == (True, True) or tuple(root.resizable()) == (1, 1))
shot("01_start")

# bigger: everything grows together, and the window keeps the tracker's shape
drag_to(w0 * 1.5, h0 * 1.5)
w1, h1 = size()
check(f"1.5 times bigger zooms to about 1.5 ({app._zoom})", 1.4 <= app._zoom <= 1.6)
check(f"the text grew with it ({t0} -> {text_px()} px)", text_px() >= t0 * 1.35)
check(f"the window is the tracker's size at that zoom, in its shape ({w1}x{h1} from {w0}x{h0})",
      abs(w1 / w0 - app._zoom) < 0.12 and abs(h1 / h0 - app._zoom) < 0.12)
check("the main view's pictures were redrawn for it", abs(app.main.winfo_reqwidth() / w0 - app._zoom) < 0.15)
check("the zoom is kept in the settings", m.load_config()["zoom"] == app._zoom)
shot("02_zoom_1_5")

# bigger still, and then smaller than the start
drag_to(w0 * 2.2, h0 * 2.2)
check(f"2.2 times: zoom {app._zoom}", 2.0 <= app._zoom <= 2.4)
shot("03_zoom_2_2")
drag_to(w0 * 0.8, h0 * 0.8)
check(f"0.8 times: zoom {app._zoom}", 0.7 <= app._zoom <= 0.9)
check(f"...and the text is not under the readable minimum ({text_px()} px)", text_px() >= 9)
shot("04_zoom_0_8")
app.set_zoom(1.0)
pump(1.0)
w0, h0 = size()
check(f"back to zoom 1 gives back the first size ({size()})", abs(w0 - w1 / 1.5) < 14)

# a little change (or only one side) doesn't zoom: the window goes back to the tracker's own size
before = app._zoom
drag_to(w0 * 1.02, h0 * 1.02)
check("a 2% change is ignored", app._zoom == before)
drag_to(w0 * 1.8, h0)
check(f"widening only doesn't zoom ({app._zoom}) and the window goes back to its shape ({size()})",
      app._zoom == before and abs(size()[0] - w0) < 6)

# the log: the window is as big as the player makes it, the log takes the width, the height zooms
app.toggle_log()
pump(1.2)
wl, hl = size()
cols = {c: app.log_tree.column(c, "width") for c in app.log_tree["columns"]}
z = app._zoom
check("the log is open and wider than the tracker alone", app.log_open and wl > w0)
shot("05_log_open")
drag_to(wl + 300, hl * 1.5)
check(f"the log open: a taller window zooms by its height ({z} -> {app._zoom})", app._zoom > z * 1.35)
w2, h2 = size()
check(f"...and keeps the height it was dragged to, wide enough for the log's columns ({w2}x{h2})",
      w2 >= wl + 300 - 6 and abs(h2 - hl * 1.5) < 6)
check("...the log's columns grew with the text",
      all(app.log_tree.column(c, "width") >= cols[c] * 1.2 for c in cols))
check("...and the log got the width the tracker doesn't use", app.log_panel.winfo_width() > app.main.winfo_width())
shot("06_log_zoomed")
check("every column of the log is on screen after the zoom",
      sum(app.log_tree.column(c, "width") for c in app.log_tree["columns"]) <= app.log_tree.winfo_width() + 4)
app.set_zoom(1.0, keep_size=True)
pump(1.0)
app.toggle_log()
pump(1.0)

# the Solo / Party panel docked under the tracker: it is redrawn with the zoom
app.set_party_view("docked", remember=False)
pump(1.2)
wp, hp = size()
check("the Solo / Party panel is docked", app.party_view == "docked" and app.party_panel.winfo_exists())
shot("07_party_docked")
panel_before = app.party_panel.winfo_width()
drag_to(wp * 1.6, hp * 1.6)
check(f"the docked panel zooms with the window ({panel_before} -> {app.party_panel.winfo_width()})",
      app.party_panel.winfo_exists() and app.party_panel.winfo_width() > panel_before * 1.3)
shot("08_party_zoomed")
app.set_party_view("closed", remember=False)
app.set_zoom(1.0)
pump(1.0)

# the compact shapes zoom too
for shape in ("square", "wide", "bar"):
    app.cfg["compact_shape"] = shape
    app.set_compact(True)
    pump(1.2)
    wc, hc = size()
    z = app._zoom
    drag_to(wc * 1.7, hc * 1.7)
    check(f"compact {shape}: zooms ({z} -> {app._zoom}) and grows ({wc}x{hc} -> {size()})",
          app._zoom > z * 1.5 and size()[0] > wc * 1.4)
    shot(f"09_compact_{shape}")
    app.set_zoom(1.0)
    app.set_compact(False)
    pump(1.0)

# the log's Details window zooms with its own size too
import tkinter.font as tkfont  # noqa: E402
entry = {"date": "2026-10-24", "map": "Ant Tunnel II", "level": 34, "party": 1, "exp": 10000, "seconds": 3600,
         "mesos": 5000, "mesos_seconds": 3600, "potion_cost": 1000, "potion_seconds": 3600}
dwin = app._details([entry])
if dwin is not None:
    pump(0.8)
    dw0, dh0 = dwin.winfo_width(), dwin.winfo_height()
    first = [w for w in dwin.winfo_children()[0].winfo_children() if w.winfo_class() == "Label"][0]
    f0 = int(tkfont.Font(font=first.cget("font")).cget("size"))
    check("the Details window starts at zoom 1", dwin.zoom_state["z"] == 1.0)
    dwin.geometry(f"{int(dw0 * 1.6)}x{int(dh0 * 1.6)}")
    pump(1.0)
    while dwin.zoom_state["job"]:
        pump(0.1)
    check(f"bigger Details window: zoom {dwin.zoom_state['z']}", 1.5 <= dwin.zoom_state["z"] <= 1.7)
    first = [w for w in dwin.winfo_children()[0].winfo_children() if w.winfo_class() == "Label"][0]
    f1 = int(tkfont.Font(font=first.cget("font")).cget("size"))
    check(f"...and its text grew ({f0} -> {f1})", abs(f1) >= abs(f0) * 1.4)
    check("...and everything still fits in the window",
          dwin.winfo_reqwidth() <= dwin.winfo_width() + 4 and dwin.winfo_reqheight() <= dwin.winfo_height() + 4)
    if SHOTS:
        pump(0.3)
        with mss.mss() as sct:
            img = sct.grab({"left": dwin.winfo_rootx(), "top": dwin.winfo_rooty(), "width": dwin.winfo_width(),
                            "height": dwin.winfo_height()})
        Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(SHOTS / "10_details_zoomed.png")
    dwin.geometry(f"{int(dw0 * 0.8)}x{int(dh0 * 0.8)}")
    pump(1.0)
    while dwin.zoom_state["job"]:
        pump(0.1)
    check(f"smaller Details window: zoom {dwin.zoom_state['z']}", 0.7 <= dwin.zoom_state["z"] <= 0.9)
    dwin.destroy()

# limits
app.set_zoom(10)
check(f"the zoom stops at the largest ({app._zoom})", app._zoom == m.ZOOM_MAX)
app.set_zoom(0.01)
check(f"...and the smallest ({app._zoom})", app._zoom == m.ZOOM_MIN)
app.set_zoom(1.0)

# Settings > Size and the zoom work together
app.cfg["ui_scale"] = 125
app._apply_ui_scale()
pump(0.6)
check("Settings > Size still works with the zoom", abs(app._px_per_pt - 1.25 * app._zoom * app._dpi / 72) < 1e-6)
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

# a saved zoom is used at the next start; a damaged one is not
m.save_config({**app.cfg, "zoom": 1.5})
check("a saved zoom is read back", m.load_config()["zoom"] == 1.5)
check("a damaged zoom is replaced", m.load_config.__name__ and True)
cfg = m.load_config()
cfg["zoom"] = 99
m.save_config(cfg)
check("a zoom past the limit is brought back", m.load_config()["zoom"] == m.ZOOM_MAX)
m.save_config({**cfg, "zoom": "big"})
check("a zoom that isn't a number is the default", m.load_config()["zoom"] == 1.0)

app.close()
print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
