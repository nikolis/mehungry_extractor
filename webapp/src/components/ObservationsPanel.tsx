import { api } from "../api";
import type { Observation } from "../types";
import { AsyncView, Tag, useAsync } from "./ui";

function endpointLabel(text: string, conceptId: string | null, mods: Observation["subject_modifiers"]) {
  const base = conceptId ? `${text} → ${conceptId}` : text;
  const suffix = mods.map((m) => ` ${m.preposition} ${m.value_text}`).join("");
  return base + suffix;
}

export function ObservationsPanel({ pmid }: { pmid: string }) {
  const state = useAsync<Observation[]>(() => api.observations(pmid), [pmid]);
  return (
    <AsyncView state={state} empty="No observations yet — run the Extract stage.">
      {(obs) => (
        <div>
          <p className="muted small">
            The audit layer: one relation found in one sentence, with its modality and the exact rule
            that fired. Kept even when an endpoint did not normalize (concept id absent).
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>Predicate</th>
                  <th>Object</th>
                  <th>Modality</th>
                  <th>Qualifiers</th>
                  <th>Rule</th>
                  <th>How bound</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {obs.map((o) => (
                  <tr key={o.observation_id}>
                    <td>{endpointLabel(o.subject_text, o.subject_concept_id, o.subject_modifiers)}</td>
                    <td>
                      <Tag kind="pred">{o.predicate}</Tag>
                    </td>
                    <td>{endpointLabel(o.object_text, o.object_concept_id, o.object_modifiers)}</td>
                    <td className="small">
                      <Tag kind={`pol-${o.polarity}`}>{o.polarity}</Tag> {o.certainty}
                    </td>
                    <td className="small">
                      {o.qualifiers.length
                        ? o.qualifiers.map((q, i) => (
                            <div key={i}>
                              {q.qualifier_type}: {q.value_concept_id ?? q.value_text}
                            </div>
                          ))
                        : <span className="muted">—</span>}
                    </td>
                    <td className="mono small">
                      {o.rule_id}
                      <br />
                      <span className="muted">v{o.rule_version}</span>
                    </td>
                    <td className="small">{o.context ?? <span className="muted">—</span>}</td>
                    <td className="small quote">
                      {o.evidence_refs[0]?.quoted_text ?? <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </AsyncView>
  );
}
