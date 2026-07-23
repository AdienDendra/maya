from __future__ import annotations

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = ROOT / "ad-skin-tools"
PACKAGE_ROOT = TOOLS_ROOT / "ad_skin_tools"


def _node_bounds(node: ast.AST) -> tuple[int, int]:
    start = int(getattr(node, "lineno"))
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        start = min(start, *(int(item.lineno) for item in decorators))
    return start, int(getattr(node, "end_lineno"))


def _extract_definition(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name:
            start, end = _node_bounds(node)
            return "".join(source.splitlines(keepends=True)[start - 1:end]).rstrip()
    raise RuntimeError("Definition not found: {}".format(name))


def _replace_function(source: str, name: str, replacement: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start, end = _node_bounds(node)
            lines = source.splitlines(keepends=True)
            replacement_lines = (replacement.rstrip() + "\n").splitlines(keepends=True)
            lines[start - 1:end] = replacement_lines
            return "".join(lines)
    raise RuntimeError("Function not found: {}".format(name))


def _remove_functions(source: str, names: set[str]) -> str:
    tree = ast.parse(source)
    ranges = []
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            ranges.append(_node_bounds(node))
            found.add(node.name)
    missing = names - found
    if missing:
        raise RuntimeError("Functions not found for cleanup: {}".format(sorted(missing)))
    lines = source.splitlines(keepends=True)
    for start, end in sorted(ranges, reverse=True):
        del lines[start - 1:end]
    return "".join(lines)


def _promote_component_smooth() -> None:
    production_path = PACKAGE_ROOT / "components" / "smooth.py"
    candidate_path = PACKAGE_ROOT / "components" / "smooth_shared.py"
    baseline = production_path.read_text(encoding="utf-8")
    candidate = candidate_path.read_text(encoding="utf-8")

    scope_class = _extract_definition(baseline, "ComponentSmoothScope")
    collect_scope = _extract_definition(baseline, "collect_smooth_scope")
    object_selected = _extract_definition(baseline, "_loaded_mesh_object_selected")

    candidate = candidate.replace(
        '"""Candidate Component Smooth using the shared bind diffusion kernel."""',
        '"""Production Component Smooth using the shared bind diffusion kernel."""',
        1,
    )
    candidate = candidate.replace(
        "from ad_skin_tools.components import smooth as baseline_smooth\n",
        "from ad_skin_tools.components.selection import collect_weighted_mesh_vertices\n"
        "from ad_skin_tools.core.component_selection import collect_selected_mesh_vertices\n",
        1,
    )

    start_marker = "MINIMUM_COMPONENT_BLEND = baseline_smooth.MINIMUM_COMPONENT_BLEND"
    end_marker = "collect_smooth_scope = baseline_smooth.collect_smooth_scope"
    start = candidate.index(start_marker)
    end = candidate.index(end_marker) + len(end_marker)
    definitions = """MINIMUM_COMPONENT_BLEND = 0.0
MAXIMUM_COMPONENT_BLEND = 1.0
DEFAULT_COMPONENT_BLEND = 0.25
MINIMUM_COMPONENT_ITERATIONS = 1
MAXIMUM_COMPONENT_ITERATIONS = 10
DEFAULT_COMPONENT_ITERATIONS = 1

MINIMUM_COMPONENT_PASSES = MINIMUM_COMPONENT_ITERATIONS
MAXIMUM_COMPONENT_PASSES = MAXIMUM_COMPONENT_ITERATIONS
DEFAULT_COMPONENT_PASSES = DEFAULT_COMPONENT_ITERATIONS


{scope_class}


{collect_scope}


{object_selected}""".format(
        scope_class=scope_class,
        collect_scope=collect_scope,
        object_selected=object_selected,
    )
    candidate = candidate[:start] + definitions + candidate[end:]

    candidate = _remove_functions(
        candidate,
        {"_smooth_selected_rows", "_smooth_context_rows"},
    )
    candidate = _replace_function(
        candidate,
        "print_component_smooth_report",
        '''def print_component_smooth_report(result: ComponentSmoothResult) -> None:
    print("\\n[AD Skin Tool - Component Smooth]")
    print("Mesh:", result.mesh_transform)
    print("Whole object:", result.whole_object)
    print("Soft Selection enabled:", result.soft_selection_enabled)
    print("Selected vertices:", result.selected_vertex_count)
    print("Changed vertices:", result.smoothed_vertex_count)
    print("Blend:", result.blend)
    print("Iterations:", result.iterations)
    print("Locked influences:", len(result.locked_influences))
    print("Elapsed: {:.6f} s".format(result.elapsed_seconds))''',
    )

    ast.parse(candidate, filename=str(production_path))
    production_path.write_text(candidate, encoding="utf-8")
    candidate_path.unlink()

    component_init = PACKAGE_ROOT / "components" / "__init__.py"
    text = component_init.read_text(encoding="utf-8")
    text = re.sub(r'^\s*"smooth_shared",\s*\n', "", text, flags=re.MULTILINE)
    component_init.write_text(text, encoding="utf-8")


def _clean_launcher() -> None:
    path = PACKAGE_ROOT / "launch.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r'^\s*import ad_skin_tools\.components\.smooth_shared as component_smooth_shared\s*\n',
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r'^\s*component_smooth_shared,\s*\n',
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r'^\s*from ad_skin_tools\.components import smooth_shared as component_smooth_shared\s*\n',
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r'^\s*component_section\.smooth = component_smooth_shared\s*\n',
        "",
        text,
        flags=re.MULTILINE,
    )
    path.write_text(text, encoding="utf-8")


def _replace_region_namespace() -> None:
    for path in TOOLS_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace("ad_skin_tools.region_research", "ad_skin_tools.region")
        updated = updated.replace("region_research/", "region/")
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def _update_production_reports() -> None:
    bind_path = PACKAGE_ROOT / "core" / "smoothed_automatic_bind.py"
    bind_source = bind_path.read_text(encoding="utf-8")
    bind_source = _replace_function(
        bind_source,
        "print_automatic_surface_report",
        '''def print_automatic_surface_report(result: AutomaticSurfaceBindResult) -> None:
    global_owner = result.global_owner_joint or "<none>"
    print("\\n[AD Skin Tool - Bind Skin]")
    print("Mesh:", result.mesh_transform)
    print("Global Owner:", global_owner)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", result.smoothing_mixed_vertex_count)
    print("Elapsed: {:.6f} s".format(result.production_elapsed_seconds))''',
    )
    ast.parse(bind_source, filename=str(bind_path))
    bind_path.write_text(bind_source, encoding="utf-8")

    add_path = PACKAGE_ROOT / "core" / "add_influence.py"
    add_source = add_path.read_text(encoding="utf-8")
    add_source = _replace_function(
        add_source,
        "print_report",
        '''def print_report(result: AddInfluenceResult) -> None:
    global_owner = (
        result.ownership_pipeline.global_owner_assignment.global_owner_joint
        or "<none>"
    )
    influence_count = len(result.existing_influences) + len(result.target_joints)
    mixed_vertex_count = (
        result.smoothing_result.diffusion_result.mixed_vertex_count
        if result.smoothing_result is not None
        else 0
    )
    print("\\n[AD Skin Tool - Add Influence]")
    print("Mesh:", result.mesh_transform)
    print("Global Owner:", global_owner)
    print("Vertices:", result.ownership_pipeline.vertex_count)
    print("Influences:", influence_count)
    print("New influences:", len(result.target_joints))
    print("Claimed vertices:", result.claimed_vertex_count)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", mixed_vertex_count)
    print("Locked influences:", len(result.locked_influences))
    print("Elapsed: {:.6f} s".format(result.production_elapsed_seconds))''',
    )
    ast.parse(add_source, filename=str(add_path))
    add_path.write_text(add_source, encoding="utf-8")


def _harden_source_validator() -> None:
    path = TOOLS_ROOT / "tools" / "validate_source.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('    "region",\n', '')
    forbidden_marker = "FORBIDDEN_PATHS = (\n"
    additions = (
        '    "region_research",\n'
        '    "development",\n'
        '    "components/smooth_shared.py",\n'
        '    "components/smooth_optimizer_smoke.py",\n'
    )
    if additions not in text:
        text = text.replace(forbidden_marker, forbidden_marker + additions, 1)
    path.write_text(text, encoding="utf-8")


def _validate_cleanup_state() -> None:
    forbidden_paths = (
        PACKAGE_ROOT / "region_research",
        PACKAGE_ROOT / "development",
        PACKAGE_ROOT / "components" / "smooth_shared.py",
        PACKAGE_ROOT / "components" / "smooth_optimizer_smoke.py",
    )
    remaining = [str(path.relative_to(ROOT)) for path in forbidden_paths if path.exists()]
    if remaining:
        raise RuntimeError("Cleanup left forbidden paths: {}".format(remaining))

    stale_tokens = ("region_research", "smooth_shared", "smooth_optimizer_smoke")
    stale_hits = []
    for path in TOOLS_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".sh"}:
            continue
        if path == TOOLS_ROOT / "tools" / "validate_source.py":
            continue
        text = path.read_text(encoding="utf-8")
        for token in stale_tokens:
            if token in text:
                stale_hits.append("{}: {}".format(path.relative_to(ROOT), token))
    if stale_hits:
        raise RuntimeError("Stale production references remain: {}".format(stale_hits))

    for path in PACKAGE_ROOT.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def main() -> None:
    _replace_region_namespace()
    _promote_component_smooth()
    _clean_launcher()
    _update_production_reports()
    _harden_source_validator()
    _validate_cleanup_state()

    for temporary in (
        ROOT / ".github" / "production_cleanup.py",
        ROOT / ".github" / "workflows" / "production_cleanup.yml",
    ):
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
