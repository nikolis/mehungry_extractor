# Gold-standard evaluation — precision/recall/F1 against hand-labeled observations

**What this is.** A way to measure the extraction pipeline's quality against a small, checked-in set
of **hand-labeled ground truth** (`tests/gold/observations.json`). It is the labeled counterpart to
the [self-loop precision probe](./relation-quality-metrics.md).

**Why it exists.** The self-loop metric is label-free and clever, but structurally limited: it can
only see relations that are wrong *by construction* (subject concept == object concept), so it
measures a **precision floor** and is **blind to recall** — it cannot tell you about observations the
pipeline *should* have emitted but didn't. Hand labels unlock exactly that:

| | Self-loop probe | Gold evaluation |
|---|---|---|
| Labels needed | none | a hand-labeled gold set |
| Precision | a *floor* (extrapolated via `p_alias`) | **true**, over labeled sentences |
| Recall | **cannot measure** | **measured** (missed observations) |
| Root cause | mechanism buckets (precision only) | failure-mode histogram (precision + recall) |
| Coverage | whole corpus | only labeled sentences |

Use them together: the self-loop probe watches precision across the *whole* corpus continuously; the
gold set gives a precise, recall-inclusive picture on a *curated* slice.

Everything here is a pure, offline, deterministic fold over already-extracted rows + the gold file —
no model, no network — mirroring `metrics.py` / `claims.normalize`.

---

## Where it lives

| Layer | Symbol | Role |
|---|---|---|
| Data | `tests/gold/observations.json` | Version-controlled hand labels, one record per sentence. |
| Compute | `knowledge/goldeval.py` — `score(...)`, `load_gold(...)` | Pure fold: align produced ↔ expected, bucket, compute P/R/F1. |
| Query | `knowledge/query.py` — `gold_eval_report(engine, gold_path=None)` | Assembles produced observations from the store, returns a JSON-friendly dict. |
| Audit | `knowledge/audit.py` — `render_gold_eval(engine, gold_path=None)` | Human-readable text scorecard. |
| CLI | `mehungry eval-gold [--gold PATH]` | Prints the scorecard. |
| Tests | `tests/test_goldeval.py` | Unit tests for the fold + an end-to-end baseline guard. |

---

## How to use it

```bash
mehungry eval-gold                          # text scorecard from the CLI
```

```python
from mehungry_extractor.knowledge import audit, query
from mehungry_extractor.knowledge.db import get_engine

engine = get_engine("data/mehungry.sqlite")
print(audit.render_gold_eval(engine))       # text scorecard
report = query.gold_eval_report(engine)     # dict, for programmatic use / CI
```

### As a regression guard

The report is a dict, so a guard is a few one-liners (see
`tests/test_goldeval.py::test_gold_eval_baseline_no_regression`). Guards are **one-sided** —
improvements are welcome, regressions fail — the mirror image of the self-loop guard's
`self_loop_rate <= 0.32`:

```python
c = query.gold_eval_report(engine)["counts"]
assert c["correct"] >= 2 and c["spurious"] <= 2 and c["missed"] <= 6
```

Because the whole computation is deterministic, the numbers only move when the rules, the vocabulary,
or the gold set move.

---

## How to read the scorecard

```
correct 2  field_error 2  missed 6  spurious 2   (expected 10, produced 6)
```

Every produced observation *in a labeled sentence* and every expected observation lands in one bucket
(produced observations in un-labeled sentences are ignored — neither credited nor penalized):

| Bucket | Meaning |
|---|---|
| **correct** (TP) | endpoints align and predicate + polarity + certainty all agree |
| **field_error** | endpoints align but ≥1 label differs (right entities + association, wrong label) |
| **missed** (FN) | an expected observation with no aligned produced observation |
| **spurious** (FP) | a produced observation with no aligned expected observation |

Endpoints "align" on concept id when both sides normalized, else on casefolded surface-text
containment (a gold span is often richer than the produced surface, e.g. `dysbiosis of the gut
microbiome` vs `dysbiosis`).

### Precision (vocabulary-independent, reported once)

- `precision_strict` = `correct / produced` — of what the pipeline emits, how much is exactly right.
- `precision_lenient` = `(correct + field_error) / produced` — gives credit for right entities.

### Recall — reported twice

- `recall_strict` (**end-to-end**) = `correct / expected` over **all** gold, including observations
  whose entities aren't in the vocabulary. This is the true end-to-end capability.
- `recall_achievable_strict` = the same over gold whose **both endpoints are in-vocabulary**. This
  isolates the relation/clause logic from vocabulary coverage.

The gap between them is the recall ceiling imposed *purely by vocabulary coverage*. A large gap says
"grow the vocabulary"; a low achievable recall says "fix the relation/clause logic."

### `failure_mode` — the actionable part

A root-cause tally over every non-correct outcome. The gold file supplies a human `failure_mode`
hint per expected observation (for misses and field errors); spurious produced observations bucket as
`spurious`; a field error with no hint is named `wrong_<field>`. This is the single most useful
output — it converts "quality is low" into "the dominant cause is X, fix layer Y first."

| Failure mode | Root cause / layer to fix |
|---|---|
| `vocab_gap` | entity not in the 25-concept vocabulary → `normalize.py` / `vocab` coverage |
| `argument_mispairing` (surfaces as `spurious`) | consecutive-pair scan grabs the entity nearest the cue, often modifier-embedded → `observations.py` pairing + `relations.py` cue governance |
| `coordination_missing` | one sentence should yield ≥2 observations → `clauses.py` coordination/comparative/list splitting |
| `hedging_missed` | hedged statement emitted as asserted → `negation.py` certainty cues |
| `wrong_polarity` / `wrong_certainty` / `wrong_predicate` | aligned endpoints, mislabeled → the relevant tagging layer |
| `object_granularity` | concept collapses a richer phrase (usually still `correct`; informational) |

---

## Growing the gold set

Add a record to `tests/gold/observations.json`:

```json
{
  "pmid": "<PMID>",
  "sentence_id": "<sentence_id>",
  "sentence_quote": "<the sentence, for humans>",
  "expected": [
    {
      "subject": {"text": "...", "concept_id": "<id or null>", "in_vocab": true},
      "predicate": "associated_with",
      "object":  {"text": "...", "concept_id": "<id or null>", "in_vocab": false},
      "polarity": "positive", "certainty": "asserted",
      "qualifiers": [],
      "failure_mode": "<hint>", "note": "<why>"
    }
  ]
}
```

- The paper must be in the cached corpus (`data/corpus/pmid_<PMID>/`); anchor by `sentence_id` (find
  it via `query.list_sentences_for_document`).
- Fill each endpoint's `concept_id` / `in_vocab` from `normalize.normalize(surface)` — `in_vocab` is
  `false` when the surface is `unmatched`/`ambiguous`.
- `predicate` / `polarity` / `certainty` must be valid enum values (`enums.py`); `load_gold` rejects
  typos loudly.
- Use `"unscorable": true` on an expected observation you want to record for completeness but that
  has no clean triple (e.g. a state with no object).

No code changes are needed to add papers — the scorer, report, and guard pick them up automatically.
