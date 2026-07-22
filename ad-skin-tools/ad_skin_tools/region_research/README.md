# Region Research v10.0

This directory is a clean replacement research area for hard ownership. It does
not import any function, class, or constant from `ad_skin_tools.region`.
Production Bind Skin and Add Influence are untouched.

Joint input is always a flat list. Joint hierarchy, parent-child relationships,
selection order, and joint names do not participate in the Region mathematics.

## Stage 01: exact nearest regions

Stage 01 intentionally contains only four operations:

1. Capture mesh vertices and joint pivots in world space.
2. Build direct vertex adjacency once.
3. Assign only vertices with one unique exact-nearest joint pivot.
4. Split each owner's vertices into connected topology regions.

The component containing the owner's exact closest owned vertex is marked as the
primary region. Other connected components are secondary regions. Exact-distance
ties remain unassigned and visible; Stage 01 does not invent a tie breaker.

## Stage 02: secondary boundary contacts

Stage 02 preserves every Stage 01 owner. For each secondary connected region it
measures only direct topology evidence:

1. Region boundary vertices.
2. Other owners touching the region through mesh edges.
3. Contact-edge count for each touching owner.
4. The unique or tied dominant contact owner.
5. Edges touching exact-tie vertices that remain unassigned.

Stage 02 does not move vertices. Edge-contact count is diagnostic evidence, not
yet an ownership rule.

## Maya usage

```python
import maya.cmds as cmds
import importlib

import ad_skin_tools.region_research.runner as rr
import ad_skin_tools.region_research.visual as rv

importlib.reload(rr)
importlib.reload(rv)

mesh = "body_geo"
joints = cmds.ls(selection=True, long=True, type="joint") or []

stage_01 = rr.run_stage_01(mesh, joints)
```

The selected joints may be independent or come from unrelated hierarchies.

### Continue from an existing Stage 01 result

```python
stage_02 = rr.run_stage_02_from_stage_01(stage_01)
```

This is the preferred research workflow because scene capture, exact distance,
and connected-region discovery are not repeated.

### Run both stages from fresh scene data

```python
stage_02 = rr.run_stage_02(mesh, joints)
stage_01 = stage_02.stage_01
```

Results are also stored in `builtins`:

```python
import builtins

stage_01 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_01
stage_02 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_02
```

## Stage 01 visual selection

```python
rv.select_exact_ties(stage_01)
rv.select_all_secondary_regions(stage_01)
rv.select_owner(stage_01, "leftLeg_jnt")
rv.select_primary(stage_01, "leftLeg_jnt")
rv.select_secondary(stage_01, "leftLeg_jnt")
rv.select_region(stage_01, "leftLeg_jnt", 1)
```

## Stage 02 visual selection

Select the complete boundary of one secondary region:

```python
rv.select_region_boundary(stage_02, "fingerC_jnt", 2)
```

Select only the source-side boundary vertices touching a specific owner:

```python
rv.select_contact_source_vertices(
    stage_02,
    "fingerC_jnt",
    2,
    "fingerB_jnt",
)
```

Select only the neighbouring owner's vertices across those boundary edges:

```python
rv.select_contact_neighbour_vertices(
    stage_02,
    "fingerC_jnt",
    2,
    "fingerB_jnt",
)
```

Select both sides of the contact:

```python
rv.select_contact_pair(
    stage_02,
    "fingerC_jnt",
    2,
    "fingerB_jnt",
)
```

When one owner has the highest contact-edge count without a tie:

```python
rv.select_unique_dominant_contact_pair(
    stage_02,
    "fingerC_jnt",
    2,
)
```

Selection helpers only select Maya vertices. They do not create a skinCluster or
modify weights, so both stages are safe to run repeatedly during research.

## Current stopping point

The research implementation still deliberately stops before:

- facing classification;
- secondary-region reassignment;
- exact-tie resolution;
- neighbour fallback;
- Maya edge-loop discovery;
- smoothing.

The next rule will be chosen after contact counts and visual boundary selections
are evaluated on real meshes.
