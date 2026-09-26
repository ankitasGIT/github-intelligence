# GitHub Engineering Intelligence — Implementation Plan

## 1. Product goal

Build a portfolio-quality web application that connects to one GitHub repository and answers three reliable questions:

1. **What changed?** — compute delivery, review, issue, PR-quality, and activity metrics from stored GitHub events.
2. **What is off track, why, and what should we do?** — produce deterministic, evidence-backed findings and recommendations.
3. **Can you explain this?** — use an LLM only to narrate already-computed findings or to call read-only application tools.

The first public demo should work without an LLM. That makes the project technically credible and gives the AI layer a clear, bounded job.

### MVP definition of done

A signed-in user can select one repository, trigger a historical sync, and see:

- a reproducible health score and dimension breakdown;
- PR cycle-time and review-wait trends;
- at least three evidence-backed findings (including review bottleneck);
- deterministic recommended actions; and
- links from every displayed number to its metric definition and time window.

## 2. Scope and guardrails

### Build now

- One GitHub account and one repository per workspace.
- Read-only GitHub OAuth integration, historical sync, and scheduled polling.
- Pull requests, review events, issues, commits, milestones, and contributors.
- Health scoring, trends, findings, recommendations, and a responsive dashboard.
- Optional grounded weekly summary after the deterministic MVP is stable.

### Explicitly defer

- Multi-repository / organization rollups, Slack/email alerts, Jira/Linear, and write-back actions.
- Developer ranking, surveillance, or claims about individual performance. Findings describe work-system signals such as workload concentration.
- Code-quality analysis, predictive delivery dates, and arbitrary chat over raw GitHub history.
- Webhooks and MCP until polling-based MVP data is demonstrably correct.

## 3. Recommended stack

| Concern | Choice | Why |
|---|---|---|
| Frontend | React + TypeScript + Vite, Tailwind CSS, TanStack Query, Recharts | Modern SDE-1-friendly stack; fast dashboard iteration and typed API consumption. |
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic | Excellent for typed APIs, analytics-domain code, migrations, and generated OpenAPI docs. |
| Background work | Celery + Redis | Durable historical sync and scheduled jobs without blocking requests. Start with a single worker. |
| Data | PostgreSQL 16 | Relational event timelines, aggregates, and flexible JSON evidence payloads. |
| GitHub integration | GitHub OAuth App + REST API (PyGithub or httpx client) | Least privilege and clear user/repository selection; REST is sufficient for the MVP. |
| Auth/session | GitHub OAuth, secure HTTP-only session cookie or short-lived JWT | Keeps login simple while avoiding frontend token storage. |
| AI (Phase 6) | OpenAI Responses API with structured outputs/tool calling | Constrains explanations to known findings and enables audited tool calls. |
| MCP (Phase 7) | Python MCP SDK | Exposes the same read-only service layer to compatible clients. |
| Quality | pytest, pytest-asyncio, Playwright, Ruff, mypy, pre-commit | Strong automated evidence for a resume project. |
| Delivery | Docker Compose locally; GitHub Actions; Vercel (web) + Render/Fly.io (API/worker/Postgres) | Simple, low-ops deployment path with a polished live demo. |
| Observability | Structured JSON logs, Sentry, health/readiness endpoints | Enough production discipline without overbuilding. |

**Pragmatic simplification:** if Celery adds friction during week 1, use a FastAPI background task only for local development. Move to Celery before deploying long-running syncs; never run a multi-minute backfill inside a request handler.

## 4. Architecture

```text
Browser (React/TypeScript)
        │ HTTPS
        ▼
FastAPI API ──────────────────── PostgreSQL
  │  auth, dashboard queries         │ raw normalized events,
  │  sync orchestration              │ metric snapshots, findings
  ▼                                  │
Redis ──► Celery worker ─────────────┘
              │
              ├── GitHub REST API (OAuth token)
              ├── normalizer/upsert layer
              ├── metrics engine
              └── detection + recommendation engine

Phase 6: LLM receives only a versioned analysis snapshot / tool results.
Phase 7: MCP server calls the same read-only application service methods.
```

### Layer boundaries

| Layer | Responsibility | Must not do |
|---|---|---|
| Collector | Fetch paginated GitHub records; retain source IDs and timestamps; make retries idempotent. | Calculate product metrics. |
| Normalizer/repository layer | Convert GitHub payloads into a stable relational model and upsert transactions. | Contain UI or LLM logic. |
| Metrics engine | Calculate named metrics from stored data for a specified repository/window/baseline. | Guess causes or generate prose. |
| Detection engine | Evaluate versioned rules against metrics and emit evidence/confidence. | Call an LLM. |
| Recommendation engine | Map finding types to actions and rank them deterministically. | Mutate GitHub. |
| API/UI | Present analysis snapshots and drill-down evidence. | Recalculate business logic in JavaScript. |
| LLM/MCP adapters | Explain or retrieve facts through read-only service methods. | Access raw tokens, write data, or invent metrics. |

