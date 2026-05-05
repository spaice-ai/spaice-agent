VERDICT: needs revision  
FINDINGS:
1. [severity=blocker] `retrieve_related()` can fail at runtime due to invalid `ORDER BY` alias usage.
   In `retrieve_related`, SQL uses `ORDER BY (CASE WHEN layer = 6 THEN 2.0 ELSE 1.0 END) * similarity DESC` where `similarity` is a select-list alias.  
   PostgreSQL aliases are not reliably usable inside new expressions in `ORDER BY` (only as direct sort keys), which can raise `column "similarity" does not exist`.  
   This is a hard runtime failure path for a public API method.

2. [severity=major] `link_from_cross_references()` overcounts created links.
   The function increments `count` for every attempted insert, but SQL uses `ON CONFLICT ... DO NOTHING`.  
   When duplicates already exist, no row is inserted but `count` still increases, so the returned value is incorrect.  
   This makes operational stats and callers’ behavior unreliable.

3. [severity=major] `link()` returns a UUID even when no link row was inserted.
   `link()` always returns a newly generated `link_id`, but inserts use `ON CONFLICT ... DO NOTHING`.  
   On conflict, the returned ID does not correspond to any DB record, violating function contract (“Returns link ID”).  
   This creates a fragile API and can mislead downstream logic that assumes persistence.

4. [severity=major] `build_spatial_index()` return shape is inconsistent with its own documented contract.
   Docstring says return includes `"cross_layer_links": Q`, but `stats` is initialized without that key and only adds it if cross-layer matches exist.  
   In runs with zero cross-layer links, callers receive a different schema than documented.  
   This is an API consistency bug (edge-case behavior mismatch).

5. [severity=major] `retrieve_multi_hop()` applies decay in a compounded way that does not match the documented scoring model.
   It computes `new_score = best[eid][0] * (decay ** hop_depth) * rel_weight`, where `best[eid][0]` is already decayed from prior hops.  
   This causes decay exponent growth across hops (e.g., depth 3 effectively includes `decay^(1+2+...)`), distorting ranking significantly.  
   The behavior diverges from the stated “`base_similarity * (decay ^ hop_depth) * relationship_weight`” semantics.
