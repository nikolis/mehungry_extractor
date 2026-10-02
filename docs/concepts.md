# Concepts, from PMID to result

This document explains the **ideas** behind the evidence engine, not its code.
It follows one paper — identified by a PubMed ID (PMID) — all the way to a synthesized,
evidence-backed conclusion, and at each stop it answers three questions:

- **What** is this concept?
- **Why** do we do it this way?
- **What are the alternatives, and where would you extend it?**

Code is quoted only as a *reference example* of a concept — never the other way around. If you
want the file-and-function tour instead, read [`current_state.md`](../current_state.md); if you
want to run the thing, read [`run.md`](../run.md). This document is the "why".

---

## The one idea everything else serves

Before any pipeline stage, understand the single requirement the whole system is built around:

> If the system tells you a fact, it can show you which paper, section, and — wherever the text
> supports it — which sentence or phrase made it believe that fact.

One design choice follows from that requirement, and it explains almost every decision downstream:

- **Provenance is mandatory.** No piece of knowledge — an entity, a relation, a claim, a funding
  note, an assessment — is allowed to exist without a pointer back to the exact text it came from.
  When a step reasons over several sentences and can't pin a single phrase, it records the
  *highest precision actually available* and labels it as such; it never fabricates a tighter
  pointer than it truly has.

**What changed, and why.** Earlier versions of this engine treated *determinism* — byte-identical
output on every run, and therefore no embeddings, no similarity search, no learned models — as a
second pillar, co-equal with provenance. **That requirement has been dropped. Determinism is no
longer a goal.** The system may now use non-deterministic techniques — embeddings, vector
similarity, learned scorers — wherever they earn their keep, *as long as every resulting fact
still carries provenance back to the part of the paper it came from.* Knowing **where** a
conclusion came from is the guarantee we keep; reproducing it **byte-for-byte** is not.

**Two constraints still hold, and they are firm:**

1. **Offline only, on what Python offers.** No online integrations — no hosted LLM APIs, no remote
   vector databases, no network calls except the single acquisition fetch (Concept 1).
   Non-deterministic power comes from libraries that run **locally**: local embedding models,
   `scikit-learn`, `gensim`, `numpy`, and the like. This keeps the engine self-contained and
   re-runnable without depending on a third party's uptime or a private model behind an API.
2. **Provenance is never traded away.** A technique that can't point back to the source text —
   however good its recall — doesn't belong in this engine. When a non-deterministic step makes a
   call (an embedding match, a learned score), it records *how* it decided (the match, the score,
   the model + version) so the decision stays **auditable** even though it isn't reproducible.
   Uncertainty about provenance is itself recorded as provenance; it is never hidden.

**Why not just call an LLM API, then?** Because a hosted model can't be re-run offline years
later, its availability and behavior sit outside our control, and it moves the trust boundary to a
third party. A *local* embedding model, by contrast, ships with the engine, runs without a network,
and — critically — is always used to *support* a span-anchored fact, never to replace the span.

> The legacy LLM path still exists (`mehungry-extract`) for the fuzzier task of reading
> disease-phase advice out of prose. It calls a **hosted** model, so it is deliberately kept
> **separate** and non-deployed. The two never mix. Everything below is the local evidence engine.

---

## Concept 0 — A stable identity: the PMID and the document id

**What.** Everything starts from a PMID (e.g. `32005824`). The engine immediately derives a stable
internal id, `pmid:32005824` → `pmid_32005824`, used as the key everywhere (folders, DB rows,
provenance).

**Why.** Ids must be *deterministic and position-based*, never random. The same paper always maps
to the same id; the third sentence of the seventh paragraph of section 3 always gets the same
`..._sec003_p007_s02` id. That is what lets a re-run line up with the previous run instead of
creating duplicates.

**Alternative / extension.** Random UUIDs would be simpler to generate but would break idempotency
and make provenance unstable across runs. Other id namespaces (DOI, PMCID) are carried as metadata
but the PMID-derived id is the spine.

**In the code** — `knowledge/ids.py`

```python
def normalize_pmid(pmid: str | int) -> str:
    """Return the bare numeric PMID as a string, or raise ``ValueError``."""
    s = str(pmid).strip()
    if s.lower().startswith("pmid:"):
        s = s[len("pmid:"):].strip()
    if not _PMID_RE.match(s):
        raise ValueError(f"not a valid PMID: {pmid!r}")
    return s

def document_id(pmid: str | int) -> str:
    return f"pmid_{normalize_pmid(pmid)}"

def section_id(doc_id: str, index: int) -> str:   return f"{doc_id}_sec{index:03d}"
def paragraph_id(sec_id: str, index: int) -> str: return f"{sec_id}_p{index:03d}"
def sentence_id(par_id: str, index: int) -> str:  return f"{par_id}_s{index:02d}"
```

- **Parameters.** `normalize_pmid`/`document_id` accept `"32005824"`, `32005824`, or `"pmid:32005824"`. The structural helpers each take the *parent* id plus a 0-based positional `index`.
- **Called by.** `document_id` is the spine: `corpus.CorpusStore` derives every path from it and `canonical.build_document` stamps it on the `Document`; the `section_id → paragraph_id → sentence_id` chain is invoked inside `canonical.build_document` as it walks the parsed structure.
- **Returns.** A deterministic, position-based id string. The same `(pmid, position)` always yields the same id, so a re-ingest lines up with the prior run instead of creating duplicates.

---

## Concept 1 — Acquisition and the immutable corpus

**What.** The one and only network step. The engine fetches raw XML from NCBI: always the PubMed
record, and — if the paper is in PMC open access — the full-text JATS XML. Those raw bytes are
written to disk once and **never overwritten**, alongside a SHA-256 checksum of each file.

```
data/corpus/pmid_32005824/
  raw/{pubmed.xml, pmc.xml, abstract.txt}   # immutable — the ground truth
  canonical/document.json                    # the parsed, offset-addressable text
  metadata.json                              # bibliographic + acquisition provenance
  checksums.json                             # SHA-256 of every raw file
```

**Why.** Two reasons. First, **reproducibility**: if you keep the exact bytes the paper was built
from, every later step can be re-run offline and will produce the same result years later, even if
the paper is retracted or NCBI changes its formatting. Second, **honesty**: the checksum lets you
prove the source hasn't been tampered with. Re-running is fast and offline because the network is
only ever touched once per paper.

**Alternatives / extension points.**
- You could re-fetch on every run (simpler, no disk) — but then you lose reproducibility and get
  rate-limited. The immutable cache is the deliberate opposite choice.
- Adding a new source (e.g. bioRxiv, a PDF) means adding a fetcher that returns raw bytes and a
  parser; the corpus layout and everything downstream stay the same.
- `--force` re-downloads raw bytes when you *intend* to refresh — the only sanctioned way to
  overwrite the immutable layer.

**In the code** — `knowledge/acquire.py` (the one network call) and `knowledge/corpus.py` (the archive)

```python
# acquire.py — fetch raw XML from NCBI E-utilities
def fetch(pmid: str | int, timeout: int = 30) -> RawSources:
    pmid = normalize_pmid(pmid)
    pubmed_resp = requests.get(f"{pmc.EUTILS}/efetch.fcgi",
                               params=pmc._params({"db": "pubmed", "id": pmid, "retmode": "xml"}),
                               timeout=timeout)
    pubmed_resp.raise_for_status()
    pmcid = pmc._pmid_to_pmcid(pmid, timeout)          # PMC full text only if open-access
    pmc_xml = _fetch_pmc(pmcid) if pmcid else None
    return RawSources(pmid, pmcid, pubmed_resp.content, pmc_xml, urls, retrieval_timestamp)

# corpus.py — write once, never silently overwrite
def save_raw(self, pmid, files: dict[str, bytes], force: bool = False) -> dict[str, str]:
    if self.has_raw(pmid) and not force:
        raise FileExistsError(...)                     # refuses to clobber the ground truth
    for name in sorted(files):
        (rd / name).write_bytes(files[name])
        checksums[name] = sha256(files[name])          # SHA-256 per raw file
    _write_json(self.doc_dir(pmid) / "checksums.json", checksums)
    return checksums
```

- **Parameters.** `fetch` takes a PMID and a network `timeout`; `save_raw` takes the PMID, a `{filename: bytes}` map, and the `force` override.
- **Called by.** Both from `ingest.ingest_pmid`: `fetch` supplies the bytes, `save_raw` archives them. On a later run `corpus.has_raw(pmid)` short-circuits `ingest_pmid` into the offline `normalize_document` path, so the network is never touched twice.
- **Returns.** `fetch` → a `RawSources` (raw bytes + acquisition provenance); `save_raw` → the checksum map, after writing `raw/` and `checksums.json`. `force=True` is the only sanctioned overwrite of the immutable layer.

### The topical gate: title screening before archival

**What.** Between *fetching* a paper and *archiving* it, the engine screens the paper by its
**title**. Only a paper whose title names a nutrition/diet topic is allowed to proceed; an
off-topic paper is dropped at the gate — nothing is written to the corpus, so it never reaches
any downstream stage. The screen is a small fixed vocabulary (nutrition, diet, food, meal,
protein, metabolic, lifestyle, plant-based, vegetarian, Body Mass Index, …); a title passes if it
contains at least one keyword. Matching is **case-insensitive** and **whole-word plus regular
plurals**, so `plant` matches "plant" / "plants" / "plant-based" but not "transplantation" or
"plantar", and `diet` matches "diet" / "diets" while "dietary" is caught by its own keyword.

**Why.** The corpus is a scarce, deliberately immutable resource, and every later stage costs
work; the title is the cheapest, most reliable topical signal available *before* we commit any of
that. Screening at the single network entry point means the "only relevant papers" policy is
enforced where papers are born, so an off-topic paper is normally never even archived. Whole-word
matching is a precision choice — a substring test would let "transplantation" or "implantation"
masquerade as "plant"; allowing regular plurals is a recall choice, so common forms like
"diets"/"foods"/"meals" are not lost.

**Enforced again at the point of use.** Because a paper *can* enter the corpus by another path
(ingested before the filter existed, or seeded directly), consumers that must guarantee on-topic
results re-assert the barrier when they read. The REST API does exactly this: before a cached
paper's results are returned it re-checks the archived title, so a paper that slipped into the
corpus off-topic still cannot leak into a response. The keyword vocabulary is the single source of
truth for both the ingest gate and this read-time check, so the two can never disagree.

**Alternatives / extension points.**
- The keyword list is the single source of truth (`titlefilter.TITLE_KEYWORDS`); widening or
  narrowing the topical scope is a data edit there, not a code change.
- Screening on the title (not the abstract or full text) is intentional: it is present for every
  paper, it is cheap, and it decides admission *before* archival. A richer gate (abstract terms, a
  classifier) could be added, but it would trade the current transparency and determinism.
- A skipped paper raises `TitleFiltered`, which callers report as a deliberate *exclusion*, never
  as an acquisition/extraction *failure* — the distinction matters for honest batch reporting.

**In the code** — `knowledge/titlefilter.py` (the vocabulary + `title_matches`), applied in
`ingest.ingest_pmid` right after the PubMed record is parsed and before `corpus.save_raw`.

---

## Concept 2 — The canonical document and the offset contract

This is the most important structural concept in the system.

**What.** All the parsed text — title, abstract, and body sections/paragraphs/sentences — is
concatenated into **one** canonical `text` string. Every structural element records absolute
character offsets into that one string, such that:

```python
document.text[start:end] == element.text     # always true, enforced by tests
```

That invariant is called the **offset contract**. A sentence isn't stored as a copy of its words;
it's stored as "characters 4,210 to 4,335 of the document text".

**Why.** Provenance needs a coordinate system, and there must be exactly **one**. If every layer
invented its own way to point at text ("paragraph 3, token 5" here, a substring copy there),
provenance would be un-checkable and the layers wouldn't line up. By making everyone address the
same string by absolute offset, *any* piece of knowledge can be reconstructed to the exact source
span by a simple slice — and a test can verify it. This single invariant is what makes the guiding
question ("show me exactly where") mechanically answerable.

**Alternatives / extension points.**
- Token offsets or (section, paragraph, sentence, token) tuples are the common alternative. They're
  more structured but fragile: retokenize and every offset shifts. Character offsets into an
  immutable string never drift.
- Sentence boundaries currently come from a rule-based segmenter (a blank spaCy pipeline with only
  a sentencizer). Swapping in a statistical segmenter is now fair game — it need not be
  deterministic — but it **must** preserve the offset contract; that is the one non-negotiable.
- Any new span you ever add (a clause, a qualifier cue, a dose phrase) **must** be expressed as
  absolute offsets into this same `text`. Don't invent a second coordinate system — that rule is
  load-bearing.

**In the code** — `knowledge/canonical.py`

```python
def build_document(metadata, sections, checksums=None) -> Document:
    parts: list[str] = []          # accumulates the ONE canonical text
    cursor = 0                     # running absolute offset into that text
    for psec in sections:
        ...
        for kept_par, (norm, raw) in enumerate(kept):
            if cursor > 0:
                parts.append(_PARAGRAPH_SEP); cursor += len(_PARAGRAPH_SEP)
            p_start = cursor
            parts.append(norm); cursor += len(norm)          # advance the cursor
            sentences = [
                Sentence(..., start_char=p_start + rel_start,  # segmenter offset, rebased
                              end_char=p_start + rel_end, text=stext)
                for k, (rel_start, rel_end, stext) in enumerate(segment(norm))
            ]
            ...
    canonical_text = "".join(parts)
    for sec in sections_out:
        sec.text = canonical_text[sec.start_char:sec.end_char]  # slice == text, by construction
    return Document(text=canonical_text, sections=sections_out, ...)
```