### Core data model

Start with these entities; use GitHub node IDs as stable external keys and store all timestamps in UTC.

```text
users ──< github_connections ──< repositories
repositories ──< sync_runs
repositories ──< pull_requests ──< reviews
pull_requests ──< review_requests
repositories ──< issues
repositories ──< commits
repositories ──< milestones
repositories ──< metric_snapshots ──< findings ──< recommendations
```

Important fields include `source_updated_at`, `synced_at`, `sync_cursor`, PR `created_at/merged_at/closed_at`, review `submitted_at/state`, and a JSONB `evidence` object on findings. Add unique constraints on `(repository_id, github_node_id)` and indexes on repository + time columns. Keep raw payloads only where needed for debugging, with an explicit retention policy.

### Metric definitions to publish in the UI

Define these before implementing charts so every result remains explainable:

| Metric | Initial definition |
|---|---|
| PR cycle time | `merged_at - created_at` for PRs merged in the selected window; display median and sample size, not only average. |
| Review-wait time | Time from PR creation (or latest author update, later enhancement) to first submitted review. Clearly label the MVP definition. |
| Merge-wait time | `merged_at - first_approval_at`; unavailable values are excluded and count disclosed. |
| Pending-review count | Open, non-draft PRs with no completed review after their latest meaningful update. |
| Review concentration | Share of pending review requests held by the most-loaded reviewer; show anonymized/team-safe framing where appropriate. |
| Stale PR / issue | Open item older than a configured threshold with no recent update. |
| Issue backlog growth | Percentage change in open issues versus the baseline window. |
| PR size | Additions + deletions (and/or changed files); use median and an oversized threshold. |

Use a current 7-day window compared with the preceding 30 days initially. Display “insufficient data” rather than a score when sample thresholds are not met.

## 5. Health, findings, and recommendations

### Initial health model

Use a documented, versioned model rather than opaque math:

```text
overall = 0.30 * delivery
        + 0.25 * reviews
        + 0.20 * issues
        + 0.15 * PR quality
        + 0.10 * activity
```

Each dimension starts at 100 and receives capped penalties from explicitly listed signals. Store `model_version`, component scores, inputs, and calculation time in every snapshot. This enables later tuning without rewriting history.

### First five detection rules

| Finding | Example trigger | Required evidence | Recommendation |
|---|---|---|---|
| `REVIEW_BOTTLENECK` | Review-wait median rises ≥50% vs baseline, at least 5 samples, and pending review count is elevated. | Current/baseline medians, sample sizes, queue count, concentration. | Redistribute pending reviews; prioritize oldest PRs. |
| `STALE_PULL_REQUESTS` | At least 3 open non-draft PRs are idle for ≥5 days. | PR numbers, ages, last activity. | Review/close/split the oldest items. |
| `ISSUE_BACKLOG_GROWTH` | Open issues increase ≥25% and exceed a minimum absolute count. | Current/baseline count and age buckets. | Triage oldest unlabelled issues; set ownership. |
| `OVERSIZED_PULL_REQUESTS` | Meaningful share of merged/open PRs exceeds size threshold. | File/line distribution and affected PRs. | Split future work; review large PRs first. |
| `MILESTONE_AT_RISK` | Due date is near and completion pace is below a transparent threshold. | Remaining items, due date, completed rate. | Re-scope, prioritize, or move the milestone. |

Confidence is deterministic: a documented function of data completeness, sample size, signal strength, and corroborating indicators. Never imply causal certainty; use wording such as “likely contributing factor.”

## 6. Delivery phases and timeline

Assumption: one developer working roughly 8–12 focused hours per week. This is a 10-week core plan plus 2 weeks of polish/contingency. Do not begin a new phase until its acceptance criteria pass.

