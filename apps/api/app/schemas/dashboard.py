import uuid
from datetime import datetime

from analytics import HealthReport, MetricsReport, TrendPoint
from pydantic import BaseModel

from app.schemas.finding import FindingRead, RecommendationRead
from app.schemas.metric_snapshot import AnalysisWindowRead, DataCompletenessRead
from app.schemas.repository import RepositoryRead
from app.schemas.sync_run import SyncRunRead


class QueueItemRead(BaseModel):
    """An open PR in the pending-review queue (§4), oldest first."""

    number: int
    title: str
    url: str
    author: str | None
    age_days: float
    idle_days: float
    requested_reviewers: list[str]


class DashboardAnalysisRead(BaseModel):
    snapshot_id: uuid.UUID
    analysis_at: datetime
    computed_at: datetime
    analysis_version: str
    detection_version: str | None
    health_version: str | None
    window: AnalysisWindowRead
    data_completeness: DataCompletenessRead
    # Null on snapshots computed before the health model existed.
    health: HealthReport | None
    metrics: MetricsReport
    findings: list[FindingRead]
    recommendations: list[RecommendationRead]
    trend: list[TrendPoint]
    pending_queue: list[QueueItemRead]


class DashboardRead(BaseModel):
    repository: RepositoryRead
    latest_sync: SyncRunRead | None
    # Null until the first snapshot exists: the UI shows sync status instead.
    analysis: DashboardAnalysisRead | None
