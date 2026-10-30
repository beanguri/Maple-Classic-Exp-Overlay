"""Drives the real overlay window (it needs a screen, so it isn't part of test_logic.py): the notes
box under the log. Run from the project folder:  py -3 tests/test_ui.py

It uses its own temporary data folder and never goes online."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(tempfile.mkdtemp())
os.environ["MAPLE_EXP_DATA"] = str(DATA)                    # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402
assert Path(os.environ["MAPLE_EXP_DATA"]) == m.DATA_DIR, "tests must run on their own temp data folder, never on real data"
from screens import keep_windows_there, second_screen, stray_windows  # noqa: E402
SCREEN = second_screen()                    # every window these tests open goes on the second screen, not the main one
keep_windows_there(SCREEN)
POS = [SCREEN["left"] + 60, SCREEN["top"] + 60] if SCREEN else [60, 60]
print("windows open on the second screen:" if SCREEN else "only one screen found: windows open where the system puts them.", SCREEN or "")

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def farm(log, t0, seconds, party=1, map_name="Rocky Road II", level=27):
    t, nxt = t0, t0
    while t < t0 + seconds:
        if t >= nxt:
            log.gain(t, 100)
            nxt = t + 3
        log.tick(t, map_name, level, party, None, "Bandit")
        t += 0.5
    return t


def on_disk():
    return {(e["map"], e["party"]): e.get("note") for e in
            json.loads((DATA / "exp_log.json").read_text(encoding="utf-8"))["entries"]}


# a log to look at: one solo line, and one map played in parties of 4 and 5 (shown as one line)
seed = m.AutoLog(15, m.LOG_PATH, m.LOG_TEXT_PATH)
t = farm(seed, 0, 310, party=1, map_name="Ant Tunnel I")
t = farm(seed, t + 60, 310, party=4, map_name="Wild Boar Land II", level=30)
t = farm(seed, t + 60, 310, party=5, map_name="Wild Boar Land II", level=30)
m.save_config({**m.DEFAULTS, "log_open": True, "pos": POS, "hotkeys_on": False, "check_updates": False,
               "share_runs": False})

root = m.tk.Tk()
app = m.App(root, m.DigitTemplates.load())
root.update()
tree, box = app.log_tree, app.note_text


def pump(seconds=0.0):
    end = time.time() + seconds
    root.update()
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def row(map_name, group=False):
    for iid in tree.get_children():
        if tree.item(iid, "values")[1] == map_name and (iid in app.groups) == group:
            return iid


def select(iid):
    tree.selection_set(iid)
    pump(0.1)


def type_note(text):
    box.insert("end", text)
    pump(0.1)


solo, group = row("Ant Tunnel I"), row("Wild Boar Land II", group=True)
check("the log has a solo line and one shared party line", solo and group)
check("the Note column is there, empty to begin with",
      "note" in tree["columns"] and tree.item(solo, "values")[9] == "" and tree.item(group, "values")[9] == "")
check("with no line selected, the note box can't be typed in", str(box.cget("state")) == "disabled")

# a solo line
select(solo)
check("selecting a line opens its note", str(box.cget("state")) == "normal" and "Ant Tunnel I" in app.note_title.cget("text")
      and "only" in app.note_title.cget("text").lower())
type_note("Good drops, bring pots!")
check("what's typed isn't saved at once (it waits for a pause)", on_disk()[("Ant Tunnel I", 1)] is None)
pump(1.3)
check("...but a moment later it's saved in the log file", on_disk()[("Ant Tunnel I", 1)] == "Good drops, bring pots!")
check("...and the line gets its mark", tree.item(solo, "values")[9] == m.NOTE_MARK and "saved" in app.note_title.cget("text"))

# moving to another line saves the one left, and shows the other's note
type_note(" More")
select(group)
check("clicking another line saves the note being typed", on_disk()[("Ant Tunnel I", 1)] == "Good drops, bring pots! More")
check("the other line's box is empty and ready", box.get("1.0", "end-1c") == "" and str(box.cget("state")) == "normal")

# a shared party line: one note for all of its party sizes
type_note("Pt of 4-5 works.\nKS-ing.")
select(solo)
check("a note on a shared party line goes on each party size", on_disk()[("Wild Boar Land II", 4)] == on_disk()[("Wild Boar Land II", 5)]
      == "Pt of 4-5 works.\nKS-ing.")
check("...and shows on the shared line", tree.item(group, "values")[9] == m.NOTE_MARK)
tree.item(group, open=True)
app._sync_group(group)
child = tree.get_children(group)[0]
select(child)
check("opening it, each party size shows the same note", box.get("1.0", "end-1c") == "Pt of 4-5 works.\nKS-ing."
      and tree.item(child, "values")[9] == m.NOTE_MARK)

# the log refreshes by itself every few minutes: that must not disturb a note being typed
select(solo)
box.mark_set("insert", "1.4")
type_note("\n(typed while the log refilled)")
before = box.get("1.0", "end-1c")
app._fill_log()
pump(0.3)
check("a refilled log keeps the same line selected", tree.selection() == (row("Ant Tunnel I"),))
check("...keeps what's being typed", box.get("1.0", "end-1c") == before)
check("...and saved it first", on_disk()[("Ant Tunnel I", 1)] == before)

# renaming a map keeps its note
app.autolog.rename(app.autolog.entries[[e["map"] for e in app.autolog.entries].index("Ant Tunnel I")], "Ant Tunnel One")
app._fill_log()
pump(0.2)
check("renaming a map keeps its note", on_disk()[("Ant Tunnel One", 1)] == before and tree.item(row("Ant Tunnel One"), "values")[9] == m.NOTE_MARK)
check("...and the box goes empty (that line is gone under its old name)", str(box.cget("state")) == "disabled")

# typing needs the hotkeys out of the way: Shift+1 is "!"
select(row("Ant Tunnel One"))
box.event_generate("<FocusIn>")
pump(0.1)
check("hotkeys are off while typing a note", getattr(app.hotkeys, "_paused", True) is True)
box.event_generate("<FocusOut>")
pump(0.1)
check("...and back on after", getattr(app.hotkeys, "_paused", False) is False)

# an over-long note is cut to the limit, in the box too
select(row("Ant Tunnel One"))
box.delete("1.0", "end")
box.insert("1.0", "x" * (m.NOTE_MAX + 500))
pump(1.3)
check("a note longer than the limit is cut to it", len(box.get("1.0", "end-1c")) == m.NOTE_MAX
      and len(on_disk()[("Ant Tunnel One", 1)]) == m.NOTE_MAX)

# deleting a line with a note says so
asked = []
m.messagebox.askyesno = lambda title, text, **k: asked.append(text) or False
app._delete([app.shown[row("Ant Tunnel One")]])
check("deleting a line that has a note says its note goes too", asked and "note on it will be deleted too" in asked[0])

# closing the overlay (or hiding the log) saves a note still being typed
select(row("Ant Tunnel One"))
box.delete("1.0", "end")
type_note("last words")
app.set_compact(True)
check("switching to compact mode saves a note being typed", on_disk()[("Ant Tunnel One", 1)] == "last words")
app.set_compact(False)
pump(0.3)
select(row("Ant Tunnel One"))
type_note(" and one more")
app.close()
check("closing the overlay saves a note being typed", on_disk()[("Ant Tunnel One", 1)] == "last words and one more")
saved = json.loads((DATA / "exp_log.json").read_text(encoding="utf-8"))["entries"]
check("every line is still as the overlay wrote it (notes don't break the seal)",
      all(not m.AutoLog(15, m.LOG_PATH, m.LOG_TEXT_PATH).edited(e) for e in saved))

# ---- the design: every size, every state, and awkward values never break the window
root = m.tk.Tk()
m.save_config({**m.DEFAULTS, "hotkeys_on": False, "check_updates": False, "share_runs": False, "pos": POS})
app = m.App(root, m.DigitTemplates.load())
t, meso = app.tracker, app.meso
real_max_ui_scale = app._max_ui_scale
app._max_ui_scale = lambda: m.UI_SCALES[-1]              # (these checks draw every Size; the cap for small screens is checked below)
sizes_ok, widths = True, {}
for scale in m.UI_SCALES:
    try:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for case in ("idle", "tracking", "huge", "compact", "details"):
            if case == "idle":
                t.exp = t.pct = t.req = None
                t.rate = lambda: None
                t.rolling_rate = lambda: None
            else:
                t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
                huge = case == "huge"
                t.rate = lambda huge=huge: 123456789.0 if huge else 72566.0
                t.rolling_rate = lambda huge=huge: 99999999.0 if huge else 71900.0
                t.elapsed = lambda: 5620.0
                meso.value, meso.gained = 18449220, 41000
                app.level = 35
            app.set_compact(case == "compact")
            if case == "details" and not app.view.details_open:
                app.view._toggle_details()
            app.session.on = case != "idle"
            app._update_display()
            root.update()
            widths[scale] = app.view.winfo_width()
    except Exception as e:                                   # (printed, so a failure says where)
        print("   error at", scale, "%:", type(e).__name__, e)
        sizes_ok = False
check("the window draws at every size (80% to 200%) and in every state: idle, tracking, huge numbers, compact, details", sizes_ok)
check(f"a bigger size makes the window wider, not clipped ({widths})",
      all(widths[a] < widths[b] for a, b in zip(m.UI_SCALES, m.UI_SCALES[1:])))
app.view.show(rate="123,456,789", state="alert", state_text="Can't read the EXP bar",
              message="A very long message " * 12, tiles={"Last 5 min": "99,999,999", "Mesos / hr": "-", "Elapsed": "123:59:59"})
root.update()
check("long messages and big numbers still fit the window", app.view.winfo_reqwidth() <= app.theme.px(400) + 4)
app.set_compact(False)
app.set_compact(True)
app._update_display()
hero = app.view.hero
check("compact mode shows mesos/hr under the number, and only there",
      hero.itemcget(hero._mval, "state") == "normal" and hero.itemcget(hero._mval, "text") != "")
app.set_compact(False)
app._update_display()
check("full mode doesn't show that line (it has the tile)", hero.itemcget(hero._mval, "state") == "hidden")

# ---- the compact shapes: Square, Wide and Bar
import ui_main  # noqa: E402
t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
t.rate, t.rolling_rate, t.elapsed = (lambda: 72566.0), (lambda: 71900.0), (lambda: 5620.0)
meso.value, meso.gained = 18449220, 41000
app.level, app.session.on = 35, True
meso.per_hour = lambda el: 28830.0
app._state = lambda: ("tracking", "Tracking")
shapes_ok, fits = True, True
for scale in (60, 100, 200):
    app.cfg["ui_scale"] = scale
    app._apply_ui_scale()
    for shape in m.SHAPES:
        try:
            app.set_shape(shape)
            app.set_compact(True)
            app._update_display()
            root.update()
            want = app.theme.px(ui_main.SHAPE_W[shape])
            room = min(want, root.winfo_screenwidth())      # a small screen (a CI machine) can't show a wider window
            ok = abs(app.view.winfo_reqwidth() - want) <= 4 and root.winfo_width() >= room - 4
            if not ok:
                print("   width at", scale, "%", shape, ": want", want, "view", app.view.winfo_reqwidth(),
                      "window", root.winfo_width(), "screen", root.winfo_screenwidth())
            fits = fits and ok
        except Exception as e:
            print("   error at", scale, "%", shape, ":", type(e).__name__, e)
            shapes_ok = False
    app.set_compact(False)
root.update()
check("every compact shape draws at 60%, 100% and 200%", shapes_ok)
check("...and each window is as wide as its shape says (Square 360, Wide 560, Bar 720)", fits)
app.cfg["ui_scale"] = 100
app._apply_ui_scale()
app.set_shape("wide")
app.set_compact(True)
app._update_display()
root.update()
wide = app.view.strips["wide"]
shown = [wide.canvas.itemcget(i, "text") for i in wide._vals]
app.view.show(tiles=None)                                  # (the main view copes with no tiles; so must the strips)
wide.activate(True)
check("Wide copes with no tiles at all", wide._vals and wide.canvas.itemcget(wide._vals[0], "text") == "—")
app._update_display()
root.update()
check("Wide shows last 5 min, mesos/hr and time to the next level",shown[0] == "71,900" and shown[1] and shown[2] not in ("", "\u2014"))
check("...with the level to reach in its heading", wide.canvas.itemcget(wide._caps[3], "text") == "TO LV 36")
check("Wide has no big hero or tiles on screen", not app.view.hero.winfo_ismapped() and not app.view.tiles_row.winfo_ismapped())
app.set_shape("bar")
app._update_display()
root.update()
bar = app.view.strips["bar"]
check("Wide gives way to Bar when the shape changes in compact mode", bar.winfo_ismapped() and not wide.winfo_ismapped())
check("Bar shows mesos/hr and the level to reach", bar.canvas.itemcget(bar._mval, "text") not in ("", "\u2014")
      and "Lv 36" in bar.canvas.itemcget(bar._eta, "text"))
check("Bar has no status line while tracking...", not bar.message.winfo_ismapped())
# net mesos/hr in the compact shapes
app.cfg["potions_on"] = True
app.session.potions.used = {"Red Potion": 20}
net = app._net(5620.0)[0]
app._update_display()
root.update()
cv, hero = bar.canvas, app.view.hero
check(f"Bar shows net mesos/hr after mesos/hr, clear of the level text ({cv.itemcget(bar._net, 'text')})",
      cv.itemcget(bar._net, "text") == f"net {net}" and cv.bbox(bar._net)[0] >= cv.bbox(bar._munit)[2]
      and cv.bbox(bar._net)[2] < cv.bbox(bar._eta)[0])
app.set_shape("wide")
app._update_display()
root.update()
wc = wide.canvas
check("Wide shows it under the mesos/hr value", wc.itemcget(wide._net, "text") == f"net {net}"
      and wc.bbox(wide._net)[1] >= wc.bbox(wide._vals[1])[3] - app.theme.px(4)
      and wc.bbox(wide._net)[3] <= app.theme.px(wide.top + wide.card_h))
app.set_shape("square")
app._update_display()
root.update()
check("Square shows it on the mesos/hr line", hero.itemcget(hero._nval, "state") == "normal"
      and hero.itemcget(hero._nval, "text").endswith(f"net {net}") and hero.bbox(hero._nval)[0] >= hero.bbox(hero._munit)[2])
app.cfg["potions_on"] = False
app._update_display()
app.set_shape("wide")
app._update_display()
app.set_shape("bar")
app._update_display()
root.update()
check("with potions off none of them shows a net", hero.itemcget(hero._nval, "state") == "hidden"
      and wide.canvas.itemcget(wide._net, "text") == "" and bar.canvas.itemcget(bar._net, "text") == "")
app.cfg["potions_on"], app.cfg["potion_prices"] = True, {"Red Potion": 0}     # net can't be worked out
for shape in ("wide", "bar", "square"):
    app.set_shape(shape)
    app._update_display()
root.update()
check("with no net to show (a potion priced 0) the shapes look as without one", hero.itemcget(hero._nval, "state") == "hidden"
      and wide.canvas.itemcget(wide._net, "text") == "" and bar.canvas.itemcget(bar._net, "text") == "")
app.cfg["potions_on"], app.cfg["potion_prices"] = False, {}
app.set_shape("bar")
app._update_display()
# Full mode: net mesos/hr is a tile, four across, without a wider window
app.set_compact(False)
app.cfg["potions_on"] = True
app.session.potions.used = {"Red Potion": 20}
app._update_display()
root.update()
tile = app.view.tiles["Net / hr"]
check(f"Full mode has a Net / hr tile with the net ({tile.itemcget(tile._v, 'text')})", tile.itemcget(tile._v, "text") == app._net(5620.0)[0])
check("...four tiles side by side, none cut off and none beyond the window",
      list(app.view.tiles) == ["Last 5 min", "Mesos / hr", "Net / hr", "Elapsed"]
      and all(t.winfo_x() + t.winfo_width() <= app.view.tiles_row.winfo_width() + 2 for t in app.view.tiles.values())
      and abs(app.view.winfo_reqwidth() - app.theme.px(ui_main.W)) <= 4)
app.cfg["potions_on"] = False
app._update_display()
check("with potions off the tile shows a dash", tile.itemcget(tile._v, "text") == "—")
app.cfg["potions_on"] = True
app.session.potions.used = {}
app.set_compact(True)
app.cfg["potions_on"] = True
app.session.potions.reset()
app._update_display()

app._state = lambda: ("alert", "Can't read the EXP bar")
app.status.set("Can't find the EXP bar.")
app._update_display()
root.update()
check("...but shows it when something needs attention", bar.message.winfo_ismapped())
app._state = lambda: ("tracking", "Tracking")
app.status.set("Tracking.")
app._update_display()
app._set_start("Pause")
check("Start/Pause on the sideways shapes follows the real button", bar.start_btn.icon == "pause" and wide.start_btn.icon == "pause"
      and wide.start_btn.text == "Pause")
app._set_start("Resume")
check("...and the shape made later starts in the right state", app.view.strips["bar"].start_btn.icon == "play")
bar.compact_btn.command()
root.update()
check("the compact icon on a sideways shape goes back to full", not app.compact and app.view.head.winfo_ismapped()
      and not bar.winfo_ismapped() and app.view.hero.winfo_ismapped())
check("full mode is as wide as ever whichever shape is chosen", abs(app.view.winfo_reqwidth() - app.theme.px(ui_main.W)) <= 4)
check("the shape is saved", json.loads((DATA / "config.json").read_text(encoding="utf-8"))["compact_shape"] == "bar")
app.set_shape("square")
app._set_start("Start")

# Settings has a What's new button that shows this version's notes again
shown = []
real_whats_new = app._whats_new
app._whats_new = lambda notes, then: shown.append(notes)
app.show_whats_new()
check("Settings > What's new shows this version's notes again", shown == [m.WHATS_NEW[m.VERSION]])
app._whats_new = real_whats_new

# this version's note opens, and says what the hotfix is about
before = set(app.root.winfo_children())
app._whats_new(m.WHATS_NEW[m.VERSION], None)
root.update()
opened = [c for c in app.root.winfo_children() if c not in before and c.winfo_class() == "Toplevel"]
texts = []
for top in opened:
    stack = [top]
    while stack:
        w0 = stack.pop()
        stack.extend(w0.winfo_children())
        try:
            texts.append(str(w0.cget("text")))
        except Exception:
            pass
check("this version's What's new window opens and speaks of health reports",
      len(opened) == 1 and any("Health reports" in t for t in texts))
for top in opened:
    top.destroy()

# the two pages: Next shows the potion page (with its pictures), and the window fits the screen
before = set(app.root.winfo_children())
app._whats_new(m.WHATS_NEW["0.9.11-beta"], None)
root.update()
win = [c for c in app.root.winfo_children() if c not in before and c.winfo_class() == "Toplevel"][0]


def widgets(w):
    out = [w]
    for c in w.winfo_children():
        out += widgets(c)
    return out


def labels(w):
    return [c.cget("text") for c in widgets(w) if c.winfo_class() == "Label" and not c.cget("image")]


def button(w, text):
    return [c for c in widgets(w) if isinstance(c, ui_main.Button) and c.text == text]


check("the first page has a Next button, not Got it", button(win, "Next") and not button(win, "Got it")
      and any("what you really make" in t for t in labels(win)))
button(win, "Next")[0].command()
root.update()
pictures = len([c for c in widgets(win) if c.winfo_class() == "Label" and c.cget("image")])
check(f"Next shows the potion page with a Got it button, and both pictures on a screen with room ({pictures})",
      "How potion cost works" in labels(win) and button(win, "Got it") and not button(win, "Next")
      and (pictures == 2 or win.winfo_screenheight() < 1000))
check("...and the window fits on the screen", win.winfo_reqheight() <= win.winfo_screenheight() - 60)
win.destroy()
# first launch: a new install picks a shape, an update shows what's new, and each only once
calls = []
app._shape_picker = lambda then: calls.append("picker") or then()
app._whats_new = lambda notes, then: calls.append("notes") or then()
app._ask_to_share = lambda: calls.append("share")


def first_run(fresh, seen):
    calls.clear()
    app._fresh, app.cfg["seen_version"] = fresh, seen
    app._first_run()
    return list(calls), app.cfg["seen_version"]


check("a new install picks a compact shape, then gets the sharing question", first_run(True, "") == (["picker", "share"], m.VERSION))
check("someone updating sees what's new once, then the sharing question", first_run(False, "0.9.9-beta") == (["notes", "share"], m.VERSION))
check("an existing config from before this feature counts as an update", first_run(False, "") == (["notes", "share"], m.VERSION))
check("the same version shows neither again", first_run(False, m.VERSION) == (["share"], m.VERSION))
check("going back to an older version shows nothing and keeps what was seen", first_run(False, "0.9.99") == (["share"], "0.9.99"))
check("what's new has a note for this version", bool(m.WHATS_NEW.get(m.VERSION)))
# the health report question: asked once, after the sharing question; "No" never sends
del app._ask_to_share
real_ask_yesno, real_post_health = m.messagebox.askyesno, m.post_health
asked, answer, posted = [], [False], []
m.messagebox.askyesno = lambda title, text, **k: asked.append(title) or answer[0]
m.post_health = lambda body, timeout=15: posted.append(body) or ("ok", "")
os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"              # (post_health is replaced above: nothing leaves this PC)
app.cfg["share_runs"] = False
app.cfg.pop("share_health", None)
yesterday = m.healthlog.utc_day(time.time() - 86400)
real_clock = app.healthlog.clock
app.healthlog.clock = lambda: time.time() - 86400
app.healthlog.snapshot("0.9.14-beta", "Windows 10", [], "screen", "ok")
app.healthlog.look("read", ms=10.0, game=(1920, 1080, False, 11.0))
app.healthlog.clock = real_clock
app._ask_to_share()
check("the health question is asked once, with its own title", asked == ["Help make the overlay more reliable?"]
      and app.cfg["share_health"] is False)
app._ask_to_share()
check("...and not again", len(asked) == 1)
app._health_tick()
pump(0.6)
check("answering No never sends anything", not posted and app.cfg["share_health"] is False)
# a brand-new install (neither question answered yet): the runs question first, then the health one, each once
app.cfg.pop("share_runs", None)
app.cfg.pop("share_health", None)
asked.clear()
app._ask_to_share()
app._ask_to_share()
check("a new install is asked about sharing runs, then about health reports, and neither twice",
      asked == ["Help other players pick maps?", "Help make the overlay more reliable?"]
      and app.cfg["share_runs"] is False and app.cfg["share_health"] is False and not posted)
app.cfg.pop("share_health")
answer[0] = True
app._ask_to_share()
check("answering Yes is saved", app.cfg["share_health"] is True)
app.healthlog.last_attempt = 0.0
app._health_tick()
pump(0.8)
check("with Yes, the finished day is sent once and marked sent", len(posted) == 1 and posted[0]["day"] == yesterday
      and posted[0]["id"] == app.cfg["health_id"] and app.healthlog.last_sent == yesterday)
app._health_tick()
pump(0.4)
check("...and not again", len(posted) == 1)
app.open_settings()
sw = app.settings_win
sw.update()
check("Settings has a health report switch showing the answer", app._health_on.get() is True)
app._health_on.set(False)
sw.update()
check("switching it off in Settings is saved", app.cfg["share_health"] is False
      and json.loads(m.CONFIG_PATH.read_text())["share_health"] is False)
app._health_on.set(True)
sw.update()
check("...and on again", app.cfg["share_health"] is True)
sw.destroy()
app.settings_win = None
os.environ.pop("MAPLE_EXP_ALLOW_SHARE")
m.messagebox.askyesno, m.post_health = real_ask_yesno, real_post_health
app._ask_to_share = lambda: calls.append("share")
picked = []
selector = m.RegionSelector(root, picked.append)             # (it crashed on Windows in 0.9.7 and 0.9.8-beta)
root.update()
check("Select area opens its full-screen picker", selector.win.winfo_exists())
selector._finish(None)
check("...and Esc closes it without choosing anything", picked == [None] or picked == [])
import numpy as np
fake = m.Reading(*([None] * len(m.Reading._fields)))._replace(
    where="MapleStory window 1920x1080", text="3,060 [1.04%]", image=np.full((40, 200, 3), 60, np.uint8), boxes=[])
app.last_capture = fake
app.job = None
app.reader.job_look = {"image": np.full((60, 300, 3), 90, np.uint8), "boxes": [(10, 10, 80, 40, "exp")],
                       "why": "the tries didn't agree on what it says"}
before = set(root.winfo_children())
app.view_capture()
root.update()
win = next(w for w in set(root.winfo_children()) - before)
texts = " | ".join(w.cget("text") for w in win.winfo_children() if w.winfo_class() == "Label" and w.cget("text"))
check("View capture says why the class wasn't read", "Class. Not read: the tries didn't agree" in texts)
win.destroy()
app.reader.job_look = {"image": None, "boxes": [], "why": "this window is covering the class name: move it"}
app.view_capture(); root.update()
win = next(w for w in set(root.winfo_children()) - before)
check("...and works when there is no picture (the overlay is in the way)",
      "covering the class name" in " ".join(w.cget("text") for w in win.winfo_children() if w.winfo_class() == "Label"))
win.destroy()
app.reader.box = (700, 1000, 200, 30)                          # (the EXP bar is found: only then is the class worth asking about)
files = app._save_report_files(False)
text = app._report_text(files)
check("a bug report says why the class wasn't read", "Class not read: this window is covering the class name" in text)
check("it saves a picture, and the details as a text file", files["picture"].exists() and files["details"].exists())
check("...with the whole story in the text file", all(w in files["details"].read_text(encoding="utf-8")
                                                      for w in ("== Game window ==", "== What it found ==", "== What happened")))
game_pic = m.DATA_DIR / "bug-report-game.jpg"
game_pic.write_bytes(b"x")
app._forget_report_screen()
check("the whole-game picture of a bug report (it can show chat and names) is deleted again", not game_pic.exists())
app._forget_report_screen()
check("...and deleting it twice (or when it was never made) is fine", not game_pic.exists())
check("the web address a report is opened with stays short enough",
      len(m.urllib.parse.urlencode({"title": "x", "body": text})) < 7000)
check("it never contains the Windows user name",
      not any(len(s) > 2 and s in text + files["details"].read_text(encoding="utf-8")
              for s in {str(m.Path.home()), m.os.environ.get("USERNAME", "")}))
print(text)
app.job, app.reader.job_look, app.last_capture = None, None, None
app.reader.box = None
# ---- every Size at every Windows scaling: nothing drawn outside its box, nothing cut off, nothing broken
def spill(widget, slack=2):
    """Texts or pictures that stick out of the canvas they're drawn in (listed by kind)."""
    bad = []
    for canvas in [widget] + [c for c in widget.winfo_children()]:
        stack = [canvas]
        while stack:
            c = stack.pop()
            stack.extend(c.winfo_children())
            if c.winfo_class() != "Canvas" or not c.winfo_ismapped():
                continue
            c.update_idletasks()
            w, h = c.winfo_width(), c.winfo_height()
            for item in c.find_all():
                if c.type(item) != "text" or c.itemcget(item, "state") == "hidden" or not c.itemcget(item, "text"):
                    continue
                x0, y0, x1, y1 = c.bbox(item)
                if x0 < -slack or y0 < -slack or x1 > w + slack or y1 > h + slack:
                    bad.append(f"{c.itemcget(item, 'text')[:18]!r} {x0},{y0},{x1},{y1} in {w}x{h}")
    return bad


