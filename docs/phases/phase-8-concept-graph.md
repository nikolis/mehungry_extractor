# Phase 8 — Concept graph: hierarchy + food/compound composition

**Goal:** turn the flat concept lookup into a **graph**, so the engine can reason about food
families and about which compounds are found in which foods. Two independent additions: (1)
*hierarchy* edges (salmon → oily fish → fish; quercetin → flavonoid → polyphenol), and (2)
*composition* edges (quercetin ∈ apples, onions) sourced from a curated food-composition
reference — **not** extracted from papers. This is what lets a paper's claim about a compound be
projected onto the foods that contain it, and lets specific mentions roll up to families in
synthesis.

**Prerequisite:** Phases 1–5 (independent of Phases 6–7 — can proceed in parallel). It extends the
vocabulary/normalization layer (`knowledge/normalize.py`, `knowledge/vocab/`), which today is a
flat surface→concept index (`normalize._index`, `normalize.py`) over 25 concepts with no edges of
any kind.

## Why this is blocked today

`dictionaries.json` is a flat list of concepts (`concept_id`, `canonical_name`, `entity_type`,
`surface_forms`). There is:
- **No hierarchy** — "salmon" and "fish" are unrelated ids; a claim about salmon can't roll up to
  "oily fish", and a query for "fish" can't find salmon papers.
- **No composition** — nothing connects the `chemical`/`nutrient` concepts to the `food`
  concepts. "Quercetin reduces inflammation" can never reach "apples contain quercetin", because
  that fact isn't extracted from the paper — it's reference knowledge the engine doesn't hold.

## Deliverables

### Vocabulary / reference data
- Extend the concept record with optional `parents: list[concept_id]` (`broader` edges). Backfill
  the existing 25 concepts (foods → food families; compounds → chemical classes). Keep it curated
  and versioned in `vocab/`.
- Add `vocab/composition.json` — a curated `compound_in_food` edge set: `{compound_concept_id,
  food_concept_id, source, source_version, source_ref}`. **Every edge cites its reference**
  (USDA FoodData Central, Phenol-Explorer, FooDB). This is reference provenance, distinct from
  paper `EvidenceRef` provenance — keep the two kinds clearly separated.
- Document the intended migration path to external ontologies (MeSH/SNOMED for disease, FoodOn for
  food, ChEBI for compound). The existing namespaced `concept_id` scheme (`NUTR:dietary_fiber`)
  already anticipates this; a later phase can map curated ids to ontology ids without breaking
  stored claims.

### Modules
- `knowledge/graph.py` — the concept graph: `ancestors(concept_id)`, `descendants(concept_id)`
  over `parents` edges, and `foods_containing(compound_id)` / `compounds_in(food_id)` over the
  composition edges. Pure, deterministic, built once from the checked-in vocab (mirror the
  `functools.lru_cache` index build in `normalize.py`).
- `knowledge/projection.py` — `project_claim_to_foods(claim)`: given a claim whose subject is a
  compound, emit **derived, clearly-labeled** claim-projections onto each containing food, tagged
  with the composition edge's reference provenance and marked `derived=True` so they are never
  confused with paper-extracted claims.

### DB tables
- `concept_edges` (`parent_id`, `child_id`, `edge_type ∈ {broader, compound_in_food}`, `source`,
  `source_version`, `source_ref`). Add via `create_all` + `SCHEMA_VERSION`; export from
  `db/__init__.py`. This table holds *reference* knowledge, versioned by the vocab release, not by
  an `ExtractionRun`.
- Optionally persist derived projections in a separate `claim_projections` table so they never
  pollute the `claims` table — a projection is not an extracted claim.

### Query / Synthesis
- `query.find_claims(...)` gains hierarchy-aware matching: a query for a family concept matches
  claims on any descendant (opt-in flag, deterministic).
- `synthesis.synthesize` can optionally **roll up** claims to a family level when several papers
  each discuss different members of a family, surfacing both the family-level conclusion and the
  member breakdown (never averaging away the specifics).

## Provenance constraints (offline; determinism optional)
- **Graph is curated reference data, not inference.** Edges are hand-authored/imported and
  versioned — *not* induced by a model. (This is a trustworthiness choice for the reference layer,
  not the old project-wide determinism ban: a local embedding resolver elsewhere in the engine is
  now permitted; the composition/hierarchy graph itself stays curated.)
- **Two provenance kinds, never conflated.** Paper claims keep their `EvidenceRef` (source span).
  Composition/hierarchy edges carry a *reference* citation (dataset + version + record id). A
  projected claim must show **both**: the paper evidence for the compound claim *and* the
  reference edge that connects the compound to the food. If you can't cite both, don't project.
- **Derived ≠ extracted.** Projected claims are flagged and stored separately; they never enter
  the extracted-claims tables and never feed back into extraction.
- **Version the vocab release.** Bump `VOCAB_VERSION` (`vocab/`) whenever edges change; it is
  already recorded on the run via `NORMALIZATION_SOURCE`.

## Suggested steps
1. Add `parents` to the concept record; backfill families for the current 25 concepts.
2. `graph.py`: ancestors/descendants over `parents`.
3. `composition.json` + `concept_edges` table + loader; each edge cites a reference.
4. `foods_containing` / `compounds_in`.
5. `projection.py`: derived, dual-provenance claim projections (stored separately).
6. Hierarchy-aware `find_claims`; optional family roll-up in synthesis.
7. Tests, then stop (gate Phase 9).

## Tests to add (gate)
- **Hierarchy:** `ancestors("FOOD:salmon")` includes `FOOD:oily_fish` and `FOOD:fish`; a family
  query matches a member claim.
- **Composition:** `foods_containing("CHEM:quercetin")` returns the curated foods; each edge
  carries a resolvable reference citation.
- **Projection dual-provenance:** projecting "quercetin reduces inflammation" onto apples yields a
  `derived` projection carrying *both* the paper `EvidenceRef` and the composition edge citation;
  it does **not** appear in the extracted `claims` table.
- **No fuzzy edges:** a compound with no curated composition edge projects to nothing (never
  guesses).
- **Determinism:** graph queries and projections are byte-stable across runs.

## Done criteria
The vocabulary is a versioned graph: mentions roll up to food families and chemical classes, and a
compound claim can be projected onto its containing foods with dual (paper + reference) provenance,
kept strictly separate from extracted claims. Bump `SCHEMA_VERSION` and `VOCAB_VERSION`;
`RULESET_VERSION`/`EXTRACTOR_VERSION` if extraction/query code changed. The graph edges stay
curated/imported (not model-induced), and no online integration is introduced.
