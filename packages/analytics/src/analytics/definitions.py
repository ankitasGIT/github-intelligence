"""Short, display-ready summaries of every metric and rule, keyed the way findings reference
them (`Finding.metric_refs`). docs/metric-definitions.md remains the full contract; `doc_anchor`
points at the matching section so the UI can link every number back to its definition."""

from dataclasses import dataclass

from analytics.detection import (
    ISSUE_BACKLOG_GROWTH,
    MILESTONE_AT_RISK,
    OVERSIZED_PULL_REQUESTS,
    REVIEW_BOTTLENECK,
    STALE_PULL_REQUESTS,
)


@dataclass(frozen=True)
class Definition:
    key: str
    name: str
    definition: str
    formula: str
    unit: str
    doc_anchor: str


METRIC_DEFINITIONS: dict[str, Definition] = {
    d.key: d
    for d in (
        Definition(
            "pr_cycle_time",
            "PR cycle time",
            "Time from a PR becoming reviewable to merge, for PRs merged in the window. "
            "Reopened PRs are excluded.",
            "median(merged_at - (ready_for_review_at or created_at))",
            "hours",
            "1-pr-cycle-time",
        ),
        Definition(
            "review_wait",
            "Review-wait time",
            "Time from a PR becoming reviewable to its first submitted review, for PRs that "
            "became reviewable in the window and have been reviewed.",
            "median(first_review_submitted_at - (ready_for_review_at or created_at))",
            "hours",
            "2-review-wait-time",
        ),
        Definition(
            "merge_wait",
            "Merge-wait time",
            "Time from first approval to merge, for PRs merged in the window. PRs merged "
            "without approval are excluded and counted.",
            "median(merged_at - first_approved_at)",
            "hours",
            "3-merge-wait-time",
        ),
        Definition(
            "pending_review_count",
            "Pending-review count",
            "Open, non-draft PRs with no review since their last update, right now.",
            "count(open, non-draft PRs with no review after updated_at)",
            "pull requests",
            "4-pending-review-count",
        ),
        Definition(
            "review_concentration",
            "Review concentration",
            "Share of pending PRs (with a requested reviewer) that wait on the single "
            "most-requested reviewer. Needs at least 3 such PRs.",
            "max(pending PRs requesting reviewer X) / pending PRs with a requested reviewer",
            "share",
            "5-review-concentration",
        ),
        Definition(
            "stale_items",
            "Stale PR / issue",
            "An open, non-draft PR or open issue with no update for at least 5 days.",
            "analysis_at - updated_at >= 5 days",
            "items",
            "6-stale-pr--issue",
        ),
        Definition(
            "issue_backlog_growth",
            "Issue backlog growth",
            "Change in open issues at the end of the current window vs. the end of the "
            "baseline window. Needs at least 5 open issues at baseline.",
            "(current_open - baseline_open) / baseline_open * 100",
            "percent",
            "7-issue-backlog-growth",
        ),
        Definition(
            "pr_size",
            "PR size",
            "Lines changed per merged PR. Oversized means strictly more than 400 lines.",
            "additions + deletions",
            "lines",
            "8-pr-size",
        ),
        Definition(
            "milestone_pace",
            "Milestone pace",
            "Items closed per day since the milestone was created, compared with the pace "
            "needed to close the remaining items by the due date.",
            "closed / days_since_created vs. open / days_until_due",
            "items per day",
            "milestone_at_risk",
        ),
    )
}


RULE_DEFINITIONS: dict[str, Definition] = {
    d.key: d
    for d in (
        Definition(
            REVIEW_BOTTLENECK,
            "Review bottleneck",
            "Review-wait is rising and more PRs than usual are waiting for a first review.",
            "review-wait median +50% vs baseline (both n >= 5) AND awaiting-first-review count "
            ">= 3 and >= 1.5x its baseline daily median",
            "finding",
            "review_bottleneck",
        ),
        Definition(
            STALE_PULL_REQUESTS,
            "Stale pull requests",
            "Several open PRs have had no activity for days.",
            "at least 3 open, non-draft PRs idle >= 5 days",
            "finding",
            "stale_pull_requests",
        ),
        Definition(
            ISSUE_BACKLOG_GROWTH,
            "Issue backlog growth",
            "Open issues are growing faster than they are being closed.",
            "open issues +25% vs baseline AND baseline open count >= 5",
            "finding",
            "issue_backlog_growth",
        ),
        Definition(
            OVERSIZED_PULL_REQUESTS,
            "Oversized pull requests",
            "A meaningful share of recent PRs are larger than the oversized threshold.",
            ">= 25% of PRs merged in the window plus open non-draft PRs exceed 400 lines, "
            "with at least 5 PRs in the sample",
            "finding",
            "oversized_pull_requests",
        ),
        Definition(
            MILESTONE_AT_RISK,
            "Milestone at risk",
            "A milestone due soon is closing items too slowly to finish on time.",
            "open milestone due within 14 days (or overdue) with open items, AND overdue or "
            "closed-per-day since creation < open items per day remaining",
            "finding",
            "milestone_at_risk",
        ),
    )
}
