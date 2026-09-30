"""Gold-standard evaluation — precision/recall/F1 against hand-labeled observations.

This is the labeled counterpart to the label-free self-loop precision probe (:mod:`.metrics`).
The self-loop metric measures a precision *floor* with no annotations and is structurally blind to
**recall** — it can only see relations that are wrong-by-construction (subject concept == object
concept). This module scores the pipeline's produced observations against a small, checked-in,
hand-labeled gold set (``tests/gold/observations.json``), so it measures what self-loops cannot:

* **recall** — observations a perfect extractor should emit but the pipeline misses;
* **true precision** — spurious observations the pipeline emits that no annotator wrote down;
* a **failure-mode histogram** — a root-cause tally that names which pipeline layer to fix.

Everything here is a **pure, deterministic fold** over plain dicts (produced observations) and the
gold records — no model, no network, no store access. The query layer
(:func:`mehungry_extractor.knowledge.query.gold_eval_report`) assembles the produced side from the
store and calls in here; the audit layer (:func:`...audit.render_gold_eval`) renders the result.
This mirrors the ``metrics -> query -> audit`` shape of the self-loop probe.

Scoring model
-------------
Produced observations are scoped to the labeled sentences only (a sentence we have not annotated is
neither credited nor penalized). Within each labeled sentence, produced and expected observations
are aligned on **endpoint identity** — concept id when both sides normalized, else casefolded
surface-text containment (gold spans are richer than concept names, e.g. ``Lachnospiraceae
absence``). Each aligned pair is then graded on ``predicate`` / ``polarity`` / ``certainty``:

* **correct** — endpoints align and all three labels agree (a true positive).
* **field_error** — endpoints align but at least one label differs (right entities + association,
  wrong label). Partial credit in the *lenient* metrics; not a true positive in the *strict* ones.
* **missed** — an expected observation with no aligned produced observation (a false negative).
* **spurious** — a produced observation with no aligned expected observation (a false positive).

Recall is reported twice: **end_to_end** (all gold observations) and **achievable** (only gold whose
*both* endpoints are in-vocabulary). The gap between them is the recall ceiling imposed purely by
vocabulary coverage, held apart from relation/clause logic. Precision is vocabulary-independent and
reported once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .enums import PREDICATES, Certainty, Polarity

# Labels a gold ``failure_mode`` hint may carry. Not enforced as an enum (the histogram accepts any
# label so it stays honest as the pipeline evolves), but unknown values are flagged on load.
KNOWN_FAILURE_MODES = frozenset(
    {
        "argument_mispairing",
        "hedging_missed",
        "compound_subject",
        "coordination_missing",
        "object_granularity",
        "vocab_gap",
        "wrong_predicate",
        "wrong_polarity",
        "wrong_certainty",
    }
)

_GRADED_FIELDS = ("predicate", "polarity", "certainty")
_WS_RE = re.compile(r"\s+")


def default_gold_path() -> Path:
    """The checked-in seed gold set at ``<repo>/tests/gold/observations.json``.

    Resolved relative to this file so it works in an editable dev checkout (the gold set is a test
    asset, not shipped package data). Callers may pass their own path to score a different set.
    """
    return Path(__file__).resolve().parents[2] / "tests" / "gold" / "observations.json"


def _norm(text: Optional[str]) -> str:
    return _WS_RE.sub(" ", text or "").strip().casefold()


def load_gold(path: "str | Path | None" = None) -> dict:
    """Load + validate the gold set. Raises ``ValueError`` on a malformed record.

    Validation is strict on the controlled fields (predicate/polarity/certainty must be in the
    shared enums) so a typo in a hand-authored label fails loudly rather than silently scoring as a
    mismatch. Unknown ``failure_mode`` hints are permitted but collected for the caller to notice.
    """
    p = Path(path) if path is not None else default_gold_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    sentences = data.get("sentences", [])
    valid_pred = set(PREDICATES)
    valid_pol = {e.value for e in Polarity}
    valid_cert = {e.value for e in Certainty}
    for s in sentences:
        if not s.get("sentence_id") and not s.get("sentence_quote"):
            raise ValueError(f"gold record for {s.get('pmid')} needs sentence_id or sentence_quote")
        for exp in s.get("expected", []):
            if exp.get("unscorable"):
                continue
            if exp["predicate"] not in valid_pred:
                raise ValueError(f"unknown predicate {exp['predicate']!r} in {s.get('sentence_id')}")
            if exp["polarity"] not in valid_pol:
                raise ValueError(f"unknown polarity {exp['polarity']!r} in {s.get('sentence_id')}")
            if exp["certainty"] not in valid_cert:
                raise ValueError(f"unknown certainty {exp['certainty']!r} in {s.get('sentence_id')}")
    return data


def _endpoint_match(expected_endpoint: dict, prod_concept: Optional[str], prod_text: str) -> bool:
    """Do a gold endpoint and a produced endpoint refer to the same thing?

    Concept id when both sides carry one (the strongest signal); otherwise casefolded surface
    containment either way, since a gold span is often richer than the produced surface
    (``dysbiosis of the gut microbiome`` vs ``dysbiosis``) and vice versa.
    """
    exp_concept = expected_endpoint.get("concept_id")
    if exp_concept and prod_concept:
        return exp_concept == prod_concept
    a, b = _norm(expected_endpoint.get("text")), _norm(prod_text)
    if not a or not b:
        return False
    return a in b or b in a


def _obs_match(expected: dict, produced: dict) -> bool:
    return _endpoint_match(
        expected["subject"], produced.get("subject_concept_id"), produced.get("subject_text", "")
    ) and _endpoint_match(
        expected["object"], produced.get("object_concept_id"), produced.get("object_text", "")
    )


def _endpoints_in_vocab(expected: dict) -> bool:
    return bool(expected["subject"].get("in_vocab")) and bool(expected["object"].get("in_vocab"))


@dataclass
class GoldEvalResult:
    """Scorecard for one run of the gold evaluation. All counts are exact.

    ``correct``/``field_error``/``missed``/``spurious`` are the four scoring buckets (see module
    docstring). ``achievable_*`` recompute the recall buckets over in-vocabulary gold only.
    ``failure_mode`` tallies root causes across every non-correct outcome; ``field_error_by_field``
    breaks aligned-but-wrong pairs down by which label differed. ``details`` is an ordered,
    human-readable trace for the audit renderer.
    """

    n_sentences: int
    correct: int = 0
    field_error: int = 0
    missed: int = 0
    spurious: int = 0
    achievable_correct: int = 0
    achievable_field_error: int = 0
    achievable_missed: int = 0
    field_error_by_field: dict[str, int] = field(default_factory=dict)
    failure_mode: dict[str, int] = field(default_factory=dict)
    details: list[dict] = field(default_factory=list)

    # --- totals ---
    @property
    def n_expected(self) -> int:
        return self.correct + self.field_error + self.missed

    @property
    def n_produced(self) -> int:
        return self.correct + self.field_error + self.spurious

    @property
    def n_achievable_expected(self) -> int:
        return self.achievable_correct + self.achievable_field_error + self.achievable_missed

    # --- precision (vocabulary-independent, reported once) ---
    @property
    def precision_strict(self) -> Optional[float]:
        return self.correct / self.n_produced if self.n_produced else None

    @property
    def precision_lenient(self) -> Optional[float]:
        return (self.correct + self.field_error) / self.n_produced if self.n_produced else None

    # --- recall (end-to-end: all gold) ---
    @property
    def recall_strict(self) -> Optional[float]:
        return self.correct / self.n_expected if self.n_expected else None

    @property
    def recall_lenient(self) -> Optional[float]:
        return (self.correct + self.field_error) / self.n_expected if self.n_expected else None

    # --- recall (achievable: in-vocabulary gold only) ---
    @property
    def recall_achievable_strict(self) -> Optional[float]:
        n = self.n_achievable_expected
        return self.achievable_correct / n if n else None

    @property
    def recall_achievable_lenient(self) -> Optional[float]:
        n = self.n_achievable_expected
        return (self.achievable_correct + self.achievable_field_error) / n if n else None

    @staticmethod
    def _f1(p: Optional[float], r: Optional[float]) -> Optional[float]:
        if not p or not r:
            return None
        return 2 * p * r / (p + r)

    @property
    def f1_strict(self) -> Optional[float]:
        return self._f1(self.precision_strict, self.recall_strict)

    @property
    def f1_achievable(self) -> Optional[float]:
        return self._f1(self.precision_strict, self.recall_achievable_strict)

    def to_dict(self) -> dict:
        """JSON-friendly view, matching the query layer's dict convention."""
        return {
            "n_sentences": self.n_sentences,
            "counts": {
                "correct": self.correct,
                "field_error": self.field_error,
                "missed": self.missed,
                "spurious": self.spurious,
                "expected": self.n_expected,
                "produced": self.n_produced,
                "achievable_expected": self.n_achievable_expected,
                "achievable_correct": self.achievable_correct,
                "achievable_field_error": self.achievable_field_error,
                "achievable_missed": self.achievable_missed,
            },
            "precision_strict": self.precision_strict,
            "precision_lenient": self.precision_lenient,
            "recall_strict": self.recall_strict,
            "recall_lenient": self.recall_lenient,
            "recall_achievable_strict": self.recall_achievable_strict,
            "recall_achievable_lenient": self.recall_achievable_lenient,
            "f1_strict": self.f1_strict,
            "f1_achievable": self.f1_achievable,
            "field_error_by_field": dict(sorted(self.field_error_by_field.items())),
            "failure_mode": dict(sorted(self.failure_mode.items())),
            "details": self.details,
        }


