# Phase 6 — Qualified relations (the context model)

**Goal:** stop collapsing every relation into a bare `(subject, predicate, object, polarity,
certainty)` triple. Attach a set of **typed qualifiers** — first and foremost the *disease
state* a relation holds under — to every observation and claim, each qualifier carrying its own
`EvidenceRef`. This is the smallest change that makes a rule like *"in UC **during remission**
fiber is beneficial"* representable and distinguishable from *"in UC **during active flare**
fiber may cause harm"*. Everything else in Part II builds on this layer.

**Prerequisite:** Phases 1–5. Qualifiers hang off the existing `Observation`/`Claim` objects
(`knowledge/observations.py`, `knowledge/claims.py`) and reuse the Phase 1 offset contract and
`EvidenceRef` (`knowledge/provenance.py`) unchanged. This phase adds the *data model and the
plumbing end-to-end* with a single, deterministic qualifier extractor (`disease_state`); Phase 7
is where richer qualifier extraction (clause scope, dose, population) lands. Keeping the model
change and the extraction change in separate phases is deliberate — the schema stabilizes first.

## Why this is blocked today

`Observation` (`observations.py:32`) and `Claim` (`claims.py:29`) hold exactly a triple plus
`polarity`/`certainty`. The `context` field only ever stores a **negation/uncertainty cue**
(`observations.py:49`, set from `negation.analyze`), never a real condition. `claims.normalize`
folds observations on the 5-tuple key (`claims.py:96`), so a remission claim and a flare claim on
the same entities **merge into one** — the exact distinction we need is destroyed at the claim
step. This phase fixes the model so the distinction survives.

## Deliverables

### Modules
- `knowledge/qualifiers.py` — the `Qualifier` model, the controlled `QualifierType` enum, and a
  deterministic `disease_state` extractor (`extract(document, sentence, observation) ->
  list[Qualifier]`) that binds cue phrases (`during remission`, `in active disease`, `in
  clinical flare`, `quiescent`, …) found in the observation's sentence to the observation. Each
  qualifier normalizes its value against the vocabulary (a new `disease_state` entity type) when
  it can, and is kept `unmatched` with its surface span otherwise — never dropped.

### Data model
- `Qualifier`: `qualifier_type` (enum), `value_concept_id` (optional — set when the cue
  normalizes), `value_text` (the surface), `polarity` is **not** on a qualifier (a qualifier is a
  condition, not an assertion), `evidence_refs: list[EvidenceRef]`, `rule_id`, `rule_version`.
- `Observation` gains `qualifiers: list[Qualifier] = []`.
- `Claim` gains `qualifiers: list[Qualifier] = []`, and — critically — `_claim_id` (`claims.py:55`)
  and the grouping key in `normalize` (`claims.py:96`) fold in a **canonical qualifier signature**
  (sorted `(qualifier_type, value_concept_id or value_text)` tuples). Two observations merge into
  one claim only when their qualifier signatures match. A claim's qualifiers are the deduplicated
  union of its observations' qualifiers (mirror `_dedupe_evidence`, `claims.py:61`).
- `QualifierType` enum (start small — grow one type per phase): `disease_state` only in Phase 6.
  Phase 7 adds `population`, `dose`, `duration`, `route`, `severity`.

### DB tables
- `observation_qualifiers` and `claim_qualifiers` (columns: owner id, `qualifier_type`,
  `value_concept_id`, `value_text`, `rule_id`, `rule_version`, `run_id`) plus their M:N link to
  evidence, mirroring how `claim_evidence` is built (`db/schema.py`). Index on
  `(document_id, qualifier_type, value_concept_id)`. Add via `create_all` + `SCHEMA_VERSION` per
  README rule 7; export from `db/__init__.py`; extend the delete-then-insert persist helpers
  (`persist_relations`) so re-running a document replaces qualifier rows too (idempotent).
- Add `disease_state` concepts to `vocab/dictionaries.json` (`UC:remission`, `UC:active`,
  `IBD:flare`, … namespaced like the existing ids) with an `entity_type` of `disease_state`.

