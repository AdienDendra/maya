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

Neither experiment is inherited by v3.3 or v3.4.

## Preserved checkpoint: v3.3 raw-ownership connectivity

v3.3 investigates one selected influence at a time:

1. Take every vertex raw-owned by that influence in v3.0.
2. Build the mesh-edge graph induced only by those vertices.
3. Find every exact connected region in that induced graph.
4. Find the raw vertex or vertices with the exact minimum distance to the joint.
5. The connected region containing the exact-nearest anchor is the primary
   region when that anchor region is unique.
6. Every other connected region is reported as a detached ownership island.

There is no largest-region rule, minimum island size, visibility ray, joint
hierarchy, replacement owner, or skin-weight write.

v3.3 successfully isolates disconnected cross-surface ownership, but it also
reveals that one joint may validly own more than one disconnected surface
region. A palm can expose upper and lower surface regions that are disconnected
inside the hard v3.0 ownership mask even though both belong to the same joint.

The standalone checkpoint remains available through:

```text
scripts/test_v33_ownership_connectivity_probe.py
```

## Current smoke stage: v3.4 region-local facing

v3.4 inherits the exact v3.3 connected regions without changing them.

- The unique v3.3 anchor region remains primary.
- Every other region receives its own exact-nearest local anchor vertex or
  vertices.
- For every face incident to those local anchors, v3.4 compares the geometric
  world-space face normal with the vector from the source joint to the anchor.
- A secondary region becomes co-primary only when every local-anchor face
  observation places the joint on the interior-facing side of the patch.
- A region remains detached when every observation places the joint on the
  exterior-facing side.
- Mixed signs, degenerate observations, and numerically unresolved signs remain
  ambiguous and are not forced into either category.

The sign test does not use an artistic tolerance. Its unresolved interval is the
standard floating-point roundoff bound for a three-term `float64` dot product.

v3.4 does not reassign rejected vertices, merge by region size, inspect naming or
hierarchy, use visibility rays, or write a skinCluster.

## v3.4 smoke workflow

1. Run `scripts/test_v30_distance_ranking.py` with one mesh and the complete
   joint list selected.
2. Select exactly one influence to inspect.
3. Run `scripts/test_v34_region_facing_probe.py`.

The runner recomputes v3.3 connectivity for the selected influence and stores:

```python
builtins.AD_SKIN_V33_CONNECTIVITY_RESULT
builtins.AD_SKIN_V34_REGION_FACING_RESULT
```

Selection helpers:

```python
from ad_skin_tools.v3.region_facing_probe import select_probe_vertices

select_probe_vertices(
    builtins.AD_SKIN_V34_REGION_FACING_RESULT,
    category="accepted",
)
select_probe_vertices(
    builtins.AD_SKIN_V34_REGION_FACING_RESULT,
    category="co_primary",
)
select_probe_vertices(
    builtins.AD_SKIN_V34_REGION_FACING_RESULT,
    category="detached",
)
select_probe_vertices(
    builtins.AD_SKIN_V34_REGION_FACING_RESULT,
    category="ambiguous",
)
```

## Explicitly deferred

- assigning detached islands to replacement joints;
- combined-object and multiple-shell policy;
- skeleton-graph competition;
- final ownership resolution;
- skinCluster creation or weight writing.