| Phase | Weeks | Deliverable | Acceptance criteria |
|---|---:|---|---|
| 0. Foundation & product contract | 1 | Monorepo, local dev environment, schema draft, metric glossary, wireframes, sample data. | `docker compose up` starts web/API/DB; API health endpoint works; metric definitions are written and reviewed against a test repository. |
| 1. Auth, repository connection & storage | 2–3 | GitHub OAuth, repo picker, migrations, secure token handling, repository setup page. | A user can connect a GitHub test repo, select it, and no token is returned to the browser or logged. |
| 2. Reliable ingestion | 4 | Backfill and incremental polling for PRs, reviews, issues, commits, milestones; sync-run UI. | Re-running a sync creates no duplicates; paginated data is complete for fixture repos; failed runs surface an actionable error. |
| 3. Analytics & health | 5–6 | Metrics engine, daily snapshots, health score and trend API. | Hand-calculated fixture timelines match API results; missing data yields an explicit unavailable state. |
| 4. Findings & dashboard MVP | 7–8 | Five rules, recommendations, dashboard, evidence drill-down. | Seeded review-delay scenario produces `REVIEW_BOTTLENECK` with correct evidence; user can trace every card to source items/definition. |
| 5. Production hardening & launch | 9 | Docker images, CI, staging/prod deployment, logging, demo seed path, README. | Production URL works end-to-end with a test repo; CI is green; deployment uses environment secrets. |
| 6. Grounded AI explanation | 10 | Optional summary and “Why?/What next?” UI with strict structured context. | AI output passes grounding checks and exposes source finding IDs; unavailable AI never blocks dashboard use. |
| 7. MCP & portfolio polish | 11–12 | Read-only MCP server, architecture diagram, demo video, case study, final UX/accessibility pass. | MCP tools return schema-validated data; a fresh user can follow README and see a demo in under 10 minutes. |

### Suggested weekly rhythm

1. Begin with a small written acceptance criterion and a failing test.
2. Build one vertical slice, not a whole layer: e.g. PR backfill → cycle-time API → one dashboard card.
3. End the week by deploying to staging, recording a screenshot, and updating the README changelog.

## 7. Build order inside the repository

```text
github-intelligence/
  apps/
    api/                 # FastAPI routes, services, worker entry point
    web/                 # React application
  packages/
    analytics/           # Metric and detection domain logic; no FastAPI imports
    contracts/           # Shared OpenAPI-generated TS client or schemas
  infra/
    docker/              # Dockerfiles and Compose configuration
  docs/
    IMPLEMENTATION_PLAN.md
    metric-definitions.md
    architecture.md
  tests/
    fixtures/            # Sanitized GitHub API payloads and timeline factories
```

Keep the analytics package independently testable. API handlers should orchestrate services, not encode scoring or rule thresholds.

## 8. Testing strategy

### Test pyramid

| Level | What to test | Tools | Minimum launch bar |
|---|---|---|---|
| Unit | Durations, baseline comparison, score penalties, each rule, confidence, recommendation mapping. | pytest | Every metric/rule has normal, boundary, and insufficient-data cases. |
| Database/integration | Migrations, upserts, idempotency, transaction rollback, API query results. | pytest + temporary Postgres/Testcontainers | Repeated sync fixture produces identical counts and snapshots. |
| GitHub adapter | Pagination, rate limits, deleted/edited records, webhook/poll cursor behavior. | httpx mocks / recorded fixtures | No live GitHub dependency in CI. |
| API contract | Request validation, authz, error schemas, OpenAPI client compatibility. | pytest | Dashboard-critical endpoints have contract tests. |
| End-to-end | OAuth callback mock, repository selection, sync status, dashboard evidence flow. | Playwright | One stable happy path on every pull request. |
| Security/quality | Lint, formatting, types, dependency scan, secret scan. | Ruff, mypy, npm audit/pip-audit, Gitleaks | Required GitHub Actions checks pass. |
| AI evaluation (Phase 6) | Faithfulness, no new numbers, citations to finding IDs, safe failure. | Golden JSON fixtures | Every response is traceable to supplied structured facts. |

### Essential test fixtures

Create timeline factories for: quick review, review bottleneck, no reviews, reopened PR, draft PR, missing approval, empty repository, timezone boundary, and rate-limited sync. Use synthetic user names; never commit OAuth tokens or private repository payloads.

## 9. API and UI plan

### First API endpoints

```text
POST   /auth/github/start
GET    /auth/github/callback
GET    /repositories
POST   /repositories/{id}/sync
GET    /repositories/{id}/sync-runs
GET    /repositories/{id}/dashboard?window=7d
GET    /repositories/{id}/metrics
GET    /repositories/{id}/findings
GET    /repositories/{id}/findings/{finding_id}
GET    /repositories/{id}/recommendations
GET    /healthz
GET    /readyz
```

