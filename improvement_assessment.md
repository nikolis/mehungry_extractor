# Improvement assessment — reaching clinically useful, context-stratified conclusions

Companion to [`current_state.md`](current_state.md). This reviews the pipeline against a concrete
target and lays out what it would take to get there, pointing at the exact files/functions to
change.

> **Reading of "more efficient":** the request is really about **effectiveness / sophistication**
> of the *output*, not runtime speed. Everything below is about extraction quality, expressiveness,
> and recall. Where a change also helps performance it's noted.

---

## 1. The target vs. what we can express today

**Target conclusion shape:**

> "In **ulcerative colitis during remission**, there is evidence that **[compound/food] helps**.
> In **UC during a flare**, these are the **caution factors**."

Decompose it and check each requirement against the current model:

| The target needs… | Current capability | Where |
|---|---|---|
| A disease **state/phase** ("remission" vs "flare") as a qualifier on the relation | ❌ none — no context slot | `claims.py:29` `Claim`, `enums.py` |
| A **population / setting** ("in patients with UC") distinct from the relation object | ⚠️ partial — UC is just another entity, not a scoped condition | `observations.py:82` |
| A clinical **benefit/harm** reading ("helps" / "caution") | ⚠️ only surface predicates (`improves`, `reduces_risk`, `adverse_event`) — not mapped to a benefit axis | `relations.py:62`, `enums.py:32` |
| **Compound → food** traceability (curcumin → turmeric) | ❌ none — `food`/`nutrient` types exist but no composition edges | `vocab/dictionaries.json` |
| Aggregation that **keeps remission and flare separate** | ❌ synthesis groups on the bare `(subject, predicate, object)` triple, collapsing states together | `synthesis.py:177` |
| An **outcome / endpoint** ("induced remission", "CRP fell") as a first-class node | ⚠️ `Remission` is a single vocab entry, `outcome` type exists but isn't modeled as a relation role | `vocab/dictionaries.json` |
| **Dose / duration** ("2 g/day for 8 weeks") | ❌ none | — |

The single most important gap is the **flat, binary, single-sentence claim model**. A `Claim` today
is `(subject_concept, predicate, object_concept, polarity, certainty)` — five fields, no room for
"under what condition." Two sentences —

- *"In patients in **remission**, curcumin maintained remission."*
- *"During a **flare**, curcumin showed no benefit."*

— currently normalize toward the **same** `(curcumin, ?, remission)` grouping and get merged or
counted as a conflict, when they are actually two *state-stratified* findings. That is the core
reason the output "isn't at a usable level."

---

## 2. Root causes in the current architecture

1. **Relation extraction is consecutive-pair, single-sentence, cue-regex.**
   `observations.extract` (`observations.py:82`) only tests *adjacent* entity pairs inside one
   sentence, matching a regex on the text *between* them (`relations.match`, `relations.py:152`).
   This structurally cannot capture:
   - n-ary frames ("curcumin — induced — remission — in — UC — during — the maintenance phase"),
   - relations split across sentences,
   - any qualifier that isn't literally between the two entities.

2. **The claim key has no context dimension.** `claims.normalize` groups on
   `(subject_cid, predicate, object_cid, polarity, certainty)` (`claims.py:96`). No slot for
   disease-state, population, outcome, or dose → stratified findings collapse.

3. **Predicates are lexical, not clinical.** `enums.PREDICATES` are surface verbs. "Helps" and
   "caution" are *rollups over* predicate + polarity + the disease's desirability, which nothing
   computes today.

4. **Recall ceiling from a 25-concept vocabulary.** `normalize.py` is exact-match only (casefold +
   whitespace, no fuzzy, no morphology). With 25 concepts most real papers yield a handful of
   mentions, so most sentences produce no observation at all.

5. **Synthesis is triple-grouping only** (`synthesis.py:167`). Even if upstream produced
   state-aware claims, synthesis would need a matching group key to keep them apart.

---

## 3. Improvements — Tier A: within the current rule-based (still-deterministic) design

High ROI, no philosophical change. Ordered by impact.

### A1. Add a **context/qualifier slot** to observations → claims *(the keystone change)*
Give `Observation` and `Claim` an optional structured `qualifiers` field, e.g.
`{"disease_state": "remission", "population": "UC", "phase": "maintenance"}`.

- Detect it deterministically the same way `negation.analyze` already scans the sentence region
  (`negation.py:57`): a new `context.py` with a small versioned cue lexicon
  (`remission`, `flare`/`relapse`/`active disease`, `maintenance`, `induction`, `pediatric`,
  `adult`…). Feed it the sentence (or clause) around the relation.
