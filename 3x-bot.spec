# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['bot.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('translations/*.toml', 'translations'),
        ('fonts/*.ttf', 'fonts'),
    ],
    hiddenimports=[
        'handlers.menu',
        'handlers.search',
        'handlers.modify',
        'handlers.create',
        'handlers.inbounds',
        'handlers.bulk_ops',
        'handlers.owner',
        'handlers.router',
        'PIL',
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
    name='3x-bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
)
