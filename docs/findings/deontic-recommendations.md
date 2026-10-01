# Open question — representing recommendations (deontic statements)

**Status:** deferred design decision, not yet implemented. Recorded here so the question is not
re-derived from scratch each time it resurfaces. This was originally scoped as "Phase 4d" during the
Fix-4 coverage work; the code was *not* written because the right representation is a modelling
choice, not a mechanical extraction fix.

## The category

Some sentences state what *should* be done rather than what *is* — a **recommendation, guideline, or
advice**, not an empirical finding:

- "…guidelines **recommend reducing** the intake of red meat…"
- "…the diet is **suggested for** patients with the disease…"
- "…clinicians are **advised to encourage**…"

These are **deontic**: normative claims about action. They are common in clinical-nutrition prose
and are a distinct evidential category from the empirical relations the engine extracts today
(*"X reduces Y"*, *"X is associated with Y"*).

## Why the current model can't hold them

An observation/claim is a flat `(subject, predicate, object)` triple plus modality
(`polarity` + `certainty`) and typed qualifiers (Concept 8, Concept 9). A recommendation does not
fit this shape cleanly for two reasons:

1. **It is not an assertion.** *"A guideline recommends reducing red meat"* is not the empirical
   claim *"red meat decreases (something)"*. Mapping the embedded verb (*reducing* → `decreases`)
   the way the binder does today would make the engine **assert a fact the paper never stated** —
   exactly the kind of fabrication the rest of Fix 4 works to prevent. The deontic framing ("this is
   advice") is dropped, which is the opposite of the provenance-honesty the engine is built around
   (Concept 3).
2. **It is structurally nested.** The literal structure is *[recommender] recommends [reduce
   [thing]]* — a recommendation *about* an action *about* an entity. Any flat triple is a lossy
   projection of that, and there is more than one reasonable projection.

## The representation choices (the actual decision)

Two families, neither obviously right:

- **A — deontic predicates.** Add predicates that carry the normative meaning
  (e.g. a "recommended reduction/increase" of an entity, or an intervention "recommended for" a
  population). *Pro:* keeps advice cleanly separable from findings, so an empirical query ("what
  reduces X?") is not polluted by recommendations. *Con:* multiplies the predicate vocabulary — each
  empirical direction risks growing a deontic twin.
- **B — a deontic modality dimension.** Keep the empirical predicate and add a normative flag
  alongside `polarity`/`certainty` (a relation can be *asserted* or *recommended*). *Pro:* orthogonal
  and avoids new predicates; reuses the modality machinery. *Con:* touches the schema, the claim key,
  and serialization, and an empirical predicate is an awkward carrier for "recommend reducing intake".

There is also a **subject-convention** sub-question (is the subject the *recommender*, the *target
population*, or the recommended *intervention*?), and the observation that a "recommended-for"
targeting relation (intervention → population) is a genuinely new *relation type* regardless of how
A/B is answered.

## Guidance for whoever picks this up

Do **not** overfit to a couple of example sentences (the trap the first pass fell into). Decide the
category-level representation first — A vs. B, and the subject convention — then let the cue/binder
work follow from that decision. Until then, the honest behaviour is the current one: a sentence whose
real predicate is deontic and whose subject does not resolve is **dropped**, not turned into a
fabricated empirical claim (the Phase 4a non-entity-subject guard already ensures this for the
"recommend reducing X" shape, since the recommender/population subject is typically not an entity the
vocabulary names).

## Related

- Fix 4 coverage work and its measured gold: `tests/gold/curated_examples.json` (records `#8`, `#4`,
  `#2`, `#9` are marked `unscorable` — they need this decision or a risk-association predicate).
- The empirical-relation model these would extend: Concept 7 (relations), Concept 8 (modality),
  Concept 9 (observations vs claims) in `docs/concepts.md`.
