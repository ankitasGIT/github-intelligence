import logging
from uuid import UUID

from app.db.session import SessionLocal
from app.models.sync_run import SyncRunStatus
from app.services.metric_snapshot_service import MetricSnapshotService
from app.services.sync_service import SyncService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="sync_repository")
def sync_repository_task(repository_id: str, sync_run_id: str | None = None) -> str:
    session = SessionLocal()
    try:
        sync_run = SyncService(session).run(
            UUID(repository_id), UUID(sync_run_id) if sync_run_id else None
        )
        if sync_run.status == SyncRunStatus.success.value:
            # A failed snapshot must not mark an otherwise-successful sync as failed; it can be
            # recomputed at any time via POST /repositories/{id}/metrics/snapshots.
            try:
                MetricSnapshotService(session).create(UUID(repository_id))
            except Exception:
                session.rollback()
                logger.exception("Metric snapshot failed after sync of %s", repository_id)
        return sync_run.status
    finally:
        session.close()
