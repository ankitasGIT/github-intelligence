"""The metrics engine: every metric in docs/metric-definitions.md, computed from plain inputs.

Each metric always computes its raw value (a median, a count, a share) and separately reports
whether the sample clears its display threshold via `meets_minimum_sample` / `status`. Callers
must never hide a raw value because the sample was small, and must never display one as
trustworthy when `status` is `insufficient_data`.

Section numbers (§N) refer to docs/metric-definitions.md.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from analytics.models import (
    DEFAULT_CONFIG,
    AnalysisWindows,
    Issue,
    MetricConfig,
    PullRequest,
    RepositoryData,
    Window,
)

# Bump whenever a formula, inclusion rule, or default threshold changes. Stored on every
# snapshot so historical numbers are never silently reinterpreted.
METRICS_VERSION = "metrics-v1"

OK = "ok"
INSUFFICIENT_DATA = "insufficient_data"


def _status(meets_minimum_sample: bool) -> str:
    return OK if meets_minimum_sample else INSUFFICIENT_DATA


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def _days(delta: timedelta) -> float:
    return delta.total_seconds() / 86400


def _pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return round((current - baseline) / baseline * 100, 4)


def _median(values: Sequence[float]) -> float | None:
    return median(values) if values else None


# --- Result types -------------------------------------------------------------------------


@dataclass(frozen=True)
class DurationSample:
    median_hours: float | None
    n: int
    meets_minimum_sample: bool
    status: str
    pr_numbers: tuple[int, ...]
    # Disclosed exclusions, never silently dropped.
    excluded_draft: int
    excluded_reopened: int
    excluded_no_approval: int | None = None  # merge-wait only


@dataclass(frozen=True)
class DurationMetric:
    current: DurationSample
    baseline: DurationSample
    pct_change: float | None


@dataclass(frozen=True)
class PendingReviews:
    count: int
    pr_numbers: tuple[int, ...]


@dataclass(frozen=True)
class ReviewConcentration:
    value: float | None
    top_reviewer: str | None
    top_reviewer_pending: int
    pending_with_requested_reviewer: int
    meets_minimum_sample: bool
    status: str


@dataclass(frozen=True)
class StaleItem:
    number: int
    idle_days: float


@dataclass(frozen=True)
class StaleItems:
    count: int
    threshold_days: int
    items: tuple[StaleItem, ...]


@dataclass(frozen=True)
class IssueBacklogGrowth:
    current_open_count: int
    baseline_open_count: int
    pct_change: float | None
    meets_minimum_sample: bool
    status: str


@dataclass(frozen=True)
class SizeSample:
    median_lines: float | None
    n: int
    meets_minimum_sample: bool
    status: str
    pr_numbers: tuple[int, ...]


@dataclass(frozen=True)
class PRSize:
    current: SizeSample
    baseline: SizeSample
    oversized_threshold_lines: int
    oversized_count: int
    oversized_share: float | None
    oversized_pr_numbers: tuple[int, ...]


@dataclass(frozen=True)
class MetricsReport:
    metrics_version: str
    analysis_at: datetime
    current_window: Window
    baseline_window: Window
    config: MetricConfig
    pr_cycle_time: DurationMetric
    review_wait: DurationMetric
    merge_wait: DurationMetric
    pending_review_count: PendingReviews
    review_concentration: ReviewConcentration
    stale_pull_requests: StaleItems
    stale_issues: StaleItems
    issue_backlog_growth: IssueBacklogGrowth
    pr_size: PRSize

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (datetimes as ISO-8601 strings, tuples as lists)."""
        return _jsonable(asdict(self))  # type: ignore[no-any-return]


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


# --- Shared helpers -------------------------------------------------------------------------


def _numbers(prs: Iterable[PullRequest]) -> tuple[int, ...]:
    return tuple(sorted(pr.number for pr in prs))


def _drafts_in(prs: Iterable[PullRequest], window: Window) -> int:
    """Still-draft PRs opened within the window -- excluded from every metric, so disclosed."""
    return sum(1 for pr in prs if pr.is_draft and window.contains(pr.reviewable_at))


def _duration_sample(
    hours: Sequence[float],
    included: Sequence[PullRequest],
    config: MetricConfig,
    *,
    excluded_draft: int,
    excluded_reopened: int,
    excluded_no_approval: int | None = None,
) -> DurationSample:
    meets = len(hours) >= config.min_sample
    return DurationSample(
        median_hours=_median(hours),
        n=len(hours),
        meets_minimum_sample=meets,
        status=_status(meets),
        pr_numbers=_numbers(included),
        excluded_draft=excluded_draft,
        excluded_reopened=excluded_reopened,
        excluded_no_approval=excluded_no_approval,
    )


def _merged_in(prs: Iterable[PullRequest], window: Window) -> list[PullRequest]:
    return [pr for pr in prs if not pr.is_draft and window.contains(pr.merged_at)]


