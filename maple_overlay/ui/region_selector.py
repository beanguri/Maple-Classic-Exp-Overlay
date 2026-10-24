"""The box you draw to choose the area to read instead of the game window."""
import tkinter as tk

import mss

from maple_overlay.config import PLATFORM


class RegionSelector:
    """Full-screen translucent overlay; drag a box around the EXP bar text."""

    def __init__(self, root, on_done):
        self.on_done = on_done
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.origin = (v["left"], v["top"])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, bg="black", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(v["width"] // 2, 60, fill="white", font=(PLATFORM.font("Segoe UI", "bold")[0], 20, PLATFORM.font("Segoe UI", "bold")[1]),
                                text="Drag a box around the EXP numbers  (Esc to cancel)")
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ffcc00", width=2)

    def _drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, *self.start, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, x1 = sorted((self.start[0], e.x))
        y0, y1 = sorted((self.start[1], e.y))
        if x1 - x0 < 10 or y1 - y0 < 5:
            self._finish(None)
            return
        self._finish({"left": self.origin[0] + x0, "top": self.origin[1] + y0,
                      "width": x1 - x0, "height": y1 - y0})

    def _finish(self, region):
        self.win.destroy()
        self.on_done(region)
