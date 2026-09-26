# Getting Started — Phased Implementation Plan

Status check: the repo currently has only the folder skeleton (`apps/`, `packages/`, `infra/`, `tests/`, `scripts/`) — every source file is empty and there is no `pyproject.toml`, `package.json`, `docker-compose.yml`, or dependency manifest anywhere yet. This document turns that skeleton, plus the design already agreed in [PROJECT_IDEA.md](PROJECT_IDEA.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), into an ordered list of concrete next actions. Follow the phases in order; don't start a phase until the previous one's checklist is done.

**Scope change from IMPLEMENTATION_PLAN.md: no frontend/dashboard.** This build is API + analytics + MCP only. The system's outputs (health score, findings, evidence, recommendations, AI narration) are consumed through the FastAPI JSON API, a terminal report script, and the MCP server — not through a React app. `apps/web` and the dashboard/Playwright items from the original plan are dropped; everything else (data model, metrics, detection, health score, AI grounding, MCP tools) is unchanged, since none of it depended on having a UI.

Golden rule from the design docs, still the thing that drives every phase's order: **build the deterministic layers (data → metrics → detection → recommendations) completely before touching the LLM or MCP.** The product must be useful with zero AI calls — "useful" here means a correct JSON response and a readable terminal report, not a chart.

Because there's no OAuth redirect target (no browser UI to land the callback on), Phase 2 uses a GitHub **Personal Access Token** supplied via API/env instead of a full OAuth web flow. This is a simplification, not a security downgrade — same read-only scopes, same encryption-at-rest requirement; revisit OAuth only if this ever needs to serve other people's repos, not just yours.

---

## Phase 0 — Make the skeleton runnable

Goal: `docker compose up` brings up an empty-but-working stack; nothing analytical yet.

- [ ] `pyproject.toml` for the API: FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, httpx, pytest.
- [ ] `infra/docker/docker-compose.yml`: Postgres 16, Redis, `api` service. Celery worker service can be stubbed now, wired up in Phase 2.
- [ ] `apps/api/app/main.py`: FastAPI app with `/healthz` and `/readyz`.
- [ ] `apps/api/app/core/`: settings (env-driven config) and logging setup.
- [ ] `apps/api/app/db/`: SQLAlchemy engine/session setup + Alembic initialized (`alembic init`), pointed at Postgres via env var.
- [ ] `.env.example` at repo root documenting required variables (DB URL, Redis URL, GitHub PAT).
- [ ] `scripts/`: a `dev.sh` (or Makefile) that runs `docker compose up` + migrations in one command.
- [ ] CI skeleton in `infra/github/` (GitHub Actions): lint + typecheck + `pytest` on every PR, even with near-zero tests.

**Done when:** `docker compose up` starts API/DB, `GET /healthz` returns 200, and CI is green on an empty test suite.

---

## Phase 1 — Product contract before code

Goal: lock down what a "metric" and a "finding" mean so nothing downstream is ambiguous. This is writing, not coding — do it before Phase 2's ingestion code so the schema isn't designed twice.

- [ ] `docs/metric-definitions.md`: exact definition, time window, exclusions, and units for each metric in the table in [IMPLEMENTATION_PLAN.md §4](IMPLEMENTATION_PLAN.md) (PR cycle time, review-wait, merge-wait, pending-review count, review concentration, stale PR/issue, issue backlog growth, PR size).
- [ ] `docs/adr/0001-data-model.md`: the core entity relationships from IMPLEMENTATION_PLAN.md §4 (`users → github_connections → repositories → pull_requests/issues/commits/milestones → metric_snapshots → findings → recommendations`), plus the decision to key everything off GitHub node IDs.
- [ ] `tests/fixtures/`: hand-built synthetic JSON timelines for the scenarios listed in IMPLEMENTATION_PLAN.md §8 — quick review, review bottleneck, no reviews, reopened PR, draft PR, missing approval, empty repo, timezone boundary. These become the inputs for every unit test in Phases 3–4, so write them once, now.