# --- §1 PR cycle time ---------------------------------------------------------------------


def pr_cycle_time_sample(
    prs: Sequence[PullRequest], window: Window, config: MetricConfig = DEFAULT_CONFIG
) -> DurationSample:
    """`merged_at - reviewable_at` for non-draft, non-reopened PRs merged within the window."""
    merged = _merged_in(prs, window)
    included = [pr for pr in merged if not pr.reopened]
    hours = [_hours(pr.merged_at - pr.reviewable_at) for pr in included if pr.merged_at]
    return _duration_sample(
        hours,
        included,
        config,
        excluded_draft=_drafts_in(prs, window),
        excluded_reopened=len(merged) - len(included),
    )


# --- §2 Review-wait time ------------------------------------------------------------------


def review_wait_sample(
    prs: Sequence[PullRequest],
    window: Window,
    analysis_at: datetime,
    config: MetricConfig = DEFAULT_CONFIG,
) -> DurationSample:
    """`first_review_at - reviewable_at` for PRs that became reviewable within the window and
    have at least one review submitted by `analysis_at` (the review may land after the window
    closes). A review left while the PR was still a draft counts as zero wait, not negative."""
    opened = [pr for pr in prs if not pr.is_draft and window.contains(pr.reviewable_at)]
    not_reopened = [pr for pr in opened if not pr.reopened]
    included: list[PullRequest] = []
    hours: list[float] = []
    for pr in not_reopened:
        first_review = pr.first_review_at(analysis_at)
        if first_review is None:
            continue  # zero reviews so far: counted in pending-review count instead (§4)
        included.append(pr)
        hours.append(max(0.0, _hours(first_review - pr.reviewable_at)))
    return _duration_sample(
        hours,
        included,
        config,
        excluded_draft=_drafts_in(prs, window),
        excluded_reopened=len(opened) - len(not_reopened),
    )


# --- §3 Merge-wait time -------------------------------------------------------------------


def merge_wait_sample(
    prs: Sequence[PullRequest], window: Window, config: MetricConfig = DEFAULT_CONFIG
) -> DurationSample:
    """`merged_at - first_approved_at` for PRs merged within the window. PRs merged without any
    prior approval are excluded and counted in `excluded_no_approval`."""
    merged = _merged_in(prs, window)
    not_reopened = [pr for pr in merged if not pr.reopened]
    included: list[PullRequest] = []
    hours: list[float] = []
    no_approval = 0
    for pr in not_reopened:
        assert pr.merged_at is not None
        first_approval = pr.first_approved_at(pr.merged_at)
        if first_approval is None:
            no_approval += 1
            continue
        included.append(pr)
        hours.append(_hours(pr.merged_at - first_approval))
    return _duration_sample(
        hours,
        included,
        config,
        excluded_draft=_drafts_in(prs, window),
        excluded_reopened=len(merged) - len(not_reopened),
        excluded_no_approval=no_approval,
    )


def _compare(current: DurationSample, baseline: DurationSample) -> DurationMetric:
    return DurationMetric(
        current=current,
        baseline=baseline,
        pct_change=_pct_change(current.median_hours, baseline.median_hours),
    )


# --- §4 Pending-review count --------------------------------------------------------------


def _pending_prs(prs: Iterable[PullRequest], analysis_at: datetime) -> list[PullRequest]:
    pending = []
    for pr in prs:
        if pr.is_draft or not pr.is_open_at(analysis_at):
            continue
        reviewed_since_update = any(
            pr.updated_at < r.submitted_at <= analysis_at for r in pr.reviews
        )
        if not reviewed_since_update:
            pending.append(pr)
    return pending


def pending_reviews(prs: Sequence[PullRequest], analysis_at: datetime) -> PendingReviews:
    """Point-in-time: open, non-draft PRs with no review submitted after their last update.
    Not windowed by creation date. Always displayed -- zero is a meaningful answer."""
    pending = _pending_prs(prs, analysis_at)
    return PendingReviews(count=len(pending), pr_numbers=_numbers(pending))


# --- §5 Review concentration --------------------------------------------------------------


def review_concentration(
    prs: Sequence[PullRequest], analysis_at: datetime, config: MetricConfig = DEFAULT_CONFIG
) -> ReviewConcentration:
    """Share of pending PRs (with at least one requested reviewer) that request the single
    most-requested reviewer. Ties break alphabetically so the result is deterministic."""
    with_request = [pr for pr in _pending_prs(prs, analysis_at) if pr.requested_reviewers]
    counts = Counter(login for pr in with_request for login in set(pr.requested_reviewers))
    denominator = len(with_request)
    meets = denominator >= config.concentration_min_pending
    if not counts:
        return ReviewConcentration(None, None, 0, 0, meets, _status(meets))
    top_reviewer, top_count = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return ReviewConcentration(
        value=round(top_count / denominator, 4),
        top_reviewer=top_reviewer,
        top_reviewer_pending=top_count,
        pending_with_requested_reviewer=denominator,
        meets_minimum_sample=meets,
        status=_status(meets),
    )


