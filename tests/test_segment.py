"""Deterministic sentence segmentation (spec §17)."""

from mehungry_extractor.knowledge.segment import segment

_TEXT = "Fiber was associated with remission. It did not cause harm. Effects varied."


def test_segment_offsets_reconstruct():
    for start, end, text in segment(_TEXT):
        assert _TEXT[start:end] == text


def test_segment_is_deterministic():
    a = segment(_TEXT)
    b = segment(_TEXT)
    assert a == b
    assert len(a) == 3


def test_segment_empty():
    assert segment("   ") == []
