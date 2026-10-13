import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MetricSnapshot(Base):
    """One immutable run of the analytics engine for a repository "as of" `analysis_at`.
    Never updated after insert: a changed formula produces a new snapshot with a new
    `model_version`, so historical numbers are never silently reinterpreted."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_repo_analysis_at", "repository_id", "analysis_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False
    )
    analysis_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    # Provenance: thresholds used, input row counts, and the sync run the data came from.
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The full analytics.MetricsReport, serialized.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Phase 4 additions; null on snapshots computed before detection/health existed.
    detection_version: Mapped[str | None] = mapped_column(String, nullable=True)
    health_version: Mapped[str | None] = mapped_column(String, nullable=True)
    # analytics.HealthReport and the weekly trend series, serialized.
    health: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    trend: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
