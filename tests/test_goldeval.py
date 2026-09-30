"""Gold-standard evaluation — precision/recall/F1 against hand-labeled observations.

The scorer is a pure fold over dicts, so most tests drive it directly with lightweight stubs. One
end-to-end test runs the real pipeline over the two labeled corpus papers and guards the measured
baseline against regression; it skips when the machine-local corpus is absent (``data/corpus`` is
git-ignored).
"""

import pytest

from mehungry_extractor.knowledge.goldeval import GoldEvalResult, load_gold, score


def _prod(subj_c, obj_c, *, predicate="associated_with", polarity="positive", certainty="asserted",
          subj_t="s", obj_t="o"):
    return {
        "subject_concept_id": subj_c, "object_concept_id": obj_c,
        "subject_text": subj_t, "object_text": obj_t,
        "predicate": predicate, "polarity": polarity, "certainty": certainty,
    }


def _exp(subj_c, obj_c, *, predicate="associated_with", polarity="positive", certainty="asserted",
         subj_t="s", obj_t="o", subj_vocab=True, obj_vocab=True, failure_mode=None):
    return {
        "subject": {"text": subj_t, "concept_id": subj_c, "in_vocab": subj_vocab},
        "object": {"text": obj_t, "concept_id": obj_c, "in_vocab": obj_vocab},
        "predicate": predicate, "polarity": polarity, "certainty": certainty,
        "failure_mode": failure_mode,
    }


def _sentence(expected, sentence_id="s1", pmid="1"):
    return {"pmid": pmid, "sentence_id": sentence_id, "expected": expected}


# --- core buckets ------------------------------------------------------------------


def test_exact_match_is_correct():
    gold = [_sentence([_exp("A", "B")])]
    r = score(gold, {"s1": [_prod("A", "B")]})
    assert (r.correct, r.field_error, r.missed, r.spurious) == (1, 0, 0, 0)
    assert r.precision_strict == 1.0 and r.recall_strict == 1.0 and r.f1_strict == 1.0


def test_label_mismatch_is_field_error_not_correct():
    gold = [_sentence([_exp("A", "B", certainty="asserted")])]
    r = score(gold, {"s1": [_prod("A", "B", certainty="possible")]})
    assert (r.correct, r.field_error, r.missed, r.spurious) == (0, 1, 0, 0)
    assert r.field_error_by_field == {"certainty": 1}
    # Strict gives no credit; lenient does (right entities + association).
    assert r.precision_strict == 0.0
    assert r.precision_lenient == 1.0


def test_missing_expected_is_false_negative():
    gold = [_sentence([_exp("A", "B")])]
    r = score(gold, {"s1": []})
    assert (r.correct, r.missed) == (0, 1)
    assert r.recall_strict == 0.0
    assert r.precision_strict is None  # nothing produced


def test_extra_produced_is_spurious_false_positive():
    gold = [_sentence([_exp("A", "B")])]
    r = score(gold, {"s1": [_prod("A", "B"), _prod("X", "Y")]})
    assert (r.correct, r.spurious) == (1, 1)
    assert r.precision_strict == 0.5
    assert r.failure_mode.get("spurious") == 1


def test_produced_in_unlabeled_sentence_is_ignored():
    gold = [_sentence([_exp("A", "B")], sentence_id="s1")]
    r = score(gold, {"s1": [_prod("A", "B")], "s2": [_prod("Q", "Z")]})
    assert (r.correct, r.spurious) == (1, 0)


# --- endpoint alignment ------------------------------------------------------------


def test_concept_id_alignment_ignores_surface_granularity():
    # Gold object surface richer than produced; same concept id → still aligns.
    gold = [_sentence([_exp("DIS:ibd", "DIS:dysbiosis", obj_t="dysbiosis of the gut microbiome")])]
    r = score(gold, {"s1": [_prod("DIS:ibd", "DIS:dysbiosis", obj_t="dysbiosis")]})
    assert r.correct == 1


def test_out_of_vocab_endpoint_aligns_by_surface_containment():
    exp = _exp(None, "DIS:ibd", subj_t="Lachnospiraceae absence", subj_vocab=False)
    r = score([_sentence([exp])], {"s1": [_prod(None, "DIS:ibd", subj_t="Lachnospiraceae")]})
    assert r.correct == 1


