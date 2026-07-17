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

## Staged pipeline

Each stage is implemented and accepted independently. A later stage may reject
or constrain candidates from an earlier stage, but it must not silently alter
the earlier stage's recorded data.

1. **Exact distance ranking**
   - **1A. Exact joint-pivot ranking — current smoke stage**
     - Compare each world-space mesh vertex with every supplied joint pivot.
     - Record unique nearest joints.
     - Preserve exact ties as unresolved.
     - Detect joint groups with exactly coincident world positions.
     - Do not create or edit a skinCluster.
   - **1B. Native Closest Distance primitive audit — not implemented**
     - Compare stage 1A against Maya `bindMethod=0`.
     - Determine the exact contribution of each joint's outgoing bone segment.
     - Do not assume that pivot distance alone reproduces Maya.

2. **Topology-component validation** — not implemented

3. **First-surface visibility validation** — not implemented

4. **Normal-facing consistency** — not implemented

5. **Skeleton-graph competition constraint** — not implemented

6. **Deterministic owner resolution** — not implemented

## Stage-1A smoke test

Run `scripts/test_v30_distance_ranking.py` in Maya after selecting exactly one
polygon mesh and every joint to evaluate. The result is stored as:

```python
builtins.AD_SKIN_V30_DISTANCE_RESULT
```

Inspect the complete ranking for one vertex with:

```python
from ad_skin_tools.v3.distance_ranking import format_vertex_ranking
print(format_vertex_ranking(builtins.AD_SKIN_V30_DISTANCE_RESULT, 0))
```

Stage 1A deliberately reports exact ties and zero-region joints. It does not
attempt to fix them and is not yet a clone of Maya Closest Distance. The report
provides the reference data needed by stage 1B and the later validation stages.
