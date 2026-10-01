"""
enquiry_link.py — the way into the Project Enquiry tool's shared lists.

Both applications have a module called database.py (and calculator.py), so a
plain `import accounts` from inside the estimator picks up the estimator's
database.py and reads the wrong folder. This loads the enquiry tool's modules
with its own folder first on the path, then puts the estimator's modules back,
so each side keeps the modules it expects.

What is shared:
    accounts.py  — the vendor master  (vendors.py here reads and writes it)
    store.py     — the Store          (inventory.py keeps the offcut stock there)
"""
import importlib
import os
import sys

PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = {}
_SWAPPABLE = ("database", "calculator", "accounts", "store", "quotation")


def _load(name):
    """Import one of the enquiry tool's modules without disturbing ours."""
    if name in _CACHE:
        return _CACHE[name]
    if not os.path.exists(os.path.join(PARENT, name + ".py")):
        _CACHE[name] = None
        return None
    saved = {n: sys.modules.pop(n, None) for n in _SWAPPABLE}
    sys.path.insert(0, PARENT)
    try:
        mod = importlib.import_module(name)
        _CACHE[name] = mod
        return mod
    except Exception:                       # standalone estimator, or a broken install
        _CACHE[name] = None
        return None
    finally:
        try:
            sys.path.remove(PARENT)
        except ValueError:
            pass
        for n, m in saved.items():          # give the estimator its modules back
            if m is not None:
                sys.modules[n] = m
            elif n in sys.modules and sys.modules[n].__file__ and \
                    os.path.dirname(os.path.abspath(sys.modules[n].__file__)) == PARENT:
                del sys.modules[n]


def accounts():
    return _load("accounts")


def store():
    return _load("store")
