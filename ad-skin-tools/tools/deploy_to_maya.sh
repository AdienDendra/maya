#!/usr/bin/env bash
set -e

SRC="$HOME/dev/dcc/maya/ad-skin-tools/ad_skin_tools"
DST="/mnt/c/Users/Arzio/Documents/maya/2023/scripts/ad_skin_tools"

echo "Deploying AD Skin Tools..."
echo "From: $SRC"
echo "To:   $DST"

if [ ! -d "$SRC" ]; then
    echo "Source folder not found: $SRC"
    exit 1
fi

rm -rf "$DST"
mkdir -p "$(dirname "$DST")"
cp -r "$SRC" "$DST"

echo "Done."