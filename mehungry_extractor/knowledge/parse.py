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

# Subordinate-clause arcs whose verbal head is *also* a predicate to bind from (Phase 4a). A
# relation is frequently stated in a relative clause ("products **that increase** …"), an adverbial
# clause ("…crucial, focusing on **preventing** …"), or a clausal complement ("research
# recommending **reducing** …"); binding only the ROOT + its ``conj`` verbs leaves those invisible,
# so the flat adjacency fallback takes over and mis-binds. Matched on the label's base (the model
# emits ``acl:relcl``), so both ``acl`` and ``relcl`` conventions are covered.
_SUB_CLAUSE_DEPS = {"advcl", "acl", "relcl", "ccomp", "xcomp", "pcomp"}

# Relative pronouns whose real referent is the noun the relative clause modifies (its antecedent).
_REL_PRONOUNS = {"that", "which", "who", "whom", "whose"}

# Modifier arcs a *subject* resolver may descend to find an entity that modifies a non-entity head
# noun ("sulfur-containing" under "products"). Deliberately excludes prepositional/clausal arcs
# (nmod/obl/pobj/acl/appos): an entity buried in a restrictive PP under the subject head is not the
# subject ("IBD" in "care in IBD patients"), and reaching it is how a subject gets fabricated.
_SUBJECT_DESCENT_DEPS = {"amod", "compound", "conj", "nummod"}

# Prepositions that introduce a *condition* the relation holds under (→ offered to the qualifier
# extractor) rather than the relation's object. Deliberately excludes ``with``/``of``: those are
# the tails of relation connectives ("associated **with** X", "risk **of** Y") where the noun is
# the object, not a condition — mirroring :data:`.qualifiers._CONTEXT`'s trigger set.
_CONDITION_PREPS = {"during", "in", "under", "among", "amongst", "throughout", "while"}

# Prepositions that introduce a measure/container noun's *content* ("intake **of** red meat", "risk
# **of** cancer") — the phrase whose entities are the real endpoints. Deliberately narrow: a locative
# or condition phrase on the same noun ("concentration of H2S **in the intestine**") uses a different
# preposition and is left for the qualifier extractor, not taken as an endpoint.
_CONTENT_GENITIVE_PREPS = {"of", "for"}

# Container/measure nouns whose grammatical object status stands in for the entities inside their
# content genitive (Phase 4b). "reduce the intake of red meat and saturated fats" is a relation about
# red meat and saturated fats, not about the abstract noun "intake"; the object is unwrapped to the
# ``of``-phrase, coordination-expanded, exactly as the ``risk`` special case already does. ``risk``
# is handled separately because it additionally *promotes* the predicate (decreases → reduces_risk).
MEASURE_NOUNS = {
    "intake", "consumption", "ingestion", "concentration", "level", "amount",
    "abundance", "production", "quantity", "dose", "dosage", "number",
}

# spaCy part-of-speech tags a predicate token may carry (a verb, or an auxiliary standing in for
# an elided verb).
_VERBAL_POS = {"VERB", "AUX"}

