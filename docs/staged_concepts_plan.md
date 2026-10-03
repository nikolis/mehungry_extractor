# Plan — Staged concepts & UI-gated promotion

Status: **design / not yet implemented**. This plan challenges and generalizes the
Concept 9 rule that *"a claim can only form if both endpoints normalize."*

## Decisions baked in

- Provisional (STAGE-keyed) claims **form, but are flagged** (`provisional=true`) and
  excluded from synthesis.
- Promotion of a staged candidate **auto re-extracts** the papers that referenced it.

## 0. Guiding invariant (the new Concept 9)

> A claim is **trusted** when both endpoints resolve against the **curated** vocabulary
> (built-in + overlay). A claim is **provisional** when an endpoint resolves only to a
> **staged candidate** — a `STAGE:`-namespaced id minted deterministically from an
> `unmatched` surface. Provisional claims form and carry `provisional=true`, but are
> excluded from cross-paper synthesis and are the input to a human review queue.
> Promotion of a staged candidate into the overlay (the permanent writable vocabulary)
> auto-re-extracts the papers that referenced it, at which point the provisional claim is
> reproduced as a trusted one and the staged id disappears.

`ambiguous` endpoints are **not** staged — they drop as today (the entity exists; the fix
is disambiguation, not a new concept). This preserves Concept 3: a provisional claim is a
*labelled*, gated assertion, never a silent one about an unknown entity.

## 1. `observations.py` — carry endpoint status

- Add `subject_status: str` and `object_status: str` to `Observation`
  (`normalized` / `ambiguous` / `unmatched`).
- Populate from the mention at the construction site (`observations.py:367–375`), where the
  mention's status is already available.
- **Why:** `claims.normalize` currently only sees `concept_id is None`; it must distinguish
  `unmatched` (stage) from `ambiguous` (drop).

## 2. `staging.py` (new) — the mint + candidate aggregation

- `staged_id(surface) -> str` → `f"STAGE:{_key(surface)}"` (reuse `normalize._key` so
  slugging matches the normalizer's own casefold/whitespace rules; identical surfaces merge).
- `aggregate_candidates(observations) -> list[StagedCandidate]` → group `unmatched`
  endpoints by `staged_id`, collecting surface variants, suggested entity_type (extractor
  hint), `support_count` (distinct observations + distinct papers), and example evidence
  refs / provisional claim ids.
- No new DB table — candidates are **derived** on read from persisted observations whose
  endpoint is `unmatched`.

## 3. `claims.py` — graded claim formation

- `Claim.provisional: bool = False`.
- In `normalize()`:
  - For each endpoint: if `concept_id` set → use it. Else if status is `unmatched` →
    substitute `staged_id(text)`, mark the claim `provisional`. Else (`ambiguous`) → drop.
  - Keep the key / `claim_id` hash machinery unchanged — a `STAGE:` id hashes like any
    other, so provisional claim ids are stable and reproducible.
- `NormalizeResult`: split into `dropped` (ambiguous / non-stageable) and `staged` (count of
  observations that produced a provisional claim). Keep `dropped` reported by the CLI; add
  `staged` beside it.
- Update the module docstring (currently asserts "can only form when **both** endpoint
  mentions normalized") to the graded rule.

## 4. Synthesis guard (Concept 15)

- In the set-arithmetic over normalized concepts, filter out any
  `concept_id.startswith("STAGE:")`. Provisional concepts never pollute topic clustering
  until promoted. One-line guard; note it in the synthesis code comment.

## 5. Overlay — a `dismissed` list

- Extend the overlay schema from `{concepts, removed}` to `{concepts, removed, dismissed}`
  in `vocab/__init__.py` (`_EMPTY_OVERLAY`, `save_overlay`; `load_concepts` unaffected).
- `dismissed` holds staged ids a reviewer rejected as noise, so they stop surfacing as
  candidates (and optionally stop forming provisional claims). Add
  `dismiss_staged(staged_id)` / `undismiss_staged`.

## 6. REST API (`api/vocab.py` + `api/models.py`)

- `StagedCandidate` model in `models.py`:
  `staged_id, surface, suggested_type, support_count, paper_count, surface_forms[],
  example_claim_ids[], example_refs[]`.
- `GET /vocab/staged` → ranked candidate list (desc by support), excluding `dismissed`.
- `POST /vocab/staged/{staged_id}/promote` → body is a `ConceptModel` (pre-filled
  client-side); validates the target `concept_id` is new, calls `upsert_concept`,
  `invalidate_caches`, then **auto re-extracts** the affected papers (§7), returns the new
  `ConceptRecord` + list of re-extracted pmids.
- `POST /vocab/staged/{staged_id}/dismiss` → adds to `dismissed`.
- *(RULE 2: all require `docs/api.md` updates.)*

## 7. Auto re-extraction on promotion

- Add a helper (likely in `api/service.py`) `papers_referencing_staged(staged_id) ->
  list[pmid]` — query observations whose `unmatched` endpoint slugs to that id.
- Promotion loops those pmids through the existing `run_extract` path. Because claims are a
  pure function of observations and `claim_id` is derived, the provisional claim is simply
  replaced by the trusted one with its real hashed id — nothing to migrate.
- **Open:** re-extract synchronously in the request (simple, slow for a widely-referenced
  surface) vs. enqueue. Default to synchronous, returning the pmid list; flag if fan-out is
  large.

## 8. UI (`VocabPanel.tsx` + `observer_ui.md`)

- New **Staged** section/tab in the Vocabulary panel: candidates ranked by support, each
  showing surface, suggested type, support/paper counts, and example claims.
- **Promote** → opens the existing add-concept form pre-filled (`canonical_name`=surface,
  `entity_type`=suggested, `surface_forms`=observed variants); reviewer confirms/edits id,
  submits to `/vocab/staged/{id}/promote`.
- **Dismiss** → `/vocab/staged/{id}/dismiss`.
- Provisional claims in `ClaimsPanel.tsx` get a `provisional` badge (parallel to the
  existing `origin` tags).
- *(RULE 3: `docs/observer_ui.md` updated.)*

## 9. Docs (mandatory per CLAUDE.md)

- **`docs/concepts.md`** (RULE 1): rewrite Concept 9's "both endpoints normalize" into the
  graded trusted/provisional/dropped rule; update the `claims.normalize` code excerpt and the
  `dropped`/`staged` description; add an "Alternatives / extension points" note on
  promotion-as-vocab-growth. Likely a short **new concept** for the staging + promotion
  lifecycle, cross-linked from Concept 3 and Concept 15.
- **`docs/api.md`** (RULE 2): the three new routes + `StagedCandidate` shape.
- **`docs/observer_ui.md`** (RULE 3): the Staged section and the provisional badge.

## 10. Tests

- `claims.normalize`: `unmatched` endpoint → provisional claim with `STAGE:` id;
  `ambiguous` → still dropped; `staged`/`dropped` counts correct; provisional `claim_id`
  reproducible across runs.
- `staging.aggregate_candidates`: merging by slug, support counts.
- Promotion round-trip: stage → promote → re-extract → trusted claim appears with real id,
  `STAGE:` claim gone.
- Synthesis excludes `STAGE:` ids.

## Open items (flagged, not blocking)

- §7: synchronous vs. queued re-extraction on promote.
- §5: whether a dismissed staged id should also suppress *future* provisional claim
  formation or only hide it from the review queue.
