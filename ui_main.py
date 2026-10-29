"""The overlay's main window, built from smooth custom widgets (see ui_gfx.py).

MainView takes the numbers through show(...) and only touches what changed, so while nothing changes
it draws nothing. It knows nothing about screens, logs or the game: the App gives it callbacks."""
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw

from ui_gfx import (GOLD_BOTTOM, GOLD_TOP, INK, TEXT, TEXT_DIM, TEXT_FAINT, UP, DOWN, Gfx, rgb)

W = 360                                   # the window's width, in logical pixels
SIDE = 16
INNER = W - 2 * SIDE
# The compact shapes (see Strip): Square is the narrow card (the Hero in a smaller window); Wide and Bar are sideways.
SHAPES = ("square", "wide", "bar")
SHAPE_W = {"square": W, "wide": 560, "bar": 720}

# state -> (dot colour, glows)
STRIP_KEYS = ("rate", "pct", "level", "eta", "eta_time", "mesos", "net", "tiles", "state", "state_text",
              "message", "read_health", "read_health_on")
STATES = {"waiting": ("#6c7280", False), "ready": ("#ffcf4a", False), "tracking": ("#5fe39a", True),
          "paused": ("#ffb27a", False), "alert": ("#ff7a5c", True)}
# How well a feature can be read: coloured dots on the readability strip.
READ_STATES = {"ok": "#5fe39a", "stale": "#ffcf4a", "bad": "#ff7a5c", "wait": "#6c7280", "off": "#3a3f4d"}


MIN_TEXT_PX = 8                          # the smallest text, at any Size, so it stays readable


class Theme:
    """One size of everything: the graphics engine and the text fonts, for this screen and Size setting."""

    def __init__(self, root, scale, fonts_dir, own_fonts, fallback):
        self.root, self.gfx = root, Gfx(scale, fonts_dir)
        self.fam = ({"regular": "Manrope", "semibold": "Manrope SemiBold", "bold": "Manrope Bold",
                     "extra": "Manrope ExtraBold"} if own_fonts else
                    {k: fallback(f, "normal")[0] for k, f in (("regular", "Segoe UI"), ("semibold", "Segoe UI Semibold"),
                                                              ("bold", "Segoe UI Semibold"), ("extra", "Segoe UI Semibold"))})
        self._tk = {}

    def px(self, n):
        return self.gfx.px(n)

    def font(self, weight, size):
        key = (weight, size)
        if key not in self._tk:
            self._tk[key] = tkfont.Font(root=self.root, family=self.fam[weight], size=-max(MIN_TEXT_PX, self.px(size)))
        return self._tk[key]


class Tip:
    """A small label that appears after a short hover, so icon-only buttons say what they do."""

    def __init__(self, widget, theme, text):
        self.w, self.theme, self.text, self.win, self.job = widget, theme, text, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress-1>", self._hide, add="+")

    def _enter(self, _):
        self._hide()
        self.job = self.w.after(450, self._show)

    def _show(self):
        self.win = tk.Toplevel(self.w)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        tk.Label(self.win, text=self.text, bg="#242833", fg=TEXT, font=self.theme.font("semibold", 11), padx=10, pady=5,
                 highlightthickness=1, highlightbackground="#343a48").pack()
        self.win.geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 6}")

    def _hide(self, _=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


# kind -> (top, bottom) for normal / hover / pressed, and the text colour
LOOKS = {
    "primary": {"normal": ("#ffd75e", "#ffb81f"), "hover": ("#ffe48c", "#ffc43b"), "press": ("#f2b620", "#e5a100"),
                "fg": "#1b1403", "fg_hover": "#1b1403", "border": 0.28},
    "secondary": {"normal": ("#272c38", "#1f232d"), "hover": ("#303644", "#262b36"), "press": ("#1c2029", "#181b23"),
                  "fg": TEXT, "fg_hover": TEXT, "border": 0.12},
    "ghost": {"normal": None, "hover": ("#212530", "#1a1d26"), "press": ("#171a21", "#14161c"),
              "fg": TEXT_DIM, "fg_hover": TEXT, "border": 0.0},
}


class Button(tk.Canvas):
    """A rounded button: text and/or an icon. Hover and press change it; Tab reaches it, and Space or
    Enter press it. Primary buttons carry a soft gold glow."""

    def __init__(self, parent, theme, text="", icon=None, command=None, kind="secondary", width=None, height=40,
                 radius=12, weight="semibold", size=13, tip=None, bg=INK, align="center"):
        self.t, self.text, self.icon, self.command, self.kind, self.align = theme, text, icon, command, kind, align
        self.weight, self.size, self.radius, self.bw, self.bh = weight, size, radius, width, height
        self._hover = self._down = self._focus = False
        self._enabled = True
        self._glow = 8 if kind == "primary" else 0
        g = theme.gfx
        if width is None:
            width = 28 + (22 if icon and text else 0) + (g.text_width(text, weight, size) / g.s if text else 0)   # (text_width is in screen pixels)
            if icon and not text:
                width = height
        self.bw = width
        super().__init__(parent, width=g.px(width) + 2 * g.px(self._glow), height=g.px(height) + 2 * g.px(self._glow),
                         bg=bg, highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self._bg = self.create_image(0, 0, anchor="nw")
        self._ico = self.create_image(0, 0, anchor="nw") if icon else None
        self._txt = self.create_text(0, 0, text=text, font=theme.font(weight, size), anchor="center") if text else None
        self._photo = self._iphoto = None
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda e: (self.focus_set(), self._set(down=True)))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda e: self._set(focus=True))
        self.bind("<FocusOut>", lambda e: self._set(focus=False))
        self.bind("<space>", lambda e: self._fire())
        self.bind("<Return>", lambda e: self._fire())
        if tip:
            Tip(self, theme, tip)
        self._render()

    def _render(self):
        g, look = self.t.gfx, LOOKS[self.kind]
        pad, w, h = g.px(self._glow), g.px(self.bw), g.px(self.bh)
        state = "press" if self._down else "hover" if self._hover else "normal"
        colors = look[state] if self._enabled else ("#1d2029", "#1a1c24")
        # ghost buttons only show a body when hovered; a primary one shows a glow
        if colors is None:
            body = None
        else:
            body = g.panel(w, h, self.radius, colors[0], colors[1], look["border"])
        photo_parts = []
        if self.kind == "primary" and self._enabled:
            photo_parts.append(g.glow(w, h, self.radius, "#ffb71f", 7 if not self._hover else 9,
                                      0.30 if not self._hover else 0.45, pad))
        img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        for p in photo_parts:
            img.alpha_composite(p)
        if body is not None:
            img.alpha_composite(body, (pad, pad))
        if self._focus:
            ring = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle((pad - 1, pad - 1, pad + w, pad + h), self.radius * g.s + 1,
                                                   outline=rgb("#ffd25a") + (255,), width=max(1, g.px(1.6)))
            img.alpha_composite(ring)
        self._photo = g.photo(img)
        self.itemconfigure(self._bg, image=self._photo)
        fg = (look["fg_hover"] if self._hover else look["fg"]) if self._enabled else TEXT_FAINT
        cx, cy = pad + w / 2, pad + h / 2
        isz = g.px(18)
        total = (isz if self.icon else 0) + (g.px(8) if self.icon and self.text else 0)
        tw = self.t.font(self.weight, self.size).measure(self.text) if self.text else 0
        left = cx - (total + tw) / 2 if self.align == "center" else pad + g.px(14)
        if self.icon:
            self._iphoto = g.photo(g.icon(self.icon, isz, fg))
            self.itemconfigure(self._ico, image=self._iphoto)
            self.coords(self._ico, left, cy - isz / 2)
            left += isz + (g.px(8) if self.text else 0)
        if self.text:
            self.itemconfigure(self._txt, text=self.text, fill=fg, anchor="w")
            self.coords(self._txt, left, cy + 1)

    def _set(self, hover=None, down=None, focus=None):
        if hover is not None:
            self._hover = hover
        if down is not None:
            self._down = down
        if focus is not None:
            self._focus = focus
        self._render()

    def _release(self, e):
        was = self._down
        self._set(down=False)
        if was and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self._fire()

    def _fire(self):
        if self._enabled and self.command:
            self.command()

    def resize(self, width):
        self.bw = width
        g = self.t.gfx
        self.configure(width=g.px(width) + 2 * g.px(self._glow))
        self._render()

    def set(self, text=None, icon=None, kind=None, enabled=None):
        if text is not None:
            self.text = text
            if self._txt is None:
                self._txt = self.create_text(0, 0, text=text, font=self.t.font(self.weight, self.size), anchor="w")
        if icon is not None:
            self.icon = icon
        if kind is not None:
            self.kind = kind
        if enabled is not None:
            self._enabled = enabled
        self._render()