def _bump(table: dict[str, int], key: str) -> None:
    table[key] = table.get(key, 0) + 1


def score(
    gold_sentences: Iterable[dict],
    produced_by_sentence: dict[str, list[dict]],
) -> GoldEvalResult:
    """Fold gold sentences + produced observations into a :class:`GoldEvalResult`. Pure.

    ``produced_by_sentence`` maps ``sentence_id -> list of produced-observation dicts`` (the shape
    :func:`...query.list_observations_for_document` returns, regrouped by sentence). Only sentences
    present in the gold set are scored; produced observations in any other sentence are ignored.
    Alignment is greedy and order-stable (expected in file order, produced consumed first-match), so
    the result is deterministic.
    """
    gold_sentences = list(gold_sentences)
    result = GoldEvalResult(n_sentences=len(gold_sentences))

    for s in gold_sentences:
        sentence_id = s.get("sentence_id")
        produced = list(produced_by_sentence.get(sentence_id, []))
        consumed: set[int] = set()
        expected = [e for e in s.get("expected", []) if not e.get("unscorable")]

        for exp in expected:
            in_vocab = _endpoints_in_vocab(exp)
            match_idx = next(
                (i for i, p in enumerate(produced) if i not in consumed and _obs_match(exp, p)),
                None,
            )
            if match_idx is None:
                # false negative
                result.missed += 1
                if in_vocab:
                    result.achievable_missed += 1
                _bump(result.failure_mode, exp.get("failure_mode") or "missing")
                result.details.append(_detail(s, exp, "missed", None, in_vocab))
                continue

            prod = produced[match_idx]
            consumed.add(match_idx)
            diffs = [f for f in _GRADED_FIELDS if _field_value(exp, f) != prod.get(f)]
            if not diffs:
                result.correct += 1
                if in_vocab:
                    result.achievable_correct += 1
                result.details.append(_detail(s, exp, "correct", prod, in_vocab))
            else:
                result.field_error += 1
                if in_vocab:
                    result.achievable_field_error += 1
                for f in diffs:
                    _bump(result.field_error_by_field, f)
                # Prefer the authored root cause; else name the offending field(s).
                label = exp.get("failure_mode") or "wrong_" + "_".join(diffs)
                _bump(result.failure_mode, label)
                result.details.append(_detail(s, exp, "field_error", prod, in_vocab, diffs))

        for i, prod in enumerate(produced):
            if i not in consumed:
                result.spurious += 1
                _bump(result.failure_mode, "spurious")
                result.details.append(_detail(s, None, "spurious", prod, False))

    return result