- **Parameters.** `metadata` (bibliographic), the parsed `sections` (each a `ParsedSection` of raw paragraphs from `jats`/`pubmed`), and the raw-file `checksums` for the reverse trace.
- **Called by.** `ingest.assemble_document`, which runs on *both* the network path (`ingest_pmid`) and the offline rebuild (`normalize_document`) — so the raw→canonical transform is identical either way. Sentence spans come from `segment()` (the deterministic sentencizer in `knowledge/segment.py`).
- **Returns.** A `Document` whose single `text` satisfies `document.text[obj.start_char:obj.end_char] == obj.text` for every section/paragraph/sentence — the offset contract, enforced by tests. Serialized by `to_canonical_json` with sorted keys so identical input is byte-identical output.

---

## Concept 3 — Provenance: the `EvidenceRef` and precision

**What.** Every knowledge object carries an `EvidenceRef`: a pointer to the document and, where
possible, the exact character span it came from. Crucially, the ref records **how precise** it is —
an `EXACT_SPAN`, or (when only the sentence is known) `SENTENCE`, and so on.

**Why.** Not every fact can be pinned to a phrase. A claim aggregated from several sentences knows
its sentences but not a single span. The honest thing is to record the *highest precision actually
available* and label it — never to pretend a sentence-level ref is a phrase-level one. The rule is:
**never silently upgrade precision.** Uncertainty about provenance is itself provenance.

**In practice.** `provenance.py` defines `EvidenceRef` and `ProvenancePrecision`; entity mentions
get `EXACT_SPAN` refs, observations get `SENTENCE` refs, and a claim's evidence is the deduplicated
*union* of its observations' refs.

**Alternatives / extension points.** You could store just "this came from paper X" (document-level
provenance). Many systems do. This engine insists on span-level wherever the text supports it,
because "which sentence" is the difference between a checkable claim and a vague one. New knowledge
types plug in by requiring an `EvidenceRef` in their constructor — that's how the discipline is
enforced structurally rather than by convention.

**In the code** — `knowledge/provenance.py`

```python
class ProvenancePrecision(str, Enum):        # best → coarsest
    EXACT_SPAN = "EXACT_SPAN"; SENTENCE = "SENTENCE"; PARAGRAPH = "PARAGRAPH"
    SECTION = "SECTION"; DOCUMENT = "DOCUMENT"; METADATA = "METADATA"

class EvidenceRef(BaseModel):
    document_id: str
    section_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    quoted_text: Optional[str] = None
    precision: ProvenancePrecision = ProvenancePrecision.SENTENCE
    extraction_rule: str = Field("canonical_ingest", ...)
    extraction_rule_version: str = Field("0.1.0", ...)
    extractor_version: str = Field(default=EXTRACTOR_VERSION, ...)

    @classmethod
    def for_span(cls, document, start_char, end_char, *, sentence=None,
                 extraction_rule, extraction_rule_version) -> "EvidenceRef":
        if sentence is None:                       # locate the owning sentence if not passed
            for sent in document.iter_sentences():
                if sent.start_char <= start_char and end_char <= sent.end_char:
                    sentence = sent; break
        return cls(document_id=document.document_id,
                   start_char=start_char, end_char=end_char,
                   quoted_text=document.text[start_char:end_char],   # sliced ⇒ self-consistent
                   precision=ProvenancePrecision.EXACT_SPAN, ...)
```

- **Parameters.** `for_span` takes the `document`, absolute `start_char`/`end_char`, the (optional) owning `sentence`, and the `extraction_rule` + version that produced the evidence. The sibling `for_sentence` builds a `SENTENCE`-precision ref from a whole sentence.
- **Called by.** `entities._build_mention` (`EXACT_SPAN` per mention), `qualifiers.extract` (`EXACT_SPAN` per cue), and `study.classify` (spans for sample size/design/country) call `for_span`; `observations._emit` calls `for_sentence`; `assessment._ref` constructs a `METADATA`-precision ref by hand where no span exists.
- **Returns.** An `EvidenceRef` whose `quoted_text` is sliced straight from `document.text` at the given offsets, so it reconstructs its source by construction and records `precision` honestly — never silently upgraded.

---

## Concept 4 — The `ExtractionRun`: versioned, reproducible bookkeeping

**What.** Every persisted object is tied to a *run*. A run records the pipeline version, ruleset
version, extractor version, schema version, the git commit, tool/model versions, and the ontology
versions in force. Its `run_id` is a **deterministic hash** of those version fields plus the
document ids — not a random UUID.

**Why.** Two payoffs. **Idempotency**: re-running the same pipeline over the same papers yields the
same `run_id`, so writes replace rather than duplicate. **Distinguishability**: when you change a
rule, you bump a version, which changes the run, so `v1` and `v2` outputs are separable and
comparable instead of silently overwriting each other. The wall-clock timestamp is recorded but
deliberately *excluded* from the hash, so "when you ran it" doesn't pollute "what you ran".

Note the run id remains a *bookkeeping* device even though extraction output is no longer
byte-identical. The id identifies the **configuration** (the versions + papers) that produced a
result, so a re-run replaces rather than duplicates; it is no longer a promise that the bytes will
match. When a non-deterministic component runs, its model + version belong on the run alongside the
others, so two results carrying the same `run_id` are still comparable and explainable.

**Alternatives / extension points.** Random run ids are the usual default; they make idempotency
and reproducibility impossible, which is why they're rejected here. When you add a rule or change
behavior, the contract is: bump the matching version constant. That's the mechanism that keeps old
conclusions faithful to the code that produced them.

**In the code** — `knowledge/run.py`

```python
def build_run(document_ids: list[str], timestamp: Optional[str] = None) -> ExtractionRun:
    doc_ids = sorted(set(document_ids))
    git_commit    = _git_commit()
    spacy_version = _tool_version("spacy")
    scispacy_version = _tool_version("scispacy")
    python_version   = platform.python_version()

    fingerprint = "\n".join([                # everything that defines "what you ran"
        PIPELINE_VERSION, RULESET_VERSION, EXTRACTOR_VERSION,
        python_version, spacy_version or "", scispacy_version or "",
        git_commit or "", *doc_ids,
    ])
    run_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

    return ExtractionRun(run_id=run_id,
                         timestamp=timestamp or datetime.now(timezone.utc).isoformat(),  # NOT hashed
                         git_commit=git_commit, pipeline_version=PIPELINE_VERSION, ...)
```

- **Parameters.** `document_ids` (the papers this run covers) and an optional explicit `timestamp` (defaults to now, in UTC).
- **Called by.** Every persistence entry point builds exactly one run: `ingest._persist`, `entities.analyze_document`, and `pipeline.extract_document` all call `build_run([document.document_id])` and then set `run.ontology_versions` before writing. The API's batch path builds one per extracted paper.
- **Returns.** An `ExtractionRun` whose `run_id` is a deterministic SHA-256 (first 16 hex chars) of the version fields + sorted doc ids. The wall-clock `timestamp` is recorded but deliberately excluded from the hash, so re-running identical inputs reproduces the `run_id` (writes replace, not duplicate) while a version bump forks it.

---

## Concept 5 — Normalization: surface form → controlled concept

**What.** Text says "vitamin D", "Vitamin-D", "cholecalciferol". These are the same *concept*.
Normalization maps a surface string to a single controlled concept id from a bundled, checked-in
vocabulary — by pure lookup: casefold, collapse whitespace, resolve against the index.

Three outcomes, and the last two are the interesting ones:
- exactly one match → **normalized** (a concept id)
- several matches (genuine ambiguity) → **ambiguous** — *no concept is chosen*
- no match → **unmatched**

A mention is **never dropped** for failing to normalize; it's kept with its status and its span.

**Why.** Downstream reasoning (do two papers talk about the same thing? do their claims agree?)
requires a shared vocabulary — you can't compare raw strings. And the "never invent a resolution"
rule matters: if a surface truly maps to two concepts, guessing one would fabricate knowledge. Far
better to mark it `ambiguous` and keep the evidence visible. Keeping `unmatched` mentions means the
system can *show you what it couldn't understand* instead of hiding its blind spots.

**Alternatives / extension points.** This is the sharpest fork in the whole design, and it is now
**open** where it used to be closed:
- **Local embeddings / vector similarity** would link "cholecalciferol" to "vitamin D" without a
  dictionary entry. This used to be forbidden as non-deterministic; it is now a **sanctioned
  path**, provided it runs offline (a local embedding model) and the resulting mention still
  carries its `EXACT_SPAN` provenance *and* records how it was resolved — the concept it matched
  and the similarity score — so the decision is auditable even though it isn't reproducible.
- **Fuzzy string matching** is likewise acceptable now, under the same rule: keep the span, record
  the match and its score, never resolve silently.
- **LLM entity linking via a hosted API** stays out — not because it's non-deterministic, but
  because it's an online integration (see the two firm constraints in "The one idea"). A *local*
  model is fine.

The curated dictionary remains the trustworthy backbone: exact, transparent, and the yardstick
every other resolver is measured against. Two extension paths now coexist — *growing the
vocabulary* (`knowledge/vocab/`; coverage as a data problem) and *adding a local embedding
resolver* that proposes matches the dictionary missed, each match kept only with its span and a
recorded score. A truly ambiguous surface is still marked `ambiguous` rather than guessed.

**The writable overlay — editing the recognisable-entity list at runtime.** Coverage is a data
problem, so the vocabulary is editable *without* editing the checked-in file. The bundled
`dictionaries.json` stays the pristine, version-controlled baseline; a separate **overlay** file
(default `data/vocab_overlay.json`, beside the corpus/DB) records a user's edits and is **merged**
into `load_concepts()`:
- an overlay concept whose `concept_id` matches a built-in one **replaces** it (this is how a
  built-in's surface forms are edited);
- a `concept_id` not in the built-in set is a **net-new** concept;
- an id in the overlay's `removed` list is **hidden** (a built-in can be masked without deleting it
  from the baseline).

The merge is the single source of truth for both the normalization index and the surface-form
matcher regex, so an edit takes effect for the **next extraction** once those caches are
invalidated (`entities.reload_vocabulary()`); papers already extracted are not reprocessed. Two
rules keep this honest. First, the baseline is never mutated — remove/edit only ever write the
overlay, so the reproducible core is always recoverable by deleting the overlay. Second, an edited
vocabulary is **never silent**: `overlay_digest()` is a content hash of the overlay, and it is
recorded in every `ExtractionRun`'s `ontology_versions` (as `mehungry_curated_overlay`) whenever the
overlay is non-empty — so a result produced under a customized vocabulary is distinguishable from
one produced against the pristine baseline, and the determinism story (Concept 4) still holds
*relative to a stated vocabulary state*. This is the model behind the `/vocab` REST surface and the
Observer UI's **Vocabulary** panel, which list every recognisable concept and let a user add, edit,
or remove them.

**In the code** — `knowledge/normalize.py`

```python
def normalize(surface: str, entity_type: Optional[str] = None) -> Normalization:
    candidates = _index().get(_key(surface), [])            # casefold + collapse-ws lookup
    if entity_type is not None:                             # optional type filter (see caveat below)
        candidates = [c for c in candidates if c.entity_type == entity_type]

    if not candidates:
        return Normalization(concept=None, status=STATUS_UNMATCHED)
    if len(candidates) > 1:
        distinct = {c.concept_id for c in candidates}
        if len(distinct) > 1:                               # truly ambiguous — do NOT guess
            return Normalization(concept=None, status=STATUS_AMBIGUOUS)
    return Normalization(concept=candidates[0], status=STATUS_NORMALIZED)
```

- **Parameters.** `surface` (the raw string to resolve) and an optional `entity_type` that restricts candidates to that type so a surface unambiguous *within its type* resolves cleanly. **Caveat (Concept 6):** this filter is *not* fed scispaCy's label — BC5CDR's coarse `CHEMICAL`/`DISEASE` conflates our finer types (chemical/nutrient/biomarker, disease/symptom/outcome), so narrowing on it would wrongly block resolution (e.g. "Vitamin D", tagged `CHEMICAL`, is a *nutrient*). The vocabulary decides the type instead.
- **Called by.** `entities.extract` — once per dictionary hit and once per scispaCy span (the latter *without* a type filter, per the caveat). `_index()` (surface-key → concepts) is built once from the checked-in vocabulary and `lru_cache`d.
- **Returns.** A `Normalization` carrying a `status` of `normalized` (one concept), `ambiguous` (several distinct concepts — none chosen), or `unmatched` (no hit). The caller *keeps the mention regardless* — it is never dropped for failing to normalize.

---

## Concept 6 — Entity mentions: scispaCy detection over a dictionary floor

**What.** Scanning sentence by sentence, the engine produces every `EntityMention` with an
`EXACT_SPAN` ref from two span sources, unified through the *same* normalizer:

- **scispaCy** (`en_ner_bc5cdr_md`) is the **primary detector for its two labels** — Chemical and
  Disease. It runs *first* and its spans **win any overlap** with the dictionary. It is a
  **required** dependency of the default path: with `use_model=True` (the default), a model that
  cannot be loaded raises `ModelUnavailableError` rather than silently degrading.
