import { useState } from "react";
import { api } from "../api";
import type { SynthesizeResult } from "../types";
import { Tag } from "./ui";

export function BatchPanel({ initialPmids }: { initialPmids: string[] }) {
  const [text, setText] = useState(initialPmids.join("\n"));
  const [result, setResult] = useState<SynthesizeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    const pmids = text
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!pmids.length) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await api.synthesize(pmids));
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <p className="muted small">
        The batch stages: cohesion drops off-topic outliers (set arithmetic over shared concepts),
        then synthesis groups the on-topic papers' claims into conclusions — conflicts surfaced, never
        averaged away.
      </p>
      <textarea
        className="pmid-area"
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="One or more PMIDs (whitespace/comma separated)"
      />
      <button className="primary" onClick={run} disabled={loading}>
        {loading ? "Running cohesion + synthesis…" : "Run batch synthesis"}
      </button>
      {error && <div className="error pad">⚠ {error}</div>}

      {result && (
        <div className="batch-result">
          <section>
            <h4>Topic core</h4>
            <div className="chips">
              {result.report.topic.core_concepts.map((c) => (
                <span key={c.concept_id} className="chip" title={c.concept_id}>
                  {c.name ?? c.concept_id} <span className="muted">×{c.paper_count}</span>
                </span>
              ))}
              {!result.report.topic.core_concepts.length && <span className="muted">—</span>}
            </div>
          </section>

          <section>
            <h4>
              Papers — included {result.report.included_pmids.length}/{result.report.requested_pmids.length}
            </h4>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>PMID</th>
                    <th>Status</th>
                    <th>Title</th>
                    <th>Claims</th>
                    <th>Cohesion</th>
                  </tr>
                </thead>
                <tbody>
                  {result.report.papers.map((p, i) => (
                    <tr key={i}>
                      <td className="mono">{String(p.pmid)}</td>
                      <td>
                        <Tag kind={String(p.status)}>{String(p.status)}</Tag>
                      </td>
                      <td className="small">{String(p.title ?? p.error ?? "—")}</td>
                      <td>{String(p.n_claims ?? "")}</td>
                      <td className="small">
                        {p.cohesion_score != null ? Number(p.cohesion_score).toFixed(2) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h4>Conclusions ({result.synthesis.conclusions.length})</h4>
            {result.synthesis.conclusions.map((c, i) => (
              <div key={i} className="conclusion">
                <div className="conclusion-head">
                  <b>{c.subject_name}</b> <Tag kind="pred">{c.predicate}</Tag> <b>{c.object_name}</b>
                  <Tag kind={`dir-${c.direction}`}>{c.direction}</Tag>
                  <Tag kind={`clin-${c.clinical_direction}`}>{c.clinical_direction}</Tag>
                  <span className="muted small">
                    {c.paper_count} paper(s): +{c.supporting_papers.length} / −{c.contradicting_papers.length} / ~{c.neutral_papers.length}
                  </span>
                </div>
                {c.evidence[0] && <div className="quote small">“{c.evidence[0].quoted_text}”</div>}
              </div>
            ))}
            {!result.synthesis.conclusions.length && <p className="muted">No shared conclusions.</p>}
          </section>

          {result.synthesis.derived_conclusions.length > 0 && (
            <section>
              <h4>Derived food conclusions ({result.synthesis.derived_conclusions.length})</h4>
              {result.synthesis.derived_conclusions.map((c, i) => (
                <div key={i} className="conclusion">
                  <b>{c.subject_name}</b> <Tag kind="pred">{c.predicate}</Tag> <b>{c.object_name}</b>{" "}
                  <Tag kind={`clin-${c.clinical_direction}`}>{c.clinical_direction}</Tag>
                </div>
              ))}
            </section>
          )}

          {result.report.warnings.length > 0 && (
            <section>
              <h4>Warnings</h4>
              <ul className="muted small">
                {result.report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
