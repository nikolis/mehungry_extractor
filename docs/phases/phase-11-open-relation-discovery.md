# Phase 11 — Open relation discovery (relation-bearing sentences, for human review)

**Goal:** add a **discovery-first** stage that imposes *no* structure on relationships at all — not
a predicate, not even a subject/object split. A local, purpose-built model reads a paper's canonical
text and identifies the **sentences (or clauses) that assert a relationship**. Each such span is
recorded **verbatim, whole**, anchored to its exact source offsets, and the REST API returns those
spans per paper in a **review-friendly** shape so a human can read *how relationships are actually
expressed* — in the paper's own words — before any schema is imposed.

This is the deliberate opposite of a triple extractor. A `(subject, predicate, object)` triple bakes
in binarity, a single connecting predicate, and an argument/predicate segmentation — all of which are
*schema decided in advance*. Dietary/biomedical relationships are routinely n-ary and conditional
(dose, population, disease state, duration — the very richness Part II of `concepts.md` treats as
first-class), so a binary triple would throw exactly that away. The whole point of this phase is to
**commit to nothing** and just surface the natural-language relationship for reading.

**Explicitly out of scope (later phases):** any decomposition into subject/predicate/object or n-ary
frames; delineating or clustering predicate surfaces; inducing a controlled predicate vocabulary;
folding into **claims** (Concept 9); **synthesis**/**cohesion**; **valence** (Concept 16). These
relation-bearing spans are a **sandbox**: detected, persisted to their *own* table, served for
review — and they **never** enter the trusted claims/synthesis pipeline. That separation mirrors how
the legacy hosted-LLM path is "kept separate and non-deployed; the two never mix."

## The accepted trade-off

Recording whole spans is maximally faithful and imposes nothing, but it is deliberately **not
aggregable**: with no predicate delineated, you cannot yet tabulate or cluster "how predicates are
phrased." That is fine — this phase produces a *reviewable inventory of relationship statements*, and
delineating predicates for aggregation is a later phase's job. We choose fidelity now, structure
later.

## Why this can't be a trusted `Observation`

A trusted `Observation` (Concept 9) requires a controlled `predicate`, a `rule_id`/`rule_version`, and
a `polarity`/`certainty` — everything a `Claim` key and `synthesis` grouping depend on. A
relation-bearing span has **none** of those, by design: it is a stretch of text flagged as asserting
*some* relationship, nothing more. So Phase 11 adds a **parallel, clearly-labelled** record that
carries only the span + how it was detected, and that cannot leak into claims.

## Provenance — trivially exact, still audited

Because the unit of record *is* a sentence/clause of the canonical text, provenance is the simplest it
can be: the span is `document.text[start_char:end_char]`, so `EvidenceRef` is `EXACT_SPAN` **by
construction** — no argument re-anchoring, no generative-rewrite problem, no precision fallback. The
offset contract (Concept 2) holds for free.

The detector is still a **model**, hence **non-deterministic**, so — exactly as `concepts.md`'s
guiding rule requires — each record stores *how it was produced*: the detector **name + version** and
the model's **confidence score**. Auditable though not reproducible. This stage runs only when its
optional model is installed; there is no model-free floor for it (detection is a model feature by
definition, and it feeds nothing downstream that would need a deterministic fallback).

## The design

### Modules

- **`knowledge/openrel.py` (new).** A thin wrapper over the chosen model, structured like
  `parse.py`/`entities.py`:
  - `available()` + a lazily-loaded, cached handle, so importing the package without the optional
    `[openrel]` extra never fails.
  - A `RelationBearingDetector` **protocol** — `detect(document: Document) -> list[DetectedSpan]`,
    where each `DetectedSpan` is `(sentence_id, start_char, end_char, score)` over the canonical text.
    The concrete model is **pluggable**: comparing which model flags which sentences is itself part
    of the review.
  - `extract_open_observations(document, *, detector=None) -> list[OpenObservation]` — runs the
    detector, and for each flagged span builds an `OpenObservation` with an `EXACT_SPAN` ref.
  - **Default granularity is the sentence** (always known, always exact). Optional **clause** scoping
    reuses the existing `clauses.py` segmenter (Concept 15): when the detector localizes to a clause,
    the recorded span is the clause; otherwise the whole sentence.
  - **Default detector strategy — reuse, don't retrain.** Any off-the-shelf open-RE/OpenIE model can
    serve *purely as a detector*: run it, and for every sentence on which it fires (emits ≥1 relation),
    record that **sentence verbatim** + the model's max score, and **discard its triple/decomposition
    entirely**. This leverages a model "specifically designed for the job" while committing to none of
    its imposed structure. A dedicated relation-sentence classifier is an alternative adapter behind
    the same protocol.

- **`knowledge/openobs.py` (new model).** An `OpenObservation` Pydantic model — despite the name it
  holds **no** subject/predicate/object; that absence *is* the design:

  ```
  open_observation_id: str      # deterministic hash(document_id, start_char, end_char, detector_name)
  document_id: str
  sentence_id: str              # the owning sentence (clause index too, if clause-scoped)
  start_char: int; end_char: int
  text: str                     # verbatim == document.text[start_char:end_char]
  detector_name: str; detector_version: str; score: float
  evidence_refs: list[EvidenceRef]   # a single EXACT_SPAN over [start_char, end_char]
  ```

### Persistence — `db/schema.py` (new table)

- **`open_observations`** — one row per record, columns mirroring the model, evidence stored inline as
  JSON (consistent with the `observations` table). It has **no** foreign key into
  `claims`/`claim_evidence` and is **never** read by `claims.py`/`synthesis.py`.
- Tied to an `ExtractionRun` whose fingerprint includes the **detector name + version** (Concept 4),
  so re-running the same detector **replaces** a document's open observations and switching detectors
  forks the run. `db.persist_open_observations(engine, run, doc_id, observations)` writes idempotently
  (delete-by-document + insert), the same pattern as `persist_relations`.
- **`query.list_open_observations_for_document(engine, doc_or_pmid) -> list[dict]`** — read side,
  ordered by descending `score` then start offset (most-confident first, for review).

### Pipeline seam

- A **standalone** entry point `pipeline.discover_open_relations(pmid, *, corpus, engine,
  persist=True)` that assembles the canonical `Document` (reusing the normalize path) and calls
  `openrel.extract_open_observations`. It does **not** run inside `extract_document` — discovery is
  opt-in and orthogonal to the trusted pass, so it never slows or contaminates normal extraction.

### REST API — `api/` (new endpoints + models)

Two endpoints under a `discover` prefix, kept off the trusted `/analyze` batch path:

- **`POST /discover/relations`** — body `{ "pmids": [...], "extract": true }`. For each PMID: ensure
  the paper is in the corpus (same acquisition gate as `/analyze`), run discovery, persist, and return
  the flagged spans. The "run + review" call.
- **`GET /discover/relations/{pmid}`** — return the already-persisted spans for one paper (no
  re-extraction), so a reviewer can re-open results without recomputing.

**Human-friendly response shape** (new `api/models.py` types):

```
OpenRelationDetail:
  open_observation_id
  text                                 # the relation-bearing sentence/clause, verbatim — the thing you read
  sentence_id, start_char, end_char
  score, detector_name, detector_version
  entities: [ { surface_text, entity_type, concept_id, start_char, end_char } ]   # OPTIONAL context
  evidence: [ { document_id, sentence_id, start_char, end_char, quoted_text, precision } ]

PaperOpenRelations:
  pmid, document_id, paper_title, paper_url
  relations: [ OpenRelationDetail ]    # sorted by score desc

OpenRelationResponse:
  run: RunInfo                         # includes detector name + version
  papers: [ PaperOpenRelations ]
  warnings: [ ... ]
```

The reviewable payload is just `text` + `score`: a human reads the sentences the model believes assert
a relationship, in the paper's own words, most-confident first. The optional `entities` array reuses
the existing Concept-6 mention detection to *lightly* mark which known entities appear in the span —
context only, imposing no relation structure. There is no `subject`/`predicate`/`object` field and no
highlighting of a predicate, because none has been decided.

This endpoint set is **REST-API-only and adds no controlled concept**, so — per project RULE 1's sole
exclusion — the route/serialization work is documented in `docs/api.md`. But the **new open-relation
concept itself** (an unconstrained, model-flagged, span-anchored *relationship statement* deliberately
kept out of claims) is a genuine new idea and, when implemented, **requires a new section in
`docs/concepts.md`** describing what it is, why it carries no predicate, and how it keeps provenance
while imposing no structure.

## Tool choice (pluggable; a recommended default)

The `RelationBearingDetector` protocol keeps this swappable — comparing which sentences different
models flag is part of the review. All candidates run **locally/offline** (no hosted API — a hosted
LLM stays in the separate legacy path):

- **An open-RE/OpenIE model used as a detector** (generative REBEL-style, or extractive Stanford
  OpenIE/OpenIE6): run it, keep only the sentences it fires on, discard the triples. **Recommended
  default** — no training needed, and it directly answers "which sentences does a relation model think
  are relational?" Packaged behind the optional `[openrel]` extra, lazily loaded.
- **A dedicated relation-sentence classifier** — a fine-tuned binary "does this sentence assert a
  relationship?" model. More targeted, needs training data; an alternative adapter for later.

## Constraints honored

- **Offline/local only** — the detector is a local model behind an optional extra; no network beyond
  the single acquisition fetch.
- **Provenance never traded away** — every record is a verbatim span with an `EXACT_SPAN` ref and
  records detector name + version + score.
- **Separation** — open observations live in their own table and endpoints; they never enter claims,
  cohesion, or synthesis. The trusted pipeline is untouched.

## Testing / validation

- Deterministic unit tests over a **fake detector** implementing the protocol (returns fixed spans),
  covering the `EXACT_SPAN` anchoring (`text == document.text[start:end]`), idempotent persistence,
  query ordering, and API serialization — all without loading a real model.
- A model-gated integration test (skipped when `[openrel]` is absent, mirroring the scispaCy-gated
  tests) that runs the real detector over a fixture paper and asserts the response shape.

## Follow-ups this phase deliberately leaves open

Reading the flagged sentences → deciding what a "relationship" should decompose into for *this*
purpose → delineating predicate surfaces → inducing a curated vocabulary → a second-layer extractor
that maps into claims. All out of scope here; this phase only produces the reviewable inventory of
relationship statements.