Use a single `dashboard` endpoint for the first page load, then add smaller drill-down endpoints only when needed. Every analytical response includes `window`, `baseline_window`, `computed_at`, `data_completeness`, and `analysis_version`.

### MVP dashboard sequence

1. Connect GitHub / select repository.
2. Show sync progress, last successful sync, and data coverage.
3. Show overall score plus five dimension cards; never hide insufficient data.
4. Show top findings ranked by severity and confidence.
5. Expand a finding to show evidence, affected items, metric definitions, and recommendations.
6. Show a small cycle-time/review-wait trend chart and recent PR queue.

Design principle: the primary screen answers “what should I look at today?” in under 30 seconds.

## 10. Deployment and operations

### Environments

| Environment | Purpose | Data |
|---|---|---|
| Local | Daily development through Docker Compose. | Synthetic fixtures and a personal test repository. |
| Staging | Automatic deploy from `main`; validates OAuth callback and worker flow. | Dedicated GitHub OAuth app and test repository. |
| Production | Public demo and portfolio link. | Explicitly authorized repositories only. |

### Deployment checklist

- Provision PostgreSQL, Redis, API service, Celery worker, and frontend as separate deployable services.
- Set GitHub OAuth callback URLs per environment; request the narrowest read-only scopes.
- Encrypt tokens at rest using a managed secret/key service; redact authorization headers and tokens from logs.
- Run Alembic migrations as a release step, then deploy API/worker versions compatible with that schema.
- Configure CORS to the production frontend origin only; add secure cookies, CSRF protection if cookie-authenticated, rate limits, and security headers.
- Schedule incremental syncs (for example every 15–30 minutes) and expose sync status; respect GitHub rate-limit headers with backoff.
- Back up the production database and document restore steps, even if it is a small managed-instance backup.
- Add Sentry/error alerts and a basic uptime check for `/healthz`.

### CI/CD pipeline

On every pull request: backend lint/type/unit tests, frontend lint/type/unit tests, API contract tests, and Playwright smoke test. On merge to `main`: build immutable Docker image(s), deploy staging, run migration + smoke test, then promote manually to production. Keep deployment credentials solely in GitHub Actions/environment secrets.

## 11. Risks and decisions

| Risk | Mitigation |
|---|---|
| GitHub rate limits and slow backfills | Paginate, checkpoint sync cursors, retry with backoff, and process in worker jobs. Start with one repo. |
| Ambiguous engineering metrics | Publish exact definitions, samples, exclusions, and timezone. Treat metrics as versioned contracts. |
| Sparse or unusual repository data | Require minimum samples; surface “not enough data” instead of generating a misleading score. |
| OAuth/token exposure | Least privilege, encryption, no frontend token storage, redacted logs, and a disconnect/delete-data flow. |
| Overly broad scope | Keep webhooks, MCP, and AI behind completed MVP gates. |
| LLM hallucination | Supply only structured analysis; require source finding IDs; never render AI numbers as canonical metrics. |
| Negative people-management interpretation | Phrase findings around queues and distribution, avoid leaderboards and performance labels. |

## 12. Portfolio and resume finish

Before sharing publicly, add a polished README with architecture, local setup, metric definitions, privacy/security posture, screenshots, and a 60–90 second demo video. Seed a fully synthetic demo repository or fixture mode so reviewers can evaluate the product without granting GitHub access.

Potential resume bullet after launch (replace placeholders with measured results):

> Built a full-stack GitHub Engineering Intelligence platform using React, FastAPI, PostgreSQL, Redis, and Docker; ingested repository activity asynchronously and surfaced evidence-backed delivery risks through a versioned analytics and rule engine, with optional LLM explanations grounded in computed findings.

In interviews, lead with the separation of concerns: **GitHub events → stored facts → deterministic metrics → deterministic findings → optional AI narration.** Be ready to explain idempotent syncs, metric definitions, confidence/data-quality handling, security of OAuth tokens, and why the product avoids individual performance scoring.

## 13. First-session checklist

1. Create the monorepo folders and Docker Compose services for Postgres, Redis, API, worker, and web.
2. Write `metric-definitions.md` and create synthetic timeline fixtures before the GitHub client.
3. Create the initial schema and Alembic migration for repositories, sync runs, PRs, reviews, metrics, and findings.
4. Implement one end-to-end vertical slice: import PRs and reviews from a fixture, calculate median review-wait time, expose it via FastAPI, and render one dashboard card.
5. Add the `REVIEW_BOTTLENECK` rule and its deterministic evidence payload only after the metric tests pass.

This order creates visible progress quickly while protecting the project’s most important promise: every conclusion is explainable and reproducible.
