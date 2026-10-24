"""The overlay's colours (the same family as the website), and the table, scrollbar and slider styles.
The smooth widgets are in ui_main.py, drawn by ui_gfx.py."""
from tkinter import ttk

# ---- colours: one dark family, one gold. (The website uses the same.)
BG = "#0b0c10"            # the window
SURFACE = "#14161c"       # cards
RAISED = "#232732"        # buttons, inputs
RAISED_HI = "#2c313e"     # ...hovered
RAISED_LO = "#1b1e26"     # ...pressed
LINE = "#242833"          # hairlines
FG = "#f1efe9"            # main text (slightly warm, easier on the eyes than pure white)
DIM = "#9ba1ad"           # labels
FAINT = "#6c7280"         # hints
ACCENT = "#ffcc33"        # the one gold
ACCENT_HI = "#ffd966"
ACCENT_LO = "#e5b52b"
ACCENT_SOFT = "#d9b04a"   # a quieter gold, for text next to the big number
ON_ACCENT = "#1a1608"     # text on gold
GOOD = "#5fd38a"
WARN = "#ffb27a"
BAD = "#ff7a5c"
SELECT = "#272c3a"        # a selected row

# the table
LIST_BG, LIST_BG2, LIST_PART = "#101218", "#13151c", "#0d0f14"


def setup_ttk(root):
    """The table, the scrollbar and the slider, in the overlay's colours."""
    st = ttk.Style(root)
    st.theme_use("clam")
    st.layout("Dark.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Dark.Vertical.TScrollbar", troughcolor=BG, background=RAISED_HI, bordercolor=BG,
                 lightcolor=RAISED_HI, darkcolor=RAISED_HI, relief="flat", width=10, gripcount=0)
    st.map("Dark.Vertical.TScrollbar", background=[("active", FAINT), ("pressed", DIM)])
    st.configure("Gold.Horizontal.TScale", background=ACCENT, troughcolor=RAISED, bordercolor=BG,
                 lightcolor=ACCENT, darkcolor=ACCENT, sliderthickness=14, sliderrelief="flat")
    st.map("Gold.Horizontal.TScale", background=[("active", ACCENT_HI)])
    return st
