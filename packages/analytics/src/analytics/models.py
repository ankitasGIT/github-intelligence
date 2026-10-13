"""Plain input models the analytics engine operates on.

These are deliberately independent of the API's SQLAlchemy models: the API maps its rows into
these before calling the engine, and tests build them straight from the JSON fixtures. Every
timestamp must be timezone-aware UTC (see docs/metric-definitions.md, "Conventions").
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

APPROVED = "APPROVED"


@dataclass(frozen=True)
class Review:
    reviewer: str
    state: str
    submitted_at: datetime


@dataclass(frozen=True)
class PullRequest:
    number: int
    created_at: datetime
    updated_at: datetime
    is_draft: bool = False
    ready_for_review_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    reopened: bool = False
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    reviews: tuple[Review, ...] = ()
    # Outstanding review requests only -- feeds review concentration (§5).
    requested_reviewers: tuple[str, ...] = ()

    @property
    def reviewable_at(self) -> datetime:
        """When the PR became reviewable: `ready_for_review_at` for an ex-draft, else creation.
        This stands in for `created_at` in every duration formula and window filter."""
        return self.ready_for_review_at or self.created_at

    @property
    def lines_changed(self) -> int:
        return self.additions + self.deletions

    def is_open_at(self, as_of: datetime) -> bool:
        if self.created_at > as_of:
            return False
        ended_at = self.merged_at or self.closed_at
        return ended_at is None or ended_at > as_of

    def first_review_at(self, as_of: datetime) -> datetime | None:
        """Earliest review of any state submitted no later than `as_of`."""
        times = [r.submitted_at for r in self.reviews if r.submitted_at <= as_of]
        return min(times, default=None)

    def first_approved_at(self, as_of: datetime) -> datetime | None:
        times = [
            r.submitted_at for r in self.reviews if r.state == APPROVED and r.submitted_at <= as_of
        ]
        return min(times, default=None)


@dataclass(frozen=True)
class Issue:
    number: int
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None

    def is_open_at(self, as_of: datetime) -> bool:
        return self.created_at <= as_of and (self.closed_at is None or self.closed_at > as_of)


@dataclass(frozen=True)
class Milestone:
    """Current-state milestone counts as last synced -- GitHub reports open/closed issue totals,
    not their history, so milestone pace is measured since `created_at` (see MILESTONE_AT_RISK)."""

    number: int
    title: str
    state: str
    due_on: datetime | None
    open_issues: int
    closed_issues: int
    created_at: datetime | None = None


@dataclass(frozen=True)
class RepositoryData:
    pull_requests: tuple[PullRequest, ...] = ()
    issues: tuple[Issue, ...] = ()
    milestones: tuple[Milestone, ...] = ()


@dataclass(frozen=True)
class Window:
    """Half-open interval `[start, end)` -- inclusive lower bound, exclusive upper bound."""

    start: datetime
    end: datetime

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.start <= moment < self.end


@dataclass(frozen=True)
class AnalysisWindows:
    analysis_at: datetime
    current: Window
    baseline: Window

    @classmethod
    def ending_at(
        cls, analysis_at: datetime, current_days: int = 7, baseline_days: int = 30
    ) -> "AnalysisWindows":
        if analysis_at.tzinfo is None:
            raise ValueError("analysis_at must be timezone-aware (UTC)")
        current_start = analysis_at - timedelta(days=current_days)
        return cls(
            analysis_at=analysis_at,
            current=Window(current_start, analysis_at),
            baseline=Window(current_start - timedelta(days=baseline_days), current_start),
        )


@dataclass(frozen=True)
class MetricConfig:
    """Every tunable threshold from docs/metric-definitions.md, in one versioned place."""

    min_sample: int = 5
    staleness_threshold_days: int = 5
    oversized_threshold_lines: int = 400
    concentration_min_pending: int = 3
    backlog_min_baseline_open: int = 5
    current_window_days: int = 7
    baseline_window_days: int = 30


DEFAULT_CONFIG = MetricConfig()

