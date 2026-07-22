# Region Ownership Research

This package follows one explicit ownership pipeline:

1. `closest_region_ownership.py`
   - exact closest joint per vertex
   - deterministic exact-distance tie resolution
   - connected owner regions
   - primary and secondary region classification
2. `secondary_surface_facing.py`
   - runs only when a Global Owner is tagged
   - queries only secondary-region anchor vertices
   - keeps co-primary and ambiguous secondary regions
   - marks detached secondary regions
3. `global_owner_assignment.py`
   - sends detached secondary vertices to the one tagged Global Owner
   - preserves the exact closest map when no Global Owner is tagged
4. `closed_loop_ownership.py`
   - scans mesh edges once
   - queries Maya edge loops only from ownership-boundary edges
   - applies two-owner aggregate-distance consensus
   - preserves supported opposite pairs
   - preserves exact ties, multi-owner loops, and conflicting proposals
5. `ownership_pipeline.py`
   - orchestrates the complete final hard-owner map
   - never creates or writes a skinCluster
6. `visual_bind.py`
   - creates one smoke-test skinCluster
   - writes the final hard-owner map exactly once
   - validates Maya's stored one-owner weights

The old boundary-contact and reassignment experiments were removed. Production `Bind Skin`
is still untouched until this complete visual result is approved.
