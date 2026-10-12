import uuid
from datetime import datetime

from analytics import MetricsReport
from pydantic import BaseModel


class AnalysisWindowRead(BaseModel):
    """Half-open intervals: `[current_start, current_end)` and `[baseline_start, baseline_end)`."""

    current_start: datetime
    current_end: datetime
    baseline_start: datetime
    baseline_end: datetime


class DataCompletenessRead(BaseModel):
    has_synced: bool
    source_sync_run_id: uuid.UUID | None
    source_synced_at: datetime | None
    pull_requests_analyzed: int
    issues_analyzed: int


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
