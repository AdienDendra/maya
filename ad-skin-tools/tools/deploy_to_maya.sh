#!/usr/bin/env bash
set -euo pipefail

REPO="$HOME/dev/dcc/maya/ad-skin-tools"

PACKAGE_SRC="$REPO/ad_skin_tools"
PACKAGE_DST="/mnt/c/Users/Arzio/Documents/maya/2023/scripts/ad_skin_tools"

TEST_SRC="$REPO/scripts/test_v27_automatic_surface.py"
TEST_DST="/mnt/c/Users/Arzio/Documents/maya/2023/scripts/test_v27_automatic_surface.py"

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

if [ -f "$TEST_SRC" ]; then
    echo "Deploying v2.7 test runner..."
    cp "$TEST_SRC" "$TEST_DST"
else
    echo "Warning: test runner not found: $TEST_SRC"
fi

echo "Done."