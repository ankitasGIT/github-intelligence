import uuid
from datetime import datetime

from analytics import MetricsReport
from pydantic import BaseModel

from app.models.metric_snapshot import MetricSnapshot


class AnalysisWindowRead(BaseModel):
    """Half-open intervals: `[current_start, current_end)` and `[baseline_start, baseline_end)`."""

    current_start: datetime
    current_end: datetime
    baseline_start: datetime
    baseline_end: datetime

    @classmethod
    def from_snapshot(cls, snapshot: MetricSnapshot) -> "AnalysisWindowRead":
        return cls(
            current_start=snapshot.window_start,
            current_end=snapshot.window_end,
            baseline_start=snapshot.baseline_start,
            baseline_end=snapshot.baseline_end,
        )


class DataCompletenessRead(BaseModel):
    has_synced: bool
    source_sync_run_id: uuid.UUID | None
    source_synced_at: datetime | None
    pull_requests_analyzed: int
    issues_analyzed: int

    @classmethod
    def from_snapshot(cls, snapshot: MetricSnapshot) -> "DataCompletenessRead":
        inputs = snapshot.inputs
        return cls(
            has_synced=inputs.get("source_sync_run_id") is not None,
            source_sync_run_id=inputs.get("source_sync_run_id"),
            source_synced_at=inputs.get("source_synced_at"),
            pull_requests_analyzed=inputs.get("pull_requests", 0),
            issues_analyzed=inputs.get("issues", 0),
        )


class MetricSnapshotRead(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    analysis_at: datetime
    computed_at: datetime
    analysis_version: str
    window: AnalysisWindowRead
    data_completeness: DataCompletenessRead
    # Typed straight from the analytics package so OpenAPI documents every metric's shape.
    # Each sample-based metric carries `status`: "ok" or "insufficient_data".
    metrics: MetricsReport
