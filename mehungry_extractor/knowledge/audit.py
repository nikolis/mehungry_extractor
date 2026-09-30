"""Human-readable provenance/audit rendering (spec §15–§16).

``render_claim`` turns :func:`.query.explain_claim`'s provenance object into the audit block
from the spec: CLAIM, CONTEXT, SOURCE, STUDY, EVIDENCE, MATCH, FUNDING, ASSESSMENT, DOCUMENT
HASH, EXTRACTION RUN. The renderer is thin and deterministic: every quoted sentence has already
been re-sliced from the canonical text by ``explain_claim`` (``reconstructed_text``), and the
renderer asserts that slice equals the stored ``quoted_text`` so a drifted offset surfaces as an
error rather than a silently wrong quote.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Engine

from .query import explain_claim, gold_eval_report, relation_quality_report


def _fmt_polarity(claim: dict) -> str:
    return f"{claim['predicate']} ({claim['polarity']}, {claim['certainty']})"


def _fmt_qualifier(q: dict) -> str:
    """One qualifier as ``type: value`` — the concept when it normalized, else its surface."""
    if q.get("value_concept_id"):
        value = f"{q['value_text']} ({q['value_concept_id']})"
    else:
        value = f'"{q["value_text"]}" (unmatched)'
    return f"{q['qualifier_type']}: {value}"


def render_claim(engine: Engine, claim_id: str) -> Optional[str]:
    """Render the audit block for a claim, or ``None`` if the claim id is unknown."""
    data = explain_claim(engine, claim_id)
    if data is None:
        return None

    claim = data["claim"]
    doc = data["document"] or {}
    lines: list[str] = []

    lines.append(f"CLAIM     {claim['subject_name']} — {_fmt_polarity(claim)} — {claim['object_name']}")
    lines.append(f"          [{claim['claim_id']}]")
    for q in claim.get("qualifiers") or []:
        lines.append(f"QUALIFIER [{_fmt_qualifier(q)}]")
    if claim.get("context"):
        lines.append(f"CONTEXT   {claim['context']}")

    year = data["study"].get("publication_year", {}).get("value")
    lines.append(
        "SOURCE    "
        + f"PMID {doc.get('pmid', '?')}"
        + (f" · PMCID {doc['pmcid']}" if doc.get("pmcid") else "")
        + (f" · {year}" if year else "")
    )
    if doc.get("title"):
        lines.append(f"          {doc['title']}")
    if doc.get("journal"):
        lines.append(f"          {doc['journal']}")

    design = data["study"].get("study_design", {})
    sample = data["study"].get("sample_size", {})
    study_bits = []
    if design:
        study_bits.append(f"design={design['value']} ({design['source']})")
    if sample:
        study_bits.append(f"n={sample['value']}")
    if data["study"].get("follow_up"):
        study_bits.append(f"follow-up={data['study']['follow_up']['value']}")
    if data["study"].get("country"):
        study_bits.append(f"country={data['study']['country']['value']}")
    if study_bits:
        lines.append("STUDY     " + ", ".join(study_bits))

    lines.append("EVIDENCE")
    for ev in data["evidence"]:
        # Self-verify: the stored quote must equal the re-sliced canonical text.
        if (
            ev["reconstructed_text"] is not None
            and ev["quoted_text"] is not None
            and ev["reconstructed_text"] != ev["quoted_text"]
        ):
            raise ValueError(
                f"evidence offset drift for claim {claim_id}: stored quote != canonical slice "
                f"({ev['sentence_id']} [{ev['start_char']}:{ev['end_char']}])"
            )
        loc = ev["sentence_id"] or ev["paragraph_id"] or ev["section_id"] or doc.get("document_id", "")
        span = ""
        if ev["start_char"] is not None:
            span = f" [{ev['start_char']}:{ev['end_char']}]"
        lines.append(f"  ({ev['precision']}) {loc}{span}")
        quote = ev["reconstructed_text"] if ev["reconstructed_text"] is not None else ev["quoted_text"]
        if quote:
            lines.append(f'    "{quote}"')
        lines.append(
            f"    MATCH rule={ev['extraction_rule']} v{ev['extraction_rule_version']} "
            f"· extractor v{ev['extractor_version']}"
        )

    if data["funding"]:
        lines.append("FUNDING")
        for f in data["funding"]:
            lines.append(f"  {f['funder']} — {f['funder_type']} (source: {f['source']})")
    else:
        lines.append("FUNDING   (none recorded — funding independence: unknown)")

    if data["assessments"]:
        lines.append("ASSESSMENT")
        for a in data["assessments"]:
            lines.append(
                f"  [{a['framework_id']} v{a['framework_version']}] {a['criterion']}: {a['value']}"
            )
            lines.append(f"    {a['rationale']}")

    checksums = doc.get("checksums") or {}
    if checksums:
        lines.append("DOCUMENT HASH")
        for name in sorted(checksums):
            lines.append(f"  {name}: {checksums[name]}")

    run = data["run"]
    if run:
        lines.append(
            "EXTRACTION RUN  "
            + f"{run['run_id']} · pipeline v{run['pipeline_version']} · ruleset v{run['ruleset_version']}"
            + f" · extractor v{run['extractor_version']}"
        )
        if run.get("git_commit"):
            lines.append(f"                git {run['git_commit']}")
        if run.get("ontology_versions"):
            ov = ", ".join(f"{k}={v}" for k, v in sorted(run["ontology_versions"].items()))
            lines.append(f"                ontologies: {ov}")

    return "\n".join(lines) + "\n"


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def render_relation_quality(engine: Engine, run_id: Optional[str] = None) -> Optional[str]:
    """Render the relation-layer precision probe (see :mod:`.metrics`) as a text report.

    Deterministic and read-only. Returns ``None`` if there is no extraction run to report on.
    Rows are ordered by self-loop count (worst first) so the over-firing cues/documents lead.
    """
    data = relation_quality_report(engine, run_id)
    if data is None:
        return None

    total = data["total_claims"]
    loops = data["self_loops"]
    lines: list[str] = []
    scope = data["run_id"] if data["run_id"] else "* (all runs)"
    lines.append(f"RELATION QUALITY  run {scope}")
    lines.append(
        f"  self-loops        {loops}/{total} claims  ({_pct(loops, total)})"
        "   ← observed false-positive FLOOR"
    )
    if data["p_alias"] is not None:
        lines.append(f"  p_alias           {data['p_alias']:.3f}   (detectability: P adjacent both-normalized pair aliases)")
    if data["estimated_fp_rate"] is not None:
        lines.append(
            f"  est. FP rate      ~{100 * data['estimated_fp_rate']:.0f}%"
            "   ← floor / p_alias (model-based; assumes spurious pairs alias at background rate)"
        )

    def _table(title: str, table: dict, *, limit: Optional[int] = None) -> None:
        rows = sorted(table.items(), key=lambda kv: (-kv[1][0], kv[0]))
        if limit is not None:
            rows = rows[:limit]
        lines.append(title)
        for key, (self_loops, tot) in rows:
            lines.append(f"  {key:<34} {self_loops:>3}/{tot:<4} ({_pct(self_loops, tot)})")

    _table("BY PREDICATE  (self-loops / total)", data["by_predicate"])
    _table("BY RULE       (self-loops / total)", data["by_rule_id"])

    lines.append("MECHANISM  (self-loop root-cause buckets — see metrics docs)")
    for name, count in sorted(data["mechanism"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {name:<34} {count:>3}")

    # Documents with the highest self-loop counts — the best debugging fixtures.
    worst = [(k, v) for k, v in data["by_document"].items() if v[0] > 0]
    if worst:
        _table("WORST DOCUMENTS  (self-loops / total)", dict(worst), limit=10)

    return "\n".join(lines) + "\n"


def _rate(value: Optional[float]) -> str:
    return f"{100 * value:.0f}%" if value is not None else "—"


def render_gold_eval(engine: Engine, gold_path: Optional[str] = None) -> str:
    """Render the gold-standard evaluation (see :mod:`.goldeval`) as a text scorecard.

    Deterministic and read-only. Shows precision (strict/lenient), end-to-end vs achievable recall,
    the failure-mode histogram (the actionable part — it names which layer to fix), and a per-outcome
    detail trace so a low number is immediately traceable to the offending sentence.
    """
    data = gold_eval_report(engine, gold_path)
    c = data["counts"]
    lines: list[str] = []
    lines.append(f"GOLD EVALUATION  {data['n_sentences']} labeled sentences")
    lines.append(
        f"  correct {c['correct']}  field_error {c['field_error']}  "
        f"missed {c['missed']}  spurious {c['spurious']}"
        f"   (expected {c['expected']}, produced {c['produced']})"
    )
    lines.append("PRECISION  (of what the pipeline emits in labeled sentences)")
    lines.append(
        f"  strict            {_rate(data['precision_strict'])}"
        f"   ({c['correct']}/{c['produced']} exact)"
    )
    lines.append(
        f"  lenient           {_rate(data['precision_lenient'])}"
        f"   (+field_error: right entities, wrong label)"
    )
    lines.append("RECALL     (of what a perfect extractor should emit)")
    lines.append(
        f"  end-to-end strict {_rate(data['recall_strict'])}"
        f"   ({c['correct']}/{c['expected']} — includes out-of-vocab gold)"
    )
    lines.append(
        f"  achievable strict {_rate(data['recall_achievable_strict'])}"
        f"   ({c['achievable_correct']}/{c['achievable_expected']} — in-vocab gold only, isolates logic)"
    )
    lines.append(
        f"  achievable lenient{_rate(data['recall_achievable_lenient'])}"
        f"   (+field_error)"
    )
    lines.append(
        f"  F1 (strict) end-to-end {_rate(data['f1_strict'])}"
        f"   achievable {_rate(data['f1_achievable'])}"
    )

    fm = data["failure_mode"]
    if fm:
        lines.append("FAILURE MODES  (root-cause tally over non-correct outcomes — fix highest first)")
        for name, count in sorted(fm.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {name:<24} {count:>3}")

    if data["field_error_by_field"]:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(data["field_error_by_field"].items()))
        lines.append(f"FIELD ERRORS BY LABEL  {parts}")

    lines.append("DETAIL  (per expected/produced observation)")
    for d in data["details"]:
        tag = d["outcome"].upper()
        lines.append(f"  [{tag}] {d['pmid']} {d['sentence_id']}")
        if d.get("expected"):
            lines.append(f"      expected: {d['expected']}")
        if d.get("produced"):
            lines.append(f"      produced: {d['produced']}")
        if d.get("differing_fields"):
            lines.append(f"      wrong: {', '.join(d['differing_fields'])}")

    return "\n".join(lines) + "\n"
