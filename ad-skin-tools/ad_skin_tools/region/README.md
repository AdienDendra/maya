# AD Skin Tool Region Ownership

`ad_skin_tools.region` is the accepted universal hard-ownership foundation. It
replaces the experimental `ad_skin_tools.v3` package name while preserving the
validated distance, connectivity, and local-facing mathematics.

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
   repeat until every vertex has a valid primary/co-primary owner.

Candidate rank moves only farther from the vertex, so no arbitrary iteration
limit is needed. Exact ties, mixed normal signs, and candidate exhaustion are
reported as underdetermined instead of being resolved with joint names,
selection order, region size, or tuned thresholds.

## UI path

The existing **Bind Automatic Surface** action calls
`ad_skin_tools.core.joint_automatic_bind`. That core boundary now delegates
ownership to `ad_skin_tools.region.solve_region_ownership`, then creates a
skinCluster and writes one influence at weight `1.0` for every vertex.
