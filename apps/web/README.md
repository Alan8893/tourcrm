# TourCRM Web (frontend skeleton)

React + TypeScript + Vite frontend application skeleton for TourCRM. Issue #4
provided the application skeleton; Issue #7 added the test harness. No
business screens, routing, or API integration are implemented here. See
`docs/03-architecture/application-architecture.md` §4 for the canonical
frontend contract.

## Structure

```text
src/
├── main.tsx        # entrypoint — mounts the React application
├── App.tsx         # placeholder root component
├── App.test.tsx    # real component test (Issue #7)
└── test/setup.ts   # Vitest setup (jest-dom matchers)
```

Feature/domain screens are added under `src/` by their own Issues once the UI
foundation (routing, design system, API client) is in place; this skeleton
intentionally does not pre-create empty feature directories.

## Running (development)

```bash
cd apps/web
npm install
npm run dev
```

## Smoke check (build)

```bash
cd apps/web
npm install
npm run build
```

## Testing (Issue #7)

Test runner: Vitest (Vite-native; no separate framework/config added),
with Testing Library for component tests (jsdom environment configured in
`vite.config.ts`'s `test` block).

```bash
cd apps/web
npm install
npm test                        # full suite, non-watch, exits with the test result's code
npx vitest run src/App.test.tsx # one file
```

`npm test` runs `vitest run` (CI/non-interactive mode — it exits rather than
watching); a failing test returns a non-zero exit code.
