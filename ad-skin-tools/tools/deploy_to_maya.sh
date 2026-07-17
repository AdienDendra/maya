#!/usr/bin/env bash
set -euo pipefail

REPO="$HOME/dev/dcc/maya/ad-skin-tools"

PACKAGE_SRC="$REPO/ad_skin_tools"
PACKAGE_DST="/mnt/c/Users/Arzio/Documents/maya/2023/scripts/ad_skin_tools"
SCRIPT_DST_DIR="/mnt/c/Users/Arzio/Documents/maya/2023/scripts"

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
CURRENT_COMMIT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true)"

echo "Deploying AD Skin Tools..."
echo "Repository:     $REPO"
echo "Git branch:     ${CURRENT_BRANCH:-<unknown>}"
echo "Git commit:     ${CURRENT_COMMIT:-<unknown>}"
echo "Package from:   $PACKAGE_SRC"
echo "Package to:     $PACKAGE_DST"

if [ ! -d "$PACKAGE_SRC" ]; then
    echo "Source package not found: $PACKAGE_SRC"
    exit 1
fi

required_v41_files=(
    "$PACKAGE_SRC/core/component_selection.py"
    "$PACKAGE_SRC/core/influence_lock.py"
    "$PACKAGE_SRC/core/component_flood.py"
    "$PACKAGE_SRC/ui/component_flood_section.py"
)

for required_file in "${required_v41_files[@]}"; do
    if [ ! -f "$required_file" ]; then
        echo "ERROR: v4.1 source file is missing: $required_file"
        echo "Pull the latest feature/ad-skin-v4-component-flood branch before deploying."
        exit 1
    fi
done

rm -rf "$PACKAGE_DST"
mkdir -p "$(dirname "$PACKAGE_DST")"
cp -r "$PACKAGE_SRC" "$PACKAGE_DST"

# Never retain bytecode from a previous package revision.
find "$PACKAGE_DST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DST" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

for relative_path in \
    "core/component_selection.py" \
    "core/influence_lock.py" \
    "core/component_flood.py" \
    "ui/component_flood_section.py"; do
    if [ ! -f "$PACKAGE_DST/$relative_path" ]; then
        echo "ERROR: v4.1 deployment verification failed: $PACKAGE_DST/$relative_path"
        exit 1
    fi
done

mkdir -p "$SCRIPT_DST_DIR"

# Remove runners from retired experimental package names.
rm -f \
    "$SCRIPT_DST_DIR/test_v30_distance_ranking.py" \
    "$SCRIPT_DST_DIR/test_v33_ownership_connectivity_probe.py" \
    "$SCRIPT_DST_DIR/test_v34_region_facing_probe.py"

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

echo
echo "Other ad_skin_tools copies under the Maya documents directory:"
find /mnt/c/Users/Arzio/Documents/maya \
    -type d \
    -name ad_skin_tools \
    -path '*/scripts/ad_skin_tools' \
    -print 2>/dev/null || true

echo
echo "v4.1 deployment verified."
echo "Restart Maya or purge cached ad_skin_tools modules before reopening the UI."
echo "Diagnostic runner: $SCRIPT_DST_DIR/test_v40_install_diagnostic.py"
echo "Done."
