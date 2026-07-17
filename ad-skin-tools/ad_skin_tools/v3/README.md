# AD Skin Tool v3

Version 3 is a new solver foundation and is intentionally isolated from
`ad_skin_tools.core`. The v2.x implementation remains available as a reference
and is not imported by the v3 smoke-test path.

## Universal constraints

Production ownership must be derived from the supplied geometry and joints.
The solver must not use body-part names, joint-name conventions, calibrated
percentages, tuned multipliers, minimum vertex quotas, or sample-specific
rules. Performance-only chunking is allowed because it must not change the
mathematical result.

## Development rule

Only one failure mode is investigated at a time. Multiple-shell handling,
topology-component partitioning, normal consistency, hierarchy constraints,
and skin-weight writing are outside the current smoke-test scope.

## v3.0 baseline: exact pivot-distance ranking

Version 3.0 is frozen as the accepted rollback baseline.

`scripts/test_v30_distance_ranking.py` compares every world-space mesh vertex
with every supplied joint pivot using exact squared Euclidean distance.

The baseline:

- records a unique nearest joint;
- preserves exact ties as unresolved;
- reports joints with zero unique-nearest vertices;
- detects exactly coincident joint positions;
- does not create or edit a skinCluster.

The result is stored as:

```python
builtins.AD_SKIN_V30_DISTANCE_RESULT
```

## v3.1 smoke stage: focused first-surface visibility

Version 3.1 investigates only cross-surface ownership between nearby surfaces.
It builds on the accepted v3.0 result and does not divide the mesh into topology
components.

Workflow:

1. Run `scripts/test_v30_distance_ranking.py` with the mesh and complete joint
   list selected.
2. Select exactly one problematic joint whose raw region crosses onto an
   adjacent surface.
3. Run `scripts/test_v31_visibility_probe.py`.

For only the vertices raw-owned by the selected joint, the probe tests whether
the target vertex patch is the first mesh surface reached by the segment from
the candidate joint pivot to the target vertex.

- If the target patch is hit first, the raw owner remains visible in the report.
- If another surface is hit first, the vertex is reported as cross-surface.
- For rejected vertices, the probe searches the exact-distance ranking for the
  nearest candidate whose segment reaches the target patch first.
- Exact-distance ties between visible candidates remain unresolved.
- No weights are written.

The probe result is stored as:

```python
builtins.AD_SKIN_V31_VISIBILITY_RESULT
```

Rejected vertices are selected automatically for visual inspection. Additional
selection helpers are available through:

```python
from ad_skin_tools.v3.visibility_probe import select_probe_vertices

select_probe_vertices(
    builtins.AD_SKIN_V31_VISIBILITY_RESULT,
    category="visible",
)
select_probe_vertices(
    builtins.AD_SKIN_V31_VISIBILITY_RESULT,
    category="rejected",
)
```

Maya's documented default mesh-intersection tolerance is used explicitly in
this smoke probe for reproducibility. It is not accepted as a production
ownership parameter.

## Deferred work

The following are intentionally not designed yet:

- multiple-shell or combined-object handling;
- topology-component modes;
- normal-facing consistency;
- skeleton-graph competition;
- native Maya bone-segment comparison;
- deterministic final owner writing;
- skinCluster creation or modification.
