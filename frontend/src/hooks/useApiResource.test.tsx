import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useApiResource } from "./useApiResource";

describe("useApiResource", () => {
  it("starts in a loading state and resolves with data", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 5 });
    const { result } = renderHook(() => useApiResource(fetcher));

    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ total: 5 });
    expect(result.current.error).toBeNull();
  });

  it("surfaces an API error message", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("Request failed with status 500"));
    const { result } = renderHook(() => useApiResource(fetcher));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("Request failed with status 500");
    expect(result.current.data).toBeNull();
  });

  it("refresh() re-invokes the fetcher", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 1 });
    const { result } = renderHook(() => useApiResource(fetcher));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.refresh();
    });

    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });
});
