# Phase 12 — Qualified entities (text-derived entity-modifier edges)

**Goal:** stop letting an entity head lose its meaning when the sentence restricts it. A mention
of *"dysbiosis of the gut microbiome"* is stored today as the bare concept `DIS:dysbiosis` — the
restrictive prepositional phrase *"of the gut microbiome"* is dropped, so the rendered claim
*"IBD — associated_with — Dysbiosis"* reads as under-specified and the same node silently absorbs
*"oral dysbiosis"*, *"vaginal dysbiosis"*, etc. This phase attaches a set of **typed restrictive
modifiers** to an `EntityMention`, each modifier's value normalized to *its own* concept and each
carrying its own `EvidenceRef`. It is the entity-level analogue of the Phase 6 qualifier layer: a
*condition on meaning, not an assertion* — no polarity, never dropped, fully provenanced.

The modifier is a **text-derived concept→concept edge** —
`dysbiosis —localized_in→ gut microbiome` — the read-from-text sibling of the curated
`found_in` composition edges in [Phase 8 / Concept 17](phase-8-concept-graph.md). Same "edge
between two concepts" shape; different provenance (a source *span*, not an ontology version).

**Scope — shipped in two slices.** **A1** (representation, provenance, display): capture, normalize,
persist, and surface the modifier, leaving the claim key untouched. **A2** (discrimination): fold a
*key-bearing* endpoint modifier signature into the claim key and the cross-paper synthesis key, so a
localized entity pair (*"dysbiosis of the gut microbiome"*) forms its own claim/conclusion instead of
merging into the bare pair. A1 was built first (the schema stabilized before the key changed, exactly
as Phase 6 preceded Phase 7); both are now in the tree. Because each signature is appended to the key
**only when non-empty**, every modifier-free claim id is byte-identical to pre-Phase-12.

**Prerequisite:** Phases 1–3 (entities, offset contract, `EvidenceRef`) and Phase 10's parse
binder (`knowledge/parse.py`, `SentenceParse`), whose `nmod`/`prep` arcs off an entity's anchor
token are the primary detector here. Modifiers hang off the existing `EntityMention`
(`knowledge/entities.py`) and reuse the offset contract and `EvidenceRef`
(`knowledge/provenance.py`) unchanged.

## Why this is blocked today

`entities.extract` (`entities.py`) emits an `EntityMention` from a single span — either a scispaCy
span or a dictionary match — normalized against the vocabulary. The span is exactly the head
noun: BC5CDR tags only *"dysbiosis"*, and the dictionary matcher is whole-word/longest-first over
surface forms, so a prepositional phrase after the head is never part of the mention. The
information *"of the gut microbiome"* is present in the parse (an `nmod`+`case` child of the
`dysbiosis` token, resolvable to the `gut microbiome` concept, which is already in the vocabulary
at `vocab/dictionaries.json`) but nothing reads it. Widening the entity span is **not** the fix:
*"dysbiosis of the gut microbiome"* is not a vocabulary surface form, so it would fall to
`unmatched` and produce no claim — the head must keep resolving. The fix is a second, typed slot
on the mention that records the modifier as its own resolved concept.

## Deliverables

### Modules
- `knowledge/modifiers.py` — the `EntityModifier` model, the controlled `ModifierRelation` enum, a
  deterministic-floor + parse-based extractor
  (`extract(document, sentence, mentions, parse=None, *, use_model=True) ->
  dict[str, list[EntityModifier]]`, keyed by `mention_id`), a `registry()` of its cue rules, and a
  `signature(modifiers) -> str` helper (**empty when there are no modifiers**) reserved for the A2
  claim-key work. Each modifier normalizes its value against the **entity** vocabulary
  (`normalize.normalize`) when it can and is kept `unmatched` with its surface span otherwise —
  never dropped.
- `knowledge/parse.py` — a `SentenceParse.modifier_phrases(anchor_tok)` helper returning
  `(preposition, pobj_token)` for attributive prep-phrase modifiers of a **noun** anchor,
  mirroring the existing `condition_tokens` (which hang off a *verb*). Keeps all parse navigation
  in one module.

