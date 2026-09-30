# Phase 10 — Parse-based relation binding (dependency arcs over flat text windows)

**Goal:** replace the brittle *flat consecutive-mention* relation binder with **dependency-parse
navigation**. Bind a relation's subject and object by walking the sentence's grammatical structure
(the `nsubj`/`dobj`/`nmod` arcs off a predicate verb) instead of by "which two entity spans happen
to be adjacent in the text". Expand coordination (`conj`/`cc`) into the correct cross-product of
observations, and treat prepositional-phrase modifiers as qualifier conditions rather than objects.
The *predicate* stays rule-based (a versioned verb-lemma → predicate map, evolving `relations.py`);
only *argument binding* moves to the parse.

**Prerequisite / what already changed to unblock this.** Two things landed before this phase:

1. **The scispaCy model was repaired and promoted to first-class** (`entities.py`): spaCy was
   pinned to `>=3.7.4,<3.8.0` so `en_ner_bc5cdr_md` loads, and the model is now the authoritative
   Chemical/Disease *detector* (Concept 6). With `use_model=True` (default) the model is
   **required** — a missing model raises `ModelUnavailableError` rather than silently degrading.
2. **The parser is already in the loaded model.** `en_ner_bc5cdr_md`'s pipeline is
   `['tok2vec', 'tagger', 'attribute_ruler', 'lemmatizer', 'parser', 'ner']` — a dependency parse
   is *already computed* on the object `entities._nlp()` returns. **No new model dependency.** The
   Phase-7 seam `observations._refine_with_parse` (`observations.py:298`) was written for exactly
   this but is a no-op pointed at a different, uninstalled model (`en_core_sci_sm`); Phase 10
   repurposes that seam onto the parser we already load.

## Why this is blocked today

`observations.extract` binds relations by scanning mentions in span order and testing only
*consecutive* pairs within a clause (`observations.py:157-169`, the `emit_pairs`/`zip(candidates,
candidates[1:])` loop). Promoting scispaCy exposed two failures this design cannot fix by tuning:

1. **Fragmentation by intervening spans.** In *"Dietary fiber was associated with reduced disease
   activity during remission"* scispaCy tags **"reduced disease"** as a span between the two real
   endpoints. Consecutive-pair binding then produces `fiber → "reduced disease"` (unmatched →
   dropped) and loses `fiber ⋯ remission` entirely. The Phase-7 "qualifier-transparent" workaround
   (`observations.py:171-175`) only covers *qualifier* mentions; a generic intervening span still
   severs the pair. Grammatically, "reduced disease" is an `amod`/`compound` **inside** the object
   noun's subtree — a modifier, not an argument — so a parse-based binder ignores it for free.
