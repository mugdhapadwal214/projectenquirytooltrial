# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the RM Estimator.

Build with:   py -3.11 -m PyInstaller rm_estimator.spec
Never with a bare `pyinstaller` — that resolves to whatever interpreter is
first on PATH, which is rarely the one the app was tested against.

WHY data/ IS NOT BUNDLED
    Everything under data/ is meant to be edited: material compositions,
    cutting rates, the flange and pipe charts, and the live project, offcut and
    job records. Bundling those into the exe would freeze them, and worse,
    PyInstaller unpacks bundled files to a temp folder that is DELETED on exit
    — every project, every remnant and every booked job would vanish when the
    window closed.

    So data/ ships beside the exe and is written there. build_exe.bat copies it
    into dist/ after the build.

    templates/ and static/ are bundled, because they are code in all but name.
"""

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        # Read-only reference tables that the app cannot start without.
        ('flange_database.json', '.'),
    ],
    hiddenimports=[
        'webview',
        'docx',
        'openpyxl',
        # pywebview picks its GUI backend at runtime, so PyInstaller cannot
        # see the import. Without this the desktop window silently falls back
        # to the browser on a clean machine.
        'webview.platforms.winforms',
        'clr_loader',
        'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RM Estimator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no console window behind the app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='RM Estimator',
)
