#!/usr/bin/env python3
"""Validate the AD Skin Tools source tree without importing Maya."""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "ad_skin_tools"
VERSIONED_FILENAME = re.compile(r"(?:^|_)v\d+(?:_|\.|$)", re.IGNORECASE)
VERSION_LABEL = re.compile(r"\bv\d+(?:\.\d+)*\b", re.IGNORECASE)

REQUIRED_PATHS = (
    "launch.py",
    "components/selection.py",
    "components/flood.py",
    "components/smooth.py",
    "core/add_influence.py",
    "core/smoothed_automatic_bind.py",
    "core/undoable_skin_weights.py",
    "region/ownership_pipeline.py",
    "ui/component_section.py",
    "ui/joint_list.py",
    "ui/skin_operations.py",
    "ui/smoothing_bind_section.py",
    "ui/tool_window.py",
)

FORBIDDEN_PATHS = (
    "region_research",
    "development",
    "components/smooth_shared.py",
    "components/smooth_optimizer_smoke.py",
    "components/flood_blend.py",
    "components/flood_v81.py",
    "components/smooth_undo.py",
    "components/undoable_weights.py",
    "core/component_flood.py",
    "core/joint_automatic_bind.py",
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
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("ad_skin_tools"):
                    continue
                if not _internal_module_exists(alias.name):
                    _append_missing_import(
                        errors,
                        source_path,
                        node,
                        alias.name,
                    )
            continue

        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level or not node.module:
            continue
        if not node.module.startswith("ad_skin_tools"):
            continue

        module_path = _internal_module_path(node.module)
        if module_path is None:
            _append_missing_import(
                errors,
                source_path,
                node,
                node.module,
            )
            continue

        if not module_path.is_dir():
            continue

        package_symbols = _package_symbols(module_path)
        for alias in node.names:
            if alias.name == "*":
                continue
            candidate_name = "{}.{}".format(node.module, alias.name)
            if _internal_module_exists(candidate_name):
                continue
            if alias.name in package_symbols:
                continue
            _append_missing_import(
                errors,
                source_path,
                node,
                candidate_name,
            )


def _append_missing_import(
    errors: list[str],
    source_path: Path,
    node: ast.AST,
    module_name: str,
) -> None:
    errors.append(
        "missing internal import {}:{}: {}".format(
            source_path,
            getattr(node, "lineno", "?"),
            module_name,
        )
    )


def _internal_module_exists(module_name: str) -> bool:
    return _internal_module_path(module_name) is not None


def _internal_module_path(module_name: str) -> Path | None:
    parts = module_name.split(".")
    if not parts or parts[0] != "ad_skin_tools":
        return PACKAGE_ROOT

    relative_parts = parts[1:]
    if not relative_parts:
        return PACKAGE_ROOT if (PACKAGE_ROOT / "__init__.py").is_file() else None

    module_path = PACKAGE_ROOT.joinpath(*relative_parts)
    file_path = module_path.with_suffix(".py")
    if file_path.is_file():
        return file_path
    if (module_path / "__init__.py").is_file():
        return module_path
    return None


@lru_cache(maxsize=None)
def _package_symbols(package_path: Path) -> frozenset[str]:
    init_path = package_path / "__init__.py"
    if not init_path.is_file():
        return frozenset()

    try:
        tree = ast.parse(
            init_path.read_text(encoding="utf-8"),
            filename=str(init_path),
        )
    except SyntaxError:
        return frozenset()

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)

    return frozenset(names)


if __name__ == "__main__":
    sys.exit(main())
