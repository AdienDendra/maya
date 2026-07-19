"""Small v6.1B smoke for owner maximality and cutoff tie-breaking."""

import builtins
import importlib

import numpy as np

from ad_skin_tools.bind_smoothing import final_constraints


STRESS_MAXIMUM_INFLUENCES = 3
PRINT_LIMIT = 20


importlib.reload(final_constraints)


def _format_row(row, influences, epsilon):
    columns = np.where(row > epsilon)[0].tolist()
    columns.sort(key=lambda column: -float(row[int(column)]))
    return " | ".join(
        "{}={:.8f}".format(
            influences[int(column)].split("|")[-1],
            float(row[int(column)]),
        )
        for column in columns
    )


def run():
    if not hasattr(builtins, "AD_SKIN_V61_REGION_RESULT"):
        raise RuntimeError(
            "Run test_v61_bind_smoothing_constraints.py first."
        )
    if not hasattr(builtins, "AD_SKIN_V61_BIND_SMOOTHING_RESULT"):
        raise RuntimeError(
            "Run test_v61_bind_smoothing_constraints.py first."
        )

    region = builtins.AD_SKIN_V61_REGION_RESULT
    smooth = builtins.AD_SKIN_V61_BIND_SMOOTHING_RESULT
    raw = smooth.diffusion_result.weights
    owners = region.owner_indices
    epsilon = smooth.options.weight_epsilon

    production_ties = final_constraints.enforce_maximum_influences_by_distance(
        weights=raw,
        owner_indices=owners,
        vertex_positions=region.vertex_positions,
        influence_positions=region.influence_positions,
        maximum_influences=smooth.effective_maximum_influences,
        weight_epsilon=epsilon,
    )
    production_owner = final_constraints.project_region_owner_to_maximum(
        production_ties.weights,
        owners,
    )

    stress_ties = final_constraints.enforce_maximum_influences_by_distance(
        weights=raw,
        owner_indices=owners,
        vertex_positions=region.vertex_positions,
        influence_positions=region.influence_positions,
        maximum_influences=STRESS_MAXIMUM_INFLUENCES,
        weight_epsilon=epsilon,
    )
    stress_owner = final_constraints.project_region_owner_to_maximum(
        stress_ties.weights,
        owners,
    )

    print("\n[AD Skin Tool v6.1B - Final Constraint Smoke]")
    print("No existing v6.1 solver files were replaced.")

    print("\nRegion Owner maximality:")
    print(
        "  owner below maximum before:",
        len(production_owner.owner_below_maximum_before),
    )
    print(
        "  projected rows:",
        production_owner.projected_vertex_count,
    )
    print(
        "  owner below maximum after:",
        len(production_owner.owner_below_maximum_after),
    )
    print(
        "  maximum active influences:",
        final_constraints.maximum_active_influences(
            production_owner.weights,
            epsilon,
        ),
    )
    print(
        "  maximum row-sum error:",
        float(
            np.max(
                np.abs(
                    np.sum(
                        production_owner.weights,
                        axis=1,
                        dtype=np.float64,
                    )
                    - 1.0
                )
            )
        ),
    )

    print("\nCutoff tie rule stress test (Max Influences = {}):".format(
        STRESS_MAXIMUM_INFLUENCES
    ))
    print("  pruned vertices:", stress_ties.pruned_vertex_count)
    print("  discarded entries:", stress_ties.discarded_entry_count)
    print(
        "  equal-weight cutoff rows:",
        len(stress_ties.cutoff_weight_tie_vertex_ids),
    )
    print(
        "  resolved by joint distance:",
        len(stress_ties.distance_resolved_vertex_ids),
    )
    print(
        "  unresolved exact weight+distance ties:",
        len(stress_ties.unresolved_exact_tie_vertex_ids),
    )
    print(
        "  owner below maximum after:",
        len(stress_owner.owner_below_maximum_after),
    )
    print(
        "  maximum active influences after:",
        final_constraints.maximum_active_influences(
            stress_owner.weights,
            epsilon,
        ),
    )

    if stress_ties.unresolved_exact_tie_vertex_ids:
        print(
            "  unresolved IDs:",
            list(
                stress_ties.unresolved_exact_tie_vertex_ids[:PRINT_LIMIT]
            ),
        )

    print("\nOwner-projected rows:")
    for vertex_id in production_owner.projected_vertex_ids[:PRINT_LIMIT]:
        vertex_id = int(vertex_id)
        owner_index = int(owners[vertex_id])
        print(
            "\nvtx[{}] | Region owner={}".format(
                vertex_id,
                region.influences[owner_index].split("|")[-1],
            )
        )
        print(
            "  before:",
            _format_row(raw[vertex_id], region.influences, epsilon),
        )
        print(
            "  after: ",
            _format_row(
                production_owner.weights[vertex_id],
                region.influences,
                epsilon,
            ),
        )

    builtins.AD_SKIN_V61B_PRODUCTION_TIE_RESULT = production_ties
    builtins.AD_SKIN_V61B_OWNER_MAXIMUM_RESULT = production_owner
    builtins.AD_SKIN_V61B_STRESS_TIE_RESULT = stress_ties
    builtins.AD_SKIN_V61B_STRESS_OWNER_RESULT = stress_owner

    print("\nNo skinCluster was created or modified.")


if __name__ == "__main__":
    run()
