from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.repository import Repository
from app.models.sync_run import SyncRun, SyncRunStatus
from app.schemas.repository import RepositoryCreate, RepositoryRead
from app.schemas.sync_run import SyncRunRead
from app.services.connection_service import get_or_create_connection
from app.services.github_client import GitHubAPIError, GitHubClient
from app.workers.tasks import sync_repository_task

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=list[RepositoryRead])
def list_repositories(db: Session = Depends(get_db)) -> list[Repository]:
    return list(db.execute(select(Repository)).scalars())


@router.post("", response_model=RepositoryRead, status_code=201)
def create_repository(payload: RepositoryCreate, db: Session = Depends(get_db)) -> Repository:
    settings = get_settings()
    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN is not configured")

    connection = get_or_create_connection(db, settings.github_token)

    try:
        with GitHubClient(token=settings.github_token) as client:
            gh_repo = client.get_repository(payload.owner, payload.name)
    except GitHubAPIError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not fetch {payload.owner}/{payload.name} from GitHub: {exc}",
        ) from exc

    existing = db.execute(
        select(Repository).where(Repository.github_node_id == gh_repo["node_id"])
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    repository = Repository(
        github_connection_id=connection.id,
        github_node_id=gh_repo["node_id"],
        owner=payload.owner,
        name=payload.name,
        full_name=gh_repo["full_name"],
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


@router.post("/{repository_id}/sync", response_model=SyncRunRead, status_code=202)
def trigger_sync(repository_id: UUID, db: Session = Depends(get_db)) -> SyncRun:
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    sync_run = SyncRun(repository_id=repository_id, status=SyncRunStatus.pending.value)
    db.add(sync_run)
    db.commit()
    db.refresh(sync_run)

    sync_repository_task.delay(str(repository_id), str(sync_run.id))
    return sync_run


@router.get("/{repository_id}/sync-runs", response_model=list[SyncRunRead])
def list_sync_runs(repository_id: UUID, db: Session = Depends(get_db)) -> list[SyncRun]:
    stmt = (
        select(SyncRun)
        .where(SyncRun.repository_id == repository_id)
        .order_by(SyncRun.created_at.desc())
    )
    return list(db.execute(stmt).scalars())
