# Gold evaluation — first baseline (2026-09)

First measured precision/recall of the extraction pipeline against hand-labeled ground truth. Seed
gold set: **2 papers, 6 labeled sentences, 10 scorable expected observations**
(`tests/gold/observations.json`, PMIDs `42676636` and `42626304`). Measured with
`audit.render_gold_eval` over a fresh deterministic extraction (`use_model=False`).

## Headline

```
correct 2   field_error 2   missed 6   spurious 2      (expected 10, produced 6)

PRECISION   strict 33%   lenient 67%
RECALL      end-to-end strict 20%      achievable strict 40%   (achievable lenient 80%)
F1 (strict) end-to-end 25%             achievable 36%
```

- **Precision is low but not catastrophic**: of 6 observations emitted in labeled sentences, 2 are
  exactly right, 2 have the right entities but a wrong label, 2 are spurious.
- **Recall is the bigger problem**: only 2 of 10 expected observations are captured exactly. The
  end-to-end/achievable split (20% vs 40%) shows **half the recall loss is vocabulary coverage** and
  half is relation/clause logic.

## Failure-mode histogram (root cause → fix priority)

```
vocab_gap                5      ← entities not in the 25-concept vocabulary
spurious                 2      ← argument mispairing (see below)
coordination_missing     1      ← one sentence, ≥2 observations
hedging_missed           1      ← asserted where it should be uncertain
wrong_polarity           1      ← direction inverted
```

## Worked examples (the 6 labeled sentences)

1. **Correct.** `IBD — associated_with — dysbiosis` (PMID 42676636). Produced `dysbiosis`, gold
   `dysbiosis of the gut microbiome`: aligned at concept level (`DIS:dysbiosis`); the lost modifier
   is an `object_granularity` note, not an error.

2. **`hedging_missed` (field_error, certainty).** *"…whether these shifts in the gut microbiome are a
   cause of IBD … without establishing clear causality…"* → emitted `gut microbiome — causes — IBD`
   as **asserted**; gold is `insufficient_evidence`. A false-confidence causal claim. Fix in
   `negation.py`.

3. **`coordination_missing` + `wrong_polarity`.** *"microbial dysbiosis is closely linked to disease
   activity **and** may recover during remission."* Two clauses:
   - `dysbiosis — associated_with — disease activity` → **missed** (`disease activity` out of vocab,
     and the coordination isn't split).
   - `dysbiosis — associated_with — remission` should be **negative** (recovers in remission); the
     pipeline emits **positive** → field_error(polarity).

4. **`vocab_gap` + argument mispairing.** *"…Lachnospiraceae are key … butyrate-producing bacteria …
   their absence has been linked to IBD…"* True subject `Lachnospiraceae absence` is out of vocab →
   **missed**; the pipeline instead pairs the modifier-embedded `butyrate` with IBD → **spurious**.

5. **`coordination_missing` (achievable).** *"exclusive enteral nutrition … similar efficacy … for
   clinical remission **but** superior in achieving mucosal healing."*
   - `EEN — achieves — mucosal healing` → **correct**.
   - `EEN — achieves — clinical remission` → **missed** — and both endpoints are in vocabulary, so
     this is a pure logic recall miss (the comparative's first target is dropped).

6. **`vocab_gap` ×3 + argument mispairing.** *"Environmental exposures — including antibiotic use and
   a high-fat, low-fiber diet — … worsen dysbiosis, reduce SCFA production…"* The three real causal
   subjects are all out of vocab → 3 **missed**; the pipeline emits `dysbiosis — decreases — SCFA`
   (two adjacent downstream mentions) → **spurious**.

## Conclusions — where the leverage is

Ranked by how many failures each fix removes, and cross-checked against the label-free self-loop
findings (`relation-layer-self-loops-2026-09.md`), which independently point at the same
pairing/segmentation layer:

1. **Vocabulary coverage (5/10 misses).** The single biggest recall lever. Adding
   `Lachnospiraceae`, `environmental exposures`, `antibiotic use`, `microbial imbalance`,
   `disease activity`, etc. lifts end-to-end recall toward the achievable ceiling. Cheap, additive,
   low-risk.
2. **Coordination / comparative splitting (`clauses.py`).** Even with perfect vocabulary, achievable
   recall is only 40% — the dominant *logic* loss is one sentence that should yield multiple
   observations (example 5 is a clean in-vocab case). Same root as the self-loop `long_window`
   mechanism.
3. **Argument pairing / cue governance (`observations.py` + `relations.py`).** The 2 spurious
   observations both come from pairing the entity nearest a cue rather than the grammatical argument
   (butyrate; dysbiosis→SCFA). This is the precision lever and matches the `topic_repeat` self-loop
   mechanism.
4. **Certainty & polarity cues (`negation.py`).** Lower volume here (1 each) but high-stakes:
   over-asserting hedged causality (example 2) and inverting recovery-in-remission (example 3)
   produce confidently-wrong claims.

## Caveats

- The seed set is tiny (10 observations); these rates are directional, not statistically strong.
  Grow the gold set (see `docs/metrics/gold-eval-metrics.md`) before treating any number as a target.
- One of the originally-supplied examples (the ESPEN omega-3/omega-6 sentence) is **not present in
  the cached corpus text** for PMID 42676636, so it is excluded from the gold set. `omega-3 fatty
  acids` / `omega-6 fatty acids` *are* in the vocabulary — if that sentence is added to the corpus,
  it becomes an achievable test of `compound_subject` handling (subject = "high omega-3 and low
  omega-6", not the nearest single entity).
