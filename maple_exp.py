"""Maple Classic Exp Overlay: EXP/hr, mesos/hr and time to level for MapleStory Classic World.

Run this file to start it. The program itself is in the maple_overlay/ folder.

It only looks at the screen: it never reads or changes the game's files or memory and never sends input
to the game. Your settings and log are kept in the MapleEXP folder inside %APPDATA% on Windows,
~/Library/Application Support on macOS (named after its old name, Maple EXP/hr, so updating keeps them).
"""
from maple_overlay.ui.app import main

if __name__ == "__main__":
    main()
