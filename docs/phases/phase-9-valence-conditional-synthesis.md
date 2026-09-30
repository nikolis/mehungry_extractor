# Phase 9 — Clinical valence & conditional synthesis

**Goal:** produce the actual target artifact — a **conditional, valence-bearing rule set** such as:

> *In UC **during remission**, dietary fiber is considered **beneficial** (SUPPORTED, 4 papers).
> In UC **during active flare**, fiber may be beneficial but some research reports **serious
> adverse events** (CONFLICTING, 2 support / 1 harm).*

Two additions on top of Phases 6–8: (1) a **valence** dimension (`beneficial` / `harmful` /
`neutral` / `mixed`) distinct from directional polarity, because recommendations consume valence,
not raw effect direction; and (2) **conditional synthesis** that partitions conclusions by
qualifier signature and reports valence + adverse-event flags per condition.

**Prerequisite:** Phases 6 (qualifiers), 7 (clause scope populates them), and ideally 8 (family
roll-up). This is the maturity layer that turns qualified claims into the rules the product wants.

## Why this is blocked today

- **Predicates encode effect direction, not clinical valence.** `enums.PREDICATES`
  (`enums.py:32`) are `increases`/`reduces_risk`/`causes`/… The motivating rules are stated in
  *valence* terms ("considered beneficial", "cause serious problems"). There is no field that says
  "this is good for the patient" vs "this harms the patient".
- **Synthesis groups on the bare triple.** Phase 6 changes the grouping key to include qualifiers,
  but the conclusion object still reports polarity, not valence, and doesn't specially surface
  adverse events as a distinct clinical signal.

## Deliverables

### Data model
- `Valence` enum: `beneficial`, `harmful`, `neutral`, `mixed`, `unknown`. Add `valence` to `Claim`
  (and carry it on `ConclusionModel`).
- `knowledge/valence.py` — a **derivation function**, `derive_valence(claim) -> Valence`, that is
  a pure, versioned function of `(predicate, polarity, object endpoint desirability)`. Endpoint
  desirability comes from a small annotation on the vocabulary: an outcome/disease concept is
  marked `desirable` (remission) or `undesirable` (flare, adverse event, mortality). Rules like:
  *reduces_risk + undesirable-endpoint + positive → beneficial*; *causes + adverse-event endpoint
  → harmful*; `contraindicated`/`associated_with_adverse_event` → `harmful` directly. Anything the
  rules can't resolve is `unknown` (never guessed).
- Mark endpoint desirability in `vocab/dictionaries.json` (`desirable: true/false/null` on
  outcome/disease/symptom concepts). Adverse-event concepts get an explicit `adverse_event` flag.

### Synthesis (`knowledge/synthesis.py`)
- Partition conclusions by qualifier signature (Phase 6 already changed the grouping key) and, per
  partition, report:
  - **valence** rollup (`beneficial` / `harmful` / `mixed`) alongside the existing
    SUPPORTED/REFUTED/CONFLICTING direction,
  - an explicit **adverse-event / safety flag** when any supporting claim is
    `associated_with_adverse_event` or `harmful` — surfaced prominently, never averaged into the
    valence,
  - the per-condition paper breakdown and certainty/study-design spread already computed in
    `_batch_facts` (`synthesis.py:125`).
- Render a **conditional rule view**: one line per `(subject, object, disease_state)` giving
  valence + direction + safety flag + evidence quote — the shape of the motivating example. This
  is the presentation layer over the conditional conclusions.

### Query / API / audit
- `find_claims(..., valence=...)`; expose valence + safety flags on `AnalyzeResponse`
  (`api/models.py`) and in `audit.render_claim`.

## Provenance constraints (offline; determinism optional)
- **Valence is derived, not asserted.** `derive_valence` is a pure function of already-provenanced
  facts (predicate, polarity, endpoint desirability from the versioned vocab). It introduces no new
  evidence; a claim's evidence is unchanged. The derivation is versioned so v1/v2 valence outputs
  are distinguishable.
- **Conflicts and harms are surfaced, never smoothed.** `mixed` valence and the safety flag are
  first-class; a beneficial-in-remission / harmful-in-flare pair must present as two conditional
  conclusions with opposite valence, not one blended verdict. This mirrors the existing
  CONFLICTING handling in synthesis.
- **`unknown` is honest.** If desirability or valence can't be derived, say `unknown` — do not
  assume beneficial (parallels the Phase 5 rule that `unknown` funding is *not* independence).
- **Read-side only.** Valence derivation + conditional synthesis add no new evidence rows;
  `SYNTHESIS_VERSION` marks the aggregation change.

## Suggested steps
1. `Valence` enum; add `desirable`/`adverse_event` annotations to the vocab.
2. `valence.py`: pure `derive_valence`; unit-test the truth table.
3. Carry `valence` on `Claim`/`ConclusionModel`.
4. Partition synthesis by qualifier signature; add valence rollup + safety flag.
5. Conditional rule view rendering; `find_claims` valence filter; API/audit exposure.
6. Tests; end of the Part II arc.

## Tests to add (gate)
- **Valence truth table:** `reduces_risk` + undesirable endpoint + positive → `beneficial`;
  `causes` + adverse-event endpoint → `harmful`; unresolved → `unknown`.
- **The full motivating rule:** a fiber/UC corpus split across remission and flare papers →
  two conditional conclusions: remission `beneficial`/SUPPORTED, flare `mixed`/CONFLICTING with a
  raised safety flag — each conclusion carrying an evidence quote that reconstructs from offsets.
- **No smoothing:** opposite-valence conditions never collapse into one verdict; the safety flag is
  never averaged away.
- **Determinism:** run twice → identical valence, conditional conclusions, and safety flags.

## Done criteria
`/analyze` returns condition-partitioned conclusions carrying clinical valence and explicit safety
flags, rendering the motivating "beneficial in remission / risky in active flare" rule with full
provenance on every part; `unknown` is returned honestly where derivation isn't possible. Bump
`SYNTHESIS_VERSION` and `VOCAB_VERSION`; `EXTRACTOR_VERSION`/`RULESET_VERSION` if extraction code
changed. Still offline and fully provenanced; any model used stays **local** (no hosted APIs).
