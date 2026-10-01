import { api } from "../api";
import type { Facts } from "../types";
import { AsyncView, Tag, useAsync } from "./ui";

export function FactsPanel({ pmid }: { pmid: string }) {
  const state = useAsync<Facts>(() => api.facts(pmid), [pmid]);
  return (
    <AsyncView state={state} empty="No facts yet — run the Extract stage.">
      {(f) => (
        <div className="facts">
          <section>
            <h4>Study characteristics</h4>
            {f.study_characteristics.length ? (
              <table>
                <tbody>
                  {f.study_characteristics.map((s) => (
                    <tr key={s.field}>
                      <th>{s.field}</th>
                      <td>{s.value}</td>
                      <td className="muted small">{s.classification_source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">None.</p>
            )}
          </section>

          <section>
            <h4>Funding</h4>
            {f.funding.length ? (
              <table>
                <tbody>
                  {f.funding.map((fu, i) => (
                    <tr key={i}>
                      <td>{fu.funder}</td>
                      <td>
                        <Tag kind="type">{fu.funder_type}</Tag>
                      </td>
                      <td className="muted small">{fu.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">No funding statements found.</p>
            )}
          </section>

          <section>
            <h4>Assessment ({f.assessments[0]?.framework_id ?? "—"})</h4>
            {f.assessments.length ? (
              <table>
                <tbody>
                  {f.assessments.map((a) => (
                    <tr key={a.criterion}>
                      <th>{a.criterion}</th>
                      <td>
                        <Tag kind="grade">{a.value}</Tag>
                      </td>
                      <td className="muted small">{a.rationale}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">None.</p>
            )}
          </section>

          <section>
            <h4>Authors &amp; affiliations</h4>
            {f.affiliations.length ? (
              <ul className="authors">
                {f.affiliations.map((a, i) => (
                  <li key={i}>
                    <b>{a.name}</b>
                    {a.affiliations.map((af, j) => (
                      <div key={j} className="muted small">
                        {af.institution_name ?? af.raw_text}
                        {af.institution_type ? ` · ${af.institution_type}` : ""}
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">None.</p>
            )}
          </section>
        </div>
      )}
    </AsyncView>
  );
}
