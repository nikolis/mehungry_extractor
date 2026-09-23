"""Batch driver: pull pending pairs, fetch text, extract, post candidates.

    mehungry-extract --limit 50
    python -m mehungry_extractor --limit 50 --no-ner

Requires env: LOCAL_AI_SERVER_URL, LOCAL_AI_API_TOKEN, ANTHROPIC_API_KEY
(optional: NCBI_API_KEY, EXTRACTOR_MODEL).
"""

from __future__ import annotations

import argparse
import sys

from . import extract, ner, pmc
from .client import Client

BATCH = 25


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mehungry-extract", description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="max pairs to process")
    parser.add_argument("--no-ner", action="store_true", help="skip biomedical NER grounding")
    parser.add_argument("--dry-run", action="store_true", help="extract but do not POST candidates")
    args = parser.parse_args(argv)

    client = Client()
    use_ner = (not args.no_ner) and ner.available()
    print(f"mehungry-extract: NER grounding {'on' if use_ner else 'off'}; up to {args.limit} pairs")

    processed = posted = 0
    remaining = args.limit

    while remaining > 0:
        take = min(remaining, BATCH)
        data = client.pending(limit=take)
        pairs = data.get("pairs", [])
        if not pairs:
            break

        for pair in pairs:
            posted += _process_pair(client, pair, use_ner, args.dry_run)
            processed += 1

        remaining -= len(pairs)

    print(f"done: processed {processed} pairs, posted {posted} candidate findings")
    return 0


def _process_pair(client: Client, pair: dict, use_ner: bool, dry_run: bool) -> int:
    study_id, condition = pair["study_id"], pair["condition"]
    pmid = pair["pmid"]

    try:
        doc = pmc.fetch(str(pmid))
    except Exception as exc:  # noqa: BLE001 — one bad fetch must not kill the batch
        print(f"  study {study_id} (pmid {pmid}): fetch failed ({exc}); ledgering empty", file=sys.stderr)
        _ledger_empty(client, study_id, condition["id"], dry_run)
        return 0

    hints = ner.chemical_terms(doc.text) if use_ner else []

    try:
        findings = extract.extract(condition, pair.get("states", []), doc.text, hints)
    except Exception as exc:  # noqa: BLE001
        print(f"  study {study_id} (pmid {pmid}): extraction failed ({exc}); ledgering empty", file=sys.stderr)
        _ledger_empty(client, study_id, condition["id"], dry_run)
        return 0

    payload = [f.model_dump(exclude_none=True) for f in findings.findings]
    print(f"  study {study_id} · {condition['name']} [{doc.source}]: {len(payload)} findings")

    if dry_run:
        return len(payload)

    result = client.post_candidates(study_id, condition["id"], payload)
    return int(result.get("written", 0))


# Post an empty finding list so the server still ledgers the attempt and the pair
# leaves the pending set (matching the measurement extractor's termination guarantee).
def _ledger_empty(client: Client, study_id: int, condition_id: int, dry_run: bool) -> None:
    if not dry_run:
        client.post_candidates(study_id, condition_id, [])


if __name__ == "__main__":
    raise SystemExit(main())
