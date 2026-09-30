"""Low-level relation *observations* — the audit layer beneath claims (spec §5, §7).

An :class:`Observation` is a rule match kept deliberately close to the text: it records the two
entity mentions, the predicate the rule assigned, the negation/uncertainty judgement, the cue
that triggered it, and a sentence-level :class:`~.provenance.EvidenceRef`. Observations are
retained even when an endpoint mention did not normalize (``concept_id is None``); such an
observation simply cannot become a :class:`~.claims.Claim` (see :mod:`.claims`), but it is never
silently discarded — the drop is surfaced, not hidden.

There are two binders, chosen by ``use_model``:

* **The flat clause binder — the deterministic, model-free floor** (``use_model=False``). Each
  sentence is segmented into ordered clauses (:mod:`.clauses`) on coordinating/contrastive markers;
  relation matching runs *within a clause*, so both the connecting-text window and the negation
  region are clause-bounded. Within a clause the endpoints are scanned in span order and every
  *consecutive* pair is tested; a qualifier concept (e.g. a ``disease_state`` mention) may sit
  **between** subject and object without breaking the pair. When a contrastive clause elides its
  subject ("… but aggravates pain …") the subject is carried over from the preceding clause. This
  path is byte-reproducible and is exactly the pre-Phase-10 behaviour.

* **The parse binder — Phase 10, the model path** (``use_model=True``). Instead of binding by
  adjacency it binds arguments from the sentence's **dependency structure** (:mod:`.parse`): a
  predicate verb's ``nsubj``/``dobj``/``nmod`` arcs give its subject and object, coordination
  (``conj``/``cc``) expands to the correct cross-product, and a ``during``/``in`` prepositional
  modifier is offered to the qualifier extractor as a *condition* rather than mis-read as the
  object. This is what lets denser entity detection not fragment a relation and what turns
  *"Vitamin D and calcium reduced the risk of cancer and osteoporosis"* into the four observations
  its 2×2 coordination implies. The *predicate* stays rule-based (:func:`.relations.rule_for_verb`),
  so negation/uncertainty and provenance are unchanged; only *argument binding* moves to the parse.
  A parse-bound observation records ``binder:parse`` on ``context``; a sentence the parse cannot
  handle (no mapped predicate/argument) falls back to the flat clause binder, so nothing is lost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from . import clauses as _clauses
from . import qualifiers as _qualifiers
from .modifiers import EntityModifier
from . import relations
from .negation import analyze as analyze_modality
from .provenance import EvidenceRef, ProvenancePrecision
from .qualifiers import Qualifier

if TYPE_CHECKING:
    from .canonical import Document, Sentence
    from .clauses import Clause
    from .entities import EntityMention


class Observation(BaseModel):
    """One rule-detected relation between two mentions, with its modality + provenance."""

    observation_id: str
    document_id: str
    sentence_id: Optional[str] = None

    subject_mention_id: str
    subject_text: str
    subject_concept_id: Optional[str] = None
    # Phase 12 (A2) — restrictive modifiers on the subject head, carried from the endpoint mention so
    # a discriminating one (e.g. localized_in gut microbiome) can widen the claim key.
    subject_modifiers: list[EntityModifier] = []

    predicate: str

    object_mention_id: str
    object_text: str
    object_concept_id: Optional[str] = None
    object_modifiers: list[EntityModifier] = []

    context: Optional[str] = None  # negation/uncertainty cue + clause marker, for audit
    polarity: str
    certainty: str

    rule_id: str
    rule_version: str
    evidence_refs: list[EvidenceRef]

    # Phase 6/7 — typed conditions the relation holds under. Clause-scoped: an observation carries
    # exactly the qualifiers detected in the clause it was matched in.
    qualifiers: list[Qualifier] = []


def _observation_id(document_id: str, subj: "EntityMention", obj: "EntityMention", predicate: str) -> str:
    """Deterministic id from the subject/object spans + predicate (a pair is unique per sentence)."""
    return f"{document_id}_obs_{subj.start_char:06d}_{obj.start_char:06d}_{predicate}"


def _clause_note(clause: "Clause") -> Optional[str]:
    """A compact, auditable record of which marker opened the clause and whether it is contrastive."""
    if clause.marker is None:
        return None
    return f"clause:{clause.marker}({'contrastive' if clause.contrastive else 'coordinating'})"


def _compose_context(modality_cue: Optional[str], clause: "Clause") -> Optional[str]:
    parts = [p for p in (modality_cue, _clause_note(clause)) if p]
    return "; ".join(parts) if parts else None


def _qualifier_spans(quals: "list[Qualifier]") -> list[tuple[int, int]]:
    """Absolute (start, end) spans a qualifier cue occupies — the mentions to skip as endpoints."""
    spans: list[tuple[int, int]] = []
    for q in quals:
        for ref in q.evidence_refs:
            if ref.start_char is not None and ref.end_char is not None:
                spans.append((ref.start_char, ref.end_char))
    return spans


def _is_qualifier_mention(mention: "EntityMention", qual_spans: list[tuple[int, int]]) -> bool:
    """True if the mention's span overlaps a qualifier cue span (so it is a condition, not an endpoint)."""
    return any(
        mention.start_char < qend and qstart < mention.end_char
        for (qstart, qend) in qual_spans
    )