class Pill(tk.Canvas):
    """The status pill: a coloured dot and a short word for where things stand."""

    H = 30

    def __init__(self, parent, theme, bg=INK):
        self.t = theme
        g = theme.gfx
        super().__init__(parent, width=g.px(190), height=g.px(self.H), bg=bg, highlightthickness=0, bd=0)
        self._img = self.create_image(0, 0, anchor="nw")
        self._dot = self.create_image(0, 0, anchor="nw")
        self._txt = self.create_text(0, 0, anchor="w", fill=TEXT, font=theme.font("semibold", 12))
        self._last, self._photos = None, []
        self.set("waiting", "Looking for the game")

    def set(self, state, text):
        if (state, text) == self._last:
            return
        self._last = (state, text)
        g = self.t.gfx
        color, glows = STATES[state]
        tw = self.t.font("semibold", 12).measure(text)
        w, h = tw + g.px(40), g.px(self.H)
        self.configure(width=w)
        body = g.panel(w, h, self.H / 2, "#1c1f28", "#161920", 0.10)
        d = g.px(8)
        dot = Image.new("RGBA", (d * 3, d * 3), (0, 0, 0, 0))
        if glows:
            dot.alpha_composite(g.glow(d, d, d / g.s / 2, color, 3, 0.55, d))
        ImageDraw.Draw(dot).ellipse((d, d, 2 * d - 1, 2 * d - 1), fill=rgb(color) + (255,))
        self._photos = [g.photo(body), g.photo(dot)]
        self.itemconfigure(self._img, image=self._photos[0])
        self.itemconfigure(self._dot, image=self._photos[1])
        self.coords(self._dot, g.px(14) - d, h / 2 - d * 1.5)
        self.itemconfigure(self._txt, text=text)
        self.coords(self._txt, g.px(14) + d + g.px(8), h / 2 + 1)


