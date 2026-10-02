import { useMemo, useState } from "react";
import { api } from "../api";
import type { ConceptRecord, VocabResponse } from "../types";
import { AsyncView, Tag, useAsync } from "./ui";

// The /vocab panel: browse every concept the entity recognizer CAN match, and add / edit / remove
// them. Edits persist to the writable overlay and take effect for the next extraction run.

const BLANK = { concept_id: "", canonical_name: "", entity_type: "", surface_forms: "" };

function parseSurfaces(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const piece of raw.split(/[\n,]/)) {
    const s = piece.trim();
    if (s && !seen.has(s.toLowerCase())) {
      seen.add(s.toLowerCase());
      out.push(s);
    }
  }
  return out;
}

export function VocabPanel() {
  const state = useAsync<VocabResponse>(() => api.vocab(), []);
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [form, setForm] = useState(BLANK);
  const [editing, setEditing] = useState<string | null>(null); // concept_id being edited; null = add mode
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function resetForm() {
    setForm(BLANK);
    setEditing(null);
    setErr(null);
  }

  function startEdit(c: ConceptRecord) {
    setEditing(c.concept_id);
    setErr(null);
    setForm({
      concept_id: c.concept_id,
      canonical_name: c.canonical_name,
      entity_type: c.entity_type,
      surface_forms: c.surface_forms.join(", "),
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const concept = {
      concept_id: form.concept_id.trim(),
      canonical_name: form.canonical_name.trim(),
      entity_type: form.entity_type.trim(),
      surface_forms: parseSurfaces(form.surface_forms),
    };
    if (!concept.concept_id || !concept.canonical_name || !concept.entity_type || concept.surface_forms.length === 0) {
      setErr("concept id, name, type and at least one surface form are required");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (editing) await api.vocabReplace(concept);
      else await api.vocabAdd(concept);
      resetForm();
      state.reload();
    } catch (e2) {
      setErr(String((e2 as Error).message ?? e2));
    } finally {
      setBusy(false);
    }
  }

  async function remove(c: ConceptRecord) {
    if (!window.confirm(`Remove ${c.concept_id} (${c.canonical_name})? It will no longer be recognised.`)) return;
    setBusy(true);
    setErr(null);
    try {
      await api.vocabRemove(c.concept_id);
      if (editing === c.concept_id) resetForm();
      state.reload();
    } catch (e2) {
      setErr(String((e2 as Error).message ?? e2));
    } finally {
      setBusy(false);
    }
  }

  const shown = useMemo(() => {
    const data = state.data?.concepts ?? [];
    const needle = q.trim().toLowerCase();
    return data.filter((c) => {
      if (typeFilter !== "all" && c.entity_type !== typeFilter) return false;
      if (!needle) return true;
      return (
        c.concept_id.toLowerCase().includes(needle) ||
        c.canonical_name.toLowerCase().includes(needle) ||
        c.surface_forms.some((s) => s.toLowerCase().includes(needle))
      );
    });
  }, [state.data, q, typeFilter]);

  return (
    <AsyncView state={state} empty="Vocabulary unavailable.">
      {(vocab) => (
        <div className="vocab">
          <div className="vocab-head">
            <div>
              <strong>{vocab.vocabulary}</strong> <span className="mono small">v{vocab.version}</span>
              {vocab.overlay_digest ? (
                <Tag kind="overridden">customized · {vocab.overlay_digest}</Tag>
              ) : (
                <Tag kind="builtin">pristine</Tag>
              )}
              <span className="count">{vocab.concepts.length}</span>
            </div>
            <p className="muted small">
              Every concept the entity recognizer can match. Add, edit, or remove them — changes apply to the
              <b> next extraction</b> (already-extracted papers are not re-processed).
            </p>
          </div>

          {/* add / edit form */}
          <form className="vocab-form" onSubmit={submit}>
            <div className="vocab-form-row">
              <input
                placeholder="concept id (PREFIX:slug, e.g. NUTR:zinc)"
                value={form.concept_id}
                disabled={!!editing}
                onChange={(e) => setForm({ ...form, concept_id: e.target.value })}
              />
              <input
                placeholder="canonical name"
                value={form.canonical_name}
                onChange={(e) => setForm({ ...form, canonical_name: e.target.value })}
              />
              <input
                placeholder="entity type"
                list="vocab-types"
                value={form.entity_type}
                onChange={(e) => setForm({ ...form, entity_type: e.target.value })}
              />
              <datalist id="vocab-types">
                {vocab.entity_types.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </div>
            <textarea
              placeholder="surface forms — one per line or comma-separated (case-insensitive)"
              value={form.surface_forms}
              rows={2}
              onChange={(e) => setForm({ ...form, surface_forms: e.target.value })}
            />
            <div className="vocab-form-actions">
              <button className="primary" type="submit" disabled={busy}>
                {editing ? `Save ${editing}` : "＋ Add concept"}
              </button>
              {editing && (
                <button type="button" className="ghost" onClick={resetForm} disabled={busy}>
                  Cancel edit
                </button>
              )}
              {err && <span className="error small">⚠ {err}</span>}
            </div>
          </form>

          {/* filters */}
          <div className="toolbar">
            <input className="filter" placeholder="search id / name / surface form" value={q} onChange={(e) => setQ(e.target.value)} />
            <label>
              Type:&nbsp;
              <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
                <option value="all">all</option>
                {vocab.entity_types.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted small">{shown.length} shown</span>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Concept</th>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Surface forms</th>
                  <th>Origin</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.concept_id} className={editing === c.concept_id ? "active" : ""}>
                    <td className="mono small">{c.concept_id}</td>
                    <td>{c.canonical_name}</td>
                    <td>
                      <Tag kind="type">{c.entity_type}</Tag>
                    </td>
                    <td className="small">{c.surface_forms.join(", ")}</td>
                    <td>
                      <Tag kind={c.origin}>{c.origin}</Tag>
                    </td>
                    <td className="vocab-actions">
                      <button className="ghost" onClick={() => startEdit(c)} disabled={busy}>
                        Edit
                      </button>
                      <button className="ghost danger" onClick={() => remove(c)} disabled={busy}>
                        Remove
                      </button>
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
