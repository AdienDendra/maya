# Region Ownership Pipeline

This package owns the production blocking architecture used by **Bind Skin**.

1. `mesh_context.py`
   - captures mesh positions, joint pivots, and vertex adjacency once
2. `exact_distance_ties.py`
   - resolves exact closest-distance ties deterministically
3. `closest_region_ownership.py`
   - assigns the closest joint
   - builds connected owner regions
   - separates primary and secondary regions
4. `secondary_surface_facing.py`
   - runs only when a Global Owner is tagged
   - queries only secondary-region anchor vertices
   - protects co-primary and ambiguous secondary regions
   - classifies detached secondary regions
5. `global_owner_assignment.py`
   - assigns detached secondary vertices to the tagged Global Owner
   - leaves closest ownership unchanged when no Global Owner is tagged
6. `closed_loop_ownership.py`
   - scans mesh edges once
   - discovers loops only from ownership-boundary edges
   - applies two-owner aggregate-distance consensus
   - preserves supported opposite pairs
   - preserves exact ties, multi-owner loops, and conflicting proposals
7. `ownership_pipeline.py`
   - produces one final hard-owner map without writing skin weights

`core/smoothed_automatic_bind.py` consumes that final owner map, optionally runs
the shared smoothing solver, creates one skinCluster, and writes one final weight
matrix.

The legacy Region package remains only for workflows that have not yet been
migrated, including Add Influence.