**Done when:** every metric definition is written down with a worked numeric example, and the fixture files exist (even if nothing consumes them yet).

---

## Phase 2 — GitHub connection and ingestion

Goal: real GitHub data lands in Postgres, idempotently.

- [ ] `apps/api/app/core/`: a single GitHub connection configured from a Personal Access Token (env var / `POST /repositories` with the token stored encrypted), read-only scopes only.
- [ ] `apps/api/app/models/`: SQLAlchemy models for `github_connections`, `repositories`, `sync_runs`, `pull_requests`, `reviews`, `review_requests`, `issues`, `commits`, `milestones` per the ADR from Phase 1. First Alembic migration.
- [ ] `apps/api/app/services/`: GitHub REST client (httpx or PyGithub) + normalizer that upserts payloads into the models above, keyed by `(repository_id, github_node_id)`.
- [ ] `apps/api/app/workers/`: Celery task for historical backfill; start it via a FastAPI background task locally if Celery setup causes friction, but migrate to Celery before anything long-running ships.
- [ ] `GET /repositories`, `POST /repositories/{id}/sync`, `GET /repositories/{id}/sync-runs` endpoints.
- [ ] Adapter tests using recorded/mocked GitHub responses (no live GitHub dependency in CI) — pagination, rate limits, and re-sync idempotency are the three cases that matter most.

**Done when:** connecting a real test repo and re-running sync twice produces no duplicate rows, and a failed sync surfaces an actionable error instead of silently stopping.

---

## Phase 3 — Analytics engine

Goal: every metric from `metric-definitions.md` is computed from stored data and independently testable, with zero FastAPI imports in `packages/analytics`.

