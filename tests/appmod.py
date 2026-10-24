"""The program as one namespace, for the tests: `import appmod as m` and then m.AutoLog, m.CONFIG_PATH, m.TESS...

The program is a package (maple_overlay/) split into folders by what each part does. The tests were written when it was
one file, and they swap things in it for the length of a check (m.CONFIG_PATH = ..., m.TESS = ..., m.time.monotonic = ...).
So reading m.X finds X in whichever module defines it, and setting m.X sets it in every module that has a name X (a
module that did `from ... import X` has its own copy), so the swap is seen wherever X is used.
"""
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NAMES = ("config", "updates", "core.levels", "core.exp_tracker", "core.meso_tracker", "core.session", "core.session_stats",
         "vision.engine", "vision.screen", "vision.exp_bar", "vision.job", "vision.text", "vision.maps", "vision.mesos",
         "vision.party", "vision.screen_reader", "logs.entries", "logs.autolog", "ui.format", "ui.region_selector",
         "ui.main_view", "ui.app")
MODULES = [importlib.import_module("maple_overlay." + n) for n in NAMES]


class _Program(types.ModuleType):
    def __getattr__(self, name):
        for mod in MODULES:
            if name in vars(mod):
                return vars(mod)[name]
        raise AttributeError(f"the program has no {name}")

    def __setattr__(self, name, value):
        hit = False
        for mod in MODULES:
            if name in vars(mod):
                setattr(mod, name, value)
                hit = True
        if not hit:
            raise AttributeError(f"the program has no {name} to set")


sys.modules[__name__].__class__ = _Program
