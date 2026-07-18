#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
MAYA_VERSION="${MAYA_VERSION:-2023}"

# Optional manual override using a WSL profile path:
#   WINDOWS_PROFILE=/mnt/c/Users/Arzio ./deploy_to_maya.sh
WINDOWS_PROFILE="${WINDOWS_PROFILE:-}"

if [ -z "$WINDOWS_PROFILE" ]; then
    WINDOWS_PROFILE_WINDOWS="$(
        cmd.exe /d /c 'echo %USERPROFILE%' 2>/dev/null \
            | tr -d '\r\n' \
            || true
    )"

    if [ -z "$WINDOWS_PROFILE_WINDOWS" ] || [ "$WINDOWS_PROFILE_WINDOWS" = "%USERPROFILE%" ]; then
        echo "ERROR: Unable to detect the Windows profile directory."
        echo "Run the deployment with an explicit profile path:"
        echo "       WINDOWS_PROFILE=/mnt/c/Users/<profile> ./deploy_to_maya.sh"
        exit 1
    fi

    WINDOWS_PROFILE="$(wslpath -u "$WINDOWS_PROFILE_WINDOWS" 2>/dev/null || true)"
fi

if [ -z "$WINDOWS_PROFILE" ] || [ ! -d "$WINDOWS_PROFILE" ]; then
    echo "ERROR: Windows profile directory is unavailable from WSL:"
    echo "       ${WINDOWS_PROFILE:-<empty>}"
    echo "Run with an explicit profile path, for example:"
    echo "       WINDOWS_PROFILE=/mnt/c/Users/Arzio ./deploy_to_maya.sh"
    exit 1
fi

PACKAGE_SRC="$REPO/ad_skin_tools"
WINDOWS_DOCUMENTS="$WINDOWS_PROFILE/Documents"
SCRIPT_DST_DIR="$WINDOWS_DOCUMENTS/maya/$MAYA_VERSION/scripts"
PACKAGE_DST="$SCRIPT_DST_DIR/ad_skin_tools"

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
CURRENT_COMMIT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true)"

echo "Deploying AD Skin Tools..."
echo "Repository:       $REPO"
echo "Git branch:       ${CURRENT_BRANCH:-<unknown>}"
echo "Git commit:       ${CURRENT_COMMIT:-<unknown>}"
echo "Windows profile:  $WINDOWS_PROFILE"
echo "Maya version:     $MAYA_VERSION"
echo "Package from:     $PACKAGE_SRC"
echo "Package to:       $PACKAGE_DST"

if [ ! -d "$PACKAGE_SRC" ]; then
    echo "ERROR: source package not found: $PACKAGE_SRC"
    exit 1
fi

if [ ! -d "$WINDOWS_DOCUMENTS" ]; then
    echo "ERROR: Windows Documents directory is unavailable from WSL:"
    echo "       $WINDOWS_DOCUMENTS"
    echo "Check the profile path or run with an explicit override:"
    echo "       WINDOWS_PROFILE=$WINDOWS_PROFILE ./deploy_to_maya.sh"
    exit 1
fi

required_v42_files=(
    "$PACKAGE_SRC/core/component_selection.py"
    "$PACKAGE_SRC/core/influence_lock.py"
    "$PACKAGE_SRC/core/component_flood.py"
    "$PACKAGE_SRC/ui/component_flood_section.py"
    "$PACKAGE_SRC/ui/joint_list.py"
    "$PACKAGE_SRC/ui/__init__.py"
)

for required_file in "${required_v42_files[@]}"; do
    if [ ! -f "$required_file" ]; then
        echo "ERROR: v4.2 source file is missing: $required_file"
        exit 1
    fi
done

if [ -f "$PACKAGE_SRC/ui/joint_tree_maya2023.py" ]; then
    echo "ERROR: retired duplicate UI module still exists:"
    echo "       $PACKAGE_SRC/ui/joint_tree_maya2023.py"
    exit 1
fi

mkdir -p "$SCRIPT_DST_DIR"
rm -rf "$PACKAGE_DST"
cp -r "$PACKAGE_SRC" "$PACKAGE_DST"

find "$PACKAGE_DST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DST" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

for relative_path in \
    "core/component_selection.py" \
    "core/influence_lock.py" \
    "core/component_flood.py" \
    "ui/component_flood_section.py" \
    "ui/joint_list.py" \
    "ui/__init__.py"; do
    if [ ! -f "$PACKAGE_DST/$relative_path" ]; then
        echo "ERROR: deployment verification failed: $PACKAGE_DST/$relative_path"
        exit 1
    fi
done

if [ -f "$PACKAGE_DST/ui/joint_tree_maya2023.py" ]; then
    echo "ERROR: deployed package contains the retired duplicate UI module."
    exit 1
fi

rm -f \
    "$SCRIPT_DST_DIR/test_v30_distance_ranking.py" \
    "$SCRIPT_DST_DIR/test_v33_ownership_connectivity_probe.py" \
    "$SCRIPT_DST_DIR/test_v34_region_facing_probe.py" \
    "$SCRIPT_DST_DIR/test_v40_install_diagnostic.py" \
    "$SCRIPT_DST_DIR/test_v41_install_diagnostic.py"

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
find "$WINDOWS_DOCUMENTS/maya" \
    -type d \
    -name ad_skin_tools \
    -path '*/scripts/ad_skin_tools' \
    -print 2>/dev/null || true

echo
echo "v4.2 consolidated UI deployment verified."
echo "Diagnostic runner: $SCRIPT_DST_DIR/test_v42_install_diagnostic.py"
echo "Done."
