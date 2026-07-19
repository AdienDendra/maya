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

ADD_INFLUENCE_DIAGNOSTIC_SRC="$REPO/scripts/test_add_influence.py"
ADD_INFLUENCE_DIAGNOSTIC_DST="$SCRIPT_DST_DIR/test_add_influence.py"

V60_DIFFUSION_DIAGNOSTIC_SRC="$REPO/scripts/test_v60_bind_smoothing_diffusion.py"
V60_DIFFUSION_DIAGNOSTIC_DST="$SCRIPT_DST_DIR/test_v60_bind_smoothing_diffusion.py"

V310_BOUNDARY_DIAGNOSTIC_SRC="$REPO/scripts/test_region_boundary_coherence.py"
V310_BOUNDARY_DIAGNOSTIC_DST="$SCRIPT_DST_DIR/test_region_boundary_coherence.py"

V310B_BOUNDARY_RING_DIAGNOSTIC_SRC="$REPO/scripts/test_region_boundary_ring_coherence.py"
V310B_BOUNDARY_RING_DIAGNOSTIC_DST="$SCRIPT_DST_DIR/test_region_boundary_ring_coherence.py"

V310C_COLOR_FEEDBACK_SRC="$REPO/scripts/test_region_hard_bind_color_feedback.py"
V310C_COLOR_FEEDBACK_DST="$SCRIPT_DST_DIR/test_region_hard_bind_color_feedback.py"

V310D_CLOSED_LOOP_BIND_SRC="$REPO/scripts/test_region_closed_loop_consensus_bind.py"
V310D_CLOSED_LOOP_BIND_DST="$SCRIPT_DST_DIR/test_region_closed_loop_consensus_bind.py"

V310E_LOCAL_RUN_BIND_SRC="$REPO/scripts/test_region_local_closed_loop_runs_bind.py"
V310E_LOCAL_RUN_BIND_DST="$SCRIPT_DST_DIR/test_region_local_closed_loop_runs_bind.py"

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
    exit 1
fi

required_files=(
    "$PACKAGE_SRC/bind_smoothing/__init__.py"
    "$PACKAGE_SRC/bind_smoothing/diffusion.py"
    "$PACKAGE_SRC/core/add_influence.py"
    "$PACKAGE_SRC/core/automatic_surface_commands.py"
    "$PACKAGE_SRC/core/component_flood.py"
    "$PACKAGE_SRC/core/component_selection.py"
    "$PACKAGE_SRC/core/influence_lock.py"
    "$PACKAGE_SRC/core/joint_automatic_bind.py"
    "$PACKAGE_SRC/core/skin_cluster.py"
    "$PACKAGE_SRC/region/boundary_coherence.py"
    "$PACKAGE_SRC/region/boundary_ring_coherence.py"
    "$PACKAGE_SRC/region/closed_loop_consensus.py"
    "$PACKAGE_SRC/region/local_closed_loop_runs.py"
    "$PACKAGE_SRC/region/solver.py"
    "$PACKAGE_SRC/ui/component_flood_section.py"
    "$PACKAGE_SRC/ui/joint_list.py"
    "$PACKAGE_SRC/ui/skin_operations.py"
    "$PACKAGE_SRC/ui/tool_window.py"
)

for required_file in "${required_files[@]}"; do
    if [ ! -f "$required_file" ]; then
        echo "ERROR: active source file is missing: $required_file"
        exit 1
    fi
done

if [ ! -f "$ADD_INFLUENCE_DIAGNOSTIC_SRC" ]; then
    echo "ERROR: Add Influence diagnostic is missing:"
    echo "       $ADD_INFLUENCE_DIAGNOSTIC_SRC"
    exit 1
fi

if [ ! -f "$V60_DIFFUSION_DIAGNOSTIC_SRC" ]; then
    echo "ERROR: v6.0 Bind Smoothing diagnostic is missing:"
    echo "       $V60_DIFFUSION_DIAGNOSTIC_SRC"
    exit 1
fi

if [ ! -f "$V310_BOUNDARY_DIAGNOSTIC_SRC" ]; then
    echo "ERROR: v3.10 Region boundary diagnostic is missing:"
    echo "       $V310_BOUNDARY_DIAGNOSTIC_SRC"
    exit 1
fi