def extract(
    document: "Document",
    mentions: "list[EntityMention]",
    *,
    use_model: bool = True,
) -> list[Observation]:
    """Build observations from Phase-2 mentions over a canonical document.

    ``use_model=False`` runs the deterministic, model-free **flat clause binder** (the floor;
    byte-reproducible, exactly the pre-Phase-10 behaviour). ``use_model=True`` runs the Phase-10
    **parse binder** (:func:`_parse_bind_sentence`) per sentence, falling back to the flat binder
    for any sentence the parse cannot handle — so a relation the floor would have found is never
    lost. The parse binder requires the scispaCy model (its pipeline supplies the parser); if the
    model is unavailable the flat floor is used throughout.
    """
    sentences: dict[str, "Sentence"] = {s.sentence_id: s for s in document.iter_sentences()}

    # Group mentions by their owning sentence, preserving span order.
    by_sentence: dict[str, list["EntityMention"]] = {}
    for m in mentions:
        if m.sentence_id is None:
            continue
        by_sentence.setdefault(m.sentence_id, []).append(m)

    parse_on = use_model and _parse_available()

    observations: list[Observation] = []
    seen: set[str] = set()

    for sentence_id, sent_mentions in by_sentence.items():
        sentence = sentences.get(sentence_id)
        if sentence is None:
            continue

        ordered = sorted(sent_mentions, key=lambda m: (m.start_char, m.end_char))

        made = 0
        if parse_on:
            made = _parse_bind_sentence(document, sentence, ordered, observations, seen)
        # Floor, or fallback for a sentence the parse yielded nothing for (never lose a relation
        # the flat binder would have bound).
        if made == 0:
            _flat_bind_sentence(document, sentence, ordered, observations, seen)

    observations.sort(
        key=lambda o: (o.subject_mention_id, o.object_mention_id, o.predicate)
    )
    return observations


