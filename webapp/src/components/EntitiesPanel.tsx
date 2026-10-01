import { useMemo, useState } from "react";
import { api } from "../api";
import type { Mention } from "../types";
import { AsyncView, Tag, useAsync } from "./ui";

export function EntitiesPanel({ pmid }: { pmid: string }) {
  const state = useAsync<Mention[]>(() => api.entities(pmid), [pmid]);
  const [status, setStatus] = useState<string>("all");

  const filtered = useMemo(() => {
    if (!state.data) return [];
    return status === "all" ? state.data : state.data.filter((m) => m.status === status);
  }, [state.data, status]);

  return (
    <AsyncView state={state} empty="No entities yet — run the Analyze (entities) stage.">
      {(all) => (
        <div>
          <div className="toolbar">
            <label>
              Status:&nbsp;
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="all">all ({all.length})</option>
                <option value="normalized">normalized</option>
                <option value="ambiguous">ambiguous</option>
                <option value="unmatched">unmatched</option>
              </select>
            </label>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Surface</th>
                  <th>Type</th>
                  <th>Concept</th>
                  <th>Status</th>
                  <th>Span</th>
                  <th>Modifiers</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => (
                  <tr key={m.mention_id}>
                    <td>{m.surface_text}</td>
                    <td>
                      <Tag kind="type">{m.entity_type}</Tag>
                    </td>
                    <td>{m.concept_id ?? <span className="muted">—</span>}</td>
                    <td>
                      <Tag kind={m.status}>{m.status}</Tag>
                    </td>
                    <td className="mono small">
                      {m.start_char}:{m.end_char}
                    </td>
                    <td className="small">
                      {m.modifiers.length
                        ? m.modifiers.map((mo, i) => (
                            <div key={i}>
                              {mo.relation}: {mo.value_concept_id ?? mo.value_text}
                            </div>
                          ))
                        : <span className="muted">—</span>}
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
