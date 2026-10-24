"""The text reader (Tesseract), only a fallback; main() finds it and sets it here for everyone."""
import os

from maple_overlay.config import APP_DIR, HOME_DIR, PLATFORM


tesseract_ok = False        # set in main(); Tesseract is only a fallback

TESS = None                 # the ocr.Tesseract to read text with, once main() has found one

def find_tesseract():
    """The Tesseract that comes with the download, else an installed one. Never one in the
    folder the overlay happens to be started from (Windows' usual search looks there first,
    so a tesseract.exe someone left in, say, Downloads could be run)."""
    return next((str(p) for p in PLATFORM.tesseract_candidates(HOME_DIR, APP_DIR)
                 if p.is_file() and os.access(p, os.X_OK)), None)
