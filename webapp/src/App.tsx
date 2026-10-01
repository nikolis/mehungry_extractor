import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { DocumentSummary, StageRunResult } from "./types";
import { BatchPanel } from "./components/BatchPanel";
import { CanonicalPanel } from "./components/CanonicalPanel";
import { ClaimsPanel } from "./components/ClaimsPanel";
import { DeconstructPanel } from "./components/DeconstructPanel";
import { EntitiesPanel } from "./components/EntitiesPanel";
import { FactsPanel } from "./components/FactsPanel";
import { ObservationsPanel } from "./components/ObservationsPanel";
import { OpenRelationsPanel } from "./components/OpenRelationsPanel";

type Tab = "canonical" | "entities" | "deconstruct" | "observations" | "claims" | "facts" | "openrel" | "batch";
const PAPER_TABS: { id: Tab; label: string }[] = [
  { id: "canonical", label: "Canonical" },
  { id: "entities", label: "Entities" },
  { id: "deconstruct", label: "Sentence deconstruction" },
  { id: "observations", label: "Observations" },
  { id: "claims", label: "Claims" },
  { id: "facts", label: "Facts & assessment" },
  { id: "openrel", label: "Open relations" },
];

interface LogEntry {
  stage: string;
  ok: boolean;
  detail: string;
}

export function App() {
  const [pmidInput, setPmidInput] = useState("");
  const [activePmid, setActivePmid] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("canonical");
  const [refreshKey, setRefreshKey] = useState(0);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [filter, setFilter] = useState("");

  async function loadDocs() {
    try {
      setDocs(await api.documents());
    } catch {
      /* API may not be up yet */
    }
  }
  useEffect(() => {
    loadDocs();
  }, [refreshKey]);

  function addLog(stage: string, ok: boolean, detail: string) {
    setLog((l) => [{ stage, ok, detail }, ...l].slice(0, 30));
  }

  function selectPaper(pmid: string) {
    setActivePmid(pmid);
    setPmidInput(pmid);
    setTab("canonical");
    setRefreshKey((k) => k + 1);
  }

  async function runStage(stage: "ingest" | "analyze" | "extract", pmid: string): Promise<StageRunResult> {
    const fn =
      stage === "ingest" ? api.runIngest : stage === "analyze" ? api.runAnalyze : api.runExtract;
    const res = await fn(pmid);
    const detail = summarizeRun(stage, res);
    addLog(stage, res.status === "ok", detail);
    return res;
  }

  async function handleRun(stage: "ingest" | "analyze" | "extract" | "all") {
    const pmid = pmidInput.trim();
    if (!pmid) return;
    setBusy(stage);
    try {
      if (stage === "all") {
        const ing = await runStage("ingest", pmid);
        if (ing.status === "ok") {
          await runStage("analyze", pmid);
          await runStage("extract", pmid);
        }
      } else {
        await runStage(stage, pmid);
      }
      setActivePmid(pmid);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      addLog(stage, false, String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  }

  const shownDocs = useMemo(() => {
    const q = filter.toLowerCase();
    return docs.filter(
      (d) => !q || d.pmid.includes(q) || (d.title ?? "").toLowerCase().includes(q),
    );
  }, [docs, filter]);

  const paperKey = `${activePmid}:${refreshKey}`;

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>
          mehungry<span className="muted"> · stage observer</span>
        </h1>

        <div className="run-box">
          <input
            value={pmidInput}
            onChange={(e) => setPmidInput(e.target.value)}
            placeholder="PMID e.g. 40422571"
            onKeyDown={(e) => e.key === "Enter" && activePmid !== pmidInput.trim() && selectPaper(pmidInput.trim())}
          />
          <div className="run-buttons">
            <button disabled={!!busy} onClick={() => handleRun("ingest")}>
              {busy === "ingest" ? "…" : "Ingest"}
            </button>
            <button disabled={!!busy} onClick={() => handleRun("analyze")}>
              {busy === "analyze" ? "…" : "Analyze"}
            </button>
            <button disabled={!!busy} onClick={() => handleRun("extract")}>
              {busy === "extract" ? "…" : "Extract"}
            </button>
          </div>
          <button className="primary wide" disabled={!!busy} onClick={() => handleRun("all")}>
            {busy === "all" ? "Running all stages…" : "▶ Run all stages"}
          </button>
          <button
            className="wide ghost"
            disabled={!pmidInput.trim()}
            onClick={() => selectPaper(pmidInput.trim())}
          >
            Observe cached (no run)
          </button>
          <p className="muted small">Ingest hits the network once; Analyze &amp; Extract are offline.</p>
        </div>

        {log.length > 0 && (
          <div className="log">
            <h3>Run log</h3>
            {log.map((e, i) => (
              <div key={i} className={`log-row ${e.ok ? "ok" : "bad"}`}>
                <b>{e.stage}</b> {e.detail}
              </div>
            ))}
          </div>
        )}

        <div className="picker">
          <h3>
            Cached papers <span className="count">{docs.length}</span>
          </h3>
          <input
            className="filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter by PMID / title"
          />
          <ul>
            {shownDocs.slice(0, 200).map((d) => (
              <li
                key={d.document_id}
                className={activePmid === d.pmid ? "active" : ""}
                onClick={() => selectPaper(d.pmid)}
              >
                <span className="mono">{d.pmid}</span>
                <span className="title">{d.title ?? d.document_id}</span>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      <main className="content">
        <nav className="tabs">
          {PAPER_TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "active" : ""}
              disabled={!activePmid}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
          <button className={`batch-tab ${tab === "batch" ? "active" : ""}`} onClick={() => setTab("batch")}>
            Batch synthesis
          </button>
        </nav>

        <section className="panel">
          {tab === "batch" ? (
            <BatchPanel initialPmids={activePmid ? [activePmid] : []} />
          ) : !activePmid ? (
            <div className="muted pad">
              Enter a PMID and run a stage, or pick a cached paper on the left.
            </div>
          ) : tab === "canonical" ? (
            <CanonicalPanel key={paperKey} pmid={activePmid} />
          ) : tab === "entities" ? (
            <EntitiesPanel key={paperKey} pmid={activePmid} />
          ) : tab === "deconstruct" ? (
            <DeconstructPanel key={paperKey} pmid={activePmid} />
          ) : tab === "observations" ? (
            <ObservationsPanel key={paperKey} pmid={activePmid} />
          ) : tab === "claims" ? (
            <ClaimsPanel key={paperKey} pmid={activePmid} />
          ) : tab === "facts" ? (
            <FactsPanel key={paperKey} pmid={activePmid} />
          ) : (
            <OpenRelationsPanel key={paperKey} pmid={activePmid} />
          )}
        </section>
      </main>
    </div>
  );
}

function summarizeRun(stage: string, res: StageRunResult): string {
  if (res.status !== "ok") return `${res.status}${res.detail ? ` — ${res.detail}` : ""}`;
  if (stage === "ingest") return `${res.source_type} · ${res.sentences} sentences`;
  if (stage === "analyze") return `${res.mentions} mentions (${res.normalized} normalized)`;
  if (stage === "extract")
    return `${res.observations} obs · ${res.claims} claims · ${res.assessments} assessments`;
  return "ok";
}
