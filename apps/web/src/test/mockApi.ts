import { vi } from "vitest";

export type MockApiHandler = {
  /** HTTP method; defaults to matching any method. */
  method?: string;
  /** URL substring. Handlers are tried in order — list specific ones first. */
  match: string;
  status?: number;
  headers?: Record<string, string>;
  /** Static JSON body, or a function computing it per request. */
  body?: unknown | ((init: RequestInit | undefined) => unknown);
  /** Holds the response until the returned promise settles — lets a test
   * observe the in-flight (loading / disabled) state of a request. */
  gate?: () => Promise<void>;
};

/** Like `stubFetch`, but method-aware and with per-request bodies — for
 * multi-step workflows (import job lifecycle, export POST) where the same
 * URL answers differently over time. Unmatched requests throw. */
export function mockApi(handlers: MockApiHandler[]) {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = (init?.method ?? "GET").toUpperCase();
      const handler = handlers.find(
        (entry) => url.includes(entry.match) && (!entry.method || entry.method === method),
      );
      if (!handler) {
        throw new Error(`No mock registered for ${method} ${url}`);
      }
      if (handler.gate) await handler.gate();
      const body = typeof handler.body === "function" ? handler.body(init) : handler.body;
      return new Response(JSON.stringify(body ?? {}), {
        status: handler.status ?? 200,
        headers: { "Content-Type": "application/json", ...handler.headers },
      });
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** `[METHOD, url]` of every request the mock received, in order. */
export function requests(fetchMock: ReturnType<typeof mockApi>): Array<[string, string]> {
  return fetchMock.mock.calls.map(([input, init]) => [
    (init?.method ?? "GET").toUpperCase(),
    typeof input === "string" ? input : input.toString(),
  ]);
}
