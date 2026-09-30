"""Curated-vocabulary integrity (A3 growth).

The dictionary is the recall backbone (spec §6): every added concept must keep the normalizer
deterministic. These checks guard the invariants the matcher/normalizer rely on — no duplicate
concept ids, no casefolded surface shared across two concepts (which would silently make both
``ambiguous``), well-formed records — plus a few anchor lookups so a bad edit is caught loudly.
"""

import re

from mehungry_extractor.knowledge.normalize import STATUS_NORMALIZED, normalize
from mehungry_extractor.knowledge.vocab import VOCAB_VERSION, load_concepts

_WS = re.compile(r"\s+")


def _key(s: str) -> str:
    return _WS.sub(" ", s).strip().casefold()


def test_records_are_well_formed():
    for c in load_concepts():
        assert c["concept_id"] and ":" in c["concept_id"]
        assert c["canonical_name"]
        assert c["entity_type"]
        assert c["surface_forms"] and all(s.strip() for s in c["surface_forms"])


def test_concept_ids_are_unique():
    ids = [c["concept_id"] for c in load_concepts()]
    assert len(ids) == len(set(ids))


def test_no_surface_is_shared_across_two_concepts():
    """A casefolded surface owned by two distinct concepts would make both resolve `ambiguous`."""
    owner: dict[str, str] = {}
    clashes = []
    for c in load_concepts():
        for s in c["surface_forms"]:
            k = _key(s)
            if k in owner and owner[k] != c["concept_id"]:
                clashes.append((s, owner[k], c["concept_id"]))
            owner[k] = c["concept_id"]
    assert not clashes, clashes


def test_vocabulary_grew_and_covers_the_domain():
    concepts = load_concepts()
    assert VOCAB_VERSION >= "0.2.0"
    assert len(concepts) >= 200  # A3: order-of-magnitude growth over the original 25
    types = {c["entity_type"] for c in concepts}
    assert {"nutrient", "food", "chemical", "disease", "symptom", "biomarker", "outcome", "intervention"} <= types


def test_anchor_surfaces_resolve_uniquely():
    # A spread across old and new concepts — each must normalize cleanly to its id.
    anchors = {
        "dietary fiber": "NUTR:dietary_fiber",       # original
        "ulcerative colitis": "DIS:ulcerative_colitis",  # original
        "curcumin": "CHEM:curcumin",                 # new compound
        "Mediterranean diet": "INT:mediterranean_diet",  # new intervention
        "fecal calprotectin": "BIOM:fecal_calprotectin",  # new biomarker
        "irritable bowel syndrome": "DIS:ibs",       # new disease
        "mucosal healing": "OUT:mucosal_healing",    # new outcome
        "probiotics": "NUTR:probiotics",             # new nutrient
    }
    for surface, cid in anchors.items():
        n = normalize(surface)
        assert n.status == STATUS_NORMALIZED and n.concept.concept_id == cid, (surface, n.status)