def _flat_bind_sentence(
    document: "Document",
    sentence: "Sentence",
    ordered: "list[EntityMention]",
    observations: list[Observation],
    seen: set[str],
) -> int:
    """The deterministic, model-free flat clause binder for one sentence (the floor).

    Byte-identical to the pre-Phase-10 binder: clause segmentation, consecutive-pair scanning with
    transparent qualifier mentions, and subject carry-over across contrastive clauses. Returns the
    number of observations appended.
    """
    made_total = 0
    clause_list = _clauses.segment(document, sentence)

    # Subject carried across clauses so a contrastive clause that elides its subject
    # ("… but aggravates pain …") still binds to the sentence's subject.
    carried_subject: Optional["EntityMention"] = None

    for clause in clause_list:
        clause_mentions = [
            m for m in ordered
            if m.start_char >= clause.start_char and m.end_char <= clause.end_char
        ]
        # Clause-scoped qualifiers: a contrastive clause's condition attaches only here.
        clause_qualifiers = _qualifiers.extract(
            document, sentence, start_char=clause.start_char, end_char=clause.end_char
        )
        qual_spans = _qualifier_spans(clause_qualifiers)

        def emit_pairs(candidates: "list[EntityMention]") -> int:
            made = 0
            for subj, obj in zip(candidates, candidates[1:]):
                if obj.start_char < subj.end_char:  # overlapping spans — not a relation pair
                    continue
                connecting = document.text[subj.end_char : obj.start_char]
                rule = relations.match(connecting)
                if rule is None:
                    continue
                if _emit(observations, seen, document, sentence, clause,
                         subj, obj, rule, clause_qualifiers):
                    made += 1
            return made

        # A qualifier concept (e.g. a ``disease_state`` mention) is allowed to sit between the
        # subject and object without breaking the pair, so first try binding across such
        # mentions — treat them as transparent conditions, not endpoints.
        reduced = [m for m in clause_mentions if not _is_qualifier_mention(m, qual_spans)]
        made = emit_pairs(reduced)
        # Only a clause with a real pair establishes the subject to carry forward; a lone
        # mention is an object (candidate for subject carry-over), not a new subject.
        if len(reduced) >= 2:
            carried_subject = reduced[0]

        # Fallback: nothing bound with qualifier-mentions transparent, but the clause's only
        # object candidate *is* the condition ("associated with X during remission" — remission
        # is both). Let the qualifier-mention serve as an endpoint so the relation is not lost.
        if made == 0 and len(clause_mentions) >= 2:
            made = emit_pairs(clause_mentions)
            if made and not reduced:
                carried_subject = clause_mentions[0]

        # Subject elided in this clause — borrow it, and look for the cue in the clause text
        # preceding the (single) object. Still clause-bounded.
        if made == 0:
            objects = reduced or clause_mentions
            if len(objects) == 1 and carried_subject is not None \
                    and carried_subject.mention_id != objects[0].mention_id:
                obj = objects[0]
                region = document.text[clause.start_char : obj.start_char]
                rule = relations.match(region)
                if rule is not None:
                    if _emit(observations, seen, document, sentence, clause,
                             carried_subject, obj, rule, clause_qualifiers):
                        made += 1
        made_total += made

    return made_total


def _emit(
    observations: list[Observation],
    seen: set[str],
    document: "Document",
    sentence: "Sentence",
    clause: "Clause",
    subj: "EntityMention",
    obj: "EntityMention",
    rule: "relations.RelationRule",
    clause_qualifiers: "list[Qualifier]",
) -> bool:
    """Build one observation for a bound (subject, cue, object) within a clause.

    Returns ``True`` if an observation was appended, ``False`` if this exact (subject, object,
    predicate) was already produced for the sentence (deduplicated).
    """
    obs_id = _observation_id(document.document_id, subj, obj, rule.predicate)
    if obs_id in seen:
        return False
    seen.add(obs_id)

    # Modality region: clause start → object start, so pre-subject cues ("insufficient evidence
    # that …") and inter-entity cues ("… not …") are seen, but only within *this* clause — a
    # negation in a sibling clause can no longer flip this relation.
    region = document.text[clause.start_char : obj.start_char]
    modality = analyze_modality(
        region, base_polarity=rule.base_polarity, flip_on_negation=rule.flip_on_negation
    )

    evidence = EvidenceRef.for_sentence(
        document,
        sentence,
        precision=ProvenancePrecision.SENTENCE,
        extraction_rule=rule.rule_id,
        extraction_rule_version=rule.version,
    )
    observations.append(
        Observation(
            observation_id=obs_id,
            document_id=document.document_id,
            sentence_id=sentence.sentence_id,
            subject_mention_id=subj.mention_id,
            subject_text=subj.surface_text,
            subject_concept_id=subj.concept_id,
            subject_modifiers=[m.model_copy(deep=True) for m in subj.modifiers],
            predicate=rule.predicate,
            object_mention_id=obj.mention_id,
            object_text=obj.surface_text,
            object_concept_id=obj.concept_id,
            object_modifiers=[m.model_copy(deep=True) for m in obj.modifiers],
            context=_compose_context(modality.cue, clause),
            polarity=modality.polarity,
            certainty=modality.certainty,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            evidence_refs=[evidence],
            qualifiers=[q.model_copy(deep=True) for q in clause_qualifiers],
        )
    )
    return True


