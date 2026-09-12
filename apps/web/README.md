# TourCRM Web (frontend skeleton)

React + TypeScript + Vite frontend application skeleton for TourCRM. This is
Issue #4 (application skeleton) — no business screens, routing, or API
integration are implemented here. See
`docs/03-architecture/application-architecture.md` §4 for the canonical
frontend contract.

## Structure

```text
src/
├── main.tsx   # entrypoint — mounts the React application
└── App.tsx    # placeholder root component
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
