"""Staged CLI for the deterministic engine (spec §18): ``mehungry <stage>``.

Each stage is independently rerunnable and reads/writes the same immutable corpus + SQLite
DB, so stages compose without hidden state:

    mehungry ingest    --pmid 12345678            # network: acquire → archive → canonical → DB
    mehungry ingest    --pmids 111,222 --force
    mehungry normalize --document pmid:12345678   # offline: rebuild canonical from cached raw
    mehungry export    --document pmid:12345678   # print canonical document.json
    mehungry list                                 # list ingested documents

    mehungry analyze  ...   # Phase 2 (entities)        — not yet implemented
    mehungry extract  ...   # Phase 3 (relations/claims) — not yet implemented
    mehungry audit    ...   # Phase 5 (audit rendering)  — not yet implemented

The legacy LLM path keeps its own ``mehungry-extract`` entry point, untouched.
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


def _stage_not_implemented(phase: str, name: str):
    def _run(_args) -> int:
        print(f"`mehungry {name}` is not yet implemented (arrives in {phase}).", file=sys.stderr)
        return 2

    return _run


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

    for name, phase in (("analyze", "Phase 2"), ("extract", "Phase 3"), ("audit", "Phase 5")):
        p = sub.add_parser(name, help=f"{phase} — not yet implemented")
        p.set_defaults(func=_stage_not_implemented(phase, name))

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
