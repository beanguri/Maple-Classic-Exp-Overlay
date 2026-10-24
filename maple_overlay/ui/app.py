"""The overlay's windows (the tracker, the log, Settings...) and what happens when you press a button."""
import hashlib
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog, ttk

import cv2
import mss
from PIL import Image, ImageDraw, ImageFont, ImageTk

from maple_overlay.config import (
    APP_DIR, ASSETS, CONFIG_PATH, DATA_DIR, DEFAULTS, ERROR_LOG_PATH, EXTRA_KEYS, GITHUB_REPO, HOME_DIR,
    HOTKEY_ACTIONS, ICON_PATH, LEVEL_FAILS_FOR_FALLBACK, load_config, log_error, MAP_BLIND_SECONDS,
    MAP_HIDDEN_SECONDS, MAX_TRANSPARENCY, move_old_data, NOTE_MARK, NOTE_MAX, OLD_TITLES, PICTURE_WIDTHS,
    PLATFORM, RATE_HZ_DEFAULT, RATE_HZ_MAX, RATE_HZ_MIN, RELEASES_PAGE, RUN_SECONDS, save_config,
    SESSION_LOG_PATH, SHAPE_BLURBS, SHAPE_LABELS, SHAPES, STATS_SITE, UI_HZ_DEFAULT, UI_HZ_MAX, UI_HZ_MIN,
    UI_SCALES, VERSION, WHATS_NEW, WINDOW_TITLE, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP,
)
from maple_overlay.core import session_stats
from maple_overlay.core.levels import EXP_TABLE, level_for_req
from maple_overlay.core.session import Session
from maple_overlay.logs import log_details, report
from maple_overlay.logs.autolog import AutoLog
from maple_overlay.logs.entries import (
    clean_note, fmt_logged, format_entry, group_row, log_row, note_of, notes_of, party_groups, party_label,
)
from maple_overlay.logs.report import Health, Recent
from maple_overlay.platforms import hotkey_text
from maple_overlay.ui.format import APPROX, approx_num, fmt_dur, fmt_eta, fmt_num, fmt_short
from maple_overlay.ui.gfx import INK, TEXT_DIM
from maple_overlay.ui.kit import (
    ACCENT, BG, DIM, FAINT, FG, LINE, LIST_BG, LIST_BG2, LIST_PART, RAISED, SELECT, setup_ttk,
)
from maple_overlay.ui.main_view import Button, MainView, PopupMenu, Slider, Theme, Toggle
from maple_overlay.ui.region_selector import RegionSelector
from maple_overlay.updates import check_for_update, post_runs, version_key
from maple_overlay.vision import engine, level_ocr, ocr, potions
from maple_overlay.vision.engine import find_tesseract
from maple_overlay.vision.exp_bar import DigitTemplates
from maple_overlay.vision.maps import is_town
from maple_overlay.vision.screen import grab
from maple_overlay.vision.screen_reader import ScreenReader


# --------------------------------------------------------------------------- app
FIELD = RAISED              # inputs and buttons

# The window's fonts: (family, size at 100%, weight, underline). Settings > Size scales them all.
FONTS = {"small": ("Manrope", 9, "normal", False), "text": ("Manrope", 10, "normal", False),
         "link": ("Manrope SemiBold", 10, "normal", False), "row": ("Manrope", 10, "normal", False),
         "icon": ("Segoe UI", 12, "normal", False), "value": ("Manrope Bold", 11, "normal", False),
         "next": ("Manrope SemiBold", 11, "normal", False), "title": ("Manrope Bold", 13, "normal", False),
         "caps": ("Manrope SemiBold", 9, "normal", False), "strong": ("Manrope SemiBold", 10, "normal", False),
         "big": ("Manrope ExtraBold", 32, "normal", False)}

FONT_FILES = ("Manrope.ttf", "ManropeSemiBold.ttf", "ManropeBold.ttf", "ManropeExtraBold.ttf")

FALLBACK_FONT = {"Manrope": "Segoe UI", "Manrope SemiBold": "Segoe UI Semibold", "Manrope Bold": "Segoe UI Semibold",
                 "Manrope ExtraBold": "Segoe UI Semibold"}

LOG_HINT = "Click a line to write a note about it. Right-click for details, to copy, rename or delete it."

