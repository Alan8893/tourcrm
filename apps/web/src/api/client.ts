/**
 * Minimal typed fetch wrapper for the real TourCRM backend
 * (docs/03-architecture/adr/ADR-0014-api-response-envelope.md): a single
 * resource is returned directly, a collection as `{items, pagination}`,
 * and an error as `{error:{code,message,details,request_id}}`.
 *
 * `/api/v1` is proxied to the backend by Vite in dev (vite.config.ts) and
 * by the reverse proxy in front of both apps in every other deployment
 * mode (docs/03-architecture/application-architecture.md §3) — so this
 * client always uses a same-origin relative path and never hardcodes a
 * backend host.
 */

export type Pagination = {
  page: number;
  page_size: number;
  total: number;
  pages: number;
};

export type CollectionResponse<T> = {
  items: T[];
  pagination: Pagination;
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const API_BASE = "/api/v1";

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Double-submit CSRF pattern: every state-changing request echoes the
  // non-HttpOnly `csrf_token` cookie back in a header.
  if (method !== "GET" && method !== "HEAD") {
    const csrfToken = readCookie("csrf_token");
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const errorBody = (body as { error?: { code?: string; message?: string; details?: unknown } })
      ?.error;
    throw new ApiError(
      response.status,
      errorBody?.code ?? "unknown_error",
      errorBody?.message ?? "Request failed",
      errorBody?.details,
    );
  }

  return body as T;
}