app._check_screen = lambda: None                          # (it would put the screen's own scaling back)
layout_bad = []
for dpi in (96, 120, 144, 192):                          # Windows scaling 100%, 125%, 150%, 200%
    app._dpi = dpi
    for scale in m.UI_SCALES:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for case in ("tracking", "huge", "compact", "details"):
            t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
            huge = case == "huge"
            t.rate = lambda huge=huge: 123456789.0 if huge else 72566.0
            t.rolling_rate = lambda huge=huge: 99999999.0 if huge else 71900.0
            t.elapsed = lambda: 5620.0
            meso.value, meso.gained = 18449220, 41000
            app.level = 35
            app.set_compact(case == "compact")
            if case == "details" and not app.view.details_open:
                app.view._toggle_details()
            if case != "details" and app.view.details_open:
                app.view._toggle_details()
            app.session.on = True
            app._update_display()
            root.update()
            for line in spill(app.view):
                layout_bad.append(f"{dpi}dpi {scale}% {case}: {line}")
        app.set_compact(False)
check("at every Size and Windows scaling, no text sticks out of its box on the main window"
      + (f" ({len(layout_bad)} problems, first: {layout_bad[:3]})" if layout_bad else ""), not layout_bad)

fonts_small = []
for dpi in (96, 120, 144, 192):
    app._dpi = dpi
    for scale in m.UI_SCALES:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for name, f in app.fonts.items():
            if abs(int(f.cget("size"))) < 9:
                fonts_small.append(f"{dpi}dpi {scale}% {name} {f.cget('size')}")
        for (weight, size), f in app.theme._tk.items():
            if abs(int(f.cget("size"))) < m.MIN_TEXT_PX if hasattr(m, "MIN_TEXT_PX") else False:
                fonts_small.append(f"{dpi}dpi {scale}% {weight} {size}")
