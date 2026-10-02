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
    # Snapshot 2026-10-01 after Phases 4a–4c + 4f, Phase 13 (hierarchical relations) and Phase 15
    # (descriptive-object binding). #c2 contributes the reviewer-confirmed relation
    # `dysbiosis -characterized_by-> "alterations in the composition and function of the gut
    # microbiota"`, nested beneath `inflammation -causes-> dysbiosis`: the past-participle rule binds
    # the governing *object* (dysbiosis), so the controller fallback no longer fabricates the
    # `inflammation` subject, and the descriptive object is the WHOLE phrase, not the deep entity.
    # That phrase is a free-text descriptor (out of vocabulary), so the relation is `correct` (it
    # aligns and all labels agree) but no longer `achievable` recall — hence correct stays 10 while
    # achievable_correct is 5 of 8. The remaining misses are recall ceilings: an out-of-vocab subject
    # (#1), an unmapped verb "regulate" (kefir→gut microbiota), and #6 whose grammatical subject is
    # "the metabolites", not "fiber". One-sided: never fewer correct, never more spurious.
    assert c["correct"] >= 10, d["details"]
    assert c["spurious"] <= 0, d["details"]
    assert c["achievable_correct"] >= 5, d["details"]
    assert d["precision_strict"] >= 0.99, d["details"]
