"""Reading text with Tesseract: the only place the overlay starts it.

Starting Tesseract (and loading its language data) costs more than reading a short line, so
read_lines() hands it all of its pictures in one run: a list of image files, read one after
the other. The text of each comes back as if it had been run on that picture alone (tests/
test_ocr.py checks that). Tesseract's own threads are off (OMP_THREAD_LIMIT=1): for a few
small lines they only cost CPU.

The pictures are single-channel (grey) images, which is what the readers make.
"""
import os
import subprocess
import tempfile

import cv2

PAGE_BREAK = "\f"                    # what Tesseract puts between one picture's text and the next


class TesseractError(RuntimeError):
    pass


def _hidden():
    """On Windows, no console window flashing up each time it runs."""
    if not hasattr(subprocess, "STARTUPINFO"):
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": info}


class Tesseract:
    """cmd: the full path of the tesseract program (never looked up on PATH or in this folder)."""

    def __init__(self, cmd, timeout=30):
        self.cmd, self.timeout = cmd, timeout
        self.env = dict(os.environ, OMP_THREAD_LIMIT="1")
        self.runs = 0                                   # how many times Tesseract was started

    def read_lines(self, images, chars=None):
        """One line of text per picture (--psm 7), as a list of strings in the same order.
        chars: the only characters it may answer with."""
        if not images:
            return []
        with tempfile.TemporaryDirectory(prefix="maple-exp-ocr-") as folder:
            names = []
            for i, image in enumerate(images):
                ok, png = cv2.imencode(".png", image)   # (cv2.imwrite can't take every Windows path)
                if not ok:
                    raise TesseractError("couldn't save a picture for Tesseract")
                names.append(f"{i}.png")
                with open(os.path.join(folder, names[-1]), "wb") as f:
                    f.write(png.tobytes())
            with open(os.path.join(folder, "images.txt"), "w", encoding="ascii") as f:
                f.write("\n".join(names) + "\n")
            # (run in that folder, with plain file names: Tesseract can't open a path with letters
            # outside the PC's ANSI code page, like a user name with accents)
            args = [self.cmd, "images.txt", "stdout", "--psm", "7"]
            if chars:
                args += ["-c", f"tessedit_char_whitelist={chars}"]
            self.runs += 1
            run = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, env=self.env,
                                 timeout=self.timeout, cwd=folder, **_hidden())
        if run.returncode != 0:
            raise TesseractError(run.stderr.decode("utf-8", "replace").strip()[-300:] or f"exit code {run.returncode}")
        out = run.stdout.decode("utf-8", "replace")
        texts = out.split(PAGE_BREAK)                   # (a break between pictures, none after the last)
        if len(texts) != len(images):
            raise TesseractError(f"Tesseract read {len(texts)} pictures of {len(images)}")
        return texts

    def version(self):
        """Tesseract's version line (asked once), or None if it can't say."""
        if not hasattr(self, "_version"):
            try:
                run = subprocess.run([self.cmd, "--version"], capture_output=True, stdin=subprocess.DEVNULL,
                                     timeout=10, **_hidden())
                text = (run.stdout or run.stderr).decode("utf-8", "replace").strip()
                self._version = text.splitlines()[0] if text else None
            except Exception:
                self._version = None
        return self._version

    def read_line(self, image, chars=None):
        return self.read_lines([image], chars)[0]
