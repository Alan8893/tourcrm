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

Via Docker Compose (recommended — also starts backend + PostgreSQL; see
root `README.md`):

```bash
cd /path/to/tourcrm && cp .env.example .env
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up --build
```

Or directly on the host:

```bash
cd apps/web
npm install
npm run dev
```

The Vite dev server binds to all interfaces (`server.host: true` in
`vite.config.ts`) so it is reachable both from the host directly and from
inside its Docker container (Issue #8).

`HOST_UID`/`HOST_GID` matter here specifically: `./apps/web` is bind-mounted
into the container, so the non-root container user must match the host
owner of this directory to have write access — otherwise Vite fails with
`EACCES` trying to create `vite.config.ts.timestamp-*.mjs` in `/app`. See
the root `README.md` for the full explanation and the root-owned-checkout
edge case.

## Smoke check (build)

```bash
cd apps/web
npm install
npm run build
```

## Lint & type checking (Issue #9)

`eslint` (flat config, `eslint.config.js` — the same rule sets Vite's own
`react-ts` template scaffolds) and `tsc` — the CI minimums from
`docs/03-architecture/technology-stack.md`.

```bash
cd apps/web
npm install
npm run lint       # eslint .
npm run typecheck  # tsc -b --noEmit
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