- **The dictionary** (curated vocabulary, matched case-insensitively, whole-word,
  longest-match-first) is the auditable floor. It is the *sole* source for the six entity types
  scispaCy cannot see (food, nutrient, biomarker, intervention, symptom, outcome), it **gap-fills**
  Chemical/Disease where the model was silent, and — crucially — it still performs **all** concept
  normalization, including for scispaCy's spans.

**Why this split, and why the model can't simply "be" the extractor.** BC5CDR emits only
`CHEMICAL`/`DISEASE`, but the vocabulary spans eight types dominated by *food* and *nutrient* — the
whole point of a dietary engine — which the model is blind to. And concept resolution is
dictionary-based regardless: scispaCy proposes only *spans*; a span becomes a concept (and thus a
claim) only if it normalizes against the vocabulary. So the model earns "first class" as the
authoritative **span detector** for its labels (better boundaries, and recall on chemical/disease
names absent from the vocabulary), while the dictionary remains load-bearing as both the coverage
floor for everything else and the sole concept resolver. A span scispaCy finds but the vocabulary
can't resolve is kept `unmatched` — provenance intact, but unable to form a claim.

**One sharp interaction: don't narrow normalization on the model's coarse label.** BC5CDR's
`CHEMICAL` conflates our finer *chemical / nutrient / biomarker*, and `DISEASE` conflates *disease /
symptom / outcome*. Passing that label to `normalize()` as a type filter would wrongly *block*
resolution — "Vitamin D", tagged `CHEMICAL`, is stored as a *nutrient* and would fall to
`unmatched`. So for scispaCy spans the **vocabulary decides the concept and type**; the model's
label is only a *fallback* type for spans the vocabulary doesn't recognize (e.g. "Ibuprofen").

**Alternatives / extension points.** The model is now the primary detector for higher recall, with
the dictionary as the auditable floor beneath it. `use_model=False` is the one path that neither
touches nor requires the model — a deterministic, model-free floor kept for offline reproducibility
and for the deterministic tests. Extension points: add other **local** detectors (a local embedding
matcher, a fine-tuned span tagger) under the same "detect a span → normalize against the vocabulary
→ record provenance" contract; broaden the vocabulary so more of the model's spans resolve. Whenever
the model is used, its version is recorded on the run, so model-assisted output stays distinguishable
from pure-dictionary output. A *hosted* model would break the offline constraint and belongs in the
separate legacy path instead.

**In the code** — `knowledge/entities.py`

```python
def extract(document, *, use_model: bool = True) -> list[EntityMention]:
    if use_model:
        _nlp()                                 # fail loudly if the REQUIRED model can't load
    matcher = _matcher()                       # word-bounded, longest-first regex alternation
    mentions = []
    for sent in document.iter_sentences():     # scan sentence by sentence ⇒ offsets known
        base = sent.start_char
        occupied = []                          # sentence-relative spans already claimed
        if use_model:                          # 1) scispaCy FIRST ⇒ authoritative, wins overlaps
            for rel_start, rel_end, model_label in _scispacy_spans(sent.text):
                norm = normalize(sent.text[rel_start:rel_end])   # vocab decides concept + type;
                etype = norm.concept.entity_type if norm.concept else model_label  # label is fallback
                occupied.append((rel_start, rel_end))
                mentions.append(_build_mention(document, sent, base+rel_start, base+rel_end, ...))
        if matcher is not None:                # 2) dictionary: other 6 types + gap-fill
            for m in matcher.finditer(sent.text):
                if any(m.start() < oe and os < m.end() for (os, oe) in occupied):
                    continue                   # overlaps a scispaCy span — the model wins
                norm = normalize(m.group())
                mentions.append(_build_mention(document, sent, base+m.start(), base+m.end(), ...))
    unique.sort(key=lambda m: (m.start_char, m.end_char, m.entity_type, m.surface_text))
    return unique
```

- **Parameters.** `document` (the canonical doc) and `use_model`. `use_model=True` (default) *requires* the scispaCy model — a missing model raises `ModelUnavailableError`; `use_model=False` selects the deterministic, model-free dictionary-only floor.
- **Called by.** `pipeline.extract_document` (the full pass) and `entities.analyze_document` (the standalone `mehungry analyze` stage). Both then persist via `db.persist_entities`.
- **Returns.** An ordered list of `EntityMention`, each with an `EXACT_SPAN` `EvidenceRef` and a normalization `status`. scispaCy is claimed first, so it wins overlaps for Chemical/Disease; the dictionary supplies the other six types, gap-fills, and normalizes every span. *(Extraction is deterministic only on the `use_model=False` floor; the model path may vary across model versions, which is recorded on the run.)*

---

## Concept 7 — Relations: rule-based cues between two entities

**What.** A relation is a predicate between two entity mentions in a sentence. The *predicate* is
always chosen from an **ordered** list of lexical/regex cue rules — `no_association`, `reduces_risk`,
`associated_with`, `causes`, and so on; the first rule that matches wins, and each carries a stable
`rule_id` and `version`. What differs is how the two *arguments* are found: the model-free floor
scans **consecutive mention pairs** and matches the cue in the text *between* them, while the model
path binds arguments from the dependency parse (see "Two binders" below). Both share the same rule
set and provenance.

Most predicates assert an *effect* (`causes`, `decreases`, `reduces_risk`). A small **descriptive**
family does the opposite — it says what the subject *is* or *manifests*, not what acts on it, so it is
clinically neutral (Concept 16) and never flips on negation, and it most often appears in a
subordinate clause that **nests** under the relation it elaborates (Concept 20):

- `characterized_by` — *"dysbiosis characterized by alterations in the gut microbiota"* elaborates
  what the subject is. Its object is the **whole descriptor phrase** hanging off the `by`-cue. When
  that phrase is a bare entity (*"characterized by Firmicutes"*) the object is that entity. But when its
  head noun is an abstract noun that merely *dominates* an entity (*"alterations in the composition and
  function **of** the gut microbiota"*), the object is the **entire** head-noun phrase taken verbatim —
  *not* the deep entity (*gut microbiota*) the general object resolver would otherwise descend to.
  Binding the object to *gut microbiota* there would assert the wrong thing: the relation is about the
  *alterations*, which *gut microbiota* only modifies. Such a free-text descriptor is not a vocabulary
  concept, so it carries no `concept_id` — the observation keeps the correct object text, but no
  concept-level claim forms from it (a descriptor is not a concept node; see Concept 3, never guess).
- `has_decreased_abundance_of` / `has_increased_abundance_of` — a *state* "…, **decreasing**
  Firmicutes" or "…, **increasing** Proteobacteria" does not act, it describes a compositional change
  it manifests. On the parse path a free-adjunct directional participle over a state is **re-labelled**
  from the active `decreases`/`increases` to the descriptive abundance predicate of the same
  *direction* — the direction is recorded without asserting a per-taxon benefit/harm the engine cannot
  know. Their model-free counterpart is a narrow flat cue requiring an explicit *abundance/level/
  proportion of* noun, so a bare "reduced Firmicutes" stays the ordinary active `decreases`. Two parse-side rules let such subordinate relations bind their
subject from structure rather than be dropped: a reduced relative / participle takes its antecedent
noun as subject (already true for `acl`/`relcl`), and a **free-adjunct participle** on an `advcl` arc
(*"induces dysbiosis, **decreasing** Firmicutes"* / *"…, **characterized** by …"*) takes the
*governing clause's object* as its implicit subject — the manifestation reading, which also stops the
generic controller fallback from fabricating the governing-subject reading.

**Why ordered / first-match.** Specific cues must come before generic ones they'd otherwise be
swallowed by: "no association" has to be tested before "associated with", and "reduces the risk of"
before "reduces". Priority order is how the rule set encodes "more specific meaning wins". Because
each rule is a pinned, versioned regex, every relation can be traced to the exact rule that fired —
that's the provenance requirement reaching into the logic layer.

**Two binders, chosen by `use_model` (Phase 10).** Pairing *consecutive* mentions assumes the two
entities of a relation are textually adjacent. Denser entity detection breaks that: any intervening
span — e.g. scispaCy tagging "reduced disease" inside "…associated with reduced disease activity
during remission" — severs the real pair, and coordinated arguments ("vitamin D **and** calcium
reduced the risk of cancer **and** osteoporosis") mis-bind entirely (the flat binder finds just one
of the four intended relations). [Phase 10](phases/phase-10-parse-based-relations.md) fixes this on
the model path by binding arguments from **dependency structure** (`knowledge/parse.py`) instead of
adjacency:

- **Predicates** are the sentence's ROOT verb plus its `conj` verbs — so "reduced CRP **but**
  increased bloating" splits into two predicates *sharing the same subject*.
- **Subjects/objects** are read off each verb's grammatical arcs (`nsubj`/`nsubjpass`,
  `dobj`/`nmod`/`obl`), each expanded across `conj`/`cc`, and the subject×object **cross-product**
  is emitted — turning the vitamin-D/calcium sentence into its four correct observations. Object
  coordination also recovers **apposition list-items**: in a comma list *"A, B, and C"* spaCy often
  labels the *middle* element `appos` of A while only the last gets `conj` (so *"decreasing
  Firmicutes, the Bifidobacterium genus, and Faecalibacterium prausnitzii"* would otherwise drop
  Bifidobacterium). An `appos` child is treated as a list-item **only when its head already heads a
  `conj`** — the list signal — so a lone apposition (*"CRP, a marker of inflammation"*) is never
  mistaken for coordination. This is scoped to object binding; subject coordination stays strict.
- **Prepositional-phrase modifiers** introduced by a condition preposition (`during`/`in`/`under` —
  *not* the connective tails `with`/`of`) fall out as **qualifier conditions** (Concept 14), not
  objects. So "during remission" becomes a `disease_state` qualifier on the relation rather than its
  object. A condition is read not only off the predicate that emits the relation but off the
  **governing clause of a control predicate**: in *"the Mediterranean diet has been shown **in
  patients with active disease** to reduce disease activity"*, the relation is stated on the
  controlled `reduce` (an `xcomp`) while the condition hangs off the governing `shown`. Just as the
  controlled predicate **inherits its subject** from that governor (below), it inherits the
  governor's conditions — climbed along the *same* bounded `head` chain the subject came from — so
  the condition is attached to the relation rather than lost on the non-emitting governing verb.
  This is deliberately scoped to the control case: a predicate with its own overt subject never
  inherits a governor's condition.
- An entity that is only an `amod`/`compound` modifier of the object noun (the "reduced disease"
  inside "activity") is bound as the object *via the noun that dominates it*, so a denser tag no
  longer fragments the pair. But that descent is **attributive-only** (`amod`/`compound`/`nummod`):
  it must *not* reach across a coordinated sibling (`conj` — a separate object token, enumerated on
  its own) or into a prepositional/appositive complement (`of …`, `such as …`) to grab a
  *different* entity. When an object head is itself a non-entity and the only entity reachable lies
  across such an arc — "reduce **symptom burden** and inflammation", where descending would bind the
  coordinated *inflammation* to *burden* and silently lose *symptom burden* — the head-noun phrase is
  kept verbatim as a **free-text (`unmatched`, no `concept_id`) object** instead of mis-binding. This
  is the same honest discipline Phase 15 applies to a descriptive object (Concept 20) and that
  Concept 3 demands everywhere: retain the span and its provenance, but form no concept-level claim
  from a phrase the vocabulary cannot name, rather than fabricate a wrong one. It fires only to
  *replace a would-be wrong binding*; an object head that dominates no entity at all stays unbound,
  so it adds no free-text noise. (Naming the endpoint in the vocabulary — e.g. `disease activity` as
  an outcome concept, Concept 5/6 — remains the way to turn such a phrase into a real claim.)
