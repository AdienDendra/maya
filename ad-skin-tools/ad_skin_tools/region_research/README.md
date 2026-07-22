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
primary region. Other connected components are called secondary regions.

Important: `secondary` is descriptive only. It means disconnected from the
owner's primary component; it does not prove that the ownership is wrong.

Stage 01 guarantees:

```python
np.all(stage_01.nearest.owner_indices >= 0)
```

## Stage 02: external boundary contacts

Stage 02 preserves every completed Stage 01 owner. For each secondary region it
measures:

1. boundary vertices;
2. external owners touching the boundary through direct mesh edges;
3. contact-edge counts for each touching owner.

Stage 02 does not move vertices. External contacts describe local topology; they
are not automatically replacement owners.

## Stage 03: conservative single-contact correction

Stage 03 starts from a copy of the complete Stage 01 owner map.

Current research rule:

- exactly one external contact owner: propose moving the whole secondary region
  to that owner;
- multiple external contact owners: preserve the original source owner;
- no external contact owner: preserve the original source owner.

The multiple-contact case is intentionally preserved. A valid source region can
sit between two neighbouring ownerships. The finger test demonstrated this with
`Rfing1BJNTENV` regions 1 and 2: both touch A and C but correctly remain owned by B.

The removed aggregate-distance experiment was invalid for this purpose. Stage 01
already assigned every region vertex to its nearest joint. Comparing the source
joint against other joints with the same point-to-pivot distance metric therefore
cannot provide independent evidence for overriding the source ownership. Excluding
the source joint from that comparison forced a false reassignment.

Stage 03 still does not create or edit a skinCluster. It only returns a copied
proposal owner map and visual diagnostics.

## Maya usage

```python
import maya.cmds as cmds
import importlib

import ad_skin_tools.region_research.runner as rr
import ad_skin_tools.region_research.visual as rv
import ad_skin_tools.region_research.exact_tie_visual as rtv

importlib.reload(rr)
importlib.reload(rv)
importlib.reload(rtv)

mesh = "body_geo"
joints = cmds.ls(selection=True, long=True, type="joint") or []

stage_03 = rr.run_stage_03(mesh, joints)
stage_02 = stage_03.stage_02
stage_01 = stage_02.stage_01
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
rv.select_stage_03_deferred_region(stage_03, "jointB", 1)
```

The last helper retains its older name for compatibility; it selects a region
whose source ownership is now intentionally preserved.

Selection helpers only select Maya vertices. They do not change weights.

## Current stopping point

The research pipeline currently stops before:

- recomputing connectivity from the Stage 03 proposal owner map;
- validating whether another conservative correction pass is required;
- creating hard skin weights;
- smoothing.
