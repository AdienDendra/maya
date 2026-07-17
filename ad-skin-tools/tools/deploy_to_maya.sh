#!/usr/bin/env bash
set -euo pipefail

REPO="$HOME/dev/dcc/maya/ad-skin-tools"

PACKAGE_SRC="$REPO/ad_skin_tools"
PACKAGE_DST="/mnt/c/Users/Arzio/Documents/maya/2023/scripts/ad_skin_tools"
SCRIPT_DST_DIR="/mnt/c/Users/Arzio/Documents/maya/2023/scripts"

echo "Deploying AD Skin Tools..."
echo "Package from: $PACKAGE_SRC"
echo "Package to:   $PACKAGE_DST"

if [ ! -d "$PACKAGE_SRC" ]; then
    echo "Source package not found: $PACKAGE_SRC"
    exit 1
fi

rm -rf "$PACKAGE_DST"
mkdir -p "$(dirname "$PACKAGE_DST")"
cp -r "$PACKAGE_SRC" "$PACKAGE_DST"

mkdir -p "$SCRIPT_DST_DIR"
found_runner=false
for test_src in "$REPO"/scripts/test_*.py; do
    if [ ! -f "$test_src" ]; then
        continue
    fi

    found_runner=true
    echo "Deploying smoke runner: $(basename "$test_src")"
    cp "$test_src" "$SCRIPT_DST_DIR/$(basename "$test_src")"
done

if [ "$found_runner" = false ]; then
    echo "Warning: no smoke runners found in $REPO/scripts"
fi

echo "Done."