# --- parse binder (Phase 10, model path) ---------------------------------------------
#
# The parser lives inside the scispaCy model :mod:`.entities` already loads (its pipeline is
# ``tok2vec tagger attribute_ruler lemmatizer parser ner``), so there is no separate parse model to
# install — :mod:`.parse` wraps that same object. When the model is unavailable the binder is
# simply never selected and the flat floor runs throughout.

_PARSE_BINDER_TAG = "binder:parse"


def model_available() -> bool:
    """True iff the parser-bearing scispaCy model is loadable (the parse binder's precondition)."""
    return _parse_available()


def _parse_available() -> bool:
    try:
        from . import parse as _parse

        return _parse.available()
    except Exception:
        return False


def _dedup_keep_order(mentions: "list[EntityMention]") -> "list[EntityMention]":
    seen: set[str] = set()
    out: list["EntityMention"] = []
    for m in mentions:
        if m is not None and m.mention_id not in seen:
            seen.add(m.mention_id)
            out.append(m)
    return out


def _parse_bind_sentence(
    document: "Document",
    sentence: "Sentence",
    ordered: "list[EntityMention]",
    observations: list[Observation],
    seen: set[str],
) -> int:
    """Bind relations for one sentence from its dependency parse (Phase 10). Model path only.

    For each predicate verb (the ROOT and its ``conj`` verbs — coordinated predicates sharing a
    subject), subjects and objects are read off the verb's argument arcs and expanded across
    coordination, prep-phrase conditions are diverted to the qualifier extractor, and the
    subject×object cross-product is emitted. Returns the number of observations appended; ``0``
    means the caller should fall back to the flat binder for this sentence.
    """
    from . import parse as _parse

    sp = _parse.SentenceParse(sentence, ordered)
    made = 0
    for root in sp.roots():
        root_subjects = sp.subject_tokens(root)
        for pred in [root] + sp.conj_verbs(root):
            # A coordinated predicate that elides its subject ("reduced CRP but increased …")
            # inherits the ROOT's subject.
            subj_tokens = sp.subject_tokens(pred) if pred.i != root.i else root_subjects
            if pred.i != root.i and not subj_tokens:
                subj_tokens = root_subjects

            # Resolve objects, unwrapping the ``risk``-object special case to its ``of``-phrase
            # endpoints and recording that the predicate is a risk predicate.
            object_is_risk = False
            obj_tokens: list = []
            for ot in sp.object_tokens(pred):
                if ot.lemma_.lower() == "risk":
                    object_is_risk = True
                    obj_tokens.extend(sp.risk_objects(ot))
                else:
                    obj_tokens.append(ot)

            rule = relations.rule_for_verb(pred.lemma_, object_is_risk=object_is_risk)
            if rule is None:
                continue  # not a mapped predicate verb — leave this predicate to the fallback

            subjects = _dedup_keep_order([sp.mention_for_token(t) for t in subj_tokens])
            objects = _dedup_keep_order([sp.mention_for_token(t) for t in obj_tokens])
            if not subjects or not objects:
                continue

            # Prep-phrase conditions of this predicate → qualifiers, scoped to each condition
            # phrase's own span (so a sibling predicate's condition does not bleed in).
            qualifiers = _parse_qualifiers(document, sentence, sp, pred)

            neg = sp.has_neg(pred)
            for subj in subjects:
                for obj in objects:
                    if subj.mention_id == obj.mention_id:
                        continue  # a predicate never relates an entity to itself
                    if _emit_parse(observations, seen, document, sentence,
                                   subj, obj, rule, qualifiers, neg):
                        made += 1
    return made