### Data model
- `ModifierRelation` enum (start small — grow one type per need): `localized_in` first
  (`… of the gut microbiome`, `… in the colon`); `part_of`, `derived_from` reserved as extension
  points.
- `EntityModifier`: `relation` (enum value), `preposition` (the cue surface, for audit),
  `value_concept_id` (optional — set when the object normalizes), `value_text` (the surface),
  `value_type` (entity type of the resolved modifier concept), `status`
  (`normalized`/`unmatched`), `evidence_refs: list[EvidenceRef]`, `rule_id`, `rule_version`. There
  is deliberately **no polarity** — a modifier restricts meaning, it does not assert anything.
- `EntityMention` gains `modifiers: list[EntityModifier] = []`. Defaulted, so all existing
  construction sites and stored rows stay valid and the mention's own id is unchanged.

### Relation typing (never guess from the preposition alone)
- `vocab/modifier_relations.json` (with a `MODIFIER_RULES_VERSION`) maps
  `(preposition, object_entity_type) → ModifierRelation`, e.g. `(of|in|within, microbiome|anatomy)
  → localized_in`, with a generic recorded-but-non-discriminating fallback for other `of`-phrases.
  `of` is ambiguous ("dysbiosis **of** the gut microbiome" vs "reduction **of** inflammation" vs
  "risk **of** cancer"); the relation is decided by the resolved object's type, not the word.

### DB tables
- `entity_modifiers` (columns: `mention_id` FK → `entity_mentions.mention_id`, `relation`,
  `preposition`, `value_concept_id`, `value_text`, `value_type`, `status`, `evidence` (JSON inline,
  mirroring `observation_qualifiers`), `rule_id`, `rule_version`, `run_id`). Index on
  `(document_id, relation, value_concept_id)`. Add via `create_all` + `SCHEMA_VERSION` (README
  rule 7); export from `db/__init__.py`; extend the delete-then-insert `persist_entities` so a
  re-run replaces modifier rows too (idempotent). Register the modifier cue rules in
  `extraction_rules` like the qualifier/clause rules.

### CLI / Query
- `query.py` renders an endpoint's normalized modifiers inline, so the observation from the report
  reads: *"IBD — associated_with — Dysbiosis [localized_in: gut microbiome] · asserted"*. Return
  modifiers on the mention payloads (`list_*_for_document`, `explain_*`). *(This surfacing reflects
  a new concept, so it is **not** the REST-only exclusion in CLAUDE.md rule 1 — `docs/concepts.md`
  is updated.)*

### Pipeline wiring
- `pipeline.extract_document` and `entities.analyze_document` (the standalone `mehungry analyze`
  stage) build one `SentenceParse` per sentence (reusing the parse the relation binder already
  needs) and call `modifiers.extract`, assigning results onto the mentions **before** persist. On
  `use_model=False` the deterministic floor runs instead.

## Detection

- **Primary (parse).** For each mention, take its anchor token (`SentenceParse` already maps
  mention → anchor), walk `modifier_phrases`, and resolve each prep-phrase object's subtree via
  `normalize.normalize`. Build one `EntityModifier` per resolved (or unmatched) object.
- **Deterministic floor (`use_model=False`).** Operate on already-extracted mentions — no parser:
  for consecutive mentions `m1, m2` in a sentence where the exact gap
  `document.text[m1.end_char:m2.start_char]` matches `^\s+(of|in|within)(\s+the)?\s+$`, attach
  `m2` as a modifier of `m1`. Reuses existing spans; deterministic.

### Two guards against collisions with existing machinery
1. **vs. Phase 6 qualifiers.** A condition like *"in remission"* normalizes to a **disease-state**
   concept, not an entity concept; the modifier detector only fires when the object normalizes
   against the **entity** vocabulary. Normalization target is the discriminator — *"remission"*
   stays a qualifier, *"gut microbiome"* becomes a modifier. No overlap.
2. **vs. the `risk of X` special case.** When the anchor is the `risk` trigger whose of-phrase is
   already consumed as a relation endpoint (`parse.risk_objects`), skip it so *"risk of cancer"* is
   not double-counted as both an endpoint and a modifier.

## Provenance constraints (offline; determinism optional)

