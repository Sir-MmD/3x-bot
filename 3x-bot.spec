# -*- mode: python ; coding: utf-8 -*-
import pathlib
from PyInstaller.utils.hooks import collect_all

# Collect all data/binaries/hidden-imports for packages with native extensions.
# This ensures .so files, data files, and submodules are all bundled.
_all_datas = []
_all_binaries = []
_all_hiddenimports = []
for _pkg in ('PIL', 'uharfbuzz', 'cryptography', 'cffi'):
    try:
        _d, _b, _h = collect_all(_pkg)
        _all_datas += _d
        _all_binaries += _b
        _all_hiddenimports += _h
    except Exception:
        pass

# Collect vendored native libs from manylinux .libs directories
# (e.g. Pillow.libs/libtiff-*.so, cryptography.libs/libssl-*.so)
# collect_all() only gets the package dir, missing sibling *.libs/ dirs.
# Place them at '.' (top-level) so the dynamic linker can find them
# after RPATH is stripped during the static build.
import PIL
_site_packages = pathlib.Path(PIL.__file__).parent.parent
_vendored_binaries = []
for _libs_dir in sorted(_site_packages.glob('*.libs')):
    for _so in _libs_dir.rglob('*.so*'):
        if _so.is_file():
            _vendored_binaries.append((str(_so), '.'))

a = Analysis(
    ['bot.py'],
    pathex=[],
    binaries=_all_binaries + _vendored_binaries,
    datas=[
        ('translations/*.toml', 'translations'),
        ('fonts/*.ttf', 'fonts'),
    ] + _all_datas,
    hiddenimports=[
        'handlers.menu',
        'handlers.search',
        'handlers.modify',
        'handlers.create',
        'handlers.inbounds',
        'handlers.bulk_ops',
        'handlers.owner',
        'handlers.router',
        '_cffi_backend',
    ] + _all_hiddenimports,
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
    name='3x-bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
)
