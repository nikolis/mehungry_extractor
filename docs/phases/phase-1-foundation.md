# Phase 1 — Foundation (DONE)

The deterministic base every later phase builds on. This file is the "what you can rely on"
contract for Phases 2–5. It documents the delivered surface; it is not a to-do list.

## What exists

```
PubMed/PMC → acquire → immutable corpus → JATS/PubMed parse → segment
          → canonical Document/Section/Paragraph/Sentence (absolute offsets)
          → SQLite;  + EvidenceRef provenance;  + ExtractionRun reproducibility
```

All under `mehungry_extractor/knowledge/`. Runs offline; this foundation stage is deterministic
parsing (no models). Later stages may use local non-deterministic techniques — see the updated
foundation in [`docs/phases/README.md`](README.md) — but every fact must keep its provenance span.

## Public surface you will reuse

### Canonical model — `knowledge/canonical.py`
- `Document` (fields: `document_id`, `source_type`, `pipeline_version`, `text`, `sections`,
  `metadata: DocumentMetadata`, `checksums`). Convenience: `.pmid`, `.pmcid`, `.doi`,
  `.iter_sentences()`.
- `Section`, `Paragraph` (has `text` normalized + `raw_text` original), `Sentence`. Every
  object carries `start_char`/`end_char`.
- **Offset contract:** `document.text[obj.start_char:obj.end_char] == obj.text` for every
  section/paragraph/sentence. Enforced by `tests/test_canonical_offsets.py`.
- `ParsedSection(title, paragraphs)` — the structural input to `build_document(...)`.
- `build_document(metadata, sections, checksums)` and `to_canonical_json(document)`
  (deterministic: sorted keys, fixed indent, trailing newline).
- `normalize_ws(text)` — the one whitespace-normalization function; reuse it.

### IDs — `knowledge/ids.py`
Deterministic, hierarchical: `document_id(pmid)` → `pmid_12345678`,
`section_id`/`paragraph_id`/`sentence_id`, and `normalize_pmid(...)` (accepts `pmid:NNNN`).
A sentence ID names its paragraph/section/document, giving cheap traceability.

### Provenance — `knowledge/provenance.py`
- `ProvenancePrecision`: `EXACT_SPAN | SENTENCE | PARAGRAPH | SECTION | DOCUMENT | METADATA`.
- `EvidenceRef` with `document_id, section_id, paragraph_id, sentence_id, start_char,
  end_char, quoted_text, precision, extraction_rule, extraction_rule_version,
  extractor_version`.
- `EvidenceRef.for_sentence(document, sentence, ...)` — builds a self-consistent ref by
  slicing the canonical text. **Phase 2 should add `EvidenceRef.for_span(document, section_id,
  paragraph_id, sentence_id, start, end, precision=EXACT_SPAN)`** for sub-sentence spans.

### Parsing — `knowledge/jats.py`, `knowledge/pubmed.py`
- `jats.parse(xml) -> list[ParsedSection]` (body `<sec>`/`<p>`, hierarchy preserved).
  Note: **front/back matter (funding, conflicts, acknowledgements) is not parsed yet** —
  Phase 4 extends this.
- `pubmed.parse(xml) -> PubMedRecord` with `pmid, pmcid, doi, title, journal,
  publication_date, authors, publication_types, affiliations, abstract_sections`.
  `publication_types` and `affiliations` are already captured for Phases 3–4.

### Acquisition + corpus — `knowledge/acquire.py`, `knowledge/corpus.py`
- `acquire.fetch(pmid) -> RawSources` (raw `pubmed_xml`/`pmc_xml` bytes, `pmcid`,
  `source_urls`, `retrieval_timestamp`). Reuses `pmc.EUTILS`/`pmc._params`.
- `CorpusStore(root)` — immutable, idempotent archive:
  `save_raw`/`read_raw`/`read_checksums`, `save_canonical`/`read_canonical`,
  `save_metadata`/`read_metadata`, `has_raw`/`has_canonical`. `save_raw` refuses to
  overwrite without `force=True`. Layout: `data/corpus/pmid_X/{raw,canonical,metadata.json,
  checksums.json}`.

### Persistence — `knowledge/db/`
- Tables (`schema.py`): `extraction_runs`, `documents`, `document_versions`, `sections`,
  `paragraphs`, `sentences`. Indexes on `pmid/pmcid/doi`, `document_id`, and composite
  `ix_sentences_doc_span`.
- `persist_document(session, document, run)` (idempotent; replaces structural rows),
  `load_document(session, document_id)` (reverse trace back to a `Document`).
- Rows use plain FK columns, not ORM relationships — `persist_document` flushes parents
  before children. **Follow that pattern when adding child tables.**
- `session.py`: `get_engine(path)`, `init_db(engine)` (`create_all`), `session_scope(engine)`.

### Reproducibility — `knowledge/run.py`
- `build_run(document_ids) -> ExtractionRun` captures deterministic `run_id`, `git_commit`,
  `pipeline_version`, `ruleset_version`, `extractor_version`, `python_version`,
  `spacy_version`, `scispacy_version`, `ontology_versions` (empty now), `document_ids`.
  `run_id` is a hash of the version fields + doc ids (timestamp excluded) → idempotent writes.

### Orchestration + CLI — `knowledge/ingest.py`, `knowledge/stagecli.py`, `knowledge/query.py`
- `ingest_pmid(pmid, force, corpus, engine, persist)` (network) and
  `normalize_document(pmid, ...)` (offline, byte-reproducible) share `assemble_document(...)`.
- CLI `mehungry`: `ingest`, `normalize`, `export`, `list` live; `analyze`, `extract`,
  `audit` are `_stage_not_implemented` stubs waiting for Phases 2/3/5.
- `query.py`: `list_documents`, `get_document`, `list_sentences_for_document`,
  `get_source_span` implemented; `find_claims`, `find_papers`, `explain_claim` raise
  `NotImplementedError` naming their phase.

## Invariants later phases must not break
- `document.json` is a pure function of source bytes + `PIPELINE_VERSION` (volatile
  acquisition fields live only in `metadata.json`). Keep it that way.
- Raw sources are immutable. Never rewrite `raw/`.
- Every knowledge object gets an `EvidenceRef`.

## How to run
```bash
uv venv && uv pip install -e . pytest
pytest                                   # 31 tests, offline
mehungry ingest --pmid 32005824          # live
mehungry normalize --document pmid:32005824   # offline rebuild
mehungry export --document pmid:32005824 | head
```
