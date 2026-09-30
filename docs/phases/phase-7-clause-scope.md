# Phase 7 — Clause scope & richer qualifier extraction

**Goal:** make the extractor actually *populate* the Phase 6 qualifier layer from real text —
including the hard case at the heart of the motivating rule: a single sentence that asserts
**opposite valence under different conditions**, e.g. *"fiber is beneficial in remission **but**
may aggravate symptoms during an active flare"*. That requires breaking a sentence into
**clauses** before relation matching, scoping each relation to its clause, and attaching the
right qualifiers (disease state, dose, population, duration) to the right clause.

**Prerequisite:** Phase 6 (the `Qualifier` model + `disease_state` type + persistence). This phase
is the extraction-engine work behind that model. It also extends the relation rule framework
(`knowledge/relations.py`) from single-regex-over-connecting-text toward **templated, multi-slot
patterns**, because qualifier-bearing patterns can't be expressed as one flat regex without the
rule list becoming unmaintainable.

## Why this is blocked today

`observations.extract` (`observations.py:63`) has two structural limits that defeat the
motivating example:

1. **Consecutive-pair-only, single-sentence-only.** It scans mentions in span order and tests
   only *adjacent* pairs within one sentence (`observations.py:82`). A contrastive clause
   ("beneficial in remission **but** harmful in flare") is two scoped relations of opposite
   valence inside one sentence — consecutive-pair scanning over the whole sentence collapses or
   mis-binds them.
2. **The connecting-text window ignores clause boundaries.** `document.text[subj.end:obj.start]`
   (`observations.py:85`) can span a `but`/`whereas` boundary, so a cue from the wrong clause can
   fire, and the negation region (`sentence.start → object`, `observations.py:92`) can pick up a
   negation that belongs to a different clause.

## Deliverables

### Modules
- `knowledge/clauses.py` — deterministic clause/contrast segmentation of a sentence on
  coordinating/contrastive markers (`but`, `whereas`, `however`, `while`, `although`, `;`) into
  ordered `Clause` spans (absolute offsets into canonical `text`). Pure, versioned, cue-based —
  no parser model required. Each clause records the marker that opened it (for audit) and whether
  it is *contrastive* to the previous clause.
- `knowledge/qualifiers.py` (extend) — add extractors for the remaining `QualifierType`s:
  `dose` (`high-dose`, `≥N mg`, `low dose`), `population` (`in children`, `in adults with UC`,
  `elderly patients`), `duration` (`for 12 weeks`, `long-term`), `route`, `severity`. Each is a
  pinned cue ruleset scoped to a **clause**, not the whole sentence.
- `knowledge/relations.py` (extend) — introduce a `RelationTemplate` alongside the current
  `RelationRule`: an ordered sequence of slots (`subject`, optional `qualifier`, `cue`, `object`)
  matched within a clause. Keep the existing flat `RULES` working (a template with just
  `subject·cue·object` is equivalent), so this is additive. Each template keeps a `rule_id` +
  `version`.

### Extraction changes (`observations.py`)
- Segment each sentence into clauses first; run relation matching **within a clause**, so the
  connecting-text window and the negation region are both clause-bounded.
- Allow a qualifier concept (e.g. a `disease_state` mention) to sit **between** subject and object
  without breaking the pair — currently such an intervening mention would make them non-adjacent.
- Attach clause-scoped qualifiers (from `qualifiers.py`) to the observation produced in that
  clause. The clause marker + contrastive flag are recorded on the observation's `context` for
  audit.

### Optional enhancement layer (gated, never required)
- A dependency-parse assist via the existing optional scispaCy path (mirror the `[ner]` extra and
  `model_available()` gate in `entities.py:104`): when `en_core_sci_sm` is installed, use the
  parse to improve qualifier attachment and subject/object binding. **When absent, results are
  identical** to the rule-only path. Pin and record the model version on the run (README rule 1).

## Provenance constraints (offline; determinism optional)
- **Rules only by default.** Clause segmentation and qualifier attachment are pinned, versioned
  cue rules — no model needed for the default path. The dependency-parse layer is strictly
  optional and must be a no-op when the model is absent.
- **Clause spans are evidence.** Each `Clause` and each attached `Qualifier` carries offsets/an
  `EvidenceRef`; the clause marker is auditable. Contrastive segmentation must be explainable:
  the audit output shows which marker split the sentence and which clause each relation came from.
- **Offset contract** holds — clauses are sub-spans of the sentence span, sentences of the
  document `text`.
- **No silent loss.** A clause with no relation is fine; a relation with no qualifier is fine
  (empty signature, Phase 6 behavior). Nothing is dropped for failing to segment cleanly — a
  sentence with no clause marker is a single clause.

## Suggested steps
1. `clauses.py`: segment on contrastive/coordinating markers; unit-test the spans.
2. Rework `observations.extract` to iterate clauses; bound the cue window and negation region to
   the clause.
3. Allow an intervening qualifier mention between subject and object.
4. Add the remaining qualifier extractors, clause-scoped.
5. Introduce `RelationTemplate` (keep flat rules working via equivalence).
6. (Optional) wire the gated dependency-parse assist behind `model_available()`.
7. Tests, then stop (gate Phase 8).

## Tests to add (gate)
- **The motivating sentence:** *"Fiber is beneficial during remission but may aggravate symptoms
  in active flare"* → **two** observations, opposite valence/polarity, each with the correct
  `disease_state` qualifier and the correct clause marker recorded.
- **Clause-bounded negation:** a negation in clause A must not flip a relation in clause B.
- **Intervening qualifier:** *"fiber reduces flare risk in remission"* with a disease-state
  mention between subject and object → still one observation with the qualifier attached.
- **Dose / population:** *"high-dose iron in children caused adverse events"* → observation with
  `dose` + `population` qualifiers, each with provenance.
- **Optional-model parity:** with and without the scispaCy parse model, the default-path results
  are identical (the model only *adds* precision, never changes rule-only output).
- **Determinism:** run twice → identical clauses, observations, qualifiers.

## Done criteria
A single contrastive sentence yields multiple correctly-scoped, oppositely-signed, qualifier-
bearing observations, each auditable back to its clause and cue. Dose/population/duration
qualifiers populate from clause-scoped rules. The templated rule framework coexists with the flat
rules. Bump `RULESET_VERSION` and `EXTRACTOR_VERSION`; bump `PIPELINE_VERSION` only if clause
spans change any produced offsets (they should not — clauses are additive sub-spans).
