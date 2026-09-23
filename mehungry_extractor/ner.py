"""Optional biomedical NER grounding (scispaCy).

If the ``[ner]`` extra + a scispaCy model are installed, we extract Chemical/Disease
spans up front and hand them to the LLM as hints — this focuses extraction on real
entities and improves the raw-term → compound/nutrient resolution the server does.
When scispaCy isn't available the module degrades to a no-op and the LLM extracts
unaided; nothing breaks.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def _nlp():
    try:
        import spacy  # type: ignore

        # BC5CDR model tags Chemicals and Diseases — the entities we care about.
        return spacy.load("en_ner_bc5cdr_md")
    except Exception:
        return None


def available() -> bool:
    return _nlp() is not None


def chemical_terms(text: str, limit: int = 40) -> list[str]:
    """Distinct Chemical spans found in the text (empty if scispaCy is unavailable)."""
    nlp = _nlp()
    if nlp is None or not text:
        return []

    # Cap input so a huge full text doesn't blow up the pipeline.
    doc = nlp(text[:100_000])
    seen: dict[str, None] = {}
    for ent in doc.ents:
        if ent.label_ == "CHEMICAL":
            key = ent.text.strip().lower()
            if key and key not in seen:
                seen[key] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())
