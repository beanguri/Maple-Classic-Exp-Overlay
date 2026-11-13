#!/bin/sh
# Runs Maple Classic Exp Overlay from source on Linux (X11 or Wayland with XWayland).
# Needs Tesseract with English data (Arch: pacman -S tesseract tesseract-data-eng) and Tk (pacman -S tk).
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
# Tk must use XWayland, so the overlay shares the game's coordinates and can stay on top.
exec env GDK_BACKEND=x11 QT_QPA_PLATFORM=xcb .venv/bin/python maple_exp.py "$@"
