# FinOps Dashboard — Agent Guide

Cursor rules in `.cursor/rules/` load automatically when this project is open. Read them before making changes.

## Quick context

- **What:** Multi-tenant AWS FinOps — costs, attribution, savings, finance planning, chat assistant, admin console.
- **API:** FastAPI on port **8100** · **UI:** Next.js on **3000**
- **Dev login:** `admin` / `admin` · **Tenant:** `tenant-demo`

## Rules (`.cursor/rules/`)

| Rule | Scope |
|------|-------|
| `finops-project.mdc` | Always — stack, architecture, docs, how to run |
| `finops-guardrails.mdc` | Always — LLM/cost safety, scans, utilization, pitfalls |
| `finops-backend.mdc` | `backend/**/*.py` — routes, services, engines, tests |
| `finops-frontend.mdc` | `frontend/**/*.{ts,tsx}` — pages, AdminConsole, API client |

## Documentation

Start with `README.md` and `docs/05-beginner-guide.md`. Architecture: `docs/01-architecture.md`. Visual diagrams: `docs/diagrams/index.html`.

## Before you ship

1. Backend: `cd backend && pytest -q`
2. Frontend: `cd frontend && npm run lint` (fix TS errors — especially AdminSection / SECTION_META sync)
3. Restart API after new models or DB engine changes
4. Never invent cost numbers in LLM/chat responses
5. Do not commit unless the user asks

## Recent feature map

| Feature | Frontend | Backend |
|---------|----------|---------|
| Chat | `app/chat/page.tsx` | `api/chat_routes.py`, `engines/chat_service.py` |
| Finance | `app/finance/page.tsx` | `api/finance_routes.py`, `services/planned_cost_service.py` |
| Utilization | Finance tab + Admin | `services/utilization_service.py`, `engines/optimization_engine.py` |
| Long timeout | `lib/longTimeout.ts`, `LongTimeoutControl.tsx` | — (client-side only) |
| Feature Builder | `app/admin/feature-builder/page.tsx` | `api/feature_agent_routes.py`, `engines/feature_agent/`, `services/feature_agent_service.py` |
| Admin | `app/admin/*/page.tsx`, `AdminConsole.tsx` | `api/admin/routes.py` |

## Agentic feature development

See **`docs/06-feature-agent.md`**. CLI: `python scripts/feature_agent.py run --help`.
