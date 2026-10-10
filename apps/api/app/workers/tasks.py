from uuid import UUID

from app.db.session import SessionLocal
from app.services.sync_service import SyncService
from app.workers.celery_app import celery_app


@celery_app.task(name="sync_repository")
def sync_repository_task(repository_id: str, sync_run_id: str | None = None) -> str:
    session = SessionLocal()
    try:
        sync_run = SyncService(session).run(
            UUID(repository_id), UUID(sync_run_id) if sync_run_id else None
        )
        return sync_run.status
    finally:
        session.close()
