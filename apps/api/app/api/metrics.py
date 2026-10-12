from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.metric_snapshot import MetricSnapshot
from app.models.repository import Repository
from app.schemas.metric_snapshot import (
    AnalysisWindowRead,
    DataCompletenessRead,
    MetricSnapshotRead,
)
from app.services.metric_snapshot_service import MetricSnapshotService

router = APIRouter(prefix="/repositories", tags=["metrics"])


def _to_read(snapshot: MetricSnapshot) -> MetricSnapshotRead:
    inputs = snapshot.inputs
    return MetricSnapshotRead.model_validate(
        {
            "id": snapshot.id,
            "repository_id": snapshot.repository_id,
            "analysis_at": snapshot.analysis_at,
            "computed_at": snapshot.computed_at,
            "analysis_version": snapshot.model_version,
            "window": AnalysisWindowRead(
                current_start=snapshot.window_start,
                current_end=snapshot.window_end,
                baseline_start=snapshot.baseline_start,
                baseline_end=snapshot.baseline_end,
            ),
            "data_completeness": DataCompletenessRead(
                has_synced=inputs.get("source_sync_run_id") is not None,
                source_sync_run_id=inputs.get("source_sync_run_id"),
                source_synced_at=inputs.get("source_synced_at"),
                pull_requests_analyzed=inputs.get("pull_requests", 0),
                issues_analyzed=inputs.get("issues", 0),
            ),
            "metrics": snapshot.metrics,
        }
    )


def _require_repository(db: Session, repository_id: UUID) -> None:
    if db.get(Repository, repository_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")


@router.get("/{repository_id}/metrics", response_model=MetricSnapshotRead)
def get_latest_metrics(repository_id: UUID, db: Session = Depends(get_db)) -> MetricSnapshotRead:
    """The most recent metric snapshot. Read-only: never recomputes."""
    _require_repository(db, repository_id)
    snapshot = MetricSnapshotService(db).latest(repository_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No metric snapshot yet. Run a sync, or POST "
            f"/repositories/{repository_id}/metrics/snapshots to compute one.",
        )
    return _to_read(snapshot)


@router.post(
    "/{repository_id}/metrics/snapshots", response_model=MetricSnapshotRead, status_code=201
)
def create_metric_snapshot(
    repository_id: UUID,
    analysis_at: datetime | None = Query(
        default=None,
        description="Compute metrics as of this instant (UTC; naive values are treated as "
        "UTC). Defaults to now. Must not be in the future.",
    ),
    db: Session = Depends(get_db),
) -> MetricSnapshotRead:
    _require_repository(db, repository_id)
    if analysis_at is not None:
        if analysis_at.tzinfo is None:
            analysis_at = analysis_at.replace(tzinfo=UTC)
        if analysis_at > datetime.now(UTC):
            raise HTTPException(status_code=422, detail="analysis_at must not be in the future")
    snapshot = MetricSnapshotService(db).create(repository_id, analysis_at)
    return _to_read(snapshot)
