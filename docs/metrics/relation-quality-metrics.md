# Relation-quality metrics — self-loops as a label-free precision probe

**What this is.** A way to measure the precision of the deterministic relation layer
(`relations.py` → `observations.py` → `claims.py`) **without any hand-labelled ground truth**.
It works by counting *self-loops* — claims whose subject and object resolve to the same concept
(`Inflammatory bowel disease — associated_with — Inflammatory bowel disease`). A self-loop is
wrong by construction (a concept cannot stand in a directional relation to itself), so the rate at
which the pipeline emits them is a directly observable lower bound on its false-positive rate — and
the *distribution* of self-loops tells you which part of the pipeline to fix.

Everything here is a pure, offline, deterministic fold over already-extracted rows — no model, no
network, no new evidence — mirroring `claims.normalize`.

---

## Where it lives

| Layer | Symbol | Role |
|---|---|---|
| Compute | `knowledge/metrics.py` — `relation_quality(...)`, `aliasing_probability(...)` | Pure fold over claims/observations/mentions. |
| Query | `knowledge/query.py` — `relation_quality_report(engine, run_id=None)` | Assembles inputs from the store, returns a JSON-friendly dict. |
| Audit | `knowledge/audit.py` — `render_relation_quality(engine, run_id=None)` | Human-readable text report. |
| Tests | `tests/test_metrics.py` | Unit tests for the fold + buckets + an end-to-end store check. |

`run_id=None` aggregates the **whole store** (the corpus lens — useful because the current
ingestion is one-document-per-run). Pass an explicit `run_id` to scope to a single run (the lens
for a CI regression guard). Aggregating across runs mixes `ruleset_version`s; use a single
`run_id` when attributing a rate change to a ruleset change.

---

## How to use it

### Quick look (Python)

```python
from sqlalchemy import create_engine
from mehungry_extractor.knowledge import audit, query

engine = create_engine("sqlite:///data/mehungry.sqlite")

print(audit.render_relation_quality(engine))          # whole corpus
print(audit.render_relation_quality(engine, run_id))  # one run

report = query.relation_quality_report(engine)        # dict, for programmatic use / CI
```

### As a test / regression guard

The report is a dict, so a threshold assertion is a one-liner:

```python
def test_relation_layer_precision_floor(engine):
    r = query.relation_quality_report(engine)
    # Self-loops are wrong by construction: guard against regressions above today's baseline.
    assert r["self_loop_rate"] <= 0.32, r["by_predicate"]
```

Because the whole computation is deterministic, the numbers only move when the rules, the
vocabulary, or the corpus move — so a change in `self_loop_rate` between two runs of the *same*
ruleset is a real precision change, not noise. Store the per-`run_id` number and diff it across
runs to catch a rule edit or a vocabulary addition that made pairing worse.

---

## How to read the numbers

```
RELATION QUALITY  run * (all runs)
  self-loops        67/215 claims  (31%)   ← observed false-positive FLOOR
  p_alias           0.373                   (detectability)
  est. FP rate      ~83%                    ← floor / p_alias (model-based)
```

### `self_loop_rate` — the hard number

`self_loops / total_claims`. This is an **observed floor** on the false-positive rate: every one
of these claims is definitely wrong. Treat it as ground truth. Nothing about it is modelled or
assumed.

### `p_alias` — detectability

`aliasing_probability(...)` = the probability that two adjacent, both-normalized mentions in a
sentence already share a concept. It is computed from the mention table alone (the same
consecutive-pair scan the relation layer itself uses), independently of which relations fired, so
it is not circular. It answers: *if the pipeline emits a spurious relation, how often would it
happen to land as a visible self-loop rather than as a plausible-looking relation between two
different concepts?*

### `estimated_fp_rate` — the soft number

`self_loop_rate / p_alias`, clamped to `[0, 1]`. Self-loops are only the *detectable* fraction of
the spurious population. Dividing the floor by the detectability extrapolates to the full
false-positive rate **under the stated assumption** that spurious pairs alias at the same rate as
the background adjacent-pair population. That assumption is rough — treat this as an
order-of-magnitude indicator, not a measurement. Report the floor as fact and this as a caution
flag. If `p_alias` is undefined (no candidate pairs), this is `None` and only the floor is shown.

### `by_predicate` / `by_rule_id`

`(self_loops, total)` per predicate and per firing `rule_id`. Because `rule_id`/`version` map into
the `extraction_rules` registry, a spike is traceable to the exact cue. A *flat* self-loop share
across predicates (as observed today, ~23–39%) means the problem is **not** one rogue rule — it is
upstream of the rules, in the shared pairing/normalization layer. A *spiky* distribution would
point at a specific over-broad cue.

### `mechanism` — the actionable part

Each self-loop is bucketed by the cheapest signal that explains its root cause. These map directly
to the failure modes in the [Phase 3 relation model](../phases/phase-3-relations-claims.md), and
tell you *which shared layer to fix*:

| Bucket | Signal | Root cause | What it points at |
|---|---|---|---|
| `sub_phrase` | subject/object have different surface text but one concept | compound / head-noun match (`dietary fiber` / `fiber`, `glucose production` / `glucose uptake`) | **vocabulary granularity** — generic head nouns collapse distinct phrases |
| `long_window` | connecting text between the two mentions is long (`> LONG_WINDOW_CHARS`) | the pair was formed across a clause, or across a **sentence-segmentation error** | **pairing distance cap + segmentation** |
| `list_like` | connecting text is only separators/conjunctions (`and`, `or`, `,`) | a coordinate enumeration of endpoints read as a predicate | **coordination handling** |
| `topic_repeat` | the remainder: same surface, short window | the concept is the paper's topic, repeated as context on both sides of a cue that actually relates two *other* entities (also absorbs nominalizations like `prevention`/`association`) | **cue governance / argument attachment** (needs syntax) |

The `mechanism` histogram is the single most useful output: it converts "precision is low" into
"the dominant cause is X, so fix layer Y first." If `long_window` dominates, tightening the
regexes is wasted effort — the fix is a distance cap and better segmentation. If `sub_phrase`
dominates, it is a vocabulary problem.

---

## Interpreting the mechanism → efficiency link

The reason self-loops are worth measuring at all is that **the same mechanisms that produce them
also produce spurious relations between two *different* concepts**, which are invisible because
they look plausible. Self-loops are the tip of the iceberg that happens to be self-evidently wrong.
So:

- A high `long_window` count is not just "self-loops from long windows" — it is evidence that the
  consecutive-pair scan is reaching across clauses **in general**, mislinking many cross-concept
  pairs too.
- A high `sub_phrase` count implies the vocabulary is matching generic head nouns **everywhere**,
  not only where the two happen to collapse.
- `topic_repeat` implies the layer asserts a predicate wherever a cue sits between two mentions,
  regardless of whether the cue grammatically governs them — the core limitation of a parser-free
  design.

That is why the fix priorities are read off the `mechanism` histogram, and why the floor is a
*floor*: the true error rate is higher by roughly `1 / p_alias`.

## Tuning knob

`LONG_WINDOW_CHARS` (default `80`) in `metrics.py` sets the `long_window` threshold — roughly one
clause of scientific prose, against a corpus median sentence of ~150 chars. Lowering it moves
borderline self-loops from `topic_repeat` into `long_window`; it does not change any rate, only the
mechanism split.
