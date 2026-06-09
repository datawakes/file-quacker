# PyInstaller spec for File Quacker (one-file mode).
#
# Run `npm --prefix frontend run build` first so frontend/dist/ exists,
# then `pyinstaller file_quacker.spec` to build dist/file_quacker.exe.

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PROJECT_ROOT = Path(SPECPATH).resolve()

# Read __version__ directly from the source file rather than importing
# file_quacker — the package pulls in pyodbc / duckdb / pywebview at
# import time, which we don't want to do from inside the spec.
_init_text = (PROJECT_ROOT / 'file_quacker' / '__init__.py').read_text(encoding='utf-8')
_match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", _init_text)
if not _match:
    raise RuntimeError("could not parse __version__ from file_quacker/__init__.py")
APP_VERSION = _match.group(1)

# Windows VERSIONINFO wants a 4-part tuple of ints.  Pad with zeros so
# semver-style 'X.Y.Z' becomes (X, Y, Z, 0).
_parts = [int(p) for p in APP_VERSION.split('.')]
while len(_parts) < 4:
    _parts.append(0)
VERSION_TUPLE = tuple(_parts[:4])
VERSION_STRING = '.'.join(str(p) for p in VERSION_TUPLE)

# Right-click -> Properties -> Details on the built exe reads this.
# StringTable id '040904B0' is en-US + Unicode codepage; matches the
# VarFileInfo Translation entry below.  Keep them in sync.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=VERSION_TUPLE,
        prodvers=VERSION_TUPLE,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable('040904B0', [
                StringStruct('CompanyName',      'Datawake LLC'),
                StringStruct('FileDescription',  'File Quacker'),
                StringStruct('FileVersion',      VERSION_STRING),
                StringStruct('InternalName',     'file_quacker'),
                StringStruct('LegalCopyright',   'Copyright (c) 2026 Tim Civatte. MIT License.'),
                StringStruct('OriginalFilename', 'file_quacker.exe'),
                StringStruct('ProductName',      'File Quacker'),
                StringStruct('ProductVersion',   VERSION_STRING),
            ]),
        ]),
        VarFileInfo([VarStruct('Translation', [0x0409, 0x04B0])]),
    ],
)

# pywebview ships platform-specific backends that PyInstaller's static
# analysis can miss; collect_all walks the package and picks them up.
pywebview_datas, pywebview_binaries, pywebview_hiddenimports = collect_all('webview')

a = Analysis(
    [str(PROJECT_ROOT / 'file_quacker' / '__main__.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=pywebview_binaries,
    datas=[
        (str(PROJECT_ROOT / 'frontend' / 'dist'), 'dist'),
        (str(PROJECT_ROOT / 'file_quacker.ico'), '.'),
        # keyring discovers its backends via importlib metadata entry
        # points, which PyInstaller's static analysis can't see; ship the
        # dist-info so the Windows Credential Manager backend resolves in
        # the frozen exe instead of silently falling back to a no-op.
        *copy_metadata('keyring'),
        *pywebview_datas,
    ],
    # keyring.backends.Windows + its pywin32-ctypes shim are imported
    # dynamically; name them explicitly so the vault works when bundled.
    hiddenimports=[
        'duckdb', 'chardet',
        'keyring.backends.Windows',
        'win32ctypes.core',
        'win32ctypes.core.ctypes',
        *pywebview_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='file_quacker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / 'file_quacker.ico'),
    version=version_info,
)
