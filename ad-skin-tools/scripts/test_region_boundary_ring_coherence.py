"""Read-only v3.10B smoke for Region ownership boundary edge rings.

This runner keeps the original v3.10 edge-loop diagnostic intact. It runs the
production Region solver, ignores connected owner regions that touch more than
one neighbouring owner, and uses Maya ``polySelect(edgeRing=...)`` for the
remaining single-neighbour regions.
"""

import builtins
import importlib

from ad_skin_tools.region import boundary_ring_coherence
from ad_skin_tools.region import connectivity
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


SELECT_SUSPICIOUS_EDGES = True
PRINT_DIAGNOSTIC_LIMIT = 40


for module in (
    boundary_ring_coherence,
    connectivity,
    region_solver,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10B Region smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError(
            "Add at least two joints to the AD Skin Tool list."
        )
    return state["mesh_transform"], joints


def _count(result, classification):
    return sum(
        diagnostic.classification == classification
        for diagnostic in result.diagnostics
    )


def _short_name(path):
    return path.split("|")[-1]


def _print_diagnostic(index, diagnostic):
    neighbours = ", ".join(
        _short_name(path) for path in diagnostic.neighbour_joints
    )
    print(
        "\nDiagnostic {} | {} region {}".format(
            index,
            _short_name(diagnostic.source_joint),
            diagnostic.source_region_index,
        )
    )
    print("  classification:", diagnostic.classification)
    print("  source region vertices:", len(diagnostic.source_region_vertex_ids))
    print("  neighbouring owners:", diagnostic.neighbour_count)
    print("  neighbours:", neighbours)
    print("  ownership-crossing edges:", len(diagnostic.boundary_edge_ids))
    print("  Maya edge rings:", diagnostic.maya_ring_count)

    if diagnostic.unresolved_seed_edge_ids:
        print(
            "  unresolved seed edges:",
            list(diagnostic.unresolved_seed_edge_ids),
        )

    for ring_index, ring in enumerate(diagnostic.maya_rings):
        print(
            "    ring {} | seed={} | Maya edges={} | boundary overlap={}".format(
                ring_index,
                ring.seed_edge_id,
                len(ring.edge_ids),
                ring.boundary_overlap_count,
            )
        )


def run():
    mesh, joints = _loaded_unskinned_context()
    region_result = region_solver.solve_region_ownership(
        mesh=mesh,
        joints=joints,
    )
    ring_result = boundary_ring_coherence.analyze_region_boundary_rings(
        region_result
    )

    print("\n[AD Skin Tool v3.10B - Region Boundary Ring Smoke]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Connected Region diagnostics:", len(ring_result.diagnostics))
    print(
        "Junction regions ignored:",
        _count(ring_result, boundary_ring_coherence.JUNCTION_IGNORED),
    )
    print(
        "Single Maya edge ring:",
        _count(ring_result, boundary_ring_coherence.SINGLE_RING),
    )
    print(
        "Multiple Maya edge rings (suspicious):",
        _count(ring_result, boundary_ring_coherence.MULTIPLE_RINGS),
    )
    print(
        "Unresolved Maya ring traversal:",
        _count(ring_result, boundary_ring_coherence.UNRESOLVED),
    )

    review_indices = list(ring_result.suspicious_diagnostic_indices)
    review_indices.extend(ring_result.unresolved_diagnostic_indices)

    print("\nDiagnostics requiring viewport review:")
    if not review_indices:
        print("  None")
    for index in review_indices[:PRINT_DIAGNOSTIC_LIMIT]:
        _print_diagnostic(index, ring_result.diagnostics[int(index)])

    builtins.AD_SKIN_V310B_REGION_RESULT = region_result
    builtins.AD_SKIN_V310B_BOUNDARY_RING_RESULT = ring_result

    if SELECT_SUSPICIOUS_EDGES:
        boundary_ring_coherence.select_boundary_ring_diagnostics(
            ring_result,
            category="suspicious",
        )
        print(
            "\nSelected suspicious ownership-crossing edges:",
            ring_result.suspicious_diagnostic_count,
            "diagnostics",
        )

    print("No Region ownership was changed. No skinCluster was created.")
    print(
        "Saved result as "
        "builtins.AD_SKIN_V310B_BOUNDARY_RING_RESULT"
    )


if __name__ == "__main__":
    run()