### CLI / Query
- `mehungry extract` output and `mehungry audit` (`audit.render_claim`) render qualifiers inline:
  *"fiber — reduces_risk → flare  [disease_state: remission]  (positive/asserted)"*.
- Extend `query.find_claims(...)` with a `disease_state=...` filter and return qualifiers on the
  claim payloads (`query.list_claims_for_document`, `query.explain_claim`).

### Synthesis (read-side)
- `synthesis.synthesize` groups by `(subject, predicate, object, **qualifier_signature**)` instead
  of the bare triple (`synthesis.py:167` region). The result is a **conditional** conclusion set:
  the same entity pair yields one conclusion per disease state. This is the payoff — see Phase 9
  for the full conditional-synthesis maturity; Phase 6 only needs the grouping key to change and
  the qualifier to appear on the `ConclusionModel`.

## Provenance constraints (offline; determinism optional)
- **Rules only.** The `disease_state` extractor is pinned, versioned regex/cue rules over the
  observation's sentence text — no model, no network. Same rule discipline as `relations.py` /
  `negation.py`: each rule has a `rule_id` + `version`, recorded in `extraction_rules`.
- **A qualifier is evidence-bearing.** Every `Qualifier` carries ≥1 `EvidenceRef` (at least
  `SENTENCE` precision; `EXACT_SPAN` when the cue phrase's offsets are known) pointing at the cue
  that justified it. No qualifier without provenance (README rule 2).
- **Never drop, never merge across conditions.** An unnormalized disease-state cue is kept as a
  qualifier with `value_concept_id=None`. Claims with differing qualifier signatures must not
  merge. If a relation has *no* detected qualifier, its signature is empty and behavior is
  identical to Phase 5 (fully backward compatible).
- **Offset contract** unchanged — qualifier spans are absolute offsets into canonical `text`.

## Suggested steps
1. Add `QualifierType` enum + `Qualifier` model in `knowledge/qualifiers.py`; add `qualifiers` to
   `Observation`/`Claim`.
2. Add `disease_state` concepts to the vocabulary; write the cue-based `disease_state` extractor.
3. Wire the extractor into `observations.extract` (after a rule fires, attach qualifiers).
4. Fold the qualifier signature into `_claim_id` + the `normalize` grouping key; dedupe qualifier
   union onto the claim.
5. Tables + export + persist helpers + `find_claims` filter + audit rendering.
6. Change the synthesis grouping key; surface qualifiers on `ConclusionModel`.
7. Tests, then stop (gate Phase 7).

## Tests to add (gate)
- **Distinction:** *"fiber reduced flares during remission"* vs *"fiber worsened symptoms during
  active disease"* on identical entities → **two** claims with distinct `disease_state`
  qualifiers, not one merged claim.
- **Backward compatibility:** a sentence with no disease-state cue → claim with empty qualifier
  set, byte-identical to the Phase 5 claim id.
- **Provenance:** every qualifier's `EvidenceRef` quoted text reconstructs from offsets; `rule_id`
  present.
- **Normalization:** a disease-state cue that isn't in the vocab is kept `unmatched` with its
  surface, and still keys the claim distinctly.
- **Conditional synthesis:** two papers, one about remission and one about flare, on the same
  relation → two conclusions, not one averaged conclusion.
- **Determinism:** run twice → identical observations, claims, and qualifiers.

## Done criteria
`mehungry extract` and `/analyze` produce claims that carry disease-state qualifiers, each
traceable to the exact cue and rule that set it; a remission claim and a flare claim on the same
entities remain distinct through claim normalization and cross-paper synthesis; `find_claims(...,
disease_state=...)` filters deterministically. Bump `RULESET_VERSION` (0.4.0 → 0.5.0),
`SCHEMA_VERSION` (0.5.0 → 0.6.0), and `SYNTHESIS_VERSION` (0.1.0 → 0.2.0). `EXTRACTOR_VERSION` if
code shipped.
