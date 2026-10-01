import { api } from "../api";
import type { OpenRelation } from "../types";
import { AsyncView, useAsync } from "./ui";

export function OpenRelationsPanel({ pmid }: { pmid: string }) {
  const state = useAsync<OpenRelation[]>(() => api.openRelations(pmid), [pmid]);
  return (
    <AsyncView
      state={state}
      empty="No relation-bearing spans persisted. This is the opt-in Phase 11 sandbox; it needs the [openrel] detector and a discovery run — it never feeds claims/synthesis."
    >
      {(rels) => (
        <div>
          <p className="muted small">
            A model flags spans that assert <i>some</i> relationship, recorded verbatim, most-confident
            first. No subject/predicate/object is imposed — fidelity now, structure later.
          </p>
          <ol className="openrel">
            {rels.map((r) => (
              <li key={r.open_observation_id}>
                <span className="score">{r.score.toFixed(3)}</span>
                <span className="quote">{r.text}</span>
                <div className="muted mono small">
                  {r.sentence_id} · [{r.start_char}:{r.end_char}] · {r.detector_name}@{r.detector_version}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </AsyncView>
  );
}
