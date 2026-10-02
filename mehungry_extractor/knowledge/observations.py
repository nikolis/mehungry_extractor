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
  A parse-bound observation records ``binder:parse`` on ``context``. The flat clause binder is used
  as a **fallback only for a sentence whose predicate the parse did not recognise** — a lexical
  predicate (``no_association``/``no_effect``/…) or a verb outside the verb-predicate map. When the
  parse *did* recognise a predicate but bound nothing because an argument did not resolve to a
  concept, the fallback is **suppressed**: that is the honest signal that the true argument is a
  non-entity (an abstract noun, an anaphor, a population), and the adjacency floor would only
  re-introduce the mis-binding the parse declined to make. Such a relation stays dropped and
  reportable rather than fabricated (Concept 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from . import RULESET_VERSION as _RULESET_VERSION
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

    # Phase 13 (hierarchical relations) — the observation this one is nested beneath: the relation
    # whose *object* is this relation's *subject*, within the same sentence ("inflammation causes
    # **dysbiosis**" is the parent of "**dysbiosis** characterized by …" / "**dysbiosis** decreases
    # …"). ``None`` for a top-level relation. Derived post-hoc from the bound endpoints
    # (:func:`_link_parents`), so it is additive — it never changes which observations are emitted —
    # and it is *not* part of :func:`_observation_id`, so ids stay stable across this change.
    parent_observation_id: Optional[str] = None

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
        block_fallback = False
        if parse_on:
            made, block_fallback = _parse_bind_sentence(
                document, sentence, ordered, observations, seen
            )
        # Fall back to the flat clause binder unless the parse positively determined that the
        # relation's **subject is a non-entity** (``block_fallback``). That happens when a mapped
        # predicate had an overt noun subject — or a control-resolved controller — that did not
        # resolve to a concept: an abstract noun ("Nutritional care", "an imbalance"), a demonstrative
        # anaphor ("This diet"), or a population. In that case the adjacency floor would only
        # re-introduce the mis-binding the parse declined to make (grabbing whatever entity sits
        # nearby as the subject), so the relation is kept *dropped and reportable* rather than
        # fabricated (Concept 3: never assert a fact whose endpoint is not a known concept).
        #
        # The fallback still runs for a sentence whose predicate the parse did not recognise (a
        # lexical predicate — ``no_association``/``no_effect``/… — or a verb outside
        # :data:`.relations.VERB_PREDICATE_MAP`), and for a predicate whose subject is a pronoun or a
        # control structure the parse could not resolve — there the flat adjacency heuristic is a
        # reasonable last resort rather than a fabrication.
        if made == 0 and not block_fallback:
            _flat_bind_sentence(document, sentence, ordered, observations, seen)

    _link_parents(observations, mentions)
    observations.sort(
        key=lambda o: (o.subject_mention_id, o.object_mention_id, o.predicate)
    )
    return observations


def _link_parents(
    observations: list[Observation], mentions: "list[EntityMention]"
) -> None:
    """Set ``parent_observation_id`` on each observation that elaborates another's object (Phase 13).

    A relation whose **subject** is another relation's **object** — within the same sentence — is a
    *child* of that relation: the nested, hierarchical reading ("inflammation causes dysbiosis" is
    the parent of "dysbiosis characterized by …" and "dysbiosis decreases …"). The link is derived
    purely from the bound endpoints, so it serves **both binders**: on the parse path a subordinate
    clause resolves its subject to the governing clause's object, so several children share one
    parent (a *tree*); on the flat floor adjacency resolves each subject to the previous object, so
    the same rule yields a *chain*. It is additive — it never changes which observations are emitted,
    only annotates them.

    Deterministic and acyclic: within a sentence, observations are ordered by (subject span, object
    span, predicate) and each is linked to the *earliest strictly-earlier* observation whose object
    mention is this one's subject mention. Linking only to an earlier observation in that fixed order
    makes a cycle impossible (A↔B can never both point at each other)."""
    start_of = {m.mention_id: m.start_char for m in mentions}
    by_sentence: dict[Optional[str], list[Observation]] = {}
    for o in observations:
        by_sentence.setdefault(o.sentence_id, []).append(o)
    for sent_obs in by_sentence.values():
        ordered = sorted(
            sent_obs,
            key=lambda o: (
                start_of.get(o.subject_mention_id, -1),
                start_of.get(o.object_mention_id, -1),
                o.predicate,
            ),
        )
        for idx, child in enumerate(ordered):
            for parent in ordered[:idx]:
                if (
                    parent.object_mention_id == child.subject_mention_id
                    and parent.observation_id != child.observation_id
                ):
                    child.parent_observation_id = parent.observation_id
                    break


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


# The entity type stamped on a synthetic descriptive-object mention (below). The phrase it carries
# is a free-text descriptor, not a dictionary entity, so this type never maps to a vocabulary and
# the mention is never added to the persisted entity set — it exists only to carry the object
# surface of a ``characterized_by`` relation. The value only needs to be stable for the id.
_DESCRIPTOR_ENTITY_TYPE = "descriptor"
_DESCRIPTIVE_OBJECT_RULE = "parse_descriptive_object"


def _descriptive_object_mention(
    document: "Document", sentence: "Sentence", start: int, end: int
) -> "EntityMention":
    """A synthetic object mention spanning a descriptive relation's full head-noun phrase (Phase 15).

    The object of ``characterized_by`` is the *whole* descriptor — e.g. "alterations in the
    composition and function of the gut microbiota" — not the deep entity the general object resolver
    would descend to (*gut microbiota*). That phrase is not a vocabulary concept, so the mention
    carries its surface verbatim with ``concept_id=None`` (``unmatched``), exactly like any endpoint
    that did not normalize: the observation is retained with the correct object text, and the claim
    layer forms no concept-level edge from it (a free-text descriptor is not a concept node, so there
    is deliberately nothing to normalize). The id is span+type derived, so re-runs reproduce it, and
    the mention is not added to the persisted entity set — it is only an argument carrier."""
    from .entities import EntityMention, _mention_id
    from .normalize import NORMALIZATION_SOURCE, STATUS_UNMATCHED

    return EntityMention(
        mention_id=_mention_id(document.document_id, start, end, _DESCRIPTOR_ENTITY_TYPE),
        document_id=document.document_id,
        sentence_id=sentence.sentence_id,
        surface_text=document.text[start:end],
        normalized_text=None,
        concept_id=None,
        entity_type=_DESCRIPTOR_ENTITY_TYPE,
        start_char=start,
        end_char=end,
        normalization_source=NORMALIZATION_SOURCE,
        status=STATUS_UNMATCHED,
        evidence_ref=EvidenceRef.for_span(
            document,
            start,
            end,
            sentence=sentence,
            extraction_rule=_DESCRIPTIVE_OBJECT_RULE,
            extraction_rule_version=_RULESET_VERSION,
        ),
    )


def _parse_bind_sentence(
    document: "Document",
    sentence: "Sentence",
    ordered: "list[EntityMention]",
    observations: list[Observation],
    seen: set[str],
) -> "tuple[int, bool]":
    """Bind relations for one sentence from its dependency parse (Phase 10 + Phase 4a). Model path.

    For each predicate head (:meth:`.parse.SentenceParse.predicate_heads` — the ROOT, its ``conj``
    verbs, and verbal predicates nested in subordinate clauses), subjects and objects are read off
    the verb's argument arcs and expanded across coordination, an elided subject is resolved via its
    controller, prep-phrase conditions are diverted to the qualifier extractor, and the
    subject×object cross-product is emitted.

    Returns ``(made, block_fallback)``: the number of observations appended, and whether the parse
    positively determined that a mapped predicate's **subject is a non-entity** (an overt noun, or a
    control-resolved controller, that did not resolve to a concept). The caller suppresses the flat
    adjacency fallback only when ``block_fallback`` — so a fabricated subject is not substituted for
    the relation the parse honestly declined. A predicate whose subject is a pronoun or an
    unresolvable control structure does *not* block: the flat heuristic is left as a last resort.
    """
    from . import parse as _parse

    sp = _parse.SentenceParse(sentence, ordered)
    made = 0
    block_fallback = False
    for root in sp.predicate_heads():
        root_subjects = sp.subject_tokens(root)
        # A participial / reduced-relative predicate ("consumption **associated** with reduced cancer")
        # binds its antecedent noun as subject, but that antecedent is frequently a non-entity head
        # whose real entity sits in a PP the strict subject resolver will not enter. Such a participle
        # must therefore *not* trip ``block_fallback`` when it fails to resolve a subject — unlike a
        # main-clause predicate, the flat adjacency binder remains a reasonable last resort for it
        # (mirroring the pronoun-subject case). It still binds normally when its arguments do resolve.
        is_participial = root.dep_.split(":")[0] in ("acl", "relcl")
        for pred in [root] + sp.conj_verbs(root):
            # A coordinated predicate that elides its subject ("reduced CRP but increased …")
            # inherits the ROOT's subject.
            subj_tokens = sp.subject_tokens(pred) if pred.i != root.i else root_subjects
            if pred.i != root.i and not subj_tokens:
                subj_tokens = root_subjects

            # Unwrap a measure/container-noun *subject* to the entities in its content genitive — the
            # subject-side mirror of the object unwrap below ("**Intake of red meat** increased CRP"
            # → red meat; "**consumption of vegetables** … associated with …" → vegetables). Without
            # this the strict subject resolver (which never enters an ``of``-phrase) leaves such a
            # subject unresolved and the relation is dropped. Falls back to the noun itself when it
            # has no content genitive.
            expanded_subj: list = []
            for st in subj_tokens:
                if st.lemma_.lower() in _parse.MEASURE_NOUNS:
                    unwrapped = sp.measure_objects(st)
                    expanded_subj.extend(unwrapped if unwrapped else [st])
                else:
                    expanded_subj.append(st)
            subj_tokens = expanded_subj

            # Resolve objects, unwrapping a measure/container noun to the entities in its content
            # genitive (Phase 4b). ``risk`` additionally promotes the predicate (reduces_risk /
            # increases_risk); other measure nouns ("intake of red meat and saturated fats") unwrap
            # to the coordinated endpoints without changing the predicate, falling back to the noun
            # itself if it has no content genitive.
            # A direction word on the object ("associated with **reduced** CRP", "**lower** risk")
            # promotes a bare association to its directional variant (:func:`.relations.rule_for_verb`).
            # Read off the object noun *before* it is unwrapped, so "reduced consumption of X" and
            # "reduced risk of X" are both caught; first direction found wins.
            object_is_risk = False
            object_direction: Optional[str] = None
            obj_tokens: list = []
            for ot in sp.object_tokens(pred):
                lemma = ot.lemma_.lower()
                if lemma == "risk":
                    object_is_risk = True
                    object_direction = object_direction or sp.direction_of(ot)
                    obj_tokens.extend(sp.measure_objects(ot))
                elif lemma in _parse.MEASURE_NOUNS:
                    object_direction = object_direction or sp.direction_of(ot)
                    unwrapped = sp.measure_objects(ot)
                    obj_tokens.extend(unwrapped if unwrapped else [ot])
                else:
                    object_direction = object_direction or sp.direction_of(ot)
                    obj_tokens.append(ot)

            rule = relations.rule_for_verb(
                pred.lemma_, object_is_risk=object_is_risk, object_direction=object_direction
            )
            if rule is None:
                continue  # not a mapped predicate verb — leave this predicate to the fallback

            # Condition prep-phrases inherited from a governing clause when this predicate's
            # subject is control-resolved (B1). Populated only in the control branch below, so a
            # predicate with its own overt subject never inherits a governor's condition.
            inherited_conditions: list = []

            subjects = _dedup_keep_order([sp.subject_mention_for_token(t) for t in subj_tokens])
            if not subjects:
                # A free-adjunct present participle ("induces dysbiosis, **decreasing** X") carries no
                # subject of its own; its implicit subject is the governing clause's object (Phase 13).
                # Resolve that before the generic cascade so "decreasing X" binds `dysbiosis → X` and
                # nests under the parent, instead of being deferred to the flat fallback.
                part_tokens = sp.participle_subject_tokens(pred)
                if part_tokens:
                    subjects = _dedup_keep_order(
                        [sp.subject_mention_for_token(t) for t in part_tokens]
                    )
                    if subjects:
                        # A directional effect participle over a *state* ("dysbiosis, **decreasing**
                        # Firmicutes") describes a compositional change the state manifests, not an
                        # effect it exerts — re-label it to the descriptive abundance-manifestation
                        # predicate of the same direction (Phase 14). Any non-directional predicate
                        # is left exactly as the verb map produced it.
                        manifestation_rule = relations.abundance_manifestation_for(rule.predicate)
                        if manifestation_rule is not None:
                            rule = manifestation_rule
            if not subjects:
                # The predicate has no *directly resolved* subject. Decide, from the subject's
                # syntactic shape, between several outcomes (Phase 4a/4f):
                #   • a gerund clausal subject ("Consuming … of kefir")  → the agent is inside the
                #     clause: resolve the subject verb permissively (Phase 4f);
                #   • an overt noun that did not resolve  → the true subject is a non-entity: block
                #     the flat fallback so it cannot fabricate one from an adjacent entity (Fix 1);
                #   • a pronoun ("they"/"it")             → an anaphor we cannot resolve reliably:
                #     leave it to the flat adjacency fallback rather than block or mis-bind;
                #   • an elided subject (control)         → resolve the controller from the governing
                #     clause; failing that, share the sentence's gerund clausal subject (Phase 4f).
                gerund_subjects = _dedup_keep_order(
                    [sp.mention_for_token(t) for t in subj_tokens if t.pos_ in ("VERB", "AUX")]
                )
                if gerund_subjects:
                    subjects = gerund_subjects
                elif any(t.pos_ in ("NOUN", "PROPN") for t in subj_tokens):
                    # An overt non-entity subject blocks the flat fallback — but only for a
                    # main-clause predicate. A participle's antecedent may legitimately keep its real
                    # entity in a PP we won't enter, so it defers to the fallback instead of blocking.
                    if not is_participial:
                        block_fallback = True
                    continue
                elif any(t.pos_ == "PRON" for t in subj_tokens):
                    continue
                else:
                    ctrl_tokens = sp.controller_tokens(pred)
                    subjects = _dedup_keep_order(
                        [sp.subject_mention_for_token(t) for t in ctrl_tokens]
                    )
                    if subjects:
                        # Subject came from the governing clause (control) — inherit that clause's
                        # scoping conditions too ("…shown in patients with active disease to
                        # reduce…"), which hang off the governor, not this controlled verb (B1).
                        inherited_conditions = sp.controller_conditions(pred)
                    if not subjects:
                        if ctrl_tokens and any(t.pos_ in ("NOUN", "PROPN") for t in ctrl_tokens):
                            if not is_participial:
                                block_fallback = True
                            continue
                        # Last resort: the sentence's gerund clausal subject, shared by predicates
                        # that elide their own ("Consuming … helps regulate …, improves …").
                        subjects = _dedup_keep_order(sp.clausal_subject_mentions())
                        if not subjects:
                            continue

            # Descriptive-relation object (Phase 15). A ``characterized_by`` object is the **whole**
            # descriptor phrase, not the deep entity the general resolver would descend to:
            # "characterized by **alterations in the composition and function of the gut microbiota**"
            # is *about* the alterations — binding the object to *gut microbiota* asserts the wrong
            # thing. So when the object head noun is not itself an entity but dominates one (the
            # descent case), the full head-noun phrase is taken verbatim as the object. A descriptive
            # object that *is* an entity ("characterized by inflammation") keeps the normal resolution.
            if rule.predicate == "characterized_by":
                objects = []
                for ot in sp.object_tokens(pred):
                    span = sp.descriptive_object_span(ot)
                    if span is not None:
                        objects.append(
                            _descriptive_object_mention(document, sentence, span[0], span[1])
                        )
                    else:
                        objects.append(sp.mention_for_token(ot))
                objects = _dedup_keep_order(objects)
            else:
                objects = _dedup_keep_order([sp.mention_for_token(t) for t in obj_tokens])
            if not objects:
                continue

            # Prep-phrase conditions of this predicate → qualifiers, scoped to each condition
            # phrase's own span (so a sibling predicate's condition does not bleed in). A
            # control-resolved predicate additionally inherits the governing clause's conditions
            # (B1), so "…shown in patients with active disease to reduce…" carries the condition.
            qualifiers = _parse_qualifiers(
                document, sentence, sp, pred, extra_condition_tokens=inherited_conditions
            )

            neg = sp.has_neg(pred)
            for subj in subjects:
                for obj in objects:
                    if subj.mention_id == obj.mention_id:
                        continue  # a predicate never relates an entity to itself
                    if _emit_parse(observations, seen, document, sentence,
                                   subj, obj, rule, qualifiers, neg):
                        made += 1
    return made, block_fallback


def _parse_qualifiers(
    document: "Document",
    sentence: "Sentence",
    sp: "object",
    pred,
    extra_condition_tokens: "tuple | list" = (),
) -> "list[Qualifier]":
    """Typed qualifiers from ``pred``'s prep-phrase condition modifiers ("during remission").

    ``extra_condition_tokens`` are condition phrases inherited from a governing clause when
    ``pred`` is subject-controlled (B1, :meth:`.parse.SentenceParse.controller_conditions`); they
    are scanned alongside ``pred``'s own, deduplicated identically, so a governor's scoping
    condition ("…shown in patients with active disease to reduce…") attaches to the controlled
    relation rather than being lost on the non-emitting governing verb.
    """
    quals: list[Qualifier] = []
    cond_tokens = list(sp.condition_tokens(pred)) + list(extra_condition_tokens)
    seen_spans: set[tuple[int, int]] = set()
    for cond in cond_tokens:
        start, end = sp.token_span(cond)
        if (start, end) in seen_spans:
            continue
        seen_spans.add((start, end))
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