check(f"text never gets smaller than the readable minimum ({fonts_small[:3]})", not fonts_small)

# the window, the log and Settings still fit their contents at the smallest and biggest sizes
fit_bad = []
for dpi in (96, 192):
    app._dpi = dpi
    for scale in (60, 70, 100, 200):
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        app.set_compact(False)
        if not app.log_open:
            app.toggle_log()
        root.update()
        tree = app.log_tree
        headfont = m.tkfont.nametofont("TkHeadingFont") if "TkHeadingFont" in m.tkfont.names(root) else app.fonts["text"]
        for col in tree["columns"]:
            need = app.fonts["text"].measure(tree.heading(col, "text") or "") + 6
            if int(tree.column(col, "width")) + 2 < min(need, 40):
                fit_bad.append(f"{dpi}dpi {scale}% log column {col}")
        app.open_settings()
        sw = app.settings_win
        sw.geometry("")
        sw.update()
        for child in sw.winfo_children():
            stack = [child]
            while stack:
                c = stack.pop()
                stack.extend(c.winfo_children())
                scrolls = str(c).startswith(str(app._settings_inner))      # (below the window's bottom it scrolls)
                if c.winfo_ismapped() and (c.winfo_rootx() + c.winfo_width() > sw.winfo_rootx() + sw.winfo_width() + 2
                                           or not scrolls and c.winfo_rooty() + c.winfo_height() > sw.winfo_rooty() + sw.winfo_height() + 2):
                    fit_bad.append(f"{dpi}dpi {scale}% settings: {c.winfo_class()} sticks out")
        for line in spill(sw):
            fit_bad.append(f"{dpi}dpi {scale}% settings: {line}")
        sw.destroy()
        app.settings_win = None
        app.toggle_log()
