import { useCallback, useEffect, useState } from "react";

// Small async-data hook: tracks loading/error/data and re-runs when `deps` change.
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const run = useCallback(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e.message ?? e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => run(), [run]);
  return { data, error, loading, reload: run };
}

export function AsyncView<T>({
  state,
  empty,
  children,
}: {
  state: { data: T | null; error: string | null; loading: boolean };
  empty?: string;
  children: (data: T) => React.ReactNode;
}) {
  if (state.loading && state.data === null) return <div className="muted pad">Loading…</div>;
  if (state.error) return <div className="error pad">⚠ {state.error}</div>;
  if (state.data === null) return <div className="muted pad">{empty ?? "No data."}</div>;
  if (Array.isArray(state.data) && state.data.length === 0)
    return <div className="muted pad">{empty ?? "Nothing here yet — run the stage first."}</div>;
  return <>{children(state.data)}</>;
}

export function Tag({ kind, children }: { kind?: string; children: React.ReactNode }) {
  return <span className={`tag tag-${kind ?? "default"}`}>{children}</span>;
}

export function Count({ n }: { n: number }) {
  return <span className="count">{n}</span>;
}
