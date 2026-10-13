"""The health model: a documented, versioned score built from capped penalties on metrics.

Each of five dimensions starts at 100 and loses points for explicitly listed signals. A
dimension is only scored when at least one of its *primary* (sample-based) signals meets its
minimum sample; otherwise it is `insufficient_data` and contributes nothing. The overall score
is the weighted mean of scored dimensions (weights renormalized), and is itself only reported
when both delivery and reviews are scored. Formulas: docs/metric-definitions.md, "Health model".
"""

from collections.abc import Callable
from dataclasses import dataclass

from analytics.metrics import INSUFFICIENT_DATA, OK, MetricsReport
from analytics.serialization import to_jsonable

HEALTH_VERSION = "health-v1"

WEIGHTS = {
    "delivery": 0.30,
    "reviews": 0.25,
    "issues": 0.20,
    "pr_quality": 0.15,
    "activity": 0.10,
}
# The overall score is withheld unless these dimensions are scored.
REQUIRED_DIMENSIONS = ("delivery", "reviews")


@dataclass(frozen=True)
class Penalty:
    signal: str
    value: float | None
    points: float
    cap: float
    rule: str  # the formula, human-readable


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    weight: float
    score: float | None
    status: str
    penalties: tuple[Penalty, ...]
    signals_evaluated: tuple[str, ...]
    signals_insufficient: tuple[str, ...]


@dataclass(frozen=True)
class HealthReport:
    model_version: str
    overall: float | None
    status: str
    scored_weight: float
    dimensions: tuple[DimensionScore, ...]

    def to_dict(self) -> dict[str, object]:
        return to_jsonable(self)  # type: ignore[no-any-return]


def _rising(pct_change: float | None, per_point: float, cap: float) -> float:
    """Points for a metric that got worse by `pct_change` percent; improvements cost nothing."""
    return min(cap, max(0.0, (pct_change or 0.0) * per_point))


class _Builder:
    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        self.penalties: list[Penalty] = []
        self.evaluated: list[str] = []
        self.insufficient: list[str] = []
        self.primary_ok = False

    def signal(
        self,
        name: str,
        ok: bool,
        value: float | None,
        points: float,
        cap: float,
        rule: str,
        *,
        primary: bool,
    ) -> None:
        if not ok:
            self.insufficient.append(name)
            return
        self.evaluated.append(name)
        self.primary_ok = self.primary_ok or primary
        if points > 0:
            self.penalties.append(Penalty(name, value, round(float(points), 2), float(cap), rule))

    def build(self) -> DimensionScore:
        scored = self.primary_ok
        return DimensionScore(
            dimension=self.dimension,
            weight=WEIGHTS[self.dimension],
            score=round(max(0.0, 100.0 - sum(p.points for p in self.penalties)), 1)
            if scored
            else None,
            status=OK if scored else INSUFFICIENT_DATA,
            penalties=tuple(self.penalties) if scored else (),
            signals_evaluated=tuple(self.evaluated),
            signals_insufficient=tuple(self.insufficient),
        )


def _delivery(r: MetricsReport) -> DimensionScore:
    b = _Builder("delivery")
    cycle, merge = r.pr_cycle_time, r.merge_wait
    b.signal(
        "pr_cycle_time",
        cycle.current.status == OK and cycle.baseline.status == OK,
        cycle.pct_change,
        _rising(cycle.pct_change, 0.4, 40),
        40,
        "0.4 points per +1% cycle-time change vs baseline",
        primary=True,
    )
    b.signal(
        "merge_wait",
        merge.current.status == OK and merge.baseline.status == OK,
        merge.pct_change,
        _rising(merge.pct_change, 0.2, 20),
        20,
        "0.2 points per +1% merge-wait change vs baseline",
        primary=True,
    )
    return b.build()


def _reviews(r: MetricsReport) -> DimensionScore:
    b = _Builder("reviews")
    wait, concentration, stale = r.review_wait, r.review_concentration, r.stale_pull_requests
    b.signal(
        "review_wait",
        wait.current.status == OK and wait.baseline.status == OK,
        wait.pct_change,
        _rising(wait.pct_change, 0.4, 40),
        40,
        "0.4 points per +1% review-wait change vs baseline",
        primary=True,
    )
    b.signal(
        "review_concentration",
        concentration.status == OK,
        concentration.value,
        min(25.0, max(0.0, ((concentration.value or 0) - 0.5) * 100)),
        25,
        "1 point per percentage point of concentration above 50%",
        primary=False,
    )
    b.signal(
        "stale_pull_requests",
        True,
        stale.count,
        min(25.0, 5.0 * stale.count),
        25,
        "5 points per stale open PR",
        primary=False,
    )
    return b.build()


def _issues(r: MetricsReport) -> DimensionScore:
    b = _Builder("issues")
    growth, stale = r.issue_backlog_growth, r.stale_issues
    b.signal(
        "issue_backlog_growth",
        growth.status == OK,
        growth.pct_change,
        _rising(growth.pct_change, 0.8, 40),
        40,
        "0.8 points per +1% open-issue growth vs baseline",
        primary=True,
    )
    b.signal(
        "stale_issues",
        True,
        stale.count,
        min(30.0, 3.0 * stale.count),
        30,
        "3 points per stale open issue",
        primary=False,
    )
    return b.build()


def _pr_quality(r: MetricsReport) -> DimensionScore:
    b = _Builder("pr_quality")
    size = r.pr_size
    b.signal(
        "oversized_share",
        size.current.status == OK,
        size.oversized_share,
        min(50.0, (size.oversized_share or 0) * 100),
        50,
        "1 point per percentage point of merged PRs above the oversized threshold",
        primary=True,
    )
    return b.build()


def _activity(r: MetricsReport) -> DimensionScore:
    """Merge throughput: PRs merged this window vs. the baseline's rate scaled to the window."""
    b = _Builder("activity")
    size, config = r.pr_size, r.config
    expected = size.baseline.n * config.current_window_days / config.baseline_window_days
    pct = (size.current.n - expected) / expected * 100 if expected else None
    b.signal(
        "merge_throughput",
        size.baseline.status == OK and pct is not None,
        round(pct, 4) if pct is not None else None,
        min(50.0, max(0.0, -(pct or 0) * 0.5)),
        50,
        "0.5 points per -1% merged-PR throughput vs the baseline's weekly rate",
        primary=True,
    )
    return b.build()


DIMENSIONS: tuple[Callable[[MetricsReport], DimensionScore], ...] = (
    _delivery,
    _reviews,
    _issues,
    _pr_quality,
    _activity,
)


def compute_health(report: MetricsReport) -> HealthReport:
    dimensions = tuple(dimension(report) for dimension in DIMENSIONS)
    scored = [d for d in dimensions if d.score is not None]
    scored_weight = round(sum(d.weight for d in scored), 4)
    required_ok = all(
        d.status == OK for d in dimensions if d.dimension in REQUIRED_DIMENSIONS
    )
    overall = (
        round(sum(d.weight * (d.score or 0) for d in scored) / scored_weight, 1)
        if required_ok and scored_weight
        else None
    )
    return HealthReport(
        model_version=HEALTH_VERSION,
        overall=overall,
        status=OK if overall is not None else INSUFFICIENT_DATA,
        scored_weight=scored_weight,
        dimensions=dimensions,
    )