class Hero(tk.Canvas):
    """The big card: EXP per hour, how it's trending, and how far you are into the level."""

    def __init__(self, parent, theme, bg=INK):
        self.t, self.compact = theme, False
        g = theme.gfx
        super().__init__(parent, width=g.px(INNER), height=g.px(self._height()), bg=bg, highlightthickness=0, bd=0)
        self._bgi = self.create_image(0, 0, anchor="nw")
        self._cap = self.create_text(0, 0, anchor="nw", text="EXP PER HOUR", fill=TEXT_DIM, font=theme.font("semibold", 10))
        self._chip = self.create_image(0, 0, anchor="ne")
        self._chip_t = self.create_text(0, 0, anchor="center", text="", font=theme.font("bold", 11))
        self._digits, self._gphotos = [], {}
        self._spark = self.create_image(0, 0, anchor="nw")
        self._barimg = self.create_image(0, 0, anchor="nw")
        self._eta = self.create_text(0, 0, anchor="nw", text="", fill="#e9c15a", font=theme.font("bold", 13))
        self._pct = self.create_text(0, 0, anchor="ne", text="", fill=TEXT_DIM, font=theme.font("semibold", 12))
        # compact mode has no tiles, so mesos/hr sits under the number (value, then its unit)
        self._mval = self.create_text(0, 0, anchor="nw", text="", fill=TEXT, font=theme.font("bold", 12), state="hidden")
        self._munit = self.create_text(0, 0, anchor="nw", text="mesos / hr", fill=TEXT_DIM, font=theme.font("semibold", 10), state="hidden")
        self._nval = self.create_text(0, 0, anchor="nw", text="", fill=STATES["tracking"][0], font=theme.font("bold", 12), state="hidden")
        self._photos, self._chip_text = {}, None
        self._m = dict(rate=None, delta=None, spark=(), pct=None, eta="", chip=None, mesos="", net="")
        self._layout()
        self.redraw(everything=True)

    def _show_number(self, text, live):
        """EXP/hr as a row of ready-made digits: changing the number only moves pictures that already exist."""
        g = self.t.gfx
        style = ("extra", 52, GOLD_TOP, GOLD_BOTTOM, "#ffb71f") if live else ("extra", 52, "#4a505d", "#393e49", None)
        x = g.px(18)
        for i, ch in enumerate(text):
            img, adv, asc, pad = g.glyph(ch, *style)
            key = (ch, live)
            if key not in self._gphotos:
                self._gphotos[key] = g.photo(img)
            if i >= len(self._digits):
                self._digits.append(self.create_image(0, 0, anchor="nw"))
            item = self._digits[i]
            self.itemconfigure(item, image=self._gphotos[key], state="normal")
            self.coords(item, x - pad, g.px(30) - pad // 2)
            x += adv
        for item in self._digits[len(text):]:
            self.itemconfigure(item, state="hidden")

    def _height(self):
        return 158 if self.compact else 196

    def _layout(self):
        g = self.t.gfx
        self.configure(height=g.px(self._height()))
        self.coords(self._cap, g.px(18), g.px(16))
        self.coords(self._chip, g.px(INNER - 14), g.px(11))
        self.coords(self._chip_t, g.px(INNER - 14) - g.px(34), g.px(24))
        self._bar_y = 119 if self.compact else 148
        self.coords(self._mval, g.px(19), g.px(96))
        self.coords(self._barimg, g.px(18), g.px(self._bar_y) - g.px(6))
        self.coords(self._eta, g.px(18), g.px(self._bar_y + 16))
        self.coords(self._pct, g.px(INNER - 18), g.px(self._bar_y + 16))
        self.coords(self._spark, g.px(8), g.px(96))

    def set_compact(self, on):
        if on != self.compact:
            self.compact = on
            self._layout()
            self.redraw(everything=True)

    def show(self, **kw):
        changed = {k for k, v in kw.items() if self._m.get(k) != v}
        if changed:
            self._m.update(kw)
            self.redraw(changed)

    def redraw(self, changed=(), everything=False):
        g, m = self.t.gfx, self._m
        every = everything
        if every:
            self._photos["bg"] = g.photo(g.hero_panel(g.px(INNER), g.px(self._height()), 18))
            self.itemconfigure(self._bgi, image=self._photos["bg"])
        if every or "rate" in changed:
            self._show_number(m["rate"] if m["rate"] else "—", bool(m["rate"]))
        if (every or "spark" in changed) and not self.compact:
            self._photos["spark"] = g.photo(g.spark(m["spark"], g.px(INNER - 16), g.px(40)))
            self.itemconfigure(self._spark, image=self._photos["spark"], state="normal")
        elif every and self.compact:
            self.itemconfigure(self._spark, state="hidden")
        if every or "mesos" in changed or "net" in changed:
            show = self.compact and bool(m["mesos"])
            self.itemconfigure(self._mval, text=m["mesos"], state="normal" if show else "hidden")
            self.itemconfigure(self._munit, state="normal" if show else "hidden")
            if show:
                bbox = self.bbox(self._mval)
                self.coords(self._munit, bbox[2] + g.px(6), g.px(96) + g.px(2))
                if m["net"]:                        # net mesos/hr, after the mesos/hr
                    self.coords(self._nval, self.bbox(self._munit)[2] + g.px(10), g.px(96))
            self.itemconfigure(self._nval, text=f"\u00b7  net {m['net']}" if m["net"] else "",
                               state="normal" if show and m["net"] else "hidden")
        if every or "pct" in changed:
            frac = round((m["pct"] or 0) / 100 * 500) / 500          # (in steps you can't see, so the picture is reused)
            self._photos["bar"] = g.photo(g.bar(g.px(INNER - 36), g.px(8), frac, pad=g.px(6) // 1))
            self.itemconfigure(self._barimg, image=self._photos["bar"])
            self.itemconfigure(self._pct, text=f"{m['pct']:.1f}%" if m["pct"] is not None else "")
        if every or "eta" in changed:
            self.itemconfigure(self._eta, text=m["eta"])
        if every or "delta" in changed or "rate" in changed:
            d = m["delta"]
            if d is None or not m["rate"]:
                self.itemconfigure(self._chip, state="hidden")
                self.itemconfigure(self._chip_t, state="hidden")
                self._chip_text = None
            else:
                up = d >= 0.005
                down = d <= -0.005
                text = (f"▲ {d * 100:.0f}%" if up else f"▼ {abs(d) * 100:.0f}%" if down else "● steady") + "  vs 5 min"
                if text == self._chip_text and not every:
                    return
                col = UP if up else DOWN if down else TEXT_DIM
                w = self.t.font("bold", 11).measure(text) + g.px(22)
                body = g.panel(w, g.px(26), 13, "#1b2a24" if up else "#2b1c1b" if down else "#222631",
                               "#15221c" if up else "#231615" if down else "#1c1f28", 0.10)
                self._photos["chip"] = g.photo(body)
                self.itemconfigure(self._chip, image=self._photos["chip"], state="normal")
                self.itemconfigure(self._chip_t, text=text, fill=col, state="normal")
                self.coords(self._chip, g.px(INNER - 14), g.px(11))
                self.coords(self._chip_t, g.px(INNER - 14) - w / 2, g.px(24))


TILE_SHORT = {"Last 5 min": "5 min", "Mesos / hr": "Mesos", "Net / hr": "Net"}      # for tiles too narrow for the full label


class ReadHealth(tk.Canvas):
    """A row of dots: EXP / Mesos / Map / Potions — green when readable, yellow when holding,
    red when failing. Toggleable in Settings. items is [(label, state, tip), ...]."""

    H = 28

    def __init__(self, parent, theme, width, bg=INK):
        self.t, self.bw = theme, width
        g = theme.gfx
        super().__init__(parent, width=g.px(width), height=g.px(self.H), bg=bg, highlightthickness=0, bd=0)
        self._last, self._photos, self._tips = None, [], []
        self._tip_win, self._tip_job, self._tip_text = None, None, None
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self._hide_tip())

    def set(self, items):
        items = tuple((lab, st, tip) for lab, st, tip in (items or ()))
        if items == self._last:
            return
        self._last = items
        self.delete("all")
        self._photos, self._tips = [], []
        if not items:
            return
        g = self.t.gfx
        x, gap = g.px(2), g.px(14)
        for label, state, tip in items:
            color = READ_STATES.get(state, READ_STATES["wait"])
            d = max(3, g.px(4))
            dot = Image.new("RGBA", (d * 3, d * 3), (0, 0, 0, 0))
            if state == "ok":
                try:
                    dot.alpha_composite(g.glow(d, d, d / g.s / 2, color, 2.5, 0.45, d))
                except Exception:
                    pass
            ImageDraw.Draw(dot).ellipse((d, d, 2 * d - 1, 2 * d - 1), fill=rgb(color) + (255,))
            photo = g.photo(dot)
            self._photos.append(photo)
            self.create_image(x, g.px(self.H / 2) - d * 1.5, anchor="nw", image=photo)
            x0 = x
            x += d * 2 + g.px(5)
            tid = self.create_text(x, g.px(self.H / 2), anchor="w", text=label,
                                   fill=TEXT_DIM if state in ("off", "wait") else TEXT,
                                   font=self.t.font("semibold", 10))
            box = self.bbox(tid)
            x1 = box[2] if box else x + g.px(40)
            self._tips.append((x0, x1, tip))
            x = x1 + gap

    def _hover(self, e):
        for x0, x1, tip in self._tips:
            if x0 <= e.x <= x1:
                self._schedule_tip(tip)
                return
        self._hide_tip()

    def _schedule_tip(self, text):
        if text == self._tip_text and self._tip_win is not None:
            return
        self._hide_tip()
        self._tip_text = text
        self._tip_job = self.after(400, lambda: self._show_tip(text))

    def _show_tip(self, text):
        self._tip_job = None
        if not text or not self.winfo_exists():
            return
        win = self._tip_win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        tk.Label(win, text=text, bg="#242833", fg=TEXT, font=self.t.font("semibold", 11),
                 padx=10, pady=5, wraplength=self.t.px(280), justify="left",
                 highlightthickness=1, highlightbackground="#343a48").pack()
        win.geometry(f"+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height() + 6}")

    def _hide_tip(self):
        if self._tip_job:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        self._tip_text = None
        if self._tip_win is not None:
            self._tip_win.destroy()
            self._tip_win = None


class Tile(tk.Canvas):
    """A small panel with one number."""

    def __init__(self, parent, theme, label, width, bg=INK):
        self.t, self.label_text, self.bw = theme, label, width
        g = theme.gfx
        super().__init__(parent, width=g.px(width), height=g.px(62), bg=bg, highlightthickness=0, bd=0)
        self._photo = g.photo(g.panel(g.px(width), g.px(62), 13, "#191c25", "#13151b", 0.09))
        self.create_image(0, 0, anchor="nw", image=self._photo)
        cap = self.create_text(g.px(13), g.px(12), anchor="nw", text=label.upper(), fill=TEXT_DIM, font=theme.font("semibold", 9))
        if self.bbox(cap)[2] > g.px(width) - g.px(6) and label in TILE_SHORT:      # a narrow tile (small Size): the short label
            self.itemconfigure(cap, text=TILE_SHORT[label].upper())
        self._v = self.create_text(g.px(13), g.px(29), anchor="nw", text="—", fill=TEXT, font=theme.font("bold", 17))

    def set(self, value):
        if self.itemcget(self._v, "text") != value:
            room = self.t.px(self.bw) - 2 * self.t.px(13)
            for size in (17, 15, 13, 11):                      # a long number gets a smaller size, not cut off
                font = self.t.font("bold", size)
                if font.measure(value) <= room:
                    break
            else:                                              # still too long (a narrow tile): 99,999,999 becomes 100.0M
                digits = value.replace(",", "")
                if digits.lstrip("-").isdigit() and abs(int(digits)) >= 10000:
                    n = int(digits)
                    value = f"{n / 1e6:.1f}M" if abs(n) >= 999_500 else f"{n / 1e3:.0f}k"
            self.itemconfigure(self._v, text=value, font=font)


class Details(tk.Canvas):
    """The rest of the numbers, one line each, for when you want them."""

    ROW = 28

    def __init__(self, parent, theme, labels, bg=INK):
        self.t, self.labels = theme, labels
        g = theme.gfx
        h = 14 + self.ROW * len(labels)
        super().__init__(parent, width=g.px(INNER), height=g.px(h), bg=bg, highlightthickness=0, bd=0)
        self._photo = g.photo(g.panel(g.px(INNER), g.px(h), 14, "#191c25", "#13151b", 0.09))
        self.create_image(0, 0, anchor="nw", image=self._photo)
        self._vals = {}
        for i, name in enumerate(labels):
            y = g.px(7 + i * self.ROW + self.ROW / 2)
            self.create_text(g.px(16), y, anchor="w", text=name, fill=TEXT_DIM, font=theme.font("regular", 12))
            self._vals[name] = self.create_text(g.px(INNER - 16), y, anchor="e", text="—", fill=TEXT, font=theme.font("bold", 12.5))
            if i:
                self.create_line(g.px(14), g.px(7 + i * self.ROW), g.px(INNER - 14), g.px(7 + i * self.ROW), fill="#232733")

    def set(self, name, value):
        if self.itemcget(self._vals[name], "text") != value:
            self.itemconfigure(self._vals[name], text=value)


class Toggle(tk.Canvas):
    """A switch with a label, tied to a BooleanVar-like get/set."""

    def __init__(self, parent, theme, text, value=True, command=None, bg=INK, alt=None):
        self.t, self.on, self.command, self.text = theme, value, command, text
        g = theme.gfx
        tw = max(theme.font("semibold", 12).measure(x) for x in (text, alt or text))
        super().__init__(parent, width=g.px(46) + tw + g.px(6), height=g.px(30), bg=bg, highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2")
        self._img = self.create_image(0, g.px(3), anchor="nw")
        self._lbl = self.create_text(g.px(46), g.px(15), anchor="w", text=text, fill=TEXT_DIM, font=theme.font("semibold", 12))
        self._photo = None
        for ev in ("<Button-1>", "<space>"):
            self.bind(ev, lambda e: self.toggle())
        self.bind("<FocusIn>", lambda e: self.itemconfigure(self._lbl, fill=TEXT))
        self.bind("<FocusOut>", lambda e: self._paint())
        self._paint()

    def _paint(self):
        g = self.t.gfx
        w, h = g.px(36), g.px(22)
        img = g.panel(w, h, 11, *(("#ffd25a", "#ffb71f") if self.on else ("#2a2f3b", "#232731")), 0.10 if self.on else 0.06)
        base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        base.alpha_composite(img)
        r = h - g.px(6)
        x = w - g.px(3) - r if self.on else g.px(3)
        knob = Image.new("RGBA", (w * 3, h * 3), (0, 0, 0, 0))
        ImageDraw.Draw(knob).ellipse((x * 3, g.px(3) * 3, (x + r) * 3 - 1, (g.px(3) + r) * 3 - 1),
                                     fill=rgb("#1b1403" if self.on else "#e8e6e0") + (255,))
        base.alpha_composite(knob.resize((w, h), Image.LANCZOS))
        self._photo = g.photo(base)
        self.itemconfigure(self._img, image=self._photo)
        self.itemconfigure(self._lbl, fill=TEXT if self.on else TEXT_DIM)

    def toggle(self):
        self.set(not self.on)
        if self.command:
            self.command(self.on)

    def set(self, value, label=None):
        if label is not None:
            self.itemconfigure(self._lbl, text=label)
        if value != self.on:
            self.on = value
        self._paint()


class Message(tk.Label):
    """The line of guidance at the bottom."""

    def __init__(self, parent, theme, bg=INK):
        super().__init__(parent, text="", bg=bg, fg=TEXT_FAINT, font=theme.font("regular", 11), justify="left", anchor="w")

    def set(self, text, wrap):
        if self.cget("text") != text or str(self.cget("wraplength")) != str(wrap):
            self.configure(text=text, wraplength=wrap)


class Strip(tk.Frame):
    """The Wide and Bar compact shapes: the same numbers as the Square one, laid out sideways. Wide is a card with the
    level bar on top and four numbers below it; Bar is one thin strip with three numbers and a slim bar under them.
    Both carry their own Start/Pause, compact and settings buttons, because the main window's are hidden here."""

    CARD = {"wide": (54, 112), "bar": (12, 72)}               # the card's top and height
    STATS = ("EXP / HR", "LAST 5 MIN", "MESOS / HR", "NEXT LEVEL")

    def __init__(self, parent, theme, cb, shape, bg=INK):
        super().__init__(parent, bg=bg)
        self.t, self.shape, self.cb, self.active = theme, shape, cb, False
        self.W = SHAPE_W[shape]
        self.top, self.card_h = self.CARD[shape]
        g, wide = theme.gfx, shape == "wide"
        self.CW = self.W - 2 * SIDE - (0 if wide else 124)    # the card's width (Bar keeps its buttons beside it, on the window's colour)
        self.canvas = c = tk.Canvas(self, width=g.px(self.W), height=g.px(self.top + self.card_h + 6), bg=bg, highlightthickness=0, bd=0)
        c.grid(row=0, column=0)
        self.columnconfigure(0, minsize=g.px(self.W))
        self._photos, self._gphotos, self._digits = {}, {}, []
        self._start = None
        self._m = dict(rate=None, pct=None, level=None, eta="", eta_time="", mesos="\u2014", net="", tiles={}, state="waiting", state_text="")
        x0, y0 = g.px(SIDE), g.px(self.top)
        self._card = c.create_image(x0, y0, anchor="nw")
        self._photos["card"] = g.photo(g.hero_panel(g.px(self.CW), g.px(self.card_h), 16))
        c.itemconfigure(self._card, image=self._photos["card"])
        dim, text = TEXT_DIM, TEXT
        self._bar = c.create_image(0, 0, anchor="nw")
        self._pct = c.create_text(0, 0, anchor="e", text="", fill=dim, font=theme.font("semibold", 12))
        if wide:
            self._level = c.create_text(x0 + g.px(18), y0 + g.px(24), anchor="w", text="", fill="#e9c15a", font=theme.font("bold", 13))
            self._caps, self._vals = [], []
            colw = self.CW / 4
            c.create_line(x0 + g.px(14), y0 + g.px(46), x0 + g.px(self.CW - 14), y0 + g.px(46), fill="#232733")
            for i, cap in enumerate(self.STATS):
                x = x0 + g.px(colw * i + 18)
                if i:
                    c.create_line(x0 + g.px(colw * i), y0 + g.px(56), x0 + g.px(colw * i), y0 + g.px(102), fill="#232733")
                self._caps.append(c.create_text(x, y0 + g.px(56), anchor="nw", text=cap, fill=dim, font=theme.font("semibold", 9)))
                if i:
                    self._vals.append(c.create_text(x, y0 + g.px(72), anchor="nw", text="\u2014", fill=text, font=theme.font("bold", 18)))
            self._net = c.create_text(x0 + g.px(colw * 2 + 18), y0 + g.px(94), anchor="nw", text="", fill=STATES["tracking"][0],
                                      font=theme.font("semibold", 10))
            self.pill = Pill(c, theme, bg)
            c.create_window(g.px(SIDE), g.px(27), anchor="w", window=self.pill)
        else:
            self._dot = c.create_image(0, 0, anchor="nw")
            self._unit = c.create_text(0, y0 + g.px(28), anchor="w", text="EXP/hr", fill=dim, font=theme.font("semibold", 10))
            self._mval = c.create_text(x0 + g.px(192), y0 + g.px(28), anchor="w", text="\u2014", fill=text, font=theme.font("bold", 17))
            self._munit = c.create_text(0, y0 + g.px(28), anchor="w", text="mesos/hr", fill=dim, font=theme.font("semibold", 10))
            self._eta = c.create_text(x0 + g.px(372), y0 + g.px(28), anchor="w", text="", fill="#e9c15a", font=theme.font("bold", 13))
            self._net = c.create_text(0, y0 + g.px(31), anchor="w", text="", fill=STATES["tracking"][0], font=theme.font("semibold", 10))
            self._div = [c.create_line(0, y0 + g.px(14), 0, y0 + g.px(42), fill="#232733") for _ in range(2)]
            self.pill = None
        # buttons, from the right: settings, compact (back to full), Start/Pause
        mid = g.px(27) if wide else y0 + g.px(self.card_h / 2)
        right = g.px(self.W - SIDE)
        self.settings_btn = Button(c, theme, icon="gear", kind="ghost", width=34, height=34, radius=10, command=cb["settings"], tip="Settings", bg=bg)
        self.compact_btn = Button(c, theme, icon="grow", kind="ghost", width=34, height=34, radius=10, command=cb["compact"], tip="Back to full size", bg=bg)
        c.create_window(right - g.px(17), mid, anchor="center", window=self.settings_btn)
        c.create_window(right - g.px(17 + 36), mid, anchor="center", window=self.compact_btn)
        if wide:
            self.start_btn = Button(c, theme, text="Start", icon="play", kind="primary", width=104, height=34, radius=11, size=12,
                                    weight="bold", command=cb["start"], bg=bg)
            c.create_window(right - g.px(17 + 36 + 8 + 52), mid, anchor="center", window=self.start_btn)
        else:
            self.start_btn = Button(c, theme, icon="play", kind="primary", width=34, height=34, radius=11, command=cb["start"],
                                    tip="Start / Pause", bg=bg)
            c.create_window(right - g.px(17 + 36 + 36 + 4), mid, anchor="center", window=self.start_btn)
        self.message = Message(self, theme, bg)
        self.message.grid(row=1, column=0, padx=g.px(SIDE + 2), pady=(g.px(2), g.px(10)), sticky="we")
        self._wrap = g.px(self.CW - 6)

    # ---- the numbers
    def show(self, **m):
        changed = {k for k, v in m.items() if self._m.get(k) != v}
        if changed:
            self._m.update({k: m[k] for k in changed})
            if self.active:
                self._paint(changed)

    def activate(self, on):
        self.active = on
        if on:
            if self._start:
                self.set_start(*self._start)
            self._paint(None)

    def set_start(self, text, icon, kind):
        self._start = (text, icon, kind)
        if self.shape == "wide":
            self.start_btn.set(text=text, icon=icon, kind=kind)
        else:
            self.start_btn.set(icon=icon, kind=kind)

    def _number(self, text, live, size, x, baseline):
        """EXP/hr as ready-made digits, like the Hero's. Returns where the number ends."""
        g = self.t.gfx
        style = ("extra", size, GOLD_TOP, GOLD_BOTTOM, "#ffb71f") if live else ("extra", size, "#4a505d", "#393e49", None)
        for i, ch in enumerate(text):
            img, adv, asc, pad = g.glyph(ch, *style)
            key = (ch, live, size)
            if key not in self._gphotos:
                self._gphotos[key] = g.photo(img)
            if i >= len(self._digits):
                self._digits.append(self.canvas.create_image(0, 0, anchor="nw"))
            self.canvas.itemconfigure(self._digits[i], image=self._gphotos[key], state="normal")
            self.canvas.coords(self._digits[i], x - pad, baseline - asc)
            x += adv
        for item in self._digits[len(text):]:
            self.canvas.itemconfigure(item, state="hidden")
        return x

    def _flow(self):
        """Bar: the three numbers side by side, each starting where the one before it ends (so a long number or a
        small Size never makes them touch), with a divider between them."""
        g, c, m = self.t.gfx, self.canvas, self._m
        x0, y0 = g.px(SIDE), g.px(self.top)
        gap, y, y2 = g.px(14), y0 + g.px(28), y0 + g.px(31)
        x = c.bbox(self._unit)[2] + gap
        c.coords(self._div[0], x, y0 + g.px(14), x, y0 + g.px(42))
        c.coords(self._mval, x + gap, y)
        c.coords(self._munit, c.bbox(self._mval)[2] + g.px(6), y2)
        x = c.bbox(self._munit)[2]
        if m["net"]:                                              # net mesos/hr, right after the mesos/hr
            c.itemconfigure(self._net, text=f"net {m['net']}")
            c.coords(self._net, x + g.px(8), y2)
            x = c.bbox(self._net)[2]
        else:
            c.itemconfigure(self._net, text="")
        x += gap
        c.coords(self._div[1], x, y0 + g.px(14), x, y0 + g.px(42))
        c.itemconfigure(self._eta, text=m["eta"])
        c.coords(self._eta, x + gap, y)
        if c.bbox(self._eta)[2] > x0 + g.px(self.CW - 14):        # too long for the card: just the time
            c.itemconfigure(self._eta, text=m["eta_time"] or "")

    def _paint(self, changed):
        g, m, c = self.t.gfx, self._m, self.canvas
        everything = changed is None
        wide = self.shape == "wide"
        x0, y0 = g.px(SIDE), g.px(self.top)
        live = bool(m["rate"])
        if everything or "rate" in changed:
            base = y0 + g.px(94 if wide else 38)
            end = self._number(m["rate"] if live else "\u2014", live, 26, x0 + g.px(18 if wide else 36), base)
            if not wide:
                c.coords(self._unit, end + g.px(6), y0 + g.px(31))
        if wide and (everything or "net" in changed):
            c.itemconfigure(self._net, text=f"net {m['net']}" if m["net"] else "")
        if wide and (everything or "tiles" in changed or "mesos" in changed or "eta_time" in changed or "level" in changed):
            vals = ((m["tiles"] or {}).get("Last 5 min", "\u2014"), m["mesos"] or "\u2014", m["eta_time"] or "\u2014")
            for item, value in zip(self._vals, vals):
                c.itemconfigure(item, text=value)
            lvl = m["level"]
            c.itemconfigure(self._caps[3], text=f"TO LV {lvl + 1}" if lvl else "NEXT LEVEL")
            c.itemconfigure(self._level, text=f"LV {lvl}" if lvl else "")
        if not wide and (everything or "mesos" in changed or "eta" in changed or "pct" in changed):
            c.itemconfigure(self._mval, text=m["mesos"] or "\u2014")
            c.itemconfigure(self._eta, text=m["eta"])
            pct = m["pct"]
            c.itemconfigure(self._pct, text=f"{pct:.1f}%" if pct is not None else "")
        if not wide and (everything or "rate" in changed or "mesos" in changed or "eta" in changed or "net" in changed):
            self._flow()
        if everything or "pct" in changed:
            frac = round((m["pct"] or 0) / 100 * 500) / 500
            if wide:
                left = x0 + g.px(18 + 52)
                right = x0 + g.px(self.CW - 18 - 54)
                self._photos["bar"] = g.photo(g.bar(right - left, g.px(8), frac, pad=g.px(6)))
                c.itemconfigure(self._bar, image=self._photos["bar"])
                c.coords(self._bar, left, y0 + g.px(14))
                c.itemconfigure(self._pct, text=f"{m['pct']:.1f}%" if m["pct"] is not None else "")
                c.coords(self._pct, x0 + g.px(self.CW - 18), y0 + g.px(24))
            else:
                w = g.px(self.CW - 36 - 50)
                self._photos["bar"] = g.photo(g.bar(w, g.px(6), frac, pad=g.px(4)))
                c.itemconfigure(self._bar, image=self._photos["bar"])
                c.coords(self._bar, x0 + g.px(18), y0 + g.px(self.card_h - 26))
                c.itemconfigure(self._pct, text=f"{m['pct']:.1f}%" if m["pct"] is not None else "")
                c.coords(self._pct, x0 + g.px(self.CW - 18), y0 + g.px(self.card_h - 19))
        if wide and (everything or "state" in changed or "state_text" in changed):
            self.pill.set(m["state"], m["state_text"])
        if not wide and (everything or "state" in changed):
            color, glows = STATES[m["state"]]
            d = g.px(8)
            dot = Image.new("RGBA", (d * 3, d * 3), (0, 0, 0, 0))
            if glows:
                dot.alpha_composite(g.glow(d, d, d / g.s / 2, color, 3, 0.55, d))
            ImageDraw.Draw(dot).ellipse((d, d, 2 * d - 1, 2 * d - 1), fill=rgb(color) + (255,))
            self._photos["dot"] = g.photo(dot)
            c.itemconfigure(self._dot, image=self._photos["dot"])
            c.coords(self._dot, x0 + g.px(18) - d, y0 + g.px(28) - d * 1.5)
        if everything or "message" in changed or "state" in changed:
            self.message.set(m.get("message", ""), self._wrap)
            # Bar has no room for a status line, so it shows one only when something needs attention
            if wide or m["state"] in ("alert", "waiting"):
                self.message.grid()
            else:
                self.message.grid_remove()


class MainView(tk.Frame):
    """Everything in the main window, top to bottom."""

    def __init__(self, parent, theme, cb, details=False, bg=INK):
        super().__init__(parent, bg=bg)
        self.t, self.cb, g = theme, cb, theme.gfx
        self.columnconfigure(0, weight=1, minsize=g.px(W))
        pad = lambda a=0, b=0: (g.px(a), g.px(b))
        self.compact, self.shape, self.strips, self._last = False, "square", {}, {}

        head = self.head = tk.Frame(self, bg=bg)
        head.grid(row=0, column=0, sticky="we", padx=g.px(SIDE), pady=pad(14, 10))
        head.columnconfigure(0, weight=1)
        self.pill = Pill(head, theme, bg)
        self.pill.grid(row=0, column=0, sticky="w")
        icons = tk.Frame(head, bg=bg)
        icons.grid(row=0, column=1, sticky="e")
        self.settings_btn = Button(icons, theme, icon="gear", kind="ghost", width=34, height=34, radius=10, command=cb["settings"], tip="Settings", bg=bg)
        self.compact_btn = Button(icons, theme, icon="shrink", kind="ghost", width=34, height=34, radius=10, command=cb["compact"], tip="Compact mode", bg=bg)
        self.log_btn = Button(icons, theme, icon="list", kind="ghost", width=34, height=34, radius=10, command=cb["log"], tip="Open the log", bg=bg)
        for b in (self.log_btn, self.compact_btn, self.settings_btn):
            b.pack(side="left", padx=(2, 0))

        self.hero = Hero(self, theme, bg)
        self.hero.grid(row=1, column=0, padx=g.px(SIDE), pady=pad(0, 10))
        self.hero.bind("<Double-Button-1>", lambda e: cb["compact"]())

        self.read_health = ReadHealth(self, theme, INNER, bg)
        self.read_health.grid(row=2, column=0, padx=g.px(SIDE), pady=pad(0, 8), sticky="we")
        self._read_health_on = True

        self.tiles_row = tk.Frame(self, bg=bg)
        self.tiles_row.grid(row=3, column=0, padx=g.px(SIDE), pady=pad(0, 6), sticky="we")
        tw = (INNER - 24) // 4
        self.tiles = {}
        for i, name in enumerate(("Last 5 min", "Mesos / hr", "Net / hr", "Elapsed")):
            t = Tile(self.tiles_row, theme, name, tw, bg)
            t.grid(row=0, column=i, padx=(0 if i == 0 else g.px(8), 0))
            self.tiles[name] = t

        self.more_btn = Button(self, theme, text="Details", icon="down", kind="ghost", width=INNER, height=30, radius=10,
                               size=12, weight="semibold", command=self._toggle_details, bg=bg)
        self.more_btn.grid(row=4, column=0, padx=g.px(SIDE), pady=pad(0, 4))
        self.details_open = details
        self.details = Details(self, theme, ("% / hr", "Gained", "Current", "Class", "Party", "Mesos", "Mesos / 5 min",
                                             "Etc gain", "Potion gain", "Potion cost", "Net mesos / hr", "Left out"), bg)
        self.details.grid(row=5, column=0, padx=g.px(SIDE), pady=pad(0, 8))
        if not details:
            self.details.grid_remove()
        self.more_btn.set(icon="up" if details else "down")

        controls = self.controls = tk.Frame(self, bg=bg)
        controls.grid(row=6, column=0, padx=g.px(SIDE - 8), pady=pad(0, 2), sticky="we")
        controls.columnconfigure(0, weight=1)
        self.start_btn = Button(controls, theme, text="Start", icon="play", kind="primary", width=INNER - 8 - 52 - 52, height=46,
                                radius=14, size=14, weight="bold", command=cb["start"], bg=bg)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.reset_btn = Button(controls, theme, icon="reset", kind="secondary", width=46, height=46, radius=14, command=cb["reset"], tip="Reset", bg=bg)
        self.reset_btn.grid(row=0, column=1, padx=(g.px(-2), 0))
        self.menu_btn = Button(controls, theme, icon="more", kind="secondary", width=46, height=46, radius=14, command=self._menu, tip="More", bg=bg)
        self.menu_btn.grid(row=0, column=2, padx=(g.px(4), 0))

        foot = tk.Frame(self, bg=bg)
        foot.grid(row=7, column=0, padx=g.px(SIDE), pady=pad(6, 0), sticky="we")
        foot.columnconfigure(0, weight=1)
        self.logging = Toggle(foot, theme, "Log this run", True, cb["logging"], bg, alt="Not logging")
        self.logging.grid(row=0, column=0, sticky="w")
        self.best_btn = Button(foot, theme, text="Best maps", icon="link", kind="ghost", height=30, radius=10, size=12,
                               command=cb["best"], bg=bg)
        self.best_btn.grid(row=0, column=1, sticky="e")
        self.message = Message(self, theme, bg)
        self.message.grid(row=8, column=0, padx=g.px(SIDE + 2), pady=pad(2, 14), sticky="we")
        self.foot_widgets = [self.read_health, self.tiles_row, self.more_btn, self.details, self.reset_btn, self.menu_btn, foot]

    # ---- showing things
    def show(self, **m):
        """rate, delta, spark, pct, eta (hero); tiles, details (dicts); state, state_text, message."""
        hero = {k: m[k] for k in ("rate", "delta", "spark", "pct", "eta", "mesos", "net") if k in m}
        if "spark" in hero:
            hero["spark"] = tuple(hero["spark"])
        if hero:
            self.hero.show(**hero)
        for name, value in (m.get("tiles") or {}).items():
            self.tiles[name].set(value)
        for name, value in (m.get("details") or {}).items():
            self.details.set(name, value)
        if "state" in m:
            self.pill.set(m["state"], m.get("state_text", ""))
        if "message" in m:
            self.message.set(m["message"], self.t.px(INNER - 6))
        if "read_health_on" in m:
            self._read_health_on = bool(m["read_health_on"])
            self._show_read_health()
        if "read_health" in m:
            self.read_health.set(m["read_health"] if self._read_health_on else ())
        strip = {k: v for k, v in m.items() if k in STRIP_KEYS}
        if strip:
            self._last.update(strip)
            for st in self.strips.values():
                st.show(**strip)

    def _toggle_details(self):
        self.details_open = not self.details_open
        (self.details.grid if self.details_open else self.details.grid_remove)()
        self.more_btn.set(icon="up" if self.details_open else "down")
        if self.cb.get("details"):
            self.cb["details"](self.details_open)

    def set_start(self, text, icon, kind):
        """The Start/Pause button, here and on the sideways shapes."""
        self.start_btn.set(text=text, icon=icon, kind=kind)
        self._start = (text, icon, kind)
        for st in self.strips.values():
            st.set_start(text, icon, kind)

    def _strip(self, shape):
        if shape not in self.strips:
            st = self.strips[shape] = Strip(self, self.t, self.cb, shape, self["bg"])
            st.show(**self._last)
            if getattr(self, "_start", None):
                st.set_start(*self._start)
        return self.strips[shape]

    def set_compact(self, on, shape="square"):
        """Compact mode in one of the shapes. Square is this window with fewer rows; Wide and Bar swap the whole
        window for a sideways Strip."""
        self.compact, self.shape = on, shape if on else "square"
        sideways = on and shape in ("wide", "bar")
        for st in self.strips.values():
            st.activate(False)
            st.grid_remove()
        rows = [self.head, self.hero, self.controls, self.message]
        if sideways:
            for w in rows + self.foot_widgets:
                w.grid_remove()
            st = self._strip(shape)
            st.grid(row=9, column=0)
            self.columnconfigure(0, minsize=self.t.px(SHAPE_W[shape]))
            st.activate(True)
            self.compact_btn.set(icon="grow")
            return
        self.columnconfigure(0, minsize=self.t.px(W))
        for w in rows:
            w.grid()
        self.hero.set_compact(on)
        for w in self.foot_widgets:
            if on:
                w.grid_remove()
            elif w is self.read_health:
                self._show_read_health()
            elif w is not self.details or self.details_open:
                w.grid()
        self.start_btn.resize(INNER - 8 if on else INNER - 8 - 52 - 52)
        self.compact_btn.set(icon="grow" if on else "shrink")
        (self.log_btn.pack_forget() if on else self.log_btn.pack(side="left", padx=(2, 0), before=self.compact_btn))

    def _show_read_health(self):
        """Shows the readability strip in the full view when the setting is on (hidden in compact)."""
        if self._read_health_on and not self.compact:
            self.read_health.grid()
        else:
            self.read_health.grid_remove()

    def _menu(self):
        self.cb["menu"](self.menu_btn)


class PopupMenu(tk.Toplevel):
    """The 'more' menu: a short list of actions in a rounded panel, closed by choosing one, clicking away or Esc."""

    def __init__(self, theme, anchor, items):
        super().__init__(anchor)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=INK)
        g = theme.gfx
        w, row = 210, 38
        h = 12 + row * len(items)
        body = tk.Canvas(self, width=g.px(w), height=g.px(h), bg=INK, highlightthickness=0, bd=0)
        body.pack()
        self._photo = g.photo(g.panel(g.px(w), g.px(h), 14, "#20242e", "#181b23", 0.16))
        body.create_image(0, 0, anchor="nw", image=self._photo)
        for i, (text, icon, command) in enumerate(items):
            b = Button(body, theme, text=text, icon=icon, kind="ghost", width=w - 12, height=row - 4, radius=10, size=12,
                       align="left", command=lambda c=command: (self.destroy(), c()), bg="#1c1f28")
            body.create_window(g.px(6), g.px(6 + i * row), anchor="nw", window=b)
        self.update_idletasks()
        x = anchor.winfo_rootx() + anchor.winfo_width() - self.winfo_reqwidth()
        y = anchor.winfo_rooty() - self.winfo_reqheight() - 6
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.focus_force()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<FocusOut>", lambda e: self.after(120, self._maybe_close))

    def _maybe_close(self):
        try:
            if self.winfo_exists() and not str(self.focus_get()).startswith(str(self)):
                self.destroy()
        except Exception:
            pass


class Slider(tk.Canvas):
    """A slider tied to an IntVar: drag it, click the track, or use the arrow keys."""

    def __init__(self, parent, theme, variable, lo, hi, width=330, bg=INK):
        self.t, self.var, self.lo, self.hi, self.bw = theme, variable, lo, hi, width
        g = theme.gfx
        super().__init__(parent, width=g.px(width), height=g.px(30), bg=bg, highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self._img = self.create_image(0, 0, anchor="nw")
        self._photo, self._focus = None, False
        self.bind("<Button-1>", lambda e: (self.focus_set(), self._drag(e)))
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Left>", lambda e: self._nudge(-1))
        self.bind("<Right>", lambda e: self._nudge(1))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _set_focus(self, on):
        self._focus = on
        self._paint()

    def _value(self):
        try:
            return min(self.hi, max(self.lo, int(float(self.var.get()))))
        except (ValueError, tk.TclError):
            return self.lo

    def _paint(self):
        g = self.t.gfx
        frac = (self._value() - self.lo) / (self.hi - self.lo)
        self._photo = g.photo(g.slider(g.px(self.bw), g.px(30), frac, self._focus))
        self.itemconfigure(self._img, image=self._photo)

    def _drag(self, e):
        r = self.t.gfx.px(10) * 1.7
        frac = (e.x - r) / max(1, self.winfo_width() - 2 * r)
        self.var.set(self.lo + round(min(1, max(0, frac)) * (self.hi - self.lo)))

    def _nudge(self, by):
        self.var.set(min(self.hi, max(self.lo, self._value() + by)))
