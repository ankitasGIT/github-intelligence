# GitHub Engineering Intelligence

An evidence-backed engineering-health service for GitHub repositories. It calculates metrics and deterministic findings first, then optionally uses AI to explain them. There is no web dashboard — results are consumed via the JSON API, a terminal report script, and an MCP server. See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for the full phased implementation plan.

## Project structure

```text
.
├── apps/
│   └── api/                    # FastAPI application and background worker
│       ├── app/
│       │   ├── api/            # HTTP routes and dependencies
│       │   ├── core/           # Configuration, logging, security
│       │   ├── db/             # Database session and migrations setup
│       │   ├── models/         # SQLAlchemy persistence models
│       │   ├── schemas/        # Pydantic request/response schemas
│       │   ├── services/       # GitHub sync and application services
│       │   └── workers/        # Celery tasks and schedules
│       └── tests/              # API-specific tests
├── packages/
│   ├── analytics/              # Pure metrics, health, detection and recommendation logic
│   │   ├── src/
│   │   └── tests/
│   └── contracts/              # Shared OpenAPI schema / generated clients (MCP + external consumers)
│       ├── openapi/
│       └── src/
├── infra/
│   ├── docker/                 # Dockerfiles and Compose configuration
│   └── github/                 # CI/CD notes (the actual workflow lives in .github/workflows/)
├── tests/
│   ├── e2e/                    # End-to-end tests (API + MCP flows)
│   ├── fixtures/               # Sanitized GitHub payloads and timeline data
│   └── integration/            # Cross-service/database tests
├── scripts/                    # Local development and maintenance scripts
└── docs/
    ├── adr/                    # Architecture decision records
    ├── GETTING_STARTED.md      # Phased implementation plan (start here)
    └── IMPLEMENTATION_PLAN.md  # Original detailed design doc
```

The analytics package remains independent from FastAPI, so metric and detection logic is easy to test and reuse through the API and the MCP server.

## Getting started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- Python 3.12+ — only needed if you want to run the API outside Docker

### Run the stack

```bash
git clone <this repo> && cd github-intelligence
./scripts/dev.sh
```

`scripts/dev.sh` copies `.env.example` to `.env` on first run (defaults work as-is for now — no secrets are required until GitHub ingestion lands in Phase 2) and then runs:

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

This starts three services: `db` (Postgres 16), `redis`, and `api` (FastAPI, hot-reloading on code changes).

### Verify it's working

```bash
curl http://localhost:8000/healthz   # {"status":"ok"}       — process is up
curl http://localhost:8000/readyz    # {"status":"ready"}     — process is up AND can reach Postgres
```

Interactive API docs (Swagger UI): http://localhost:8000/docs

### Stop the stack

```bash
docker compose -f infra/docker/docker-compose.yml down   # add -v to also drop the Postgres volume
```

### Running the API without Docker

```bash
cd apps/api
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload   # requires Postgres/Redis reachable at the URLs in .env
```

### Tests, lint, and type-check

```bash
cd apps/api
ruff check .
mypy app
pytest
```

These same checks run automatically on every PR via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
