import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiResourceState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * Fetches a resource on mount and exposes loading/error state plus a
 * manual refresh function. Used for the initial REST fetch of every
 * dashboard card; WebSocket events update state separately.
 */
export function useApiResource<T>(fetcher: () => Promise<T>, intervalMs?: number): ApiResourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    fetcherRef
      .current()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load data.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const cancel = load();
    if (!intervalMs) {
      return cancel;
    }
    const interval = setInterval(load, intervalMs);
    return () => {
      cancel();
      clearInterval(interval);
    };
  }, [load, intervalMs]);

  return { data, loading, error, refresh: load };
}
