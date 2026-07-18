#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
MAYA_VERSION="${MAYA_VERSION:-2023}"
WINDOWS_USERS_ROOT="${WINDOWS_USERS_ROOT:-/mnt/c/Users}"

_detect_windows_documents_via_powershell() {
    local powershell_path=""
    local documents_windows=""
    local documents_wsl=""
    local candidate=""

    for candidate in \
        "$(command -v powershell.exe 2>/dev/null || true)" \
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"; do
        if [ -n "$candidate" ] && [ -f "$candidate" ]; then
            powershell_path="$candidate"
            break
        fi
    done

    if [ -z "$powershell_path" ] || ! command -v wslpath >/dev/null 2>&1; then
        return 0
    fi

    documents_windows="$(
        "$powershell_path" \
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

    if [ -n "$documents_wsl" ] && [ -d "$documents_wsl" ]; then
        printf '%s' "$documents_wsl"
    fi
}

_detect_windows_user_via_cmd() {
    local cmd_path=""
    local candidate=""
    local detected_user=""

    for candidate in \
        "$(command -v cmd.exe 2>/dev/null || true)" \
        "/mnt/c/Windows/System32/cmd.exe"; do
        if [ -n "$candidate" ] && [ -f "$candidate" ]; then
            cmd_path="$candidate"
            break
        fi
    done

    if [ -z "$cmd_path" ]; then
        return 0
    fi

    detected_user="$(
        "$cmd_path" /d /c "echo %USERNAME%" 2>/dev/null \
            | tr -d '\r' \
            | tail -n 1 \
            || true
    )"

    if [ -n "$detected_user" ] && [ "$detected_user" != "%USERNAME%" ]; then
        printf '%s' "$detected_user"
    fi
}

_print_ambiguous_documents_error() {
    local label="$1"
    shift

    echo "ERROR: multiple Windows Documents candidates match $label:" >&2
    local candidate=""
    for candidate in "$@"; do
        echo "       $candidate" >&2
    done
    echo >&2
    echo "Select the directory explicitly, for example:" >&2
    echo "       WINDOWS_DOCUMENTS='/mnt/c/Users/<user>/Documents' ./deploy_to_maya.sh" >&2
}

_scan_windows_documents() {
    if [ ! -d "$WINDOWS_USERS_ROOT" ]; then
        echo "ERROR: Windows user profiles are unavailable from WSL:" >&2
        echo "       $WINDOWS_USERS_ROOT" >&2
        return 1
    fi

    local -a package_candidates=()
    local -a scripts_candidates=()
    local -a maya_candidates=()
    local -a documents_candidates=()
    local -A seen=()

    local user_dir=""
    local user_name=""
    local documents_dir=""

    shopt -s nullglob
    for user_dir in "$WINDOWS_USERS_ROOT"/*; do
        [ -d "$user_dir" ] || continue
        user_name="$(basename "$user_dir")"

        case "$user_name" in
            "All Users"|Default|"Default User"|defaultuser0|Public|WDAGUtilityAccount)
                continue
                ;;
        esac

        for documents_dir in \
            "$user_dir/Documents" \
            "$user_dir"/OneDrive*/Documents; do
            [ -d "$documents_dir" ] || continue

            if [ -n "${seen[$documents_dir]+x}" ]; then
                continue
            fi
            seen["$documents_dir"]=1
            documents_candidates+=("$documents_dir")

            if [ -d "$documents_dir/maya/$MAYA_VERSION/scripts/ad_skin_tools" ]; then
                package_candidates+=("$documents_dir")
            elif [ -d "$documents_dir/maya/$MAYA_VERSION/scripts" ]; then
                scripts_candidates+=("$documents_dir")
            elif [ -d "$documents_dir/maya" ]; then
                maya_candidates+=("$documents_dir")
            fi
        done
    done
    shopt -u nullglob

    local -a selected_pool=()
    local selected_label=""

    if [ "${#package_candidates[@]}" -gt 0 ]; then
        selected_pool=("${package_candidates[@]}")
        selected_label="an existing AD Skin Tool deployment"
    elif [ "${#scripts_candidates[@]}" -gt 0 ]; then
        selected_pool=("${scripts_candidates[@]}")
        selected_label="an existing Maya $MAYA_VERSION scripts directory"
    elif [ "${#maya_candidates[@]}" -gt 0 ]; then
        selected_pool=("${maya_candidates[@]}")
        selected_label="an existing Maya documents directory"
    else
        selected_pool=("${documents_candidates[@]}")
        selected_label="a Windows Documents directory"
    fi

    if [ "${#selected_pool[@]}" -eq 1 ]; then
        printf '%s' "${selected_pool[0]}"
        return 0
    fi

    if [ "${#selected_pool[@]}" -gt 1 ]; then
        _print_ambiguous_documents_error "$selected_label" "${selected_pool[@]}"
        return 1
    fi

    echo "ERROR: no Windows Documents directory was found under:" >&2
    echo "       $WINDOWS_USERS_ROOT" >&2
    echo >&2
    echo "Inspect available profiles with:" >&2
    echo "       find /mnt/c/Users -maxdepth 3 -type d -name Documents -print" >&2
    echo "Then pass the correct path explicitly:" >&2
    echo "       WINDOWS_DOCUMENTS='/mnt/c/Users/<user>/Documents' ./deploy_to_maya.sh" >&2
    return 1
}

