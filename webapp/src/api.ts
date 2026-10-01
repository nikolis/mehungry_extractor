// Typed client over the /observe/* surface. Relative URLs so it works both under the Vite dev
// proxy (localhost:5173 → :8000) and when served by FastAPI itself at /app.

import type {
  Canonical,
  Claim,
  Deconstruction,
  DocumentSummary,
  Facts,
  Mention,
  Observation,
  OpenRelation,
  Provenance,
  StageRunResult,
  SynthesizeResult,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  // reads
  documents: () => request<DocumentSummary[]>("/observe/documents"),
  canonical: (pmid: string) => request<Canonical>(`/observe/document/${pmid}`),
  entities: (pmid: string) => request<Mention[]>(`/observe/entities/${pmid}`),
  observations: (pmid: string) => request<Observation[]>(`/observe/observations/${pmid}`),
  claims: (pmid: string) => request<Claim[]>(`/observe/claims/${pmid}`),
  deconstruct: (pmid: string) => request<Deconstruction>(`/observe/deconstruct/${pmid}`),
  facts: (pmid: string) => request<Facts>(`/observe/facts/${pmid}`),
  openRelations: (pmid: string) => request<OpenRelation[]>(`/observe/open-relations/${pmid}`),
  provenance: (claimId: string) => request<Provenance>(`/observe/provenance/claim/${claimId}`),

  // live stage runs
  runIngest: (pmid: string, force = false) =>
    request<StageRunResult>("/observe/ingest", {
      method: "POST",
      body: JSON.stringify({ pmid, force }),
    }),
  runAnalyze: (pmid: string, useModel = true) =>
    request<StageRunResult>("/observe/analyze", {
      method: "POST",
      body: JSON.stringify({ pmid, use_model: useModel }),
    }),
  runExtract: (pmid: string, useModel = true) =>
    request<StageRunResult>("/observe/extract", {
      method: "POST",
      body: JSON.stringify({ pmid, use_model: useModel }),
    }),
  synthesize: (pmids: string[]) =>
    request<SynthesizeResult>("/observe/synthesize", {
      method: "POST",
      body: JSON.stringify({ pmids }),
    }),
};