2. **Coordination produces garbage.** *"Vitamin D and calcium reduced the risk of colorectal cancer
   and osteoporosis"* should yield the 2×2 cross-product `{vitamin D, calcium} × reduces_risk ×
   {cancer, osteoporosis}` = four observations. Consecutive-pair scanning instead pairs
   `vitD→calcium`, `calcium→cancer`, `cancer→osteoporosis` — all wrong. The parse encodes exactly
   the right structure: `calcium` is `conj` of `D` under `cc "and"`; `osteoporosis` is `conj` of
   `cancer`.

Both are fundamental to *denser* entity detection (any additional entity, even a correctly-bounded
one, breaks a flat window), so they are not fixable with more regex.

## The design

**Parse-based binding when the model is on; the current regex flat-window as the model-free
floor.** This keeps the deterministic, offline `use_model=False` path (which every existing
deterministic test uses) byte-identical, and confines non-determinism to the model path — where
`concepts.md`'s guiding rule already permits it because every span still reconstructs from offsets.

### Modules

- **`knowledge/relations.py` (extend).** Add a **verb-lemma → predicate** table beside the existing
  connecting-text regexes. Map the lemmas the current cues already cover: `associate`→
  `associated_with`, `reduce`/`lower`/`decrease`→`decreases` (or `reduces_risk` when the object head
  is *risk*), `increase`/`raise`/`elevate`→`increases`/`increases_risk`, `cause`/`induce`/`lead`→
  `causes`, `prevent`/`protect`→`prevents`, `improve`/`enhance`/`ameliorate`→`improves`,
  `worsen`/`aggravate`/`exacerbate`→`worsens`, `achieve`/`attain`/`maintain`/`sustain`→`achieves`.
  Keep `no_association`/`no_effect`/`contraindicated`/`adverse_event` as lexical rules (they are not
  single verbs). Each predicate keeps its `base_polarity`/`flip_on_negation` so `negation.analyze`
  is unchanged. The flat `RULES`/`match` stay as the floor's binder.
- **`knowledge/parse.py` (new).** A thin wrapper over the parser already inside `entities._nlp()`:
  parse a sentence, and offer helpers to (a) find predicate tokens (the `ROOT` verb + its `conj`
  verbs), (b) collect an argument's coordinated set by walking `conj`/`cc` children, (c) map a
  mention's char span to its **head token** (so an entity is bound to the argument token that
  dominates it), and (d) detect a `neg` child on a predicate. Everything returns absolute char
  offsets so the offset contract holds.
- **`knowledge/observations.py` (rework the binder).** Replace `_refine_with_parse` with a real
  parse-driven binder used when `use_model=True`. Per sentence:
  1. Parse once (reuse the NER doc if practical, else re-parse the sentence text).
  2. For each predicate verb (ROOT and each `conj` verb — this splits *"reduced CRP but increased
     bloating"* into two predicates sharing a subject):
     - **Subjects** = entities whose head token is (or is dominated by) an `nsubj`/`nsubjpass` of
       the verb, expanded across `conj`/`cc`.
     - **Objects** = entities under a `dobj`/`nmod`/`pobj`/`attr`/`obl` of the verb, expanded
       across `conj`/`cc`. An entity buried as an `amod`/`compound` modifier of another argument
       (e.g. "reduced disease" inside "activity") is **not** a standalone object.
     - Emit the **cross-product** subject × object, each a distinct `Observation` (reusing
       `_emit`), with the predicate from the verb-lemma map and modality from `neg` + the existing
       uncertainty cues.
  3. **Prep-phrase modifiers → qualifiers.** A `during`/`in`/`for` `nmod`/`obl` phrase (e.g.
     "during remission") is offered to `qualifiers.extract` as a **condition**, not treated as an
     object — this is why the fixture's `remission` becomes a `disease_state` qualifier on the
     relation rather than its object (see *Decision* below).
  4. Fall back to the flat clause binder for any clause where the parse yields no predicate/argument
     (robustness; never lose a relation the floor would have found).

### Coexistence with clause scope (Concept 15)

`clauses.segment` (contrastive `but`/`whereas`/`;`) and `conj`/`cc` traversal overlap. Keep the
clause splitter as the floor's mechanism and as the qualifier-scoping unit; on the parse path,
predicate-level `conj` traversal is the primary splitter (it handles the shared-subject case
`clauses.py` cannot see grammatically). Reconcile by scoping qualifier attachment to the predicate's
subtree on the parse path, to the clause span on the floor path.

## Provenance constraints (offline; determinism optional on the model path)

- **Offset contract holds.** Every bound argument maps back to its entity mention's existing
  `EXACT_SPAN`; every emitted observation's `EvidenceRef` is a real char span of `document.text`.
  Token→char is via spaCy's `token.idx`, rebased to the sentence's `start_char`.
- **Record the binder.** Observations bound by the parse record the model + version (already on the
  run via `ontology_versions[_SCISPACY_MODEL]`) and set a binder tag (e.g. `parse` vs `flat`) on
  `context`, so a reader can see *how* an argument was bound. Non-reproducibility is acceptable; the
  audit trail is not.
- **Floor is untouched and deterministic.** `use_model=False` runs the current flat regex binder
  verbatim — same observations, same ids, byte-identical. This is the invariant the deterministic
  tests continue to assert.
- **No silent loss.** A sentence the parser mis-handles falls back to the flat binder; an unmatched
  argument still yields a kept observation that is `dropped` at claim time and reported, never
  hidden.

## Decision baked into this phase (test expectations change on purpose)

The parse gives a **more correct** reading of the motivating fixture than the flat heuristic did.
For *"Dietary fiber was associated with reduced disease activity during remission"*, the ROOT verb
`associated` takes `fiber` (nsubjpass) and `activity` (nmod) as arguments, while `remission` is a
`during`-phrase modifier. So the principled reading is:

> `dietary_fiber —associated_with→ disease activity`, **qualified by** `disease_state=remission`

not the flat binder's lucky `dietary_fiber —associated_with→ remission`. Phase 10 therefore
**updates** `tests/test_relations_claims.py` (and the audit/metrics fixtures that assert the same
claim) to the parse reading — this is a deliberate semantics improvement, not a test massaged to
pass. The five tests that regressed when the model was first switched on are re-baselined here, on
the model path, with the flat-floor assertions kept under `use_model=False`.

## Suggested steps

1. `parse.py`: sentence parse + argument/coordination/neg helpers over `entities._nlp()`; unit-test
   the three validated sentences' arc structure.
2. `relations.py`: add the verb-lemma → predicate map (+ the `risk`-object special case); keep flat
   `RULES` as the floor.
3. `observations.py`: implement the parse binder (predicate `conj` split, subject/object `conj`
   expansion, cross-product emit, prep-phrase → qualifier); route `use_model=True` through it and
   `use_model=False` through the existing flat binder. Retire the `_refine_with_parse` no-op.
4. Re-baseline the five affected tests on the model path; keep/duplicate their flat-floor variants.
5. Add the gate tests below.
6. Bump `RULESET_VERSION` (predicate map is a new ruleset) and `EXTRACTOR_VERSION` (binding changed);
   leave `PIPELINE_VERSION`/`SCHEMA_VERSION` unless offsets or storage shape change (they should
   not). Update `concepts.md` Concepts 7/8/15.

## Tests to add (gate)

- **Fragmentation fixed:** the fixture sentence → one `associated_with` relation `fiber → disease
  activity` with a `disease_state=remission` qualifier; **no** `fiber → "reduced disease"`
  observation on the parse path.
- **Subject + object coordination:** *"Vitamin D and calcium reduced the risk of colorectal cancer
  and osteoporosis"* → exactly four `reduces_risk` observations (the 2×2 cross-product), each with
  correct endpoints and provenance.
- **Contrastive coordination with shared subject:** *"Fiber reduced CRP but increased bloating"* →
  `fiber decreases CRP` + `fiber increases bloating`, opposite valence, shared subject.
- **Negation via `neg`:** *"Fiber did not reduce CRP"* → negated polarity from the `neg` arc.
- **Floor parity/determinism:** with `use_model=False`, observations are byte-identical to the
  pre-Phase-10 flat binder and identical across two runs.
- **Fallback safety:** a sentence the parser fails on still yields the flat-binder observation
  (nothing lost).

## Done criteria

Relation arguments are bound from dependency structure on the model path: intervening modifier
spans no longer fragment relations, coordinated subjects/objects expand to the correct
cross-product, and prep-phrase conditions land as qualifiers. The `use_model=False` floor is
unchanged and deterministic. Every observation still reconstructs from offsets and records how it
was bound. `RULESET_VERSION` and `EXTRACTOR_VERSION` are bumped; `concepts.md` Concepts 7 (and 8,
15) reflect the parse-based binder.
