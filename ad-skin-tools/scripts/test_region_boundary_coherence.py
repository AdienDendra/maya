"""Read-only v3.10 smoke test for Region ownership-boundary coherence.

Workflow:
    1. Open AD Skin Tool.
    2. Load one unskinned polygon mesh.
    3. Add the intended joints to the UI list.
    4. Run this file from Maya's Script Editor.

The test runs the existing production Region solver, asks Maya ``polySelect``
to traverse owner-boundary edge loops, and selects suspicious contacts. It does
not change owner assignments or create a skinCluster.
"""

import builtins
import importlib

from ad_skin_tools.region import boundary_coherence
from ad_skin_tools.region import connectivity
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


SELECT_SUSPICIOUS_EDGES = True
PRINT_CONTACT_LIMIT = 30


for module in (
    boundary_coherence,
    connectivity,
    region_solver,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10 Region smoke test."
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
        contact.classification == classification
        for contact in result.contacts
    )


def _short_name(path):
    return path.split("|")[-1]


def _print_contact(index, contact):
    print(
        "\nContact {} | {} region {} <-> {}".format(
            index,
            _short_name(contact.source_joint),
            contact.source_region_index,
            _short_name(contact.neighbour_joint),
        )
    )
    print("  classification:", contact.classification)
    print("  source region vertices:", len(contact.source_region_vertex_ids))
    print("  ownership boundary edges:", len(contact.boundary_edge_ids))
    print("  Maya edge loops:", contact.maya_loop_count)
    print("  open loops:", contact.open_loop_count)
    print("  closed loops:", contact.closed_loop_count)
    if contact.unresolved_seed_edge_ids:
        print("  unresolved seed edges:", list(contact.unresolved_seed_edge_ids))

    for loop_index, loop in enumerate(contact.maya_loops):
        print(
            "    loop {} | seed={} | {} | Maya edges={} | boundary overlap={}".format(
                loop_index,
                loop.seed_edge_id,
                "closed" if loop.is_closed else "open",
                len(loop.edge_ids),
                len(loop.boundary_edge_ids),
            )
        )


def run():
    mesh, joints = _loaded_unskinned_context()
    region_result = region_solver.solve_region_ownership(
        mesh=mesh,
        joints=joints,
    )
    coherence_result = boundary_coherence.analyze_region_boundary_coherence(
        region_result
    )

    print("\n[AD Skin Tool v3.10 - Region Boundary Coherence Smoke]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Boundary contacts:", len(coherence_result.contacts))
    print(
        "Single Maya loop:",
        _count(coherence_result, boundary_coherence.SINGLE_LOOP),
    )
    print(
        "Multiple open loops (suspicious):",
        _count(coherence_result, boundary_coherence.MULTIPLE_OPEN_LOOPS),
    )
    print(
        "Multiple mixed loops (review):",
        _count(coherence_result, boundary_coherence.MULTIPLE_MIXED_LOOPS),
    )
    print(
        "Unresolved Maya traversal:",
        _count(coherence_result, boundary_coherence.UNRESOLVED),
    )

    review_indices = list(coherence_result.suspicious_contact_indices)
    review_indices.extend(coherence_result.mixed_loop_contact_indices)
    review_indices.extend(coherence_result.unresolved_contact_indices)

    print("\nContacts requiring viewport review:")
    if not review_indices:
        print("  None")
    for index in review_indices[:PRINT_CONTACT_LIMIT]:
        _print_contact(index, coherence_result.contacts[int(index)])

    builtins.AD_SKIN_V310_REGION_RESULT = region_result
    builtins.AD_SKIN_V310_BOUNDARY_COHERENCE_RESULT = coherence_result

    if SELECT_SUSPICIOUS_EDGES:
        boundary_coherence.select_boundary_contact_edges(
            coherence_result,
            category="suspicious",
        )
        print(
            "\nSelected suspicious ownership-boundary edges:",
            coherence_result.suspicious_contact_count,
            "contacts",
        )

    print("No Region ownership was changed. No skinCluster was created.")
    print(
        "Saved result as "
        "builtins.AD_SKIN_V310_BOUNDARY_COHERENCE_RESULT"
    )


if __name__ == "__main__":
    run()