- **Text-derived, span-bearing.** Every `EntityModifier` carries ≥1 `EvidenceRef` (`EXACT_SPAN`
  over the connecting phrase `document.text[prep_start:pobj_end]`) plus `rule_id` + `rule_version`.
  No modifier without provenance (README rule 2). This is the deliberate contrast with the
  `found_in` edges (Phase 8), which have ontology provenance and no span.
- **Never drop.** An object that doesn't normalize is kept with `status="unmatched"` and its
  surface, never discarded.
- **Additive & backward-compatible.** No claim key changes in this phase; a mention with no
  modifier behaves exactly as pre-Phase-12, and its id is unchanged. The deterministic floor
  keeps the run-twice byte-equivalence property on `use_model=False`.
- **Offset contract** unchanged — modifier spans are absolute offsets into canonical `text`.

## Suggested steps

1. Add `ModifierRelation` + `EntityModifier` in `knowledge/modifiers.py`; add `modifiers` to
   `EntityMention`.
2. Add `vocab/modifier_relations.json` + `MODIFIER_RULES_VERSION`; ensure common site/anatomy
   objects resolve in the entity vocabulary (add a few if missing).
3. Add `SentenceParse.modifier_phrases`; write the parse-based extractor and the model-free floor,
   with both guards.
4. Table + export + persist helper (delete-then-insert) + rule registry.
5. Wire extraction into `pipeline.extract_document` and `entities.analyze_document`; render
   modifiers in `query.py`.
6. Tests, then stop.

## Tests to add (gate)

- **Floor extraction (no model):** *"dysbiosis of the gut microbiome"* → mention `DIS:dysbiosis`
  with one `localized_in` modifier resolving to the `gut microbiome` concept; every span
  reconstructs from offsets.
- **Guard vs qualifier:** *"beneficial in remission"* → a `disease_state` **qualifier**, and **no**
  modifier.
- **Guard vs risk-of:** *"reduced the risk of cancer"* → cancer is a relation **endpoint**, with
  **no** spurious modifier.
- **Unmatched:** *"dysbiosis of the foobar"* → a modifier kept with `status="unmatched"` and its
  surface retained.
- **Backward compatibility:** a corpus with no modifiers → claim ids byte-identical to
  pre-Phase-12; a mention with no modifier has an empty `modifiers` list and an unchanged id.
- **Determinism (floor):** run twice on `use_model=False` → identical mentions and modifiers.

## Extension points

- **A2 — discrimination (shipped).** `claims.normalize` folds `modifiers.signature(...)` (empty when
  none) for each endpoint into the grouping key + `_claim_id`, and `synthesis` folds the same
  signature (via `signature_from_dicts`) into its cross-paper key; the deduped endpoint modifiers
  persist to `claim_modifiers` and surface on the claim payloads. Only `localized_in` is key-bearing
  (the generic `qualified_by` never splits claims), and because the signature is empty otherwise,
  every unmodified claim id stays byte-identical — the same backward-compat property Phase 6 relied on.
- **Adjectival modifiers.** `DIS:dysbiosis` currently lists *"gut dysbiosis"* as a surface form, so
  the single-word adjectival form is pre-collapsed onto the bare concept; this phase only recovers
  the *prep-phrase* form. De-conflating adjectival *"oral/vaginal dysbiosis"* is a separate vocab
  decision.
- **More relations.** `part_of`, `derived_from`, `measured_in`, each a new `(preposition,
  object_type)` cue row.

## Done criteria

`mehungry extract` and `mehungry analyze` produce entity mentions that carry normalized restrictive
modifiers, each traceable to the exact prep-phrase and rule that set it, resolving to their own
concepts; the rendered observation shows *"Dysbiosis [localized_in: gut microbiome]"*; no existing
claim id changes. **A2:** a localized endpoint pair forms its own claim *and* its own cross-paper
conclusion, while an unmodified claim's id is byte-identical to pre-Phase-12; `claim_modifiers` rows
persist the per-endpoint modifiers. Bump `RULESET_VERSION`, `EXTRACTOR_VERSION`, `SCHEMA_VERSION`
(and `SYNTHESIS_VERSION` for A2's grouping-key change) in `knowledge/__init__.py`. `docs/concepts.md`
gains a section for the qualified-entity concept (CLAUDE.md rule 1).
