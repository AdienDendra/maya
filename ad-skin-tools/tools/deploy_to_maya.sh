#!/usr/bin/env bash
set -euo pipefail

REPO="$HOME/dev/dcc/maya/ad-skin-tools"

# Resolve the Windows host account dynamically so the same checkout can deploy
# from different PCs/laptops. The `|| true` is required because this script uses
# `set -euo pipefail`; without it, an unavailable cmd.exe would terminate the
# script before the fallback can run.
WIN_USER="$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r' || true)"

if [ -z "$WIN_USER" ]; then
    echo "Warning: Unable to detect the Windows username. Using fallback 'Arzio'."
    WIN_USER="Arzio"
fi

# Maya 2023 remains the default. A second workstation can override this without
# editing the script, for example:
#
#   MAYA_VERSION=2025 bash tools/deploy_to_maya.sh
#   MAYA_VERSION=2026 bash tools/deploy_to_maya.sh
MAYA_VERSION="${MAYA_VERSION:-2023}"
WINDOWS_MAYA_DIR="/mnt/c/Users/$WIN_USER/Documents/maya"
PACKAGE_SRC="$REPO/ad_skin_tools"
PACKAGE_DST="$WINDOWS_MAYA_DIR/$MAYA_VERSION/scripts/ad_skin_tools"
SCRIPT_DST_DIR="$WINDOWS_MAYA_DIR/$MAYA_VERSION/scripts"

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
CURRENT_COMMIT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true)"


echo "Deploying AD Skin Tools..."
echo "Repository:     $REPO"
echo "Git branch:     ${CURRENT_BRANCH:-<unknown>}"
echo "Git commit:     ${CURRENT_COMMIT:-<unknown>}"
echo "Windows user:   $WIN_USER"
echo "Maya version:   $MAYA_VERSION"
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
    "$PACKAGE_SRC/ui/joint_tree_maya2023.py"
    "$PACKAGE_SRC/ui/__init__.py"
)

for required_file in "${required_v41_files[@]}"; do
    if [ ! -f "$required_file" ]; then
        echo "ERROR: v4.1 source file is missing: $required_file"
        echo "Pull the latest feature/ad-skin-v4-component-flood branch before deploying."
        exit 1
    fi
done

# Verify that the source checkout contains the current UI revision, not merely the
# original v4.1 files. This catches a stale local branch before anything is copied.
grep -Fq '"Select Joints In The List"' \
    "$PACKAGE_SRC/ui/joint_tree_maya2023.py" || {
    echo "ERROR: source UI is stale: Select Joints In The List is missing."
    exit 1
}
grep -Fq 'label="Select Joints In The Scene"' \
    "$PACKAGE_SRC/ui/joint_tree_maya2023.py" || {
    echo "ERROR: source UI is stale: Select Joints In The Scene is missing."
    exit 1
}
grep -Fq '[("Load Mesh",' \
    "$PACKAGE_SRC/ui/joint_tree_maya2023.py" || {
    echo "ERROR: source UI is stale: Load Mesh label is missing."
    exit 1
}
grep -Fq 'class _ToolWindowReloadFinder' \
    "$PACKAGE_SRC/ui/__init__.py" || {
    echo "ERROR: source UI is stale: direct-reload self-healing hook is missing."
    exit 1
}

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
    "ui/component_flood_section.py" \
    "ui/joint_tree_maya2023.py" \
    "ui/__init__.py"; do
    if [ ! -f "$PACKAGE_DST/$relative_path" ]; then
        echo "ERROR: v4.1 deployment verification failed: $PACKAGE_DST/$relative_path"
        exit 1
    fi
done

# Verify the destination content as well. A successful copy must carry the exact
# labels and reload hook expected by the current branch.
grep -Fq '"Select Joints In The List"' \
    "$PACKAGE_DST/ui/joint_tree_maya2023.py" || {
    echo "ERROR: deployed UI verification failed: new list label is missing."
    exit 1
}
grep -Fq 'label="Select Joints In The Scene"' \
    "$PACKAGE_DST/ui/joint_tree_maya2023.py" || {
    echo "ERROR: deployed UI verification failed: scene-selection command is missing."
    exit 1
}
grep -Fq '[("Load Mesh",' \
    "$PACKAGE_DST/ui/joint_tree_maya2023.py" || {
    echo "ERROR: deployed UI verification failed: Load Mesh label is missing."
    exit 1
}
grep -Fq 'class _ToolWindowReloadFinder' \
    "$PACKAGE_DST/ui/__init__.py" || {
    echo "ERROR: deployed UI verification failed: reload hook is missing."
    exit 1
}

cat > "$PACKAGE_DST/.ad_skin_deploy_info" <<EOF
branch=${CURRENT_BRANCH:-unknown}
commit=${CURRENT_COMMIT:-unknown}
windows_user=$WIN_USER
maya_version=$MAYA_VERSION
destination=$PACKAGE_DST
EOF

mkdir -p "$SCRIPT_DST_DIR"

# Remove runners from retired or superseded package versions.
rm -f \
    "$SCRIPT_DST_DIR/test_v30_distance_ranking.py" \
    "$SCRIPT_DST_DIR/test_v33_ownership_connectivity_probe.py" \
    "$SCRIPT_DST_DIR/test_v34_region_facing_probe.py" \
    "$SCRIPT_DST_DIR/test_v40_install_diagnostic.py"

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
find "$WINDOWS_MAYA_DIR" \
    -type d \
    -name ad_skin_tools \
    -path '*/scripts/ad_skin_tools' \
    -print 2>/dev/null || true

echo
echo "v4.1 deployment verified against the current UI revision."
echo "Deploy marker: $PACKAGE_DST/.ad_skin_deploy_info"
echo "Diagnostic runner: $SCRIPT_DST_DIR/test_v41_install_diagnostic.py"
echo "Done."
