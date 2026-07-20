# AD Skin Tool Region Ownership

`ad_skin_tools.region` is the hard-ownership foundation used by Bind Skin and
Add Influence. It contains the validated distance, connectivity, local-facing,
closed-loop, and exact-tie resolution stages.

## Production pipeline

1. Rank every vertex against every supplied joint pivot by exact squared
   Euclidean distance.
2. Build the mesh-edge graph induced by each provisional owner's vertices.
3. Split that graph into exact connected ownership regions.
4. Keep the unique exact-nearest anchor region as primary.
5. Test each secondary region from its exact local anchor using geometric
   world-space face-normal orientation.
6. Keep interior-facing regions as co-primary.
7. Advance rejected detached vertices to their next exact distance candidate and
   repeat until every vertex has a valid primary or co-primary owner.
8. Resolve closed-loop and exact-tie ambiguity through the approved geometric
   guards and distance tie-break stages.

Candidate rank moves only farther from the vertex, so no arbitrary iteration
limit is needed. Joint names, hierarchy, selection order, region size, and tuned
artist thresholds are not ownership criteria.

## Runtime path

Bind Skin calls `ad_skin_tools.core.automatic_surface_commands`, which delegates
to the Region solver, applies the approved blocking guards, optionally smooths
the immutable owner map, creates the skinCluster, and writes the final weights.
Add Influence evaluates the same ownership pipeline against existing and pending
joints, then writes only accepted unlocked claims.
