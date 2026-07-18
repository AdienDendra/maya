#!/usr/bin/env bash
set -euo pipefail

REPO="$HOME/dev/dcc/maya/ad-skin-tools"
MAYA_VERSION="${MAYA_VERSION:-2023}"

_detect_windows_documents() {
    local documents_windows=""
    local documents_wsl=""

    if command -v powershell.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
        documents_windows="$(
            powershell.exe \
                -NoLogo \
                -NoProfile \
                -NonInteractive \
                -Command "[Environment]::GetFolderPath('MyDocuments')" \
                2>/dev/null \
                | tr -d '\r' \
                | tail -n 1 \
                || true
        )"

        if [ -n "$documents_windows" ]; then
            documents_wsl="$(wslpath -u "$documents_windows" 2>/dev/null || true)"
        fi
    fi

    printf '%s' "$documents_wsl"
}

_detect_windows_user() {
    local detected_user=""

    if command -v cmd.exe >/dev/null 2>&1; then
        detected_user="$(
            cmd.exe /d /c "echo %USERNAME%" 2>/dev/null \
                | tr -d '\r' \
                | tail -n 1 \
                || true
        )"
    fi

    printf '%s' "$detected_user"
}

WINDOWS_DOCUMENTS="${WINDOWS_DOCUMENTS:-$(_detect_windows_documents)}"
WIN_USER="${WIN_USER:-$(_detect_windows_user)}"

if [ -z "$WINDOWS_DOCUMENTS" ]; then
    if [ -z "$WIN_USER" ]; then
        WIN_USER="Arzio"
        echo "Warning: Unable to detect the Windows user or Documents directory."
        echo "Using fallback Windows user: $WIN_USER"
    fi
    WINDOWS_DOCUMENTS="/mnt/c/Users/$WIN_USER/Documents"
fi

if [ -z "$WIN_USER" ]; then
    WIN_USER="<resolved from Documents path>"
fi

WINDOWS_MAYA_DIR="$WINDOWS_DOCUMENTS/maya"
PACKAGE_SRC="$REPO/ad_skin_tools"
SCRIPT_DST_DIR="$WINDOWS_MAYA_DIR/$MAYA_VERSION/scripts"
PACKAGE_DST="$SCRIPT_DST_DIR/ad_skin_tools"

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
CURRENT_COMMIT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true)"

echo "Deploying AD Skin Tools..."
echo "Repository:       $REPO"
echo "Git branch:       ${CURRENT_BRANCH:-<unknown>}"
echo "Git commit:       ${CURRENT_COMMIT:-<unknown>}"
echo "Windows user:     $WIN_USER"
echo "Windows Documents:$WINDOWS_DOCUMENTS"
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
    echo "Set WINDOWS_DOCUMENTS explicitly, for example:"
    echo "       WINDOWS_DOCUMENTS=/mnt/c/Users/Arzio/Documents ./deploy_to_maya.sh"
    exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
    echo "ERROR: rsync is required for safe WSL-to-Windows deployment."
    echo "Install it with: sudo apt update && sudo apt install rsync"
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

mkdir -p "$SCRIPT_DST_DIR" "$PACKAGE_DST"

WRITE_TEST="$SCRIPT_DST_DIR/.ad_skin_deploy_write_test"
if ! printf 'write-test\n' > "$WRITE_TEST"; then
    echo "ERROR: WSL cannot write to the Maya scripts directory:"
    echo "       $SCRIPT_DST_DIR"
    echo "Close Maya and Windows applications using this folder, then retry."
    exit 1
fi
rm -f "$WRITE_TEST"

echo "Synchronising package in place..."
if ! rsync \
    --archive \
    --delete \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    "$PACKAGE_SRC/" \
    "$PACKAGE_DST/"; then
    echo
    echo "ERROR: package synchronisation failed on the Windows-mounted filesystem."
    echo "The destination path is valid, but Windows or WSL may be holding a file lock."
    echo "Close Maya, Script Editor tabs, Explorer windows, and any editor opened on:"
    echo "       $PACKAGE_DST"
    echo "Then retry. If the error remains, run 'wsl.exe --shutdown' from PowerShell,"
    echo "reopen WSL, and deploy again."
    exit 1
fi

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
find "$WINDOWS_MAYA_DIR" \
    -type d \
    -name ad_skin_tools \
    -path '*/scripts/ad_skin_tools' \
    -print 2>/dev/null || true

echo
echo "v4.2 consolidated UI deployment verified."
echo "Diagnostic runner: $SCRIPT_DST_DIR/test_v42_install_diagnostic.py"
echo "Done."
