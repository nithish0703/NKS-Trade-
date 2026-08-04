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
  // Tracks whether we've ever received data, via ref (not state) so
  // `load` itself doesn't need to depend on it and can stay a stable
  // callback for the effect below.
  const hasLoadedOnceRef = useRef(false);

  const load = useCallback(() => {
    let cancelled = false;
    // Only show the loading skeleton for the very first fetch. Interval
    // polls and WebSocket-triggered refreshes happen silently in the
    // background so already-rendered data doesn't flash/blink every
    // time a scan cycle completes -- the table swaps to the new data
    // only once it arrives, instead of disappearing behind a skeleton
    // first.
    if (!hasLoadedOnceRef.current) {
      setLoading(true);
    }
    fetcherRef
      .current()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
          hasLoadedOnceRef.current = true;
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
