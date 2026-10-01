import { useMemo, useState } from "react";
import { api } from "../api";
import type { Canonical, Mention } from "../types";
import { AsyncView, useAsync } from "./ui";

// Render the canonical text with entity spans highlighted inline. Spans are sorted; any that
// overlap an already-emitted span are skipped (the engine resolves overlaps upstream, but we
// guard so highlighting can never corrupt the text — the offset contract stays visible).
function Highlighted({ text, mentions }: { text: string; mentions: Mention[] }) {
  const nodes = useMemo(() => {
    const spans = [...mentions].sort((a, b) => a.start_char - b.start_char);
    const out: React.ReactNode[] = [];
    let cursor = 0;
    for (const m of spans) {
      if (m.start_char < cursor) continue;
      if (m.start_char > cursor) out.push(text.slice(cursor, m.start_char));
      out.push(
        <mark
          key={m.mention_id}
          className={`ent ent-${m.status}`}
          title={`${m.entity_type}${m.concept_id ? ` → ${m.concept_id}` : ""} (${m.status}) [${m.start_char}:${m.end_char}]`}
        >
          {text.slice(m.start_char, m.end_char)}
        </mark>,
      );
      cursor = m.end_char;
    }
    if (cursor < text.length) out.push(text.slice(cursor));
    return out;
  }, [text, mentions]);
  return <div className="canonical-text">{nodes}</div>;
}

export function CanonicalPanel({ pmid }: { pmid: string }) {
  const [highlight, setHighlight] = useState(true);
  const doc = useAsync<Canonical>(() => api.canonical(pmid), [pmid]);
  const ents = useAsync<Mention[]>(() => api.entities(pmid), [pmid]);

  return (
    <AsyncView state={doc} empty="Not ingested yet. Run the Ingest stage.">
      {(d) => (
        <div>
          <div className="meta-grid">
            <div>
              <span className="k">Title</span>
              {String(d.metadata.title ?? "—")}
            </div>
            <div>
              <span className="k">Journal</span>
              {String(d.metadata.journal ?? "—")}
            </div>
            <div>
              <span className="k">Source</span>
              {d.summary.source_type ?? "—"}
            </div>
            <div>
              <span className="k">Sections</span>
              {d.summary.sections}
            </div>
            <div>
              <span className="k">Sentences</span>
              {d.summary.sentences}
            </div>
            <div>
              <span className="k">Chars</span>
              {d.summary.chars.toLocaleString()}
            </div>
            <div>
              <a href={d.paper_url} target="_blank" rel="noreferrer">
                PubMed ↗
              </a>
            </div>
          </div>
          <label className="toggle">
            <input type="checkbox" checked={highlight} onChange={(e) => setHighlight(e.target.checked)} />
            Highlight entity mentions{ents.data ? ` (${ents.data.length})` : ""}
          </label>
          <p className="muted small">
            One offset-addressable text — every entity, observation, and claim points back into it
            by absolute character span (the offset contract).
          </p>
          {highlight && ents.data ? (
            <Highlighted text={d.text} mentions={ents.data} />
          ) : (
            <div className="canonical-text">{d.text}</div>
          )}
        </div>
      )}
    </AsyncView>
  );
}
