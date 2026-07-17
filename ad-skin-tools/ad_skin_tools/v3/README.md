# AD Skin Tool v3

Version 3 is a new solver foundation isolated from `ad_skin_tools.core`. The
v2.x implementation remains available as a reference and is not imported by the
v3 smoke-test path.

## Universal constraints

Production ownership must be derived from supplied geometry and joints. The
solver must not use body-part names, joint-name conventions, calibrated
percentages, tuned multipliers, minimum vertex quotas, or sample-specific rules.

## Accepted rollback baseline: v3.0

`scripts/test_v30_distance_ranking.py` compares every world-space mesh vertex
with every supplied joint pivot using exact squared Euclidean distance.

The result is stored as:

```python
builtins.AD_SKIN_V30_DISTANCE_RESULT
```

v3.0 does not read topology, normals, visibility, or hierarchy, and does not
create or modify a skinCluster.

## Previous experiments

- **v3.1 pivot visibility — rejected.** Direct pivot-to-vertex visibility caused
  self-occlusion and rejected valid vertices on the source finger.
- **v3.2 incoming-segment visibility — partial and non-universal.** It improved
  the finger result but required a deformation joint ancestor in the Maya DAG.
  Production rigs do not universally encode logical deformation chains as DAG
  joint ancestry.

Neither experiment is inherited by v3.3.

## Current smoke stage: v3.3 raw-ownership connectivity

v3.3 investigates one selected influence at a time:

1. Take every vertex raw-owned by that influence in v3.0.
2. Build the mesh-edge graph induced only by those vertices.
3. Find every exact connected region in that induced graph.
4. Find the raw vertex or vertices with the exact minimum distance to the joint.
5. The connected region containing the exact-nearest anchor is the primary
   region when that anchor region is unique.
6. Every other connected region is reported as a detached ownership island.

There is no largest-region rule, minimum island size, visibility ray, vertex
normal, joint hierarchy, replacement owner, or skin-weight write.

If exact-nearest anchor vertices occur in more than one connected region, the
primary region is underdetermined and v3.3 reports all anchor regions instead of
choosing one artificially.

## v3.3 smoke workflow

1. Run `scripts/test_v30_distance_ranking.py` with one mesh and the complete
   joint list selected.
2. Select exactly one influence to inspect.
3. Run `scripts/test_v33_ownership_connectivity_probe.py`.

The result is stored as:

```python
builtins.AD_SKIN_V33_CONNECTIVITY_RESULT
```

When the primary region is unambiguous, detached vertices are selected
automatically. Selection helpers:

```python
from ad_skin_tools.v3.ownership_connectivity_probe import select_probe_vertices

select_probe_vertices(
    builtins.AD_SKIN_V33_CONNECTIVITY_RESULT,
    category="primary",
)
select_probe_vertices(
    builtins.AD_SKIN_V33_CONNECTIVITY_RESULT,
    category="detached",
)
select_probe_vertices(
    builtins.AD_SKIN_V33_CONNECTIVITY_RESULT,
    category="anchors",
)
```

## Explicitly deferred

- assigning detached islands to replacement joints;
- combined-object and multiple-shell policy;
- normal-facing logic;
- skeleton-graph competition;
- final ownership resolution;
- skinCluster creation or weight writing.
