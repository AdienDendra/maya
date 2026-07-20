#!/usr/bin/env python3
"""Validate the AD Skin Tools source tree without importing Maya."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "ad_skin_tools"
VERSIONED_FILENAME = re.compile(r"(?:^|_)v\d+(?:_|\.|$)", re.IGNORECASE)
VERSION_LABEL = re.compile(r"\bv\d+(?:\.\d+)*\b", re.IGNORECASE)

REQUIRED_PATHS = (
    "launch.py",
    "components/selection.py",
    "components/flood.py",
    "components/smooth.py",
    "core/undoable_skin_weights.py",
    "ui/component_section.py",
    "ui/joint_list.py",
    "ui/skin_operations.py",
    "ui/smoothing_bind_section.py",
    "ui/tool_window.py",
)

FORBIDDEN_PATHS = (
    "components/flood_v81.py",
    "components/smooth_undo.py",
    "components/undoable_weights.py",
    "core/component_flood.py",
    "ui/component_flood_section.py",
    "ui/component_flood_v80.py",
    "ui/component_smooth_v81.py",
)


def main() -> int:
    errors: list[str] = []

    if not PACKAGE_ROOT.is_dir():
        print("ERROR: package directory is missing: {}".format(PACKAGE_ROOT))
        return 1

    _validate_required_paths(errors)
    _validate_forbidden_paths(errors)

    python_files = sorted(PACKAGE_ROOT.rglob("*.py"))
    for path in python_files:
        relative = path.relative_to(PACKAGE_ROOT)
        source = path.read_text(encoding="utf-8")

        if VERSIONED_FILENAME.search(path.name):
            errors.append("versioned runtime filename: {}".format(relative))

        for line_number, line in enumerate(source.splitlines(), start=1):
            match = VERSION_LABEL.search(line)
            if match:
                errors.append(
                    "historical version label {}:{}: {}".format(
                        relative,
                        line_number,
                        match.group(0),
                    )
                )

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            errors.append(
                "syntax error {}:{}:{}: {}".format(
                    relative,
                    exc.lineno,
                    exc.offset,
                    exc.msg,
                )
            )
            continue

        _validate_internal_imports(tree, relative, errors)

    if errors:
        print("AD Skin Tools source validation failed:\n")
        for error in errors:
            print("- {}".format(error))
        return 1

    print(
        "AD Skin Tools source validation passed: {} Python files checked.".format(
            len(python_files)
        )
    )
    return 0


def _validate_required_paths(errors: list[str]) -> None:
    for relative in REQUIRED_PATHS:
        if not (PACKAGE_ROOT / relative).is_file():
            errors.append("required runtime file is missing: {}".format(relative))


def _validate_forbidden_paths(errors: list[str]) -> None:
    for relative in FORBIDDEN_PATHS:
        if (PACKAGE_ROOT / relative).exists():
            errors.append("superseded runtime file still exists: {}".format(relative))


def _validate_internal_imports(
    tree: ast.AST,
    source_path: Path,
    errors: list[str],
) -> None:
    for node in ast.walk(tree):
        module_names: Iterable[str]

        if isinstance(node, ast.Import):
            module_names = (
                alias.name
                for alias in node.names
                if alias.name.startswith("ad_skin_tools")
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            module_names = (
                (node.module,)
                if node.module.startswith("ad_skin_tools")
                else ()
            )
        else:
            continue

        for module_name in module_names:
            if not _internal_module_exists(module_name):
                errors.append(
                    "missing internal import {}:{}: {}".format(
                        source_path,
                        getattr(node, "lineno", "?"),
                        module_name,
                    )
                )


def _internal_module_exists(module_name: str) -> bool:
    parts = module_name.split(".")
    if not parts or parts[0] != "ad_skin_tools":
        return True

    relative_parts = parts[1:]
    if not relative_parts:
        return (PACKAGE_ROOT / "__init__.py").is_file()

    module_path = PACKAGE_ROOT.joinpath(*relative_parts)
    return (
        module_path.with_suffix(".py").is_file()
        or (module_path / "__init__.py").is_file()
    )


if __name__ == "__main__":
    sys.exit(main())
