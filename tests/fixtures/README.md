# Fixtures

Synthetic, hand-built repository timelines used as the ground truth for Phase 3 (metrics engine) and Phase 4 (detection engine) unit tests. All names, repos, and users are fictional. Every fixture's `expected` block was computed by hand against [docs/metric-definitions.md](../../docs/metric-definitions.md) — when Phase 3/4 tests are written, they assert against these numbers directly.

## Schema

```jsonc
{
  "scenario": "review_bottleneck",           // matches the filename
  "description": "One-line summary of what this fixture is isolating",
  "analysis_at": "2026-09-25T09:00:00Z",     // fixed "now" — never wall-clock, so results are reproducible
  "window": {
    "current_start": "2026-09-18T09:00:00Z", "current_end": "2026-09-25T09:00:00Z",
    "baseline_start": "2026-08-19T09:00:00Z", "baseline_end": "2026-09-18T09:00:00Z"
  },
  "repository": { "github_node_id": "R_1", "full_name": "acme-commerce/backend" },

  "pull_requests": [
    {
      "github_node_id": "PR_1", "number": 301, "author": "alice",
      "is_draft": false, "ready_for_review_at": null,   // null => use created_at
      "created_at": "...", "updated_at": "...",
      "closed_at": null, "merged_at": "...",
      "reopened": false, "reopen_count": 0,
      "additions": 80, "deletions": 20, "changed_files": 4,
      "requested_reviewers": [],                         // outstanding review requests (pending PRs only)
      "reviews": [{ "reviewer": "rahul", "state": "APPROVED", "submitted_at": "..." }]
    }
  ],

  "issues": [
    { "github_node_id": "I_1", "number": 40, "created_at": "...", "closed_at": null, "updated_at": "..." }
  ],

  "expected": {
    "pr_cycle_time":     { "current": { "median_hours": 5.5, "n": 4, "meets_minimum_sample": false }, "baseline": { "...": "..." }, "pct_change": null },
    "review_wait":       { "current": { "median_hours": 20,  "n": 5, "meets_minimum_sample": true  }, "baseline": { "median_hours": 6, "n": 6, "meets_minimum_sample": true }, "pct_change": 233.33 },
    "merge_wait":        { "current": { "median_hours": 2, "n": 5, "meets_minimum_sample": true, "excluded_no_approval": 0 } },
    "pending_review_count": 7,
    "review_concentration": { "value": 0.714, "top_reviewer": "rahul", "meets_minimum_sample": true },
    "stale_prs": { "count": 1, "threshold_days": 5, "items": [306] },
    "issue_backlog_growth": "insufficient_data",
    "pr_size": { "current": { "median_lines": 60, "n": 5 }, "oversized_threshold_lines": 400, "oversized_count": 0 },
    "findings_expected": ["REVIEW_BOTTLENECK"],
    "findings_rationale": "One sentence on why these rules do (or don't) fire."
  }
}
```

Notes:

- `ready_for_review_at: null` means the PR was never a draft — use `created_at` everywhere per metric-definitions.md.
- `requested_reviewers` is only populated for currently-open PRs with an outstanding (unreviewed) request — it feeds Review Concentration (§5), not a general reviewer-assignment log.
- Any metric not relevant to a given scenario is simply omitted from `expected` rather than force-computed from irrelevant data — check the fixture's `description` for what it's actually isolating.

## Scenarios

| File | Isolates |
|---|---|
| `quick_review.json` | Healthy baseline: fast reviews, no findings. Sanity-checks the medians and merge-wait formula with small, easily hand-verified numbers. |
| `review_bottleneck.json` | The canonical scenario from the product docs: review-wait +233%, 7 pending PRs, 71% concentrated on one reviewer → `REVIEW_BOTTLENECK` fires. |
| `no_reviews.json` | PRs with zero reviews. Confirms review-wait correctly excludes them (→ `n=0`, insufficient data) rather than silently treating "no review yet" as "not applicable." |
| `reopened_pr.json` | A closed-then-reopened PR. Confirms it's excluded from cycle time and merge-wait (its elapsed span includes an unknown closed period) while normal PRs alongside it still compute. |
| `draft_pr.json` | A draft PR that later becomes ready. Confirms every duration metric uses `ready_for_review_at`, not `created_at`, once a PR has been a draft. |
| `missing_approval.json` | A PR merged with no prior `APPROVED` review (e.g. admin override). Confirms merge-wait excludes it and discloses `excluded_no_approval` rather than crashing or silently using a null. |
| `empty_repository.json` | Zero PRs, zero issues. Every metric must degrade to `insufficient_data` / zero counts, never an error. |
| `timezone_boundary.json` | PR timestamps sitting exactly on window boundaries and around a UTC midnight rollover. Confirms window filtering (`>=` current_start, `<` current_end) is exact, not off-by-one. |
