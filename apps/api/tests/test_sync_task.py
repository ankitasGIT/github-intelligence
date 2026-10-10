import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.metric_snapshot import MetricSnapshot
from app.models.repository import Repository
from app.models.sync_run import SyncRun, SyncRunStatus
from app.workers import tasks


@pytest.fixture
def fake_sync(monkeypatch: pytest.MonkeyPatch, db_session: Session):
    """Runs the task against the test session with GitHub sync stubbed to a given status."""

    def install(status: SyncRunStatus) -> None:
        def run(self, repository_id, sync_run_id=None):  # noqa: ANN001
            sync_run = SyncRun(repository_id=repository_id, status=status.value)
            db_session.add(sync_run)
            db_session.commit()
            return sync_run

        monkeypatch.setattr(tasks.SyncService, "run", run)
        monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)

    return install


def _snapshot_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(MetricSnapshot)).scalar_one()


def test_successful_sync_creates_metric_snapshot(
    fake_sync, db_session: Session, seeded_repository: Repository
) -> None:
    fake_sync(SyncRunStatus.success)
    assert tasks.sync_repository_task(str(seeded_repository.id)) == "success"
    assert _snapshot_count(db_session) == 1


def test_failed_sync_creates_no_snapshot(
    fake_sync, db_session: Session, seeded_repository: Repository
) -> None:
    fake_sync(SyncRunStatus.failed)
    assert tasks.sync_repository_task(str(seeded_repository.id)) == "failed"
    assert _snapshot_count(db_session) == 0


def test_snapshot_error_does_not_fail_the_sync(
    fake_sync, monkeypatch: pytest.MonkeyPatch, db_session: Session, seeded_repository: Repository
) -> None:
    fake_sync(SyncRunStatus.success)

    def boom(self, repository_id):  # noqa: ANN001
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(tasks.MetricSnapshotService, "create", boom)
    assert tasks.sync_repository_task(str(seeded_repository.id)) == "success"
