# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

pil_datas, pil_binaries, pil_hiddenimports = collect_all('PIL')

a = Analysis(
    ['bot.py'],
    pathex=[],
    binaries=pil_binaries,
    datas=[
        ('translations/*.toml', 'translations'),
        ('fonts/*.ttf', 'fonts'),
    ] + pil_datas,
    hiddenimports=[
        'handlers.menu',
        'handlers.search',
        'handlers.modify',
        'handlers.create',
        'handlers.inbounds',
        'handlers.bulk_ops',
        'handlers.owner',
        'handlers.router',
    ] + pil_hiddenimports,
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
