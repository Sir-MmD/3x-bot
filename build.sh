#!/usr/bin/env bash
set -e

# Build a fully static 3x-bot binary (no dependencies, not even glibc)
# Requires: pip install pyinstaller staticx patchelf

echo "[1/4] Building with PyInstaller..."
pyinstaller 3x-bot.spec --noconfirm 2>&1 | tail -1

echo "[2/4] Patching RUNPATH in cached libraries..."
for lib in ~/.cache/pyinstaller/bincache*/**.so*; do
    rpath=$(patchelf --print-rpath "$lib" 2>/dev/null)
    if [[ -n "$rpath" ]]; then
        patchelf --remove-rpath "$lib"
    fi
done 2>/dev/null

echo "[3/4] Rebuilding with patched libraries..."
rm -rf build/3x-bot dist/3x-bot
pyinstaller 3x-bot.spec --noconfirm 2>&1 | tail -1

echo "[4/4] Creating static binary..."
staticx dist/3x-bot dist/3x-bot-static 2>/dev/null

SIZE=$(du -h dist/3x-bot-static | cut -f1)
echo ""
echo "Done! dist/3x-bot-static (${SIZE}, fully static)"