# Direction words an object noun may carry as an ``amod`` — the signal that a bare association is an
# association *with a reduction* / *with an increase* ("associated with **reduced** CRP", "**lower**
# risk"). Matched on the spaCy lemma, so inflected/participial forms collapse (reduced/reduces/reducing
# → ``reduce``; lower/lowered → ``lower``/``low``). Feeds :meth:`SentenceParse.direction_of`, which the
# parse binder turns into the directional association predicate via :func:`.relations.rule_for_verb`.
_REDUCED_LEMMAS = {
    "reduce", "reduced", "low", "lower", "decrease", "decreased", "diminish", "diminished",
    "decline", "declined", "lessen", "less", "few", "fewer", "small", "smaller",
}
_INCREASED_LEMMAS = {
    "increase", "increased", "high", "higher", "elevate", "elevated", "great", "greater",
    "raise", "raised", "more", "large", "larger",
}


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

    def subject_mention_for_token(self, tok) -> "Optional[EntityMention]":
        """Resolve a **subject** token to a mention, descending only through *attributive* modifiers
        (:data:`_SUBJECT_DESCENT_DEPS` — amod/compound/conj/nummod), never into a prepositional or
        clausal phrase (Phase 4a).

        This is the deliberately stricter sibling of :meth:`mention_for_token`. A subject head that
        is not itself an entity should bind to an entity that *modifies* it ("sulfur-containing" in
        "sulfur-containing products"), but must **not** reach into a restrictive PP under it: the
        subject of "Nutritional care **in IBD patients**" is the non-entity "care", not the nested
        "IBD", and the subject of "an imbalance **in the consumption of omega-3**" is "imbalance",
        not "omega-3". Descending into those PPs is exactly how the binder used to fabricate a
        subject from an adjacent entity; restricting the descent is what lets such a subject correctly
        resolve to *nothing* (→ the relation is dropped, not mis-bound).

        **Normalization gate on descent (drop-guard).** A descent-reached candidate is only accepted
        when it **normalized** to a concept (``concept_id is not None``). The descent is an unguarded
        recall heuristic — it recovers a real subject when the head noun is a measure/container and
        the entity modifies it ("High **vegetable** intake lowered the risk…" → *vegetable*), but the
        same descent otherwise adopts whatever modifier the parser attached to the head, including a
        *false* entity that never resolved to a concept. That is how "early-life **diet** influences…
        increased consumption of vegetables" fabricated *early-life* (a non-normalizing scispaCy
        DISEASE false positive on the ``amod`` of *diet*) as the subject. Requiring the descended
        mention to carry a concept keeps exactly the useful case (the modifier is a known concept, so
        a claim can be built) and discards exactly the fabrication (the modifier is not a concept, so
        no claim could ever be built anyway) — and, because an overt non-entity subject then resolves
        to *nothing*, the caller's ``block_fallback`` path suppresses the flat adjacency binder rather
        than letting it re-fabricate. The **exact-anchor** path (the token *is* the entity's head) is
        never gated: an unnormalized head-anchored subject is still returned and retained as an
        observation, unchanged. This gate is specific to the entity reached by *descent*."""
        exact = self._anchor_to_mention.get(tok.i)
        if exact is not None:
            return exact
        best: "Optional[EntityMention]" = None
        best_depth = 1 << 30
        stack = [tok]
        while stack:
            cur = stack.pop()
            for c in cur.children:
                if c.dep_ in _SUBJECT_DESCENT_DEPS:
                    cand = self._anchor_to_mention.get(c.i)
                    # Drop-guard: only a descended entity that normalized to a concept may stand in
                    # for a non-entity subject head; a non-normalizing one is treated as absent, so
                    # the subject resolves to nothing and the relation is dropped, not fabricated.
                    if cand is not None and cand.concept_id is not None:
                        d = _depth(c)
                        if d < best_depth:
                            best, best_depth = cand, d
                    stack.append(c)
        return best

    # --- predicate discovery ----------------------------------------------------------

    def roots(self) -> list:
        """The sentence's predicate verbs to bind from — the verbal ROOT tokens (usually one).

        When the syntactic ROOT is **non-verbal** (a copula-headed adjective or noun, e.g.
        *"This finding is exciting, … their absence has been linked to IBD"* roots at the adjective
        *exciting*), the real predicate verbs are coordinated onto it as ``conj`` children. Those are
        surfaced here so a copula-rooted sentence is not invisible to the parse binder — otherwise
        the binder silently produces nothing and the flat fallback (which cannot tell the true
        grammatical subject from an adjacent entity) takes over. Only consulted when there is no
        verbal ROOT, so a normal verb-rooted sentence is unaffected."""
        verbal = [t for t in self.doc if t.dep_ == "ROOT" and t.pos_ in _VERBAL_POS]
        if verbal:
            return verbal
        surfaced: list = []
        for t in self.doc:
            if t.dep_ == "ROOT" and t.pos_ not in _VERBAL_POS:
                surfaced.extend(
                    c for c in t.children if c.dep_ == "conj" and c.pos_ in _VERBAL_POS
                )
        return surfaced

    def conj_verbs(self, verb) -> list:
        """Verbal ``conj`` children of ``verb`` — coordinated predicates sharing its subject
        ("reduced CRP **but increased** bloating")."""
        return [c for c in verb.children if c.dep_ == "conj" and c.pos_ in _VERBAL_POS]

    def predicate_heads(self) -> list:
        """Every verbal predicate token to bind a relation from (Phase 4a).

        Extends :meth:`roots` beyond the ROOT + its ``conj`` verbs to the verbal predicates nested
        in subordinate clauses (:data:`_SUB_CLAUSE_DEPS` — relative/adverbial/complement clauses),
        reached by descending through those arcs *and* through ``conj`` (so a subordinate clause of a
        coordinated verb is still found). ``conj`` verbs themselves are **not** returned — the binder
        pairs each returned head with its own ``conj_verbs`` and lets them inherit its subject, so
        surfacing them here would drop that inheritance. Deterministically ordered: the verbal
        ROOT(s) first, then subordinate predicates in token order.

        **Participial / reduced-relative predicates** (``acl``/``relcl``) are surfaced even when they
        attach to an *argument noun* the traversal never descends into. A relation is often stated in a
        participle hanging off a noun — *"increased consumption of vegetables, **associated** with
        reduced cancer"* (``associated`` is an ``acl`` of the noun *consumption*, which is only a
        ``dobj``/``nmod`` of the clause verb). The ROOT-seeded walk above follows only subordinate and
        ``conj`` arcs, so it steps over such nouns and misses their participle; a final scan adds every
        remaining verbal ``acl``/``relcl`` predicate so the relation is bound *from structure* rather
        than left to the flat fallback — which the non-entity-subject guard (:func:`.observations.extract`)
        may have suppressed for the sentence. :func:`.relations.rule_for_verb` still filters unmapped
        participles (e.g. *"risk of **developing** cancer"*), and a participle whose antecedent subject
        and object do not resolve simply binds nothing (the binder does not let it block the fallback).

        Discovering these predicates is what lets the guarded fallback (:func:`.observations.extract`)
        recognise that a subordinate-clause predicate's argument did not resolve — and therefore
        *suppress* the adjacency guess — instead of never seeing the predicate at all and falling
        back to it.
        """
        heads: list = list(self.roots())  # verbal ROOT(s) + copula-conj verbs (seeded discovery)
        seen: set[int] = {t.i for t in heads}

        # Traverse from every ROOT and every seed head, following subordinate-clause and ``conj``
        # arcs, adding the verbal predicates found under a subordinate-clause arc.
        start = {t.i: t for t in [x for x in self.doc if x.dep_ == "ROOT"]}
        for h in heads:
            start.setdefault(h.i, h)
        queue = list(start.values())
        queued = set(start)
        while queue:
            cur = queue.pop(0)
            for c in cur.children:
                base = c.dep_.split(":")[0]
                if base in _SUB_CLAUSE_DEPS and c.pos_ in _VERBAL_POS and c.i not in seen:
                    seen.add(c.i)
                    heads.append(c)
                if (base in _SUB_CLAUSE_DEPS or base == "conj") and c.i not in queued:
                    queued.add(c.i)
                    queue.append(c)
        # Final scan: participial / reduced-relative predicates on argument nouns the walk above steps
        # over (see the docstring). Additive and deterministic — only verbal acl/relcl not already found.
        for t in self.doc:
            if t.dep_.split(":")[0] in ("acl", "relcl") and t.pos_ in _VERBAL_POS and t.i not in seen:
                seen.add(t.i)
                heads.append(t)
        heads.sort(key=lambda t: (0 if t.dep_ == "ROOT" else 1, t.i))
        return heads

    def clausal_subject_mentions(self) -> "list[EntityMention]":
        """Entity mentions inside the sentence's **gerund clausal subject** (Phase 4f).

        A sentence can put its agent in a clausal subject — *"**Consuming** 400 mL **of kefir** …
        helps regulate …, improves …"* — where the grammatical subject of the main predicates is the
        gerund clause, not a plain noun. The agent entity ("kefir") sits *inside* that clause, so it
        is found by a permissive subtree search of each ``csubj``/``csubjpass`` gerund (unlike an
        ordinary NP subject, which is resolved restrictively). The result is offered to a predicate
        that elides its own subject as a **shared last-resort** subject, so *"…, improves abdominal
        pain …"* binds *kefir → improves → abdominal pain* instead of the flat binder's mis-paired
        guess. Returns the mentions in token order; empty when the sentence has no such clause."""
        out: "list[EntityMention]" = []
        seen: set[str] = set()
        for t in self.doc:
            if t.dep_ in {"csubj", "csubjpass"} and t.pos_ in _VERBAL_POS:
                m = self.mention_for_token(t)  # permissive: the agent is inside the clause
                if m is not None and m.mention_id not in seen:
                    seen.add(m.mention_id)
                    out.append(m)
        return out

    def controller_tokens(self, verb) -> list:
        """Controller of a subordinate predicate whose subject is elided (Phase 4a).

        For *"require enteral nutrition … **to prevent** dehydration"* the subject of ``prevent`` is
        not spelled out; its controller is an argument of the governing clause (object control here:
        *enteral nutrition*; subject control elsewhere: *"Nutritional care … focusing on
        **preventing** …"* → *Nutritional care*). Climbs the ``head`` chain (bounded) until a
        governor exposes subject/object arguments and returns them, coordination-expanded. Empty when
        no governing argument is found. The caller resolves these to entities and decides: a resolved
        controller becomes the subject (recall); an *unresolved nominal* controller means the true
        subject is a non-entity, so the relation is dropped rather than fabricated."""
        node = verb
        for _ in range(6):  # bounded climb, guards against cycles / runaway
            gov = node.head
            if gov.i == node.i:
                break
            cands: list = []
            for c in gov.children:
                if c.dep_ in _SUBJECT_DEPS and not self._is_relative_pronoun(c):
                    cands.extend(self.coordinate(c))
            for c in gov.children:
                if c.dep_ in _OBJECT_DEPS and c.i != node.i:
                    cands.extend(self.coordinate(c))
            if cands:
                return cands
            node = gov
        return []

    def _is_relative_clause(self, verb) -> bool:
        return verb.dep_.split(":")[0] in {"acl", "relcl"}

    def _is_relative_pronoun(self, tok) -> bool:
        return tok.tag_ in {"WDT", "WP", "WP$"} or tok.lemma_.lower() in _REL_PRONOUNS

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
        Empty when the verb elides its subject (the caller supplies an inherited one).

        In a relative clause the grammatical subject is the relative pronoun ("products **that**
        increase X"); its real referent is the antecedent noun the clause modifies (``verb.head``),
        so a relative-pronoun subject is resolved to that antecedent (Phase 4a). Without this a
        relative-clause relation would bind no subject and be dropped.

        A **reduced relative / participle** ("consumption **associated** with reduced cancer") spells
        out no subject at all — the antecedent noun the participle modifies (``verb.head``) *is* the
        subject. So when a relative/participial clause exposes no subject arc, the antecedent is used
        directly, letting the participial predicate bind instead of being dropped for want of a
        subject. (A resolved-nothing antecedent still binds nothing — the binder does not let a
        participle whose subject is a non-entity suppress the flat fallback.)"""
        rel = self._is_relative_clause(verb)
        out: list = []
        for c in verb.children:
            if c.dep_ in _SUBJECT_DEPS:
                if rel and self._is_relative_pronoun(c):
                    out.append(verb.head)  # the antecedent, not the pronoun
                else:
                    out.extend(self.coordinate(c))
        if not out and rel:
            out.append(verb.head)  # reduced relative / participle: antecedent is the subject
        return out

    def participle_subject_tokens(self, verb) -> list:
        """Implicit subject of a free-adjunct **participle** (``advcl`` + ``VBG``/``VBN``), or ``[]``.

        A result/manifestation participle attached to a clause spells out no subject of its own, yet
        it predicates over what the governing clause just introduced — its **object**, not its subject:

        * present participle (``VBG``): *"inflammation induces dysbiosis, **decreasing** Firmicutes …"*
          → *dysbiosis* decreases Firmicutes;
        * past participle (``VBN``): *"inflammation induces dysbiosis, **characterized** by alterations
          …"* → *dysbiosis* is characterized by those alterations.

        In both the implicit subject is the **governing verb's object** (here *dysbiosis*), falling
        back to the governing verb's subject only when that clause is intransitive (so a true
        subject-controlled adjunct — *"When **treated** with X, patients improved"* — still resolves).
        This is what lets *"…, decreasing/characterized …"* bind to *dysbiosis* and nest beneath
        *inflammation → causes → dysbiosis* (see the hierarchical-relations concept), rather than
        being dropped for want of a subject **or** mis-bound to the governing *subject* by the generic
        controller fallback (which, for *"induces dysbiosis, characterized …"*, would otherwise return
        both *inflammation* and *dysbiosis* and fabricate the *inflammation* reading).

        Deliberately narrow so it cannot fabricate subjects elsewhere: it fires **only** for a ``VBG``/
        ``VBN`` participle on an ``advcl`` arc that exposes **no** overt subject of its own
        (:meth:`subject_tokens` already handles ``acl``/``relcl`` antecedents and overt subjects). A
        finite adverbial clause ("…, while it reduces X") carries its own subject and is left
        untouched. Like the ``acl``/``relcl`` antecedent case, an unresolved result is simply no
        subject — it never suppresses the flat fallback."""
        if verb.dep_.split(":")[0] != "advcl" or verb.tag_ not in ("VBG", "VBN"):
            return []
        if any(c.dep_ in _SUBJECT_DEPS for c in verb.children):
            return []
        head = verb.head
        objs = self.object_tokens(head)
        if objs:
            return objs
        return self.subject_tokens(head)

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
                out.extend(self._coordinate_list(c))
        return out

    def _coordinate_list(self, tok) -> list:
        """:meth:`coordinate` plus apposition **list items**.

        spaCy frequently labels the *middle* element of a comma list "A, B, and C" as ``appos`` of A
        while only the final element gets ``conj`` (e.g. "decreasing Firmicutes, the Bifidobacterium
        genus, and Faecalibacterium prausnitzii" parses *genus* as ``appos`` of *Firmicutes*). Plain
        :meth:`coordinate` follows only ``conj`` and so drops B. When ``tok`` already heads a ``conj``
        it *is* a coordinated list, so its ``appos`` children are list items too and are expanded
        alongside the conjuncts. The ``conj`` gate keeps a lone apposition ("CRP, **a marker of
        inflammation**") from being mistaken for coordination — only an object that is demonstrably a
        list reaches into its appositives. Scoped to object binding (:meth:`object_tokens`); subject
        coordination stays strict."""
        out = self.coordinate(tok)
        if any(c.dep_ == "conj" for c in tok.children):
            for c in tok.children:
                if c.dep_ == "appos":
                    out.extend(self.coordinate(c))
        return out

    def measure_objects(self, obj_tok) -> list:
        """Endpoints inside a measure/container noun's **content genitive**, coordination-expanded.

        Covers both the ``risk``-object special case ("reduced the **risk of** cancer and
        osteoporosis") and the general measure nouns (:data:`MEASURE_NOUNS` — "reduce the **intake
        of** red meat and saturated fats"). Only ``of``/``for`` phrases (:data:`_CONTENT_GENITIVE_PREPS`)
        are taken; a locative/condition phrase on the same noun ("concentration of H2S **in the
        intestine**") is intentionally excluded so it flows to the qualifier extractor rather than
        being mistaken for a second endpoint (Phase 4b)."""
        out: list = []
        for c in obj_tok.children:
            if c.dep_ in _PREP_PHRASE_DEPS and self._prep_surface(c) in _CONTENT_GENITIVE_PREPS:
                out.extend(self.coordinate(c))
        return out

    def descriptive_object_span(self, obj_tok) -> "Optional[tuple[int, int]]":
        """Absolute span of a descriptive object's *whole head-noun phrase*, or ``None`` (Phase 15).

        A descriptive relation's object is the entire phrase hanging off the cue, not the deep entity
        buried inside it — "characterized by **alterations in the composition and function of the gut
        microbiota**" is *about* the alterations, and *gut microbiota* is merely a modifier the
        general object resolver would wrongly descend to. When ``obj_tok`` is **not** an entity anchor
        yet dominates one (so an entity-seeking descent would skip past the true head noun), this
        returns the full span of ``obj_tok``'s subtree — the phrase from the head noun *alterations …*
        to the end of the clause — which :mod:`.observations` makes the relation's object verbatim.
        Returns ``None`` when ``obj_tok`` is itself the entity (no deeper descriptor to preserve — the
        entity *is* the object) or dominates none."""
        if obj_tok.i in self._anchor_to_mention:
            return None
        if self.mention_for_token(obj_tok) is None:
            return None
        # Exclude the leading preposition (the ``case``/``mark`` child, e.g. the "by" of "by
        # alterations …") so the recorded phrase starts at the head noun. Internal case markers
        # ("of"/"in") lie between the boundary tokens, so dropping them does not move the span.
        toks = [t for t in obj_tok.subtree if t.dep_ not in ("case", "mark")]
        if not toks:
            return None
        rel_start = min(t.idx for t in toks)
        rel_end = max(t.idx + len(t.text) for t in toks)
        return self.base + rel_start, self.base + rel_end

    def direction_of(self, obj_tok) -> "Optional[str]":
        """The direction an object noun's ``amod`` expresses — ``"reduced"``/``"increased"``/``None``.

        A bare association whose object is modified by a direction word ("associated with **reduced**
        CRP", "**lower** risk") is a directional association; this reads that word off the object's
        attributive-adjective children (matched on lemma, :data:`_REDUCED_LEMMAS`/:data:`_INCREASED_LEMMAS`).
        First match in token order wins; a noun with no such modifier returns ``None`` (a plain
        association). Used by the parse binder to promote the predicate via
        :func:`.relations.rule_for_verb`."""
        for c in obj_tok.children:
            if c.dep_ != "amod":
                continue
            lemma = c.lemma_.lower()
            if lemma in _REDUCED_LEMMAS:
                return "reduced"
            if lemma in _INCREASED_LEMMAS:
                return "increased"
        return None

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
