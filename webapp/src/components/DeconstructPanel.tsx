import { useMemo, useState } from "react";
import { api } from "../api";
import type { DeconSentence, Deconstruction, ParseToken, PredicateHead } from "../types";
import { AsyncView, Tag, useAsync } from "./ui";

function arg(a: { surface: string; concept_id: string | null; status: string } | null) {
  if (!a) return <span className="muted">unresolved</span>;
  return (
    <span>
      {a.surface}
      {a.concept_id ? <span className="muted"> → {a.concept_id}</span> : <Tag kind={a.status}>{a.status}</Tag>}
    </span>
  );
}

function PredicateHeads({ heads }: { heads: PredicateHead[] }) {
  if (!heads.length) return <div className="muted small">No verbal predicate head found (left to the flat binder).</div>;
  return (
    <div className="decon-sub">
      <div className="decon-label">Predicate heads &amp; selection</div>
      {heads.map((h, i) => (
        <div key={i} className="phead">
          <div className="phead-top">
            <code className="verb">{h.verb}</code>
            <span className="muted small">lemma «{h.lemma}», dep {h.dep}</span>
            {h.rule ? (
              <Tag kind="pred">{h.rule.predicate}</Tag>
            ) : (
              <Tag kind="unmatched">no rule</Tag>
            )}
            {h.negated && <Tag kind="pol-negative">negated</Tag>}
            {h.nested && <Tag kind="normalized">↳ nested</Tag>}
            {h.object_is_risk && <Tag kind="type">risk→promoted</Tag>}
            {h.object_direction && <Tag kind="type">dir: {h.object_direction}</Tag>}
            {h.is_participial && <Tag>participial</Tag>}
          </div>
          <div className="phead-args small">
            <div>
              <span className="k">subj</span>
              {h.subjects.length ? h.subjects.map((s, j) => <span key={j} className="argchip">{arg(s)}</span>) : <span className="muted">—</span>}
            </div>
            <div>
              <span className="k">obj</span>
              {h.objects.length ? h.objects.map((o, j) => <span key={j} className="argchip">{arg(o)}</span>) : <span className="muted">—</span>}
            </div>
            {h.conditions.length > 0 && (
              <div>
                <span className="k">cond</span>
                {h.conditions.map((c, j) => (
                  <span key={j} className="argchip">{c}</span>
                ))}
              </div>
            )}
          </div>
          <div className={`phead-note small ${h.rule && !h.note.includes("dropped") ? "ok" : "muted"}`}>↳ {h.note}</div>
        </div>
      ))}
    </div>
  );
}

