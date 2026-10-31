# KubeSage frontend

React + TypeScript UI for KubeSage (Phase 2): workload overview, startup waterfalls, detector candidates and incidents.

## Prerequisites

- Node 22+ and npm
- The KubeSage backend running on `http://127.0.0.1:8000` (the dev server proxies `/api` there)

## Run

```bash
npm install
npm run dev        # http://localhost:5173
```

Other scripts:

```bash
npm run typecheck  # tsc -b
npm run build      # typecheck + production build into dist/
npm run preview    # serve dist/ locally
```

## Layout

```
src/
  api/          types.ts (mirrors backend/kubesage/schemas.py), client.ts (fetch wrapper), hooks.ts (React Query)
  components/   Waterfall, StatusDot, CategoryBadge, Badges, SloBar, SloEditor, CandidatesList,
                KeyValueTable, QueryState (loading/error/empty), Section, Layout, format.ts, phases.ts
  pages/        OverviewPage, WorkloadPage, StartupPage, IncidentPage, NotFoundPage
  App.tsx       route table
```

Routes: `/`, `/workloads/:id`, `/startups/:id`, `/incidents/:id`.

Lists poll every 5 s (`LIST_POLL_MS` in `src/api/hooks.ts`); a startup detail polls until the pod is ready.

## Extending

- New page: add a route in `src/App.tsx` and, if it is top-level, an entry in `NAV_ITEMS` in `src/components/Layout.tsx`.
- API changes: update `src/api/types.ts` to match `backend/kubesage/schemas.py`, then add a hook in `src/api/hooks.ts`.
- Theme: colour tokens live in `src/index.css` (light by default, dark via `prefers-color-scheme`).