def _field_value(expected: dict, f: str) -> str:
    return expected[f]


def _endpoint_str(endpoint: dict) -> str:
    cid = endpoint.get("concept_id")
    return f"{endpoint.get('text')!r} ({cid})" if cid else f"{endpoint.get('text')!r} (unmatched)"


def _detail(
    sentence: dict,
    expected: Optional[dict],
    outcome: str,
    produced: Optional[dict],
    in_vocab: bool,
    diffs: Optional[list[str]] = None,
) -> dict:
    d: dict = {
        "pmid": sentence.get("pmid"),
        "sentence_id": sentence.get("sentence_id"),
        "outcome": outcome,
        "in_vocab": in_vocab,
    }
    if expected is not None:
        d["expected"] = (
            f"{_endpoint_str(expected['subject'])} -{expected['predicate']}-> "
            f"{_endpoint_str(expected['object'])} "
            f"[{expected['polarity']}/{expected['certainty']}]"
        )
        d["failure_mode"] = expected.get("failure_mode")
        if expected.get("note"):
            d["note"] = expected["note"]
    if produced is not None:
        d["produced"] = (
            f"{produced.get('subject_text')!r} ({produced.get('subject_concept_id')}) "
            f"-{produced.get('predicate')}-> "
            f"{produced.get('object_text')!r} ({produced.get('object_concept_id')}) "
            f"[{produced.get('polarity')}/{produced.get('certainty')}]"
        )
    if diffs:
        d["differing_fields"] = diffs
    return d
