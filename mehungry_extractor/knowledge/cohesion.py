"""Deterministic topic-cohesion / outlier detection over concept sets.

The batch analysis API (:mod:`.api`) receives a small set of PMIDs that are *usually* about one
topic but may contain off-topic outliers. This module decides, **deterministically and without
any embeddings or inference** (the engine forbids them), which papers cohere with the batch's
shared topic and which do not.

A paper's "topic" is the set of normalized concept ids its entity mentions resolved to
(:mod:`.entities` / :mod:`.normalize`). The batch's shared topic — the **topic core** — is the
set of concepts that recur across enough papers. A paper that covers little of that shared
vocabulary is flagged as an outlier. Everything here is set arithmetic over sorted collections,
so the same batch always produces byte-identical output.

Nothing is ever silently dropped: an outlier is fully reported (its score and exactly which
core concepts it lacks); the API excludes it only from the synthesized conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Optional

# A concept must appear in at least this fraction of the batch's papers to join the topic core.
DEFAULT_CORE_FRACTION = 0.5
# A paper covering less than this fraction of the topic core is treated as off-topic.
DEFAULT_OUTLIER_THRESHOLD = 0.2

# Reasons a paper is excluded from the conclusions (reported verbatim to the caller).
REASON_OFF_TOPIC = "off_topic"
REASON_NO_CONCEPTS = "no_extractable_concepts"


@dataclass(frozen=True)
class PaperCohesion:
    """One paper's standing relative to the batch's topic core."""

    document_id: str
    # Fraction of the topic core the paper covers (0..1), or ``None`` when there is no core to
    # score against (a single paper, or a batch with no shared concept).
    score: Optional[float]
    concept_count: int
    is_outlier: bool
    reason: Optional[str]
    missing_core_concepts: list[str]


@dataclass
class CohesionResult:
    """The batch-level cohesion analysis: the topic core, per-paper scores, and warnings."""

    core_concepts: list[str]  # sorted concept ids forming the topic core
    core_fraction: float
    outlier_threshold: float
    min_core_papers: int
    papers: list[PaperCohesion]  # ordered by document_id
    warnings: list[str] = field(default_factory=list)

    @property
    def included(self) -> list[str]:
        """Document ids that cohere with the topic (fed into synthesis), ordered by id."""
        return [p.document_id for p in self.papers if not p.is_outlier]

    @property
    def outliers(self) -> list[PaperCohesion]:
        return [p for p in self.papers if p.is_outlier]


def detect(
    concept_sets: dict[str, "set[str]"],
    *,
    core_fraction: float = DEFAULT_CORE_FRACTION,
    outlier_threshold: float = DEFAULT_OUTLIER_THRESHOLD,
) -> CohesionResult:
    """Score each paper's topic cohesion and flag outliers. Pure + deterministic.

    ``concept_sets`` maps ``document_id -> {concept_id, ...}`` (normalized concepts only). The
    topic core is the set of concepts present in at least ``max(2, ceil(core_fraction * N))``
    papers; a paper's score is the fraction of that core it covers, and it is an outlier when the
    score is below ``outlier_threshold``. A paper with no normalized concepts is excluded with a
    distinct reason (it cannot be topic-scored) rather than being confidently called off-topic.
    """
    doc_ids = sorted(concept_sets)
    n = len(doc_ids)
    warnings: list[str] = []

    # Document frequency of every concept across the batch.
    df: dict[str, int] = {}
    for doc_id in doc_ids:
        for concept_id in concept_sets[doc_id]:
            df[concept_id] = df.get(concept_id, 0) + 1

    min_core_papers = max(2, ceil(core_fraction * n)) if n >= 2 else 1
    core = sorted(c for c, count in df.items() if count >= min_core_papers)
    core_set = set(core)

    if n >= 2 and not core:
        warnings.append(
            "no shared topic core: no normalized concept is common to at least "
            f"{min_core_papers} of the {n} papers, so topic cohesion cannot be assessed and no "
            "paper was excluded as an outlier."
        )

    papers: list[PaperCohesion] = []
    for doc_id in doc_ids:
        concepts = concept_sets[doc_id]

        if not concepts:
            papers.append(
                PaperCohesion(
                    document_id=doc_id,
                    score=None,
                    concept_count=0,
                    is_outlier=True,
                    reason=REASON_NO_CONCEPTS,
                    missing_core_concepts=list(core),
                )
            )
            warnings.append(
                f"{doc_id}: no normalized concepts extracted (e.g. abstract-only with no "
                "vocabulary hits); excluded from the topic conclusions."
            )
            continue

        if not core_set:
            # No core to compare against (single paper, or heterogeneous batch). Keep the paper.
            papers.append(
                PaperCohesion(
                    document_id=doc_id,
                    score=None,
                    concept_count=len(concepts),
                    is_outlier=False,
                    reason=None,
                    missing_core_concepts=[],
                )
            )
            continue

        overlap = concepts & core_set
        score = round(len(overlap) / len(core_set), 4)
        is_outlier = score < outlier_threshold
        papers.append(
            PaperCohesion(
                document_id=doc_id,
                score=score,
                concept_count=len(concepts),
                is_outlier=is_outlier,
                reason=REASON_OFF_TOPIC if is_outlier else None,
                missing_core_concepts=sorted(core_set - concepts),
            )
        )

    return CohesionResult(
        core_concepts=core,
        core_fraction=core_fraction,
        outlier_threshold=outlier_threshold,
        min_core_papers=min_core_papers,
        papers=papers,
        warnings=warnings,
    )
