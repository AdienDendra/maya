# Region Research v10.0

This directory is a clean replacement research area for hard ownership. It does
not import any function, class, or constant from `ad_skin_tools.region`.
Production Bind Skin and Add Influence remain untouched.

Joint input is always a flat list. Joint hierarchy, parent-child relationships,
selection order, and joint names do not participate in the Region mathematics.

## Stage 01: complete hard ownership before connectivity

Stage 01 runs in this order:

1. capture mesh vertices and joint pivots in world space;
2. build direct vertex adjacency once;
3. calculate exact nearest-distance candidates;
4. resolve every exact-distance tie;
5. verify that no vertex remains unassigned;
6. split each owner's completed vertex set into connected topology regions.

Exact ties are resolved before connectivity because an unassigned tie vertex can
artificially split one valid owner region into multiple disconnected regions.

The tie resolver is pragmatic and deterministic:

1. direct-neighbour majority among the tied candidates;
2. simultaneous topology propagation through connected tie strips;
3. candidate with fewer frozen pre-resolution owned vertices;
4. stable joint world-position key, then Maya UUID as the mechanical final key.

The component containing the owner's exact closest owned vertex is marked as the
primary region. Other connected components are secondary regions.

Stage 01 guarantees:

```python
np.all(stage_01.nearest.owner_indices >= 0)
```

## Stage 02: boundary candidate owners

Stage 02 preserves every completed Stage 01 owner. For each secondary region it
measures:

1. boundary vertices;
2. assigned owners touching the boundary through direct mesh edges;
3. contact-edge counts for each candidate owner.

Stage 02 does not move vertices. Its main result is the set of topology-valid
candidate owners. Contact-edge counts remain diagnostic evidence, not a final
ownership decision.

## Stage 03: single-candidate proposals

Stage 03 creates a copied proposal owner map. A whole secondary region is changed
when exactly one assigned candidate owner touches its boundary.

Regions with multiple candidates or no assigned candidate remain deferred.
Stage 03 does not create or edit a skinCluster and does not modify Stage 01 or
Stage 02 data.

Stage 03 treats any unassigned boundary as an invariant failure because exact ties
must already be resolved before connected regions are built.

## Stage 04: multiple-candidate proposals

Stage 04 starts from the Stage 03 proposal owner map and resolves only secondary
regions that touch more than one topology-valid candidate owner.

Every candidate is scored against the complete connected region:

```text
aggregate squared distance =
sum of squared distances from every region vertex to the candidate joint
```

The decision order is deterministic and contains no tuned threshold:

1. unique minimum aggregate squared distance;
2. unique largest boundary contact-edge count, only after an exact distance tie;
3. candidate with fewer frozen Stage 01 owned vertices;
4. stable joint world-position key, then Maya UUID.

The whole region is reassigned as one unit. Stage 04 never splits vertices inside
one connected secondary region between different targets.

A region with no external topology candidate remains unresolved for a later
topology-isolated-shell rule. Stage 04 still does not write Maya skin weights.

## Maya usage

```python
import maya.cmds as cmds
import importlib

import ad_skin_tools.region_research.runner as rr
import ad_skin_tools.region_research.visual as rv
import ad_skin_tools.region_research.exact_tie_visual as rtv
import ad_skin_tools.region_research.multiple_candidate_visual as rmv

importlib.reload(rr)
importlib.reload(rv)
importlib.reload(rtv)
importlib.reload(rmv)

mesh = "body_geo"
joints = cmds.ls(selection=True, long=True, type="joint") or []

stage_04 = rr.run_stage_04(mesh, joints)
stage_03 = stage_04.stage_03
stage_02 = stage_03.stage_02
stage_01 = stage_02.stage_01
```

The selected joints may be independent or come from unrelated hierarchies.

Continue from an existing Stage 03 result without repeating earlier work:

```python
stage_04 = rr.run_stage_04_from_stage_03(stage_03)
```

Results are also stored in `builtins`:

```python
import builtins

stage_01 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_01
stage_02 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_02
stage_03 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_03
stage_04 = builtins.AD_SKIN_REGION_RESEARCH_STAGE_04
```

## Exact-tie visual selection

```python
rtv.select_all_exact_ties(stage_01)
rtv.select_ties_resolved_by_topology(stage_01)
rtv.select_ties_resolved_by_fewer_owned_vertices(stage_01)
rtv.select_ties_resolved_by_stable_joint_key(stage_01)
```

Inspect one original tie vertex:

```python
rtv.print_exact_tie_vertex(stage_01, 1234)
```

## Region visual selection

Stage 01:

```python
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

Stage 04:

```python
rmv.select_changed_vertices(stage_04)
rmv.select_proposal(stage_04, "jointB", 1)
rmv.select_recipient(stage_04, "jointA")
rmv.select_unresolved_region(stage_04, "jointC", 0)
```

Selection helpers only select Maya vertices. They do not change weights.

## Current stopping point

The research pipeline currently stops before:

- resolving topology-isolated shells;
- recomputing connectivity from the complete Stage 04 proposal owner map;
- iterating proposals until stable;
- creating hard skin weights;
- smoothing.
