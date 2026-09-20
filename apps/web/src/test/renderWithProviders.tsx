import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, RouterProvider, createMemoryRouter } from "react-router-dom";
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

/**
 * Like `renderWithProviders`, but backed by a real data router
 * (`createMemoryRouter`) instead of the declarative `MemoryRouter` — needed
 * to test actual browser Back/Forward semantics (`router.navigate(-1)`/
 * `router.navigate(1)`), which a plain `MemoryRouter` has no way to
 * exercise from a test (its internal history stack isn't exposed).
 * `unstable_HistoryRouter` + a hand-built `createMemoryHistory` was tried
 * first and rejected: `history.push`/`.back()` updated the `history`
 * object correctly but never triggered a re-render of the app in this
 * environment (matches its `unstable_` status) — `createMemoryRouter` is
 * the stable, actively-maintained API and reliably re-renders here.
 *
 * `ui` is rendered for every path (`path: "*"`), exactly like
 * `renderWithProviders`'s permissive `MemoryRouter` — it may itself
 * contain nested `<Routes>`/`<Route>` for tests that need real navigation
 * between pages.
 */
export function renderWithHistory(
  ui: ReactElement,
  { initialEntries, client = createTestQueryClient() }: { initialEntries: string[]; client?: QueryClient },
) {
  const router = createMemoryRouter([{ path: "*", element: ui }], { initialEntries });
  const result = render(
    <QueryClientProvider client={client}>
      <NotificationProvider>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </NotificationProvider>
    </QueryClientProvider>,
  );
  return { ...result, router };
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