- [ ] `packages/analytics/src/metrics/metrics.py`: implement each metric function against the Phase 1 fixtures first, then against real synced data.
- [ ] `packages/analytics/src/models/models.py`: the plain dataclasses/Pydantic models the analytics package operates on (independent of the API's SQLAlchemy models).
- [ ] `packages/analytics/src/tests/test_metrics.py`: hand-calculate expected values for each fixture timeline and assert against them — this is the test suite that makes the "every number is reproducible" claim true.
- [ ] `apps/api/app/services/`: a thin snapshot service that runs the metrics engine for a repository/window and persists a `metric_snapshots` row (`model_version`, inputs, computed_at).
- [ ] `GET /repositories/{id}/metrics` endpoint returning the current snapshot.

**Done when:** for every fixture in `tests/fixtures/`, the API-computed metric matches the hand-calculated value in the test, including the "insufficient data" case.

---

## Phase 4 — Detection, health score, recommendations

Goal: the five rules from IMPLEMENTATION_PLAN.md §5 fire correctly, deterministically, on the fixtures.

- [ ] `packages/analytics/src/detectors.py`: implement `REVIEW_BOTTLENECK`, `STALE_PULL_REQUESTS`, `ISSUE_BACKLOG_GROWTH`, `OVERSIZED_PULL_REQUESTS`, `MILESTONE_AT_RISK`, each returning a typed finding with `severity`, `confidence`, and an `evidence` payload.
- [ ] `packages/analytics/src/tests/test_detectors.py`: one test per rule per fixture scenario (fires / doesn't fire / boundary / insufficient data).
- [ ] Health score module: the weighted formula from IMPLEMENTATION_PLAN.md §5 (`0.30 delivery + 0.25 reviews + 0.20 issues + 0.15 PR quality + 0.10 activity`), versioned.
- [ ] Recommendation mapping: finding type → ranked actions, fully deterministic (no LLM).
- [ ] API endpoints: `GET /repositories/{id}/dashboard`, `GET /repositories/{id}/findings`, `GET /repositories/{id}/findings/{finding_id}`, `GET /repositories/{id}/recommendations`.

**Done when:** the seeded review-delay fixture produces a `REVIEW_BOTTLENECK` finding with the exact evidence numbers the fixture implies, end to end through the API.

---

## Phase 5 — Terminal report (the UI substitute)

Goal: you can see the whole story — health score, findings, evidence, recommendations — without a browser, and without reading the database directly.

- [ ] `scripts/report.py`: a CLI (argparse/typer) that calls the Phase 4 endpoints (or the service layer directly) for a given repository and prints a formatted terminal report — overall score, per-dimension breakdown, ranked findings with evidence, recommended actions. `--json` flag for machine-readable output.
- [ ] Rely on FastAPI's auto-generated `/docs` (Swagger UI) as the ad hoc inspection surface during development — no custom UI code needed for that.
- [ ] Integration test in `tests/integration/`: run the report script against a seeded fixture repo and snapshot-assert the output contains the expected finding and score.

**Done when:** running `scripts/report.py <repo>` against the seeded review-delay fixture prints a correct, evidence-backed `REVIEW_BOTTLENECK` section and the right overall score.

---

## Phase 6 — Grounded AI explanation

Goal: the LLM narrates existing findings; it computes nothing.

- [ ] Structured-output LLM call that receives only a versioned findings/metrics snapshot (never raw GitHub data) and returns prose + citations to finding IDs.
- [ ] `GET /repositories/{id}/explain` (and/or a free-text `POST /repositories/{id}/ask`) backed by read-only tool calls into the existing service layer.
- [ ] `scripts/report.py --explain` prints the AI narrative alongside the deterministic report.
- [ ] Golden-fixture AI eval: no new numbers introduced, every claim traceable to a supplied finding ID, safe failure when the LLM is unavailable (report/API still work).

**Done when:** AI output passes the grounding check on every golden fixture, and disabling the AI layer entirely leaves the API and report script fully functional.

---

## Phase 7 — MCP server (primary interactive surface)

Goal: the same analysis is reachable from Claude Desktop / IDEs / agents — this is now the main way a person "uses" the product interactively, since there's no dashboard.

- [ ] Python MCP server exposing `get_project_health()`, `get_project_metrics()`, `get_project_risks()`, `get_open_prs()`, `get_stale_issues()`, `get_review_bottlenecks()`, `get_recommendations()` — calling the same read-only service methods the API uses, read-only, no raw tokens exposed.
- [ ] Schema-validated MCP tool responses, tested against a running instance.
- [ ] `docs/mcp-setup.md`: how to point Claude Desktop / an MCP-compatible client at the running server.

**Done when:** from Claude Desktop (or another MCP client), asking "why is my project at risk?" triggers tool calls and returns an answer grounded in the same findings the report script shows.

---

## Phase 8 — Hardening and launch

Goal: a deployed, demoable service — API + worker + MCP server, still no frontend.

- [ ] Dockerize `api`, `worker`, `mcp`; deploy Postgres/Redis as managed services.
- [ ] Full CI/CD: lint/type/unit/contract/integration tests on PR; build + deploy to staging on merge to `main`; manual promote to production.
- [ ] Token encryption at rest, redacted logs, rate limits, security headers.
- [ ] Scheduled incremental sync (15–30 min) with GitHub rate-limit backoff.
- [ ] README polish: architecture diagram, setup instructions, sample `scripts/report.py` output, MCP demo transcript.

**Done when:** the deployed API + MCP server work end to end against a real test repo, CI is green, and secrets are only in environment/Actions config.

---

## Immediate next 3 actions

If you're starting today, do these in order:

1. Phase 0: add `docker-compose.yml` (Postgres + Redis + API only) + FastAPI `main.py` with `/healthz`, and get `docker compose up` working.
2. Phase 1: write `docs/metric-definitions.md` and the first 2–3 fixture timelines (start with "quick review" and "review bottleneck" — they unlock both the metrics and detector test suites).
3. Phase 2: implement PAT-based GitHub connection + the PR/review models and one working backfill against your own test repo.

## Housekeeping

`apps/web` and `packages/contracts` (an OpenAPI→TypeScript client, only useful for a frontend) are now unused under this scope. They're harmless to leave empty, but if you'd rather keep the tree honest, they can be deleted — say the word and I'll remove them.
