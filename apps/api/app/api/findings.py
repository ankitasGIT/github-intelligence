from typing import Literal
from uuid import UUID

from analytics import METRIC_DEFINITIONS, RULE_DEFINITIONS
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.metric_snapshot import MetricSnapshot
from app.models.repository import Repository
from app.schemas.dashboard import DashboardRead
from app.schemas.finding import (
    DefinitionsRead,
    FindingDetailRead,
    FindingRead,
    RecommendationRead,
)
from app.services.findings_service import FindingsService, definition_read
from app.services.metric_snapshot_service import MetricSnapshotService

router = APIRouter(tags=["findings"])


def _repository(db: Session, repository_id: UUID) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repository


def _latest_snapshot(db: Session, repository_id: UUID) -> MetricSnapshot:
    snapshot = MetricSnapshotService(db).latest(repository_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No analysis yet. Run a sync, or POST "
            f"/repositories/{repository_id}/metrics/snapshots to compute one.",
        )
    return snapshot


@router.get("/repositories/{repository_id}/dashboard", response_model=DashboardRead)
def get_dashboard(
    repository_id: UUID,
    window: Literal["7d"] = Query(
        default="7d", description="Current window. Only 7d (vs. the preceding 30d) in the MVP."
    ),
    db: Session = Depends(get_db),
) -> DashboardRead:
    """Everything the first page load needs, from the latest snapshot. `analysis` is null until
    the first snapshot exists, so the UI can show sync status instead."""
    return FindingsService(db).dashboard(_repository(db, repository_id))


@router.get("/repositories/{repository_id}/findings", response_model=list[FindingRead])
def list_findings(repository_id: UUID, db: Session = Depends(get_db)) -> list[FindingRead]:
    """Findings from the latest snapshot, most important first."""
    repository = _repository(db, repository_id)
    return FindingsService(db).findings_for(repository, _latest_snapshot(db, repository_id))


@router.get(
    "/repositories/{repository_id}/findings/{finding_id}", response_model=FindingDetailRead
)
def get_finding(
    repository_id: UUID, finding_id: UUID, db: Session = Depends(get_db)
) -> FindingDetailRead:
    """One finding with its evidence, source items, recommendations, and definitions. Works for
    findings from older snapshots too, so a shared link keeps pointing at the same evidence."""
    finding = FindingsService(db).finding_detail(_repository(db, repository_id), finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


@router.get(
    "/repositories/{repository_id}/recommendations", response_model=list[RecommendationRead]
)
def list_recommendations(
    repository_id: UUID, db: Session = Depends(get_db)
) -> list[RecommendationRead]:
    """Recommended actions from the latest snapshot, in the order to do them."""
    _repository(db, repository_id)
    return FindingsService(db).recommendations_for(_latest_snapshot(db, repository_id))


@router.get("/metric-definitions", response_model=DefinitionsRead)
def get_definitions() -> DefinitionsRead:
    return DefinitionsRead(
        metrics=[definition_read(d) for d in METRIC_DEFINITIONS.values()],
        rules=[definition_read(d) for d in RULE_DEFINITIONS.values()],
    )