- A **measure/container noun** (`intake`, `consumption`, `concentration`, `level`, `abundance`, … —
  and `risk`) is not itself the endpoint; the relation is about the entities inside its **content
  genitive**. So "reduce the intake **of red meat and processed meat**" unwraps to *both* foods
  (coordination-expanded), just as "risk **of** cancer and osteoporosis" already did (Phase 4b). Only
  the `of`/`for` phrase is taken — a locative on the same noun ("concentration of H₂S **in the
  intestine**") stays a qualifier, not a second endpoint. `risk` remains the one measure noun that
  *also* promotes the predicate (`decreases`→`reduces_risk`); the others keep the predicate and only
  widen the objects. This unwrap now runs on **both endpoints**: a measure-noun *subject* is unwrapped
  the same way — *"**Intake of red meat** increased CRP"* binds *red meat → increases → CRP*. Without
  it the strict subject resolver (which never enters an `of`-phrase, see below) would leave the
  subject unresolved and drop the relation.
- A **gerund clausal subject** names the agent *inside* the subject clause: *"**Consuming** 400 mL
  **of kefir** … improves abdominal pain …"* has no plain noun subject, so the entity is recovered by
  a permissive search of the `csubj` gerund and shared across the predicates it governs — binding
  *kefir → improves → abdominal pain* rather than the flat binder's mis-paired guess (Phase 4f). This
  is the one place subject resolution is *permissive* rather than restrictive, and it is deliberately
  a **last resort** (tried only after a direct subject and a control resolution both fail), so it
  cannot override the non-entity-subject drop that keeps a fabricated subject out of the record.

This needs **no new dependency**: the first-class scispaCy model (Concept 6) already ships a
`parser` in its pipeline, and `parse.py` wraps that same object. The **predicate stays rule-based** —
a versioned verb-lemma → predicate map (`relations.VERB_PREDICATE_MAP`) with two object-driven
promotions: a `risk` object promotes `decreases`/`increases` to `reduces_risk`/`increases_risk`, and a
**direction word on a non-risk object** promotes a *bare association* to a **directional association**
(`associated_with_reduced`/`associated_with_increased`, see the directional-associations concept
below). Every promotion resolves back to the *same* `RelationRule` a flat cue would, so
polarity/flip/`rule_id`/version — and therefore modality (Concept 8) and provenance — are identical
whichever binder fired. Lexical, non-verb
predicates (`no_association`, `no_effect`, `contraindicated`, adverse-event) have no verb to map, so
a sentence built on them yields no parse predicate and **falls back** to the flat binder.

**The fallback is guarded, not automatic — precision over a fabricated guess.** The flat binder runs
as a fallback *unless the parse positively determined that the relation's **subject is a
non-entity***. That determination is what keeps a fabricated subject out of the record, and it rests
on three cooperating pieces:

- **Predicate discovery reaches into subordinate clauses.** Binding only the verbal ROOT and its
  coordinated (`conj`) verbs leaves a relation stated in a relative clause (*"products **that
  increase** …"*), an adverbial clause (*"…crucial, focusing on **preventing** …"*), or a clausal
  complement (*"research recommending **reducing** …"*) invisible — and an invisible predicate is
  exactly what used to hand the sentence to the adjacency floor. Discovery now also walks
  `advcl`/`acl`/relative/`ccomp`/`xcomp`/`pcomp` arcs (and, when the ROOT is a copula-headed
  adjective/noun, the verbal `conj` predicates hanging off it), so the predicate is *seen* and can be
  reasoned about instead of defaulted on.
- **Participial / reduced-relative predicates on argument nouns are surfaced too.** A relation is often
  stated in a participle hanging off a *noun* the ROOT-seeded walk never descends into —
  *"increased consumption of vegetables, **associated** with reduced cancer"*, where `associated` is an
  `acl` of the noun *consumption* (itself only a `dobj`/`nmod` of the clause verb). A final scan adds
  every remaining verbal `acl`/`relcl` predicate, and a reduced relative's **antecedent noun is its
  subject** (it spells out none) — so the participle binds *vegetables → associated_with → cancer* from
  structure. Crucially, a participle is **not** allowed to trip the non-entity-subject block below:
  its antecedent is frequently a non-entity head keeping the real entity in a PP the strict resolver
  won't enter (*"a **diet** rich **in fiber**, associated with …"*), so when a participle cannot
  resolve its subject it **defers to the flat fallback** (like the pronoun case) rather than
  suppressing it — recovering the relation instead of losing it. This is what made the reported
  *early-life* sentence's *correct* relations reachable while its fabricated one stays dropped.
- **The subject is resolved honestly, not by descent into a phrase.** A subject head that is not
  itself an entity may bind to an entity that *attributively modifies* it (`amod`/`compound`/`conj`/
  `nummod` — "High **vegetable** intake lowered the risk …", where the head noun *intake* is a
  measure/container and *vegetable* is its modifier), but the resolver deliberately **refuses to
  descend into a prepositional phrase** under the subject: the subject of *"Nutritional care **in IBD
  patients**"* is the non-entity "care", not the nested "IBD", and the subject of *"an imbalance **in
  the consumption of omega-3**"* is "imbalance", not "omega-3". Reaching into those PPs is precisely
  how a subject used to be fabricated; refusing lets such a subject correctly resolve to *nothing*.
- **Descent is gated on normalization — the modifier must be a real concept (drop-guard).** The
  attributive descent above is an unguarded recall heuristic: it recovers the true subject when the
  modifier is a known concept, but the *same* descent otherwise adopts whatever the parser hung on
  the head noun, including a **false entity that never resolved**. That is how *"early-life **diet**
  influences IBD risk, with … increased consumption of vegetables …"* fabricated *early-life →
  increases → vegetables*: the parser misread *increased consumption* as a finite verb coordinated
  with *influences* (so it inherited *diet*'s subject), *early-life* is a non-normalizing scispaCy
  DISEASE false positive on the `amod` of *diet*, and the descent adopted it. The resolver therefore
  accepts a **descended** subject only when it carries a `concept_id`; a non-normalizing one is
  treated as *absent*. This keeps exactly the useful case (the modifier is a known concept, so a
  claim can be built) and discards exactly the fabrication (the modifier is not a concept, so no claim
  could ever be built) — and because the subject then resolves to nothing over an overt non-entity
  head (*diet*), the non-entity-subject guard below suppresses the flat fallback too, so the relation
  is dropped rather than re-fabricated. The gate applies **only to the entity reached by descent**:
  when the subject head *is* the entity, an unnormalized head-anchored subject is still bound and
  retained as an observation, unchanged.
- **An elided subject** (control: *"require enteral nutrition … to prevent …"*) is resolved to its
  controller from the governing clause, so the relation is still bound when the controller is a real
  entity.
- **The guard blocks only a non-entity subject.** When a discovered, mapped predicate has an overt
  noun subject — or a control-resolved controller — that resolves to **no concept**, the flat
  fallback is suppressed for that sentence: the true subject is a non-entity (an abstract noun, a
  demonstrative anaphor "This diet", a population), and the floor would only re-introduce the
  mis-binding the parse declined to make. The relation is kept **dropped and reportable** rather than
  fabricated — the same discipline as Concept 3 (never assert a fact whose endpoint is not a known
  concept). A predicate whose subject is a bare **pronoun** ("they"/"it") or an unresolvable control
  structure does *not* block: reliable resolution there would need anaphora we do not have, so the
  flat adjacency heuristic is left as a reasonable last resort rather than a fabrication.

The net effect is a precision choice: a relation whose true subject the vocabulary cannot name is
better *absent* than *invented*, while a relation stated in a subordinate clause with a resolvable
subject is now *recovered* rather than lost.

**Directional associations — an association carries the direction its object states.** A bare
`associated_with` throws away real information when the object is modified by a direction word:
*"vegetables associated with **reduced** CRP"* and *"sugary drinks associated with **increased**
inflammation"* are opposite clinical signals, yet both would flatten to the same direction-less
predicate — and the clinical valence (Concept 16), which reads benefit/harm from *(predicate, object)*,
would then call *reduced CRP* **harmful** (CRP is an undesirable marker, and bare association leans by
the object alone). So when an association's object carries an `amod` direction word, the predicate is
promoted to `associated_with_reduced` or `associated_with_increased` — the association analogues of
`decreases`/`increases`. They stay *associations*: the non-causal, weaker epistemic standing is carried
by `certainty`, not by inventing a causal predicate; only the **direction** the sentence actually
stated is recorded, which is exactly what valence needs to read *reduced CRP* as beneficial. The parse
binder reads the direction off the object noun's `amod` (`parse.direction_of`); the flat binder has the
matching higher-priority cues (`rel_associated_with_reduced`/`_increased`, ranked above bare
`associated_with`). When the object is a **risk** noun the direction resolves instead to the *causal*
risk predicate — *"linked to a **lower risk** of cardiovascular disease"* → `reduces_risk`, the mirror
of the reviewer-confirmed *"…**higher risk**… → `increases_risk`"*. This matters: without it the parse
binder emitted a bare, direction-less `associated_with` that read as a plain association *with* the
disease — the exact **opposite** of the protective message — and the flat `reduces_risk` cue had a
matching gap (`lower\w+` demanded a suffix, so bare "lower risk" silently fell through to
`associated_with` while "higher risk" was caught); both cues are now symmetric. The choice of the
*causal* risk predicate here (rather than a distinct association-with-*risk* predicate —
`associated_with_reduced_risk`/`associated_with_increased_risk`, the aspiration recorded in the curated
gold) is a deliberate product decision: keeping the reviewer-confirmed causal reading, not a mechanical
one.

The regex flat binder is kept verbatim as the deterministic, model-free **`use_model=False` floor**
(byte-identical to pre-Phase-10). Non-determinism is confined to the model path, where it is
acceptable because every bound argument still reconstructs from its mention's offsets; a parse-bound
observation records `binder:parse` on `context` so a reader can see *how* it was bound, and a
sentence whose predicate the parse does not recognise falls back rather than losing the relation. **A deliberate semantics
change rides along:** the motivating fixture is read as `dietary_fiber —associated_with_reduced→ disease
activity` *qualified by* `disease_state=remission`, not the flat binder's old lucky `fiber → remission`
object. Once *disease activity* became a curated outcome concept (`OUT:disease_activity`; see
Concept 5/6 — coverage as a data problem), **both** binders read this sentence the same correct way:
the parse binds the disease-activity endpoint from structure, and the floor — with the true endpoint
now a resolvable concept sitting between subject and the trailing `during remission` condition —
pairs it too. The "lucky" `fiber → remission` object only ever arose because the real endpoint was
not in the vocabulary, leaving `remission` as the nearest noun to grab; with the endpoint named, the
adjacency artifact disappears and `remission` is correctly a condition, not the object.

**Other alternatives / extension points.**
- **Statistical or embedding-based relation extraction** would catch relations no hand-written
  rule anticipates. This is permitted, provided it runs on a **local** model and each extracted
  relation still records the sentence/span it came from and the model + version that proposed it.
  A hosted **LLM** relation extractor stays out — an online-integration objection, not a determinism
  one.

Extension is by *writing more cue rules* (`relations.py`), each with an id, a version, a base
polarity, and a flag for whether negation may flip it. New rules go in priority order and bump the
ruleset version.

**In the code** — `knowledge/relations.py`

```python
# Ordered by priority: the FIRST matching rule wins. Lexically-negative predicates come first and
# carry their own polarity (flip=False) so the negation pass can't double-negate them.
RULES: tuple[RelationRule, ...] = (
    _rule("rel_no_association", "no_association",
          r"\bno (?:significant )?association\b|\bnot associated\b|...",
          polarity=Polarity.NEGATIVE.value, flip=False, description="..."),
    _rule("rel_reduces_risk", "reduces_risk",
          r"\b(?:reduc\w+|lower\w+|decreas\w+) (?:the |a )?risk\b", description="..."),
    # Directional associations rank ABOVE bare associated_with so "associated with reduced CRP"
    # records the direction; a "risk" object is still caught by the causal risk cues above.
    _rule("rel_associated_with_reduced", "associated_with_reduced",
          r"\b(?:associat\w+ with|correlat\w+ with|linked to|...)\b.*\b(?:reduc\w*|lower\w*|...)\b",
          description="..."),
    _rule("rel_associated_with_increased", "associated_with_increased",
          r"\b(?:associat\w+ with|correlat\w+ with|linked to|...)\b.*\b(?:increas\w*|higher|...)\b",
          description="..."),
    _rule("rel_associated_with", "associated_with",
          r"\bassociat\w+ with\b|\bassociation between\b|...", description="..."),
    ... # causes, improves, worsens, increases, decreases — generic verbs come LAST
)

def match(connecting_text: str) -> "RelationRule | None":
    """First-match: the highest-priority rule whose cue appears in ``connecting_text``."""
    for rule in RULES:
        if rule.pattern.search(connecting_text):
            return rule
    return None
```

- **Parameters.** `match` takes `connecting_text` — the slice of canonical text *between* two adjacent entity mentions (`document.text[subj.end_char:obj.start_char]`).
- **Called by.** The flat floor (`observations._flat_bind_sentence` / `_emit`) calls `match` for every consecutive mention pair inside a clause; the parse binder (`observations._parse_bind_sentence`) instead calls `relations.rule_for_verb(lemma, object_is_risk=…)` to map a predicate verb to the same `RelationRule`. Each rule carries a stable `rule_id` + `version`; `registry()` feeds them into the `extraction_rules` audit table (the verb map reuses those same ids, so it adds no new registry rows).
- **Returns.** The single highest-priority `RelationRule` whose regex fires (or `None`). Priority order is how "more specific meaning wins" is encoded — `no_association` is tested before `associated_with`, `reduces_risk` before `decreases`. The rule's `base_polarity` and `flip_on_negation` are then handed to `negation.analyze` (Concept 8).

---

## Concept 8 — Modality: negation and uncertainty (polarity + certainty)

**What.** A relation isn't just its predicate; it has a **modality** — a `polarity` (positive /
negative / neutral) and a `certainty` (`asserted` / `possible` / `hypothetical` /
`insufficient_evidence`). Cue rules over the sentence region leading up to the relation decide it,
in strict precedence:

1. *insufficient / no evidence* → neutral polarity, `insufficient_evidence` — this dominates
   ("insufficient evidence that X is associated with Y" asserts nothing).
2. *hypothetical* cues ("hypothesized", "in theory") → `hypothetical`.
3. *negation* cues ("not", "failed to") → flip a flippable rule's polarity (`positive`↔`negative`
   only; a neutral base polarity is left untouched).
4. *uncertainty* cues ("may", "suggests") → `possible`.

**Why.** "X reduces risk", "X may reduce risk", "X does not reduce risk", and "there is
insufficient evidence that X reduces risk" are four *different* facts. Collapsing them would make
the engine assert things the paper hedged. Precedence matters because a negated-and-hedged sentence
must resolve to one honest reading; "insufficient evidence" outranks everything because it's the
strongest disclaimer. Note the interplay with relations: a lexically-negative predicate like
`no_association` sets polarity itself and must **not** be flipped again — otherwise a double
negative would silently invert the meaning.

**Alternatives / extension points.** Local negation/uncertainty ML models (e.g. a small classifier
over the clause) are now an option — they were previously excluded as non-deterministic.
[Phase 10](phases/phase-10-parse-based-relations.md) additionally reads negation off the parse's
`neg` dependency arc on the predicate verb (sharper than a regex region), falling back to the cue
rules. Whichever path, the judgement must stay **auditable**: store the triggering cue text, or the
model + version and its score, so a reader can see *why* a relation was read as hedged or negated.
The cue-rule approach stays the transparent default; a local model or the parse would extend it, not
replace it.

**In the code** — `knowledge/negation.py`

```python
def analyze(region: str, *, base_polarity: str, flip_on_negation: bool) -> Modality:
    if _INSUFFICIENT.search(region):                       # 1) dominates everything
        return Modality(Polarity.NEUTRAL.value, Certainty.INSUFFICIENT_EVIDENCE.value, ...)
    certainty = Certainty.ASSERTED.value
    polarity  = base_polarity
    if _HYPOTHETICAL.search(region):                       # 2) hypothesized / in theory
        certainty = Certainty.HYPOTHETICAL.value
    if flip_on_negation and _NEGATION.search(region):      # 3) flip positive↔negative only
        if base_polarity == Polarity.POSITIVE.value:  polarity = Polarity.NEGATIVE.value
        elif base_polarity == Polarity.NEGATIVE.value: polarity = Polarity.POSITIVE.value
    if certainty == Certainty.ASSERTED.value and _UNCERTAINTY.search(region):  # 4) may / suggests
        certainty = Certainty.POSSIBLE.value
    return Modality(polarity, certainty, "; ".join(cues) or None)
```

- **Parameters.** `region` — the clause text up to and including the object (so pre-subject and inter-entity cues are both seen); `base_polarity` and `flip_on_negation` come straight from the `RelationRule` that fired.
- **Called by.** On the floor, `observations._emit`, once per candidate relation, over `document.text[clause.start_char:obj.start_char]` — the region is clause-bounded (Concept 15), so a negation in a sibling clause can't leak in. On the parse path, `observations._emit_parse` over the sentence-up-to-object region, and additionally honours the predicate verb's `neg` dependency arc: if the verb is negated but the region regex missed it, the polarity is flipped explicitly (recorded with a `neg` cue).
- **Returns.** A `Modality(polarity, certainty, cue)`. The four checks run in strict precedence; `insufficient_evidence` outranks everything, and a lexically-negative predicate (`flip_on_negation=False`) is never re-flipped. The triggering `cue` string is stored on `Observation.context` for audit.

---

## Concept 9 — Two layers on purpose: observations vs. claims

This distinction is subtle and central, so it gets its own section.

**What.**
- An **Observation** is one relation found in one sentence: two specific mentions, a predicate, a
  modality, the triggering cue, and a *sentence-precision* `EvidenceRef`. It's the **audit layer** —
  it stays close to the text and is kept even when an endpoint didn't normalize.
- A **Claim** is the **concept layer**: observations that agree on
  `(subject_concept, predicate, object_concept, polarity, certainty)` are folded into one claim,
  keyed by concept ids. A claim can only form when **both** endpoints normalized. Its evidence is
  the deduplicated union of its observations' refs. The `claim_id` is a hash of that canonical key,
  so re-runs reproduce it.

Both layers now also carry an **optional parent pointer** — `parent_observation_id` on the
observation, `parent_claim_id` on the claim — recording that this relation is *nested beneath*
another (its subject is that one's object). It is derived from the bound endpoints after folding, is
never part of the id, and is left null whenever the parent is absent or ambiguous (Concept 20). It
does not change what an observation or claim *is*, only how one relates to another.

**Why two layers.** They answer different questions. The observation layer answers "what does *this
sentence* literally say?" — maximally faithful, never dropped. The claim layer answers "what does
the *literature* assert about these two concepts?" — comparable across sentences and papers.
Crucially, claim-building is a **pure function of observations**: it introduces no new evidence,
only groups existing evidence. Observations that can't become claims (an unmatched endpoint) are
*counted and reported* (`dropped`), never silently swallowed — the system tells you what it saw but
couldn't concept-ify.

**Which layer the batch surface returns.** The batch REST entry point (`/analyze`) returns the
**claim** layer per paper — the concept-keyed, comparable-across-papers assertions, each still
carrying the deduplicated union of its observations' source spans. That is the right default for a
consumer asking "what does this paper assert?": it is already de-duplicated and concept-normalized,
yet one step below cross-paper synthesis so each paper stays individually inspectable. The
finer-grained observation layer is not lost — it is exposed per paper on the observe surface
(`/observe/observations/{pmid}`) for anyone who needs the raw, never-dropped audit view.

**Alternatives / extension points.** A single flat layer would be simpler but would force a choice
between fidelity and comparability; splitting them keeps both. Extension: the claim key is exactly
the set of things that define "the same claim" — Part II widens it to include qualifiers (below).

**In the code** — `knowledge/claims.py` (the concept layer folds `knowledge/observations.py`, the audit layer)

```python
def normalize(observations, *, concept_name=None) -> NormalizeResult:
    groups: dict[tuple, list[Observation]] = {}
    dropped = 0
    for obs in observations:
        if not obs.subject_concept_id or not obs.object_concept_id:  # an endpoint didn't normalize
            dropped += 1                                             # counted, never swallowed
            continue
        key = (obs.subject_concept_id, obs.predicate, obs.object_concept_id,
               obs.polarity, obs.certainty, _qualifiers.signature(obs.qualifiers))
        groups.setdefault(key, []).append(obs)

    claims = []
    for key, obs_group in groups.items():
        evidence = _dedupe_evidence([r for o in obs_group for r in o.evidence_refs])  # union
        claims.append(Claim(claim_id=_claim_id(obs_group[0].document_id, key[:5], key[5]),
                            ..., evidence_refs=evidence,
                            observation_ids=sorted(o.observation_id for o in obs_group)))
    return NormalizeResult(claims=claims, dropped=dropped)
```

- **Parameters.** `observations` (the audit-layer rows to fold) and an optional `concept_name` resolver (defaults to `normalize.concept_by_id`).
- **Called by.** `pipeline.extract_document`, immediately after `observations.extract(document, mentions)`. The resulting claims + observations are persisted together by `db.persist_relations`.
- **Returns.** A `NormalizeResult(claims, dropped)`. Observations agreeing on `(subject_concept, predicate, object_concept, polarity, certainty, qualifier_signature)` collapse into one `Claim` whose evidence is the deduped union of theirs and whose `claim_id` is a hash of that key (so re-runs reproduce it). `dropped` counts observations that couldn't form a claim — reported by the CLI, never hidden.

---

## Concept 10 — Study, funding, and affiliations: the context facts

**What.** Separately from the claims, the engine extracts *facts about the paper*:
- **Study characteristics** — design (from PubMed publication types + text cues), publication year,
  sample size, country.
- **Funding** — funding statements pulled from the PMC JATS, with each funder mapped to a curated
  funder vocabulary and given a `funder_type` (e.g. industry vs. public).
- **Affiliations** — the author list and institutions from the PubMed record.

**Why.** A claim's weight depends on *who ran the study, how, and who paid*. These facts feed the
assessment layer. They're kept as first-class, provenanced facts rather than folded into the claim,
so the claim stays a pure statement and the judgement stays separate (next concept).

**Alternatives / extension points.** Funder classification is a vocabulary, so extending it (new
funders, new types) is a data edit + version bump. New study attributes (blinding, follow-up
duration) are new characteristic extractors following the same rule-and-provenance discipline.

**In the code** — `knowledge/study.py`, `knowledge/funding.py`, `knowledge/affiliations.py`

```python
# study.py — design from authoritative metadata first, then text, else "unknown" (never a guess)
def classify(document, *, affiliations=None) -> list[StudyCharacteristic]:
    pub_match = _design_from_pubtypes(document.metadata.publication_types or [])
    if pub_match is not None:                       # PubMed publication types (METADATA precision)
        design, matched_pt = pub_match
        out.append(StudyCharacteristic(field="study_design", value=design,
                                       classification_source="pubmed_publication_type", ...))
    else:
        text_match = _design_from_text(document)    # deterministic text cues (EXACT_SPAN) — fallback
        out.append(... value=(design if text_match else DESIGN_UNKNOWN) ...)
    # + publication_year (metadata), sample_size / follow_up / country (regex/dictionary over text)
    return out

def extract(document_id, statements) -> list[FundingRelationship]:   # funding.py
    #   map each funding statement's funder to the curated funder vocab → funder_type
def extract(document_id, pubmed_xml) -> AffiliationExtraction:       # affiliations.py
    #   authors + institutions parsed straight from the PubMed record
```

- **Parameters.** `study.classify` takes the canonical `document` (+ author `affiliations` for country); `funding.extract` takes the doc id and the JATS `FundingStatement`s; `affiliations.extract` takes the doc id and raw PubMed XML.
- **Called by.** `pipeline.extract_document`, all three in sequence. Funding statements come from `jats.parse_funding` + `jats.parse_back_matter`; each result is persisted by `db.persist_study` / `persist_funding` / `persist_affiliations`.
- **Returns.** Lists of provenanced *facts* — `StudyCharacteristic` (with `classification_source` recording metadata-vs-text origin), `FundingRelationship` (funder → `funder_type`), and `AuthorAffiliation`. Absent info stays absent / `unknown`; nothing is inferred. These feed the assessment layer next.

---

## Concept 11 — Assessment frameworks: judgement *on top of*, never *into*, the evidence

**What.** An assessment framework (the shipped one is `mehungry_evidence_v1`) is a **pure, versioned
function** of the paper's facts. It grades criteria — study-design strength, recency, sample-size
adequacy, funding independence — and writes the grades to a *separate* `assessments` table. It
**never** writes a score back onto a claim or a paper.

**Why the separation.** Evidence is what the paper says; assessment is how *we* weigh it. If you
mutate the evidence with a score, you can never change your mind without corrupting the record, and
two frameworks can't disagree over the same data. By keeping assessment a pure function landing in
its own table, multiple frameworks can coexist over untouched evidence, and changing the rubric just
bumps the framework version and produces new rows — the evidence stays byte-identical. One rule
worth calling out: **`unknown` funding is treated as *not* independent.** The engine never assumes
independence from missing information — absence of a conflict disclosure is not evidence of no
conflict.

**Alternatives / extension points.** This mirrors well-known evidence rubrics (GRADE-style
thinking) but stays deterministic and local. The obvious extension is *more frameworks*: write
another pure function over the same facts, give it a version, and it coexists with `v1`. Consumers
choose which lens they trust.

**In the code** — `knowledge/assessment.py`

```python
def assess(facts: PaperFacts) -> list[Assessment]:
    out = []
    # study_design_strength: high / moderate / low / unknown
    design = facts.study_design
    if design is None or design == "unknown":
        out.append(_mk(facts, "study_design_strength", "unknown", ...))
    elif design in _HIGH_DESIGNS:      out.append(_mk(facts, "study_design_strength", "high", ...))
    elif design in _MODERATE_DESIGNS:  out.append(_mk(facts, "study_design_strength", "moderate", ...))
    else:                              out.append(_mk(facts, "study_design_strength", "low", ...))
    # recency (>= 2015), sample_size_adequacy (>= 100) ...
    # funding_independence — unknown funding is NOT independent (spec §14)
    if facts.industry_funding is None:
        out.append(_mk(facts, "funding_independence", "unknown",
                       "No funding information; independence cannot be assumed.", "None"))
    elif facts.industry_funding:  out.append(_mk(facts, "funding_independence", "industry_funded", ...))
    else:                         out.append(_mk(facts, "funding_independence", "independent", ...))
    return out
```

- **Parameters.** `facts: PaperFacts` — a small pure struct (`study_design`, `publication_year`, `sample_size`, `industry_funding`, `funder_types`) assembled deterministically by `pipeline._build_facts` from the Concept-10 facts.
- **Called by.** `pipeline.extract_document`, which calls `assess(facts)` and persists the result with `db.persist_assessments` into the *separate* `assessments` table — never onto a claim or paper row.
- **Returns.** A list of `Assessment` grades (one per `Criterion`), each carrying `framework_id`/`framework_version` and a `METADATA`-precision `EvidenceRef`. Because it's a pure function landing in its own table, a new framework or a rubric change just bumps the version and produces new rows while the evidence stays byte-identical. Note `industry_funding is None` (no disclosure) grades as *not independent*.

---

## Concept 12 — Cohesion: which papers actually belong to this batch

*(Batch/REST path only — the CLI works one paper at a time.)*

**What.** Give the API a handful of PMIDs "about one topic". Some may be off-topic. Cohesion decides
which cohere and which are outliers, using only **set arithmetic** over each paper's normalized
concept ids:
- The **topic core** = concepts that recur across enough papers.
- Each paper scores as the fraction of the core it covers; below a threshold it's an **outlier**.

**Why.** Synthesizing across papers only makes sense if they're about the same thing; one off-topic
paper would pollute the conclusions. Today it's pure set math over shared normalized concepts — no
model — so it's fully explainable: an outlier is reported *with the exact core concepts it lacks*.
Nothing is silently dropped; outliers are fully reported and merely excluded from synthesis.

**Alternatives / extension points.** Embedding-based topic clustering — the mainstream approach,
previously forbidden — is now an available alternative for catching topical relatedness the
shared-concept test misses, provided it runs on a **local** model and each outlier decision stays
explainable (which papers it clustered near, or which core concepts it lacks, plus the similarity
score). Because the current definition of "topic" is "shared normalized concepts", improving
cohesion within today's design is still mostly a vocabulary/normalization improvement; thresholds
(`core_fraction`, `outlier_threshold`) are the tuning knobs.

**In the code** — `knowledge/cohesion.py`

```python
def detect(concept_sets, *, core_fraction=0.5, outlier_threshold=0.2) -> CohesionResult:
    doc_ids = sorted(concept_sets); n = len(doc_ids)
    df = {}                                        # document frequency of each concept
    for doc_id in doc_ids:
        for cid in concept_sets[doc_id]:
            df[cid] = df.get(cid, 0) + 1
    min_core_papers = max(2, ceil(core_fraction * n)) if n >= 2 else 1
    core_set = {c for c, count in df.items() if count >= min_core_papers}   # the topic core

    papers = []
    for doc_id in doc_ids:
        concepts = concept_sets[doc_id]
        score = round(len(concepts & core_set) / len(core_set), 4)          # fraction of core covered
        is_outlier = score < outlier_threshold
        papers.append(PaperCohesion(doc_id, score, len(concepts), is_outlier,
                                    reason=REASON_OFF_TOPIC if is_outlier else None,
                                    missing_core_concepts=sorted(core_set - concepts)))
    return CohesionResult(core_concepts=sorted(core_set), papers=papers, ...)
```

- **Parameters.** `concept_sets: {document_id -> {concept_id, …}}` (normalized concepts only), plus the `core_fraction` / `outlier_threshold` tuning knobs.
- **Called by.** `api/service._synthesize` (batch/REST path only), which builds the concept sets from each paper's persisted mentions, then feeds `result.included` (the non-outliers) into synthesis.
- **Returns.** A `CohesionResult` with the topic core, a per-paper `score` + `is_outlier` flag, and — for each outlier — exactly which core concepts it `missing_core_concepts`. Pure set arithmetic, so byte-identical per batch; outliers are fully reported and merely excluded from synthesis, never silently dropped.

---

## Concept 13 — Synthesis: turning many claims into one conclusion, conflicts and all

*(Batch/REST path only.)*

**What.** Read-side only: it reads the persisted claims of the on-topic papers and groups them by
canonical relation `(subject_concept, predicate, object_concept)`. Each group becomes a
**conclusion** with a direction:
- **SUPPORTED** / **REFUTED** / **INCONCLUSIVE**, or
- **CONFLICTING** when both polarities are present.

Each conclusion is annotated with which papers support/contradict/are neutral, the spread of
certainty, the spread of study-design strength (from the assessments), and a real source quote
reconstructed from the offset contract. Conclusions are ranked most-corroborated first with a fully
deterministic tie-break.

**Why.** This is the payoff: a defensible summary of what the literature says about a relationship.
The defining choice is that **conflicts are surfaced, never averaged away.** A naive system would
compute "net effect" and hide disagreement; this one shows you that three papers say yes and two say
no, with quotes. Because synthesis only *reads and groups* already-persisted, provenanced claims (it
"adds no new evidence and performs no inference"), every conclusion drills back down to source
spans — which is the guarantee that matters, whether or not the grouping step is deterministic.

**Alternatives / extension points.** Meta-analytic weighting or an LLM summary would produce
smoother prose but lose the audit trail. A *local* embedding-based grouper — clustering
near-duplicate relations across papers instead of grouping by exact concept key — is now permitted,
as long as each conclusion still drills back to source spans and records how it was grouped. A
hosted-LLM prose summary stays out (online integration). Other extension points: the
ranking/tie-break rules, and the roll-up of batch-level facts (design mix, year range, funding
independence).

**In the code** — `knowledge/synthesis.py`

```python
def synthesize(engine, document_ids) -> Synthesis:
    groups: dict[tuple, dict] = {}
    for doc_id in sorted(set(document_ids)):
        for c in list_claims_for_document(engine, doc_id):        # read-side only
            key = (c["subject_concept_id"], c["predicate"], c["object_concept_id"],
                   _qualifier_signature(c.get("qualifiers") or []))
            g = groups.setdefault(key, {"pos": set(), "neg": set(), "neu": set(), ...})
            (g["pos"] if c["polarity"]=="positive" else
             g["neg"] if c["polarity"]=="negative" else g["neu"]).add(doc_id)

    conclusions = []
    for (subject, predicate, obj, _sig), g in groups.items():
        direction = (CONFLICTING if g["pos"] and g["neg"]           # conflicts SURFACED, not averaged
                     else SUPPORTED if g["pos"] else REFUTED if g["neg"] else INCONCLUSIVE)
        conclusions.append(Conclusion(..., direction=direction,
                           clinical_direction=_valence.clinical_direction(predicate, obj),  # Concept 16
                           evidence=[_first_quote(engine, cid) ...]))                        # offset contract
    conclusions.sort(key=lambda c: (-c.paper_count, c.subject_name.casefold(), ...))  # deterministic tie-break
    return Synthesis(conclusions, _batch_facts(engine, doc_ids),
                     _derive_food_conclusions(conclusions), warnings)                # Concept 17
```

- **Parameters.** `engine` (the SQLite handle) and `document_ids` — the *on-topic* papers that survived cohesion.
- **Called by.** `api/service._synthesize`, with `cohesion` outliers already removed. It reads persisted claims via `query.list_claims_for_document` — it never re-extracts.
- **Returns.** A `Synthesis`: claims grouped by canonical relation `(subject, predicate, object, qualifier-signature)` into ranked `Conclusion`s, each with a `direction` (`SUPPORTED`/`REFUTED`/`INCONCLUSIVE`/`CONFLICTING`), a `clinical_direction` (Concept 16), supporting/contradicting papers, and a real source quote reconstructed via the offset contract. Adds no evidence and performs no inference; every conclusion still traces to source spans even if a non-deterministic grouper is used.

---

## Part II — representing *conditional* knowledge

Part I produces flat `(subject, predicate, object, polarity, certainty)` claims. Real dietary
science is conditional: "beneficial in remission, risky in active flare". Part II adds just enough
structure to represent that without abandoning the non-negotiables (offline, fully provenanced).
These are the newer concepts.

### Concept 14 — Qualifiers: typed conditions attached to a relation

**What.** A `Qualifier` attaches a **typed condition** (first type: disease state) to an observation
or claim — "…**in remission**". A qualifier is a *condition, not an assertion*, so it has **no
polarity**. Its value normalizes against a small disease-state vocabulary, and (as everywhere) an
unrecognized cue is kept `unmatched` with its span, never dropped. Each qualifier carries its own
`EvidenceRef` to the cue that justified it.

**Why.** Without it, "fiber is beneficial in remission" and "fiber may harm in active flare" collapse
into one contradictory claim about fiber. The qualifier is what keeps them distinct — and it does so
by **widening the claim key** to include qualifiers, so conditional statements group separately.
That's the whole point of the layer: same discipline (versioned regex cues, provenance), new slot.

**Where the condition is read.** A condition cue is matched over the relation's span, but on the
parse path "the relation's span" includes the **governing clause of a control predicate**, not just
the verb that emits. When a controlled predicate inherits its subject from a governor (e.g. `reduce`
under *"…has been shown **in patients with active disease** to reduce…"*), it inherits that
governor's conditions along the same climb (see the parse binder under "Relations") — otherwise the
condition, which hangs off the non-emitting governing verb, would be silently lost. The disease-state
cue vocabulary also matches regular plurals of its heads (*"active **diseases**"* → `active disease`),
mirroring the plural handling elsewhere, so a pluralized condition is neither missed nor left
`unmatched` when its singular is in the vocabulary.

**History — the retired `manifestation` type.** A descriptive relation's abstract object phrase
(*"characterized by **alterations in the composition and function of** the gut microbiota"*) was once
represented as a parse-derived `manifestation` qualifier: the object stayed the deep concept (*gut
microbiota*) and the head-noun phrase rode along as a condition. That was the wrong slot — the
relation is *about* the alterations, not the microbiota — so Phase 15 makes the whole phrase the
object itself (see Concept 20 and the descriptive family under "Relations"). The free-text object
simply carries no `concept_id`, which is honest: a descriptor is not a concept node, so no
concept-level claim forms from it, rather than a wrong one. The `manifestation` qualifier type is gone.

**Extension points.** More qualifier *types* — dose, population, duration — each a new vocabulary +
cue set. Disease state is simply the first. (Qualifiers remain a regex-cue layer; the one-time
parse-derived `manifestation` experiment has been folded back into the object slot it belongs in.)

**In the code** — `knowledge/qualifiers.py`

```python
def extract(document, sentence, *, start_char=None, end_char=None) -> list[Qualifier]:
    base = sentence.start_char if start_char is None else start_char     # scope to a clause (Phase 7)
    end  = sentence.end_char   if end_char   is None else end_char
    text = document.text[base:end]
    for rule in _RULES:                        # ordered: disease_state, population, dose, duration, ...
        for m in rule.pattern.finditer(text):
            core = m.group("core")             # the normalizable condition surface
            abs_start, abs_end = base + m.start("core"), base + m.end("core")
            concept_id = rule.normalize(core)  # disease-state lookup, or None for Phase-7 types
            ... Qualifier(qualifier_type=rule.qualifier_type,
                          value_concept_id=concept_id, value_text=core,      # unmatched ⇒ keeps surface
                          evidence_refs=[EvidenceRef.for_span(document, abs_start, abs_end, ...)])
    return sorted(by_key.values(), key=lambda q: (q.qualifier_type, q.value_concept_id or "", ...))

def signature(qualifiers) -> str:              # the condition component of the claim key — EMPTY if none
    parts = sorted((q.qualifier_type, q.value_concept_id or _key(q.value_text)) for q in qualifiers)
    return "|".join(f"{qtype}={value}" for qtype, value in parts)
```

- **Parameters.** `document` + the owning `sentence`, and optional absolute `start_char`/`end_char` that scope the scan to a single clause. `signature` takes a qualifier list.
- **Called by.** `observations.extract` calls `extract(...)` per clause (so a contrastive clause's condition attaches only there); `claims.normalize` and `synthesis` call `signature(...)` to fold it into the grouping key.
- **Returns.** A deduplicated, ordered list of `Qualifier` — each a *typed condition* with **no polarity** and its own `EXACT_SPAN` ref; an unrecognized cue is kept `unmatched` (surface retained), never dropped. `signature` is **empty when there are no qualifiers**, so unqualified claims hash exactly as they did pre-Phase-6 (backward-compatible ids).

### Concept 15 — Clause scope: don't let one clause leak into another

**What.** A single sentence can assert opposite things under different conditions: "fiber is
beneficial during remission **but** may aggravate symptoms in active flare". Matching relations over
the *whole* sentence mis-binds those, and a negation or cue from one half leaks into the other. Clause
segmentation splits the sentence on contrastive markers (`but`, `whereas`, `however`, `while`,
`although`, `;`) so relations, negation, and qualifiers are all scoped **within a clause**.

**Why.** It bounds the "connecting text" window and the negation region to the clause, so scoped
relations stay separate and correctly qualified. It's pure and cue-based — no parser — and it honors
the offset contract (a clause is a sub-span of its sentence). A sentence with no marker is simply one
clause; nothing is lost for failing to segment.

**Extension points.** Richer clause markers, or templated multi-slot relation rules that fill several
qualifier slots from one clause. On the model path, [Phase 10](phases/phase-10-parse-based-relations.md)
splits coordinated predicates grammatically via the parse's `conj`/`cc` arcs (e.g. a shared subject
across "reduced CRP **but** increased bloating"), which subsumes part of this surface-marker
splitting; the cue-based segmenter here remains the deterministic `use_model=False` floor and the
unit that scopes qualifier attachment.

**In the code** — `knowledge/clauses.py`

```python
_MARKER_RE = re.compile(r"\b(?:but|whereas|however|although|while)\b|;", re.IGNORECASE)
_CONTRASTIVE = {"but": True, "whereas": True, "however": True,
                "although": True, "while": True, ";": False}   # ";" merely coordinates

def segment(document, sentence) -> list[Clause]:
    base, text = sentence.start_char, sentence.text
    segments, cursor, prev_marker = [], 0, None
    for m in _MARKER_RE.finditer(text):
        segments.append((cursor, m.start(), prev_marker))      # text BEFORE this marker = one clause
        prev_marker, cursor = m.group().lower(), m.end()       # marker itself excluded from the span
    segments.append((cursor, len(text), prev_marker))
    clauses = []
    for rel_start, rel_end, marker in segments:
        ... # trim whitespace; skip empties
        contrastive = bool(clauses) and marker is not None and _CONTRASTIVE.get(marker, False)
        clauses.append(Clause(..., start_char=base+start, end_char=base+end,   # obeys offset contract
                              text=text[start:end], marker=marker, contrastive=contrastive))
    return clauses
```

- **Parameters.** `document` and the `sentence` to split.
- **Called by.** `observations._flat_bind_sentence` (the floor), once per sentence — the returned clauses bound both the connecting-text window and the negation region (`document.text[clause.start_char:obj.start_char]`), and `qualifiers.extract` is scoped to each clause's span. The parse binder does not segment into clauses; it splits on `conj` predicates instead and scopes `qualifiers.extract` to each predicate's own prep-phrase condition spans (Concept 7).
- **Returns.** Ordered `Clause` sub-spans, each obeying the offset contract (`document.text[start_char:end_char] == text`) and recording the `marker` that opened it and whether it's `contrastive`. A sentence with no marker is a single clause — nothing is lost for failing to segment.

### Concept 16 — Valence: the *clinical* reading (helps / harms / caution / neutral)

**What.** The relation layer speaks in *lexical* predicates ("decreases CRP", "decreases
remission"). A reader wants the *clinical* reading: does this compound **help**, **harm**, warrant
**caution**, or read as **neutral**? For most predicates that depends not on the predicate alone but
on whether the object is something we want *more* or *less* of. "Decreases CRP" (an undesirable
marker) is **beneficial**; "decreases remission" (a desirable state) is **harmful**. Two predicate
groups short-circuit that object test: adverse-event/contraindication predicates are always
**caution**, and null predicates (`no_effect`, `no_association`) are always **neutral**. Valence is a
small checked-in table from `(predicate, object concept)` to a clinical direction.

**Why it's separate from polarity.** Polarity/synthesis-direction answers "do the papers *agree* that
this relation holds?" Valence answers "what does the relation *mean* clinically when it does hold?"
They're orthogonal: a negated claim ("fiber does *not* improve remission") is *no benefit shown*, not
*harm*. So a mature conclusion pairs a **clinical direction** (what it means) with a **synthesis
direction** (whether papers agree) — two independent axes. Keeping valence a versioned table means the
clinical reading can evolve without silently rewriting old outputs.

**Extension points.** The valence table itself (more predicate/object mappings), and safety flags in
condition-partitioned conclusions built on top of qualifiers + valence.

**In the code** — `knowledge/valence.py`

```python
def clinical_direction(predicate, object_concept_id, object_entity_type=None) -> str:
    if predicate in _CAUTION_PREDICATES:  return CAUTION    # adverse_event / contraindicated
    if predicate in _NULL_PREDICATES:     return NEUTRAL    # no_effect / no_association
    desirability = object_desirability(object_concept_id, object_entity_type)  # want more / less / neither
    if predicate == "improves": return BENEFICIAL
    if predicate == "worsens":  return HARMFUL
    if predicate in _LOWERING_PREDICATES:      # decreases / reduces_risk / prevents / associated_with_reduced
        return BENEFICIAL if desirability == UNDESIRABLE else HARMFUL if desirability == DESIRABLE else NEUTRAL
    if predicate in _RAISING_PREDICATES:       # increases / increases_risk / causes / achieves
        return HARMFUL if desirability == UNDESIRABLE else BENEFICIAL if desirability == DESIRABLE else NEUTRAL
    ...
    return NEUTRAL
```

- **Parameters.** `predicate` (the lexical relation), the `object_concept_id`, and an optional `object_entity_type` (else resolved from the vocabulary / id prefix). `object_desirability` classifies the object as desirable / undesirable / neutral via per-concept overrides then an entity-type default.
- **Called by.** `synthesis.synthesize`, once per conclusion: `clinical_direction=_valence.clinical_direction(predicate, object_cid)`.
- **Returns.** One of `beneficial` / `harmful` / `neutral` / `caution`. It is **polarity-independent** on purpose — whether papers *agree* is the synthesis `direction`; this is what the relation *means* when asserted. "Decreases CRP" (undesirable ↓) is beneficial; "decreases remission" (desirable ↓) is harmful. A small checked-in table, versioned by `CLINICAL_VALENCE_VERSION`.

### Concept 17 — The concept graph: composition edges (built) + hierarchy (planned)

**What.** Two additions that turn the flat concept lookup into a graph. The first is **built**:
curated *composition* edges (`foods.py`, `vocab/food_sources.json`) that materialize a
`compound —found_in→ food` graph, so the engine can already answer "which foods contain this
compound". The second is **planned**: a *hierarchy* over concepts (food families, chemical classes)
to reason up/down (salmon → oily fish → fish).

**Why the composition layer is careful about provenance.** A `found_in` edge has no source span —
it is asserted by the curated composition ontology, so it carries its own `ontology` + version as
provenance. And the module is deliberately just the **linking layer** (5a): it supplies the edge,
but it never itself asserts a food↔disease relation. Turning a compound-level finding into a
food-level conclusion ("the food containing it helps") is a separate, explicitly-labeled *derived*
layer (5b) — that leap is inference, and must be attributed to the compound claim's evidence **plus**
the `found_in` edge + version. That split is exactly this document's provenance discipline reaching
into the reference layer: dual provenance, never a silent inference.

**Why it's a separate layer.** It's reference/vocabulary, orthogonal to extraction, which is why it
can be built in parallel. It extends *what concepts mean and how they relate* without touching how
claims are extracted.

**In the code** — `knowledge/foods.py` (the built composition layer)

```python
FOUND_IN = "found_in"

@functools.lru_cache(maxsize=1)
def load_edges() -> list[CompositionEdge]:
    edges = []
    for rec in load_food_sources():                      # the checked-in, versioned ontology
        source = concept_by_id(rec["source_concept_id"])
        food   = concept_by_id(rec["food_concept_id"])
        if source is None or food is None:               # a bad edit is a hard error, not a guess
            raise ValueError(f"food_sources edge references unknown concept: ...")
        edges.append(CompositionEdge(source_concept_id=source.concept_id, source_name=source.canonical_name,
                                     food_concept_id=food.concept_id, food_name=food.canonical_name,
                                     ontology="mehungry_food_sources", ontology_version=FOOD_SOURCES_VERSION))
    return sorted(edges, key=lambda e: (e.source_concept_id, e.food_concept_id))

def food_sources_for(source_concept_id: str) -> list[CompositionEdge]:
    """Foods a compound/nutrient is found in (empty if none). Deterministic order."""
    return list(_by_source().get(source_concept_id, []))
```

- **Parameters.** `food_sources_for` takes a compound/nutrient `concept_id`; the sibling `compounds_in` takes a food id.
- **Called by.** `synthesis._derive_food_conclusions` (the derived 5b layer): for each compound-level conclusion it looks up `food_sources_for(subject)` and emits a *separately-labeled* food-level `DerivedConclusion`, citing the exact `found_in` edge + `ontology_version` as the provenance for the inference — reusing the compound claim's evidence, adding none.
- **Returns.** `CompositionEdge`s resolved to canonical names + types. Each edge is asserted by the curated ontology (no source span), so it carries its own `ontology`/`ontology_version` — the dual-provenance discipline reaching into the reference layer. *(The hierarchy half of this concept is still planned.)*

---

### Concept 18 — Open relation discovery: relationship statements, before any schema

**What.** A **discovery-first** stage that imposes *no* structure on relationships at all — not a
predicate, not even a subject/object split. A local, purpose-built model reads the canonical text and
flags the **spans that assert *some* relationship**; each flagged span is recorded **verbatim and
whole** as an `OpenObservation`, anchored to its exact offsets, and served for a human to read —
most-confident first, in the paper's own words. Despite the name, an `OpenObservation` carries **no**
subject/predicate/object: that absence *is* the design.

**Why it carries no predicate.** A `(subject, predicate, object)` triple bakes in binarity, a single
connecting predicate, and an argument/predicate segmentation — all *schema decided in advance*.
Dietary/biomedical relationships are routinely n-ary and conditional (dose, population, disease state,
duration — the very richness Part II treats as first-class), so committing to a triple would throw
exactly that away. This stage commits to nothing; it just surfaces the natural-language relationship
for reading. That is a deliberate trade: whole spans are maximally faithful but **not aggregable** (with
no predicate delineated you can't yet cluster "how predicates are phrased") — fidelity now, structure
later.

**Why it can't be a trusted `Observation`/`Claim` (Concept 9).** A trusted observation requires a
controlled `predicate`, a `rule_id`/`rule_version`, and a `polarity`/`certainty` — everything a claim
key and synthesis grouping depend on. A relation-bearing span has none of those, by design. So this is
a **parallel, clearly-labelled** record in its **own** table with its **own** endpoints; it is **never**
read by `claims.py`/`synthesis.py` and cannot leak into the trusted pipeline. That separation mirrors
how the legacy hosted-LLM path is "kept separate and non-deployed; the two never mix."

**How it keeps provenance while imposing no structure.** Because the unit of record *is* a
sentence/clause of the canonical text, provenance is the simplest it can be: `text ==
document.text[start:end]`, so the single `EvidenceRef` is `EXACT_SPAN` **by construction** — no argument
re-anchoring, no generative-rewrite problem, the offset contract (Concept 2) holds for free. The
detector is still a *model*, hence non-deterministic, so — exactly as the guiding rule requires — each
record stores *how it was produced*: the detector **name + version** and the model's **confidence
score**. Auditable though not reproducible. There is no model-free floor: detection is a model feature
by definition, and it feeds nothing downstream that would need a deterministic fallback.

**Alternatives / extension points.** The detector is a **pluggable protocol** (`RelationBearingDetector`)
— comparing which sentences different models flag is itself part of the review. The recommended default
reuses an off-the-shelf open-RE/OpenIE model *purely as a detector*: run it, keep the sentences it fires
on + its score, and **discard its triples entirely** (leveraging a model "designed for the job" while
committing to none of its structure). A dedicated relation-sentence classifier is an alternative adapter
behind the same protocol. The follow-up chain this leaves open — read the flagged sentences → decide
what a relationship should decompose into → delineate predicate surfaces → induce a curated vocabulary →
a second-layer extractor that maps into claims — is all deliberately out of scope; this concept produces
only the reviewable *inventory* of relationship statements.

**In the code** — `knowledge/openrel.py` (detector + extraction), `knowledge/openobs.py` (the model),
served over `POST /discover/relations` + `GET /discover/relations/{pmid}` (`docs/api.md`).

```python
def extract_open_observations(document, *, detector=None) -> list[OpenObservation]:
    detector = detector or _default_detector()          # a model; no deterministic floor
    out = []
    for span in detector.detect(document):              # spans it flags as relation-bearing
        out.append(OpenObservation(
            open_observation_id=open_observation_id(document.document_id, span.start_char,
                                                    span.end_char, detector.name),
            text=document.text[span.start_char:span.end_char],   # verbatim, whole
            detector_name=detector.name, detector_version=detector.version, score=span.score,
            evidence_refs=[EvidenceRef.for_span(document, span.start_char, span.end_char, ...)],  # EXACT_SPAN
            ...))
    out.sort(key=lambda o: (-o.score, o.start_char, o.end_char))  # most-confident first, for review
    return out
```

- **Called by.** `pipeline.discover_open_relations` — a **standalone**, opt-in entry point that does
  *not* run inside `extract_document` (discovery is orthogonal to the trusted pass and must never slow or
  contaminate it). Persisted by `db.persist_open_observations` (delete-by-document + insert) under an
  `ExtractionRun` whose fingerprint includes the detector name + version (Concept 4).
- **Returns.** `OpenObservation`s ordered most-confident first, each a verbatim span with an `EXACT_SPAN`
  ref and the detector's name/version/score — never a subject/predicate/object.

---

### Concept 19 — Qualified entities: restrictive modifiers on the entity head

**What.** An entity mention can carry a set of typed **restrictive modifiers** — the prepositional
phrase that specifies *which* one the sentence meant. "dysbiosis **of the gut microbiome**" keeps
resolving its head to the bare concept `DIS:dysbiosis`, but the mention *additionally* records the
modifier as a **text-derived concept→concept edge**: `dysbiosis —localized_in→ gut microbiome`
(`BIOM:gut_microbiota`). The modifier's value is itself a resolved concept, so this is a real edge
between two vocabulary nodes, not free text. Like a qualifier (Concept 14) it is a *condition on
meaning, not an assertion* — it has **no polarity**, and an object that doesn't normalize is kept
`unmatched` with its surface, never dropped.

**Why this, and not the two tempting alternatives.** Widening the entity span to *"dysbiosis of the
gut microbiome"* would break normalization (Concept 6): that string isn't a vocabulary surface form,
so it would fall to `unmatched` and produce no claim — the head *must* stay resolvable. Folding the
phrase into a relation-level qualifier (Concept 14) is the wrong owner too: *"of the gut microbiome"*
restricts the *dysbiosis entity*, it doesn't state a condition the IBD↔dysbiosis relation holds
under. So the modifier is a third slot, on the *entity*, sitting exactly parallel to the qualifier
layer — same discipline (versioned cues, its own `EvidenceRef`), new home.

**Why the relation is decided by the object, never the preposition.** "of" is ambiguous — "dysbiosis
*of* the gut microbiome" (localization) vs "risk *of* cancer" (an endpoint) vs "reduction *of*
inflammation" (aboutness). So a curated cue table (`vocab/modifier_relations.json`) types the edge
from the *resolved object*: an object in the site-concept allowlist under a localizing preposition is
`localized_in`; any other attributive `of`/`within` phrase falls back to the generic, deliberately
**non-discriminating** `qualified_by`; a condition preposition like *"in remission"* is left to the
qualifier layer and yields no modifier. This gives two clean guards for free: a disease-state cue
resolves against the disease-state vocabulary (not the entity one), so it can't become a modifier;
and a `risk of X` object hangs off the non-entity word "risk", so it's never walked as an entity
head's modifier.

**Relationship to the concept graph.** This is the *text-derived* sibling of Concept 17's curated
`found_in` edges: same "edge between two concepts" shape, opposite provenance — a modifier is
justified by an `EXACT_SPAN` over the connecting phrase, whereas a composition edge is asserted by
the ontology with a version and no span. Both extend *how concepts relate* without touching how
claims are extracted.

**How a discriminating modifier splits a claim.** A *key-bearing* modifier (currently
`localized_in`; the generic `qualified_by` is deliberately not) folds into the claim key exactly the
way a qualifier does (Concept 14): `claims.normalize` appends each endpoint's
`modifiers.signature(...)` to the grouping key and to `_claim_id` — but **only when non-empty**, so
an endpoint with no key-bearing modifier hashes the *identical* string it did before and every
existing claim id is byte-for-byte unchanged. The consequence is that *"dysbiosis of the gut
microbiome"* and a bare *"dysbiosis"* on the same `IBD —associated_with→` relation become **two
distinct claims**, and cross-paper synthesis (Concept 13) carries the same signature into its
grouping key, so a localized pair aggregates as its own conclusion rather than silently merging back
into the bare pair. The endpoint modifiers ride along on the observation (from its mention), are the
deduped union on the claim, and persist to `claim_modifiers` beside `claim_qualifiers`.

**What is still deferred.** Only `localized_in` is key-bearing today, and `DIS:dysbiosis` still lists
*"gut dysbiosis"* as a surface form — so the single-word adjectival form is pre-collapsed and only
the prep-phrase form is recovered and split. Widening the site allowlist and an adjectival detector
are the natural next steps (below).

**Extension points.** More relations (`part_of`, `derived_from`, `measured_in`), each a new
`(preposition, object)` cue; a richer site allowlist (add anatomy/locus concepts to the vocabulary
so more objects type as `localized_in`); the claim-key contribution described above; and an
*adjectival* detector — `DIS:dysbiosis` currently lists *"gut dysbiosis"* as a surface form, so the
single-word modified form is pre-collapsed onto the bare concept and only the prep-phrase form is
recovered here.

**In the code** — `knowledge/modifiers.py`

```python
def extract(document, mentions, *, use_model=True) -> dict[str, list[EntityModifier]]:
    # Parse detector: walk each mention's anchor-token prep-phrase children (parse.modifier_phrases),
    #   resolve the object (prefer an existing entity mention under it, else normalize the phrase).
    # Deterministic floor (use_model=False): two consecutive mentions joined by exactly a modifier
    #   preposition ("<head> of the <object>") form the edge, reusing both existing spans.
    ...

def _relation_for(preposition, concept_id, status):        # never guess from the preposition alone
    if status == "normalized" and concept_id in SITE_CONCEPTS and preposition in LOCALIZING_PREPS:
        return "localized_in"
    if preposition in ATTRIBUTIVE_PREPS:                   # of / within → generic, non-key-bearing
        return "qualified_by"
    return None                                            # e.g. a condition "in" phrase → not a modifier

def signature(modifiers) -> str:                           # folded into the claim key (A2); EMPTY ⇒ id unchanged
    parts = sorted((m.relation, m.value_concept_id or _key(m.value_text))
                   for m in modifiers if m.relation in KEY_BEARING_RELATIONS)  # qualified_by never contributes
    return "|".join(f"{rel}={val}" for rel, val in parts)
```

- **Parameters.** `document` + its `mentions`, and `use_model` (parse detector vs the model-free
  floor). `signature` takes a modifier list.
- **Called by.** `modifiers.annotate(...)`, a post-pass in `pipeline.extract_document` and
  `entities.analyze_document` that assigns each mention's `modifiers` list after the parse is
  available; persisted by `db.persist_entities` into `entity_modifiers` (delete-then-insert,
  idempotent); surfaced by `query.list_observations_for_document` as `subject_modifiers`/
  `object_modifiers` so the rendered observation reads *"Dysbiosis [localized_in: gut microbiome]"*.
  `signature` is called by `claims.normalize` (endpoint modifiers ride from mention → observation →
  the claim key + the `claim_modifiers` rows) and by `synthesis` (via `signature_from_dicts` over the
  persisted claim payload), so the localized-vs-bare split holds through claim normalization *and*
  cross-paper aggregation.
- **Returns.** `mention_id → [EntityModifier, …]` — each a *typed restrictive modifier* with **no
  polarity**, its own `EXACT_SPAN` ref over the connecting phrase, and `status=unmatched` (surface
  retained) when the object doesn't resolve. A mention with no modifier is simply absent, and a claim
  with no *key-bearing* modifier keeps its exact pre-A2 id.

---

### Concept 20 — Hierarchical relations: nesting a relation under its parent

**What.** A relation is still one flat binary edge (Concept 7) with sentence provenance — but it may
now carry a pointer to the relation it *elaborates*. The rule is purely structural: **a relation
whose subject is another relation's object, within the same sentence, is nested beneath it.** From

> *"inflammation induces dysbiosis characterized by alterations in the composition and function of
> the gut microbiota, decreasing Firmicutes, the Bifidobacterium genus, and Faecalibacterium
> prausnitzii"*

the engine produces a little tree:

```
inflammation ─causes→ dysbiosis                                              (parent)
  ├ dysbiosis ─characterized_by→ "alterations in the composition and         (reduced relative; whole
  │                              function of the gut microbiota"              descriptor phrase is the object)
  ├ dysbiosis ─has_decreased_abundance_of→ Firmicutes                        (participle adjunct ", decreasing …")
  ├ dysbiosis ─has_decreased_abundance_of→ Bifidobacterium                   (the appos-split middle list-item)
  └ dysbiosis ─has_decreased_abundance_of→ Faecalibacterium prausnitzii
```

Two small extraction additions make the children *reachable*, and one post-pass records the *link*:

1. **A descriptive predicate, `characterized_by`** (Concept 7). "X characterized by Y" asserts no
   effect on X — it says what X *is*. It is clinically `NEUTRAL` (Concept 16) and lexically negative
   only in the trivial sense, so it never flips on negation. Its object is the phrase inside the
   `by`-cue (an agent `nmod`, so the existing object resolver already finds it). When that phrase is a
   bare entity the object is that entity; when its head noun is an abstract noun that merely dominates
   an entity (*"alterations in the composition and function **of** the gut microbiota"*), the object
   is the **whole head-noun phrase**, verbatim — not the deep entity (*gut microbiota*) the general
   resolver would descend to, which would assert the wrong thing. That free-text object carries no
   `concept_id` (Phase 15; it replaced an earlier design that kept the deep entity as the object plus a
   `manifestation` qualifier — see Concept 14's history note).
2. **A participle free-adjunct subject rule** (Concept 7). A `VBG`/`VBN` participle on an `advcl`
   arc that spells out no subject — *"…, **decreasing** Firmicutes"*, *"…, **characterized** by …"* —
   predicates over what the governing clause just introduced: its **object** (the dysbiosis), not its
   subject (the inflammation). Resolving to the governing object is also what *prevents* the generic
   controller fallback from fabricating the governing-subject reading (it would otherwise return both).
   Because that subject is a *state* and not an agent, a directional `decreasing`/`increasing`
   participle is **re-labelled** to the descriptive `has_decreased_abundance_of` /
   `has_increased_abundance_of` predicate (Concept 7) — a manifestation of the state, not an effect it
   exerts. Its coordinated object list binds every taxon, including the apposition-split middle one.
3. **A parent-link post-pass** (`observations._link_parents`). After both binders have run, each
   sentence's observations are ordered by span and each is linked to the earliest *earlier* one whose
   object mention is this one's subject mention. Deriving the link from the bound endpoints — not from
   the parse — means it serves **both binders**: on the model path the subordinate clauses resolve
   their subjects to the shared governing object, so several children hang off one parent (a *tree*);
   on the model-free floor adjacency resolves each subject to the previous object, so the same rule
   degrades to a *chain*. It is **additive**: it never changes which observations are emitted, only
   annotates them, and it is not part of the observation id, so ids stay stable.

The link rides up to the claim layer too: a claim's `parent_claim_id` is lifted from its
observations' parents, but **only when unambiguous** — when every parent observation that formed a
claim points at the same claim. Otherwise it is left null; and it is *necessarily* null when the
parent relation formed no claim at all (its subject never normalized, as *inflammation* does not
above). That is the Concept 3 discipline again: claim-level nesting may point only at a real claim,
never a guess, even though the finer-grained observation layer still records the link.

**Why.** A flat list throws away a real part of the meaning: that the characterization and the
bacterial decreases are *about the dysbiosis*, not three unrelated facts that happen to share a
sentence. Nesting recovers that structure **without** abandoning the engine's spine — every node is
still a binary, span-anchored, concept-normalized edge that stands on its own and folds into claims
exactly as before. The hierarchy is *derived from* those edges, not a parallel representation that
could drift from them. This is deliberately a different hierarchy from Concept 17's **planned
concept-type** hierarchy (salmon → oily fish → fish): that one is about what concepts *are*; this one
is about how *relations* in a sentence depend on one another.

**Why keep the honest nulls.** The nested reading is only as good as the parse, and the participle
rule is a genuine heuristic that can misfire on a non-manifestation adjunct. So the link is *always*
optional: a relation with no resolvable parent is simply top-level, a claim whose parent is ambiguous
or non-normalizing keeps a null parent, and nothing downstream (claims folding, synthesis) *requires*
the hierarchy — it is metadata a reader can trust precisely because it is never fabricated.

**Alternatives / extension points.**
- The parent rule links on `object mention == subject mention`. A richer version could use the parse's
  subordination type to distinguish *elaboration* (`characterized_by`) from *consequence*
  (`decreasing`) and label the edge accordingly, rather than leaving both as a bare parent pointer.
- `characterized_by` was the first *descriptive* predicate; the `has_decreased_abundance_of` /
  `has_increased_abundance_of` manifestations now join the same clinically-neutral family.
  `defined_as`/`comprises`/`consists_of` would extend it further, each a new cue + verb-map entry.
- The abundance manifestations are deliberately neutral because the engine does not know whether a
  given taxon is beneficial or harmful. A per-taxon desirability vocabulary (like
  `valence._DESIRABLE_CONCEPTS`) could later let *"decreased Firmicutes"* read as harmful and
  *"increased Proteobacteria"* as harmful without changing the predicate.
- Synthesis (Concept 13) currently treats every claim independently; the parent link is additive
  metadata it could later use to roll a condition's sub-effects up under the condition.

**In the code** — `knowledge/observations.py` (`_link_parents`, and the participle re-label +
descriptive-object step `_descriptive_object_mention` in `_parse_bind_sentence`), `knowledge/claims.py`
(`_link_parent_claims`), `knowledge/parse.py` (`participle_subject_tokens`, `_coordinate_list` for
apposition list-items, `descriptive_object_span` for the whole descriptor phrase), `knowledge/relations.py`
(the `characterized_by` rule + `characterize` verb-map entry; the `has_decreased_abundance_of` /
`has_increased_abundance_of` rules + `abundance_manifestation_for`), and the gut-taxa concepts in
`knowledge/vocab/dictionaries.json`.

```python
# observations._link_parents — derive the nesting from the bound endpoints (both binders).
for idx, child in enumerate(ordered):            # ordered by (subject span, object span, predicate)
    for parent in ordered[:idx]:                 # only an EARLIER one ⇒ acyclic
        if parent.object_mention_id == child.subject_mention_id:
            child.parent_observation_id = parent.observation_id
            break
```

- **Parameters.** `_link_parents(observations, mentions)` — the sentence's observations plus the
  mentions (for endpoint spans); `participle_subject_tokens(verb)` — a candidate predicate token.
- **Called by.** `observations.extract` (once, after both binders) and `claims.normalize` (which then
  lifts the link to `parent_claim_id`). The participle rule is consulted by `observations._parse_bind_sentence`
  before the generic subject cascade, and mirrored in the `/observe/deconstruct` trace.
- **Returns.** Nothing — both linkers mutate in place, setting `parent_observation_id` /
  `parent_claim_id` (persisted to the additive `observations.parent_observation_id` /
  `claims.parent_claim_id` columns, and surfaced on the REST observation/claim payloads).

---

## The whole journey, in one breath

```
PMID
 → acquire raw XML (network, once)                     [Concept 1]
 → immutable corpus + checksums                        [Concept 1]
 → canonical document, one text, absolute offsets      [Concept 2]  ← the offset contract
 → entity mentions (scispaCy detection + dictionary floor)[Concepts 5,6]
   (+ restrictive modifiers on the head: dysbiosis —localized_in→ gut microbiome) [Concept 19]
 → observations (rule-based relations + modality)      [Concepts 7,8,9]  ← audit layer
 → claims (concept-level, grouped, hashed)             [Concept 9]       ← concept layer
 → study / funding / affiliations facts                [Concept 10]
 → assessment (pure function, separate table)          [Concept 11]
 → SQLite, all tied to one versioned ExtractionRun     [Concepts 3,4]
 → [batch] cohesion: drop off-topic outliers           [Concept 12]
 → [batch] synthesis: conclusions, conflicts surfaced  [Concept 13]
 → JSON result
   (+ Part II: qualifiers, clause scope, valence, concept graph, qualified entities — conditional knowledge)

[separate, opt-in sandbox — never enters the trusted path above]
PMID → canonical document → open relation discovery (a model flags relation-bearing
       spans, recorded verbatim with EXACT_SPAN provenance)                   [Concept 18]
```

And every single row along that path carries an `EvidenceRef` back to an exact character span — which
is the one idea this whole document started with.

---

## The mental model to keep

Whenever you're deciding how to add or change something here, run it against the guiding question:

> Can the system still show which paper, and — wherever the text supports it — which section,
> sentence, or phrase produced this fact, offline, without an online service, and without hiding
> what it's unsure of?

If yes, it fits the engine — a local model's judgement, an embedding match, even a non-reproducible
score are all welcome, as long as the fact stays anchored to the source and the resolver that
produced it is recorded. If no — if it needs a hosted API, or it emits a fact with no traceable
source, or it silently drops what it couldn't handle — it belongs in a different layer (like the
separate legacy LLM path), not this one. That single test explains every "why" above.