check(f"the log and Settings fit at the smallest and biggest Sizes ({fit_bad[:3]})", not fit_bad)
app._dpi = 192 if app._dpi not in (96, 120, 144, 192) else app._dpi
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

# ---- Settings is never taller than its screen (it scrolls), and Size stops at the biggest that fits (B12-1)
real_dpi = app._dpi
app._dpi = 96
app.cfg["ui_scale"] = 100
app._apply_ui_scale()
app.set_compact(False)
if not app.view.details_open:
    app.view._toggle_details()
root.update()
measured = (root.winfo_reqwidth(), root.winfo_reqheight())
want = [round(p * 96 / 72) for p in m.MAIN_BIGGEST_PT]
check(f"the main window's biggest size the Size cap uses matches the real window ({measured} vs {want})",
      all(abs(a - b) <= 0.1 * b for a, b in zip(measured, want)))
app.view._toggle_details()
app._max_ui_scale = real_max_ui_scale
real_screen = app._screen_box
cap_bad, settings_bad = [], []
for screen in ((0, 0, 1920, 1080), (0, 0, 1366, 768), (0, 0, 2560, 1440), (0, 0, 5120, 2880)):
    app._screen_box = lambda screen=screen: screen
    for dpi in (96, 120, 144, 192):
        app._dpi = dpi
        app.cfg["ui_scale"] = 200
        app._apply_ui_scale()
        used, room = app._ui_scale_used, app._screen_room()
        tall = lambda s: s / 100 * dpi / 72 * m.MAIN_BIGGEST_PT[1]
        nxt = m.UI_SCALES[m.UI_SCALES.index(used) + 1] if used < m.UI_SCALES[-1] else None
        if used > m.UI_SCALES[0] and tall(used) > room[1] or nxt and tall(nxt) <= room[1] and nxt / 100 * dpi / 72 * m.MAIN_BIGGEST_PT[0] <= room[0]:
            cap_bad.append(f"{screen[2]}x{screen[3]} {dpi}dpi: {used}%")
        if app.cfg["ui_scale"] != 200:
            cap_bad.append(f"{screen[2]}x{screen[3]} {dpi}dpi: the chosen Size was changed")
        app.open_settings()
        sw = app.settings_win
        sw.update()
        if sw.winfo_height() > room[1] + 2:
            settings_bad.append(f"{screen[2]}x{screen[3]} {dpi}dpi {used}%: {sw.winfo_height()} > {room[1]:.0f}")
        if app._settings_inner.winfo_reqheight() > room[1] + 2 and not app._settings_bar.winfo_ismapped():
            settings_bad.append(f"{screen[2]}x{screen[3]} {dpi}dpi {used}%: taller than the screen and no scrollbar")
        sw.destroy()
        app.settings_win = None
check(f"Size is used as the biggest that fits the screen (main window full with Details), the chosen one is kept ({cap_bad[:3]})",
      not cap_bad)