def test_wrong_subject_does_not_align():
    # Produced subject (butyrate) is not the gold subject (Lachnospiraceae) → miss + spurious.
    exp = _exp(None, "DIS:ibd", subj_t="Lachnospiraceae absence", subj_vocab=False)
    r = score([_sentence([exp])], {"s1": [_prod("CHEM:butyrate", "DIS:ibd", subj_t="butyrate")]})
    assert (r.correct, r.missed, r.spurious) == (0, 1, 1)


# --- achievable vs end-to-end recall -----------------------------------------------


def test_achievable_recall_excludes_out_of_vocab_gold():
    gold = [_sentence([
        _exp("A", "B"),                                   # in-vocab, matched
        _exp(None, None, subj_t="q", obj_t="z",
             subj_vocab=False, obj_vocab=False, failure_mode="vocab_gap"),  # oov, missed
    ])]
    r = score(gold, {"s1": [_prod("A", "B")]})
    # end-to-end recall counts the oov miss; achievable does not.
    assert r.recall_strict == 0.5
    assert r.recall_achievable_strict == 1.0
    assert r.failure_mode.get("vocab_gap") == 1


def test_unscorable_expected_is_skipped():
    exp = _exp("A", "B")
    unscorable = {**_exp("A", "C"), "unscorable": True}
    r = score([_sentence([exp, unscorable])], {"s1": [_prod("A", "B")]})
    assert (r.correct, r.missed) == (1, 0)  # unscorable not counted as a miss


def test_derived_failure_mode_when_no_hint():
    # No failure_mode hint on a field error → derived from the offending field.
    gold = [_sentence([_exp("A", "B", polarity="positive", failure_mode=None)])]
    r = score(gold, {"s1": [_prod("A", "B", polarity="negative")]})
    assert r.failure_mode.get("wrong_polarity") == 1


def test_to_dict_shape():
    r = score([_sentence([_exp("A", "B")])], {"s1": [_prod("A", "B")]})
    d = r.to_dict()
    assert set(d) >= {"counts", "precision_strict", "recall_strict", "recall_achievable_strict",
                      "f1_strict", "failure_mode", "details"}
    assert d["counts"]["correct"] == 1


# --- gold file loading + validation ------------------------------------------------


def test_seed_gold_file_loads_and_validates():
    gold = load_gold()  # the checked-in seed set
    assert gold["sentences"], "seed gold set should be non-empty"
    pmids = {s["pmid"] for s in gold["sentences"]}
    assert {"42676636", "42626304"} <= pmids


def test_load_gold_rejects_bad_predicate(tmp_path):
    import json
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"sentences": [_sentence([_exp("A", "B", predicate="frobnicates")])]}))
    with pytest.raises(ValueError):
        load_gold(bad)


# --- end-to-end baseline guard -----------------------------------------------------


def test_gold_eval_baseline_no_regression(tmp_path):
    """Run the real pipeline over the labeled papers; guard the 2026-09-28 baseline.

    Skips when the machine-local corpus is absent. Assertions are one-sided (improvements welcome,
    regressions fail) — mirroring the self-loop probe's ``self_loop_rate <= 0.32`` guard.
    """
    from mehungry_extractor.knowledge.corpus import CorpusStore
    from mehungry_extractor.knowledge.db import get_engine, init_db
    from mehungry_extractor.knowledge.pipeline import extract_document
    from mehungry_extractor.knowledge.query import gold_eval_report

    store = CorpusStore()
    gold = load_gold()
    pmids = sorted({s["pmid"] for s in gold["sentences"]})
    if not all(store.has_canonical(p) for p in pmids):
        pytest.skip("gold corpus papers not present (data/corpus is machine-local)")

    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    for p in pmids:
        extract_document(p, corpus=store, engine=engine, use_model=False)

    data = gold_eval_report(engine)
    c = data["counts"]
    # Baseline snapshot 2026-09-28: correct 2 / field_error 2 / missed 6 / spurious 2.
    assert c["correct"] >= 2, data["details"]
    assert c["spurious"] <= 2, data["details"]
    assert c["missed"] <= 6, data["details"]
    assert data["precision_strict"] >= 0.33 - 1e-9
    assert data["recall_achievable_strict"] >= 0.40 - 1e-9
