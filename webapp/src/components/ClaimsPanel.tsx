import { useState } from "react";
import { api } from "../api";
import type { Claim, Provenance } from "../types";
import { AsyncView, Tag, useAsync } from "./ui";

function ProvenanceDrawer({ claimId, onClose }: { claimId: string; onClose: () => void }) {
  const state = useAsync<Provenance>(() => api.provenance(claimId), [claimId]);
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h3>Provenance</h3>
          <button onClick={onClose}>✕</button>
        </div>
        <AsyncView state={state}>
          {(p) => (
            <div className="drawer-body">
              <code className="mono small">{claimId}</code>
              <h4>Document</h4>
              <div className="small">
                {String(p.document?.title ?? "—")}
                <div className="muted mono">
                  {String(p.document?.document_id ?? "")} · {String(p.document?.source_type ?? "")}
                </div>
              </div>

              <h4>Evidence spans (self-verifying)</h4>
              {p.evidence.map((e, i) => {
                const hasRecon = e.reconstructed_text != null;
                const ok = e.reconstructed_text === e.quoted_text;
                return (
                  <div key={i} className="prov-ev">
                    <div className="quote">“{e.quoted_text}”</div>
                    <div className="muted mono small">
                      {e.sentence_id} · [{e.start_char}:{e.end_char}] · {e.precision} ·{" "}
                      {e.extraction_rule}@{e.extraction_rule_version}
                    </div>
                    {hasRecon && (
                      <div className={`small ${ok ? "ok" : "error"}`}>
                        {ok ? "✓ reconstructs from canonical text" : "✗ does not reconstruct"}
                      </div>
                    )}
                  </div>
                );
              })}

              <h4>Run</h4>
              <pre className="mono small json">{JSON.stringify(p.run, null, 2)}</pre>
              <h4>Assessments</h4>
              <pre className="mono small json">{JSON.stringify(p.assessments, null, 2)}</pre>
            </div>
          )}
        </AsyncView>
      </div>
    </div>
  );
}

export function ClaimsPanel({ pmid }: { pmid: string }) {
  const state = useAsync<Claim[]>(() => api.claims(pmid), [pmid]);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <AsyncView state={state} empty="No claims yet — run the Extract stage.">
      {(claims) => (
        <div>
          <p className="muted small">
            The concept layer: observations agreeing on (subject, predicate, object, polarity,
            certainty, conditions) folded into one claim. Click a row for full provenance.
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>Predicate</th>
                  <th>Object</th>
                  <th>Modality</th>
                  <th>Conditions</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {claims.map((c) => (
                  <tr key={c.claim_id} className="clickable" onClick={() => setOpen(c.claim_id)}>
                    <td>{c.subject_name}</td>
                    <td>
                      <Tag kind="pred">{c.predicate}</Tag>
                    </td>
                    <td>{c.object_name}</td>
                    <td className="small">
                      <Tag kind={`pol-${c.polarity}`}>{c.polarity}</Tag> {c.certainty}
                    </td>
                    <td className="small">
                      {c.qualifiers.length
                        ? c.qualifiers.map((q, i) => (
                            <div key={i}>
                              {q.qualifier_type}: {q.value_concept_id ?? q.value_text}
                            </div>
                          ))
                        : <span className="muted">—</span>}
                    </td>
                    <td className="muted small">provenance →</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {open && <ProvenanceDrawer claimId={open} onClose={() => setOpen(null)} />}
        </div>
      )}
    </AsyncView>
  );
}
