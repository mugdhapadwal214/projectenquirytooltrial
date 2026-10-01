# Building the RM Estimator into an application

## Quick version

```
build_exe.bat
```

Then, from `dist\RM Estimator\`, run `register_rmp.bat` **as a normal user**
so `.rmp` files open on double-click.

---

## If the build says "No suitable Python runtime found"

That message comes from the Windows `py` launcher, not from this app. It means
the version being asked for is not installed.

The scripts no longer hardcode a version. `find_python.bat` probes, in order:

```
py -3.12   py -3.11   py -3.13   py -3.10   py -3   python
```

and uses the first that answers. To change the preference, edit that one file.

## Which Python to use

**3.12 or 3.11.** Not because the app needs them — it runs on anything from
3.10 up — but because **PyInstaller and pywebview are what lag new releases**.
A brand-new Python often runs the app perfectly and cannot package it.

You are on 3.14. Two ways forward:

1. **Install Python 3.12 alongside 3.14.** They coexist; `find_python.bat`
   will prefer 3.12 automatically and 3.14 stays for everything else.
2. **Try the build on 3.14 anyway.** `build_exe.bat` warns and continues. If
   PyInstaller has no wheel yet it fails at step 2 with a clear message.

The app does not need to be frozen to be used. `run.bat` starts it from source
on any of these versions.

## What ends up where

```
dist\RM Estimator\
    RM Estimator.exe        the application
    data\                   YOUR data - projects, offcut stock, jobs,
                            materials, cutting rates. Editable. Back this up.
    register_rmp.bat        file association for .rmp
```

`data\` sits **next to** the exe, never inside it. PyInstaller unpacks bundled
files to a temporary folder that Windows deletes when the app closes — if the
data were bundled, every project, every remnant and every booked job would be
lost the moment the window shut.

`templates\` and `static\` **are** bundled, because they are code in all but
name and are never edited in normal use.

## File association

`register_rmp.bat` writes to `HKEY_CURRENT_USER`. Run it **as a normal user**.
Run it elevated and the keys go into the administrator's hive instead of
yours, and double-clicking a `.rmp` does nothing.

## Upgrading later

Replace the exe and the `templates`/`static` folders. **Leave `data\` alone** —
that is your projects, your offcut stock and your job history.

## Distributing to other PCs — what actually needs to be there

**Python is only needed on the machine that runs `build_exe.bat`.** That
produces `dist\RM Estimator\`, a self-contained folder — Python's own
interpreter is bundled inside the exe. Copy that *whole folder* (not just the
.exe) to another PC and it runs with nothing installed. If you instead zip up
the source `.py` files and `run.bat`, that PC needs Python — `run.bat` is a
from-source dev shortcut, not the distributable.

**If the exe opens but the app doesn't work properly** on another PC (blank
panels, buttons that don't respond, broken layout) — almost always this means
that PC is missing the **Microsoft Edge WebView2 Runtime**. Windows 11 and
current Windows 10 ship it, but some older or stripped-down installs don't.
Without it, the desktop window silently renders with a decades-old Internet
Explorer engine instead of raising an error — the window still opens, so it
looks like the app started, but it can't run this app's JavaScript.

This version checks for WebView2 at startup and, if it's missing, opens the
app in the PC's normal browser instead (which always has a modern engine)
with an on-screen note pointing at the WebView2 installer. To fix it
properly on that PC: download and run the WebView2 Runtime installer once
from https://developer.microsoft.com/microsoft-edge/webview2/ (a few MB,
one-time, works fully offline afterwards), then the desktop window works too.
