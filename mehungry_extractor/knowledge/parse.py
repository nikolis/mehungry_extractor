"""Dependency-parse navigation for relation-argument binding (Phase 10).

The flat binder in :mod:`.observations` binds a relation's subject and object by *adjacency* —
it pairs entity mentions that happen to sit next to each other in the text. That design cannot
survive denser entity detection: any intervening span (a modifier tagged as its own entity) or
any coordination ("A and B reduced C and D") mis-binds or fragments the pair. This module binds
arguments from **grammatical structure** instead, walking the sentence's dependency arcs off a
predicate verb (``nsubj``/``dobj``/``nmod``) and expanding coordination (``conj``/``cc``).

It is a thin wrapper over the parser that is *already inside* the scispaCy model loaded by
:mod:`.entities` (``en_ner_bc5cdr_md``'s pipeline includes a ``parser``), so it adds **no new model
dependency**. It is only exercised on the model path (``use_model=True``); the deterministic,
model-free floor never imports a parse. Everything here returns absolute character offsets rebased
through ``sentence.start_char`` + ``token.idx``, so the provenance offset contract still holds:
every bound argument maps back to a real char span of the document text.

The *predicate* stays rule-based (see :mod:`.relations`); this module only navigates the parse to
find **which mentions are the arguments** of a predicate the rules already recognise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from . import entities as _entities

if TYPE_CHECKING:
    from .canonical import Sentence
    from .entities import EntityMention

# Argument dependency labels. The model emits a hybrid ClearNLP/UD label set (``dobj`` for direct
# objects, but ``nmod``+``case`` for prepositional phrases, ``nsubjpass`` for passive subjects), so
# the sets below intentionally union both conventions to stay robust.
_SUBJECT_DEPS = {"nsubj", "nsubjpass", "csubj", "csubjpass"}
_OBJECT_DEPS = {"dobj", "obj", "dative", "attr", "oprd", "nmod", "obl", "pobj"}
_PREP_PHRASE_DEPS = {"nmod", "obl", "pobj"}

# Prepositions that introduce a *condition* the relation holds under (→ offered to the qualifier
# extractor) rather than the relation's object. Deliberately excludes ``with``/``of``: those are
# the tails of relation connectives ("associated **with** X", "risk **of** Y") where the noun is
# the object, not a condition — mirroring :data:`.qualifiers._CONTEXT`'s trigger set.
_CONDITION_PREPS = {"during", "in", "under", "among", "amongst", "throughout", "while"}

# spaCy part-of-speech tags a predicate token may carry (a verb, or an auxiliary standing in for
# an elided verb).
_VERBAL_POS = {"VERB", "AUX"}


def available() -> bool:
    """True iff the parser-bearing model is loadable (delegates to :func:`.entities.model_available`)."""
    return _entities.model_available()


def _nlp():
    """The cached scispaCy pipeline (``tok2vec … parser ner``) already loaded by :mod:`.entities`."""
    return _entities._nlp()


def _depth(tok) -> int:
    """Number of ``head`` hops from ``tok`` to its sentence root (guards against cycles)."""
    depth = 0
    seen = tok
    while seen.head.i != seen.i and depth < 200:
        seen = seen.head
        depth += 1
    return depth


class SentenceParse:
    """A parsed sentence with helpers to bind :class:`.entities.EntityMention` to argument tokens.

    The sentence text is parsed once on construction; each mention is mapped to its **anchor
    token** — the head of the mention's phrase (the token nearest the parse root whose char span
    overlaps the mention). Argument navigation then resolves an argument token back to the mention
    anchored at (or dominated by) it. All character offsets exposed are absolute document offsets.
    """

    def __init__(self, sentence: "Sentence", mentions: "list[EntityMention]") -> None:
        self.base = sentence.start_char
        self.doc = _nlp()(sentence.text)
        self.mentions = mentions
        self._anchor_to_mention: dict[int, "EntityMention"] = self._map_mentions()

    # --- mention ↔ token mapping ------------------------------------------------------

    def _map_mentions(self) -> "dict[int, EntityMention]":
        """Map each mention to its anchor token index (the phrase head), first-writer-wins."""
        out: dict[int, "EntityMention"] = {}
        for m in self.mentions:
            rel_start = m.start_char - self.base
            rel_end = m.end_char - self.base
            covering = [
                t for t in self.doc
                if t.idx < rel_end and (t.idx + len(t.text)) > rel_start
            ]
            if not covering:
                continue
            # The anchor is the token closest to the parse root (min head-depth); ties break to the
            # leftmost token, so a multi-token phrase resolves to a single, stable head.
            anchor = min(covering, key=lambda t: (_depth(t), t.i))
            out.setdefault(anchor.i, m)
        return out

    def mention_for_token(self, tok) -> "Optional[EntityMention]":
        """Resolve an argument token to a mention: exact anchor first, else the head-most mention
        inside the token's subtree (so an object *noun* that is not itself an entity still binds to
        the entity phrase modifying it, e.g. "reduced disease" under "activity")."""
        exact = self._anchor_to_mention.get(tok.i)
        if exact is not None:
            return exact
        best: "Optional[EntityMention]" = None
        best_depth = 1 << 30
        for sub in tok.subtree:
            cand = self._anchor_to_mention.get(sub.i)
            if cand is not None:
                d = _depth(sub)
                if d < best_depth:
                    best, best_depth = cand, d
        return best

    # --- predicate discovery ----------------------------------------------------------

    def roots(self) -> list:
        """The sentence's ROOT tokens (usually one) that are verbal — the primary predicates."""
        return [t for t in self.doc if t.dep_ == "ROOT" and t.pos_ in _VERBAL_POS]

    def conj_verbs(self, verb) -> list:
        """Verbal ``conj`` children of ``verb`` — coordinated predicates sharing its subject
        ("reduced CRP **but increased** bloating")."""
        return [c for c in verb.children if c.dep_ == "conj" and c.pos_ in _VERBAL_POS]

    # --- argument navigation ----------------------------------------------------------

    def coordinate(self, tok) -> list:
        """``tok`` plus its transitive ``conj`` chain — the coordinated set the token heads."""
        out = [tok]
        for c in tok.children:
            if c.dep_ == "conj":
                out.extend(self.coordinate(c))
        return out

    def subject_tokens(self, verb) -> list:
        """Subject argument tokens of ``verb`` (``nsubj``/``nsubjpass``), coordination-expanded.
        Empty when the verb elides its subject (the caller supplies an inherited one)."""
        out: list = []
        for c in verb.children:
            if c.dep_ in _SUBJECT_DEPS:
                out.extend(self.coordinate(c))
        return out

    def condition_tokens(self, verb) -> list:
        """Prepositional-phrase modifiers of ``verb`` that read as *conditions* ("during
        remission", "in children") rather than objects — offered to the qualifier extractor."""
        out: list = []
        for c in verb.children:
            if c.dep_ in _PREP_PHRASE_DEPS and self._has_condition_prep(c):
                out.append(c)
        return out

    def anchored_mentions(self) -> "list[tuple[EntityMention, object]]":
        """``(mention, anchor_token)`` for every mention that mapped to a phrase-head token.

        The entity-level counterpart to the predicate helpers: :mod:`.modifiers` walks each
        anchor's prep-phrase children (:meth:`modifier_phrases`) to attach restrictive modifiers to
        the entity head. Deterministically ordered by token index."""
        return [(m, self.doc[i]) for i, m in sorted(self._anchor_to_mention.items())]

    def modifier_phrases(self, anchor_tok) -> "list[tuple[str, object]]":
        """``(preposition, object_head_token)`` for attributive prep-phrase modifiers of a **noun**
        anchor (Phase 12). Mirrors :meth:`condition_tokens`, but these hang off an entity's own head
        token and are keyed by the entity vocabulary rather than the disease-state one — so
        "dysbiosis **of** the gut microbiome" yields ``("of", microbiome)``. Empty for a token with
        no prep-phrase child."""
        out: list = []
        for c in anchor_tok.children:
            if c.dep_ in _PREP_PHRASE_DEPS:
                prep = self._prep_surface(c)
                if prep is not None:
                    out.append((prep, c))
        return out

    def _prep_surface(self, tok) -> "Optional[str]":
        """The lowercased preposition (``case``/``mark`` child) introducing a prep phrase, or None."""
        for c in tok.children:
            if c.dep_ == "case" or c.dep_ == "mark":
                return c.text.lower()
        return None

    def object_tokens(self, verb) -> list:
        """Object argument tokens of ``verb``, coordination-expanded, with condition phrases
        removed (they are qualifiers, not objects)."""
        conditions = {t.i for t in self.condition_tokens(verb)}
        out: list = []
        for c in verb.children:
            if c.dep_ in _OBJECT_DEPS and c.i not in conditions:
                out.extend(self.coordinate(c))
        return out

    def risk_objects(self, obj_tok) -> list:
        """For the ``risk``-object special case ("reduced the risk **of** cancer and osteoporosis"),
        the real endpoints are the entities under ``risk``'s prepositional phrase, conj-expanded."""
        out: list = []
        for c in obj_tok.children:
            if c.dep_ in _PREP_PHRASE_DEPS:
                out.extend(self.coordinate(c))
        return out

    def has_neg(self, verb) -> bool:
        """True iff ``verb`` carries a negation arc ("did **not** reduce")."""
        return any(c.dep_ == "neg" for c in verb.children)

    def _has_condition_prep(self, tok) -> bool:
        case_markers = [c for c in tok.children if c.dep_ == "case" or c.dep_ == "mark"]
        return any(
            (c.lemma_.lower() in _CONDITION_PREPS or c.text.lower() in _CONDITION_PREPS)
            for c in case_markers
        )

    def token_span(self, tok) -> tuple[int, int]:
        """Absolute ``(start_char, end_char)`` of ``tok``'s full subtree — the phrase it heads."""
        toks = list(tok.subtree)
        rel_start = min(t.idx for t in toks)
        rel_end = max(t.idx + len(t.text) for t in toks)
        return self.base + rel_start, self.base + rel_end
