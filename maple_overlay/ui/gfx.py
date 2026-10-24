"""Smooth graphics for the overlay's windows. Tk's own widgets can't draw rounded corners, gradients or
glows, so those are drawn here (3x larger, then shrunk, which is what makes the edges smooth) and
handed to Tk as pictures. Each picture is made once and kept: nothing is redrawn unless what it shows
changes, so an idle overlay costs nothing."""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

SS = 3                                                    # drawn this many times larger, then shrunk

# the palette of the new look
INK = "#0b0c10"                  # the window
PANEL_TOP, PANEL_BOTTOM = "#1a1d26", "#13151b"            # panels are a touch lighter at the top
GOLD_TOP, GOLD_BOTTOM = "#ffe58f", "#ffb71f"
GOLD_GLOW = "#ffb71f"
UP, DOWN = "#5fe39a", "#ff8a7a"
TEXT, TEXT_DIM, TEXT_FAINT = "#f1efe9", "#9ba1ad", "#6c7280"


def rgb(hex_):
    return tuple(int(hex_[i:i + 2], 16) for i in (1, 3, 5))


class Gfx:
    """Draws the smooth shapes at one size. `scale` is device pixels per 'logical' pixel (the screen's
    scaling times Settings > Size); everything the widgets ask for is given in logical pixels."""

    WEIGHTS = {"regular": "Manrope.ttf", "semibold": "ManropeSemiBold.ttf", "bold": "ManropeBold.ttf",
               "extra": "ManropeExtraBold.ttf"}

    def __init__(self, scale, fonts_dir):
        self.s, self.dir = scale, Path(fonts_dir)
        self._fonts, self._cache = {}, {}

    def px(self, n):
        return max(1, int(round(n * self.s)))

    # ---- caching
    def cached(self, key, make):
        if key not in self._cache:
            if len(self._cache) > 400:                    # (in practice only the numbers change)
                self._cache.clear()
            self._cache[key] = make()
        return self._cache[key]

    @staticmethod
    def photo(image):
        """A picture Tk can show. The widget that shows it keeps it (Tk drops pictures nobody holds)."""
        return ImageTk.PhotoImage(image)

    def font(self, weight, size):
        key = (weight, size)
        if key not in self._fonts:
            px = max(6, round(size * self.s * SS))
            try:
                self._fonts[key] = ImageFont.truetype(str(self.dir / self.WEIGHTS[weight]), px)
            except Exception:
                try:
                    self._fonts[key] = ImageFont.truetype("segoeui.ttf", px)
                except Exception:
                    self._fonts[key] = ImageFont.load_default(px)
        return self._fonts[key]

    def text_width(self, text, weight, size):
        return int(math.ceil(self.font(weight, size).getlength(text) / SS))

    # ---- shapes
    @staticmethod
    def _gradient(w, h, top, bottom, horizontal=False):
        a, b = np.array(rgb(top), float), np.array(rgb(bottom), float)
        t = (np.linspace(0, 1, w)[None, :, None] if horizontal else np.linspace(0, 1, h)[:, None, None])
        return np.broadcast_to(a * (1 - t) + b * t, (h, w, 3)).astype(np.uint8)

    def panel(self, w, h, radius=14, top=PANEL_TOP, bottom=PANEL_BOTTOM, border=0.10):
        """A rounded panel, lighter at the top, with a hairline edge that catches light from above."""
        def make():
            W, H, R = w * SS, h * SS, radius * self.s * SS
            img = Image.fromarray(self._gradient(W, H, top, bottom), "RGB").convert("RGBA")
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), R, fill=255)
            img.putalpha(mask)
            if border:
                edge = Image.new("L", (W, H), 0)
                ImageDraw.Draw(edge).rounded_rectangle((0, 0, W - 1, H - 1), R, outline=255, width=SS)
                fade = np.linspace(1.0, 0.15, H)[:, None]
                alpha = (np.asarray(edge, float) / 255 * fade * border * 255).astype(np.uint8)
                hi = Image.new("RGBA", (W, H), (255, 255, 255, 0))
                hi.putalpha(Image.fromarray(alpha))
                img = Image.alpha_composite(img, hi)
            return img.resize((w, h), Image.LANCZOS)
        return self.cached(("panel", w, h, radius, top, bottom, border), make)

    def hero_panel(self, w, h, radius):
        """The big card: a panel with a faint warm glow behind the number."""
        def make():
            base = self.panel(w, h, radius, "#1b1e28", "#12141a", 0.12).copy()
            yy, xx = np.mgrid[0:h, 0:w]
            d = np.sqrt(((xx - w * 0.28) / (w * 0.75)) ** 2 + ((yy - h * 0.28) / (h * 0.72)) ** 2)
            a = np.clip(1 - d, 0, 1) ** 2 * 0.16
            glow = Image.new("RGBA", (w, h), rgb(GOLD_GLOW) + (0,))
            glow.putalpha(Image.fromarray((a * np.asarray(base.getchannel("A"), float)).astype(np.uint8)))
            return Image.alpha_composite(base, glow)
        return self.cached(("hero", w, h, radius), make)

    def glow(self, w, h, radius, color, blur, alpha, pad):
        """A soft coloured glow behind a rounded shape: the picture is (w + 2 pad) x (h + 2 pad)."""
        def make():
            W, H = (w + 2 * pad) * SS, (h + 2 * pad) * SS
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).rounded_rectangle((pad * SS, pad * SS, (pad + w) * SS - 1, (pad + h) * SS - 1),
                                                radius * self.s * SS, fill=int(255 * alpha))
            m = m.filter(ImageFilter.GaussianBlur(blur * self.s * SS))
            img = Image.new("RGBA", (W, H), rgb(color) + (0,))
            img.putalpha(m)
            return img.resize((w + 2 * pad, h + 2 * pad), Image.LANCZOS)
        return self.cached(("glow", w, h, radius, color, blur, alpha, pad), make)

    def text(self, text, weight, size, top, bottom=None, glow=None):
        """Text with a vertical colour gradient (and an optional glow): (picture, ascent in px, left pad)."""
        def make():
            font = self.font(weight, size)
            asc, desc = font.getmetrics()
            pad = int(8 * self.s * SS) if glow else 2 * SS
            tw = int(math.ceil(font.getlength(text)))
            W, H = tw + 2 * pad, asc + desc + 2 * pad
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).text((pad, pad), text, font=font, fill=255)
            grad = Image.fromarray(self._gradient(W, H, top, bottom or top), "RGB").convert("RGBA")
            grad.putalpha(mask)
            out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            if glow:
                g = mask.filter(ImageFilter.GaussianBlur(7 * self.s * SS / 3)).point(lambda v: int(v * 0.5))
                gl = Image.new("RGBA", (W, H), rgb(glow) + (0,))
                gl.putalpha(g)
                out = Image.alpha_composite(out, gl)
            out = Image.alpha_composite(out, grad)
            w, h = max(1, round(W / SS)), max(1, round(H / SS))
            return out.resize((w, h), Image.LANCZOS), round((asc + pad) / SS), round(pad / SS)
        return self.cached(("text", text, weight, size, top, bottom, glow), make)

    def bar(self, w, h, frac, pad=6):
        """The level progress bar: a track, a gold fill with a soft glow. Height is h + 2 pad."""
        def make():
            W, H, P = w * SS, h * SS, pad * SS
            img = Image.new("RGBA", (W, H + 2 * P), (0, 0, 0, 0))
            ImageDraw.Draw(img).rounded_rectangle((0, P, W - 1, P + H - 1), H / 2, fill=rgb("#222733") + (255,))
            if frac > 0:
                fw = max(H, int(W * frac))
                grad = Image.fromarray(self._gradient(fw, H, "#ffb21f", "#ffe48a", horizontal=True), "RGB").convert("RGBA")
                m = Image.new("L", (fw, H), 0)
                ImageDraw.Draw(m).rounded_rectangle((0, 0, fw - 1, H - 1), H / 2, fill=255)
                grad.putalpha(m)
                glow = Image.new("L", (W, H + 2 * P), 0)
                ImageDraw.Draw(glow).rounded_rectangle((0, P, fw - 1, P + H - 1), H / 2, fill=150)
                glow = glow.filter(ImageFilter.GaussianBlur(P / 2.2))
                gl = Image.new("RGBA", (W, H + 2 * P), rgb(GOLD_GLOW) + (0,))
                gl.putalpha(glow)
                img = Image.alpha_composite(img, gl)
                img.alpha_composite(grad, (0, P))
            return img.resize((w, h + 2 * pad), Image.LANCZOS)
        return self.cached(("bar", w, h, round(frac, 3), pad), make)

    def spark(self, values, w, h):
        """A smooth trend line with a soft fill underneath and a glowing dot at the latest value."""
        def make():
            W, H, P = w * SS, h * SS, 5 * self.s * SS
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            v = np.asarray(values, float)
            if len(v) < 2:                                    # nothing yet: a dotted baseline
                d = ImageDraw.Draw(img)
                x = P
                while x < W - P:
                    d.ellipse((x, H / 2 - SS, x + 2 * SS, H / 2 + SS), fill=(255, 255, 255, 46))
                    x += 9 * SS * self.s / 1.0
                return img.resize((w, h), Image.LANCZOS)
            n = len(v) * 4
            v = np.interp(np.linspace(0, len(v) - 1, n), np.arange(len(v)), v)
            k = 9
            v = np.convolve(np.pad(v, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
            lo, hi = v.min(), v.max()
            span = max(hi - lo, abs(hi) * 0.04, 1.0)
            ys = P + (1 - (v - lo) / span) * (H - 2 * P) * 0.92 + (H - 2 * P) * 0.04
            xs = np.linspace(P, W - P * 2.2, n)
            pts = list(zip(xs.tolist(), ys.tolist()))
            area = Image.new("L", (W, H), 0)
            ImageDraw.Draw(area).polygon(pts + [(xs[-1], H), (xs[0], H)], fill=255)
            fade = np.linspace(1.0, 0.0, H)[:, None] ** 1.4
            fill = Image.new("RGBA", (W, H), rgb(GOLD_GLOW) + (0,))
            fill.putalpha(Image.fromarray((np.asarray(area, float) * fade * 0.30).astype(np.uint8)))
            cols = np.arange(W)
            soften = np.clip((xs[-1] - cols) / (W * 0.16), 0, 1) * np.clip(cols / (W * 0.10), 0, 1)   # fill: soft at both ends
            fa = np.asarray(fill.getchannel("A"), float) * soften[None, :]
            fill.putalpha(Image.fromarray(fa.astype(np.uint8)))
            img = Image.alpha_composite(img, fill)
            ln = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ln).line(pts, fill=rgb("#ffd25a") + (255,), width=max(2, int(2.2 * self.s * SS)), joint="curve")
            la = np.asarray(ln.getchannel("A"), float) * np.clip(cols / (W * 0.12), 0, 1)[None, :]     # line: fades in from the left
            ln.putalpha(Image.fromarray(la.astype(np.uint8)))
            img = Image.alpha_composite(img, ln)
            x, y, r = pts[-1][0], pts[-1][1], 3.6 * self.s * SS
            halo = Image.new("L", (W, H), 0)
            ImageDraw.Draw(halo).ellipse((x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2), fill=120)
            halo = halo.filter(ImageFilter.GaussianBlur(r * 0.9))
            hg = Image.new("RGBA", (W, H), rgb(GOLD_GLOW) + (0,))
            hg.putalpha(halo)
            img = Image.alpha_composite(img, hg)
            ImageDraw.Draw(img).ellipse((x - r, y - r, x + r, y + r), fill=rgb("#fff1c2") + (255,))
            return img.resize((w, h), Image.LANCZOS)
        key = tuple(round(x) for x in values) if len(values) else ()
        return self.cached(("spark", key, w, h), make)

    # ---- icons, drawn on a 24-unit grid like most icon sets
    def icon(self, name, size, color):
        def make():
            W = size * SS
            u = W / 24
            m = Image.new("L", (W, W), 0)
            d = ImageDraw.Draw(m)
            lw = max(2, int(2.0 * u))

            def line(*pts):
                d.line([(x * u, y * u) for x, y in pts], fill=255, width=lw, joint="curve")
                for x, y in (pts[0], pts[-1]):
                    d.ellipse((x * u - lw / 2, y * u - lw / 2, x * u + lw / 2, y * u + lw / 2), fill=255)
            if name == "list":
                for y in (6, 12, 18):
                    line((9, y), (20, y))
                    d.ellipse((3.2 * u, (y - 1.3) * u, 5.8 * u, (y + 1.3) * u), fill=255)
            elif name == "shrink":
                line((4, 14), (10, 14), (10, 20)); line((20, 10), (14, 10), (14, 4))
                line((14, 10), (21, 3)); line((3, 21), (10, 14))
            elif name == "grow":
                line((15, 3), (21, 3), (21, 9)); line((9, 21), (3, 21), (3, 15))
                line((21, 3), (14, 10)); line((3, 21), (10, 14))
            elif name == "gear":
                d.ellipse((5 * u, 5 * u, 19 * u, 19 * u), fill=255)
                for i in range(8):
                    a = i * math.pi / 4
                    d.line([(12 * u + math.cos(a) * 7 * u, 12 * u + math.sin(a) * 7 * u),
                            (12 * u + math.cos(a) * 10.4 * u, 12 * u + math.sin(a) * 10.4 * u)], fill=255, width=int(3.6 * u))
                d.ellipse((8.6 * u, 8.6 * u, 15.4 * u, 15.4 * u), fill=0)
            elif name == "play":
                d.polygon([(7.5 * u, 4.5 * u), (19.5 * u, 12 * u), (7.5 * u, 19.5 * u)], fill=255)
                d.line([(7.5 * u, 4.5 * u), (19.5 * u, 12 * u), (7.5 * u, 19.5 * u), (7.5 * u, 4.5 * u)],
                       fill=255, width=int(1.8 * u), joint="curve")
            elif name == "pause":
                d.rounded_rectangle((6 * u, 4.5 * u, 10.4 * u, 19.5 * u), 1.6 * u, fill=255)
                d.rounded_rectangle((13.6 * u, 4.5 * u, 18 * u, 19.5 * u), 1.6 * u, fill=255)
            elif name == "reset":
                d.arc((4 * u, 4 * u, 20 * u, 20 * u), 225, 150, fill=255, width=lw)
                hx, hy = 12 + 8 * math.cos(math.radians(225)), 12 + 8 * math.sin(math.radians(225))
                tx, ty = math.sin(math.radians(225)), -math.cos(math.radians(225))        # the way it's turning
                for turn in (35, -35):
                    a = math.atan2(-ty, -tx) + math.radians(turn)
                    line((hx, hy), (hx + 5.2 * math.cos(a), hy + 5.2 * math.sin(a)))
            elif name == "more":
                for x in (5, 12, 19):
                    d.ellipse((x * u - 1.7 * u, 12 * u - 1.7 * u, x * u + 1.7 * u, 12 * u + 1.7 * u), fill=255)
            elif name == "party":
                # two people: a big one in front-left, a smaller one behind-right
                d.ellipse((13.2 * u, 4.2 * u, 18.8 * u, 9.8 * u), fill=255)
                d.pieslice((11 * u, 12.5 * u, 21.6 * u, 25 * u), 180, 360, fill=255)
                d.ellipse((4.6 * u, 5.4 * u, 11.4 * u, 12.2 * u), fill=0)          # (a gap where the front person overlaps)
                d.ellipse((5.2 * u, 6 * u, 10.8 * u, 11.6 * u), fill=255)
                d.pieslice((2.2 * u, 14.2 * u, 13.8 * u, 28 * u), 180, 360, fill=255)
            elif name == "link":
                line((8, 16), (16, 8)); line((9, 8), (16, 8), (16, 15))
            elif name in ("down", "up"):
                y0, y1 = (9, 15) if name == "down" else (15, 9)
                line((6, y0), (12, y1), (18, y0))
            m = m.resize((size, size), Image.LANCZOS)
            img = Image.new("RGBA", (size, size), rgb(color) + (0,))
            img.putalpha(m)
            return img
        return self.cached(("icon", name, size, color), make)


    def slider(self, w, h, frac, focused=False):
        """A slider: a thin track filled with gold up to a round knob."""
        def make():
            W, H = w * SS, h * SS
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            th, cy, r = 6 * self.s * SS, H / 2, 10 * self.s * SS
            x0, x1 = r * 1.7, W - r * 1.7
            d.rounded_rectangle((x0, cy - th / 2, x1, cy + th / 2), th / 2, fill=rgb("#262b37") + (255,))
            x = x0 + (x1 - x0) * frac
            if frac > 0:
                d.rounded_rectangle((x0, cy - th / 2, x, cy + th / 2), th / 2, fill=rgb("#ffc531") + (255,))
            halo = Image.new("L", (W, H), 0)
            ImageDraw.Draw(halo).ellipse((x - r * 1.5, cy - r * 1.5, x + r * 1.5, cy + r * 1.5), fill=150 if focused else 90)
            halo = halo.filter(ImageFilter.GaussianBlur(r * 0.55))
            hg = Image.new("RGBA", (W, H), rgb(GOLD_GLOW) + (0,))
            hg.putalpha(halo)
            img = Image.alpha_composite(img, hg)
            d = ImageDraw.Draw(img)
            d.ellipse((x - r, cy - r, x + r, cy + r), fill=rgb("#f4f2ec") + (255,))
            d.ellipse((x - r * 0.38, cy - r * 0.38, x + r * 0.38, cy + r * 0.38), fill=rgb("#ffb71f") + (255,))
            return img.resize((w, h), Image.LANCZOS)
        return self.cached(("slider", w, h, round(frac, 3), focused), make)


    def glyph(self, ch, weight, size, top, bottom=None, glow=None):
        """One character, drawn once (with its gradient and glow) and reused: (picture, advance px, ascent, pad).
        Digits all get the same width, so a number doesn't shift as it changes."""
        def make():
            img, asc, pad = self.text(ch, weight, size, top, bottom, glow)
            font = self.font(weight, size)
            width = font.getlength(ch)
            if ch.isdigit():
                width = max(font.getlength(d) for d in "0123456789")
            tighten = size * self.s * SS * (0.06 if ch.isdigit() else 0.10)       # a little closer than the font's default
            return img, int(math.ceil((width - tighten) / SS)), asc, pad
        return self.cached(("glyph", ch, weight, size, top, bottom, glow), make)