def _parse_qualifiers(
    document: "Document",
    sentence: "Sentence",
    sp: "object",
    pred,
) -> "list[Qualifier]":
    """Typed qualifiers from ``pred``'s prep-phrase condition modifiers ("during remission")."""
    quals: list[Qualifier] = []
    for cond in sp.condition_tokens(pred):
        start, end = sp.token_span(cond)
        quals.extend(
            _qualifiers.extract(document, sentence, start_char=start, end_char=end)
        )
    # Deduplicate on identity, preserving order.
    out: list[Qualifier] = []
    keys: set[tuple[str, str]] = set()
    for q in quals:
        key = (q.qualifier_type, q.value_concept_id or q.value_text.casefold())
        if key not in keys:
            keys.add(key)
            out.append(q)
    return out


def _emit_parse(
    observations: list[Observation],
    seen: set[str],
    document: "Document",
    sentence: "Sentence",
    subj: "EntityMention",
    obj: "EntityMention",
    rule: "relations.RelationRule",
    qualifiers: "list[Qualifier]",
    neg: bool,
) -> bool:
    """Append one parse-bound observation (subject, predicate verb, object). Deduplicated.

    Modality is judged over the sentence text up to the object (so pre-verb cues — "insufficient
    evidence …", "may", "not" — are seen); the predicate's ``neg`` arc forces a flip even when the
    regex missed it. The observation records ``binder:parse`` on ``context`` for audit.
    """
    obs_id = _observation_id(document.document_id, subj, obj, rule.predicate)
    if obs_id in seen:
        return False
    seen.add(obs_id)

    region = document.text[sentence.start_char : max(obj.start_char, subj.end_char)]
    modality = analyze_modality(
        region, base_polarity=rule.base_polarity, flip_on_negation=rule.flip_on_negation
    )
    cue = modality.cue
    polarity = modality.polarity
    # The dependency ``neg`` arc is authoritative: if the verb is negated but the region regex did
    # not already flip the polarity, flip it here so parse-bound negation is never missed.
    if neg and rule.flip_on_negation and polarity == rule.base_polarity:
        polarity = _flip_polarity(rule.base_polarity)
        cue = "; ".join(p for p in (cue, "neg") if p)

    context_parts = [p for p in (cue, _PARSE_BINDER_TAG) if p]
    context = "; ".join(context_parts) if context_parts else None

    evidence = EvidenceRef.for_sentence(
        document,
        sentence,
        precision=ProvenancePrecision.SENTENCE,
        extraction_rule=rule.rule_id,
        extraction_rule_version=rule.version,
    )
    observations.append(
        Observation(
            observation_id=obs_id,
            document_id=document.document_id,
            sentence_id=sentence.sentence_id,
            subject_mention_id=subj.mention_id,
            subject_text=subj.surface_text,
            subject_concept_id=subj.concept_id,
            subject_modifiers=[m.model_copy(deep=True) for m in subj.modifiers],
            predicate=rule.predicate,
            object_mention_id=obj.mention_id,
            object_text=obj.surface_text,
            object_concept_id=obj.concept_id,
            object_modifiers=[m.model_copy(deep=True) for m in obj.modifiers],
            context=context,
            polarity=polarity,
            certainty=modality.certainty,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            evidence_refs=[evidence],
            qualifiers=[q.model_copy(deep=True) for q in qualifiers],
        )
    )
    return True


def _flip_polarity(base_polarity: str) -> str:
    from .enums import Polarity

    if base_polarity == Polarity.POSITIVE.value:
        return Polarity.NEGATIVE.value
    if base_polarity == Polarity.NEGATIVE.value:
        return Polarity.POSITIVE.value
    return base_polarity
