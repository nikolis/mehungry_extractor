"""Open relation *discovery* — flag the sentences/clauses that assert a relationship (Phase 11).

This is a **discovery-first** stage that imposes *no* structure on relationships at all — not a
predicate, not even a subject/object split. A local, purpose-built model reads a paper's canonical
:class:`~.canonical.Document` and identifies the **spans that assert some relationship**; each such
span becomes an :class:`~.openobs.OpenObservation`, recorded verbatim with an ``EXACT_SPAN`` ref.
It is the deliberate opposite of a triple extractor (see :mod:`.openobs` for the "why"), and it is
kept entirely out of the trusted claims/synthesis pipeline.

Structured like :mod:`.parse`/:mod:`.entities`:

* :func:`available` + a lazily-loaded, cached default detector, so importing this module without the
  optional ``[openrel]`` extra never fails — detection is a *model* feature by definition, and it
  feeds nothing downstream that would need a deterministic fallback, so there is no model-free floor.
* A :class:`RelationBearingDetector` **protocol** (``name``/``version`` + ``detect``). The concrete
  model is **pluggable**: comparing which sentences different models flag is itself part of the
  review.
* :func:`extract_open_observations` runs a detector and turns each flagged span into an
  ``OpenObservation`` with a single ``EXACT_SPAN`` :class:`~.provenance.EvidenceRef`.

**Default granularity is the sentence** — always known, always exact. A detector *may* localize to a
clause (Concept 15) and set :attr:`DetectedSpan.clause_index`; the recorded span is then the clause,
otherwise the whole sentence.

**Default detector strategy — reuse, don't retrain.** Any off-the-shelf open-RE / OpenIE model can
serve *purely as a detector*: run it, and for every sentence on which it fires (emits ≥1 relation),
record that **sentence verbatim** + the model's score, and **discard its triple/decomposition
entirely**. That leverages a model "designed for the job" while committing to none of its imposed
structure. The bundled default (:class:`RebelRelationDetector`) does exactly this with a REBEL-style
seq2seq model behind the optional ``[openrel]`` extra; a dedicated relation-sentence classifier is an
alternative adapter behind the same protocol.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from .openobs import OpenObservation, open_observation_id
from .provenance import EvidenceRef

if TYPE_CHECKING:
    from .canonical import Document


@dataclass(frozen=True)
class DetectedSpan:
    """One span a detector flags as relation-bearing, over the canonical text.

    ``start_char``/``end_char`` are absolute offsets into ``document.text`` (obeying the offset
    contract); ``sentence_id`` is the owning sentence. ``clause_index`` is set only when the detector
    localized to a clause of that sentence — otherwise the span is the whole sentence and it is
    ``None``. ``score`` is the detector's confidence (higher = more confident), used to order the
    reviewable output most-confident first.
    """

    sentence_id: str
    start_char: int
    end_char: int
    score: float
    clause_index: Optional[int] = None


@runtime_checkable
class RelationBearingDetector(Protocol):
    """A pluggable model that reads a document and flags its relation-bearing spans.

    Implementations carry a ``name`` + ``version`` (recorded on every record and folded into the
    :class:`~.run.ExtractionRun` fingerprint, so switching detectors forks the run) and a single
    :meth:`detect` method. A detector is the *only* way an open observation is produced — there is no
    deterministic floor.
    """

    name: str
    version: str

    def detect(self, document: "Document") -> list[DetectedSpan]:
        ...


class DetectorUnavailableError(RuntimeError):
    """Raised when the optional default detector (the ``[openrel]`` extra) cannot be loaded.

    Discovery is a model feature with no model-free floor, so a missing detector is a loud error —
    callers either install the extra or pass their own :class:`RelationBearingDetector`.
    """


# --- the default detector: a REBEL-style seq2seq model used purely as a detector ---------
#
# We run the model per sentence and keep only *whether it fired* (emitted ≥1 relation) plus its
# confidence — the emitted triples are discarded entirely. The model + its heavy deps
# (transformers/torch) live behind the optional ``[openrel]`` extra and are imported lazily, so the
# package imports fine without them.

_REBEL_MODEL = "Babelscape/rebel-large"


@functools.lru_cache(maxsize=1)
def _rebel():
    """Load and cache the REBEL tokenizer+model, raising :class:`DetectorUnavailableError` if the
    optional deps/model can't be loaded. (``lru_cache`` does not memoize exceptions, so a transient
    failure is retried next call while a successful load stays cached.)"""
    try:
        from transformers import (  # noqa: PLC0415 (heavy import kept lazy)
            AutoModelForSeq2SeqLM,
            AutoTokenizer,
        )

        tokenizer = AutoTokenizer.from_pretrained(_REBEL_MODEL)
        model = AutoModelForSeq2SeqLM.from_pretrained(_REBEL_MODEL)
        model.eval()
        return tokenizer, model
    except Exception as exc:  # ImportError, OSError (weights absent), config errors, ...
        raise DetectorUnavailableError(
            f"the default open-relation detector ({_REBEL_MODEL!r}) could not be loaded: {exc}. "
            f"Install the optional [openrel] extra, or pass your own RelationBearingDetector to "
            f"extract_open_observations(..., detector=...)."
        ) from exc


def _rebel_version() -> str:
    from importlib import metadata as _im  # noqa: PLC0415

    try:
        tv = _im.version("transformers")
    except _im.PackageNotFoundError:
        tv = "unknown"
    return f"{_REBEL_MODEL}@transformers-{tv}"


class RebelRelationDetector:
    """Default detector: a REBEL seq2seq model run per sentence, used *only* to flag relation-bearing
    sentences. It emits one :class:`DetectedSpan` (the whole sentence) for every sentence on which
    the model produces at least one relation; the model's normalized generation probability is the
    span's ``score``. The generated triples are deliberately thrown away — this stage commits to no
    structure."""

    name = "rebel"

    def __init__(self) -> None:
        self.version = _rebel_version()

    def detect(self, document: "Document") -> list[DetectedSpan]:
        import torch  # noqa: PLC0415 (heavy import kept lazy, only on the model path)

        tokenizer, model = _rebel()
        spans: list[DetectedSpan] = []
        for sent in document.iter_sentences():
            if not sent.text.strip():
                continue
            inputs = tokenizer(sent.text, return_tensors="pt", truncation=True, max_length=512)
            with torch.no_grad():
                out = model.generate(
                    **inputs,
                    max_length=256,
                    num_beams=3,
                    output_scores=True,
                    return_dict_in_generate=True,
                )
            decoded = tokenizer.decode(out.sequences[0], skip_special_tokens=False)
            if not _rebel_fires(decoded):
                continue  # the model produced no relation for this sentence — don't flag it
            score = _sequence_confidence(model, out)
            spans.append(
                DetectedSpan(
                    sentence_id=sent.sentence_id,
                    start_char=sent.start_char,
                    end_char=sent.end_char,
                    score=score,
                )
            )
        return spans


def _rebel_fires(decoded: str) -> bool:
    """True iff REBEL's linearized output contains at least one triple (a ``<triplet>`` marker)."""
    return "<triplet>" in decoded


