# Phase 3 — Relations → observations → claims (with negation + uncertainty)

**Goal:** deterministically extract relations between entities, capture them first as
low-level **observations** (inspectable, close to the text), then normalize to canonical
**claims**. Negation and uncertainty are stored explicitly, never hidden. This is where the
"separate observations from claims" requirement (spec §5) is realized.

**Prerequisite:** Phases 1–2. Subjects/objects come from Phase 2 `EntityMention`s; spans and
provenance come from Phase 1. Load mentions with
`query.list_entities_for_document(engine, doc)` (persisted) or regenerate inline with
`entities.extract(document)` — both deterministic and offline. Each `EntityMention` carries
`mention_id, surface_text, entity_type, concept_id, status, sentence_id, start_char,
end_char, evidence_ref`. **Note the `status`:** a mention may be `unmatched`/`ambiguous`
with `concept_id=None` (Phase 2 never drops these). Relations can still be *observed* over
such a mention; it just can't participate in a normalized `Claim` (see the data model).

## Deliverables

### Modules
- `knowledge/relations.py` — the rule framework (token/lexical/dependency patterns) + a
  small, versioned rule registry. Each rule has an id and a version.
- `knowledge/negation.py` — negation + uncertainty detection.
- `knowledge/observations.py` — build `Observation`s from rule matches.
- `knowledge/claims.py` — normalize `Observation`s into canonical `Claim`s.

### Relation vocabulary (start small — spec §7)
`associated_with, increases, decreases, improves, worsens, reduces_risk, increases_risk,
causes, prevents, no_effect, no_association, contraindicated, associated_with_adverse_event`.
Do not try to cover every biomedical relation. Add rules incrementally, each tested.

### Data model
- `Observation`: `subject, predicate, object, context, polarity, certainty,
  evidence_refs: list[EvidenceRef], rule_id`. Subject/object reference Phase 2 mentions
  (keep both the mention id and surface text).
- `Claim`: the normalized/canonical form of one-or-more observations
  (`subject_concept, predicate, object_concept, context, polarity, certainty`) +
  `evidence_refs`. Because `subject_concept`/`object_concept` are Phase 2 `EntityConcept`s,
  a claim can only form when **both** endpoint mentions normalized (`status=normalized`,
  `concept_id` set). An observation whose subject or object is `unmatched`/`ambiguous` is
  retained at the observation layer but produces no claim — surface the drop count rather
  than silently discarding it.
- Enums (spec §8): `polarity ∈ {positive, negative, neutral}`;
  `certainty ∈ {asserted, possible, uncertain, hypothetical, insufficient_evidence}`.

### DB tables
`observations`, `claims`, `claim_evidence` (M:N claim ↔ evidence), `extraction_rules`
(registry: `rule_id`, `version`, `description`). Index on `document_id`, `predicate`,
subject/object concept ids. Each row carries `run_id`.

### CLI
Implement the `extract` stage: `mehungry extract --document pmid:NNNN` → entities (from
Phase 2, or run inline) → observations → claims → persist.

### Query
Fill `find_claims(...)` in `knowledge/query.py` (deterministic filters only):
`find_claims(condition=..., subject=..., predicate=..., polarity=...)`,
`find_evidence(claim_id)`, `list_claims_for_document(document_id)`.

## Provenance constraints (offline; determinism optional)
- **Rules only** — spaCy `Matcher`/`DependencyMatcher`, regex, dictionaries. No statistical
  relation classifier, no LLM. Recommended start: token + lexical patterns via `Matcher`
  (no parser needed, fully deterministic); add `DependencyMatcher` later, which requires a
  parser model (`en_core_sci_sm` or `en_core_web_sm`) — pin and record its version.
- **Negation/uncertainty deterministically.** Options: `negspacy`, medspaCy ConText, or
  custom cue-based rules over the sentence. Whichever you pick, pin it and record the
  version. Store the result in `polarity`/`certainty`; also keep the triggering cue text in
  `context` for auditability.
- Distinguish, on the same base relation, all four of:
  `fiber is associated with remission` (positive/asserted),
  `fiber is not associated with remission` (negative/asserted or `no_association`),
  `fiber may be associated with remission` (positive/possible),
  `insufficient evidence that fiber is associated with remission`
  (neutral/`insufficient_evidence`).
- **Every observation and claim carries ≥1 `EvidenceRef`** at `SENTENCE` or `EXACT_SPAN`
  precision, plus its `rule_id`/version. A claim's evidence is the union of its
  observations' evidence (`claim_evidence`).
- Observations are the audit layer: normalization to claims must be a pure, testable
  function of observations — no new evidence introduced at the claim step.

## Suggested steps
1. Define the enums + `Observation`/`Claim` models + `extraction_rules` registry.
2. Build `relations.py` with 2–3 token/lexical rules for `associated_with` and
   `increases/decreases`; wire `negation.py`.
3. `observations.extract(document, mentions) -> list[Observation]`.
4. `claims.normalize(observations) -> list[Claim]`.
5. Tables + persist + `extract` stage + `find_claims`.
6. Tests, then stop (gate Phase 4).

## Tests to add (gate)
- **Negation:** `associated` vs `not associated` vs `no association` → correct
  `polarity`/predicate on identical subjects/objects.
- **Uncertainty:** `may`, `might`, `suggests`, `possible`, `insufficient evidence` → correct
  `certainty`.
- **Relations:** small synthetic sentences → exact expected `(subject, predicate, object,
  polarity, certainty)` tuples.
- **Evidence:** every observation/claim has ≥1 `EvidenceRef` whose quoted text reconstructs
  from offsets; `rule_id` present.
- **Determinism:** run twice → identical observations and claims.

## Done criteria
`mehungry extract --document pmid:NNNN` produces observations and normalized claims, each
traceable to an exact sentence/phrase and a rule id/version; `find_claims` returns
deterministic filtered results. Bump `RULESET_VERSION` (Phase 2 left it at `0.1.0`) and
`SCHEMA_VERSION` (Phase 2 left it at `0.2.0`).