- Thread the qualifier into the claim key in `claims.normalize` (`claims.py:96`) so remission and
  flare claims never merge, and into the synthesis group key (`synthesis.py:177`).
- **This alone** turns "curcumin ↔ remission" into "curcumin —maintains→ remission **[state=remission]**"
  vs "curcumin —no_effect→ benefit **[state=flare]**", which is the target's backbone.

### A2. Map predicates onto a **benefit/harm axis** relative to the disease
Add a small, versioned table: `(predicate, polarity, object_role) → clinical_direction ∈
{beneficial, harmful, neutral, caution}`. E.g. `reduces_risk`+positive on a disease → beneficial;
`associated_with_adverse_event` → caution; `worsens`+positive → harmful. Compute it in a new
`assessment`-style pure function (mirrors `assessment.assess`, `assessment.py:100`) and surface it
on the `Conclusion`. This is what lets the API say "**helps**" / "**caution factors**" instead of a
raw predicate.

### A3. Grow the vocabulary an order of magnitude (recall)
The dictionary is the recall backbone. Import curated, versioned term lists into
`vocab/dictionaries.json` from public ontologies (MeSH for diseases/symptoms, ChEBI for compounds,
FoodData Central / FooDB for foods & nutrients). `normalize.py`/`entities.py` need **no code change**
— just more concepts and surface forms. Add lightweight morphology (plural/adjectival forms) to
`surface_forms()` if desired. Biggest single lever on "we barely extract anything."

### A4. Add **outcome** and **dose/duration** as relation roles
`Remission`, `CRP`, `disease activity` should be extractable as the *object/outcome* of an
intervention, not just standalone entities. Add outcome cue rules to `relations.py` and a
`dose`/`duration` regex extractor (numbers + units) recorded as observation qualifiers. Enables
"induced remission" and "2 g/day" to appear in conclusions.

### A5. Widen the relation window beyond adjacent pairs
Relax `observations.extract` (`observations.py:82`) from strictly-consecutive pairs to a bounded
window (e.g. any pair within N intervening tokens, still same sentence), with the connecting-text
regex still gating. Catches "curcumin **induced clinical remission** in patients with **UC**"
(three entities) which the adjacent-pair rule misses today. Keep it bounded to stay deterministic
and avoid combinatorial blow-up.

**Expected result of Tier A:** conclusions like
`curcumin —beneficial→ remission [state=remission, dose=2g/day] (supported, 3 papers)` and
`curcumin —caution→ [state=flare] (insufficient evidence, 1 paper)` — i.e. the target shape,
produced deterministically with full provenance.

---

## 4. Improvements — Tier B: architectural (bigger, higher ceiling)

### B1. Promote `Claim` from a triple to an **event/frame**
Model a claim as a frame with typed roles: `intervention`, `outcome`, `population`, `condition/state`,
`direction`, `dose`, `certainty`. This is the honest data model for clinical findings and it
subsumes A1/A4. It touches `claims.py`, the DB schema (`db/schema.py` claim tables), `synthesis.py`,
and the API models (`api/models.py`). Do it once Tier A proves the qualifier concept.

### B2. Replace regex-between-entities with **dependency-parse relations**
scispaCy already loads a model (`entities.py:94`). Using its dependency parse to find the
governing verb between two entities is far more precise than "regex on the substring between them,"
and handles the syntactic variety Tier A5 only approximates. Still offline; **not** byte-reproducible
across model versions, so it must be pinned and version-stamped (the engine already records
`ontology_versions`, `pipeline.py:90`).

### B3. The fork, now resolved: **local non-deterministic candidate generators are allowed**
State/qualifier and n-ary frame extraction are genuinely hard for pure regex. The engine's own
design already contains the escape hatch: **scispaCy is an optional candidate generator whose every
span is validated against the vocabulary and carries its own rule id + version**
(`entities.py:166–182`). The foundational decision has now been made: **determinism is no longer
required**, so a **local** model — embeddings, a dependency parser, a locally-run extractor — can be
added the *same way*:

- the model proposes candidate frames (intervention/outcome/state/direction) **with the exact
  source span it read them from**;
- the pipeline **rejects any candidate whose span doesn't reconstruct** (the offset contract,
  `canonical.py`) and whose entities don't normalize to the vocabulary;
- the model id + version is stamped into `ontology_versions`, so a conclusion is always labeled
  with *how* it was extracted and remains fully auditable back to a source span.

This preserves the crown jewel — **every conclusion traces to a verifiable source span** — while
lifting the recall/expressiveness ceiling dramatically. The only remaining line is **offline vs.
online**: the model must run **locally** (no hosted API). A *hosted* LLM is still out of this engine
and stays in the separate legacy `mehungry-extract` path.