def _sequence_confidence(model, generate_output) -> float:
    """A (0, 1] confidence from the beam's normalized sequence log-probability.

    Uses the length-normalized sequence score the generator already computed when available
    (``sequences_scores``), falling back to a neutral 1.0. Exponentiated back into a probability so
    the review can sort most-confident first."""
    import math  # noqa: PLC0415

    seq_scores = getattr(generate_output, "sequences_scores", None)
    if seq_scores is None:
        return 1.0
    try:
        logp = float(seq_scores[0])
    except (IndexError, TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, math.exp(logp)))


@functools.lru_cache(maxsize=1)
def _default_detector() -> RebelRelationDetector:
    """The cached default detector. Raises :class:`DetectorUnavailableError` if it can't be built."""
    detector = RebelRelationDetector()
    _rebel()  # fail loudly up front if the optional deps/model can't load
    return detector


def available() -> bool:
    """True iff the default detector (the ``[openrel]`` extra) is loadable — never raises."""
    try:
        _default_detector()
        return True
    except DetectorUnavailableError:
        return False


def extract_open_observations(
    document: "Document",
    *,
    detector: Optional[RelationBearingDetector] = None,
) -> list[OpenObservation]:
    """Run ``detector`` (or the default) over ``document`` and record each flagged span verbatim.

    Each :class:`DetectedSpan` becomes an :class:`~.openobs.OpenObservation` whose single
    :class:`~.provenance.EvidenceRef` is ``EXACT_SPAN`` by construction (``text ==
    document.text[start:end]``). Records are de-duplicated on their deterministic id and returned
    most-confident first (``score`` desc, then span), which is the review order.

    With ``detector=None`` the bundled default (:class:`RebelRelationDetector`, the ``[openrel]``
    extra) is used and :class:`DetectorUnavailableError` is raised if it cannot be loaded — there is
    no model-free floor for discovery.
    """
    detector = detector or _default_detector()
    sentences = {s.sentence_id: s for s in document.iter_sentences()}

    out: list[OpenObservation] = []
    seen: set[str] = set()
    for span in detector.detect(document):
        oid = open_observation_id(document.document_id, span.start_char, span.end_char, detector.name)
        if oid in seen:
            continue
        seen.add(oid)
        sentence = sentences.get(span.sentence_id)
        ref = EvidenceRef.for_span(
            document,
            span.start_char,
            span.end_char,
            sentence=sentence,
            extraction_rule=f"openrel:{detector.name}",
            extraction_rule_version=detector.version,
        )
        out.append(
            OpenObservation(
                open_observation_id=oid,
                document_id=document.document_id,
                sentence_id=span.sentence_id,
                clause_index=span.clause_index,
                start_char=span.start_char,
                end_char=span.end_char,
                text=document.text[span.start_char : span.end_char],
                detector_name=detector.name,
                detector_version=detector.version,
                score=float(span.score),
                evidence_refs=[ref],
            )
        )

    out.sort(key=lambda o: (-o.score, o.start_char, o.end_char))
    return out
