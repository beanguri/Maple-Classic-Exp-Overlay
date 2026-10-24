"""Maple Classic Exp Overlay: EXP/hr, mesos/hr and time to level for MapleStory Classic World.

The package is split by what its parts do:
    core/       the numbers: EXP and mesos trackers, a session, the Solo / Party statistics
    vision/     reading the game's screen: EXP bar, map, mesos, party, potions, the message lines
    logs/       the EXP log, its rows and details window, bug reports
    ui/         the windows and how they are drawn
    platforms/  what differs between Windows and macOS
    config.py   the files, settings and numbers the parts share
"""
import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")        # (before numpy is imported anywhere)
