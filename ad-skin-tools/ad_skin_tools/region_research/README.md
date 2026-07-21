# Region Research v10.0

This directory is a clean replacement research area for hard ownership. It does
not import any function, class, or constant from `ad_skin_tools.region`.
Production Bind Skin and Add Influence are untouched.

## Stage 01: exact nearest regions

Stage 01 intentionally contains only four operations:

1. Capture mesh vertices and joint pivots in world space.
2. Build direct vertex adjacency once.
3. Assign only vertices with one unique exact-nearest joint pivot.
4. Split each owner's vertices into connected topology regions.

The component containing the owner's exact closest owned vertex is marked as the
primary region. Other connected components are secondary regions. Exact-distance
ties remain unassigned and visible; Stage 01 does not invent a tie breaker.

## Maya usage

```python
import importlib
import ad_skin_tools.region_research.runner as rr
import ad_skin_tools.region_research.visual as rv

importlib.reload(rr)
importlib.reload(rv)

mesh = "body_geo"
joints = [
    "root_jnt",
    "spine_jnt",
    "leftLeg_jnt",
    "rightLeg_jnt",
]

result = rr.run_stage_01(mesh, joints)
```

The result is also stored as:

```python
import builtins
result = builtins.AD_SKIN_REGION_RESEARCH_STAGE_01
```

### Visual selection

```python
rv.select_exact_ties(result)
rv.select_all_secondary_regions(result)
rv.select_owner(result, "leftLeg_jnt")
rv.select_primary(result, "leftLeg_jnt")
rv.select_secondary(result, "leftLeg_jnt")
rv.select_region(result, "leftLeg_jnt", 1)
```

Selection helpers only select Maya vertices. They do not create a skinCluster or
modify weights, so Stage 01 is safe to run repeatedly during research.

## Current stopping point

Stage 01 deliberately stops before:

- facing classification;
- detached-region reassignment;
- exact-tie resolution;
- neighbour fallback;
- Maya edge-loop discovery;
- smoothing.

The next stage should be chosen from the visual result, not added speculatively.