function DepTree({ tokens }: { tokens: ParseToken[] }) {
  return (
    <details className="deptree">
      <summary>Dependency parse ({tokens.length} tokens)</summary>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Token</th>
              <th>POS</th>
              <th>Dep</th>
              <th>Head</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((t) => (
              <tr key={t.i}>
                <td className="mono small">{t.i}</td>
                <td>
                  {t.text} <span className="muted small">«{t.lemma}»</span>
                </td>
                <td className="small">{t.pos}</td>
                <td className="small">
                  <Tag>{t.dep}</Tag>
                </td>
                <td className="small">
                  {t.head === t.i ? <span className="muted">ROOT</span> : tokens[t.head]?.text ?? t.head}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

type DeconObservation = DeconSentence["observations"][number];

function ObsRow({ o }: { o: DeconObservation }) {
  return (
    <div className="obs-row small">
      <b>{o.subject_text}</b> <Tag kind="pred">{o.predicate}</Tag> <b>{o.object_text}</b>{" "}
      <Tag kind={`pol-${o.polarity}`}>{o.polarity}</Tag> <span className="muted">{o.certainty}</span>
      {o.context && <span className="muted"> · {o.context}</span>}
      {o.qualifiers?.length > 0 && (
        <div className="obs-quals">
          {o.qualifiers.map((q, i) => (
            <span key={i} className="qual-chip" title={q.qualifier_type}>
              <span className="qual-type">{q.qualifier_type}</span>
              {q.value_concept_id ?? q.value_text}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ObsNode({
  node,
  childrenOf,
}: {
  node: DeconObservation;
  childrenOf: Map<string, DeconObservation[]>;
}) {
  const kids = childrenOf.get(node.observation_id) ?? [];
  return (
    <li className="obs-node">
      <ObsRow o={node} />
      {kids.length > 0 && (
        <ul className="obs-children">
          {kids.map((k) => (
            <ObsNode key={k.observation_id} node={k} childrenOf={childrenOf} />
          ))}
        </ul>
      )}
    </li>
  );
}

// Rebuild the parent→children tree from the flat `parent_observation_id` pointers the engine
// emits (Concept 20). Roots are observations with no parent — or whose parent is not in this
// sentence (defensive) — rendered in document order; the rest nest beneath their parent.
function ObservationTree({ observations }: { observations: DeconObservation[] }) {
  const { roots, childrenOf, nestedCount } = useMemo(() => {
    const ids = new Set(observations.map((o) => o.observation_id));
    const childrenOf = new Map<string, DeconObservation[]>();
    const roots: DeconObservation[] = [];
    let nestedCount = 0;
    for (const o of observations) {
      const pid = o.parent_observation_id;
      if (pid && ids.has(pid)) {
        const arr = childrenOf.get(pid) ?? [];
        arr.push(o);
        childrenOf.set(pid, arr);
        nestedCount += 1;
      } else {
        roots.push(o);
      }
    }
    return { roots, childrenOf, nestedCount };
  }, [observations]);

  return (
    <div className="decon-sub">
      <div className="decon-label">
        Observations emitted
        {nestedCount > 0 && (
          <span className="muted"> · {nestedCount} nested under a parent relation</span>
        )}
      </div>
      {observations.length ? (
        <ul className="obs-tree">
          {roots.map((r) => (
            <ObsNode key={r.observation_id} node={r} childrenOf={childrenOf} />
          ))}
        </ul>
      ) : (
        <span className="muted small">None — no (subject, predicate, object) bound for this sentence.</span>
      )}
    </div>
  );
}

function SentenceCard({ s }: { s: DeconSentence }) {
  const [open, setOpen] = useState(false);
  const dropped = (s.parse?.predicate_heads ?? []).some((h) => h.note.includes("dropped"));
  return (
    <div className="decon-card">
      <div className="decon-head" onClick={() => setOpen((o) => !o)}>
        <span className="chev">{open ? "▾" : "▸"}</span>
        <span className="decon-text">{s.text}</span>
        <span className="decon-badges">
          <Tag kind="type">{s.mentions.length} ent</Tag>
          {s.observations.length > 0 && <Tag kind="normalized">{s.observations.length} obs</Tag>}
          {s.observations.length === 0 && dropped && <Tag kind="ambiguous">dropped</Tag>}
        </span>
      </div>
      {open && (
        <div className="decon-body">
          <div className="decon-sub">
            <div className="decon-label">Mentions</div>
            {s.mentions.map((m, i) => (
              <span key={i} className="argchip">
                {m.surface}
                {m.concept_id ? <span className="muted"> → {m.concept_id}</span> : <Tag kind={m.status}>{m.status}</Tag>}
              </span>
            ))}
          </div>

          <div className="decon-sub">
            <div className="decon-label">Clause segmentation (flat binder)</div>
            {s.clauses.map((c, i) => (
              <div key={i} className="clause-row small">
                {c.marker && <Tag kind={c.contrastive ? "pol-negative" : "default"}>{c.marker}</Tag>} {c.text}
              </div>
            ))}
          </div>

          {s.parse ? (
            <>
              <PredicateHeads heads={s.parse.predicate_heads} />
              {s.parse.clausal_subjects.length > 0 && (
                <div className="decon-sub small">
                  <span className="decon-label">Clausal subjects:</span>{" "}
                  {s.parse.clausal_subjects.join(", ")}
                </div>
              )}
              <DepTree tokens={s.parse.tokens} />
            </>
          ) : (
            <div className="muted small">Parse unavailable (scispaCy model not loaded) — only the flat binder path applies.</div>
          )}

          <ObservationTree observations={s.observations} />
        </div>
      )}
    </div>
  );
}

type Filter = "obs" | "drops" | "all";

export function DeconstructPanel({ pmid }: { pmid: string }) {
  const state = useAsync<Deconstruction>(() => api.deconstruct(pmid), [pmid]);
  const [filter, setFilter] = useState<Filter>("obs");

  const shown = useMemo(() => {
    const sents = state.data?.sentences ?? [];
    if (filter === "all") return sents;
    if (filter === "obs") return sents.filter((s) => s.observations.length > 0);
    return sents.filter(
      (s) => s.observations.length === 0 && (s.parse?.predicate_heads ?? []).some((h) => h.note.includes("dropped")),
    );
  }, [state.data, filter]);

  return (
    <AsyncView state={state} empty="Run the Analyze stage first (needs entity mentions to deconstruct).">
      {(d) => (
        <div>
          <p className="muted small">
            How each sentence becomes observations: clause segmentation, the dependency parse,
            predicate-head discovery, predicate selection (verb → rule, with risk/direction
            promotions), and argument binding. {d.deconstructed_count} of {d.sentence_count} sentences
            are candidates (≥2 mentions or produced an observation).
            {!d.parse_available && " Parser unavailable — flat binder only."}
          </p>
          <div className="toolbar">
            <label>
              Show:&nbsp;
              <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
                <option value="obs">sentences that produced observations</option>
                <option value="drops">dropped (bound nothing)</option>
                <option value="all">all candidate sentences</option>
              </select>
            </label>
            <span className="muted small">&nbsp;· {shown.length} shown</span>
          </div>
          {shown.map((s) => (
            <SentenceCard key={s.sentence_id} s={s} />
          ))}
          {!shown.length && <div className="muted pad">Nothing matches this filter.</div>}
        </div>
      )}
    </AsyncView>
  );
}
