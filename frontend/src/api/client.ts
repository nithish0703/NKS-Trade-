import { getApiBaseUrl } from "../utils/env";
import type { ApiError } from "../types/dashboard";

const DEFAULT_TIMEOUT_MS = 10_000;

export class ApiRequestError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

export async function apiGet<T>(path: string, timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${getApiBaseUrl()}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });

    if (!response.ok) {
      let detail = `Request failed with status ${response.status}`;
      try {
        const body = (await response.json()) as ApiError;
        if (body?.detail) {
          detail = body.detail;
        }
      } catch {
        // Response body was not JSON; keep the generic message.
      }
      throw new ApiRequestError(detail, response.status);
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiRequestError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiRequestError("Request timed out.", null);
    }
    throw new ApiRequestError("Network error while contacting the API.", null);
  } finally {
    clearTimeout(timeoutId);
  }
}