class App:
    def __init__(self, root, templates):
        self.root = root
        self._fresh = not CONFIG_PATH.exists()          # a first install: it picks a compact shape
        self.cfg = {**DEFAULTS, **load_config()}
        mode = self.cfg.get("mode", "window")
        self.reader = ScreenReader(templates, mode, self.cfg.get("region"))
        saved = self.cfg.get("mesos_box")
        if isinstance(saved, list) and len(saved) == 6:
            self.reader.saved_mesos = tuple(saved)
        self.session = Session(self.cfg["gap_seconds"])
        self.tracker, self.meso = self.session.exp, self.session.meso
        self.party = 1
        self._party_seen = (None, 0)
        self.last_mesos = None
        self.rejected = 0
        self.started, self.cpu0 = time.monotonic(), time.process_time()
        self.recent, self.health = Recent(40, self.started), Health()      # for bug reports
        self.autolog = AutoLog(self.cfg["gap_seconds"])
        self.stats = session_stats.SessionStats(session_stats.CsvLog(str(SESSION_LOG_PATH), on_error=log_error))
        self.stats.on_debug = self._split_debug
        self.life = dict.fromkeys(EXTRA_KEYS, 0)        # counters since the app started, for the log (see AutoLog.tick extras)
        self.stats.on_split = lambda own, bonus: self.life.update(own_exp=self.life["own_exp"] + own,
                                                                  bonus_exp=self.life["bonus_exp"] + bonus)
        self._stat_wins = {}                # the Session window, by name
        self.party_view = "closed"          # Solo / Party: "closed", "docked" (in the main window) or "floating"
        self.party_panel = self.party_win = self._party_refresh = self._party_job = None
        self._party_at = None
        self.map_name = None
        self.potion_bar = ()                # what the potion bar showed at the last look: potions.Slot tuple
        self.in_town = False                # on a town map: nobody farms there, so nothing is logged
        self.level = None
        self.note = None
        self.last_logged = None
        self.skip_log = False               # "Don't log" is on: this run isn't logged
        self._sharing, self._next_share = False, 0.0       # sending runs to the site
        self.job = None                     # the class, from the status bar
        self.map_read = None                # the map name exactly as read
        self.map_hidden, self.map_back_at = False, None     # the minimap's name out of sight for a few seconds
        self.found_at = None                # when the EXP bar was first read for this level
        self.level_wait = False             # a %-only bar is waiting for its level
        self.pct_note = ""                  # what the tracker last said about a %-only reading
        self._char_print = None             # the name row's print when the level was last known (memory only)
        self._char_seen = None              # the newest print looked at, so each is counted once
        self._char_diff = 0                 # prints in a row that differ from _char_print
        self._same_char = False             # the newest print matches the one from when the level was known
        self.other_char_at = None           # when another character was last noticed (the status says so for a while)
        self.level_btn = None               # Settings > Level fallback (enabled only when it can help)
        self.level_up_at = None
        self.died_at = None
        self.log_open = False
        self.settings_win = self.prices_win = None
        self._price_table = None
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.last_capture = None

        # The typeface is bundled with the overlay and made available to this program only.
        self._own_fonts = PLATFORM.register_fonts([ASSETS / "fonts" / n for n in FONT_FILES])
        family_of = lambda f: f if self._own_fonts else FALLBACK_FONT.get(f, f)
        self.fonts = {name: tkfont.Font(root, underline=underline, **dict(zip(("family", "weight"),
                                                                               PLATFORM.font(family_of(family), weight))))
                      for name, (family, _, weight, underline) in FONTS.items()}
        pos = self.cfg.get("pos")
        self._dpi = (PLATFORM.screen_dpi(pos[0] + 40, pos[1] + 15) if pos else None) or root.winfo_fpixels("1i")
        self._zoom = self.cfg["zoom"]
        self._ref = self._last_size = None
        self._apply_ui_scale()
        self.hotkey_note = None
        self.hotkeys = PLATFORM.create_hotkeys(lambda action: self.q.put(("hotkey", action)))
        self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        self.compact = False

        root.title(WINDOW_TITLE)
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass
        root.report_callback_exception = lambda kind, exc, tb: self._oops("a button or menu", exc)
        root.configure(bg=BG)
        root.resizable(True, True)                          # (drag an edge: everything in the window zooms with it)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._apply_window_settings()
        self._restore_position()

        setup_ttk(root)
        PLATFORM.style_window(root, INK, TEXT_DIM)          # a title bar in the overlay's own colours
        self._trend, self._trend_at, self._start_text = deque(maxlen=60), 0.0, "Start"
        self.status = tk.StringVar()
        self.status.trace_add("write", lambda *_: self.view.show(message=self.status.get()) if hasattr(self, "view") else None)
        root.columnconfigure(1, weight=1)                 # the log gets any extra room
        root.rowconfigure(0, weight=1)
        self._build_view()
        self._show_skip_log()
        for delay in (0, 150, 800):                        # (Windows can reset it while the window is first shown)
            root.after(delay, lambda: PLATFORM.style_window(root, INK, TEXT_DIM))

        self._build_log_panel()
        root.bind("<Configure>", self._on_root_configure, add="+")
        root.after(800, self._take_ref)
        if self.cfg["compact"]:
            self.set_compact(True)
        elif self.cfg.get("log_open"):
            self.toggle_log()
        if self.cfg.get("party_view") in ("docked", "floating"):
            self.root.after(300, lambda: self.set_party_view(self.cfg["party_view"], remember=False))

        if not templates:
            self.status.set("Some of the program's files are missing, so EXP reading is less "
                            "reliable. Please download it again.")
        else:
            self.status.set("Looking for the MapleStory window..." if mode == "window"
                            else "Using your selected area.")

        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        self._message_thread = threading.Thread(target=self._message_worker, daemon=True)
        self._message_thread.start()
        root.after(self._tick_ms(0.5), self._pump)
        root.after(self._ui_ms(), self._refresh)
        if self.cfg["check_updates"]:
            self.check_updates()                       # only now, when it starts
        root.after(1500, self._first_run)              # (once: the compact shape or what's new, then the sharing question)

    # ---- the main window
    def _build_view(self):
        """(Re)builds the main window at the current size: it's drawn smoothly for each screen's scaling
        and for Settings > Size, so a new size means new pictures."""
        if getattr(self, "view", None):
            self.view.destroy()
        self.theme = Theme(self.root, self._px_per_pt * 0.75, ASSETS / "fonts", self._own_fonts, PLATFORM.font)
        callbacks = dict(settings=self.open_settings, compact=lambda: self.set_compact(not self.compact),
                         log=self.toggle_log, party=self.toggle_party, start=self.toggle, reset=self.reset, logging=self._logging_clicked,
                         best=self.open_best_maps, menu=self._open_menu, details=self._details_changed)
        self.view = self.main = MainView(self.root, self.theme, callbacks, details=bool(self.cfg.get("details")))
        self.view.grid(row=0, column=0, sticky="n")
        if self.compact:
            self.view.set_compact(True, self.cfg["compact_shape"])
        self._set_start(self._start_text)
        self.view.log_btn.set(kind="secondary" if self.log_open else "ghost")
        self.view.show(message=self.status.get())
        self._show_skip_log()

    def _style_frame(self):
        """The title bar in the overlay's colours. (Windows resets it when the frame changes, e.g. when the
        log opens or it goes compact, so this runs again a moment after.)"""
        for delay in (30, 300):
            self.root.after(delay, lambda: PLATFORM.style_window(self.root, INK, TEXT_DIM))

    def _details_changed(self, is_open):
        self.cfg["details"] = is_open
        save_config(self.cfg)
        self.root.after(50, self._settle)

    def _open_menu(self, anchor):
        PopupMenu(self.theme, anchor, [
            ("Follow the game" if self.reader.mode == "region" else "Select area", None, self.toggle_area),
            ("Session window", None, lambda: self.open_stat_window("session")),
            ("Solo / Party", None, self.toggle_party),
            ("Open session log (CSV)", None, self.open_session_log),
            ("Stop collecting EXP messages" if self.reader.sample_dir else "Collect EXP message samples", None, self.toggle_samples),
            ("View capture", None, self.view_capture)])

    def _logging_clicked(self, on):
        if self.skip_log == on:                           # the switch and the state disagree: flip
            self.toggle_skip_log()

    # ---- log panel
    def _build_log_panel(self):
        p = self.log_panel = tk.Frame(self.root, bg=BG)
        tk.Label(p, text="EXP log", bg=BG, fg=FG, font=self.fonts["title"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.log_status = tk.StringVar()
        tk.Label(p, textvariable=self.log_status, bg=BG, fg=DIM, font=self.fonts["text"],
                 justify="left", anchor="w").grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 10))

        # A compact table: one row per day, map, level and class, solo or in a party (click a
        # party row for a row per party size). Drag the lines between the column titles to
        # resize a column; widths are remembered.
        cell, head = self.fonts["text"], self.fonts["small"]
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Log.Treeview", background=LIST_BG, fieldbackground=LIST_BG, foreground=FG,
                        font=cell, rowheight=int(cell.metrics("linespace") * 1.8), borderwidth=0)
        style.configure("Log.Treeview.Heading", background=BG, foreground=DIM, relief="flat",
                        borderwidth=0, font=self.fonts["caps"], padding=(0, 6))
        style.map("Log.Treeview", background=[("selected", SELECT)], foreground=[("selected", ACCENT)])
        style.map("Log.Treeview.Heading", background=[("active", BG)])
        # (key, title, widest usual value, alignment): starting widths are measured in the
        # real font, so nothing is cut off at any screen scaling.
        columns = (("date", "Date", "Sep 30", "w"), ("map", "Map", "The Swamp of Despair II", "w"),
                   ("lv", "Lv", "99", "center"), ("class", "Class", "Crossbowman", "w"),
                   ("party", "Party", "2–6ppl ▸", "center"),
                   ("time", "Time", "12h 30m", "center"),
                   ("exp_total", "EXP", "8,888,888", "e"), ("mesos_total", "Mesos", "88,888,888", "e"),
                   ("exp", "EXP/hr", "888,888", "e"), ("mesos", "Mesos/hr", "888,888", "e"),
                   ("net", "Net/hr", "888,888", "e"), ("note", "Note", NOTE_MARK, "center"))
        # Saved widths are in points, so they look the same on screens with different scaling
        # (older versions saved pixels: those are used as they are, once).
        saved = {k: round(v * self._px_per_pt) for k, v in (self.cfg.get("log_columns_pt") or {}).items()
                 if type(v) in (int, float)} or self.cfg.get("log_columns") or {}
        pad = cell.measure("00")
        self._log_titles = {c[0]: c[1] for c in columns}
        tree = self.log_tree = ttk.Treeview(p, columns=[c[0] for c in columns], show="headings",
                                            style="Log.Treeview", height=11, selectmode="browse")
        for key, title, sample, anchor in columns:
            width = max(cell.measure(sample), head.measure(title)) + pad
            if isinstance(saved.get(key), int) and 10 <= saved[key] <= 2000:
                # (Party is never narrower than a merged party row: older versions' Party
                # column was sized for "6ppl".)
                width = max(saved[key], width) if key == "party" else saved[key]
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width, minwidth=pad, anchor=anchor, stretch=False)
        tree.tag_configure("odd", background=LIST_BG2)
        tree.tag_configure("part", background=LIST_PART, foreground="#b3b8c0")   # one party size
        tree.tag_configure("edited", foreground="#6b7079")      # changed outside the tracker
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(p, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        # Notes: a place for the player's own notes on the selected line. They're only ever kept
        # in the log file on this PC (never shared), and saved as they're typed.
        self._note_key = self._note_entries = self._note_job = None
        self._note_dirty, self._note_label = False, ""
        nb = tk.Frame(p, bg=BG)
        nb.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        nb.columnconfigure(0, weight=1)
        self.note_title = tk.Label(nb, text="", bg=BG, fg=DIM, font=head, anchor="w", justify="left")
        self.note_title.grid(row=0, column=0, sticky="we")
        self.note_text = tk.Text(nb, height=3, wrap="word", undo=True, maxundo=100, bg=LIST_BG, fg=FG,
                                 insertbackground=FG, relief="flat", font=cell, padx=8, pady=6,
                                 highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        self.note_text.grid(row=1, column=0, sticky="we", pady=(3, 0))
        self.note_text.bind("<<Modified>>", self._note_modified)
        self.note_text.bind("<FocusIn>", lambda e: self.hotkeys.pause(True))      # typing "!" isn't Shift+1
        self.note_text.bind("<FocusOut>", lambda e: (self.hotkeys.pause(False), self._note_flush()))
        self._note_show(None)
        self.log_hint = tk.StringVar(value=LOG_HINT)
        hint = tk.Label(p, textvariable=self.log_hint, bg=BG, fg=DIM, font=head, justify="left", anchor="w")
        hint.grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))

        def fit(e):
            hint.config(wraplength=max(120, e.width - 8))
            self.note_title.config(wraplength=max(120, e.width - 8))
        p.bind("<Configure>", fit)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(2, weight=1)
        tree.bind("<Button-3>", self._log_menu)
        tree.bind("<Double-Button-1>", self._log_details_click)
        tree.bind("<<TreeviewSelect>>", self._log_selected)
        tree.bind("<Button-1>", self._log_click)
        tree.bind("<<TreeviewOpen>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        tree.bind("<<TreeviewClose>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        self.shown, self.groups, self.open_groups = {}, {}, set()
        self._fill_log()

    def toggle_log(self, remember=True):
        if self.log_open:
            self._note_flush()
            self._remember_log_size()                   # before it closes
        self.log_open = not self.log_open
        root = self.root
        if self.log_open:
            # With the log open the window can be resized (e.g. for long map names).
            self.log_panel.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(0, 12), pady=(10, 8))
            root.update_idletasks()
            root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
            size = self.cfg.get("log_size")
            root.geometry(f"{size[0]}x{size[1]}" if size else "")
        else:
            self.log_panel.grid_remove()
            root.minsize(1, 1)
            root.geometry("")                           # back to just the tracker's size
        self.view.log_btn.set(kind="secondary" if self.log_open else "ghost")
        self._style_frame()
        self._settle()
        if remember:
            self.cfg["log_open"] = self.log_open
            save_config(self.cfg)

    # ---- compact mode and size
    def set_compact(self, on):
        """Compact mode: just EXP/hr, when you'll level, the last 5 minutes and mesos/hr on one
        line, and Start/Pause, in a much smaller window. The Compact button (it says Full in
        compact mode), Shift+4, or double-clicking the big number switches it."""
        self.compact = self.cfg["compact"] = on
        if self.party_view == "docked":
            (self.party_panel.grid_remove if on else self.party_panel.grid)() if self.party_panel else None
        if on and self.log_open:
            self.toggle_log(remember=False)                 # out of the way, but remembered
        self.view.set_compact(on, self.cfg["compact_shape"])
        self._style_frame()
        if not on and self.cfg.get("log_open"):
            self.toggle_log(remember=False)
        else:
            self.root.geometry("")
        self._settle()
        save_config(self.cfg)

    def set_shape(self, shape):
        """Settings > Compact shape: Square, Wide or Bar. Only compact mode looks different."""
        if shape not in SHAPES:
            return
        self.cfg["compact_shape"] = shape
        if self.compact:
            self.view.set_compact(True, shape)
            self._style_frame()
            self.root.geometry("")
            self._settle()
        save_config(self.cfg)

    def _apply_ui_scale(self, zoomed=False):
        """Sizes every font by Settings > Size, the window's zoom and the screen the window is on (screens can
        have different Windows scaling), and the log's rows and columns with them. `zoomed`: the window was
        just dragged to a new size, so it keeps that size (the log takes what the tracker doesn't)."""
        old, self._px_per_pt = getattr(self, "_px_per_pt", None), self.cfg["ui_scale"] / 100 * self._zoom * self._dpi / 72
        for name, (_, size, _, _) in FONTS.items():
            # In pixels (negative), never under 9 so the smallest text stays readable.
            self.fonts[name].configure(size=-max(9, round(size * self._px_per_pt)))
        if old is not None and getattr(self, "view", None):
            self._build_view()
        if old is None or not hasattr(self, "log_tree"):    # at start, before the window is built
            return
        self._fit_settings()
        ttk.Style(self.root).configure("Log.Treeview", rowheight=int(self.fonts["text"].metrics("linespace") * 1.8))
        head, pad = self.fonts["caps"], self.fonts["text"].measure("00")
        for c in self.log_tree["columns"]:
            width = round(self.log_tree.column(c, "width") * self._px_per_pt / old)
            # (text doesn't shrink as far as the window does, so a column keeps room for its heading)
            self.log_tree.column(c, width=max(10, width, head.measure(self._log_titles.get(c, "")) + pad))
        if getattr(self, "party_view", "closed") != "closed":
            self.set_party_view(self.party_view, remember=False)     # (its fonts and spacing are made for one size)
        if not zoomed:
            self.cfg["log_size"] = None                     # a size from before may not fit any more
        if self.log_open:
            self.root.update_idletasks()
            self.root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
        if not (zoomed and self.log_open):
            self.root.geometry("")
        else:                                                # (the log's columns grew with the text: the window gets the room they need)
            need = (self.main.winfo_reqwidth() + sum(self.log_tree.column(c, "width") for c in self.log_tree["columns"])
                    + self.theme.px(60))
            if self.root.winfo_width() < need and need < self.root.winfo_screenwidth():
                self.root.geometry(f"{need}x{self.root.winfo_height()}")
        self._settle(80)

    # ---- zooming with the window: drag its edge and everything in it grows or shrinks together
    def _settle(self, delay=300):
        """Takes the window's size as the one that goes with the current zoom, once a change to the layout
        (the log, the compact shape, a panel...) has finished."""
        if getattr(self, "_settle_job", None):
            try:
                self.root.after_cancel(self._settle_job)
            except (tk.TclError, ValueError):
                pass
        self._ref = None                                     # (a drag while the layout is still changing isn't a zoom)
        self._settle_job = self.root.after(delay, self._take_ref)

    def _take_ref(self):
        self._settle_job = None
        try:
            self.root.update_idletasks()
            self._ref = (max(1, self.root.winfo_width()), max(1, self.root.winfo_height()))
            self._last_size = self._ref
        except tk.TclError:
            pass

    def _on_root_configure(self, e):
        if e.widget is not self.root:
            return
        self._party_place()
        size = (e.width, e.height)
        if size == getattr(self, "_last_size", None) or getattr(self, "_ref", None) is None:
            return                                           # (only moved, or still settling)
        self._last_size = size
        if getattr(self, "_zoom_job", None):
            try:
                self.root.after_cancel(self._zoom_job)
            except (tk.TclError, ValueError):
                pass
        self._zoom_job = self.root.after(300, self._zoom_to_window)

    def _zoom_to_window(self):
        """The window was dragged to a new size: zoom by how much it grew or shrank. With the tracker alone it
        zooms by whichever side changed less (it keeps its shape); with the log open by the height, and the
        log takes the width."""
        self._zoom_job = None
        if self.stop_evt.is_set() or self._ref is None:
            return
        try:
            w, h = self.root.winfo_width(), self.root.winfo_height()
            rw, rh = w / self._ref[0], h / self._ref[1]
            ratio = rh if self.log_open else min(rw, rh)
            zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, self._zoom * ratio)) / ZOOM_STEP) * ZOOM_STEP
            zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
            if abs(zoom - self._zoom) < ZOOM_STEP - 1e-9:
                if not self.log_open:
                    self.root.geometry("")                   # (too small a change: back to the tracker's own size)
                return
            self.set_zoom(zoom, keep_size=self.log_open)
        except Exception as e:
            log_error("zooming the window", e)

    def set_zoom(self, zoom, keep_size=False):
        """Sets the window's zoom (1.0 is the size from Settings > Size) and redraws everything for it."""
        zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        if zoom == self._zoom:
            return
        self._zoom = self.cfg["zoom"] = zoom
        self._apply_ui_scale(zoomed=keep_size)
        save_config(self.cfg)

    def _fit_settings(self):
        """Keeps the Settings window as big as what's in it. (Moving a window to a screen with
        different scaling can leave it at a fixed size, which would cut its contents off.)"""
        w = self.settings_win
        if w and w.winfo_exists() and (w.winfo_width() < w.winfo_reqwidth() or w.winfo_height() < w.winfo_reqheight()):
            w.geometry("")

    def _check_screen(self):
        """Resizes the text when the window is moved to a screen with different Windows scaling
        (judged by its title bar, which stays put when the window changes size)."""
        r = self._window_rect()
        dpi = PLATFORM.screen_dpi(r[0] + 40, r[1] + 15) if r else None
        if dpi and dpi != self._dpi:
            self._dpi = dpi
            self._apply_ui_scale()

    def _fill_log(self):
        """The log, newest first. A map's party sizes on a day share a row (smallest to biggest
        party, all their time together); clicking it shows a row for each party size."""
        tree = self.log_tree
        self._note_flush()                                  # a note being typed is saved first,
        keep, rows = self._selected_key(), {}               # and the same line stays selected
        tree.delete(*tree.get_children())
        self.shown, self.groups = {}, {}
        edited = lambda e: ("edited",) if self.autolog.edited(e) else ()
        for i, (key, entries) in enumerate(party_groups(self.autolog.newest_first())):
            stripe = ("odd",) if i % 2 else ()
            if len(entries) == 1:
                iid = tree.insert("", "end", values=log_row(entries[0]), tags=stripe + edited(entries[0]))
                self.shown[iid] = entries[0]
                rows[self._line_key(entries[0])] = iid
                continue
            is_open = key in self.open_groups
            all_edited = all(self.autolog.edited(e) for e in entries)
            iid = tree.insert("", "end", values=group_row(entries, is_open), open=is_open,
                              tags=stripe + (("edited",) if all_edited else ()))
            self.groups[iid] = (key, entries)
            rows[key] = iid
            for e in entries:
                child = tree.insert(iid, "end", values=log_row(e, part=True), tags=("part",) + edited(e))
                self.shown[child] = e
                rows[self._line_key(e)] = child
        if keep in rows:
            tree.selection_set(rows[keep])
        else:
            self._note_show(None)                           # that line is gone (renamed, merged, deleted)
        self.log_hint.set(LOG_HINT if self.shown else "No logs yet. Press Start and gain EXP for 5 minutes.")

    # ---- personal notes on log lines
    @staticmethod
    def _line_key(e):
        return ("line",) + AutoLog.key(e)

    def _selected_entries(self):
        """(a key for the selected row, the log lines it stands for), or (None, None)."""
        sel = self.log_tree.selection()
        if not sel:
            return None, None
        if sel[0] in self.groups:
            return self.groups[sel[0]]
        e = self.shown.get(sel[0])
        return (self._line_key(e), [e]) if e else (None, None)

    def _selected_key(self):
        return self._selected_entries()[0]

    def _note_show(self, key, entries=None):
        """Puts the selected line's note in the box (or empties it, with nothing selected)."""
        t = self.note_text
        if key is not None and key == self._note_key:
            self._note_entries = entries                    # the same line (the log was refilled): leave
            return                                          # the text, and whoever is typing in it, alone
        self._note_key, self._note_entries, self._note_dirty = key, entries, False
        t.config(state="normal")
        t.delete("1.0", "end")
        if key is None:
            self._note_label = ""
            self.note_title.config(text="Notes: click a line to write one (they stay on this PC).")
            t.config(state="disabled", bg=LIST_PART)
        else:
            first, party = entries[0], entries[0].get("party")
            where = f"{log_row(first)[0]} \u00b7 {first['map']}" + (f" \u00b7 {party}ppl" if len(entries) == 1 and party and party > 1 else "")
            self._note_label = f"Note for {where} (kept on this PC only)"
            self.note_title.config(text=self._note_label)
            t.insert("1.0", notes_of(entries))
            t.config(bg=LIST_BG)
        t.edit_reset()
        t.edit_modified(False)

    def _note_modified(self, _event=None):
        t = self.note_text
        if not t.edit_modified():
            return
        t.edit_modified(False)
        if self._note_key is None:
            return
        over = len(t.get("1.0", "end-1c")) - NOTE_MAX
        if over > 0:                                        # too long: what doesn't fit isn't kept
            t.delete(f"end-1c-{over}c", "end-1c")
            self.note_title.config(text=f"A note can be {NOTE_MAX:,} characters at most.")
        else:
            self.note_title.config(text=self._note_label)
        self._note_dirty = True
        if self._note_job:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(900, self._note_flush)

    def _note_flush(self):
        """Saves the note being typed, if it was changed (as you type, when you click another
        line or leave the box, and when the log or the overlay closes)."""
        if self._note_job:
            self.root.after_cancel(self._note_job)
            self._note_job = None
        if not self._note_dirty or self._note_key is None:
            return
        self._note_dirty = False
        text = clean_note(self.note_text.get("1.0", "end-1c"))[:NOTE_MAX]
        try:
            self.autolog.set_note(self._note_entries, text)
        except Exception as e:
            log_error("saving a note", e)
            self._note_dirty = True
            self.note_title.config(text="Couldn't save the note (see errors.log).")
            return
        for e in self._note_entries:                        # the rows on screen
            if text:
                e["note"] = text
            else:
                e.pop("note", None)
        self._note_marks()
        self.note_title.config(text=f"{self._note_label} \u00b7 saved")

    def _note_marks(self):
        """The Note column: a mark on the lines that have a note."""
        for iid, (_, entries) in self.groups.items():
            self.log_tree.set(iid, "note", NOTE_MARK if notes_of(entries) else "")
        for iid, e in self.shown.items():
            self.log_tree.set(iid, "note", NOTE_MARK if note_of(e) else "")

    def _log_click(self, event):
        """Clicking a merged party row opens it (a row per party size) or closes it."""
        iid = self.log_tree.identify_row(event.y)
        if iid in self.groups and self.log_tree.identify_region(event.x, event.y) == "cell":
            self.log_tree.item(iid, open=not self.log_tree.item(iid, "open"))
            self._sync_group(iid)

    def _sync_group(self, iid):
        """After a party row opens or closes: its arrow, and remembering it for next time."""
        if iid not in self.groups:
            return
        key, entries = self.groups[iid]
        is_open = bool(self.log_tree.item(iid, "open"))
        self.open_groups.add(key) if is_open else self.open_groups.discard(key)
        self.log_tree.item(iid, values=group_row(entries, is_open))
        if self.log_tree.selection() == (iid,):
            self._log_selected(None)

    def _log_selected(self, _event):
        self._note_flush()                                  # the line left is saved first
        key, entries = self._selected_entries()
        self._note_show(key, entries)
        sel = self.log_tree.selection()
        group = self.groups.get(sel[0]) if sel else None
        if group:
            entries = group[1]
            sizes = ", ".join(str(e["party"]) for e in entries[:-1]) + f" and {entries[-1]['party']}"
            runs = sum(self.autolog.runs_done(e["map"], e["party"], e.get("class"), e["date"]) for e in entries)
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            what = "hide" if self.log_tree.item(sel[0], "open") else "see"
            self.log_hint.set(f"{entries[0]['map']}: parties of {sizes}, over {fmt_logged(sum(e['seconds'] for e in entries))} "
                              f"of non-stop EXP gain in all.{hours} Click it to {what} each party size. "
                              f"Right-click for details, to copy, rename or delete.")
            return
        e = self.shown.get(sel[0]) if sel else None
        if e:
            if self.autolog.edited(e):
                self.log_hint.set("This line was changed outside the overlay (it no longer matches "
                                  "the overlay's seal), so it won't count toward the best-maps site.")
                return
            runs = self.autolog.runs_done(e["map"], e.get("party"), e.get("class"), e["date"])
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            self.log_hint.set(f"{e['map']}: measured over {fmt_logged(e['seconds'])} of non-stop EXP gain."
                              f"{hours} Right-click for details, to copy, rename or delete.")

    def _log_details_click(self, event):
        """Double-click on a line: its Details window."""
        iid = self.log_tree.identify_row(event.y)
        entries = self.groups[iid][1] if iid in self.groups else [self.shown[iid]] if iid in self.shown else None
        if entries and self.log_tree.identify_region(event.x, event.y) == "cell":
            self.root.after(10, lambda: self._details(entries))

    def _log_menu(self, event):
        """Right-click: copy, rename or delete a line (a merged party row: all its party sizes)."""
        iid = self.log_tree.identify_row(event.y)
        entries = self.groups[iid][1] if iid in self.groups else [self.shown[iid]] if iid in self.shown else None
        if not entries:
            return
        self.log_tree.selection_set(iid)
        many = len(entries) > 1
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Details...", command=lambda: self._details(entries))
        menu.add_separator()
        menu.add_command(label="Copy lines" if many else "Copy line", command=lambda: self._copy(entries))
        menu.add_command(label="Rename map...", command=lambda: self._rename(entries))
        menu.add_command(label=f"Delete all {len(entries)} party sizes" if many else "Delete entry",
                         command=lambda: self._delete(entries))
        menu.tk_popup(event.x_root, event.y_root)

    def _details(self, entries):
        """The Details window for a log line (or a map's party sizes shown as one line): totals, rates, potions, and
        how it compares with your other logs on the map. Numbers come from the log alone (log_details.py)."""
        th = self.theme
        sections = log_details.line_sections(entries) + log_details.compare_sections(entries, self.autolog.entries)
        win = tk.Toplevel(self.root)
        first = entries[0]
        win.title(f"Details \u00b7 {first['map']}")
        win.configure(bg=BG)
        win.transient(self.root)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            win.after(delay, lambda: PLATFORM.style_window(win, INK, TEXT_DIM) if win.winfo_exists() else None)
        note = notes_of(entries)
        bars = log_details.pace(entries)[-60:]
        state = {"z": 1.0, "ref": None, "job": None}

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(log_details.as_text(sections))

        def build(z):
            """Draws everything in the window at zoom z (text, spacing, chart and buttons all scale together)."""
            for child in win.winfo_children():
                child.destroy()
            P = lambda n: th.px(n * z)                       # noqa: E731
            F = lambda weight, size: th.font(weight, size * z)   # noqa: E731
            body = tk.Frame(win, bg=BG)
            body.pack(padx=P(20), pady=P(16), fill="both", expand=True)
            dim = dict(bg=BG, fg=DIM, font=F("regular", 11))
            val = dict(bg=BG, fg=FG, font=F("semibold", 11))
            r = 0
            for title, rows in sections:
                tk.Label(body, text=title, bg=BG, fg=ACCENT, font=F("bold", 12), anchor="w").grid(
                    row=r, column=0, columnspan=2, sticky="w", pady=(P(10) if r else 0, P(3)))
                r += 1
                for label, value in rows:
                    tk.Label(body, text=label, anchor="w", **dim).grid(row=r, column=0, sticky="w")
                    tk.Label(body, text=str(value), anchor="e", justify="right", **val).grid(
                        row=r, column=1, sticky="e", padx=(P(28), 0))
                    r += 1
            if len(bars) >= 2:                               # the pace chart: EXP per hour of each 5-minute stretch
                tk.Label(body, text="Pace (EXP per hour, each 5 minutes)", bg=BG, fg=ACCENT, font=F("bold", 12),
                         anchor="w").grid(row=r, column=0, columnspan=2, sticky="w", pady=(P(10), P(3)))
                cw, ch = P(420), P(90)
                cv = tk.Canvas(body, width=cw, height=ch, bg=LIST_BG, highlightthickness=0)
                cv.grid(row=r + 1, column=0, columnspan=2, sticky="w")
                top = max(bars) or 1
                step = cw / len(bars)
                for i, v in enumerate(bars):
                    h = max(1, (ch - P(6)) * v / top)
                    cv.create_rectangle(i * step + 1, ch - h, (i + 1) * step - 1, ch, fill=ACCENT, width=0)
                cv.create_text(cw - 4, 8, text=f"{top:,.0f}", fill=DIM, anchor="e", font=F("regular", 9))
                r += 2
            if note:
                tk.Label(body, text="Note", bg=BG, fg=ACCENT, font=F("bold", 12), anchor="w").grid(
                    row=r, column=0, columnspan=2, sticky="w", pady=(P(10), P(3)))
                tk.Label(body, text=note, wraplength=P(420), justify="left", anchor="w", **val).grid(
                    row=r + 1, column=0, columnspan=2, sticky="w")
            bar = tk.Frame(win, bg=BG)
            bar.pack(side="bottom", fill="x", padx=P(20), pady=(0, P(16)))
            Button(bar, th, text="Copy", kind="secondary", width=90 * z, height=34 * z, radius=10 * z, size=12 * z,
                   command=copy, bg=BG).pack(side="left")
            Button(bar, th, text="Close", kind="ghost", width=90 * z, height=34 * z, radius=10 * z, size=12 * z,
                   command=win.destroy, bg=BG).pack(side="right")
            body.pack_configure(side="top")
            state["z"] = z

        def zoom_now():
            state["job"] = None
            if not win.winfo_exists() or not state["ref"]:
                return
            rw, rh = state["ref"]
            z = min(win.winfo_width() / rw, win.winfo_height() / rh)
            z = round(max(ZOOM_MIN, min(ZOOM_MAX, z)) / ZOOM_STEP) * ZOOM_STEP
            if abs(z - state["z"]) > 1e-6:
                build(z)
                win.update_idletasks()
                while (z > ZOOM_MIN + 1e-6 and (win.winfo_reqwidth() > win.winfo_width()       # (text doesn't grow in
                                                or win.winfo_reqheight() > win.winfo_height())):   # a straight line: step down until it fits)
                    z = round(z - ZOOM_STEP, 2)
                    build(z)
                    win.update_idletasks()

        def resized(event):
            if event.widget is not win or not state["ref"]:
                return
            if state["job"]:
                win.after_cancel(state["job"])
            state["job"] = win.after(250, zoom_now)

        build(1.0)
        win.update_idletasks()
        state["ref"] = (win.winfo_reqwidth(), win.winfo_reqheight())      # the size that is zoom 1
        win.geometry(f"{state['ref'][0]}x{state['ref'][1]}")              # (fixed from here: only the player changes it)
        win.minsize(int(state["ref"][0] * ZOOM_MIN), int(state["ref"][1] * ZOOM_MIN))
        win.resizable(True, True)
        win.bind("<Configure>", resized)
        win.zoom_state = state
        return win
        win.bind("<Escape>", lambda e: win.destroy())

    def _copy(self, entries):
        text = "\n".join(format_entry(e) for e in entries)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log_hint.set("Copied: " + text if len(entries) == 1 else f"Copied {len(entries)} lines, one per party size.")

    def _rename(self, entries):
        self._note_flush()
        self.hotkeys.pause(True)                            # typing "!" in a name isn't Shift+1
        try:
            name = simpledialog.askstring("Rename map", "Map name:", initialvalue=entries[0]["map"],
                                          parent=self.root)
        finally:
            self.hotkeys.pause(False)
        if name and name.strip():
            for entry in entries:             # (one rename covers them all, unless it's "Unknown map")
                if entry["map"] != name.strip():
                    self.autolog.rename(entry, name.strip())
            if self.map_name:
                self.map_name = self.autolog.canonical(self.map_name)
            s = self.session                    # so a renamed map isn't taken as a new one
            s.map, s.here = (self.autolog.canonical(n) if n else n for n in (s.map, s.here))
            self._fill_log()

    def _delete(self, entries):
        self._note_flush()
        lines = "\n".join(format_entry(e) for e in entries)
        question = f"Delete these {len(entries)} log entries?" if len(entries) > 1 else "Delete this log entry?"
        if any(note_of(e) for e in entries):
            question += " The note" + ("s" if len(entries) > 1 else "") + " on " + ("them" if len(entries) > 1 else "it") + " will be deleted too."
        if messagebox.askyesno("Delete entry", f"{question}\n\n{lines}", parent=self.root):
            for entry in entries:
                self.autolog.delete(entry)
            self._fill_log()

    def _log_status_text(self, now):
        where = " · ".join(x for x in (self.map_name or "Map not read yet", f"Lv {self.level or '?'}",
                                       self.job, party_label(self.party)) if x)
        if self.skip_log:
            return f"{where}\nNot logging this run."
        if not self.session.on:
            return f"{where}\nPress Start to log EXP/hr here."
        if self.session.waiting:
            return f"{where}\nPaused. Logging carries on at your next EXP gain."
        left = self.autolog.seconds_left(now)
        if left is None:
            text = f"{where}\nWaiting for EXP gain..."
        else:
            text = f"{where}\nNext log in {int(left) // 60}:{int(left) % 60:02d} of non-stop EXP gain"
        if self.last_logged and now - self.last_logged < 8:
            text += "  (just logged)"
        run = self.autolog.run_progress(now)
        done = self.autolog.runs_done(self.map_name, self.party, self.job) if self.map_name else 0
        if run is not None or done:
            line = f"{RUN_SECONDS // 60}-min run: {int(run // 60) if run is not None else 0} of {RUN_SECONDS // 60} min"
            text += f"\n{line}" + (f" · {done} done here today" if done else "")
        return text

    # ---- settings
    def _apply_window_settings(self):
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.attributes("-alpha", 1 - min(MAX_TRANSPARENCY, max(0, int(self.cfg["transparency"]))) / 100)

    def _switch(self, parent, text, var):
        """An on/off switch tied to a BooleanVar."""
        t = Toggle(parent, self.theme, text, bool(var.get()), lambda on: var.set(on), BG)
        var.trace_add("write", lambda *_: t.set(bool(var.get())))
        return t

    def _section(self, parent, text, first=False):
        tk.Label(parent, text=text.upper(), bg=BG, fg=DIM, font=self.theme.font("semibold", 10)).pack(
            anchor="w", pady=(0 if first else self.theme.px(20), self.theme.px(8)))

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        th = self.theme
        px = th.px
        w = self.settings_win = tk.Toplevel(self.root)
        w.title("Settings")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.transient(self.root)
        r = self._window_rect()
        if r:                                    # over the overlay, so on the same screen (and scaling)
            w.geometry(f"+{r[0] + 40}+{r[1] + 40}")
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        body = tk.Frame(w, bg=BG)
        body.pack(padx=px(26), pady=px(22))
        text = dict(bg=BG, fg=FG, font=th.font("semibold", 12))
        dim = dict(bg=BG, fg=DIM, font=th.font("regular", 11))
        wrap = px(380)

        self._section(body, "Tracking", first=True)
        gap = tk.StringVar(value=str(self.cfg["gap_seconds"]))
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        tk.Label(line, text="Pause after no EXP for", **text).pack(side="left")
        gap_step = tk.Frame(line, bg=BG)
        gap_step.pack(side="right")

        def gap_by(by):
            try:
                now_ = int(gap.get())
            except ValueError:
                now_ = self.cfg["gap_seconds"]
            gap.set(str(min(300, max(5, now_ + by))))
        Button(gap_step, th, text="−", kind="secondary", width=38, height=34, radius=10, size=15,
               command=lambda: gap_by(-5), bg=BG).pack(side="left")
        tk.Label(gap_step, textvariable=gap, width=4, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
        Button(gap_step, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
               command=lambda: gap_by(5), bg=BG).pack(side="left")
        tk.Label(gap_step, text="s", bg=BG, fg=DIM, font=th.font("regular", 11)).pack(side="left", padx=(px(6), 0))
        tk.Label(body, text="Pausing also restarts the 5-minute log timer.", **dim).pack(anchor="w", pady=(px(5), 0))

        self._section(body, "Speed")

        def hz_row(key, label, lo, hi, default, note, first=False):
            var = tk.StringVar(value=str(self.cfg[key]))
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=(0 if first else px(8), 0))
            tk.Label(row, text=label, **text).pack(side="left")
            step = tk.Frame(row, bg=BG)
            step.pack(side="right")

            def by(delta):
                try:
                    now_ = int(var.get())
                except ValueError:
                    now_ = self.cfg[key]
                now_ = min(hi, max(lo, now_ + delta))
                var.set(str(now_))
                self.cfg[key] = now_                    # (used at once: the loops look it up every time)
                save_config(self.cfg)
            Button(step, th, text="\u2212", kind="secondary", width=38, height=34, radius=10, size=15,
                   command=lambda: by(-1), bg=BG).pack(side="left")
            tk.Label(step, textvariable=var, width=4, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
            Button(step, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
                   command=lambda: by(1), bg=BG).pack(side="left")
            tk.Label(step, text="Hz", bg=BG, fg=DIM, font=th.font("regular", 11)).pack(side="left", padx=(px(6), 0))
            tk.Label(body, text=f"{note} Default {default} Hz, {lo} to {hi}.", wraplength=wrap, justify="left",
                     **dim).pack(anchor="w", pady=(px(3), 0))
        hz_row("rate_hz", "Data collection rate", RATE_HZ_MIN, RATE_HZ_MAX, RATE_HZ_DEFAULT,
               "How often the screen is read. Faster catches more of the game's messages but uses more processor.",
               first=True)
        hz_row("ui_hz", "Display refresh rate", UI_HZ_MIN, UI_HZ_MAX, UI_HZ_DEFAULT,
               "How often the numbers in this window are redrawn. The data is collected at its own rate.")

        self._section(body, "Window")
        transparency = tk.IntVar(value=int(self.cfg["transparency"]))
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        tk.Label(line, text="Transparency", **text).pack(side="left")
        percent = tk.Label(line, text=f"{transparency.get()}%", **dim)
        percent.pack(side="right")
        transparency.trace_add("write", lambda *_: percent.config(text=f"{int(float(transparency.get()))}%"))
        Slider(body, th, transparency, 0, MAX_TRANSPARENCY, 330, BG).pack(anchor="w", pady=(px(6), px(12)))
        size_line = tk.Frame(body, bg=BG)
        size_line.pack(fill="x")
        tk.Label(size_line, text="Size", **text).pack(side="left")
        # \u2212 100% +: steps through UI_SCALES only, so it stops at the smallest and biggest sizes.
        size = tk.StringVar(value=f"{self.cfg['ui_scale']}%")
        stepper = tk.Frame(size_line, bg=BG)
        stepper.pack(side="right")

        def nudge(by):
            i = UI_SCALES.index(int(size.get().rstrip("%"))) + by
            if 0 <= i < len(UI_SCALES):
                size.set(f"{UI_SCALES[i]}%")
            smaller.set(enabled=i > 0)
            bigger.set(enabled=i < len(UI_SCALES) - 1)
        smaller = Button(stepper, th, text="\u2212", kind="secondary", width=38, height=34, radius=10, size=15,
                         command=lambda: nudge(-1), bg=BG)
        smaller.pack(side="left")
        tk.Label(stepper, textvariable=size, width=5, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
        bigger = Button(stepper, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
                        command=lambda: nudge(1), bg=BG)
        bigger.pack(side="left")
        nudge(0)
        shape_line = tk.Frame(body, bg=BG)
        shape_line.pack(fill="x", pady=(px(14), 0))
        tk.Label(shape_line, text="Compact shape", **text).pack(side="left")
        shape_box = tk.Frame(shape_line, bg=BG)
        shape_box.pack(side="right")
        shape_btns = {}

        def pick_shape(shape):
            self.set_shape(shape)
            for k, b in shape_btns.items():
                b.set(kind="primary" if k == shape else "secondary")
        for k in SHAPES:
            shape_btns[k] = Button(shape_box, th, text=SHAPE_LABELS[k], width=64, height=34, radius=10, size=12, bg=BG,
                                   kind="primary" if k == self.cfg["compact_shape"] else "secondary",
                                   command=lambda k=k: pick_shape(k))
            shape_btns[k].pack(side="left", padx=(px(6), 0))
        topmost = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        self._switch(body, "Always on top of other windows", topmost).pack(anchor="w", pady=(px(14), 0))

        self._section(body, "Potions")
        potions_on = tk.BooleanVar(value=bool(self.cfg["potions_on"]))
        self._switch(body, "Count potions used and what they cost", potions_on).pack(anchor="w")
        price_line = tk.Frame(body, bg=BG)
        price_line.pack(fill="x", pady=(px(8), 0))
        tk.Label(price_line, text="Your own price for each one", **text).pack(side="left")
        Button(price_line, th, text="Prices", kind="secondary", width=80, height=34, radius=10, size=12,
               command=self.open_potion_prices, bg=BG).pack(side="right")
        self._potions_on = potions_on

        self._section(body, "EXP bar without a number")
        level_line = tk.Frame(body, bg=BG)
        level_line.pack(fill="x")
        tk.Label(level_line, text="Type your level if it can't be read", **text).pack(side="left")
        self.level_btn = Button(level_line, th, text="Level fallback", kind="secondary", width=124, height=34, radius=10,
                                size=12, command=lambda: self.type_level(w), bg=BG)
        self.level_btn.pack(side="right")
        self.level_btn.set(enabled=self._can_type_level())
        tk.Label(body, text="Past a certain level the game shows only a percentage. The overlay reads your level "
                            "from the status bar and estimates EXP from it. Typing it in is only a last resort.",
                 wraplength=wrap, justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        self._section(body, "Updates and sharing")
        updates = tk.BooleanVar(value=bool(self.cfg["check_updates"]))
        self._switch(body, "Check for updates when it starts", updates).pack(anchor="w")
        sharing = tk.BooleanVar(value=bool(self.cfg.get("share_runs")))
        self._switch(body, f"Share my {RUN_SECONDS // 60}-minute runs, anonymously", sharing).pack(anchor="w", pady=(px(6), 0))
        tk.Label(body, text="They feed the best-maps site. Your notes and log never leave this PC.", wraplength=wrap,
                 justify="left", **dim).pack(anchor="w", pady=(px(4), 0))

        def apply(*_):
            try:
                seconds = int(gap.get())
            except ValueError:
                seconds = 0
            if 1 <= seconds <= 3600:
                self.cfg["gap_seconds"] = self.autolog.gap = self.session.gap = seconds
            self.cfg["transparency"] = min(MAX_TRANSPARENCY, max(0, int(float(transparency.get()))))
            self.cfg["topmost"] = bool(topmost.get())
            self.cfg["check_updates"] = bool(updates.get())
            self.cfg["potions_on"] = bool(potions_on.get())
            if bool(sharing.get()) != bool(self.cfg.get("share_runs")):
                self.cfg["share_runs"] = bool(sharing.get())
                self.share_runs()
            self._apply_window_settings()
            w.attributes("-topmost", self.cfg["topmost"])
            scale = int(size.get().rstrip("%"))
            if scale in UI_SCALES and scale != self.cfg["ui_scale"]:
                self.cfg["ui_scale"] = scale
                self._apply_ui_scale()
                self.root.after(60, self._reopen_settings)        # (drawn again at the new size)
            save_config(self.cfg)

        for var in (gap, transparency, topmost, updates, sharing, size, potions_on):
            var.trace_add("write", apply)
        self._hotkey_settings(body)
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(px(22), px(4)))
        Button(buttons, th, text="Close", kind="primary", width=96, height=38, radius=11, size=12,
               command=w.destroy, bg=BG).pack(side="right")
        Button(buttons, th, text="Data folder", kind="secondary", width=104, height=38, radius=11, size=12,
               command=self.open_data_folder, bg=BG).pack(side="left")
        Button(buttons, th, text="Check for updates", kind="secondary", width=146, height=38, radius=11, size=12,
               command=lambda: self.check_updates(manual=True), bg=BG).pack(side="left", padx=(px(8), 0))
        Button(buttons, th, text="Report a bug", kind="secondary", width=116, height=38, radius=11, size=12,
               command=lambda: self.report_bug(w), bg=BG).pack(side="left", padx=(px(8), 0))
        more = tk.Frame(body, bg=BG)
        more.pack(fill="x", pady=(px(8), 0))
        Button(more, th, text="What's new", kind="secondary", width=116, height=38, radius=11, size=12,
               command=self.show_whats_new, bg=BG).pack(side="left")
        tk.Label(body, text=f"Your log, notes and settings are saved in {DATA_DIR}", wraplength=wrap, justify="left",
                 bg=BG, fg=FAINT, font=th.font("regular", 10)).pack(anchor="w", pady=(px(8), 0))
        tk.Label(body, text=f"{WINDOW_TITLE} v{VERSION}. This is a beta: it's still being improved.", bg=BG, fg=FAINT,
                 font=th.font("regular", 10)).pack(anchor="w", pady=(px(2), 0))

    def open_potion_prices(self):
        """One price per item the bar can show, over the shipped one. Clear a box for the default."""
        if self.prices_win and self.prices_win.winfo_exists():
            self.prices_win.lift()
            return
        table = self._potion_table()
        if not table:
            return
        th = self.theme
        px = th.px
        w = self.prices_win = tk.Toplevel(self.root)
        w.title("Potion prices")
        w.configure(bg=BG)
        w.transient(self.settings_win if self.settings_win and self.settings_win.winfo_exists() else self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        tk.Label(w, text="Mesos each. Leave a box empty to use the shown price; 0 means free.", bg=BG, fg=DIM,
                 font=th.font("regular", 11)).pack(anchor="w", padx=px(20), pady=(px(16), px(8)))
        holder = tk.Frame(w, bg=BG)
        holder.pack(fill="both", expand=True, padx=px(20))
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, width=px(380), height=px(420))
        bar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        rows = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        w.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>") if e.widget is w else None)
        self._price_vars = {}
        for item in table.prices.values():
            if item.get("kind") != "consumable":
                continue
            name = item["name"]
            row = tk.Frame(rows, bg=BG)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=name, bg=BG, fg=FG, font=th.font("regular", 11), anchor="w", width=24).pack(side="left")
            default = "no NPC" if item.get("unpriced") else f"{item['price']:,}"
            own = self.cfg["potion_prices"].get(name)
            var = tk.StringVar(value="" if own is None else str(own))
            tk.Entry(row, textvariable=var, width=8, justify="right", relief="flat", bg=INK, fg=FG,
                     insertbackground=FG, font=th.font("regular", 11)).pack(side="right")
            tk.Label(row, text=default, bg=BG, fg=DIM, font=th.font("regular", 11), width=8, anchor="e").pack(side="right")
            var.trace_add("write", lambda *_, n=name, v=var: self._set_price(n, v.get()))
            self._price_vars[name] = var
        Button(w, th, text="Close", kind="primary", width=96, height=38, radius=11, size=12,
               command=w.destroy, bg=BG).pack(side="right", padx=px(20), pady=px(14))

    def _set_price(self, name, text):
        """A price typed in the prices window: whole mesos, or empty for the default."""
        text = text.replace(",", "").strip()
        if text.isascii() and text.isdigit() and len(text) <= 10:
            self.cfg["potion_prices"][name] = int(text)
        elif not text:
            self.cfg["potion_prices"].pop(name, None)
        else:
            return                                          # not a number (yet): leave what's saved
        save_config(self.cfg)

    def _reopen_settings(self):
        """After a size change: Settings again, in the same place, drawn for the new size."""
        old = self.settings_win
        if old and old.winfo_exists():
            where = f"+{old.winfo_x()}+{old.winfo_y()}"
            old.destroy()
            self.open_settings()
            self.settings_win.geometry(where)

    def _hotkey_settings(self, body):
        """Settings' Hotkeys section: on or off, and each one changeable (click it, then press
        the keys; Esc keeps it as it was, Backspace turns it off)."""
        th, w = self.theme, body.winfo_toplevel()
        px = th.px
        self._section(body, "Hotkeys")
        box = tk.Frame(body, bg=BG)
        box.pack(anchor="w", fill="x")
        if not self.hotkeys.supported:
            tk.Label(box, text=f"Hotkeys aren't available on {PLATFORM.name} yet: use the buttons.",
                     bg=BG, fg=DIM, font=th.font("regular", 11)).pack(anchor="w")
            return
        on = tk.BooleanVar(value=self.cfg["hotkeys_on"])
        self._switch(box, "Hotkeys, while MapleStory or the overlay is in front", on).pack(anchor="w", pady=(0, px(8)))
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w", padx=(px(2), 0))
        buttons, notes, choosing = {}, {}, {"action": None}
        idle_hint = "Click one, then press the keys you want. Backspace turns it off."
        hint = tk.StringVar(value=idle_hint)

        def show():
            for action, b in buttons.items():
                key = self.cfg["hotkeys"][action]
                if action == choosing["action"]:
                    b.set(text="Press keys...", kind="primary")
                else:
                    b.set(text=key or "Off", kind="secondary" if key else "ghost")
                notes[action].config(text=self.hotkeys.problems.get(action, ""))

        def stop():
            """Nobody is waiting for keys: hotkeys work again, every button shows its key."""
            choosing["action"] = None
            hint.set(idle_hint)
            self.hotkeys.pause(False)
            show()

        def watch():
            """Clicking away from Settings (into the game, say) ends the wait, so the hotkeys never stay off."""
            if choosing["action"] and w.winfo_exists():
                if w.focus_displayof() is None:
                    stop()
                else:
                    w.after(400, watch)

        def choose(action):
            if choosing["action"] == action:         # clicking the waiting one again cancels it
                stop()
                return
            first = choosing["action"] is None
            choosing["action"] = action              # (one at a time: another that was waiting goes back to its key)
            self.hotkeys.pause(True)                 # so the keys reach this window
            hint.set(f"Press the keys for {HOTKEY_ACTIONS[action]}. Click it again or press Esc to cancel.")
            show()
            w.focus_force()
            if first:
                w.after(400, watch)

        def done(action, key):
            choosing["action"] = None
            hint.set(idle_hint)
            if key:
                for other, k in self.cfg["hotkeys"].items():
                    if k == key and other != action:
                        self.cfg["hotkeys"][other] = ""      # one key, one job
            self.cfg["hotkeys"][action] = key
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
            self.hotkeys.pause(False)
            show()

        def pressed(e):
            action = choosing["action"]
            if not action or e.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                                          "Win_L", "Win_R", "Caps_Lock", "Num_Lock"):
                return None
            if e.keysym == "Escape":
                done(action, self.cfg["hotkeys"][action])
            elif e.keysym in ("BackSpace", "Delete"):
                done(action, "")
            else:
                key = hotkey_text(bool(e.state & 0x4), bool(e.state & 0x20000), bool(e.state & 0x1), e.keycode)
                if not key:
                    hint.set("Use Ctrl, Alt or Shift with a number or letter, or an F key.")
                    return "break"
                done(action, key)
            return "break"

        for i, (action, name) in enumerate(HOTKEY_ACTIONS.items()):
            tk.Label(grid, text=name, bg=BG, fg=FG, font=th.font("regular", 12), width=14, anchor="w").grid(
                row=i, column=0, sticky="w", pady=px(3))
            buttons[action] = Button(grid, th, text="", kind="secondary", width=124, height=34, radius=10, size=12,
                                     command=lambda a=action: choose(a), bg=BG)
            buttons[action].grid(row=i, column=1, sticky="w", pady=px(3))
            notes[action] = tk.Label(grid, bg=BG, fg="#ff8a7a", font=th.font("regular", 10))
            notes[action].grid(row=i, column=2, sticky="w", padx=(px(8), 0))
        tk.Label(box, textvariable=hint, bg=BG, fg=FAINT, font=th.font("regular", 10)).pack(anchor="w", pady=(px(6), 0))
        w.bind("<KeyPress>", pressed)
        w.bind("<Destroy>", lambda e: e.widget is w and self.hotkeys.pause(False))
        self._hotkey_ui = {"buttons": buttons, "choose": choose, "choosing": choosing, "pressed": pressed, "on": on}   # (tests)

        def switch(*_):
            if choosing["action"]:
                stop()
            self.cfg["hotkeys_on"] = bool(on.get())
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        on.trace_add("write", switch)
        show()

    def open_data_folder(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            PLATFORM.open_folder(DATA_DIR)
        except Exception as e:
            self._oops("opening the data folder", e)

    def _restore_position(self):
        pos = self.cfg.get("pos")
        if not pos:
            return
        with mss.mss() as sct:
            screens = sct.monitors[1:]
        # Only if its top-left is still on a screen (not one that was unplugged, nor the
        # gap between two screens of different sizes).
        if any(v["left"] <= pos[0] < v["left"] + v["width"] - 50
               and v["top"] <= pos[1] < v["top"] + v["height"] - 50 for v in screens):
            self.root.geometry(f"+{pos[0]}+{pos[1]}")

    def _window_rect(self):
        """This app's window on screen (physical pixels), or None."""
        try:
            return PLATFORM.own_window_rect(self.root)
        except Exception:
            return None

    # ---- background capture loop
    def _period(self):
        """Seconds between two readings of the screen: 1 / the reading speed set in Settings (read each time, so a change
        applies at once)."""
        try:
            return 1.0 / min(RATE_HZ_MAX, max(RATE_HZ_MIN, int(self.cfg["rate_hz"])))
        except Exception:
            return 1.0 / RATE_HZ_DEFAULT

    def _tick_ms(self, factor):
        """Milliseconds between two runs of the loop that takes in the readings: it follows the data collection rate."""
        return int(min(1000, max(40, self._period() * 1000 * factor)))

    def _ui_ms(self):
        """Milliseconds between two redraws of the numbers on screen (Settings > Display refresh rate)."""
        try:
            return int(1000 / min(UI_HZ_MAX, max(UI_HZ_MIN, int(self.cfg["ui_hz"]))))
        except Exception:
            return int(1000 / UI_HZ_DEFAULT)

    def _worker(self):
        with mss.mss() as sct:
            while not self.stop_evt.is_set():
                t0 = time.monotonic()
                try:
                    self.q.put(("reading", self.reader.read(sct)))
                    self.health.finished(t0)
                except Exception as e:
                    log_error("reading the screen", e)
                    self.health.failed(e)
                    self.q.put(("error", f"{type(e).__name__}: {e}"))
                self.stop_evt.wait(max(0.02, self._period() - (time.monotonic() - t0)))
            self.health.alive = False

    def _message_worker(self):
        """Reads the game's message lines (EXP, party bonus, mesos) as often as the screen is read (Settings >
        Reading speed): they scroll away in a few seconds."""
        try:
            with mss.mss() as sct:
                while not self.stop_evt.is_set():
                    t0 = time.monotonic()
                    self.reader.read_new_messages(sct)
                    self.stop_evt.wait(max(0.02, self._period() - (time.monotonic() - t0)))
        except Exception as e:
            log_error("reading the messages", e)

    # ---- sharing runs
    def open_best_maps(self):
        """The best-maps site, already showing the maps for this level, party size and class."""
        ask = {k: v for k, v in (("level", self.level), ("party", self.party), ("job", self.job)) if v}
        webbrowser.open(STATS_SITE + ("/?" + urllib.parse.urlencode(ask) if ask else ""))

    def _first_run(self):
        """Once per install or update: a new install picks its compact shape, an update shows what's new. The
        sharing question comes after."""
        seen = self.cfg.get("seen_version", "")
        newer = not seen or version_key(VERSION) > version_key(seen)
        if newer:
            self.cfg["seen_version"] = VERSION                 # (first, so a crash in a window can't repeat it)
            save_config(self.cfg)
        if self._fresh and newer:
            self._shape_picker(self._ask_to_share)
        elif newer and WHATS_NEW.get(VERSION):
            self._whats_new(WHATS_NEW[VERSION], self._ask_to_share)
        else:
            self._ask_to_share()

    def _small_window(self, title, then):
        """A window for the one-time messages: like the other dialogs, and `then` runs when it closes."""
        th = self.theme
        w = tk.Toplevel(self.root)
        w.title(title)
        w.configure(bg=BG, padx=th.px(26), pady=th.px(22))
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        w.bind("<Destroy>", lambda e: then and e.widget is w and self.root.after(100, then))
        return w

    def _shape_photo(self, shape, width):
        """The picture of a compact shape in the picker (assets/shape_<name>.png), `width` logical pixels wide."""
        try:
            img = Image.open(ASSETS / f"shape_{shape}.png").convert("RGBA")
            w = self.theme.px(width)
            return ImageTk.PhotoImage(img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS), master=self.root)
        except Exception:
            return None

    def _shape_picker(self, then):
        """A new install chooses how compact mode looks. Closing the window keeps Square."""
        th = self.theme
        w = self._small_window("Choose a compact look", then)
        tk.Label(w, text="Pick a compact look", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text="Compact mode is the small version of the overlay (the compact icon, or Shift+4). "
                         "Choose how it looks. You can change it any time in Settings.", bg=BG, fg=DIM,
                 font=th.font("regular", 12), wraplength=th.px(500), justify="left").pack(anchor="w", pady=(2, th.px(14)))
        widths = {"square": 150, "wide": 250, "bar": 300}

        def choose(shape):
            self.set_shape(shape)
            w.destroy()
        for shape in SHAPES:
            card = tk.Frame(w, bg=LIST_BG, highlightthickness=1, highlightbackground=LINE)
            card.pack(fill="x", pady=(0, th.px(10)))
            photo = self._shape_photo(shape, widths[shape])
            if photo:
                pic = tk.Label(card, image=photo, bg=LIST_BG, cursor="hand2")
                pic.image = photo
                pic.pack(side="left", padx=th.px(12), pady=th.px(12))
                pic.bind("<Button-1>", lambda e, s=shape: choose(s))
            side = tk.Frame(card, bg=LIST_BG)
            side.pack(side="left", fill="both", expand=True, padx=(0, th.px(12)), pady=th.px(12))
            tk.Label(side, text=SHAPE_LABELS[shape], bg=LIST_BG, fg=FG, font=th.font("bold", 14)).pack(anchor="w")
            tk.Label(side, text=SHAPE_BLURBS[shape], bg=LIST_BG, fg=DIM, font=th.font("regular", 11),
                     wraplength=th.px(190), justify="left").pack(anchor="w", pady=(2, th.px(8)))
            Button(side, th, text=f"Use {SHAPE_LABELS[shape]}", kind="primary" if shape == "square" else "secondary",
                   width=120, height=34, radius=10, size=12, command=lambda s=shape: choose(s), bg=LIST_BG).pack(anchor="w")
        w.update_idletasks()

    def show_whats_new(self):
        """Settings > What's new: this version's notes again (they are otherwise shown once, after an update)."""
        self._whats_new(WHATS_NEW.get(VERSION) or ("Nothing new to show for this version.",), None)

    def _note_photo(self, name, width):
        """A picture from assets/ for the What's new window, `width` logical pixels wide (None if it can't be read)."""
        try:
            img = Image.open(ASSETS / name).convert("RGBA")
            w = self.theme.px(width)
            return ImageTk.PhotoImage(img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS),
                                      master=self.root)
        except Exception:
            return None

    def _whats_new(self, notes, then):
        """After an update: a few plain lines on what changed, in one or more pages (see WHATS_NEW).
        Shown once per version."""
        th = self.theme
        pages = notes if notes and not isinstance(notes[0], str) else ((None, None, tuple(notes)),)
        w = self._small_window("What's new", then)
        body = tk.Frame(w, bg=BG)
        body.pack(anchor="w")
        w._pictures = []                                  # (kept, or tk drops them)

        def show(n, size=0):
            """Page n, its pictures at the first width in PICTURE_WIDTHS (then smaller, and finally none)
            if the page would be taller than the screen."""
            for child in body.winfo_children():
                child.destroy()
            w._pictures.clear()
            pic_width = PICTURE_WIDTHS[size]
            title, sub, items = pages[n]
            tk.Label(body, text=title or f"What's new in v{VERSION.split('-')[0]}", bg=BG, fg=ACCENT,
                     font=th.font("extra", 20)).pack(anchor="w")
            tk.Label(body, text=sub or "Thanks for updating. Your log, notes and settings are kept.", bg=BG, fg=DIM,
                     font=th.font("regular", 12)).pack(anchor="w", pady=(2, th.px(12)))
            for item in items:
                kind, text = ("dot", item) if isinstance(item, str) else item[:2]
                if kind == "head":
                    tk.Label(body, text=text, bg=BG, fg=FG, font=th.font("bold", 14)).pack(anchor="w", pady=(th.px(4), th.px(4)))
                elif kind == "pic":
                    photo = self._note_photo(text, pic_width) if pic_width else None
                    if photo:
                        w._pictures.append(photo)
                        tk.Label(body, image=photo, bg=BG).pack(anchor="w", pady=(0, th.px(8)))
                else:
                    row = tk.Frame(body, bg=BG)
                    row.pack(fill="x", pady=(0, th.px(6)))
                    tk.Label(row, text="\u2022", bg=BG, fg=ACCENT, font=th.font("bold", 12)).pack(side="left", anchor="n")
                    tk.Label(row, text=text, bg=BG, fg=FG, font=th.font("regular", 12), wraplength=th.px(430),
                             justify="left").pack(side="left", padx=(th.px(8), 0))
            last = n == len(pages) - 1
            Button(body, th, text="Got it" if last else "Next", kind="primary", width=110, height=40, radius=12, size=13,
                   command=w.destroy if last else lambda: show(n + 1), bg=BG).pack(anchor="e", pady=(th.px(10), 0))
            w.update_idletasks()
            if w.winfo_reqheight() > w.winfo_screenheight() - th.px(80) and size < len(PICTURE_WIDTHS) - 1:
                show(n, size + 1)
        show(0)

    def _ask_to_share(self):
        if "share_runs" in self.cfg:
            return
        yes = messagebox.askyesno(
            "Help other players pick maps?",
            "Share your farming, so everyone can see which maps give the most EXP for their "
            "class and level?\n\n"
            f"Every {RUN_SECONDS // 60} minutes of non-stop farming is shared: the map, your level, "
            "class and party size, and the EXP and mesos you got. Never your character's name.\n\n"
            f"They're combined with other players' on {STATS_SITE.split('//')[1]}. You can change "
            "this any time in Settings.", parent=self.root)
        self.cfg["share_runs"] = bool(yes)
        save_config(self.cfg)
        self.share_runs()

    def share_runs(self):
        """Sends finished runs to the stats site in the background (if agreed to)."""
        self._next_share = time.monotonic() + 1800
        if not self.cfg.get("share_runs") or self._sharing:
            return
        runs = self.autolog.unshared_runs()
        if not runs:
            return
        self._sharing, install = True, self.autolog.install

        def work():
            try:
                self.q.put(("shared", post_runs(install, runs)))
            except Exception:
                self.q.put(("shared", None))           # offline or the site is down: later
        threading.Thread(target=work, daemon=True).start()

    # ---- updates
    def check_updates(self, manual=False):
        """Asks GitHub for a newer version in the background; the answer comes via _pump."""
        def work():
            try:
                self.q.put(("update", (check_for_update(), manual, None)))
            except Exception as e:
                self.q.put(("update", (None, manual, e)))
        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, found, manual, error):
        parent = self.settings_win if manual and self.settings_win and self.settings_win.winfo_exists() \
            else self.root
        if error is not None:
            if manual:
                messagebox.showinfo("Updates", "Couldn't check for updates. Are you connected to the "
                                    "internet?", parent=parent)
            return                                      # at start: quietly try again next time
        if not found:
            if manual:
                messagebox.showinfo("Updates", f"You have the latest version (v{VERSION}).", parent=parent)
            return
        version, notes = found
        if not manual and self.cfg.get("skip_version") == version:
            return
        w = tk.Toplevel(self.root)
        w.title("Update available")
        w.configure(bg=BG, padx=self.theme.px(26), pady=self.theme.px(22))
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        th = self.theme
        tk.Label(w, text=f"v{version} is out", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text=f"You have v{VERSION}. Your log, notes and settings are kept when you update.",
                 bg=BG, fg=DIM, font=th.font("regular", 12)).pack(anchor="w", pady=(2, th.px(14)))
        tk.Label(w, text="WHAT'S NEW", bg=BG, fg=DIM, font=th.font("semibold", 10)).pack(anchor="w", pady=(0, th.px(6)))
        box = tk.Frame(w, bg=LIST_BG, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="both", expand=True, pady=(0, th.px(16)))
        text = tk.Text(box, width=62, height=12, wrap="word", bg=LIST_BG, fg=FG, relief="flat",
                       font=th.font("regular", 12), padx=th.px(12), pady=th.px(10), highlightthickness=0)
        scroll = ttk.Scrollbar(box, command=text.yview, style="Dark.Vertical.TScrollbar")
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", notes or "(No details were given for this version.)")
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        buttons = tk.Frame(w, bg=BG)
        buttons.pack(fill="x")

        def get_it():
            webbrowser.open(RELEASES_PAGE)                # always your own releases page
            w.destroy()

        def skip():
            self.cfg["skip_version"] = version
            save_config(self.cfg)
            w.destroy()
        Button(buttons, th, text="Get the update", kind="primary", width=140, height=40, radius=12, size=13,
               command=get_it, bg=BG).pack(side="left")
        Button(buttons, th, text="Not now", kind="secondary", width=96, height=40, radius=12, size=12,
               command=w.destroy, bg=BG).pack(side="left", padx=(th.px(8), 0))
        Button(buttons, th, text="Skip this version", kind="ghost", width=140, height=40, radius=12, size=12,
               command=skip, bg=BG).pack(side="right")

    def _oops(self, what, exc):
        """Something unexpected went wrong: note it in errors.log and keep going."""
        log_error(what, exc)
        self.status.set("Something went wrong, but the overlay keeps running. Details are in "
                        "errors.log (Settings > Open data folder).")

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "error":
                    self.recent.add(f"Error reading the screen: {payload}")
                    self.status.set("Something went wrong reading the screen. Trying again... "
                                    "(details are in errors.log: Settings > Open data folder)")
                elif kind == "update":
                    self._show_update(*payload)
                elif kind == "hotkey":
                    self._hotkey(payload)
                elif kind == "shared":
                    self._sharing = False
                    if payload:
                        self.autolog.mark_shared(payload)
                else:
                    self._handle(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self._oops("handling a reading", e)
        finally:
            try:
                self._feed_stats()                      # (at the data rate: the numbers follow the readings, not the redraws)
            except Exception as e:
                self._oops("updating the session numbers", e)
            if not self.stop_evt.is_set():
                self.root.after(self._tick_ms(0.5), self._pump)

    def _handle(self, r):
        t, now = self.tracker, time.monotonic()
        if r.image is not None:
            self.last_capture = r
        if r.mesos_image is not None:
            self.last_mesos = r
        if r.mesos_at:                                  # remember where the mesos box is
            self.cfg["mesos_box"] = list(r.mesos_at)
            self.reader.saved_mesos = tuple(r.mesos_at)
            save_config(self.cfg)
        if r.bar is not None:
            self.potion_bar = r.bar
            if self.cfg["potions_on"]:
                self.session.look_at_bar(r.bar)
            else:
                self.session.potions.pause()            # (switched on again later: nothing used meanwhile counts)
        self.meso.set_window(r.window, now)             # mesos moved at these windows aren't earned
        if r.mesos is not None:
            self.meso.observe(r.mesos, now, t.elapsed(), t.running)
        if r.party is not None:                         # believe a new party size once seen twice
            seen, n = self._party_seen
            n = n + 1 if r.party == seen else 1
            self._party_seen = (r.party, n)
            if n >= 2:
                self.party = r.party
        if self.reader.job:
            self.job = self.reader.job
        if r.map:
            self.map_read = r.map                       # as read (kept on record if renamed)
            # A slightly misread name of the map you're on still counts as that map.
            self.map_name = self.autolog.canonical(r.map, also=(self.map_name, self.session.map))
            waiting = self.session.waiting
            self.session.map_seen(self.map_name)
            self._after_pause_check(waiting)
        self.in_town = is_town(self.map_name)
        hidden = self._name_hidden()
        if hidden != self.map_hidden:
            self.map_hidden = hidden
            if not hidden:
                self.map_back_at = now          # (says "back" for a few seconds)
        if (self.session.on and self.map_name and self.reader.mode == "window" and engine.tesseract_ok
                and self.reader.map_blind_seconds() > MAP_BLIND_SECONDS):
            self.session.map_blind()
        self.note = r.note
        if r.candidates:
            if self.found_at is None or t.req is not None:
                self.found_at = now
            before = t.gained
            if r.percent_only:
                ok, note = self._observe_percent(r, now)
            else:
                ok, note = self.session.observe(r.candidates, r.confident, now)
                self.level_wait, self.pct_note = False, ""
                self._char_print = self.reader.name_print if self.reader.name_print is not None else self._char_print
            self.rejected = 0 if ok or self.level_wait else self.rejected + 1
            if self.rejected >= 10:                 # keeps not adding up: find the EXP bar again
                self.reader.box, self.rejected = None, 0
            if t.running and t.gained != before and not self.skip_log:
                self.autolog.gain(now, t.gained - before)
            if note == "died":
                self.died_at = now
            if note == "LEVEL UP!":
                self.level_up_at = now
                if self.level:
                    self.level += 1             # until the new level's EXP confirms it
            status = self._status_for(note)
        else:
            status = r.where
        if self.in_town:
            status += "\nIn a town: nothing is logged here."
        if self.map_hidden and self.tracker.running:
            status += (f"\nCan't see the minimap's name. Still tracking; it pauses if this lasts "
                       f"{MAP_BLIND_SECONDS} seconds.")
        elif self.map_back_at and now - self.map_back_at < 6 and self.session.on:
            status += "\nThe minimap's name is back."
        if self.note:
            status += f"\n{self.note}"
        if self.autolog.settle(now, self.tracker.running, **self.meso.for_log()):
            self.share_runs()                          # a finished run got its mesos (or went without)
        stale = self.meso.stale_note(now, self.tracker.running)       # (always asked, it keeps its own time)
        left_out = (self.autolog.alert(now, self.tracker.running) or self.meso.ignored_note(now) or stale
                    or self._net(self.tracker.elapsed())[1])
        if left_out:
            status += f"\n{left_out}"
        self.status.set(status)
        self.recent.add(status)
        self.level = level_for_req(t.req) or self.level
        self._log_tick(now)

    def _observe_percent(self, r, now):
        """A bar with only a %: the level comes from the badge (or the level known before, or typed in)."""
        level, source = r.level or (None, None)
        if self._check_character():
            self.level_wait = True
            return False, "another character"           # (this reading's level may be the other one's)
        if level is None and self.tracker.req is not None and self.tracker.level_source is None and not self._same_char:
            self.tracker.forget_level()                 # a level carried over from the number, but the character can't be told
        ok, note = self.session.observe_percent(r.candidates[0][1], level, source, now)
        self.level_wait = note == "waiting to read your level"
        self.pct_note = note
        if note.startswith(("percentage wrapped", "level up?")):
            self.reader.recheck_level()                 # it may have gone up: look at the badge now
        return ok, note

    def _check_character(self):
        """Another character (its name row looks different) has its own level: start over for it. Only a
        print that differs on two looks in a row counts, so something drawn over the name can't.
        Returns True when it started over."""
        cur = self.reader.name_print
        if cur is None:
            self._same_char = False
            return False
        if cur is self._char_seen:
            return False
        self._char_seen = cur
        if self._char_print is None or level_ocr.same_name(self._char_print, cur):
            self._same_char = self._char_print is not None
            self._char_print, self._char_diff = (cur if self._char_print is None else self._char_print), 0
            return False
        self._same_char = False
        self._char_diff += 1
        if self._char_diff < 2:
            return False
        self.reader.level = None
        self.reader.recheck_level()
        self.reset()
        self._char_print = self._char_seen = cur          # (the new character is the one to compare with from now on)
        self.other_char_at = time.monotonic()
        return True

    def _can_type_level(self):
        """Typing the level in is offered only when it can help: a %-only bar, no level known, and
        reading it from the badge keeps failing."""
        t = self.tracker
        return bool(t.req is None and self.level_wait and self.reader.level is None
                    and self.reader.level_failures >= LEVEL_FAILS_FOR_FALLBACK)

    def type_level(self, parent=None):
        """Settings > Level fallback: the player types the level shown in the game. Session only."""
        if not self._can_type_level():
            messagebox.showinfo("Level fallback", "Not needed right now: the level is read from the game by itself.",
                                parent=parent or self.root)
            return
        level = simpledialog.askinteger("Level fallback", "Your character's level, as the game shows it:",
                                        parent=parent or self.root, minvalue=1, maxvalue=len(EXP_TABLE))
        if level is None or not self._can_type_level():      # (cancelled, or the badge was read while it was open)
            return
        self.tracker.set_level(level, "typed")      # (nothing was counted without a level, so a session that runs carries on)
        self.level_wait, self.pct_note = False, ""
        self._char_print = self.reader.name_print
        self.status.set(f"Using Lv {level} as you typed it. EXP is estimated from the % until the badge can be read.")

    def _status_for(self, note):
        """What the status line says while the EXP bar is being read (details: View capture)."""
        t = self.tracker
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return f"Level up! You're now Lv {self.level}." if self.level else "Level up!"
        if self.hotkey_note and time.monotonic() - self.hotkey_note[1] < 4:
            return self.hotkey_note[0]
        if self.died_at and time.monotonic() - self.died_at < 30 and t.last_loss:
            return f"You died and lost {t.last_loss:.1f}% EXP. That isn't taken off your EXP/hr."
        if self.rejected >= 5:
            return "Having trouble reading the EXP bar clearly. View capture shows what it sees."
        if self.other_char_at and time.monotonic() - self.other_char_at < 8:
            return "This looks like another character: starting over for it."
        if self.level_wait and t.req is None:
            if self.reader.mode == "region":
                return ("Selected area: the level badge can't be read from here. "
                        "Settings, Level fallback lets you type your level in.")
            if self._can_type_level():
                return ("The EXP bar shows only a percentage, and your level can't be read from the badge. "
                        "Settings, Level fallback lets you type it in.")
            return "The EXP bar shows only a percentage. Reading your level from the badge..."
        if self.pct_note.startswith("level changed"):
            return "Your level changed. Press Reset to start again on the new level."
        if t.req is None:
            # Normally the level is known within seconds; if not, it needs an EXP gain.
            if self.found_at is None or time.monotonic() - self.found_at < 8:
                return "Found your EXP bar. Working out your level..."
            return "Found your EXP bar. Your level shows after your next EXP gain."
        s = self.session
        if s.waiting == "idle":
            return (f"Paused: no EXP for {s.gap} seconds, so this time doesn't count. "
                    "It carries on at your next EXP gain.")
        if s.waiting == "map":
            return ("Paused: you left the map. It carries on at your next EXP gain "
                    "(from zero if that's on a different map).")
        if s.waiting == "blind":
            return ("Paused: the minimap's name can't be seen, so it can't tell which map you're on. "
                    "It carries on when the minimap is back.")
        if t.running:
            where = {"badge": "read from the badge", "typed": "typed by you"}.get(t.level_source, "known from before")
            how = f" Estimated from the % (the bar shows no number; your level was {where})." if t.percent_mode else ""
            return ("Tracking your EXP. Not logging this run." if self.skip_log else "Tracking your EXP.") + how
        if t.elapsed() > 0:
            return "Paused. Press Resume to carry on."
        return "Ready. Press Start when you begin farming."

    def _name_hidden(self):
        """True while tracking and the minimap's name hasn't been seen for a few seconds."""
        return bool(self.session.on and self.map_name and self.reader.mode == "window" and engine.tesseract_ok
                    and self.reader.map_blind_seconds() > MAP_HIDDEN_SECONDS)

    def _log_tick(self, now):
        if not self.tracker.running or self.skip_log or self.in_town:
            return
        runs = len(self.autolog.runs)
        potions_now = None
        if self.cfg["potions_on"]:
            used = self.session.potions.used
            potions_now = (dict(used), self.session.potions.cost(self._prices().mapping()))
        t = self.tracker
        extras = dict(self.life, deaths=t.deaths, levels=t.levels)
        if t.req and t.exp is not None:
            extras.update(lvl_total=int(t.req), remaining=max(0, int(t.req - t.exp)))
        entry = self.autolog.tick(now, self.map_name, self.level, self.party, job=self.job, map_read=self.map_read,
                                  hidden=self.map_hidden, potions=potions_now, extras=extras,
                                  estimated=self.tracker.estimated and self.tracker.percent_mode, **self.meso.for_log())
        if len(self.autolog.runs) > runs:
            self.share_runs()                          # a run just finished
        if entry:
            self.last_logged = now
            self._fill_log()

    def _refresh(self):
        try:
            self._check_screen()
            self._fit_settings()
            self._update_display()
        except Exception as e:
            self._oops("updating the window", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(self._ui_ms(), self._refresh)

    def _state(self):
        """A word for the status pill: where things stand."""
        t, sess = self.tracker, self.session
        if self.reader.mode == "window" and not self.reader.hwnd:
            return "waiting", "Looking for the game"
        if self.rejected >= 5:
            return "alert", "Can't read the EXP bar"
        if t.req is None:
            return "waiting", "Reading your EXP bar"
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return "tracking", "Level up!"
        if sess.waiting:
            return "paused", "Paused"
        if t.running:
            return "tracking", f"Tracking \u00b7 Lv {self.level}" if self.level else "Tracking"
        return ("paused", "Paused") if t.elapsed() > 0 else ("ready", "Ready")

    def _potion_table(self):
        """The shipped item table, for prices (the reader loads its own copy at the first look at the bar)."""
        if self._price_table is None:
            try:
                self._price_table = potions.PotionTable.load(ASSETS / "potion_table.json")
            except Exception as e:
                log_error("loading the potion table", e)
        return self._price_table

    def _net(self, el):
        """(Net mesos/hr as text, a note or None): mesos/hr less the potion cost per hour. A dash when
        potions are off, nothing has run yet, or a potion that was used was priced at 0 by the player."""
        dash = "—"
        if not self.cfg["potions_on"] or not el:
            return dash, None
        prices, used = self._prices(), self.session.potions.used
        note = prices.note(used)
        if note:
            return dash, note
        gross = self.meso.per_hour(el)
        if gross is None:
            return dash, None
        cost = self.session.potions.cost(prices.mapping())
        return fmt_num(gross - cost / el * 3600), None

    def _prices(self):
        table = self._potion_table()
        return potions.Prices(table.prices if table else {}, self.cfg["potion_prices"])

    def _update_display(self):
        t, now = self.tracker, time.monotonic()
        rate, rolling = t.rate(), t.rolling_rate()
        shown = (t.exp, t.pct) if t.exp is not None else t.last_read
        best_rate = rolling or rate
        level = level_for_req(t.req) or self.level
        eta = fmt_eta((t.req - t.exp) / best_rate * 3600) if (
            t.req and t.exp is not None and best_rate and best_rate > 0) else None
        if t.running and rolling and now - self._trend_at >= 15:      # a point on the trend line every 15 s
            self._trend.append(rolling)
            self._trend_at = now
        m, el = self.meso, t.elapsed()
        if m.value is None:
            mesos = "move this window" if self.reader.mesos_covered else "open inventory"
        else:
            mesos = fmt_num(m.value) + (f"  (+{fmt_short(m.gained)})" if m.gained > 0 else "")
        state, state_text = self._state()
        net = self._net(el)[0]
        est = lambda text: APPROX + text if t.estimated and text and text != "\u2014" else text     # (worked out from a % alone)
        self.view.show(
            rate=approx_num(rate, t.estimated) if rate is not None else None,
            delta=(rolling / rate - 1) if rate and rolling else None,
            spark=list(self._trend), pct=shown[1] if shown else None,
            eta=f"Lv {level + 1} in {eta}" if level and eta else f"Lv {level}" if level else "",
            level=level or None, eta_time=eta or "",
            mesos=fmt_num(m.per_hour(el)),
            net=net if self.cfg["potions_on"] and net != "\u2014" else "",
            tiles={"Last 5 min": approx_num(rolling, t.estimated), "Mesos / hr": fmt_num(m.per_hour(el)),
                   "Net / hr": net, "Elapsed": fmt_dur(el)},
            details={"% / hr": f"{rate / t.req * 100:.2f}%" if rate and t.req else "\u2014",
                     "Gained": est(f"{fmt_short(t.gained)}{f'  (+{t.levels} lvl)' if t.levels else ''}"),
                     "Current": est(f"{fmt_num(shown[0])} [{shown[1]:.2f}%]") if shown else "\u2014",
                     "Mesos": mesos, "Mesos / 5 min": fmt_num(m.per_5min(el)),
                     "Potion cost": (fmt_num(self.session.potions.cost(self._prices().mapping()))
                                     if self.cfg["potions_on"] else "—"),
                     "Net mesos / hr": net,
                     "Left out": (f"+{fmt_short(m.left_out[0])} / -{fmt_short(m.left_out[1])}"
                                  if any(m.left_out) else "—")},
            state=state, state_text=state_text)
        if self.level_btn is not None and self.level_btn.winfo_exists():
            self.level_btn.set(enabled=self._can_type_level())
        waiting = self.session.waiting
        self.session.tick(now)
        if self.session.waiting != waiting:             # just paused by itself: say why now
            self._after_pause_check(waiting)
            self.status.set(self._status_for(""))
        self._log_tick(now)
        self.log_status.set(self._log_status_text(now))
        self.reader.own_rect = self._window_rect()
        if now >= self._next_share:                   # retry runs that couldn't be sent
            self.share_runs()

    # ---- buttons
    def _hotkey(self, action):
        if action == "start":
            self.toggle()
            done = "started" if self.session.on else "paused"
        elif action == "reset":
            self.reset()
            done = "reset"
        elif action == "skip":
            self.toggle_skip_log()
            done = "not logging this run" if self.skip_log else "logging this run again"
        else:
            self.set_compact(not self.compact)
            return
        self.hotkey_note = (f"{self.cfg['hotkeys'][action]}: {done}.", time.monotonic())
        self.status.set(self.hotkey_note[0])

    def toggle(self):
        if self.session.on:                     # (also while it's paused by itself)
            self.recent.add("You paused")
            self.session.pause()
            self._set_start("Resume")
        else:
            self.recent.add("You pressed Start")
            self.session.start(time.monotonic())
            self.reader.request_map()           # read the map name now
            self._set_start("Pause")
        self.autolog.reset_clock()
        if self.tracker.exp is not None:
            self.status.set(self._status_for(""))   # right away, not at the next read

    def _set_start(self, text):
        """The Start button: gold when pressing it starts something, quiet while it's running."""
        self._start_text = text
        self.view.set_start(text, "pause" if text == "Pause" else "play", "secondary" if text == "Pause" else "primary")

    def reset(self):
        self.session.reset()
        self.autolog.reset_clock()
        self._char_print = self._char_seen = self.other_char_at = None      # (what was known about the character is over too)
        self._char_diff, self._same_char = 0, False
        self.pct_note, self.level_wait = "", False
        self.skip_log = False
        self._show_skip_log()
        self._set_start("Start")
        # Also look for the minimap, inventory and party list again (e.g. after moving them).
        self.reader.look_again()
        self.map_name, self.party, self._party_seen = None, 1, (None, 0)
        self.cfg.pop("mesos_box", None)
        save_config(self.cfg)
        self.status.set("Reset. Looking for the map name, inventory and party list again...")

    def toggle_skip_log(self):
        self.skip_log = not self.skip_log
        self.autolog.reset_clock()              # nothing from before or during it is logged
        self._show_skip_log()
        self.status.set("This run won't be logged. Logging turns back on when you leave the map "
                        "or stop getting EXP." if self.skip_log else "Logging this run again.")

    def _show_skip_log(self):
        self.view.logging.set(not self.skip_log, "Not logging" if self.skip_log else "Log this run")

    def _after_pause_check(self, waiting_before):
        """When the run ends (tracking pauses by itself), "Don't log" turns off again, so
        the next run is logged. (Pressed while already paused, it's for the next run.)"""
        if self.skip_log and waiting_before is None and self.session.waiting:
            self.skip_log = False
            self._show_skip_log()

    def _new_source(self):
        # New place to read from -> new baseline, but keep the session totals.
        self.tracker.exp = self.tracker.pct = None
        self.tracker._pending = self.tracker._pending_lvl = None
        self.autolog.reset_clock()
        save_config(self.cfg)

    def _show_area_mode(self):
        pass                                              # (the More menu reads the mode when it opens)

    def toggle_area(self):
        if self.reader.mode == "region":
            self.follow_game()
        else:
            self.select_area()

    def follow_game(self):
        self.reader.use_window()
        self.cfg["mode"] = "window"
        self._new_source()
        self._show_area_mode()
        self.status.set("Following the MapleStory window...")

    def select_area(self):
        def done(region):
            if region:
                self.reader.use_region(region)
                self.cfg.update(mode="region", region=region)
                self._new_source()
                self._show_area_mode()
                self.status.set("Using your selected area. Click Follow game to go back to "
                                "finding it automatically.")
        RegionSelector(self.root, done)

    @staticmethod
    def _boxed_image(bgr, boxes):
        """A screenshot crop, enlarged, with a box drawn around each digit found."""
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        scale = min(4.0, max(1.0, 120 / img.height), 900 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        colors = {"exp": "#ffcc33", "pct": "#33ccff", "mesos": "#66dd66"}
        for x0, y0, x1, y1, kind in boxes:
            draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                           outline=colors[kind], width=2)
        return img

    def _boxed(self, bgr, boxes):
        return ImageTk.PhotoImage(self._boxed_image(bgr, boxes))

    def _class_line(self):
        """What the class reading shows: (picture or None, boxes, a line saying how it went)."""
        look = self.reader.job_look
        if self.job:
            how = f"Class. Read: {self.job}"
        elif look:
            how = f"Class. Not read: {look.get('why') or 'no clear look yet'}"
        else:
            how = "Class. Not read yet (it's tried every few seconds once the EXP bar is found)"
        return (look or {}).get("image"), (look or {}).get("boxes", []), how

    # ---- Session and Solo / Party windows (numbers from session_stats.py; nothing here changes the tracking)
    def _split_debug(self, text):
        """What the EXP split saw (lines read, gains, matches), appended to split_debug.log in the settings folder.
        Small, and cut down to the last part when it passes 1 MB."""
        path = DATA_DIR / "split_debug.log"
        try:
            if path.exists() and path.stat().st_size > 1_000_000:
                path.write_text(path.read_text(encoding="utf-8", errors="replace")[-200_000:], encoding="utf-8")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except Exception:
            self.stats.on_debug = None

    def _feed_stats(self):
        t, m = self.tracker, self.meso
        new = []
        while self.reader.msg_queue:                                    # (the message thread's new lines)
            try:
                new.append(self.reader.msg_queue.popleft())
            except IndexError:
                break
        # Mesos come from the game's "You have gained mesos (+N)" lines once the inventory has set the start.
        for kind, amount in new:
            if kind == "own":
                self.life["kills"] += 1                                 # (each "You received EXP" is one kill)
            elif kind == "mesos":
                self.life["mesos_drops"] += 1
                self.life["mesos_text"] += amount
            if kind == "mesos":
                why = self.meso.add_text(amount, t.elapsed(), time.monotonic(), t.running)
                self._split_debug(f"mesos +{amount}: " + ("counted" if why is True else f"not counted ({why})"))
        new = [n for n in new if n[0] != "mesos"]
        self.stats.update(t.running, self.party, t.gained, m.gained, t.levels, t.deaths, self.map_name, self.level,
                          new_lines=new)

    def toggle_samples(self):
        """Menu: saves pictures of the game's EXP messages to the program's samples folder (up to 300), so the
        exact digit reading for them can be built. Nothing is sent anywhere."""
        r = self.reader
        if r.sample_dir:
            r.sample_dir = None
            self.status.set(f"Stopped. {r.samples_saved} pictures were saved in the samples folder.")
            return
        r.sample_dir, r.samples_saved = HOME_DIR / "samples", 0
        self.status.set("Collecting EXP message pictures into the samples folder while yellow messages show. "
                        "Farm for 5 to 10 minutes in a party, then stop it from the same menu.")

    def open_session_log(self):
        if not SESSION_LOG_PATH.exists():
            messagebox.showinfo("Session log", "Nothing has been logged yet.\n\nThe file is made when you press Start.",
                                parent=self.root)
            return
        PLATFORM.show_file(SESSION_LOG_PATH)

    def open_stat_window(self, kind):
        """The Session window (a floating window; brought forward if it's open). The Solo / Party view has
        its own methods below: it can sit inside the main window or float next to it."""
        if kind == "party":
            return self.toggle_party()
        old = self._stat_wins.get(kind)
        if old is not None and old.winfo_exists():
            old.lift()
            return
        th, st = self.theme, self.stats
        win = tk.Toplevel(self.root)
        win.title("Session")
        win.configure(bg=BG)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        win.resizable(False, False)
        for delay in (0, 150, 800):
            win.after(delay, lambda: PLATFORM.style_window(win, INK, TEXT_DIM) if win.winfo_exists() else None)
        body = tk.Frame(win, bg=BG)
        body.pack(padx=th.px(18), pady=th.px(16))
        dim = dict(bg=BG, fg=DIM, font=th.font("regular", 11))
        val = dict(bg=BG, fg=FG, font=th.font("semibold", 12))
        cells = {}
        names = ["Time", "EXP", "EXP / hr", "Mesos", "Mesos / hr", "Levels gained", "Deaths", "Map", "Party now"]
        for i, name in enumerate(names):
            tk.Label(body, text=name, anchor="w", **dim).grid(row=i, column=0, sticky="w", pady=th.px(3))
            cells[name] = tk.Label(body, text="", anchor="e", **val)
            cells[name].grid(row=i, column=1, sticky="e", padx=(th.px(24), 0), pady=th.px(3))

        def refresh():
            cells["Time"].config(text=fmt_dur(st.seconds))
            cells["EXP"].config(text=fmt_num(st.exp))
            cells["EXP / hr"].config(text=self._per_hour(st.exp, st.seconds))
            cells["Mesos"].config(text=fmt_num(st.mesos))
            cells["Mesos / hr"].config(text=self._per_hour(st.mesos, st.seconds))
            cells["Levels gained"].config(text=str(st.levels))
            cells["Deaths"].config(text=str(st.deaths))
            cells["Map"].config(text=st.map or "\u2014")
            cells["Party now"].config(text="Solo" if st.now_party <= 1 else f"{st.now_party} players")
        tk.Label(win, text="Counts only while tracking. Reset Session clears just this window: your main numbers "
                           "and the log are not touched.", wraplength=th.px(420), justify="left", **dim).pack(
            anchor="w", padx=th.px(18), pady=(0, th.px(8)))
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=th.px(18), pady=(0, th.px(16)))
        for text, cmd in (("Reset Session", self._reset_session_stats), ("Open log (CSV)", self.open_session_log)):
            Button(bar, th, text=text, kind="secondary", width=150, height=36, radius=10, size=12, command=cmd,
                   bg=BG).pack(side="left", padx=(0, th.px(8)))

        def loop():
            if not win.winfo_exists() or self.stop_evt.is_set():
                return
            try:
                refresh()
            except Exception as e:
                log_error("updating the stats window", e)
            win.after(self._ui_ms(), loop)
        self._stat_wins[kind] = win
        loop()

    @staticmethod
    def _per_hour(value, seconds):
        return fmt_num(value / seconds * 3600) if seconds >= 10 else "\u2014"

    # ---- Solo / Party: a panel in the main window ("docked"), or a window that floats beside it and moves
    # with it ("floating"). The same table either way; "closed" hides it.
    def _party_table(self, parent):
        """The table (and its note and buttons) in `parent`. Returns the function that refreshes it."""
        th, st = self.theme, self.stats
        dim = dict(bg=BG, fg=DIM, font=th.font("regular", 11))
        val = dict(bg=BG, fg=FG, font=th.font("semibold", 11))
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="Solo / Party", bg=BG, fg=FG, font=th.font("bold", 13)).pack(side="left")
        where = "Pop out" if self.party_view == "docked" else "Dock"
        Button(top, th, text=where, kind="ghost", width=70, height=28, radius=9, size=11,
               command=self._party_move, bg=BG).pack(side="right")
        Button(top, th, text="Reset Session", kind="ghost", width=110, height=28, radius=9, size=11,
               command=self._reset_session_stats, bg=BG).pack(side="right", padx=(0, th.px(4)))
        table = tk.Frame(parent, bg=BG)
        table.pack(fill="x", pady=(th.px(8), th.px(4)))
        tk.Label(parent, text="Solo = while you were alone in the party list. Party = while you were in a party. "
                              "Under Party, from the game's EXP messages (only for the EXP they add up to exactly): "
                              "Solo = EXP of your own kills (white message), Party bonus = what the party added "
                              "(yellow, \"Bonus EXP for PARTY\"), Party kills = EXP of a party member's kill "
                              "(yellow \"You received EXP\"). The rest is Other. "
                              "% of level = how much of the level's EXP bar it is. % of total = its share of the Total EXP.",
                 wraplength=th.px(330), justify="left", **dim).pack(anchor="w", pady=(0, th.px(4)))
        heads = ["", "Time", "EXP", "% of level", "% of total", "Mesos"]
        for c, name in enumerate(heads):
            tk.Label(table, text=name, anchor="e", **dim).grid(row=0, column=c, sticky="e", padx=(th.px(10) if c else 0, 0))
        made = []
        colors = {"Solo": FG, "Party bonus": "#F2C94C", "Party kills": "#B79F45"}      # (the sub-rows under Party; white / yellow like the game's lines)

        def refresh():
            while made:
                made.pop().destroy()
            total, req = st.exp, self.tracker.req
            approx = "\u2248" if self.tracker.levels else ""                  # (a level-up in between: the bar's size changed)
            for r, (name, b) in enumerate(st.rows(), start=1):
                of_level = f"{approx}{b.exp / req * 100:.2f}%" if req else "\u2014"
                vals = [name, fmt_dur(b.seconds), fmt_num(b.exp), of_level,
                        f"{b.exp / total * 100:.0f}%" if total else "\u2014",
                        "\u2014" if b.mesos is None else fmt_num(b.mesos)]
                color = colors.get(name.strip()) if name.startswith(" ") else None
                for c, v in enumerate(vals):
                    look = dict(val, fg=color) if color and c in (0, 2, 3, 4) else val
                    lab = tk.Label(table, text=v, anchor="w" if c == 0 else "e", **look)
                    lab.grid(row=r, column=c, sticky="w" if c == 0 else "e", padx=(th.px(10) if c else 0, 0), pady=th.px(2))
                    made.append(lab)
        return refresh

    def toggle_party(self):
        self.set_party_view("closed" if self.party_view != "closed" else "docked")

    def _party_move(self):
        mode = "floating" if self.party_view == "docked" else "docked"
        self.root.after(10, lambda: self.set_party_view(mode))       # (not from inside the button that is about to go)

    def set_party_view(self, mode, remember=True):
        """mode: "closed", "docked" (a panel under the tracker) or "floating" (a window beside it)."""
        self._party_close()
        if mode == "docked" and self.compact:
            mode = "floating"                            # (compact mode has no room for a panel)
        self.party_view = mode
        th = self.theme
        if mode == "docked":
            panel = self.party_panel = tk.Frame(self.root, bg=BG)
            panel.grid(row=1, column=0, sticky="ew", padx=th.px(16), pady=(0, th.px(12)))     # (under the tracker only: the log keeps the whole right side)
            self._party_refresh = self._party_table(panel)
        elif mode == "floating":
            win = self.party_win = tk.Toplevel(self.root)
            win.title("Solo / Party")
            win.configure(bg=BG)
            win.transient(self.root)                     # stays with the main window: above it, closed with it, no taskbar button
            win.attributes("-topmost", bool(self.cfg["topmost"]))
            win.resizable(False, False)
            for delay in (0, 150, 800):
                win.after(delay, lambda: PLATFORM.style_window(win, INK, TEXT_DIM) if win.winfo_exists() else None)
            body = tk.Frame(win, bg=BG)
            body.pack(padx=th.px(16), pady=th.px(14))
            self._party_refresh = self._party_table(body)
            self._party_at = None
            win.protocol("WM_DELETE_WINDOW", lambda: self.set_party_view("closed"))
            win.after(30, self._party_place)
        self.view.party_btn.set(kind="secondary" if mode != "closed" else "ghost")
        self.root.update_idletasks()
        if not self.compact:
            self.root.geometry("" if not self.log_open else self.root.geometry())
        self._settle()
        if remember:
            self.cfg["party_view"] = mode
            save_config(self.cfg)
        self._party_tick()

    def _party_close(self):
        for name in ("party_panel", "party_win"):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    w.destroy()
                except tk.TclError:
                    pass
                setattr(self, name, None)
        self._party_refresh = None

    def _party_place(self):
        """The floating view sits just right of the main window and follows it."""
        win = getattr(self, "party_win", None)
        if win is None or not win.winfo_exists():
            return
        r = self.root
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()      # (of the screen the main window is on)
        x, y = r.winfo_rootx() + r.winfo_width() + 8, r.winfo_rooty()
        if x + w > sw:                                              # no room on the right: put it on the left...
            x = r.winfo_rootx() - w - 8
        if x < 0:                                                   # ...and if that doesn't fit either, on the screen's edge
            x = max(0, sw - w)
        y = max(0, min(y, sh - h - 40))
        if getattr(self, "_party_at", None) != (x, y):              # (only when the main window moved or changed size)
            self._party_at = (x, y)
            win.geometry(f"+{x}+{y}")
        win.lift()

    def _party_tick(self):
        if getattr(self, "_party_job", None):
            try:
                self.root.after_cancel(self._party_job)
            except (tk.TclError, ValueError):
                pass
            self._party_job = None
        if self.party_view == "closed" or self.stop_evt.is_set():
            return
        try:
            if self._party_refresh:
                self._party_refresh()
            self._party_place()
        except Exception as e:
            log_error("updating the Solo / Party view", e)
        self._party_job = self.root.after(self._ui_ms(), self._party_tick)

    def _reset_session_stats(self):
        self.stats.reset()
        self.status.set("Session reset. Only the Session and Solo / Party numbers were cleared.")

    def view_capture(self):
        r, mr = self.last_capture, self.last_mesos
        if not r and not mr:
            messagebox.showinfo("View capture", "Nothing has been read from the game yet.\n\n"
                                "Make sure MapleStory is open and its EXP bar is showing.",
                                parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Last capture")
        win.configure(bg=BG)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            win.after(delay, lambda: PLATFORM.style_window(win, INK, TEXT_DIM) if win.winfo_exists() else None)
        th = self.theme
        shots = self._look_shots()
        win._imgs = []
        for shot, boxes, text in shots:
            if shot is not None and shot.size:
                win._imgs.append(self._boxed(shot, boxes))
                tk.Label(win, image=win._imgs[-1], bg=BG).pack(padx=th.px(16), pady=(th.px(16), th.px(4)))
            else:
                tk.Frame(win, bg=BG, height=th.px(12)).pack()
            tk.Label(win, text=text, bg=BG, fg=FG, font=th.font("semibold", 12), wraplength=th.px(560),
                     justify="left").pack(anchor="w", padx=th.px(16))
        bottom = tk.Frame(win, bg=BG)
        bottom.pack(fill="x", padx=th.px(16), pady=(th.px(12), th.px(16)))
        tk.Label(bottom, text="Boxes show where each digit was found.", bg=BG, fg=DIM,
                 font=th.font("regular", 11)).pack(side="left")
        Button(bottom, th, text="Something's wrong? Report a bug", kind="secondary", width=240, height=36, radius=10,
               size=12, command=lambda: self.report_bug(win), bg=BG).pack(side="right")

    # ---- bug reports
    def report_bug(self, parent=None):
        """Opens a new GitHub issue in the browser, filled in with what the tracker knows, and
        saves a picture and a text file of the details for the player to drag in. Nothing is
        sent by the tracker itself: the player sees everything and clicks Submit (or doesn't)."""
        parent = parent or self.root
        if not messagebox.askokcancel(
                "Report a bug",
                "Your browser will open a bug report on GitHub, already filled in with your "
                "version and what the overlay was reading (nothing personal).\n\n"
                "1. Write a sentence about what went wrong.\n"
                "2. Drag in the two files from the folder that opens.\n"
                "3. Click Submit.\n\n"
                "You need a free GitHub account.", parent=parent):
            return
        screen = messagebox.askyesno(
            "Report a bug",
            "Also save a small picture of the whole game window?\n\n"
            "It shows exactly what the overlay sees, which helps a lot when something can't be found. "
            "But it can include chat and character names, so only drag it in if you're happy with that.\n\n"
            "(No is fine: the report works without it.)", default="no", parent=parent)
        files = self._save_report_files(screen)
        query = urllib.parse.urlencode({"title": f"Bug: (describe it here) [v{VERSION}]",
                                        "body": self._report_text(files)})
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new?{query}")
        shown = files.get("picture") or files.get("details")
        if shown:
            try:
                PLATFORM.show_file(shown)
            except Exception as e:
                log_error("opening the bug report's folder", e)

    @staticmethod
    def _private(text):
        """Without your Windows user name (it's in file paths)."""
        for secret in {str(Path.home()), os.environ.get("USERNAME", "")}:
            if len(secret) > 2:
                text = text.replace(secret, "<you>")
        return text

    def _report_facts(self):
        """Everything a bug report says, as a plain dict (see report.py)."""
        r, t = self.last_capture, self.tracker
        d = self.reader.diagnostics()
        rect = d["rect"]
        monitors = []
        try:
            with mss.mss() as sct:
                monitors = [(m_["width"], m_["height"]) for m_ in sct.monitors[1:]]
        except Exception as e:
            log_error("listing the screens for a bug report", e)
        errors = ""
        try:
            errors = self._private(ERROR_LOG_PATH.read_text(encoding="utf-8", errors="replace"))[-2500:]
        except Exception:
            pass
        now = time.monotonic()
        h = self.health
        up = max(1.0, now - self.started)
        tess = engine.TESS
        return {
            "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "system": PLATFORM.version(),
            "python": sys.version.split()[0], "monitors": monitors, "uptime_s": up,
            "cpu_pct": round((time.process_time() - self.cpu0) / up * 100, 1),
            "dpi_awareness": PLATFORM.dpi_awareness(),
            "dpi": PLATFORM.screen_dpi(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2) if rect else None,
            "tesseract": {"found": bool(tess), "version": tess.version() if tess else None,
                          "where": ("included" if str(APP_DIR) in str(tess.cmd) else "installed") if tess else None},
            "game": {"mode": d["mode"], "found": d["found"], "rect": rect, "gh": d["gh"], "region": d["region"],
                     "minimized": bool(d["found"] and not rect and d["mode"] == "window"),
                     "fullscreen": bool(rect and any(rect[2] == mw and rect[3] == mh for mw, mh in monitors))},
            "overlay": {"rect": d["own_rect"], "covering": d["covering"]},
            "reader": {**{k: d[k] for k in ("exp_box", "exp_misses", "map_box", "mesos_box", "mesos_fails", "two_boxes",
                                            "mesos_learned", "party_area", "job_why")},
                       "level_unknown": t.req is None},
            "health": {"alive": h.alive and self._worker_alive(), "polls": h.polls, "errors": h.errors,
                       "last_error": h.last_error, "average_ms": h.average_ms(), "max_ms": h.max_ms,
                       "last_ms": h.last_ms, "age": None if h.last_at is None else now - h.last_at},
            "rejected": self.rejected, "status": self.status.get().replace("\n", " | "),
            "last_exp": r.text if r else None, "level": self.level, "job": self.job, "map": self.map_name,
            "party": party_label(self.party),
            "mesos": fmt_num(self.meso.value) if self.meso.value is not None else None, "tracking": bool(self.session.on),
            "tracker": f"paused: {self.session.waiting or 'no'}, gained {fmt_num(t.gained)} EXP in {fmt_dur(t.elapsed())}",
            "settings": {"mode": self.reader.mode, **{k: self.cfg.get(k) for k in ("gap_seconds", "transparency", "topmost", "ui_scale",
                                                      "compact", "hotkeys_on", "share_runs", "check_updates")}},
            "timeline": [self._private(x) for x in self.recent.lines()], "errors": errors,
        }

    def _worker_alive(self):
        return getattr(self, "_worker_thread", None) is None or self._worker_thread.is_alive()

    def _look_shots(self):
        """What each reader last saw, with a line on how it went: [(picture or None, boxes, text)]."""
        r, mr = self.last_capture, self.last_mesos
        shots = []
        if r:
            shots.append((r.image, r.boxes, f"EXP bar. Read: {r.text or 'nothing'}   ({r.where})"))
        if mr:
            read = fmt_num(mr.mesos) if mr.mesos is not None else "nothing (waiting for a clear look)"
            shots.append((mr.mesos_image, mr.mesos_boxes, f"Mesos. Read: {read}"))
        if r:
            shots.append(self._class_line())
            look = self.reader.map_look
            if look:
                shots.append((look["image"], [], f"Map name. Read: {look['text'] or 'nothing (not clear yet)'}"))
        return shots

    def _save_report_files(self, with_screen=False):
        """The picture of what was read, the details as text, and (if asked) the game window:
        saved in the data folder. Returns {"picture", "details", "screen"} (missing ones left out)."""
        files = {}
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_error("making the data folder for a bug report", e)
            return files
        facts = None
        try:
            facts = self._report_facts()
            path = DATA_DIR / "bug-report.txt"
            path.write_text(self._private(report.details(facts)), encoding="utf-8")
            files["details"] = path
        except Exception as e:
            log_error("saving the bug report details", e)
        self._report_facts_cache = facts
        picture = self._save_report_picture()
        if picture:
            files["picture"] = picture
        if with_screen:
            screen = self._save_report_screen()
            if screen:
                files["screen"] = screen
        return files

    def _captioned(self, image, text):
        """A crop with a line of text above it."""
        try:
            font = ImageFont.truetype(str(ASSETS / "fonts" / "ManropeSemiBold.ttf"), 15)
        except Exception:
            font = ImageFont.load_default()
        w = max(image.width if image is not None else 0, 760)
        sheet = Image.new("RGB", (w, (image.height if image is not None else 0) + 26), BG)
        ImageDraw.Draw(sheet).text((2, 3), text[:110], fill=FG, font=font)
        if image is not None:
            sheet.paste(image, (0, 26))
        return sheet

    def _save_report_picture(self):
        """What each reader last saw (EXP bar, mesos, class, map name), with captions, as one PNG."""
        parts = []
        for shot, boxes, text in self._look_shots():
            image = self._boxed_image(shot, boxes) if shot is not None and shot.size else None
            parts.append(self._captioned(image, text))
        if not parts:
            return None
        sheet = Image.new("RGB", (max(p.width for p in parts), sum(p.height + 8 for p in parts)), BG)
        y = 0
        for p in parts:
            sheet.paste(p, (0, y))
            y += p.height + 8
        path = DATA_DIR / "bug-report.png"
        try:
            sheet.save(path)
            return path
        except Exception as e:
            log_error("saving the bug report picture", e)
            return None

    def _save_report_screen(self):
        """The game window as the overlay sees it (this window blacked out), shrunk, as a JPEG."""
        rect = self.reader.last_rect
        if not rect:
            return None
        try:
            with mss.mss() as sct:
                shot = self.reader._hide_own(grab(sct, *rect))
            if not shot:
                return None
            img = Image.fromarray(cv2.cvtColor(shot[0], cv2.COLOR_BGR2RGB))
            img.thumbnail((1600, 900), Image.LANCZOS)
            path = DATA_DIR / "bug-report-game.jpg"
            img.save(path, quality=72)
            return path
        except Exception as e:
            log_error("saving the bug report's game picture", e)
            return None

    def _report_text(self, files):
        """The bug report's text: room to describe it, then the short details (the whole of it is
        in bug-report.txt: the address a report is opened with can't be very long)."""
        facts = getattr(self, "_report_facts_cache", None) or self._report_facts()
        attach = [f"`{files[k].name}`" for k in ("picture", "details", "screen") if k in files]
        errors = (facts.get("errors") or "").strip()[-700:]
        lines = [
            "**What went wrong?**", "(Describe it here: what did you do, what did you expect, what did it show?)", "",
            "**Files**",
            (f"(Drag in {' and '.join(attach)} from the folder that just opened: they show what the overlay read "
             "and how it's doing.)" if attach else "(If you can, add a screenshot of the game and the overlay.)"), "",
            "---", "<details><summary>Details (filled in by the overlay)</summary>", "",
            self._private(report.summary(facts)),
        ]
        if errors:
            lines += ["", "Recent errors:", "```", errors, "```"]
        lines += ["", "</details>"]
        return "\n".join(lines)

    def _remember_log_size(self):
        """Keeps the log's column widths, and the window size if the user resized it."""
        tree, root = self.log_tree, self.root
        self.cfg["log_columns_pt"] = {c: round(int(tree.column(c, "width")) / self._px_per_pt, 1)
                                      for c in tree["columns"]}
        self.cfg.pop("log_columns", None)
        if self.log_open:
            size = [root.winfo_width(), root.winfo_height()]
            natural = [root.winfo_reqwidth(), root.winfo_reqheight()]
            self.cfg["log_size"] = size if size != natural else None

    def close(self):
        self._note_flush()
        self.stop_evt.set()
        try:
            self._remember_log_size()
        except Exception as e:
            log_error("saving the log size", e)
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()

def main():
    # Only one copy at a time (per data folder): two copies would each keep their own log.
    # If it's already running, bring that window to the front instead. (The lock keeps the old
    # name, so an older version and this one can't run at the same time either.)
    key = hashlib.md5(str(DATA_DIR).lower().encode()).hexdigest()[:12]
    if not PLATFORM.claim_single_instance(key, WINDOW_TITLE, OLD_TITLES):
        return
    PLATFORM.ask_permissions()
    move_old_data()
    tess = find_tesseract()
    if tess:
        engine.TESS = ocr.Tesseract(tess)
        engine.tesseract_ok = True
    root = tk.Tk()
    App(root, DigitTemplates.load())
    root.mainloop()