if [ ! -f "$V310B_BOUNDARY_RING_DIAGNOSTIC_SRC" ]; then
    echo "ERROR: v3.10B Region boundary-ring diagnostic is missing:"
    echo "       $V310B_BOUNDARY_RING_DIAGNOSTIC_SRC"
    exit 1
fi

if [ ! -f "$V310C_COLOR_FEEDBACK_SRC" ]; then
    echo "ERROR: v3.10C Region color-feedback diagnostic is missing:"
    echo "       $V310C_COLOR_FEEDBACK_SRC"
    exit 1
fi

if [ ! -f "$V310D_CLOSED_LOOP_BIND_SRC" ]; then
    echo "ERROR: v3.10D closed-loop consensus bind is missing:"
    echo "       $V310D_CLOSED_LOOP_BIND_SRC"
    exit 1
fi

if [ ! -f "$V310E_LOCAL_RUN_BIND_SRC" ]; then
    echo "ERROR: v3.10E local-run consensus bind is missing:"
    echo "       $V310E_LOCAL_RUN_BIND_SRC"
    exit 1
fi

mkdir -p "$SCRIPT_DST_DIR"
rm -rf "$PACKAGE_DST"
cp -r "$PACKAGE_SRC" "$PACKAGE_DST"

find "$PACKAGE_DST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DST" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

rm -f \
    "$SCRIPT_DST_DIR/test_v30_distance_ranking.py" \
    "$SCRIPT_DST_DIR/test_v33_ownership_connectivity_probe.py" \
    "$SCRIPT_DST_DIR/test_v34_region_facing_probe.py" \
    "$SCRIPT_DST_DIR/test_v40_install_diagnostic.py" \
    "$SCRIPT_DST_DIR/test_v41_install_diagnostic.py" \
    "$SCRIPT_DST_DIR/test_v42_install_diagnostic.py" \
    "$SCRIPT_DST_DIR/test_v50_object_region_add.py" \
    "$SCRIPT_DST_DIR/test_v50_object_region_rebind.py" \
    "$V60_DIFFUSION_DIAGNOSTIC_DST" \
    "$V310_BOUNDARY_DIAGNOSTIC_DST" \
    "$V310B_BOUNDARY_RING_DIAGNOSTIC_DST" \
    "$V310C_COLOR_FEEDBACK_DST" \
    "$V310D_CLOSED_LOOP_BIND_DST" \
    "$V310E_LOCAL_RUN_BIND_DST"

cp "$ADD_INFLUENCE_DIAGNOSTIC_SRC" "$ADD_INFLUENCE_DIAGNOSTIC_DST"
cp "$V60_DIFFUSION_DIAGNOSTIC_SRC" "$V60_DIFFUSION_DIAGNOSTIC_DST"
cp "$V310_BOUNDARY_DIAGNOSTIC_SRC" "$V310_BOUNDARY_DIAGNOSTIC_DST"
cp "$V310B_BOUNDARY_RING_DIAGNOSTIC_SRC" "$V310B_BOUNDARY_RING_DIAGNOSTIC_DST"
cp "$V310C_COLOR_FEEDBACK_SRC" "$V310C_COLOR_FEEDBACK_DST"
cp "$V310D_CLOSED_LOOP_BIND_SRC" "$V310D_CLOSED_LOOP_BIND_DST"
cp "$V310E_LOCAL_RUN_BIND_SRC" "$V310E_LOCAL_RUN_BIND_DST"

echo
echo "Other ad_skin_tools copies under the Maya documents directory:"
find "$WINDOWS_DOCUMENTS/maya" \
    -type d \
    -name ad_skin_tools \
    -path '*/scripts/ad_skin_tools' \
    -print 2>/dev/null || true

echo
echo "Active AD Skin Tools package deployment verified."
echo "Diagnostic runner: $ADD_INFLUENCE_DIAGNOSTIC_DST"
echo "v6.0 smoke runner: $V60_DIFFUSION_DIAGNOSTIC_DST"
echo "v3.10 Region smoke runner: $V310_BOUNDARY_DIAGNOSTIC_DST"
echo "v3.10B Region ring runner: $V310B_BOUNDARY_RING_DIAGNOSTIC_DST"
echo "v3.10C Region color runner: $V310C_COLOR_FEEDBACK_DST"
echo "v3.10D closed-loop bind runner: $V310D_CLOSED_LOOP_BIND_DST"
echo "v3.10E local-run bind runner: $V310E_LOCAL_RUN_BIND_DST"
echo "Done."
