# Region Research v10.0

This directory is a clean replacement research area for hard ownership. It does
not import any function, class, or constant from `ad_skin_tools.region`.
Production Bind Skin and Add Influence remain untouched.

Joint input is always a flat list. Joint hierarchy, parent-child relationships,
selection order, and joint names do not participate in the Region mathematics.

## Stage 01: exact nearest regions

Stage 01:

1. captures mesh vertices and joint pivots in world space;
2. builds direct vertex adjacency once;
3. assigns only vertices with one unique exact-nearest joint pivot;
4. splits each owner's vertices into connected topology regions.

The component containing the owner's exact closest owned vertex is marked as the
primary region. Other connected components are secondary regions. Exact-distance
ties remain unassigned and visible.

## Stage 02: boundary candidate owners

Stage 02 preserves every Stage 01 owner. For each secondary region it measures:

1. boundary vertices;
2. assigned owners touching the boundary through direct mesh edges;
3. contact-edge counts for each candidate owner;
4. edges touching exact-tie vertices that remain unassigned.

Stage 02 does not move vertices. Its main result is the set of topology-valid
candidate owners. Contact-edge counts remain diagnostic evidence, not a final
ownership decision.

## Stage 03: conservative single-candidate proposals

Stage 03 creates a copied proposal owner map. A whole secondary region is changed
only when:

1. exactly one assigned candidate owner touches its boundary; and
2. no unassigned exact-tie vertex touches that boundary.

Regions with multiple candidates, no assigned candidate, or unassigned boundary
contact remain deferred. Stage 03 does not create or edit a skinCluster and does
not modify Stage 01 or Stage 02 data.

Stage 03 is one simultaneous proposal pass. It does not iterate or recompute
connected regions yet.

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

stage_03 = rr.run_stage_03(mesh, joints)
```

The selected joints may be independent or come from unrelated hierarchies.

Continue from an existing Stage 02 result without repeating earlier work:

```python
stage_03 = rr.run_stage_03_from_stage_02(stage_02)
```

Results are also stored in `builtins`:

```python
import builtins

stage_01 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_01
stage_02 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_02
stage_03 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_03
```

## Visual selection

Stage 01:

```python
rv.select_exact_ties(stage_01)
rv.select_all_secondary_regions(stage_01)
rv.select_owner(stage_01, "jointA")
rv.select_primary(stage_01, "jointA")
rv.select_secondary(stage_01, "jointA")
rv.select_region(stage_01, "jointA", 1)
```

Stage 02:

```python
rv.select_region_boundary(stage_02, "jointC", 2)
rv.select_contact_pair(stage_02, "jointC", 2, "jointB")
```

Stage 03:

```python
rv.select_stage_03_changed_vertices(stage_03)
rv.select_stage_03_proposal(stage_03, "jointC", 2)
rv.select_stage_03_recipient(stage_03, "jointB")
rv.select_stage_03_deferred_region(stage_03, "jointC", 0)
```

Selection helpers only select Maya vertices. They do not change weights.

## Current stopping point

The research pipeline currently stops before:

- resolving multiple boundary candidates;
- resolving topology-isolated shells;
- resolving exact-distance ties;
- iterating proposals until stable;
- creating hard skin weights;
- smoothing.
