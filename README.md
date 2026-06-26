# AI Cost Adviser

An AI-powered tool that analyzes **AWS** cloud resources and suggests cost
optimizations. You pick which AWS services to scan and a region; the backend
collects each resource's configuration, runs cost-optimization analysis with an
LLM, and returns a report with severity-ranked issues, estimated savings, and
copy-pasteable AWS CLI fix commands.

> Adapted from the "AI Cloud Cost Detective" reference project (which targets
> Azure + resource groups). This version targets **AWS at the service level** —
> see [Scope model](#scope-model) for why.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React (Vite + TypeScript + Tailwind), dark theme |
| Backend | Python (FastAPI) |
| Auth | Custom JWT (bcrypt + PyJWT) |
| Cloud data | boto3 (AWS SDK for Python) |
| AI analysis | OpenAI API (gpt-4o) |
| Database | PostgreSQL (asyncpg) |
| Live updates | FastAPI WebSocket |

## Scope model

Azure's reference design analyzes one **resource group** at a time. AWS has no
direct equivalent and no single "list all resources" call, so this tool uses a
**per-service** model: the user selects which services to scan (EC2, EBS, S3,
RDS, …) plus a region. Each service has its own analyzer in a registry; results
are grouped by category (Compute / Storage / Database) for display.

## Architecture

```
  React (Vite)  ──HTTP+JWT──►  FastAPI backend  ──boto3──►  AWS (per service)
       ▲  ▲                         │  │  │
       │  └──── WebSocket ──────────┘  │  └──── OpenAI API (cost analysis)
       │        (live progress)        │
       └──────── report / history ─────┴──── PostgreSQL (users, analyses)
```

## Request flow

```
①  User signs up / logs in (JWT, stored in localStorage)
②  User selects AWS services + region on the Dashboard
③  Backend scans each selected service via boto3 (config + metadata)
④  Live progress streamed to the UI over WebSocket
⑤  Scanned resources sent to the OpenAI API for cost analysis
⑥  Result stored in PostgreSQL (analyses table)
⑦  React shows the report: summary, issues, savings, fix commands
```

## What it detects

- **Over-provisioned resources** — instances/databases larger than needed
- **Unused / idle resources** — unattached EBS volumes, idle instances
- **Misconfigurations** — e.g. gp2 volumes that should be gp3, public databases
- **Wrong tiers / old generations** — outdated instance families, storage classes
- **Cost opportunities** — reserved capacity, S3 lifecycle policies

Fix suggestions are emitted as **AWS CLI** commands.

## Prerequisites

- Python 3.9+ and Node.js 18+
- AWS credentials with read-only access (locally via `aws configure`; on EC2 via
  an attached IAM role — no keys needed, see below)
- A PostgreSQL database (any: AWS RDS/Aurora or local)
- An OpenAI API key

## Configuration

All configuration lives in a **single centralized `.env` at the project root**,
read by both the backend (via python-dotenv) and the frontend (via Vite
`envDir`). Copy the template and fill it in:

```bash
cp .env.example .env
```

Key variables (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI key for cost analysis |
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET` | JWT signing secret (`openssl rand -hex 32`) |
| `VITE_API_BASE` | Backend URL the browser calls (must be `VITE_`-prefixed) |
| `DB_REQUIRED` | If `true`, fail startup when the DB is unreachable (default: degrade gracefully) |

### AWS credentials

- **On EC2 with an attached IAM role:** set **no** AWS keys — boto3 fetches
  temporary credentials from the instance metadata service automatically.
- **Local development:** uses your existing `~/.aws` credentials.

The IAM role/user needs read access to the scanned services (e.g. the
`ReadOnlyAccess` managed policy).

## Run

### Backend

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload        # http://localhost:8000  (docs at /docs)
```

If `DATABASE_URL` is unset or unreachable, the backend still boots but
auth/history return 503 (it logs a warning). Set `DB_REQUIRED=true` to make a
DB failure abort startup instead.

### Frontend

```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/signup` | — | Create account, returns JWT |
| POST | `/api/auth/login` | — | Log in, returns JWT |
| GET | `/api/services` | — | List scannable AWS services |
| POST | `/api/analyze` | JWT | Scan selected services + AI analysis |
| GET | `/api/history` | JWT | Past analyses for the user |
| WS | `/ws/progress/{analysis_id}` | — | Live progress for an analysis |

## Project structure

```
.
├── .env.example          # centralized config template (backend + frontend)
├── backend/
│   ├── main.py           # FastAPI app, routes, analyze pipeline
│   ├── auth.py           # JWT + bcrypt, current_user_id dependency
│   ├── aws_scanner.py    # boto3 per-service scanner registry
│   ├── ai_analyzer.py    # OpenAI cost analysis
│   ├── db.py             # PostgreSQL (asyncpg): pool, tables, queries
│   ├── progress.py       # WebSocket progress hub
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── App.tsx, main.tsx, api.ts, auth.tsx
    │   ├── pages/        # Login, Signup, Dashboard, Report, History
    │   └── components/   # Navbar, ProgressTracker
    └── (vite / tailwind / ts config)
```
