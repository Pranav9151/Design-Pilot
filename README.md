# DesignPilot MECH — v1.0-alpha

> AI-powered mechanical engineering design — prompt to 3 validated bracket variants in seconds.
> Stress analysis, DFM checks, cost estimates, STEP export, all traceable to Shigley's formulas.

**Weeks 1–5 complete. 331 tests passing. Frontend built.**

---

## What Works Right Now

| Feature | Status |
|---|---|
| 22 REST endpoints (FastAPI) | ✅ |
| 27 verified materials (Shigley's / ASM refs) | ✅ |
| JWT auth, 5 role bundles, full IAM | ✅ |
| Append-only audit log | ✅ |
| Triple-Lock accuracy (Lock 1 deterministic engine) | ✅ |
| AST validator — 20+ attack payloads blocked | ✅ |
| Docker+gVisor CadQuery sandbox | ✅ (needs image build) |
| Dev-skip sandbox for local testing | ✅ `SANDBOX_SKIP_FOR_DEV=true` |
| Parameter tuning + reanalysis | ✅ `PATCH /{id}/parameters` |
| Auto-optimize against a goal | ✅ `POST /{id}/optimize` |
| Manager explain summary | ✅ `POST /{id}/explain` |
| Why-not reasoning | ✅ `GET /{id}/why-not` |
| Similar design search | ✅ `GET /{id}/similar` |
| Senior engineer questions | ✅ `GET /{id}/questions` |
| Material recommender | ✅ `POST /materials/recommend` |
| SSE streaming generation | ✅ `POST /designs/stream` |
| React frontend (Studio, Dashboard, Auth) | ✅ |

---

## Quickstart

### Prerequisites
- Python 3.12, Docker Desktop (running), Node.js 18+

### Backend setup

```bash
# Create venv
python -m venv .venv

# Activate — Windows Git Bash:
source .venv/Scripts/activate
# Mac / Linux:
source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env
```

**Edit `.env` — minimum required:**
```bash
SUPABASE_JWT_SECRET=any-32-character-string-for-local-dev
ANTHROPIC_API_KEY=sk-ant-...          # needed for real LLM calls
SANDBOX_SKIP_FOR_DEV=true             # skip Docker image until you build it
```

```bash
docker compose up -d                   # Postgres:5433, Redis:6380
alembic upgrade head                   # create schema + RLS + audit trigger
python -m scripts.seed_materials       # load 27 materials
uvicorn app.main:app --reload --port 8000
```

**Verify:**
```
http://localhost:8000/docs        ← Swagger UI — all 22 endpoints
http://localhost:8000/health      ← {"status":"ok"}
http://localhost:8000/api/v1/ready ← {"status":"ready","db":"ok"}
```

> **`{"detail":"Not Found"}` at `/` is correct.** FastAPI has no root route.
> Open `/docs` for the interactive API or `/health` for the liveness check.

### Get a dev token (no Supabase needed)

```bash
python -m scripts.mint_dev_token
# Prints a signed JWT. Copy it.

TOKEN=eyJ...
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/materials
# Returns all 27 materials
```

### Frontend setup

```bash
cd frontend
cp .env.example .env
# Edit frontend/.env:
#   VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
#   VITE_SUPABASE_ANON_KEY=eyJ...
# Get both from supabase.com → project → Settings → API
npm install
npm run dev           # → http://localhost:5173
```

---

## Tests

```bash
# Fast — no DB needed (331 tests)
pytest tests/unit/ tests/engineering/ tests/security/ -q

# Integration — requires postgres running
pytest tests/integration/ -q

# All
pytest -q

# Coverage
pytest tests/unit/ tests/engineering/ tests/security/ --cov=app --cov-report=term-missing
```

---

## CadQuery Sandbox Image

Required for real STEP geometry. Build once:

```bash
docker build -t designpilot/cadquery-sandbox:latest ./sandbox/
# First build ~10 min (downloads CadQuery via conda)
```

Then set `SANDBOX_SKIP_FOR_DEV=false` and restart the server.

**Without the image:** `SANDBOX_SKIP_FOR_DEV=true` — LLM parsing, stress analytics, Triple-Lock,
and DB writes all work; STEP files are minimal stubs and the 3D viewer shows a placeholder mesh.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/api/v1/health` | Health + version |
| GET | `/api/v1/ready` | DB readiness check |
| GET | `/api/v1/materials` | List 27 materials |
| POST | `/api/v1/materials/recommend` | Score materials for a use-case |
| GET | `/api/v1/materials/{slug}` | Single material |
| POST | `/api/v1/designs` | Generate design (sync) |
| POST | `/api/v1/designs/stream` | Generate design (SSE) |
| GET | `/api/v1/designs` | List user's designs |
| GET | `/api/v1/designs/{id}` | Design detail |
| DELETE | `/api/v1/designs/{id}` | Archive |
| GET | `/api/v1/designs/{id}/diary` | Design Diary |
| PATCH | `/api/v1/designs/{id}/parameters` | Tune dimensions + rerun |
| POST | `/api/v1/designs/{id}/explain` | Manager summary |
| GET | `/api/v1/designs/{id}/why-not` | Recommendation reasoning |
| GET | `/api/v1/designs/{id}/similar` | Similar designs |
| GET | `/api/v1/designs/{id}/questions` | Engineer review questions |
| POST | `/api/v1/designs/{id}/optimize` | Auto-optimize |

---

## Environment Variables

### Backend `.env`

```bash
# Database — matches docker-compose ports
DATABASE_URL=postgresql+asyncpg://designpilot:designpilot@localhost:5433/designpilot_dev
DATABASE_URL_SYNC=postgresql://designpilot:designpilot@localhost:5433/designpilot_dev

# Auth
SUPABASE_JWT_SECRET=          # 32+ chars for local dev; real Supabase secret in prod
SUPABASE_JWT_ALGORITHM=HS256
SUPABASE_JWT_AUDIENCE=authenticated

# Redis — matches docker-compose
REDIS_URL=redis://localhost:6380/0

# LLM
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# Sandbox
SANDBOX_SKIP_FOR_DEV=false    # true = skip Docker for local dev testing
SANDBOX_IMAGE=designpilot/cadquery-sandbox:latest

# Storage (Cloudflare R2) — optional in dev, local fallback if unset
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_ENDPOINT_URL=
```

### Frontend `frontend/.env`

```bash
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ...
```

---

## Project Layout

```
designpilot/
├── app/
│   ├── api/v1/          designs, materials, health, stream
│   ├── audit/           append-only audit log + middleware
│   ├── core/            config, db, logging, rate limiting, units
│   ├── data/            materials.py — 27 verified materials
│   ├── engines/         formulas (Shigley's), DFM, cost
│   ├── iam/             permissions, roles, JWT
│   ├── models/          SQLAlchemy ORM — 10 tables
│   └── services/        pipeline, LLM client, sandbox, storage,
│                        AST validator, output validator, triple-lock
├── alembic/             migrations (schema + RLS + audit trigger)
├── sandbox/             CadQuery Docker image
├── scripts/
│   ├── seed_materials.py
│   └── mint_dev_token.py    ← local JWT without Supabase
├── tests/
│   ├── unit/            no DB (331 tests total)
│   ├── engineering/     Shigley's textbook formula checks
│   ├── security/        AST filter, sandbox, LLM schema
│   └── integration/     DB + HTTP (needs Postgres)
└── frontend/
    └── src/
        ├── lib/         api.ts, supabase.ts, utils.ts
        ├── pages/       Studio, Dashboard, Auth, Landing, Settings
        ├── components/  PromptBar, VariantCard, ModelViewer,
        │                ProgressStream, CommandPalette, TopNav
        └── store/       Zustand — auth + studio state
```

---

## Security

Key threat mitigations (full list in `FORENSIC-ANALYSIS-Complete.md`):

- **Code injection** — AST validator + Docker sandbox + gVisor kernel isolation
- **LLM hallucinating material data** — LLM picks slug only; all properties from DB
- **SQL injection** — SQLAlchemy ORM + parameterized queries everywhere
- **Cross-user data access** — Row-Level Security on every user-data table
- **Audit bypass** — `REVOKE UPDATE DELETE` on audit_log + trigger defence
