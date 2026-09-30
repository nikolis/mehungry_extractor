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
