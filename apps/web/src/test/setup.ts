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