check(f"Settings is never taller than its screen, and scrolls when it would be ({settings_bad[:3]})", not settings_bad)
app._screen_box = lambda: (0, 0, 1920, 1080)
app._dpi = 96
app.cfg["ui_scale"] = 100
app._apply_ui_scale()
app.open_settings()
app.settings_win.update()
check("on a 1080p screen at Size 100% Settings scrolls (its bottom buttons can be reached)",
      app._settings_bar.winfo_ismapped() and app.settings_win.winfo_height() <= app._screen_room()[1] + 2)
app.settings_win.destroy()
app.settings_win = None
app._screen_box = real_screen
app._dpi = real_dpi
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

# ---- Size and Transparency limits
check("sizes from 60% to 200% are offered", m.UI_SCALES[0] == 60 and m.UI_SCALES[1] == 70 and m.UI_SCALES[-1] == 200)
saved = m.CONFIG_PATH
cfg_file = Path(tempfile.mkdtemp()) / "config.json"
cfg_file.write_text(json.dumps({"transparency": 90, "ui_scale": 70}), encoding="utf-8")
m.CONFIG_PATH = cfg_file
loaded = m.load_config()
m.CONFIG_PATH = saved
check("an older config with transparency 90 loads as the 40% limit, and a saved 70% size is kept",
      loaded["transparency"] == 40 and loaded["ui_scale"] == 70)
cfg_file.write_text(json.dumps({"transparency": -5, "ui_scale": 65}), encoding="utf-8")
m.CONFIG_PATH = cfg_file
loaded = m.load_config()
m.CONFIG_PATH = saved
check("a negative transparency is 0, and a size that isn't offered is 100%", loaded["transparency"] == 0 and loaded["ui_scale"] == 100)
check("transparency can't go past 40%", m.MAX_TRANSPARENCY == 40)

# ---- the transparency slider stops at the limit, and the window can't fade past it
app.cfg["transparency"] = 90
app._apply_window_settings()
check("the window is never more see-through than the limit (opacity %.2f)" % float(root.attributes("-alpha")),
      float(root.attributes("-alpha")) >= 1 - m.MAX_TRANSPARENCY / 100 - 0.01)
app.cfg["transparency"] = 0
app._apply_window_settings()

# ---- hotkeys in Settings: one waiting at a time, and clicking it again cancels
if app.hotkeys.supported:
    app.open_settings()
    sw = app.settings_win
    sw.update()
    ui = app._hotkey_ui
    names = list(ui["buttons"])
    keys_before = dict(app.cfg["hotkeys"])
    texts = lambda: {a: b.itemcget(b._txt, "text") for a, b in ui["buttons"].items()}
    ui["choose"](names[0])
    check("clicking a hotkey makes it wait for keys", ui["choosing"]["action"] == names[0] and texts()[names[0]] == "Press keys...")
    ui["choose"](names[1])
    t2 = texts()
    check("clicking another one moves the wait to it: only one says Press keys",
          ui["choosing"]["action"] == names[1] and sum(v == "Press keys..." for v in t2.values()) == 1
          and t2[names[0]] == (keys_before[names[0]] or "Off"))
    ui["choose"](names[1])
    check("clicking the waiting one again cancels it, and every key is as it was",
          ui["choosing"]["action"] is None and texts() == {a: (keys_before[a] or "Off") for a in names}
          and app.cfg["hotkeys"] == keys_before)
    check("hotkeys work again after cancelling", getattr(app.hotkeys, "_paused", True) is False)
    ui["choose"](names[2])
    esc = type("E", (), {"keysym": "Escape", "state": 0, "keycode": 27})()
    ui["pressed"](esc)
    check("Esc cancels too, keeping the key", ui["choosing"]["action"] is None and app.cfg["hotkeys"] == keys_before)
    ui["choose"](names[3])
    back = type("E", (), {"keysym": "BackSpace", "state": 0, "keycode": 8})()
    ui["pressed"](back)
    check("Backspace turns that hotkey off", app.cfg["hotkeys"][names[3]] == "" and ui["choosing"]["action"] is None)
    app.cfg["hotkeys"].update(keys_before)
    m.save_config(app.cfg)
    ui["choose"](names[0])
    ui["on"].set(False)
    sw.update()
    check("turning hotkeys off while one is waiting ends the wait", ui["choosing"]["action"] is None)
    ui["on"].set(True)
    sw.update()
    sw.destroy()
    app.settings_win = None
    check("closing Settings leaves hotkeys working", getattr(app.hotkeys, "_paused", True) is False)

# ---- potion prices in Settings
app.cfg["potion_prices"] = {}
app.open_settings()
sw = app.settings_win
sw.update()
app.open_potion_prices()
pw = app.prices_win
pw.update()
table = app._potion_table()
want = sorted(i["name"] for i in table.prices.values() if i.get("kind") == "consumable")
check(f"every item the bar counts is listed with a box for its price ({len(app._price_vars)} of {len(want)})",
      sorted(app._price_vars) == want)
app._price_vars["Red Potion"].set("70")
check("a price typed in is saved and used", app.cfg["potion_prices"] == {"Red Potion": 70}
      and app._prices().get("Red Potion") == 70 and json.loads(m.CONFIG_PATH.read_text())["potion_prices"] == {"Red Potion": 70})
app._price_vars["Red Potion"].set("7x")
check("something that isn't a number leaves the saved price alone", app.cfg["potion_prices"] == {"Red Potion": 70})
app._price_vars["Red Potion"].set("²")
app._price_vars["Red Potion"].set("12345678901")
check("...nor do digits that aren't plain 0-9, or a price too big to be real", app.cfg["potion_prices"] == {"Red Potion": 70})
app._price_vars["Red Potion"].set("")
check("clearing the box brings the shipped price back", app.cfg["potion_prices"] == {} and app._prices().get("Red Potion") == 50)
app._price_vars["Red Potion"].set("0")
check("a price of 0 is kept as the player's own", app.cfg["potion_prices"] == {"Red Potion": 0} and app._prices().get("Red Potion") == 0)
app._price_vars["Red Potion"].set("")
app._potions_on.set(False)
sw.update()
check("the Potions switch is saved", app.cfg["potions_on"] is False)
app._potions_on.set(True)
sw.update()
pw.destroy()
sw.destroy()
app.settings_win = app.prices_win = None

# ---- net mesos/hr: mesos/hr minus what the potions cost per hour
real_per_hour = app.meso.per_hour
app.meso.per_hour = lambda el: 100000
app.cfg["potions_on"], app.cfg["potion_prices"] = True, {}
app.session.potions.used = {"Red Potion": 20, "Sap of Ancient Tree": 5}
check("net mesos/hr is the mesos/hr less the potion cost per hour (20 Red at 50 in an hour)",
      app._net(3600) == ("99,000", None))
check("...over half an hour the cost per hour doubles", app._net(1800) == ("98,000", None))
app.cfg["potion_prices"] = {"Red Potion": 0}
check("a price of 0 on a used NPC-sold potion makes net a dash and asks for a price",
      app._net(3600) == ("—", "Set a price for Red Potion"))
app.cfg["potion_prices"] = {"Sap of Ancient Tree": 0}
check("an item no NPC sells at 0 doesn't", app._net(3600) == ("99,000", None))
app.cfg["potions_on"] = False
check("with potions off there's no net to show", app._net(3600) == ("—", None))
app.cfg["potions_on"], app.cfg["potion_prices"] = True, {}
app.session.potions.reset()
app.meso.per_hour = real_per_hour

# ---- every widget of the main window sits inside it, in every shape, with details open, at every size
def sticking_out(win):
    bad, stack = [], list(win.winfo_children())
    while stack:
        c = stack.pop()
        if c.winfo_toplevel() is not win:            # (another window, e.g. a tooltip, has its own box)
            continue
        stack.extend(c.winfo_children())
        if c.winfo_ismapped() and (c.winfo_rootx() + c.winfo_width() > win.winfo_rootx() + win.winfo_width() + 2
                                   or c.winfo_rooty() + c.winfo_height() > win.winfo_rooty() + win.winfo_height() + 2):
            bad.append(c.winfo_class())
    return bad
box_bad = []
app.cfg["potions_on"] = True
app.session.potions.used = {"Mana Elixir": 1234567, "Power Elixir": 7, "Cider": 3}
for scale in m.UI_SCALES:
    app.cfg["ui_scale"] = scale
    app._apply_ui_scale()
    for shape in m.SHAPES:
        app.set_shape(shape)
        for compact in (False, True):
            app.set_compact(compact)
            if not compact and not app.view.details_open:
                app.view._toggle_details()
            app._update_display()
            root.update()
            if root.winfo_reqheight() > root.winfo_screenheight():      # (taller than the screen itself: 200% full with details, flagged B13-2)
                continue
            box_bad += [f"{scale}% {shape} {'compact' if compact else 'full'}: {c}" for c in sticking_out(root)]
