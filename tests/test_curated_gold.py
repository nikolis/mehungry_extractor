"""Curated synthetic gold gate — the reviewer examples motivating the Fix-4 coverage work.

These score the real model-path pipeline against ``tests/gold/curated_examples.json`` via the
store-free synthetic evaluator (:mod:`mehungry_extractor.knowledge.goldsynth`). They require the
scispaCy model (the parse binder needs it) and are skipped otherwise. Assertions are **one-sided** —
improvements are welcome, regressions fail — mirroring the store-backed baseline guard in
``test_goldeval.py``. The numbers below are the snapshot taken after Fix 1 (guarded fallback) and
before Phase 4; each Phase-4 change tightens them.
"""

import pytest

from mehungry_extractor.knowledge import parse as _parse
from mehungry_extractor.knowledge.goldsynth import evaluate

pytestmark = pytest.mark.skipif(
    not _parse.available(), reason="scispaCy parser model not installed"
)


def test_curated_gold_baseline_no_regression():
    result = evaluate(use_model=True)
    d = result.to_dict()
    c = d["counts"]
    # Snapshot 2026-09-30 after Phases 4a–4c + 4f (4a: widened predicate discovery + control/antecedent
    # subject resolution + restrictive subject resolver + non-entity-subject fallback guard; 4b:
    # measure-noun object unwrapping; 4c: "protective … against" cue; 4f: gerund clausal subject):
    # correct 9 / field_error 0 / missed 4 / spurious 0; achievable_correct 5 of 8, precision 1.0.
    # The 4 misses are recall ceilings: an out-of-vocab subject (#1), an unmapped verb "regulate"
    # (kefir→gut microbiota), and #6 whose grammatical subject is "the metabolites", not "fiber".
    # One-sided: never fewer correct, never more spurious.
    assert c["correct"] >= 9, d["details"]
    assert c["spurious"] <= 0, d["details"]
    assert c["achievable_correct"] >= 5, d["details"]
    assert d["precision_strict"] >= 0.99, d["details"]
