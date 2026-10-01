"""
filesave.py — writing files out of the app, on the desktop and in a browser.

THE PROBLEM THIS SOLVES
    In a browser, handing the user a file is easy: send it with a
    Content-Disposition header and the browser downloads it.

    Inside a pywebview window there is NO download manager. The same response
    silently does nothing — the window either ignores it or navigates away and
    loses the page. That is why "Save as file" appeared to do nothing in the
    desktop build: the request was fine, there was simply nothing on the other
    end to catch it.

HOW IT WORKS NOW
    1. If a pywebview window is running, open the platform's native Save
       dialog so the user picks the location, exactly like any other desktop
       app.
    2. If they cancel, nothing is written and the UI says so.
    3. With no pywebview (the --web mode), fall back to a folder beside the
       application and report the full path, so the file is always findable.

    The browser download path is kept for --web, where it is the natural
    behaviour.
"""
import os
import sys

# Set by app.main() once the desktop window exists. Left as None in browser
# mode, which is what selects the fallback path below.
_WINDOW = None


def register_window(window):
    global _WINDOW
    _WINDOW = window


def app_dir():
    """Folder the app lives in — beside the exe once frozen."""
    try:
        from .runtime_paths import app_dir as _ad
    except ImportError:
        from runtime_paths import app_dir as _ad
    return _ad()


def default_dir(sub):
    d = os.path.join(app_dir(), sub)
    os.makedirs(d, exist_ok=True)
    return d


def has_desktop_dialog():
    return _WINDOW is not None


def ask_save_path(filename, file_types=()):
    """
    Native Save dialog. Returns the chosen path, or None if the user
    cancelled. Returns False when there is no desktop window at all, which
    the caller uses to pick the fallback.
    """
    if _WINDOW is None:
        return False
    try:
        import webview
        result = _WINDOW.create_file_dialog(
            webview.SAVE_DIALOG,
            directory=default_dir("saved"),
            save_filename=filename,
            file_types=file_types or (),
        )
    except Exception:
        # Any dialog failure falls back to writing a file rather than losing
        # the user's work.
        return False
    if not result:
        return None                       # cancelled
    return result if isinstance(result, str) else result[0]


def ask_folder(sub="saved"):
    """
    Native "choose folder" dialog, for when several files are written at once.

    One RFQ per vendor means several documents in one go. Asking where to put
    each of them separately would be tedious, so the user picks the folder once
    and everything lands there.

    Returns a path, None if cancelled, or False when there is no desktop window
    (browser mode), which tells the caller to use the default folder.
    """
    if _WINDOW is None:
        return False
    try:
        import webview
        result = _WINDOW.create_file_dialog(
            webview.FOLDER_DIALOG, directory=default_dir(sub))
    except Exception:
        return False
    if not result:
        return None
    return result if isinstance(result, str) else result[0]


def write(data, filename, sub="saved", file_types=()):
    """
    Save `data` (str or bytes) and report where it went.

    Returns {"saved": bool, "path": str|None, "cancelled": bool, "how": str}
    """
    chosen = ask_save_path(filename, file_types)
    if chosen is None:
        return {"saved": False, "path": None, "cancelled": True, "how": "dialog"}

    if chosen is False:
        path = os.path.join(default_dir(sub), filename)
        how = "folder"
    else:
        path = chosen
        how = "dialog"

    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    kwargs = {} if "b" in mode else {"encoding": "utf-8"}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, mode, **kwargs) as f:
        f.write(data)
    return {"saved": True, "path": path, "cancelled": False, "how": how}


def reveal(path):
    """Open the containing folder, so the user can get at what was written."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        os.startfile(folder)                       # noqa: F821 — Windows
        return True
    except AttributeError:
        pass
    try:
        import subprocess
        subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False