# --- §6 Stale PRs / issues ----------------------------------------------------------------


def _stale(
    items: Iterable[PullRequest | Issue], analysis_at: datetime, config: MetricConfig
) -> StaleItems:
    threshold = timedelta(days=config.staleness_threshold_days)
    stale = sorted(
        (
            StaleItem(number=item.number, idle_days=round(_days(analysis_at - item.updated_at), 2))
            for item in items
            if analysis_at - item.updated_at >= threshold
        ),
        key=lambda s: s.number,
    )
    return StaleItems(
        count=len(stale), threshold_days=config.staleness_threshold_days, items=tuple(stale)
    )


def stale_pull_requests(
    prs: Sequence[PullRequest], analysis_at: datetime, config: MetricConfig = DEFAULT_CONFIG
) -> StaleItems:
    """Open, non-draft PRs idle (by `updated_at`) for at least the staleness threshold."""
    candidates = [pr for pr in prs if not pr.is_draft and pr.is_open_at(analysis_at)]
    return _stale(candidates, analysis_at, config)


def stale_issues(
    issues: Sequence[Issue], analysis_at: datetime, config: MetricConfig = DEFAULT_CONFIG
) -> StaleItems:
    return _stale([i for i in issues if i.is_open_at(analysis_at)], analysis_at, config)


# --- §7 Issue backlog growth --------------------------------------------------------------


def issue_backlog_growth(
    issues: Sequence[Issue], windows: AnalysisWindows, config: MetricConfig = DEFAULT_CONFIG
) -> IssueBacklogGrowth:
    """Open-issue count at the end of the current window vs. at the end of the baseline."""
    current_open = sum(1 for i in issues if i.is_open_at(windows.current.end))
    baseline_open = sum(1 for i in issues if i.is_open_at(windows.baseline.end))
    meets = baseline_open >= config.backlog_min_baseline_open
    return IssueBacklogGrowth(
        current_open_count=current_open,
        baseline_open_count=baseline_open,
        pct_change=_pct_change(current_open, baseline_open),
        meets_minimum_sample=meets,
        status=_status(meets),
    )


# --- §8 PR size ---------------------------------------------------------------------------


def _size_sample(merged: Sequence[PullRequest], config: MetricConfig) -> SizeSample:
    meets = len(merged) >= config.min_sample
    return SizeSample(
        median_lines=_median([pr.lines_changed for pr in merged]),
        n=len(merged),
        meets_minimum_sample=meets,
        status=_status(meets),
        pr_numbers=_numbers(merged),
    )


def pr_size(
    prs: Sequence[PullRequest], windows: AnalysisWindows, config: MetricConfig = DEFAULT_CONFIG
) -> PRSize:
    """`additions + deletions` for non-draft PRs merged in the window. Reopened PRs are
    included -- size is a line count, not a duration. Oversized = strictly above threshold."""
    current = _merged_in(prs, windows.current)
    oversized = [pr for pr in current if pr.lines_changed > config.oversized_threshold_lines]
    return PRSize(
        current=_size_sample(current, config),
        baseline=_size_sample(_merged_in(prs, windows.baseline), config),
        oversized_threshold_lines=config.oversized_threshold_lines,
        oversized_count=len(oversized),
        oversized_share=round(len(oversized) / len(current), 4) if current else None,
        oversized_pr_numbers=_numbers(oversized),
    )


# --- Entry point --------------------------------------------------------------------------


def compute_metrics(
    data: RepositoryData, windows: AnalysisWindows, config: MetricConfig = DEFAULT_CONFIG
) -> MetricsReport:
    prs = data.pull_requests
    at = windows.analysis_at
    return MetricsReport(
        metrics_version=METRICS_VERSION,
        analysis_at=at,
        current_window=windows.current,
        baseline_window=windows.baseline,
        config=config,
        pr_cycle_time=_compare(
            pr_cycle_time_sample(prs, windows.current, config),
            pr_cycle_time_sample(prs, windows.baseline, config),
        ),
        review_wait=_compare(
            review_wait_sample(prs, windows.current, at, config),
            review_wait_sample(prs, windows.baseline, at, config),
        ),
        merge_wait=_compare(
            merge_wait_sample(prs, windows.current, config),
            merge_wait_sample(prs, windows.baseline, config),
        ),
        pending_review_count=pending_reviews(prs, at),
        review_concentration=review_concentration(prs, at, config),
        stale_pull_requests=stale_pull_requests(prs, at, config),
        stale_issues=stale_issues(data.issues, at, config),
        issue_backlog_growth=issue_backlog_growth(data.issues, windows, config),
        pr_size=pr_size(prs, windows, config),
    )
