"""The detection engine: versioned rules that turn a MetricsReport into evidence-backed findings.

Rules only read metrics already computed by the metrics engine plus the same plain inputs; they
never call an LLM and never claim causation. Every finding carries the numbers that triggered
it (`evidence`), the source items behind them (`affected_items`), the metric definitions it
relies on (`metric_refs`), and a deterministic confidence score.

Thresholds and formulas are documented in docs/metric-definitions.md, "Detection rules".
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from analytics.metrics import OK, MetricsReport
from analytics.models import (
    AnalysisWindows,
    Issue,
    Milestone,
    PullRequest,
    RepositoryData,
)

# Bump whenever a rule's trigger, severity, or confidence formula changes.
DETECTION_VERSION = "rules-v1"

REVIEW_BOTTLENECK = "REVIEW_BOTTLENECK"
STALE_PULL_REQUESTS = "STALE_PULL_REQUESTS"
ISSUE_BACKLOG_GROWTH = "ISSUE_BACKLOG_GROWTH"
OVERSIZED_PULL_REQUESTS = "OVERSIZED_PULL_REQUESTS"
MILESTONE_AT_RISK = "MILESTONE_AT_RISK"

# Also the final tie-break when ranking findings.
RULE_ORDER = (
    REVIEW_BOTTLENECK,
    STALE_PULL_REQUESTS,
    ISSUE_BACKLOG_GROWTH,
    OVERSIZED_PULL_REQUESTS,
    MILESTONE_AT_RISK,
)

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
SEVERITY_RANK = {HIGH: 0, MEDIUM: 1, LOW: 2}

PULL_REQUEST = "pull_request"
ISSUE = "issue"
MILESTONE = "milestone"


@dataclass(frozen=True)
class DetectionConfig:
    """Every rule threshold from docs/metric-definitions.md, in one versioned place."""

    review_wait_increase_pct: float = 50.0
    pending_elevation_ratio: float = 1.5
    pending_min_count: int = 3
    concentration_corroboration: float = 0.5
    stale_pr_min_count: int = 3
    backlog_growth_pct: float = 25.0
    oversized_min_share: float = 0.25
    oversized_min_sample: int = 5
    milestone_horizon_days: int = 14
    milestone_min_items: int = 5


DEFAULT_DETECTION_CONFIG = DetectionConfig()


@dataclass(frozen=True)
class AffectedItem:
    kind: str  # "pull_request" | "issue" | "milestone"
    number: int
    detail: dict[str, Any]


@dataclass(frozen=True)
class Confidence:
    """`score = 0.4 * sample + 0.4 * strength + 0.2 * corroboration`, each factor in [0, 1]."""

    score: float
    level: str
    sample_factor: float
    strength_factor: float
    corroboration_factor: float
    corroborating: tuple[str, ...]
    not_corroborating: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    key: str  # unique per snapshot: the rule, plus the milestone number for MILESTONE_AT_RISK
    rule: str
    rule_version: str
    severity: str
    confidence: Confidence
    title: str
    summary: str
    evidence: dict[str, Any]
    affected_items: tuple[AffectedItem, ...]
    metric_refs: tuple[str, ...]


# --- Shared helpers -------------------------------------------------------------------------


def _days(delta: timedelta) -> float:
    return delta.total_seconds() / 86400


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def _confidence(
    *, n: int, n_min: int, observed: float, threshold: float, corroborators: dict[str, bool]
) -> Confidence:
    """Sample factor reaches 1 at twice the minimum sample; strength factor reaches 1 at twice
    the trigger threshold (so a finding exactly at its threshold scores 0.5 on strength)."""
    sample = min(1.0, n / (2 * n_min)) if n_min > 0 else 1.0
    strength = min(1.0, observed / threshold / 2) if threshold > 0 else 1.0
    held = tuple(name for name, ok in corroborators.items() if ok)
    corroboration = len(held) / len(corroborators) if corroborators else 0.0
    score = round(0.4 * sample + 0.4 * strength + 0.2 * corroboration, 4)
    level = HIGH if score >= 0.75 else MEDIUM if score >= 0.5 else LOW
    return Confidence(
        score=score,
        level=level,
        sample_factor=round(sample, 4),
        strength_factor=round(strength, 4),
        corroboration_factor=round(corroboration, 4),
        corroborating=held,
        not_corroborating=tuple(name for name, ok in corroborators.items() if not ok),
    )


def _severity(observed: float, threshold: float, *, base: str = MEDIUM) -> str:
    """One step above `base` once the signal is at least twice its trigger threshold."""
    if observed >= 2 * threshold:
        return HIGH if base == MEDIUM else MEDIUM
    return base


def _open_non_draft(prs: Sequence[PullRequest], at: datetime) -> list[PullRequest]:
    return [pr for pr in prs if not pr.is_draft and pr.is_open_at(at)]


def awaiting_first_review(prs: Sequence[PullRequest], at: datetime) -> list[PullRequest]:
    """PRs that, at instant `at`, were open, reviewable, and had no review submitted yet.

    Unlike §4 pending-review count (which compares against the PR's *current* `updated_at`),
    this is reconstructable for any past instant from stored timestamps, so it is what
    REVIEW_BOTTLENECK compares against its own baseline norm.
    """
    return [
        pr
        for pr in prs
        if not pr.is_draft
        and pr.is_open_at(at)
        and pr.reviewable_at <= at
        and pr.first_review_at(at) is None
    ]


def pending_review_norm(prs: Sequence[PullRequest], windows: AnalysisWindows) -> float:
    """Median of the daily awaiting-first-review count, sampled once per day at
    `baseline.start + k days` for every day of the baseline window."""
    days = int(_days(windows.baseline.end - windows.baseline.start))
    samples = [
        len(awaiting_first_review(prs, windows.baseline.start + timedelta(days=k)))
        for k in range(days)
    ]
    return float(median(samples)) if samples else 0.0


def _pr_item(pr: PullRequest, at: datetime, **extra: Any) -> AffectedItem:
    return AffectedItem(
        kind=PULL_REQUEST,
        number=pr.number,
        detail={
            "age_days": round(_days(at - pr.reviewable_at), 2),
            "idle_days": round(_days(at - pr.updated_at), 2),
            "requested_reviewers": list(pr.requested_reviewers),
            **extra,
        },
    )


# --- REVIEW_BOTTLENECK --------------------------------------------------------------------


def _review_bottleneck(
    report: MetricsReport, data: RepositoryData, windows: AnalysisWindows, config: DetectionConfig
) -> list[Finding]:
    wait = report.review_wait
    if wait.current.status != OK or wait.baseline.status != OK or wait.pct_change is None:
        return []
    if wait.pct_change < config.review_wait_increase_pct:
        return []

    at = windows.analysis_at
    awaiting = awaiting_first_review(data.pull_requests, at)
    norm = pending_review_norm(data.pull_requests, windows)
    elevated = len(awaiting) >= config.pending_min_count and len(awaiting) >= (
        config.pending_elevation_ratio * norm
    )
    if not elevated:
        return []

    concentration = report.review_concentration
    stale_numbers = {item.number for item in report.stale_pull_requests.items}
    pending_numbers = set(report.pending_review_count.pr_numbers)
    cycle = report.pr_cycle_time
    corroborators = {
        "review_concentration_high": concentration.status == OK
        and (concentration.value or 0) >= config.concentration_corroboration,
        "cycle_time_rising": cycle.current.status == OK and (cycle.pct_change or 0) > 0,
        "stale_pending_prs": bool(stale_numbers & pending_numbers),
    }

    oldest_first = sorted(awaiting, key=lambda pr: (pr.reviewable_at, pr.number))
    share = (
        f" {concentration.top_reviewer_pending} of {concentration.pending_with_requested_reviewer}"
        " pending PRs with a requested reviewer are waiting on the same reviewer."
        if concentration.status == OK and concentration.top_reviewer
        else ""
    )
    return [
        Finding(
            key=REVIEW_BOTTLENECK,
            rule=REVIEW_BOTTLENECK,
            rule_version=DETECTION_VERSION,
            severity=_severity(wait.pct_change, config.review_wait_increase_pct),
            confidence=_confidence(
                n=wait.current.n,
                n_min=report.config.min_sample,
                observed=wait.pct_change,
                threshold=config.review_wait_increase_pct,
                corroborators=corroborators,
            ),
            title="Reviews are taking longer and the queue is growing",
            summary=(
                f"Median review-wait rose {wait.pct_change:+.0f}% "
                f"({wait.baseline.median_hours:.1f}h baseline to "
                f"{wait.current.median_hours:.1f}h now) while {len(awaiting)} PRs are awaiting a "
                f"first review (baseline norm {norm:g}).{share} A review bottleneck is a likely "
                "contributing factor to slower delivery."
            ),
            evidence={
                "review_wait_current_median_hours": wait.current.median_hours,
                "review_wait_current_n": wait.current.n,
                "review_wait_baseline_median_hours": wait.baseline.median_hours,
                "review_wait_baseline_n": wait.baseline.n,
                "review_wait_pct_change": wait.pct_change,
                "threshold_pct": config.review_wait_increase_pct,
                "pending_review_count": report.pending_review_count.count,
                "awaiting_first_review_count": len(awaiting),
                "awaiting_first_review_norm": norm,
                "pending_elevation_ratio": config.pending_elevation_ratio,
                "review_concentration": concentration.value,
                "review_concentration_status": concentration.status,
                "top_reviewer": concentration.top_reviewer,
                "top_reviewer_pending": concentration.top_reviewer_pending,
                "pending_with_requested_reviewer": concentration.pending_with_requested_reviewer,
                "current_pr_numbers": list(wait.current.pr_numbers),
                "baseline_pr_numbers": list(wait.baseline.pr_numbers),
            },
            affected_items=tuple(
                _pr_item(pr, at, awaiting_review=True, stale=pr.number in stale_numbers)
                for pr in oldest_first
            ),
            metric_refs=("review_wait", "pending_review_count", "review_concentration"),
        )
    ]


# --- STALE_PULL_REQUESTS ------------------------------------------------------------------


def _stale_pull_requests(
    report: MetricsReport, data: RepositoryData, windows: AnalysisWindows, config: DetectionConfig
) -> list[Finding]:
    stale = report.stale_pull_requests
    if stale.count < config.stale_pr_min_count:
        return []

    at = windows.analysis_at
    by_number = {pr.number: pr for pr in data.pull_requests}
    unreviewed = {pr.number for pr in awaiting_first_review(data.pull_requests, at)}
    oldest_first = sorted(stale.items, key=lambda s: (-s.idle_days, s.number))
    max_idle = oldest_first[0].idle_days
    corroborators = {
        "stale_prs_never_reviewed": any(s.number in unreviewed for s in stale.items),
        "long_idle_prs": max_idle >= 2 * stale.threshold_days,
    }
    return [
        Finding(
            key=STALE_PULL_REQUESTS,
            rule=STALE_PULL_REQUESTS,
            rule_version=DETECTION_VERSION,
            severity=_severity(stale.count, config.stale_pr_min_count),
            confidence=_confidence(
                n=stale.count,
                n_min=config.stale_pr_min_count,
                observed=stale.count,
                threshold=config.stale_pr_min_count,
                corroborators=corroborators,
            ),
            title=f"{stale.count} open pull requests have gone quiet",
            summary=(
                f"{stale.count} open, non-draft PRs have had no activity for at least "
                f"{stale.threshold_days} days (longest idle: {max_idle:.1f} days). Idle PRs "
                "tie up work in progress and drift out of date with the main branch."
            ),
            evidence={
                "stale_count": stale.count,
                "threshold_days": stale.threshold_days,
                "min_count": config.stale_pr_min_count,
                "max_idle_days": max_idle,
                "never_reviewed_count": sum(1 for s in stale.items if s.number in unreviewed),
            },
            affected_items=tuple(
                _pr_item(by_number[s.number], at, awaiting_review=s.number in unreviewed)
                for s in oldest_first
                if s.number in by_number
            ),
            metric_refs=("stale_items",),
        )
    ]


# --- ISSUE_BACKLOG_GROWTH -----------------------------------------------------------------

# Upper bounds in days; the last bucket is open-ended.
_AGE_BUCKETS: tuple[tuple[str, float], ...] = (
    ("under_7d", 7),
    ("7_to_30d", 30),
    ("30_to_90d", 90),
    ("over_90d", float("inf")),
)


def _age_buckets(issues: Sequence[Issue], at: datetime) -> dict[str, int]:
    buckets = {name: 0 for name, _ in _AGE_BUCKETS}
    for issue in issues:
        age = _days(at - issue.created_at)
        name = next(name for name, upper in _AGE_BUCKETS if age < upper)
        buckets[name] += 1
    return buckets


def _issue_backlog_growth(
    report: MetricsReport, data: RepositoryData, windows: AnalysisWindows, config: DetectionConfig
) -> list[Finding]:
    growth = report.issue_backlog_growth
    if growth.status != OK or growth.pct_change is None:
        return []
    if growth.pct_change < config.backlog_growth_pct:
        return []

    at = windows.analysis_at
    open_issues = sorted(
        (i for i in data.issues if i.is_open_at(at)), key=lambda i: (i.created_at, i.number)
    )
    stale_issue_numbers = {item.number for item in report.stale_issues.items}
    corroborators = {"stale_issues_present": report.stale_issues.count > 0}
    return [
        Finding(
            key=ISSUE_BACKLOG_GROWTH,
            rule=ISSUE_BACKLOG_GROWTH,
            rule_version=DETECTION_VERSION,
            severity=_severity(growth.pct_change, config.backlog_growth_pct),
            confidence=_confidence(
                n=growth.baseline_open_count,
                n_min=report.config.backlog_min_baseline_open,
                observed=growth.pct_change,
                threshold=config.backlog_growth_pct,
                corroborators=corroborators,
            ),
            title="The open-issue backlog is growing",
            summary=(
                f"Open issues grew {growth.pct_change:+.0f}% over the last "
                f"{report.config.current_window_days} days ({growth.baseline_open_count} to "
                f"{growth.current_open_count}). Issues are arriving faster than they are being "
                "closed."
            ),
            evidence={
                "current_open_count": growth.current_open_count,
                "baseline_open_count": growth.baseline_open_count,
                "pct_change": growth.pct_change,
                "threshold_pct": config.backlog_growth_pct,
                "stale_issue_count": report.stale_issues.count,
                "age_buckets": _age_buckets(open_issues, at),
            },
            # Oldest first, capped: the full list is the repository's issue tracker.
            affected_items=tuple(
                AffectedItem(
                    kind=ISSUE,
                    number=issue.number,
                    detail={
                        "age_days": round(_days(at - issue.created_at), 2),
                        "idle_days": round(_days(at - issue.updated_at), 2),
                        "stale": issue.number in stale_issue_numbers,
                    },
                )
                for issue in open_issues[:10]
            ),
            metric_refs=("issue_backlog_growth", "stale_items"),
        )
    ]


# --- OVERSIZED_PULL_REQUESTS --------------------------------------------------------------


_SIZE_BUCKETS: tuple[tuple[str, float], ...] = (
    ("up_to_100", 100),
    ("101_to_400", 400),
    ("401_to_1000", 1000),
    ("over_1000", float("inf")),
)


def _oversized_pull_requests(
    report: MetricsReport, data: RepositoryData, windows: AnalysisWindows, config: DetectionConfig
) -> list[Finding]:
    at = windows.analysis_at
    threshold_lines = report.pr_size.oversized_threshold_lines
    merged_numbers = set(report.pr_size.current.pr_numbers)
    merged = [pr for pr in data.pull_requests if pr.number in merged_numbers]
    population = sorted(merged + _open_non_draft(data.pull_requests, at), key=lambda p: p.number)
    if len(population) < config.oversized_min_sample:
        return []

    oversized = [pr for pr in population if pr.lines_changed > threshold_lines]
    share = round(len(oversized) / len(population), 4)
    if share < config.oversized_min_share:
        return []

    distribution = {name: 0 for name, _ in _SIZE_BUCKETS}
    for pr in population:
        distribution[next(name for name, upper in _SIZE_BUCKETS if pr.lines_changed <= upper)] += 1
    size = report.pr_size
    corroborators = {
        "median_size_rising": size.current.status == OK
        and size.baseline.status == OK
        and (size.current.median_lines or 0) > (size.baseline.median_lines or 0),
        "cycle_time_rising": report.pr_cycle_time.current.status == OK
        and (report.pr_cycle_time.pct_change or 0) > 0,
    }
    largest_first = sorted(oversized, key=lambda pr: (-pr.lines_changed, pr.number))
    return [
        Finding(
            key=OVERSIZED_PULL_REQUESTS,
            rule=OVERSIZED_PULL_REQUESTS,
            rule_version=DETECTION_VERSION,
            severity=_severity(share, config.oversized_min_share, base=LOW),
            confidence=_confidence(
                n=len(population),
                n_min=config.oversized_min_sample,
                observed=share,
                threshold=config.oversized_min_share,
                corroborators=corroborators,
            ),
            title="A large share of pull requests are oversized",
            summary=(
                f"{len(oversized)} of {len(population)} recently merged or open PRs "
                f"({share:.0%}) change more than {threshold_lines} lines. Large PRs are slower "
                "and harder to review thoroughly."
            ),
            evidence={
                "sample_size": len(population),
                "merged_in_window": len(merged),
                "open_now": len(population) - len(merged),
                "oversized_count": len(oversized),
                "oversized_share": share,
                "threshold_share": config.oversized_min_share,
                "oversized_threshold_lines": threshold_lines,
                "median_lines": median(pr.lines_changed for pr in population),
                "distribution": distribution,
            },
            affected_items=tuple(
                AffectedItem(
                    kind=PULL_REQUEST,
                    number=pr.number,
                    detail={
                        "lines_changed": pr.lines_changed,
                        "additions": pr.additions,
                        "deletions": pr.deletions,
                        "changed_files": pr.changed_files,
                        "state": "merged" if pr.number in merged_numbers else "open",
                    },
                )
                for pr in largest_first
            ),
            metric_refs=("pr_size",),
        )
    ]


# --- MILESTONE_AT_RISK --------------------------------------------------------------------


def _milestone_finding(
    milestone: Milestone, at: datetime, config: DetectionConfig
) -> Finding | None:
    if milestone.state != "open" or milestone.due_on is None or milestone.open_issues <= 0:
        return None
    days_remaining = _days(milestone.due_on - at)
    if days_remaining > config.milestone_horizon_days:
        return None

    total = milestone.open_issues + milestone.closed_issues
    overdue = days_remaining <= 0
    observed_rate: float | None = None
    required_rate: float | None = None
    projected_days: float | None = None
    if not overdue:
        if milestone.created_at is None:
            return None  # pace is unknowable without a start date; never guess
        elapsed = max(1.0, _days(at - milestone.created_at))
        observed_rate = milestone.closed_issues / elapsed
        required_rate = milestone.open_issues / days_remaining
        if observed_rate >= required_rate:
            return None
        if observed_rate > 0:
            projected_days = milestone.open_issues / observed_rate

    # Ratio of the pace needed to the pace achieved; infinite if nothing has closed yet.
    if overdue or not observed_rate:
        shortfall = float("inf")
    else:
        assert required_rate is not None
        shortfall = required_rate / observed_rate
    corroborators = {
        "overdue": overdue,
        "majority_open": milestone.open_issues > milestone.closed_issues,
    }
    when = (
        f"was due {abs(days_remaining):.1f} days ago"
        if overdue
        else f"is due in {days_remaining:.1f} days"
    )
    pace = (
        ""
        if overdue
        else f" At the pace so far ({(observed_rate or 0) * 7:.1f} items/week) it needs "
        f"{(required_rate or 0) * 7:.1f} items/week to finish on time."
    )
    return Finding(
        key=f"{MILESTONE_AT_RISK}:{milestone.number}",
        rule=MILESTONE_AT_RISK,
        rule_version=DETECTION_VERSION,
        severity=HIGH if overdue or shortfall >= 2 else MEDIUM,
        confidence=_confidence(
            n=total,
            n_min=config.milestone_min_items,
            observed=shortfall,
            threshold=1.0,
            corroborators=corroborators,
        ),
        title=f"Milestone “{milestone.title}” is at risk",
        summary=(
            f"Milestone “{milestone.title}” {when} with {milestone.open_issues} of "
            f"{total} items still open.{pace}"
        ),
        evidence={
            "milestone_number": milestone.number,
            "title": milestone.title,
            "due_on": milestone.due_on.isoformat(),
            "days_remaining": round(days_remaining, 2),
            "overdue": overdue,
            "open_issues": milestone.open_issues,
            "closed_issues": milestone.closed_issues,
            "completion_share": round(milestone.closed_issues / total, 4),
            "observed_items_per_day": _round(observed_rate, 4),
            "required_items_per_day": _round(required_rate, 4),
            "projected_days_to_finish": _round(projected_days, 1),
            "horizon_days": config.milestone_horizon_days,
        },
        affected_items=(
            AffectedItem(
                kind=MILESTONE,
                number=milestone.number,
                detail={
                    "title": milestone.title,
                    "due_on": milestone.due_on.isoformat(),
                    "open_issues": milestone.open_issues,
                    "closed_issues": milestone.closed_issues,
                },
            ),
        ),
        metric_refs=("milestone_pace",),
    )


def _milestones_at_risk(
    report: MetricsReport, data: RepositoryData, windows: AnalysisWindows, config: DetectionConfig
) -> list[Finding]:
    findings = (_milestone_finding(m, windows.analysis_at, config) for m in data.milestones)
    return [f for f in findings if f is not None]


# --- Entry point --------------------------------------------------------------------------

Rule = Callable[[MetricsReport, RepositoryData, AnalysisWindows, DetectionConfig], list[Finding]]

RULES: tuple[Rule, ...] = (
    _review_bottleneck,
    _stale_pull_requests,
    _issue_backlog_growth,
    _oversized_pull_requests,
    _milestones_at_risk,
)


def rank_key(finding: Finding) -> tuple[int, float, int, str]:
    """Severity first, then confidence, then the fixed rule order -- fully deterministic."""
    return (
        SEVERITY_RANK[finding.severity],
        -finding.confidence.score,
        RULE_ORDER.index(finding.rule),
        finding.key,
    )


def detect_findings(
    report: MetricsReport,
    data: RepositoryData,
    windows: AnalysisWindows,
    config: DetectionConfig = DEFAULT_DETECTION_CONFIG,
) -> tuple[Finding, ...]:
    """Evaluate every rule and return findings ranked most-important first."""
    findings = [finding for rule in RULES for finding in rule(report, data, windows, config)]
    return tuple(sorted(findings, key=rank_key))
