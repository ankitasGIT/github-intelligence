"""One call that produces everything a snapshot stores: metrics, health, findings,
recommendations, and a weekly trend. Each part keeps its own version string."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from analytics.detection import (
    DEFAULT_DETECTION_CONFIG,
    DETECTION_VERSION,
    DetectionConfig,
    Finding,
    detect_findings,
)
from analytics.health import HealthReport, compute_health
from analytics.metrics import (
    MetricsReport,
    compute_metrics,
    pr_cycle_time_sample,
    review_wait_sample,
)
from analytics.models import DEFAULT_CONFIG, AnalysisWindows, MetricConfig, RepositoryData, Window
from analytics.recommendations import Recommendation, recommend
from analytics.serialization import to_jsonable

TREND_WEEKS = 8


@dataclass(frozen=True)
class TrendPoint:
    """One 7-day window. The newest point is exactly the snapshot's current window."""

    start: datetime
    end: datetime
    cycle_time_median_hours: float | None
    cycle_time_n: int
    cycle_time_status: str
    review_wait_median_hours: float | None
    review_wait_n: int
    review_wait_status: str


@dataclass(frozen=True)
class AnalysisResult:
    metrics: MetricsReport
    health: HealthReport
    findings: tuple[Finding, ...]
    recommendations: tuple[Recommendation, ...]
    trend: tuple[TrendPoint, ...]
    detection_version: str = DETECTION_VERSION

    def trend_dict(self) -> list[dict[str, object]]:
        return to_jsonable(self.trend)  # type: ignore[no-any-return]


def trend_start(analysis_at: datetime, config: MetricConfig = DEFAULT_CONFIG) -> datetime:
    """Earliest instant the trend looks at -- callers loading data must reach back this far."""
    return analysis_at - timedelta(days=config.current_window_days * TREND_WEEKS)


def compute_trend(
    data: RepositoryData, windows: AnalysisWindows, config: MetricConfig = DEFAULT_CONFIG
) -> tuple[TrendPoint, ...]:
    """Cycle-time and review-wait medians per window, oldest first. Every point is computed
    "as of" the snapshot's `analysis_at`, matching the review-wait definition (a review may
    land after its window closes) and the no-future-data rule."""
    width = timedelta(days=config.current_window_days)
    points = []
    for k in reversed(range(TREND_WEEKS)):
        end = windows.analysis_at - k * width
        window = Window(end - width, end)
        cycle = pr_cycle_time_sample(data.pull_requests, window, config)
        wait = review_wait_sample(data.pull_requests, window, windows.analysis_at, config)
        points.append(
            TrendPoint(
                start=window.start,
                end=window.end,
                cycle_time_median_hours=cycle.median_hours,
                cycle_time_n=cycle.n,
                cycle_time_status=cycle.status,
                review_wait_median_hours=wait.median_hours,
                review_wait_n=wait.n,
                review_wait_status=wait.status,
            )
        )
    return tuple(points)


def run_analysis(
    data: RepositoryData,
    windows: AnalysisWindows,
    config: MetricConfig = DEFAULT_CONFIG,
    detection_config: DetectionConfig = DEFAULT_DETECTION_CONFIG,
) -> AnalysisResult:
    metrics = compute_metrics(data, windows, config)
    findings = detect_findings(metrics, data, windows, detection_config)
    return AnalysisResult(
        metrics=metrics,
        health=compute_health(metrics),
        findings=findings,
        recommendations=recommend(findings),
        trend=compute_trend(data, windows, config),
    )
