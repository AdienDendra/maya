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

## Version checkpoints

### v3.0 — accepted rollback baseline

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

### v3.1 — rejected hypothesis retained in history

The pivot-to-target first-surface visibility probe produced false rejection on
valid middle-finger vertices because a terminal pivot can be self-occluded by
its own surface. It is not part of this branch and must not be tuned into the
v3.0 baseline.

### v3.2 — current incoming-segment visibility experiment

The current question is deliberately narrow:

> For vertices raw-owned by one selected joint, does the target vertex patch
> become the first surface hit when the ray starts at the closest point on that
> joint's finite incoming parent-to-child bone segment?

The source origin is calculated independently for every tested vertex:

1. find the nearest joint ancestor of the selected source joint;
2. construct the finite incoming segment from parent pivot to source pivot;
3. project the target vertex onto that segment;
4. clamp the projection to the two segment endpoints;
5. cast toward the target vertex;
6. keep the vertex when its incident face patch is the first surface hit;
7. otherwise report it as rejected.

The probe does not:

- search for a replacement joint;
- write final ownership;
- process multiple shells;
- use vertex normals;
- add hierarchy competition rules beyond identifying the incoming segment;
- create or modify a skinCluster.

A zero-length incoming segment is reported as underdetermined rather than
falling back to pivot visibility.

## v3.2 smoke workflow

1. Run `scripts/test_v30_distance_ranking.py` with one mesh and the complete
   joint list selected.
2. Select exactly one source joint, such as `R_arm_mid_004_BND`.
3. Run `scripts/test_v32_segment_visibility_probe.py`.

The result is stored as:

```python
builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT
```

Rejected vertices are selected automatically. Diagnostic selection helpers:

```python
from ad_skin_tools.v3.segment_visibility_probe import select_probe_vertices

select_probe_vertices(
    builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT,
    category="visible",
)
select_probe_vertices(
    builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT,
    category="rejected",
)
```

Maya's documented default mesh-intersection tolerance is used explicitly only
for smoke-test reproducibility. It is not accepted as a production ownership
parameter.

## Deferred work

The following remain intentionally outside the current experiment:

- multiple-shell or combined-object handling;
- topology-component modes;
- normal-facing consistency;
- general skeleton-graph competition;
- native Maya bone-segment comparison;
- deterministic final owner writing;
- skinCluster creation or modification.
