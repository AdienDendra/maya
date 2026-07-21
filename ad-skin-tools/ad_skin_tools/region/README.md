# AD Skin Tool Region Ownership

`ad_skin_tools.region` is the hard ownership foundation used by Bind Skin and
Add Influence. It contains the validated distance, connectivity, local facing,
closed loop, exact tie, and exhausted vertex fallback stages.

## Production pipeline

1. Rank every vertex against every supplied joint pivot by exact squared
   Euclidean distance.
2. Build the mesh edge graph induced by each provisional owner's vertices.
3. Split that graph into exact connected ownership regions.
4. Keep the unique exact nearest anchor region as primary.
5. Test each secondary region from its exact local anchor using geometric
   world space face normal orientation.
6. Keep interior facing regions as co primary.
7. Advance rejected detached vertices to their next exact distance candidate and
   repeat while unused candidates remain.
8. When a vertex exhausts every candidate, assign it from the majority owner of
   its directly connected neighbours. Ignore other exhausted vertices during the
   first vote. If the vote is tied, choose the closest tied owner by exact squared
   distance.
9. Resolve closed loop and exact tie ambiguity through the approved geometric
   guards and distance tie break stages.

Normal candidate rank moves only farther from the vertex. The neighbour fallback
is used only after that ranking is exhausted. Joint names, hierarchy, region
size, and tuned artist thresholds are not fallback criteria.

## Runtime path

Bind Skin calls `ad_skin_tools.core.automatic_surface_commands`, which delegates
to the Region solver, applies the approved blocking guards, optionally smooths
the immutable owner map, creates the skinCluster, and writes the final weights.
Add Influence evaluates the same ownership pipeline against existing and pending
joints, then writes only accepted unlocked claims.
