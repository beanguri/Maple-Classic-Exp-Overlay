"""The levels' EXP table and what can be worked out from it."""



# EXP needed to finish each level in MapleStory Classic World (index 0 = level 1),
# from meowdb.com/msclassic/guides/exp-table-level-1-to-100.
EXP_TABLE = [
    15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716,
    2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680, 20216,
    24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700,
    108480, 122760, 138666, 155540, 174216, 194832, 216600, 240500, 266682, 294216,
    324240, 356916, 391160, 428280, 468450, 510420, 555680, 604416, 655200, 709716,
    748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536, 1147132, 1209994,
    1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750, 2062925,
    2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
    3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059,
    25069923, 52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548,
]

# --------------------------------------------------------------------------- map name and level
#
# The minimap's light-blue info box shows the region and, under it, the map name in white
# text. It's read with Tesseract a few different ways, and the most common answer wins.
# The level comes from the EXP table: each level needs a different amount of EXP.
def level_for_req(req):
    """The level whose EXP requirement matches req, from the Classic World EXP table."""
    if not req:
        return None
    for level, need in enumerate(EXP_TABLE, start=1):
        if abs(req - need) <= 0.005 * need:
            return level
    return None

def levels_for(exp, pct):
    """The levels whose EXP requirement fits exp at pct% (% is shown with 2 decimals)."""
    req, err = exp / (pct / 100), 0.002 + 0.01 / pct
    return [level for level, need in enumerate(EXP_TABLE, start=1)
            if abs(req - need) <= err * need and exp < need]

def fits_exp_table(exp, pct):
    """True if exp at pct% matches some level's EXP requirement. A misread almost never does
    (e.g. '1 [71.68%]' for '68,601 [71.68%]'), so a reading that doesn't isn't trusted."""
    return pct < 0.2 or bool(levels_for(exp, pct))      # under 0.2%: too little EXP to tell