app.set_shape(m.SHAPES[0])
app.set_compact(False)
if app.view.details_open:
    app.view._toggle_details()
app.cfg["ui_scale"] = 100
app._apply_ui_scale()
check(f"nothing sticks out of the main window at any size, shape or mode ({box_bad[:3]})", not box_bad)
shown = app.view.details.itemcget(app.view.details._vals["Potion cost"], "text")
check("a long potion hint is cut with an ellipsis instead of running into its label", shown.endswith("…") or len(shown) < 20)
app.session.potions.reset()
app.cfg["potions_on"] = False

# ---- a log size saved on a bigger screen doesn't outgrow this one
app.cfg["log_size"] = [root.winfo_screenwidth() + 3000, root.winfo_screenheight() + 3000]
app.toggle_log()
root.update()
check("the log opens no bigger than the screen", root.winfo_width() <= root.winfo_screenwidth() and root.winfo_height() <= root.winfo_screenheight())
app.toggle_log()
app.cfg["log_size"] = None

# ---- the potion cost line never says a bare 0 when it can't be trusted (issue #17)
real_running, real_bar = app.tracker.running, app.potion_bar
app.cfg["potions_on"], app.cfg["potion_prices"] = True, {}
app.tracker.running = True
app.potion_bar = m.potion_bar.PotionLook(True, "keyboard", "default", 1.0, ())
app._potion_unseen = None
check("potions off: a dash", (app.cfg.update(potions_on=False), app._potion_cost_text(0.0))[1] == "—")
app.cfg["potions_on"] = True
check("bar in sight, nothing used: plain 0", app._potion_cost_text(0.0) == "0")
app.session.potions.used = {"Red Potion": 20}
check("bar in sight, priced items: just the cost", app._potion_cost_text(1.0) == "1,000")
app.session.potions.used = {"Mana Elixir": 3}
check("an item used with no price says so instead of 0", app._potion_cost_text(1.0) == "No price for: Mana Elixir")
app.session.potions.used = {"Red Potion": 20, "Mana Elixir": 3}
check("...and keeps the cost next to the hint", app._potion_cost_text(1.0) == "1,000  ·  No price for: Mana Elixir")
app.session.potions.used = {"Mana Elixir": 1, "Power Elixir": 1, "Cider": 1}
check("...three or more are counted", app._potion_cost_text(1.0) == "No price for: Mana Elixir, Power Elixir +1")
app.cfg["potion_prices"] = {"Mana Elixir": 700, "Power Elixir": 900, "Cider": 5}
check("a price the player set removes the hint", app._potion_cost_text(1.0) == "1,605")
app.cfg["potion_prices"] = {}
app.session.potions.used = {}
app.potion_bar = m.potion_bar.NOT_SEEN
app._potion_unseen = None
check("bar just out of sight: still plain 0", app._potion_cost_text(100.0) == "0")
check("bar unseen for a while: says so", app._potion_cost_text(100.0 + m.POTION_UNSEEN_AFTER) == "Potion bar not seen")
app.session.potions.used = {"Red Potion": 2}
check("...with the cost so far beside it", app._potion_cost_text(200.0) == "100  ·  Potion bar not seen")
app.potion_bar = m.potion_bar.PotionLook(True, "keyboard", "default", 1.0, ())
check("the bar back in sight clears it", app._potion_cost_text(201.0) == "100" and app._potion_unseen is None)
app.potion_bar = m.potion_bar.NOT_SEEN
app.tracker.running = False
check("paused: no hint about a bar nobody is watching", app._potion_cost_text(500.0) == "100")
app.session.potions.reset()
app.tracker.running, app.potion_bar, app._potion_unseen = real_running, real_bar, None

# ---- towns aren't logged; a minimap that can't be seen pauses
blank = m.Reading(*([None] * len(m.Reading._fields)))
app._handle(blank._replace(where="Reading", map="Perion"))
check("on a town map the status says nothing is logged", app.in_town and "In a town" in app.status.get())
entries = len(app.autolog.entries)
app._log_tick(m.time.monotonic())
check("...and nothing is logged there", len(app.autolog.entries) == entries)
app._handle(blank._replace(where="Reading", map="Rocky Road II"))
check("on a hunting map it's normal again", not app.in_town and "In a town" not in app.status.get())
real_ok = m.tesseract_ok
m.tesseract_ok = True
app.session.on, app.session.waiting = True, "idle"
app.reader.map_seen_at = m.time.monotonic() - m.MAP_BLIND_SECONDS - 5
app._handle(blank._replace(where="Reading"))
check("a minimap name unseen for a while pauses it", app.session.waiting == "blind")
app.reader.map_seen_at = m.time.monotonic()
app.session.on, app.session.waiting = False, None
m.tesseract_ok = real_ok

# ---- paused while a finished run waits for an inventory look: the status asks for one
now = m.time.monotonic()
app.meso.reset()                                            # (no recent look left over from the tests above)
app.autolog.looks, app.autolog.epoch = [], app.meso.resets
app.autolog.pending = {"start": now - 902, "chain": now - 902, "end": now - 2, "started": "-", "paused_at": None}
app.session.on, app.session.waiting = False, None
app.tracker.running = False
app._handle(blank._replace(where="Reading"))
check("paused while a run waits for a look: the status asks for the inventory", m.RUN_ALERT in app.status.get())
app.autolog.pending = None

# ---- a bar that shows only a percentage ("EXP. [50.00%]")
import level_ocr  # noqa: E402
from pics import badge_scene  # noqa: E402


def print_of(name):
    pic, x0, cy = badge_scene(32, 11, name=name)
    return level_ocr.name_print(pic, x0, cy, 11)


pct = blank._replace(where="Reading", candidates=[(None, 50.0)], confident=True, percent_only=True,
                     text="[50.00%] (percentage only)")
app.reset()
app.reader.level_failures, app.reader.name_print = 0, None
for _ in range(2):
    app._handle(pct)
check("a %-only bar with no level yet says it is reading the level",
      app.level_wait and "Reading your level" in app.status.get() and not app._can_type_level())
app.reader.level_failures = m.LEVEL_FAILS_FOR_FALLBACK
app._handle(pct)
check("...typing the level in is offered once reading keeps failing, and the status says where",
      app._can_type_level() and "Level fallback" in app.status.get())
real_ask = m.simpledialog.askinteger
m.simpledialog.askinteger = lambda *a, **k: 31
app.type_level()
m.simpledialog.askinteger = real_ask
check("a typed level is used for the estimate", app.tracker.req == 108480 and app.tracker.level_source == "typed")
app.session.start(m.time.monotonic())
app._handle(pct)
check("while tracking, the status and the numbers say they are estimates",
      "Estimated from the %" in app.status.get() and app.tracker.estimated and app.level == 31)

with_level = pct._replace(level=(30, "badge"))
app.reset()
for _ in range(2):
    app._handle(with_level)
check("a level read from the badge needs nothing typed", app.tracker.req == 95700 and not app.level_wait
      and not app._can_type_level() and app.level == 30)
app.reader.name_print = print_of("Fox")
app._handle(with_level)
app.reader.name_print = print_of("Pip2")
app._handle(with_level)
check("one different name print isn't another character", app.tracker.req == 95700)
app.reader.name_print = print_of("Pip2")
app._handle(with_level)
check("two in a row are: it starts over and says so, and the old level isn't kept",
      app.tracker.req is None and "another character" in app.status.get() and app.reader.level is None)
# ---- found by the audit
app.reset()
app.pct_note = "level changed: Reset to start again on the new level"
app.reset()
check("Reset clears an old 'level changed' note", app.pct_note == "")
app.pct_note = "level changed: Reset to start again on the new level"
app._handle(blank._replace(where="Reading", candidates=[(47850, 50.0)], confident=True))
check("a bar with its number clears it too", app.pct_note == "")
app.reset()
app.reader.level_failures, app.reader.level = m.LEVEL_FAILS_FOR_FALLBACK, None
app.session.start(m.time.monotonic())
app._handle(pct)
real_ask2 = m.simpledialog.askinteger
m.simpledialog.askinteger = lambda *a, **k: 31
app.type_level()
m.simpledialog.askinteger = real_ask2
check("typing the level in doesn't stop a session that is running", app.session.on and app.tracker.req == 108480
      and app.tracker.level_source == "typed")
