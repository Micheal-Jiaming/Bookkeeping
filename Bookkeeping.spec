# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of Bookkeeping: one portable windowed .exe.

Build with `build.bat`, or:
    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm Bookkeeping.spec

Notes that were learned the hard way, so they do not get "cleaned up" later:

* Paths are built from SPECPATH so the project folder can be moved or renamed.
* `VERSION` and the icon are bundled *under `app/`* because the code looks for
  them at `sys._MEIPASS/app/...` (see app/paths.py:resource_dir).
* **tkinter must NOT be excluded.** It was on the exclude list while the
  interface was a web page; leaving it there once the interface became a Tk
  window produces an .exe that dies instantly with no window and no message.
* `collect_all` is run for the Anthropic SDK's HTTP stack: those packages carry
  data files (CA bundle, type metadata) that an import scan misses, and without
  them the .exe cannot make an API call even though it starts fine.
* `console=False`: this is a desktop app, and a console window flashing behind
  it looks like a fault. That is also why app/launcher.py never assumes
  sys.stdout exists and reports fatal errors with a message box.
"""

import os

from PyInstaller.utils.hooks import collect_all

SCRIPT = os.path.join(SPECPATH, 'bookkeeping.py')
ICON = os.path.join(SPECPATH, 'assets', 'icon.ico')

datas = [
    (os.path.join(SPECPATH, 'VERSION'), 'app'),
    # The window sets its own title-bar icon from this copy at runtime.
    (ICON, 'app'),
]
binaries = []

# Both offline engines import lazily -- inside the method that uses them -- so
# the analysis cannot see either. The Tesseract binary is not bundled; it is a
# separate install, by design. The Windows OCR recogniser is not bundled either,
# and cannot be: it is part of the operating system, and these packages are only
# the bindings that reach it.
hiddenimports = ['pytesseract']

for package in ('winrt.windows.media.ocr', 'winrt.windows.graphics.imaging',
                'winrt.windows.storage.streams', 'winrt.windows.globalization',
                'winrt.windows.foundation', 'winrt.windows.foundation.collections'):
    hiddenimports.append(package)

for package in ('anthropic', 'httpx', 'httpcore', 'certifi', 'winrt'):
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
    # Nothing here runs tests or builds packages; leaving these out saves a few
    # MB and a slower start. tkinter is deliberately absent from this list.
    excludes=['pytest', '_pytest', 'PyInstaller', 'setuptools', 'pip',
              'unittest', 'pydoc_data'],
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
