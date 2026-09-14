# FinOps Dashboard

AWS-first FinOps platform for **multi-tenant cloud cost visibility**, **tag attribution**, **deterministic savings recommendations**, **LDAP/OIDC auth**, and a configurable **Admin Console**.

| | |
|--|--|
| **API** | http://localhost:**8100** |
| **UI** | http://localhost:**3000** |
| **Dev login** | `admin` / `admin` (when `AUTH_DEV_BYPASS=true`) |
| **Default tenant** | `tenant-demo` |
| **Company portals** | `{subdomain}.localhost:3000` (set under Admin → Tenants) |
| **Cache** | In-memory by default (`CACHE_BACKEND=memory`); optional Redis |

---

## Start here

**[docs/05-beginner-guide.md](docs/05-beginner-guide.md)** — run locally, use portals, connect AWS, deploy.

---

## Documentation index

| Doc | Description |
|-----|-------------|
| [Architecture](docs/01-architecture.md) | System design, components, multi-tenant model |
| [Data flow (DFD)](docs/02-data-flow.md) | Auth, scan, retention, credentials, tenant lifecycle |
| [**Visual diagrams (HTML + SVG)**](docs/diagrams/index.html) | Open in browser — architecture & DFD as images |
| [Diagrams pack](docs/diagrams/complete-architecture.md) | Mermaid sources + SVG embeds |
| [Security / LDAP / RBAC](docs/03-security-ldap-rbac.md) | AuthN/Z, secrets handling, deletion safeguards |
| [Design corrections](docs/04-corrections.md) | Fixes vs original draft |
| [Beginner guide](docs/05-beginner-guide.md) | Hands-on walkthrough |
| [Feature Builder (Cursor SDK)](docs/06-feature-agent.md) | Agentic feature development — CLI + Admin UI |

---

## What's in this repo

| Area | Contents |
|------|----------|
| **Frontend** | Next.js — Dashboard, Reports, Advisor, Chat, Profile, Admin (menu-based) |
| **Backend** | FastAPI — JWT/RBAC, tenants, AWS discovery, attribution, retention |
| **Data** | **SQLite** and **PostgreSQL + pgvector** both first-class (also MySQL / SQL Server) |
| **Docker** | Multi-arch Compose — API + UI + Postgres(pgvector) + Redis on **ARM / AMD / x86_64** |
| **Helm / EKS** | `deploy/helm/finops` — Kubernetes chart + local/EKS values |
| **ECS** | `deploy/ecs` — Fargate task definition templates |
| **IaC** | Terraform references (see terraform/) |

---

## Quick start (Docker Compose)

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
# UI http://localhost:3000  ·  API http://localhost:8100/docs
```

Full deploy guide (Helm / EKS / ECS): **[deploy/README.md](deploy/README.md)**

---

## Quick start (local Python / Node)

```bash
# 1) Backend — port 8100
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # if present
uvicorn app.main:app --reload --port 8100

# 2) Frontend — port 3000
cd frontend
npm install
npm run dev
```

| URL | Purpose |
|-----|---------|
| http://localhost:8100/docs | API Swagger |
| http://localhost:3000/login | Sign in |
| http://localhost:3000/dashboard | User portal |
| http://localhost:3000/profile | User profile & password |
| http://localhost:3000/admin | Admin console (sidebar menu) |

---

## Architecture (one glance)

> **See diagrams as images:** open [`docs/diagrams/index.html`](docs/diagrams/index.html) in a browser  
> (terminal Markdown only shows Mermaid as text — not pictures).

![FinOps complete architecture](docs/diagrams/img/04-complete-architecture.svg)

![FinOps logical architecture](docs/diagrams/img/02-logical-architecture.svg)

Full pack: [docs/diagrams/](docs/diagrams/) · [Architecture](docs/01-architecture.md) · [DFD](docs/02-data-flow.md)

---

## Key capabilities

### Multi-tenant
- Platform **admin** creates/deletes tenants
- **tenant_admin** administers **their own** organization only  
- **admin** (master / platform) can switch into any tenant and manage platform settings
- Per-tenant: AWS credentials, auth (local/LDAP/OIDC), retention, feature flags

### Cloud credentials (portal)
- Modes: **portal access keys**, **AssumeRole**, or **local ~/.aws profiles** (opt-in)
- Admin → **Cloud Credentials** + **AWS Cloud**

### FinOps Board (recommended UI)
- Nav → **Board** — Cost Explorer style **Breakdown By** (Account / Service / Region / Environment / Team / …)
- Dimensions are **pivots**, not a forced hierarchy — org-specific lenses (Spend, Ownership, Waste, Growth, …)
- Executive cards: **Cloud spend · Potential saving · Realized saving**
- Inline drill-down with savings next to cost · **Why did cost change?** · Cost hotspots · Allocation health
- API: `GET /api/v1/user/finops/board?breakdown_by=account&lens=spend&billing_month=YYYY-MM`

### Database engine
- **Both supported:** **SQLite** (local/offline) and **PostgreSQL + pgvector** (Docker / multi-tenant)
- **CPU arches:** **ARM** (`arm64`), **AMD** and **x86_64** (`amd64`) — see [deploy/multiarch.md](deploy/multiarch.md)
- Postgres: `docker compose up -d db` → `DATABASE_URL=postgresql+psycopg://finops:finops@127.0.0.1:5432/finops`
- Multi-arch image build: `./scripts/docker-build-multiarch.sh`
- Admin → **Database Engine**: SQLite | PostgreSQL + pgvector | MySQL | SQL Server
- Schema apply runs `CREATE EXTENSION IF NOT EXISTS vector` on Postgres only
- **Apply** tests connection, **creates schema automatically**, writes URL override
- Restart API to switch live engine
- Details: [deploy/postgres/README.md](deploy/postgres/README.md)

### Profile
- Every user: **Profile** nav → display name, email, phone, job, department, timezone
- **Change password** (requires current password; min 8 chars)

### Tenant deletion
- Platform admin: deletion **inventory report** (CSV/JSON) then **Delete**
- Purges accounts, scans, users, credentials, configs; returns report (no secrets)

---

## Auth & roles (summary)

| Role | Access |
|------|--------|
| `viewer` | Dashboard / reports (cached) |
| `bill_exporter` | CSV export |
| `engineer` / `devops` / `finops` | Rescan, historical fetch |
| `finops_admin` / `tenant_admin` | Admin console for **home tenant** |
| `admin` | Master / platform admin — any tenant + tenants/DB engine |

Details: [docs/03-security-ldap-rbac.md](docs/03-security-ldap-rbac.md)

---

## Security posture (high level)

- Passwords: PBKDF2-SHA256; change requires current password
- Secrets masked in API responses (`********`)
- Tenant data isolated by `tenant_id` (+ header for cross-tenant admins)
- Tenant delete requires typed confirmation; default/last tenant protected unless `force`
- Deletion reports never include AWS keys / IdP secrets
- CloudTrail / Security Groups features are **off until admin enables** per tenant

---

## License / ownership

Internal FinOps platform — configure credentials and retention per your org policy.
