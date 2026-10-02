# Project rules

## RULE 1 — Keep `docs/concepts.md` in sync with every change (MANDATORY)

**Before considering any task in this project complete, you MUST update
[`docs/concepts.md`](docs/concepts.md) to reflect the change you made.**

This applies to **every** change — code, schema, pipeline stages, data models,
CLI, configuration, behavior — under **any** prompt. Updating `docs/concepts.md`
is part of the change, not an optional follow-up. A change is not "done" until
`docs/concepts.md` has been brought back into agreement with it.

`docs/concepts.md` explains the **ideas and the "why"** behind the engine, so the
update must be conceptual, not a changelog: if a change alters what a concept is,
why it is done that way, or its alternatives/extension points, edit the relevant
section so the document stays true. If a change introduces a genuinely new concept,
add a section for it.

### The only exclusion: REST API–only changes

The **single** exception is a change whose effect is confined to the **REST API
layer** (the `mehungry_extractor/knowledge/api/` HTTP surface and its own docs in
[`docs/api.md`](docs/api.md)) — request/response shapes, routes, serialization,
status codes, etc. — that does **not** touch any underlying concept, pipeline
behavior, or data model. Those may be excluded from the `docs/concepts.md` update
requirement.

If a change touches the REST API **and** anything conceptual, it is **not**
excluded — update `docs/concepts.md`.

When in doubt, update `docs/concepts.md`.

## RULE 2 — Keep `docs/api.md` in sync with every REST API change (MANDATORY)

**Before considering any task complete, you MUST update
[`docs/api.md`](docs/api.md) to reflect any change to the REST API layer
(the `mehungry_extractor/knowledge/api/` HTTP surface).**

This applies to **every** REST API change — routes/endpoints, request and response
shapes, field additions/removals/renames, serialization, status codes, error
responses, etc. Updating `docs/api.md` is part of the change, not an optional
follow-up. A REST API change is not "done" until `docs/api.md` has been brought
back into agreement with it.

This rule is independent of RULE 1: a REST-API-only change is excluded from the
`docs/concepts.md` requirement but is **still** subject to this one. A change that
touches both the REST API and a concept must update **both** `docs/api.md` (RULE 2)
and `docs/concepts.md` (RULE 1).

## RULE 3 — Keep `docs/observer_ui.md` in sync with every UI change (MANDATORY)

**Before considering any task complete, you MUST update
[`docs/observer_ui.md`](docs/observer_ui.md) to reflect any change to the Stage
Observer web app (the `webapp/` frontend).**

This applies to **every** UI change — panels/tabs, how a stage's output is rendered,
components, frontend structure, build/run steps, conventions, etc. Updating
`docs/observer_ui.md` is part of the change, not an optional follow-up. A UI change
is not "done" until `docs/observer_ui.md` has been brought back into agreement with it.

This rule is independent of RULES 1 and 2. A change that touches the UI **and** the
REST API must update **both** `docs/observer_ui.md` (RULE 3) and `docs/api.md`
(RULE 2); a change that also touches a concept must update `docs/concepts.md` (RULE 1)
as well. A UI-only change (no concept, pipeline, data model, or REST API effect) is
excluded from RULES 1 and 2 but is **still** subject to this one.