_resolve_windows_documents() {
    local resolved="${WINDOWS_DOCUMENTS:-}"
    local detected_user="${WIN_USER:-}"
    local candidate=""

    if [ -n "$resolved" ]; then
        printf '%s' "$resolved"
        return 0
    fi

    resolved="$(_detect_windows_documents_via_powershell)"
    if [ -n "$resolved" ]; then
        printf '%s' "$resolved"
        return 0
    fi

    if [ -z "$detected_user" ]; then
        detected_user="$(_detect_windows_user_via_cmd)"
    fi

    if [ -n "$detected_user" ]; then
        for candidate in \
            "$WINDOWS_USERS_ROOT/$detected_user/Documents" \
            "$WINDOWS_USERS_ROOT/$detected_user"/OneDrive*/Documents; do
            if [ -d "$candidate" ]; then
                printf '%s' "$candidate"
                return 0
            fi
        done
    fi

    _scan_windows_documents
}

if ! WINDOWS_DOCUMENTS="$(_resolve_windows_documents)"; then
    exit 1
fi

if [ ! -d "$WINDOWS_DOCUMENTS" ]; then
    echo "ERROR: resolved Windows Documents directory does not exist:" >&2
    echo "       $WINDOWS_DOCUMENTS" >&2
    exit 1
fi

WIN_USER="${WIN_USER:-}"
if [ -z "$WIN_USER" ] && [[ "$WINDOWS_DOCUMENTS" == "$WINDOWS_USERS_ROOT"/* ]]; then
    relative_profile="${WINDOWS_DOCUMENTS#"$WINDOWS_USERS_ROOT"/}"
    WIN_USER="${relative_profile%%/*}"
fi
WIN_USER="${WIN_USER:-<resolved from Documents path>}"

WINDOWS_MAYA_DIR="$WINDOWS_DOCUMENTS/maya"
PACKAGE_SRC="$REPO/ad_skin_tools"
SCRIPT_DST_DIR="$WINDOWS_MAYA_DIR/$MAYA_VERSION/scripts"
PACKAGE_DST="$SCRIPT_DST_DIR/ad_skin_tools"

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
CURRENT_COMMIT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true)"

echo "Deploying AD Skin Tools..."
echo "Repository:        $REPO"
echo "Git branch:        ${CURRENT_BRANCH:-<unknown>}"
echo "Git commit:        ${CURRENT_COMMIT:-<unknown>}"
echo "Windows user:      $WIN_USER"
echo "Windows Documents: $WINDOWS_DOCUMENTS"
echo "Maya version:      $MAYA_VERSION"
echo "Package from:      $PACKAGE_SRC"
echo "Package to:        $PACKAGE_DST"

if [ ! -d "$PACKAGE_SRC" ]; then
    echo "ERROR: source package not found: $PACKAGE_SRC"
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
    --no-owner \
    --no-group \
    --no-perms \
    --omit-dir-times \
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
echo "Other ad_skin_tools copies under the resolved Maya documents directory:"
find "$WINDOWS_MAYA_DIR" \
    -type d \
    -name ad_skin_tools \
    -path '*/scripts/ad_skin_tools' \
    -print 2>/dev/null || true

echo
echo "v4.2 consolidated UI deployment verified."
echo "Diagnostic runner: $SCRIPT_DST_DIR/test_v42_install_diagnostic.py"
echo "Done."
