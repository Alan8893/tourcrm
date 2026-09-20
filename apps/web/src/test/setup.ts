// Registers jest-dom matchers (toBeInTheDocument, etc.) with Vitest's
// expect, and augments the TypeScript types for them.
import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// `globals: false` in vite.config.ts (no Jest-style global `afterEach`),
// so Testing Library's own auto-cleanup detection never fires — unmount
// every rendered tree after each test explicitly, or repeated `render()`
// calls across tests in the same file leak previous DOM trees into later
// queries.
afterEach(() => {
  cleanup();
});

// jsdom has no `matchMedia` implementation. Default to "does not match"
// (desktop) so any component using it (e.g. the Calendar's mobile/desktop
// composition switch, TH-0105) doesn't crash in tests that don't care
// about a specific breakpoint; tests that do override this per-test with
// `vi.stubGlobal("matchMedia", ...)`.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
