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

function buildRequestInit(path: string, init: RequestInit): [string, RequestInit] {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  // `FormData` bodies (multipart document upload/replace) must keep the
  // browser-generated `multipart/form-data; boundary=...` Content-Type —
  // forcing `application/json` here would silently break every
  // `Form(...)`-based backend endpoint (app/api/v1/persons.py's document
  // create/replace).
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Double-submit CSRF pattern: every state-changing request echoes the
  // non-HttpOnly `csrf_token` cookie back in a header.
  if (method !== "GET" && method !== "HEAD") {
    const csrfToken = readCookie("csrf_token");
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }
  return [`${API_BASE}${path}`, { ...init, method, headers, credentials: "include" }];
}

async function throwApiError(response: Response): Promise<never> {
  const body = await response.json().catch(() => null);
  const errorBody = (body as { error?: { code?: string; message?: string; details?: unknown } })
    ?.error;
  throw new ApiError(
    response.status,
    errorBody?.code ?? "unknown_error",
    errorBody?.message ?? "Request failed",
    errorBody?.details,
  );
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(...buildRequestInit(path, init));

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    await throwApiError(response);
  }

  return (await response.json()) as T;
}

/** Filename parsed from a `Content-Disposition: attachment;
 * filename="..."; filename*=UTF-8''...` header (RFC 6266) — mirrors what
 * `app.api.v1.persons._content_disposition` /
 * `app.api.v1.events._package_content_disposition` always send. Prefers
 * the UTF-8 `filename*` form; falls back to the plain quoted `filename`. */
function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      // fall through to the ASCII fallback below
    }
  }
  const asciiMatch = /filename="([^"]+)"/i.exec(header);
  return asciiMatch ? asciiMatch[1] : null;
}

/** For binary responses (Document/package download) — never JSON-parses
 * the body, and surfaces the same `ApiError` shape as `apiFetch` on
 * failure so callers handle both identically. */
export async function apiFetchBlob(
  path: string,
  init: RequestInit = {},
): Promise<{ blob: Blob; filename: string | null }> {
  const response = await fetch(...buildRequestInit(path, init));

  if (!response.ok) {
    await throwApiError(response);
  }

  const blob = await response.blob();
  return { blob, filename: filenameFromContentDisposition(response.headers.get("Content-Disposition")) };
}

/** Triggers a browser "Save as" for an already-fetched blob via a
 * transient `URL.createObjectURL` object URL, revoked immediately after
 * the click — this is the standard client-side file-save mechanism and
 * distinct from a backend "private object URL"/storage-internal
 * reference (never used here or anywhere in this client). */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
