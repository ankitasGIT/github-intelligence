# ADR 0001: Core Data Model

**Status:** Accepted
**Date:** 2026-09-26

## Context

Phase 2 (ingestion) needs a concrete relational schema before any SQLAlchemy models or Alembic migrations are written. The schema must support every metric in [metric-definitions.md](../metric-definitions.md) without redesign, since fixtures (Phase 1) and the metrics engine (Phase 3) are both written against it.

## Decision

### Entity relationships

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

### Keys and identity

- **GitHub node IDs are the stable external key** for every synced entity (`github_node_id`, e.g. a PR's GraphQL node ID) — not GitHub's numeric `id`, which is dialect-specific, and not just the human-facing `number`, which is only unique within a repository and is reused across PRs/issues in some edge cases.
- Every synced table has a unique constraint on `(repository_id, github_node_id)`, enforced at the database level so a re-run sync cannot create duplicates (idempotency, required by Phase 2's acceptance criteria).
- Every synced table stores `source_updated_at` (GitHub's own `updated_at`) separately from `synced_at` (when our collector last wrote the row), so staleness of our own data is distinguishable from staleness of the underlying GitHub item.

### Field requirements driven by metric-definitions.md

| Table | Fields required by a specific metric |
|---|---|
| `pull_requests` | `created_at`, `is_draft`, `ready_for_review_at` (nullable — draft exclusion, §1/§2), `merged_at`, `closed_at`, `updated_at` (pending/staleness, §4/§6), `reopened` (bool), `reopen_count` (§1/§3 exclusion), `additions`, `deletions`, `changed_files` (§8) |
| `reviews` | `pull_request_id`, `reviewer_login`, `state`, `submitted_at` (§2/§3 — first-review and first-approval lookups) |
| `review_requests` | `pull_request_id`, `requested_reviewer_login`, `is_outstanding` (bool — cleared when a review is submitted or the request is removed; §5 concentration) |
| `issues` | `created_at`, `closed_at`, `updated_at` (§6/§7) |
| `milestones` | `due_on`, `open_issue_count`, `closed_issue_count` (future `MILESTONE_AT_RISK` rule) |
| `metric_snapshots` | `repository_id`, `window_start`, `window_end`, `baseline_start`, `baseline_end`, `model_version`, `computed_at`, `inputs` (JSONB) |
| `findings` | `metric_snapshot_id`, `type`, `severity`, `confidence`, `evidence` (JSONB) |
| `recommendations` | `finding_id`, `rank`, `action` |

### Timestamps and indexing

- All timestamp columns are stored as UTC (`timestamptz` in Postgres), matching the "all timestamps are UTC" convention in metric-definitions.md.
- Index every synced table on `(repository_id, created_at)` or `(repository_id, updated_at)` as appropriate — every metric query filters by repository and a time window first.

### What's explicitly deferred

- No `users` table columns beyond what OAuth/PAT auth needs (Phase 2 decides the exact shape based on whether OAuth or PAT auth is used).
- No raw-payload retention policy is decided yet; if raw GitHub JSON is kept for debugging, it goes in a separate table with an explicit retention/TTL, not inline on the normalized tables.

## Consequences

- Fixtures in `tests/fixtures/` are written directly against this shape (see `tests/fixtures/README.md`), so Phase 2's SQLAlchemy models and Phase 3's metric functions can both consume the same JSON structure without translation.
- Because `github_node_id` is the identity key, re-running a historical backfill is naturally idempotent via upsert-on-conflict, which is the mechanism Phase 2's acceptance criteria require.
- Adding a new metric later that needs a field not listed above requires updating this ADR (or superseding it) before writing the migration — the schema is not expected to grow silently.
