"""Staged CLI for the deterministic engine (spec §18): ``mehungry <stage>``.

Each stage is independently rerunnable and reads/writes the same immutable corpus + SQLite
DB, so stages compose without hidden state:

    mehungry ingest    --pmid 12345678            # network: acquire → archive → canonical → DB
    mehungry ingest    --pmids 111,222 --force
    mehungry normalize --document pmid:12345678   # offline: rebuild canonical from cached raw
    mehungry export    --document pmid:12345678   # print canonical document.json
    mehungry list                                 # list ingested documents
    mehungry analyze   --document pmid:12345678   # offline: extract + normalize entities
    mehungry extract   --document pmid:12345678   # offline: relations/claims + study/funding + assessments
    mehungry audit     --claim <claim_id>         # print a claim's full provenance block
    mehungry audit     --document pmid:12345678   # list a paper's claim ids
    mehungry eval-gold                            # score extraction vs the hand-labeled gold set
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .corpus import CorpusStore
from .db import get_engine, init_db
from .ids import normalize_pmid
from .canonical import to_canonical_json


def _resolve_pmid(value: str) -> str:
    v = value.strip()
    if v.startswith("pmid_"):
        v = v[len("pmid_") :]
    return normalize_pmid(v)


def _pmids_from_args(args) -> list[str]:
    out: list[str] = []
    if getattr(args, "pmid", None):
        out.append(_resolve_pmid(args.pmid))
    if getattr(args, "pmids", None):
        out.extend(_resolve_pmid(p) for p in args.pmids.split(",") if p.strip())
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as fh:
            out.extend(_resolve_pmid(line) for line in fh if line.strip())
    # de-dup, preserve order
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def _corpus(args) -> CorpusStore:
    return CorpusStore(getattr(args, "corpus", None))


def _engine(args):
    engine = get_engine(getattr(args, "db", None))
    init_db(engine)
    return engine


# --- stages ---------------------------------------------------------------------


def _stage_ingest(args) -> int:
    from .ingest import ingest_pmid

    pmids = _pmids_from_args(args)
    if not pmids:
        print("ingest: provide --pmid, --pmids, or --file", file=sys.stderr)
        return 2

    corpus = _corpus(args)
    engine = None if args.no_persist else _engine(args)
    rc = 0
    for pmid in pmids:
        try:
            doc = ingest_pmid(
                pmid, force=args.force, corpus=corpus, engine=engine, persist=not args.no_persist
            )
        except Exception as exc:  # noqa: BLE001 — one failure must not abort the batch
            print(f"  pmid {pmid}: FAILED ({exc})", file=sys.stderr)
            rc = 1
            continue
        n_sec = len(doc.sections)
        n_sent = sum(1 for _ in doc.iter_sentences())
        print(
            f"  {doc.document_id} [{doc.source_type}]: "
            f"{n_sec} sections, {n_sent} sentences, {len(doc.text)} chars"
        )
    return rc


def _stage_normalize(args) -> int:
    from .ingest import normalize_document

    pmids = _pmids_from_args(args)
    if not pmids:
        print("normalize: provide --document/--pmid, --pmids, or --file", file=sys.stderr)
        return 2

    corpus = _corpus(args)
    engine = None if args.no_persist else _engine(args)
    rc = 0
    for pmid in pmids:
        try:
            doc = normalize_document(pmid, corpus=corpus, engine=engine, persist=not args.no_persist)
        except Exception as exc:  # noqa: BLE001
            print(f"  pmid {pmid}: FAILED ({exc})", file=sys.stderr)
            rc = 1
            continue
        print(f"  {doc.document_id} [{doc.source_type}]: rebuilt from cached raw")
    return rc


def _stage_export(args) -> int:
    pmids = _pmids_from_args(args)
    if not pmids:
        print("export: provide --document/--pmid", file=sys.stderr)
        return 2
    corpus = _corpus(args)
    for pmid in pmids:
        if not corpus.has_canonical(pmid):
            print(f"  pmid {pmid}: no canonical document (run ingest/normalize first)", file=sys.stderr)
            return 1
        sys.stdout.write(to_canonical_json(corpus.read_canonical(pmid)))
    return 0


def _stage_list(args) -> int:
    from .query import list_documents

    engine = _engine(args)
    docs = list_documents(engine)
    print(json.dumps(docs, indent=2, ensure_ascii=False))
    return 0


def _stage_analyze(args) -> int:
    from .entities import analyze_document

    pmids = _pmids_from_args(args)
    if not pmids:
        print("analyze: provide --document/--pmid, --pmids, or --file", file=sys.stderr)
        return 2

    corpus = _corpus(args)
    engine = None if args.no_persist else _engine(args)
    rc = 0
    for pmid in pmids:
        try:
            doc, mentions = analyze_document(
                pmid, corpus=corpus, engine=engine, persist=not args.no_persist
            )
        except Exception as exc:  # noqa: BLE001 — one failure must not abort the batch
            print(f"  pmid {pmid}: FAILED ({exc})", file=sys.stderr)
            rc = 1
            continue
        n_norm = sum(1 for m in mentions if m.status == "normalized")
        print(
            f"  {doc.document_id} [{doc.source_type}]: "
            f"{len(mentions)} mentions ({n_norm} normalized)"
        )
    return rc


def _stage_extract(args) -> int:
    from .pipeline import extract_document

    pmids = _pmids_from_args(args)
    if not pmids:
        print("extract: provide --document/--pmid, --pmids, or --file", file=sys.stderr)
        return 2

    corpus = _corpus(args)
    engine = None if args.no_persist else _engine(args)
    rc = 0
    for pmid in pmids:
        try:
            s = extract_document(pmid, corpus=corpus, engine=engine, persist=not args.no_persist)
        except Exception as exc:  # noqa: BLE001 — one failure must not abort the batch
            print(f"  pmid {pmid}: FAILED ({exc})", file=sys.stderr)
            rc = 1
            continue
        print(
            f"  {s.document_id}: {s.mentions} mentions, {s.observations} observations, "
            f"{s.claims} claims ({s.dropped_observations} dropped), "
            f"{s.qualifiers} qualifiers, "
            f"{s.study_characteristics} study facts, {s.funding_relationships} funders, "
            f"{s.assessments} assessments"
        )
    return rc


def _stage_audit(args) -> int:
    from .audit import render_claim
    from .query import list_claims_for_document

    engine = _engine(args)

    if getattr(args, "claim", None):
        block = render_claim(engine, args.claim)
        if block is None:
            print(f"audit: no claim {args.claim!r} (run `mehungry extract` first?)", file=sys.stderr)
            return 1
        sys.stdout.write(block)
        return 0

    pmids = _pmids_from_args(args)
    if not pmids:
        print("audit: provide --claim <id>, or --document/--pmid to list a paper's claims", file=sys.stderr)
        return 2
    for pmid in pmids:
        claims = list_claims_for_document(engine, pmid)
        if not claims:
            print(f"  {pmid}: no claims", file=sys.stderr)
            continue
        for c in claims:
            quals = "".join(
                f"  [{q['qualifier_type']}: {q['value_concept_id'] or q['value_text']}]"
                for q in c.get("qualifiers") or []
            )
            print(
                f"  {c['claim_id']}  {c['subject_name']} — {c['predicate']} "
                f"({c['polarity']}, {c['certainty']}) — {c['object_name']}{quals}"
            )
    return 0


def _stage_eval_gold(args) -> int:
    from .audit import render_gold_eval

    engine = _engine(args)
    sys.stdout.write(render_gold_eval(engine, getattr(args, "gold", None)))
    return 0


# --- arg parsing ----------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser, *, doc_selectors: bool = True) -> None:
    if doc_selectors:
        p.add_argument("--pmid", help="a single PMID")
        p.add_argument("--document", dest="pmid", help="alias for --pmid (accepts pmid:NNNN)")
        p.add_argument("--pmids", help="comma-separated PMIDs")
        p.add_argument("--file", help="path to a file of newline-separated PMIDs")
    p.add_argument("--corpus", help="corpus root dir (default: $MEHUNGRY_CORPUS_DIR or data/corpus)")
    p.add_argument("--db", help="SQLite path (default: $MEHUNGRY_DB or data/mehungry.sqlite)")
    p.add_argument("--no-persist", action="store_true", help="skip writing to the SQLite DB")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mehungry", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="stage", required=True)

    p_ingest = sub.add_parser("ingest", help="acquire + archive + build canonical (network)")
    _add_common(p_ingest)
    p_ingest.add_argument("--force", action="store_true", help="refresh cached raw sources")
    p_ingest.set_defaults(func=_stage_ingest)

    p_norm = sub.add_parser("normalize", help="rebuild canonical from cached raw (offline)")
    _add_common(p_norm)
    p_norm.set_defaults(force=False, func=_stage_normalize)

    p_export = sub.add_parser("export", help="print canonical document.json")
    _add_common(p_export)
    p_export.set_defaults(func=_stage_export)

    p_list = sub.add_parser("list", help="list ingested documents")
    _add_common(p_list, doc_selectors=False)
    p_list.set_defaults(func=_stage_list)

    p_analyze = sub.add_parser("analyze", help="Phase 2 — extract + normalize entities (offline)")
    _add_common(p_analyze)
    p_analyze.set_defaults(func=_stage_analyze)

    p_extract = sub.add_parser(
        "extract", help="Phases 3-5 — relations/claims + study/funding + assessments (offline)"
    )
    _add_common(p_extract)
    p_extract.set_defaults(func=_stage_extract)

    p_audit = sub.add_parser("audit", help="Phase 5 — print a claim's full provenance block")
    _add_common(p_audit)
    p_audit.add_argument("--claim", help="claim id to audit")
    p_audit.set_defaults(func=_stage_audit)

    p_eval = sub.add_parser(
        "eval-gold", help="score extraction against the hand-labeled gold set (precision/recall)"
    )
    _add_common(p_eval, doc_selectors=False)
    p_eval.add_argument("--gold", help="path to gold JSON (default: tests/gold/observations.json)")
    p_eval.set_defaults(func=_stage_eval_gold)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