app.reset()
# the number with a tilde in front fits the big number and the tile at every size, even when huge
fit_ok, fit_notes = True, []
t.estimated = True
for scale in m.UI_SCALES:
    app.cfg["ui_scale"] = scale
    app._apply_ui_scale()
    t.rate = lambda: 99999999.0
    t.rolling_rate = lambda: 99999999.0
    app.set_compact(False)
    app._update_display()
    root.update()
    limit = app.theme.gfx.px(ui_main.INNER - 14)
    shown = [app.view.hero.bbox(i) for i in app.view.hero._digits if app.view.hero.itemcget(i, "state") == "normal"]
    hero_right = max((box[2] for box in shown if box), default=0)
    tile = app.view.tiles["Last 5 min"]
    x0, y0, x1, y1 = tile.bbox(tile._v)
    if x1 > tile.winfo_width() + 1:
        fit_ok = False
        fit_notes.append(f"{scale}% tile ends {x1} of {tile.winfo_width()}")
    if hero_right and hero_right > limit:
        fit_ok = False
        fit_notes.append(f"{scale}% big number ends {hero_right} of {limit}")
check(f"a huge estimate fits the tile (and the big number) at every size ({'; '.join(fit_notes) or 'ok'})", fit_ok)
t.estimated = False
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

# carrying the level over when the number goes: only for the same character (a print that matches)
exact = blank._replace(where="Reading", candidates=[(47850, 50.0)], confident=True)


def carry(before, after):
    app.reset()
    app.reader.name_print, app.reader.level, app.reader.level_failures = before, None, 0
    for _ in range(3):
        app._handle(exact)
    app.reader.name_print = after
    app._handle(pct)
    return app.tracker.req


check("the level carries over when the name print is the same", carry(print_of("Fox"), print_of("Fox")) == 95700)
check("...but not when no print could be taken (the character can't be told)", carry(None, None) is None)
check("...nor when only one was taken", carry(print_of("Fox"), None) is None and carry(None, print_of("Fox")) is None)
check("...and the status says it is waiting for the badge", app.level_wait and "Reading your level" in app.status.get())
carry(print_of("Fox"), print_of("Fox"))
app.session.start(m.time.monotonic())
app._handle(pct)
check("a carried-over level is said to be known from before", "known from before" in app.status.get())
app.reset()
app.reader.mode, app.reader.level_failures = "region", m.LEVEL_FAILS_FOR_FALLBACK
app._handle(pct)
check("in a selected area the status says the badge can't be read there",
      "Selected area" in app.status.get() and app._can_type_level())
app.reader.mode = "window"
app.reader.name_print = None
app.found_at, app.pct_note = None, ""
app.tracker.reset()
try:
    app._status_for("")
    check("the status doesn't crash before the first EXP reading (found_at unset)", True)
except TypeError:
    check("the status doesn't crash before the first EXP reading (found_at unset)", False)
app.reset()

# ---- A13: the waiting notes of a %-only bar are not misreads
def pct_at(p, level=30):
    return pct._replace(candidates=[(None, p)], level=(level, "badge"))


def start_pct(level=30):
    app.reset()
    app.reader.name_print = None
    app.session.start(m.time.monotonic())
    for p in (50.0, 50.0, 51.0, 51.0):
        app._handle(pct_at(p, level))


start_pct()
for _ in range(12):                                     # the % wrapped and the badge still shows the old level
    app._handle(pct_at(0.3))
check(f"A13-2: waiting for the badge after a wrap isn't a misread ({app.rejected}, {app.status.get()!r})",
      app.rejected == 0 and "trouble" not in app.status.get() and app.reader.box is not False)
start_pct()
for _ in range(12):
    app._handle(pct_at(51.5, 31))                       # the badge says another level and nothing wrapped
check(f"A13-2: 'level changed' isn't a misread either, and the status says to press Reset ({app.rejected})",
      app.rejected == 0 and "Press Reset" in app.status.get())
app.reset()
for _ in range(12):
    app._handle(pct_at(50.0, 150))
check(f"A13-2: a level over the table is said as it is, not as trouble reading ({app.status.get()!r})",
      app.rejected == 0 and "150" in app.status.get() and "trouble" not in app.status.get())

# ---- A13-4: a badge covered by a window counts as a failure, so the fallback is offered and the status says why
app.reset()
app.reader.level, app.reader.level_failures, app.reader.next_level_read = None, 0, 0.0
app.reader.exp_anchor, app.reader.gh = (100, 100), 11
real_blocked = app.reader._blocked
app.reader._blocked = lambda *a: True
for _ in range(m.LEVEL_FAILS_FOR_FALLBACK):
    app.reader.next_level_read = 0.0
    app.reader._read_level(None, (0, 0, 1000, 800))
app.reader._blocked = real_blocked
app._handle(pct._replace(level=None))
check(f"A13-4: a covered badge ends in the Level fallback, and the status says the window is in the way "
      f"({app.reader.level_failures}, {app.status.get()!r})",
      app._can_type_level() and "covering the level badge" in app.status.get())
app.reader.level_failures = 0

# ---- A13-5: a badge level doesn't outlive a place change or a reset; nor a forgotten level
app.reader.level = 30
app.reader.use_region((0, 0, 10, 10))
check("A13-5: switching to a selected area forgets the badge level", app.reader.level is None)
app.reader.level = 30
app.reader.use_window()
check("...and switching back does too", app.reader.level is None)
app.reader.level = 30
app.reset()
check("...and Reset", app.reader.level is None)
start_pct()
app.reader.level = 30
for _ in range(m.ExpTracker.WRAP_WAIT_READS):
    app._handle(pct_at(0.3))
check(f"...and so does the tracker forgetting it after a wrap nobody explained (reader level {app.reader.level})",
      app.tracker.req is None and app.reader.level is None)
app.reset()

# ---- A13-8: a reading already queued when another character is found doesn't bring the old level back
start_pct()
app.reader.name_print = print_of("Fox")
app._handle(pct_at(51.0))
app.reader.name_print = print_of("Pip2")
app._handle(pct_at(51.0))
app.reader.name_print = print_of("Pip2")
app._handle(pct_at(51.0))                                  # (the switch is seen here)
app._handle(pct_at(51.0))                                  # a reading from before it, still carrying Lv 30
check(f"A13-8: a level from before the character switch isn't taken again (req {app.tracker.req})",
      app.tracker.req is None)
app.reset()

# ---- A13-10: an exact "Current" reading has no approx sign, even after a %-only one
shown = {}
real_show = app.view.show
app.view.show = lambda **kw: shown.update(kw)
app.reset()
app.tracker.estimated, app.tracker._format = True, "exact"
app.tracker.exp, app.tracker.pct, app.tracker.req = 40000, 41.8, 95700
app._update_display()
cur_exact = shown["details"]["Current"]
app.tracker._format = "percent"
app._update_display()
cur_pct = shown["details"]["Current"]
app.view.show = real_show
app.reset()
check(f"A13-10: 'Current' is only approximate while the bar shows only a % ({ascii(cur_exact)}, {ascii(cur_pct)})",
      cur_exact and not cur_exact.startswith(m.APPROX) and cur_pct.startswith(m.APPROX))

