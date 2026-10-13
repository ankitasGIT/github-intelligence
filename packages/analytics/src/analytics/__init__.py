"""Pure analytics domain logic for GitHub Engineering Intelligence. No web or database imports."""

from analytics.analysis import AnalysisResult, TrendPoint, run_analysis, trend_start
from analytics.definitions import METRIC_DEFINITIONS, RULE_DEFINITIONS, Definition
from analytics.detection import (
    DEFAULT_DETECTION_CONFIG,
    DETECTION_VERSION,
    AffectedItem,
    Confidence,
    DetectionConfig,
    Finding,
    detect_findings,
)
from analytics.health import HEALTH_VERSION, HealthReport, compute_health
from analytics.metrics import METRICS_VERSION, MetricsReport, compute_metrics
from analytics.models import (
    DEFAULT_CONFIG,
    AnalysisWindows,
    Issue,
    MetricConfig,
    Milestone,
    PullRequest,
    RepositoryData,
    Review,
    Window,
)
from analytics.recommendations import Recommendation, recommend

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_DETECTION_CONFIG",
    "DETECTION_VERSION",
    "HEALTH_VERSION",
    "METRICS_VERSION",
    "METRIC_DEFINITIONS",
    "RULE_DEFINITIONS",
    "AffectedItem",
    "AnalysisResult",
    "AnalysisWindows",
    "Confidence",
    "Definition",
    "DetectionConfig",
    "Finding",
    "HealthReport",
    "Issue",
    "MetricConfig",
    "MetricsReport",
    "Milestone",
    "PullRequest",
    "Recommendation",
    "RepositoryData",
    "Review",
    "TrendPoint",
    "Window",
    "compute_health",
    "compute_metrics",
    "detect_findings",
    "recommend",
    "run_analysis",
    "trend_start",
]
