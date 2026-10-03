"""Sentence segmentation — the scispaCy-parser model path and the pysbd rule floor.

The floor (``use_model=False``) is exercised unconditionally; the model path is covered when the
scispaCy model is installed. Both must obey the offset contract and keep biomedical period-usage
(abbreviations, decimals, units, p-values) from shattering a sentence into fragments, since the
dependency parser runs per sentence and a false split silently drops relations.
"""

import pytest

from mehungry_extractor.knowledge import segment as _seg
from mehungry_extractor.knowledge.segment import SEGMENTER_MODEL, SEGMENTER_RULE, segment

_TEXT = "Fiber was associated with remission. It did not cause harm. Effects varied."

# Biomedical period-usage that a plain punctuation splitter shatters into fragments.
_WHOLE_SENTENCES = [
    "Vitamin C (approx. 2.5 mg.) was given daily.",
    "Fish intake reduced risk (p = 0.03); however, the effect vanished after adj. for BMI.",
]

_both = pytest.mark.parametrize(
    "use_model",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                not _seg.model_available(), reason="scispaCy model not installed"
            ),
        ),
    ],
)


@_both
def test_segment_offsets_reconstruct(use_model):
    for start, end, text in segment(_TEXT, use_model=use_model):
        assert _TEXT[start:end] == text


@_both
def test_segment_basic_three_sentences(use_model):
    assert len(segment(_TEXT, use_model=use_model)) == 3


@_both
@pytest.mark.parametrize("text", _WHOLE_SENTENCES)
def test_segment_does_not_split_on_abbreviations_or_decimals(use_model, text):
    sents = segment(text, use_model=use_model)
    assert len(sents) == 1, sents
    assert sents[0][2] == text.strip()


def test_segment_empty():
    assert segment("   ") == []
    assert segment("   ", use_model=False) == []


def test_floor_is_pysbd_without_model():
    # use_model=False never touches the model, so the chosen segmenter is always the rule floor.
    assert _seg.active_segmenter(use_model=False) == SEGMENTER_RULE


@pytest.mark.skipif(not _seg.model_available(), reason="scispaCy model not installed")
def test_model_path_reports_model_segmenter():
    assert _seg.active_segmenter(use_model=True) == SEGMENTER_MODEL