---

## 5. Disease ↔ compound ↔ food traceability — feasibility

**Verdict: feasible, and the data model is already 80% shaped for it.** Split into two problems:

### 5a. The linking layer — compound → food source *(HIGH feasibility)*
The vocabulary already distinguishes `chemical`/`nutrient` from `food` (`dictionaries.json`). What's
missing is **composition edges**: `curcumin —found_in→ turmeric`, `omega-3 —found_in→ fish`,
`sulforaphane —found_in→ broccoli`. This is a *checked-in, versioned ontology*, exactly the pattern
already used for funders/institutions/countries (`vocab/funders.json` etc., loaded in
`vocab/__init__.py:40`). Sources: **FooDB**, **Phenol-Explorer**, **USDA FoodData Central**. Add a
`load_food_sources()` loader and a `foods.py` stage that materializes the edges. No inference, fully
deterministic, fits the engine perfectly.

### 5b. The bridge — "compound helps disease" ⇒ "food containing it helps disease" *(MODERATE, needs a policy call)*
Most papers study **compounds/nutrients**, not whole foods. To answer "which *foods* help UC in
remission" you must bridge a compound-level finding to the foods that contain it. That bridge is
**inference**, which the engine currently forbids ("adds no new evidence," `synthesis.py`). Two clean
ways to honor the principle:

1. **Derived, clearly-labeled layer.** Emit food-level conclusions in a separate section flagged
   `derived_via: composition_ontology`, with provenance = (the compound claim's evidence span) +
   (the `found_in` ontology edge + version). The user sees exactly how the leap was made and can
   trust or discount it. **Recommended.**
2. **Text-only.** Only report food↔disease relations actually stated in text (rare), no bridging.
   Safer, far lower recall.

Recommend #1: it delivers the "which foods help" answer the product wants *without* silently
inventing evidence — the ontology edge *is* the provenance for the leap.

### 5c. Effort
- 5a: small — a new `foods.<json>` + loader + a stage, no new NLP.
- 5b: moderate — a new derived conclusion type in `synthesis.py`/`api/models.py`, plus the
  labeling/provenance discipline.
- Prerequisite: A3 (a real food/compound vocabulary) — otherwise there's almost nothing to link.

---

## 6. Suggested sequencing

Maps onto the existing per-phase structure (`docs/phases/`):

| Order | Change | Files | Payoff |
|---|---|---|---|
| 1 | **A3** grow vocabulary (diseases, compounds, foods, nutrients) | `vocab/dictionaries.json` | recall — unblocks everything |
| 2 | **A1** disease-state/context qualifier | new `context.py`, `observations.py`, `claims.py`, `synthesis.py` | remission vs flare separation (the headline) |
| 3 | **A2** benefit/harm axis | new pure module (like `assessment.py`), `api/models.py` | "helps" / "caution" phrasing |
| 4 | **5a** compound→food ontology | `vocab/foods.json`, `vocab/__init__.py`, new `foods.py` | food traceability graph |
| 5 | **A4/A5** outcomes, dose, wider window | `relations.py`, `observations.py` | richer, more complete claims |
| 6 | **5b** derived food-level conclusions | `synthesis.py`, `api/models.py` | "which foods help UC" |
| 7 | **B1/B2/B3** frame model, dep-parse, (optional) LLM generator | broad | the sophistication ceiling |

Steps 1–3 alone move the output from "entity co-occurrence" to "state-stratified, direction-labeled,
provenanced findings" — i.e. recognizably the target — using only rules and dictionaries, before any
local model is introduced. The provenance guarantee (every fact traces to a span) holds throughout.

---

## 7. The decision that gates the ceiling — now made

Everything in Tier A and 5a/5b is achievable with pure rules and dictionaries. That gets you the
target *shape* but recall/precision stay bounded by hand-written rules.

Reaching genuinely sophisticated, robust extraction across arbitrary papers (Tier B3) means allowing
a parse-model or embedding model as a **span-validated candidate generator** — the pattern scispaCy
already follows here. **This decision has now been made: determinism is dropped, and local
non-deterministic models are permitted.** The provenance guarantee survives; strict
byte-reproducibility does not (you get *versioned*, auditable extraction instead). The one line that
remains is offline-vs-online: models run **locally**, on what Python offers; hosted APIs stay in the
separate legacy path.

**With that settled, the sequencing is a pace call, not a values call.** Recommendation unchanged:
do Tier A + 5a/5b first (fast, safe, big visible jump), then layer in a local model (B3) with real
output in hand.
