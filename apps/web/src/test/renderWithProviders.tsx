import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { NotificationProvider } from "../components/ui/Notification";

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", client = createTestQueryClient() }: { route?: string; client?: QueryClient } = {},
) {
  return render(
    <QueryClientProvider client={client}>
      <NotificationProvider>
        <MemoryRouter
          initialEntries={[route]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          {ui}
        </MemoryRouter>
      </NotificationProvider>
    </QueryClientProvider>,
  );
}

/** Stubs `global.fetch` for one test with a handler keyed by URL
 * substring — enough for these component tests without pulling in a
 * network-mocking dependency. */
export function stubFetch(handlers: Array<{ match: string; response: unknown; status?: number }>) {
  // Typed with the real 2-arg `fetch` signature (even though the body only
  // reads `input`) so `fetchMock.mock.calls[i][1]` — the request `init` a
  // caller may want to assert on, e.g. to inspect a POST/PATCH body — is
  // typed, not just present at runtime.
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    const handler = handlers.find((entry) => url.includes(entry.match));
    if (!handler) {
      throw new Error(`No stub registered for fetch(${url})`);
    }
    return new Response(JSON.stringify(handler.response), {
      status: handler.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
