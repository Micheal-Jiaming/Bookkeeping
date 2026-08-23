# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of Bookkeeping: one portable windowed .exe.

Build with `build.bat`, or:
    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm Bookkeeping.spec

Notes that were learned the hard way, so they do not get "cleaned up" later:

* Paths are built from SPECPATH so the project folder can be moved or renamed.
* `app/static` and `VERSION` are bundled *under `app/`* because the code looks
  for them at `sys._MEIPASS/app/...` (see app/paths.py:resource_dir).
* uvicorn loads its protocol/loop implementations by string name at runtime, so
  PyInstaller's static analysis cannot see them -- they must be listed as hidden
  imports or the server dies on the first request.
* `console=False`: this is a browser-UI app, and a stray console window looks
  like a bug. That is also why app/launcher.py never assumes sys.stdout exists.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

SCRIPT = os.path.join(SPECPATH, 'bookkeeping.py')
ICON = os.path.join(SPECPATH, 'assets', 'icon.ico')

datas = [
    (os.path.join(SPECPATH, 'app', 'static'), 'app/static'),
    (os.path.join(SPECPATH, 'VERSION'), 'app'),
]
binaries = []

# uvicorn's dynamic imports, plus the optional OCR wrapper (the Tesseract binary
# itself is not bundled -- it is a separate install, by design).
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'pytesseract',
]
hiddenimports += collect_submodules('uvicorn')

# The Anthropic SDK and its HTTP stack pull in data files (CA bundle, type
# metadata) that a plain import scan misses.
for package in ('anthropic', 'httpx', 'httpcore', 'certifi'):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [SCRIPT],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here draws a GUI toolkit or runs tests; leaving them out saves
    # roughly 10 MB and a slower start.
    excludes=['tkinter', 'pytest', '_pytest', 'PyInstaller', 'setuptools', 'pip'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Bookkeeping',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICON],
)
