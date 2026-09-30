# Phase handovers — offline knowledge/evidence engine

This directory is the build plan for evolving `mehungry_extractor` into an offline,
fully-provenanced biomedical evidence engine. Each file is a self-contained handover for one phase:
what exists to build on, what to add, the offline/provenance constraints, the tests
that gate the next phase, and the done criteria.

| Phase | File | Scope | Status |
|---|---|---|---|
| 1 | [phase-1-foundation.md](phase-1-foundation.md) | Acquisition · immutable corpus · canonical doc + offsets · SQLite · provenance · reproducibility | **Done** |
| 2 | [phase-2-entities.md](phase-2-entities.md) | Deterministic biomedical entity extraction + normalization | **Done** |
| 3 | [phase-3-relations-claims.md](phase-3-relations-claims.md) | Rule-based relations → observations → claims; negation + uncertainty | **Done** |
| 4 | [phase-4-study-funding.md](phase-4-study-funding.md) | Study design, population, funding, affiliations, conflicts | **Done** |
| 5 | [phase-5-assessment-audit.md](phase-5-assessment-audit.md) | Assessment frameworks · filtering query API · human-readable audit CLI | **Done** |

### Part II — depth: qualified, structured, and compositional knowledge

The Part I engine (Phases 1–5) extracts flat `(subject, predicate, object, polarity, certainty)`
claims. Part II adds the structure needed to represent **conditional** knowledge ("beneficial in
remission, risky in active flare") and **food/compound composition** ("which foods contain this
compound"). Same non-negotiables — offline and fully provenanced (determinism is no longer one).

| Phase | File | Scope | Status |
|---|---|---|---|
| 6 | [phase-6-qualified-relations.md](phase-6-qualified-relations.md) | Qualifier layer on observations/claims (disease-state first); qualifier-aware claim keying + conditional synthesis grouping | **Planned** |
| 7 | [phase-7-clause-scope.md](phase-7-clause-scope.md) | Clause/contrast segmentation; clause-scoped qualifier extraction (dose/population/…); templated multi-slot relation rules | **Planned** |
| 8 | [phase-8-concept-graph.md](phase-8-concept-graph.md) | Concept hierarchy (food families, chemical classes) + curated compound-in-food composition edges + dual-provenance projection | **Planned** |
| 9 | [phase-9-valence-conditional-synthesis.md](phase-9-valence-conditional-synthesis.md) | Clinical valence (beneficial/harmful) distinct from polarity; condition-partitioned conclusions with safety flags | **Planned** |
| 10 | [phase-10-parse-based-relations.md](phase-10-parse-based-relations.md) | Dependency-parse relation binding (subject/object via arcs, `conj`/`cc` coordination) replacing flat consecutive-mention windows; regex flat binder kept as the model-free floor | **Planned** |
| 11 | [phase-11-open-relation-discovery.md](phase-11-open-relation-discovery.md) | Discovery-first stage: a local model flags relation-bearing sentences/clauses verbatim, anchored to offsets, returned for human review before any schema is imposed | **Planned** |
| 12 | [phase-12-qualified-entities.md](phase-12-qualified-entities.md) | Qualified entities: text-derived restrictive modifiers on entity mentions (`localized_in` first) as concept→concept edges — the entity-level analogue of the Phase 6 qualifier layer; A2 folds a key-bearing modifier into the claim + synthesis keys | **Done** |

Recommended sequencing: **6 → 7** (the context model, then the extraction that fills it); **8** can
run in parallel (it's a separate vocab/reference layer); **9** is the maturity layer on top of 6–8.
**10** follows 7 — it supersedes the flat relation binder that 7 clause-scoped, and is unblocked now
that the scispaCy model (whose pipeline already includes a parser) is a first-class dependency.
**12** builds on **10**'s parse binder (it reads `nmod`/`prep` arcs off entity anchors) and mirrors
**6** at the entity level; its A1 slice (representation/display) is independent of the A2 claim-key
work, just as 6 preceded 7.

## Rules that hold for every phase

1. **Offline, local only.** No online integrations — no hosted LLM APIs, no remote vector
   databases, no network calls except the single acquisition fetch. Non-deterministic power is
   allowed but must come from libraries that run **locally** (local embedding models,
   `scikit-learn`, `gensim`, `numpy`). Determinism is *not* required; when a non-deterministic
   component runs, record its model + version on the run so results stay comparable and auditable.
2. **Provenance is mandatory — and is the guarantee that replaces determinism.** No knowledge
   object (entity mention, observation, claim, funding relationship, study characteristic,
   assessment) may exist without an `EvidenceRef` (`knowledge/provenance.py`) pointing back to the
   source. Use `ProvenancePrecision` to mark how exact the span is; never silently upgrade
   precision. When a non-deterministic step resolves something, also record *how* it decided (the
   match, the score) so the call is auditable even though it isn't reproducible.
3. **Offset contract.** All new spans are absolute character offsets into the document's
   canonical `text`, so `document.text[start:end] == span_text`. Reuse it; don't invent a
   second coordinate system.
4. **Additive.** The legacy LLM path (`cli.py`, `extract.py`, `client.py`, `ner.py`,
   `models.py`, `pmc.py`) and the `/api/local_ai/*` seam stay working and untouched.
5. **Reproducibility bookkeeping.** Bump the relevant version constant in
   `knowledge/__init__.py` (`PIPELINE_VERSION` / `RULESET_VERSION` / `EXTRACTOR_VERSION` /
   `SCHEMA_VERSION`) when behavior or schema changes, and record model/ontology versions on
   `ExtractionRun` (`knowledge/run.py`) so v1 and v2 outputs are distinguishable.
6. **Tests gate the next phase.** Add the phase's tests before starting the following phase. For
   deterministic components, keep the run-twice byte-equivalence test. For non-deterministic ones,
   assert the invariant that still holds instead: every emitted fact carries a valid provenance
   span (`document.text[start:end] == quoted_text`) and records how it was resolved.
7. **New tables via `create_all` + `SCHEMA_VERSION`.** Alembic stays deferred until the
   schema stabilizes. Add tables in `knowledge/db/schema.py`, export them from
   `knowledge/db/__init__.py`, and let `init_db` create them.

## The guiding question

> If the system tells you a fact, can it show which paper, and — wherever the text supports it —
> which section, sentence, or phrase caused it to believe that fact, offline and without hiding
> what it's unsure of?

If a phase's design can't answer "yes", redesign that part before implementing it. It no longer has
to answer "byte-for-byte reproducibly" — only "traceably, offline".
