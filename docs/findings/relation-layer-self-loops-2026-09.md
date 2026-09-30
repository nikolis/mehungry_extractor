# Findings — relation-layer precision, via self-loop analysis (2026-09-26)

**Scope.** An investigation into why the extractor produces self-referential relations
(`Inflammatory bowel disease — associated_with — Inflammatory bowel disease`,
`Protein — increases — Protein`, `Remission — causes — Remission`). The self-loops turned out to
be a free, label-free diagnostic of the whole relation layer's precision. This document records
the state of the system as of the corpus in `data/mehungry.sqlite` (215 claims across ~56
one-document-per-run ingestions). The metric that produced these numbers is documented in
[relation-quality-metrics.md](../metrics/relation-quality-metrics.md); the pipeline itself was
**not** changed as part of this investigation.

## Headline

**67 of 215 claims (31%) are self-loops** — provably wrong by construction. At the observation
layer it is 73/224 (33%). This is a floor on the false-positive rate; with a detectability
(`p_alias`) of 0.373, the modelled full false-positive rate is on the order of **~80%**. Treat the
31% as fact and the ~80% as a caution-flag estimate — but even the floor alone says the relation
layer emits a large fraction of invalid claims.

## Predicate distribution

The self-loop share is **remarkably flat across predicates (~23–39%)** — it is not one rogue rule.

| predicate | self-loops / total | share |
|---|---|---|
| associated_with | 16 / 42 | 38% |
| increases | 16 / 41 | 39% |
| causes | 9 / 33 | 27% |
| improves | 8 / 31 | 26% |
| decreases | 7 / 31 | 23% |
| increases_risk | 3 / 8 | 38% |
| worsens | 3 / 8 | 38% |
| prevents | 3 / 12 | 25% |
| no_association | 1 / 3 | 33% |
| reduces_risk | 1 / 6 | 17% |

The absolute counts track cue breadth and frequency (`associated_with`, `increases` lead), not
predicate semantics. The rule-priority ordering in `relations.py` (specific-before-generic) does
not help here, because the defect is not *which* rule fires — it is *which two mentions get
paired*. **The problem is upstream of the rules, in the shared pairing/normalization layer.**

## Mechanism decomposition (of the 73 self-loop observations)

| bucket | count | root cause |
|---|---|---|
| `long_window` | 47 | pair formed across a clause / sentence-segmentation error |
| `topic_repeat` | 19 | topic entity repeated as context around an unrelated cue (incl. nominalizations) |
| `sub_phrase` | 7 | compound / head-noun match (`dietary fiber` / `fiber`) |
| `list_like` | 0 | pure coordinate enumeration (rare in isolation; usually folds into the above) |

**`long_window` dominates.** The single biggest driver is the consecutive-pair scan in
`observations.py` reaching across clause and sentence boundaries. Self-loop sentences are ~54%
longer than the corpus median (234 vs 152 chars), and `no_association` self-loops occur in
multi-sentence blobs — i.e. sentence segmentation is failing and inflating the connecting window.

## Root-cause taxonomy (grounded in real sentences)

Every self-loop, and by extension a large class of *invisible* cross-concept false positives,
comes from one root limitation: **the layer validates that a cue appears between two mentions, but
never that the cue grammatically governs those two mentions** (a deliberate consequence of the
parser-free, byte-reproducible design). The observed forms:

- **Topic/context repetition** — *"…marker of IBD-associated dysbiosis… its negative association
  with disease activity… individuals with IBD."* → `IBD associated_with IBD`. The real relation is
  *F. prausnitzii ↔ disease activity*; IBD is scenery. The true endpoints often did not normalize,
  so the scan reached past them to the repeated topic term.
- **Cross-clause / cross-sentence pairing** — *"patients with IBD had a 44% increased risk of
  developing T2D, while patients with T2D had a 40% increased risk of developing IBD."* →
  `IBD increases_risk IBD`. Two genuine relations (IBD→T2D, T2D→IBD) collapsed by pairing the first
  IBD with the last across the whole sentence.
- **Coordination / enumeration** — *"clinical response, clinical remission, endoscopic improvement
  and remission."* → `clinical remission improves remission`. A list item read as a predicate.
- **Nominalization** — *"limited evidence exists regarding IBD prevention… maintaining IBD
  remission."* → `IBD prevents IBD`. The regex `\bprevent\w*\b` matched the noun "prevention".
- **Sub-phrase head-noun match** — *"hepatic glucose production… impaired glucose uptake."* →
  `glucose causes glucose`. The vocabulary matched a generic head noun inside two different
  compound noun phrases.

## Why this matters beyond the self-loops

Self-loops are the subset of spurious relations where both endpoints happen to alias to one
concept — the only subset detectable without labels. The five mechanisms above fire just as often
between two *distinct* concepts, producing plausible-looking claims that pass review silently. The
31% floor therefore understates the true error rate by roughly `1 / p_alias` (~2.7×). The
relation layer's precision is the dominant quality risk in the current system.

## Debugging fixtures

Mid-size documents with high self-loop rates are the best test cases (enough claims to be
representative, enough self-loops to diagnose): `pmid_42638808` (4/7, 57%), `pmid_42064993`
(3/5, 60%), `pmid_40900671` (3/5, 60%), `pmid_42523904` (3/6, 50%), `pmid_42602224` (3/8, 38%).

## Implications for future work (not acted on here)

Read off the `mechanism` histogram, in priority order:

1. **`long_window` (dominant)** → add a connecting-window distance cap in `observations.py`, and
   fix sentence segmentation so pairs cannot span sentences. Cheapest, highest-impact.
2. **`topic_repeat`** → requires argument attachment / clause scope to know the cue's real
   arguments (see [phase-7-clause-scope.md](../phases/phase-7-clause-scope.md)); the parser-free
   design is the ceiling here.
3. **`sub_phrase`** → tighten vocabulary granularity so generic head nouns (`protein`, `glucose`,
   `fiber`) do not match inside larger phrases.
4. A **self-loop guard** (`subject_concept_id != object_concept_id`) would remove the visible
   symptom cheaply — but doing so *without* first logging the rate would discard the free precision
   signal. Suppress at the query/presentation layer, keep measuring at the observation layer.
