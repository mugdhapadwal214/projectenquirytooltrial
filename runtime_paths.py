"""
runtime_paths.py — where the app's files actually live.

THE TRAP THIS AVOIDS
    Every module used to build its data path from __file__. That is correct
    when running from source, and quietly wrong once frozen: PyInstaller
    unpacks the bundle to a temporary folder and points __file__ there, and
    that folder is DELETED when the app closes.

    So an exe built naively would appear to work — projects save, offcut is
    racked, jobs are booked — and lose every last byte of it the moment the
    window shut.

THE RULE
    Anything the user OWNS (projects, offcut stock, job ledger, material and
    cutting settings) lives beside the exe and is resolved with data_path().

    Anything that is really code (templates, static, the flange chart) is
    bundled inside and resolved with bundle_path().
"""
import os
import sys

sys.dont_write_bytecode = True


def is_frozen():
    return getattr(sys, "frozen", False)


def app_dir():
    """Folder the exe sits in, or the source folder when running from source."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Where PyInstaller unpacked the read-only bundle. Temporary — never write here."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_path(*parts):
    """A file the user owns. Always beside the exe, so it survives."""
    p = os.path.join(app_dir(), "data", *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def bundle_path(*parts):
    """
    A read-only file shipped with the app.

    Falls back to the folder beside the exe, so a file the user has replaced
    on purpose still wins over the bundled copy.
    """
    beside = os.path.join(app_dir(), *parts)
    if os.path.exists(beside):
        return beside
    return os.path.join(bundle_dir(), *parts)


def output_dir(sub):
    d = os.path.join(app_dir(), sub)
    os.makedirs(d, exist_ok=True)
    return d


def purge_pycache():
    """Drop stale __pycache__ so replaced .py files always load fresh."""
    for dirpath, dirnames, _ in os.walk(app_dir()):
        for d in list(dirnames):
            if d == "__pycache__":
                target = os.path.join(dirpath, d)
                try:
                    for f in os.listdir(target):
                        os.remove(os.path.join(target, f))
                    os.rmdir(target)
                except OSError:
                    pass
                dirnames.remove(d)
