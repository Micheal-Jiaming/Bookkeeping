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

FULL = os.environ.get('BOOKKEEPING_FULL_BUILD') == '1'

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

bundled = ['anthropic', 'httpx', 'httpcore', 'certifi', 'winrt']

# RapidOCR reads receipts markedly better than the Windows engine -- over the
# eight hand-verified photographs, 81 printed lines matched against 66, 4 missed
# against 19, none invented against 3, and 76 item names exact against 41 -- and
# costs 95 MB and 1.7 seconds of start-up to carry.
# (Figures from tests/fixtures/accuracy_baseline.{rapid,windows}.json. Name the
# corpus or do not quote the number: this comment carried seven-photograph,
# pre-skew-correction figures long after they stopped being true.)
# Only the full build pays that. `collect_all` is what brings the ONNX
# model weights along as data files -- an import scan finds the code and none of
# the models, and RapidOCR then looks for them under sys._MEIPASS and finds
# nothing.
# Nothing here runs tests or builds packages; leaving these out saves a few MB
# and a slower start. tkinter is deliberately absent from this list.
excludes = ['pytest', '_pytest', 'PyInstaller', 'setuptools', 'pip',
            'unittest', 'pydoc_data']

if FULL:
    hiddenimports += ['rapidocr', 'onnxruntime']
    bundled += ['rapidocr', 'onnxruntime']
else:
    # **The slim build has to exclude these by name, and cannot do it any other
    # way.** app/extract/rapid_ocr.py already loads both through
    # `importlib.import_module` rather than an import statement, which is the
    # usual way to keep something out of the bundle -- and PyInstaller resolves
    # a literal module name passed to importlib just as it resolves an import.
    # The graph still showed `rapidocr imported by app.extract.rapid_ocr`, and
    # the slim build came out at 97 MB instead of 30: cv2 alone was 29 MB of it.
    #
    # So the exclusion is stated here instead, which is also the more honest
    # place for it: this is the file that decides what each build contains.
    excludes += ['rapidocr', 'onnxruntime', 'cv2', 'numpy', 'shapely',
                 'pyclipper', 'omegaconf', 'antlr4', 'requests', 'tqdm']

for package in bundled:
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
    excludes=excludes,
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
    name='BookkeepingFull' if FULL else 'Bookkeeping',
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
