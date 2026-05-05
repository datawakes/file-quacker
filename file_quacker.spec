# PyInstaller spec for File Quacker (one-file mode).
#
# Run `npm --prefix frontend run build` first so frontend/dist/ exists,
# then `pyinstaller file_quacker.spec` to build dist/file_quacker.exe.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PROJECT_ROOT = Path(SPECPATH).resolve()

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
        *pywebview_datas,
    ],
    hiddenimports=['duckdb', 'chardet', *pywebview_hiddenimports],
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
)