# ---- A13-7: a covered name row says so; a partly covered one is judged by what shows
def half(name):
    pr = print_of(name).copy()
    pr[:, pr.shape[1] // 2:] = 0
    return pr


start_pct()
app.reader.name_print = print_of("beanguri")
app._handle(pct_at(51.0))
app.reader.name_print = None
app._handle(pct_at(51.0))
check(f"A13-7: nothing of the name showing says the name is covered, and keeps tracking ({app.status.get()!r})",
      "covered" in app.status.get() and app.tracker.req == 95700)
for _ in range(3):
    app.reader.name_print = half("beanguri")
    app._handle(pct_at(51.0))
check(f"...half of the same name showing keeps the session and the level, and says so ({app.status.get()!r})",
      app.tracker.req == 95700 and "hidden" in app.status.get() and app.other_char_at is None)
for _ in range(3):
    app.reader.name_print = half("soymiso")
    app._handle(pct_at(51.0))
check("...half of another name resets, as a clearly different character", app.tracker.req is None and app.other_char_at is not None)
app.reset()
start_pct()
app.reader.name_print = print_of("beanguri")
app._handle(pct_at(51.0))
for _ in range(3):
    mostly = print_of("beanguri").copy()
    mostly[:, :int(mostly.shape[1] * 0.95)] = 0
    app.reader.name_print = mostly
    app._handle(pct_at(51.0))
check(f"...most of the name covered (too little to tell) keeps the session and says it's covered ({app.status.get()!r})",
      app.tracker.req == 95700 and "covered" in app.status.get() and app.other_char_at is None)
app.reset()

# ---- Report a bug > Send to the developer
def new_windows(before):
    return [c for c in app.root.winfo_children() if c not in before and c.winfo_class() == "Toplevel"]


before = set(app.root.winfo_children())
choice = app.report_bug()
root.update()
check("Report a bug offers both ways: Send to the developer and Report on GitHub",
      button(choice, "Send to the developer") and button(choice, "Report on GitHub") and button(choice, "Cancel"))
button(choice, "Cancel")[0].command()
root.update()
check("Cancel closes the choice", not choice.winfo_exists())

posted = []
real_post = m.post_report
m.post_report = lambda body, timeout=15: (posted.append(body), "ok")[1]
real_details = app._report_details
app._report_details = lambda: "Maple Classic Exp Overlay bug report details\nEXP bar: found\nreading C:" + chr(92) + "Games" + chr(92) + "x.dll"
try:
    before = set(app.root.winfo_children())
    app.send_report()
    root.update()
    dlg = new_windows(before)[0]
    check("Send to the developer opens the dialog with the text box, the note and Send / Cancel",
          button(dlg, "Send") and button(dlg, "Cancel") and any("No pictures are sent" in t for t in labels(dlg))
          and any("What went wrong?" in t for t in labels(dlg)))
    check("the 'What will be sent' fold-out starts closed", not dlg.fold.winfo_ismapped())
    button(dlg, "What will be sent  " + chr(0x25b8))[0].command()
    root.update()
    shown_text = dlg.sent_text.get("1.0", "end-1c")
    check("opening the fold-out shows the exact text that will be sent", dlg.fold.winfo_ismapped() and shown_text == dlg.details
          and "EXP bar: found" in shown_text)

    button(dlg, "Send")[0].command()
    root.update()
    check("Send with an empty box is refused and nothing is posted", not posted and "write a sentence" in dlg.status.cget("text"))
    dlg.message.insert("1.0", "see www.spam.org")
    button(dlg, "Send")[0].command()
    root.update()
    check("a link in the text is refused", not posted and "leave out links" in dlg.status.cget("text"))
    dlg.message.delete("1.0", "end")
    dlg.message.insert("1.0", "x" * 2001)
    dlg.typed()
    button(dlg, "Send")[0].command()
    root.update()
    check("more than 2,000 characters is refused, and the counter shows it",
          not posted and "2,001 / 2,000" in dlg.counter.cget("text"))
    dlg.message.delete("1.0", "end")
    dlg.message.insert("1.0", "It stopped reading after I changed maps.")
    dlg.typed()
    check("the live counter counts what is typed", "40 / 2,000" in dlg.counter.cget("text"))
    check("with sharing blocked (testing) it won't send", (button(dlg, "Send")[0].command(), root.update(), not posted)[2]
          and "off while testing" in dlg.status.cget("text"))

    def wait_until(done, seconds=5):
        """Keeps the window going until done() (the app's own queue handler answers the send), at most `seconds`."""
        end = time.monotonic() + seconds
        while not done() and time.monotonic() < end:
            root.update()
            time.sleep(0.05)

    os.environ["MAPLE_EXP_ALLOW_SHARE"] = "1"
    button(dlg, "Send")[0].command()
    wait_until(lambda: "Sent, thank you" in dlg.status.cget("text"))
    check("Send posts the four fields, with the health ID and the scrubbed text",
          len(posted) == 1 and set(posted[0]) == {"id", "version", "message", "details"}
          and posted[0]["details"] == dlg.details and posted[0]["message"] == "It stopped reading after I changed maps."
          and posted[0]["version"] == m.VERSION and m.healthlog.good_id(posted[0]["id"]) and posted[0]["id"] == app.cfg["health_id"])
    check("...then says thank you and counts it", "Sent, thank you" in dlg.status.cget("text") and m.reports_sent_today(app.cfg) == 1)
    dlg.destroy()

    # A05: the site counts UTF-16 units (an emoji is 2), and the window keeps answering while a send is slow
    app.cfg.pop("reports_sent", None)
    before = set(app.root.winfo_children())
    app.send_report()
    root.update()
    d_slow = new_windows(before)[0]
    d_slow.message.insert("1.0", "\U0001F600" * 1001)
    d_slow.typed()
    check("the counter counts an emoji as 2, like the site, and turns red over 2,000",
          "2,002 / 2,000" in d_slow.counter.cget("text") and d_slow.counter.cget("fg") == m.BAD)
    d_slow.message.delete("1.0", "end")
    d_slow.message.insert("1.0", "slow site")
    slow_count = len(posted)
    m.post_report = lambda body, timeout=15: (time.sleep(1.5), "ok")[1]
    t0 = time.monotonic()
    button(d_slow, "Send")[0].command()
    stamps = [time.monotonic()]
    while "Sent, thank you" not in d_slow.status.cget("text") and time.monotonic() - t0 < 15:
        root.update()
        stamps.append(time.monotonic())
        time.sleep(0.02)
    gap = max(b - a for a, b in zip(stamps, stamps[1:])) if len(stamps) > 1 else 99
    # (a frozen window would sit the whole 1.5 s send in one update; a slow test machine only makes updates fewer)
    check(f"while a slow send is waiting the window keeps answering (no freeze; longest pause {gap:.2f} s), then says thank you",
          gap < 1.0 and stamps[-1] - t0 >= 1.4 and "Sent, thank you" in d_slow.status.cget("text") and len(posted) == slow_count)
    m.post_report = lambda body, timeout=15: (posted.append(body), "ok")[1]
    d_slow.destroy()
    app.cfg.pop("reports_sent", None)

    for n in (2, 3):
        app.cfg["reports_sent"] = {"day": m.healthlog.utc_day(time.time()), "n": n}
        before = set(app.root.winfo_children())
        app.send_report()
        root.update()
        d2 = new_windows(before)[0]
        d2.message.insert("1.0", "again")
        button(d2, "Send")[0].command()
        root.update()
        if n == 2:
            wait_until(lambda: "Sent, thank you" in d2.status.cget("text"))
            check("a 3rd report on the day is still allowed", len(posted) == 2)
        else:
            check("a 4th report on the day is refused locally, in plain words",
                  len(posted) == 2 and "sent 3 reports today" in d2.status.cget("text"))
        d2.destroy()

    # B03: the number the site gave is shown with the two ways to reach the developer; without it, no number
    for number, expect in ((17, True), (None, False)):
        app.cfg["reports_sent"] = {"day": "2000-01-01", "n": 0}
        before = set(app.root.winfo_children())
        app.send_report()
        root.update()
        d5 = new_windows(before)[0]
        done = m.ReportOutcome("ok")
        done.n = number
        app._report_done(d5, done)
        root.update()
        text = d5.status.cget("text")
        check(f"a sent report {'#17 shows its number and Want a reply?' if expect else 'without a number shows none'}",
              ("report #17" in text and "Want a reply" in text) == expect and "Sent, thank you" in text)
        check(f"...{'with a GitHub issues and a Reddit button' if expect else 'and no extra buttons'}",
              (button(d5, "GitHub issues") != [] and button(d5, "Reddit") != []) == expect)
        if expect:
            opened = []
            real_open = m.webbrowser.open
            m.webbrowser.open = lambda url, *a, **k: opened.append(url)
            try:
                check("...nothing opens until a click", not opened)
                button(d5, "Reddit")[0].command()
                button(d5, "GitHub issues")[0].command()
            finally:
                m.webbrowser.open = real_open
            check("...Reddit opens the owner's page, GitHub issues the repo's", opened == [m.REPORT_REDDIT, f"https://github.com/{m.GITHUB_REPO}/issues"])
        d5.destroy()
    app.cfg.pop("reports_sent", None)

    for outcome, words in (("later", "connected to the internet"), ("message", "leave out links"), ("limit", "sent 3 reports")):
        app.cfg["reports_sent"] = {"day": "2000-01-01", "n": 0}
        before = set(app.root.winfo_children())
        app.send_report()
        root.update()
        d3 = new_windows(before)[0]
        app._report_done(d3, outcome)
        check(f"a '{outcome}' answer is explained in plain words and Send works again",
              words in d3.status.cget("text") and d3.send_button._enabled)
        d3.destroy()
    app.cfg.pop("reports_sent", None)

    before = set(app.root.winfo_children())
    app.send_report()
    root.update()
    d4 = new_windows(before)[0]
    button(d4, "Cancel")[0].command()
    root.update()
    check("Cancel closes the dialog without sending", not d4.winfo_exists() and len(posted) == 2)
finally:
    m.post_report = real_post
    app._report_details = real_details
    os.environ.pop("MAPLE_EXP_ALLOW_SHARE", None)

if SCREEN:
    check(f"every window the tests opened is on the second screen (strays: {stray_windows(SCREEN)})", not stray_windows(SCREEN))
check("the typeface is the bundled one, or it fell back cleanly", app._own_fonts in (True, False) and bool(app.theme.fam["extra"]))
app.close()

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
