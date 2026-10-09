"""Orchestrates a full historical sync for one repository: fetch from GitHub, normalize,
upsert, record a sync_runs row. Idempotent -- safe to re-run at any time."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import decrypt_token
from app.models.repository import Repository
from app.models.sync_run import SyncRun, SyncRunStatus
from app.services import normalizers
from app.services.github_client import GitHubAPIError, GitHubClient, GitHubRateLimitError


class SyncService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, repository_id: UUID, sync_run_id: UUID | None = None) -> SyncRun:
        repository = self.session.get(Repository, repository_id)
        if repository is None:
            raise ValueError(f"Repository {repository_id} not found")

        sync_run = self._start_run(repository_id, sync_run_id)
        token = decrypt_token(repository.github_connection.encrypted_token)

        stats = {
            "pull_requests": 0,
            "reviews": 0,
            "review_requests": 0,
            "issues": 0,
            "commits": 0,
            "milestones": 0,
        }
        try:
            with GitHubClient(token=token) as client:
                self._sync_pull_requests(client, repository, stats)
                self._sync_issues(client, repository, stats)
                self._sync_commits(client, repository, stats)
                self._sync_milestones(client, repository, stats)
            sync_run.status = SyncRunStatus.success.value
            sync_run.stats = stats
        except (GitHubRateLimitError, GitHubAPIError) as exc:
            self.session.rollback()
            self.session.add(sync_run)
            sync_run.status = SyncRunStatus.failed.value
            sync_run.error_message = str(exc)
        except Exception as exc:  # surface any unexpected failure on the run instead of a bare 500
            self.session.rollback()
            self.session.add(sync_run)
            sync_run.status = SyncRunStatus.failed.value
            sync_run.error_message = f"Unexpected error: {exc}"
        finally:
            sync_run.finished_at = datetime.now(UTC)
            self.session.add(sync_run)
            self.session.commit()

        return sync_run

    def _start_run(self, repository_id: UUID, sync_run_id: UUID | None) -> SyncRun:
        if sync_run_id is not None:
            sync_run = self.session.get(SyncRun, sync_run_id)
            if sync_run is None:
                raise ValueError(f"SyncRun {sync_run_id} not found")
        else:
            sync_run = SyncRun(repository_id=repository_id)
            self.session.add(sync_run)
        sync_run.status = SyncRunStatus.running.value
        sync_run.started_at = datetime.now(UTC)
        self.session.commit()
        return sync_run

    def _sync_pull_requests(
        self, client: GitHubClient, repository: Repository, stats: dict
    ) -> None:
        owner, name = repository.owner, repository.name
        for pr in client.list_pull_requests(owner, name):
            detail = client.get_pull_request(owner, name, pr["number"])
            pr_id = normalizers.upsert_pull_request(self.session, repository.id, detail)
            stats["pull_requests"] += 1

            for review in client.list_reviews(owner, name, pr["number"]):
                normalizers.upsert_review(self.session, pr_id, review)
                stats["reviews"] += 1

            requested = client.list_requested_reviewers(owner, name, pr["number"])
            current_logins = {u["login"] for u in requested.get("users", [])}
            for login in current_logins:
                normalizers.upsert_review_request(self.session, pr_id, login)
                stats["review_requests"] += 1
            normalizers.resolve_stale_review_requests(self.session, pr_id, current_logins)

    def _sync_issues(self, client: GitHubClient, repository: Repository, stats: dict) -> None:
        for issue in client.list_issues(repository.owner, repository.name):
            if "pull_request" in issue:
                continue  # GitHub's /issues endpoint also returns PRs; those are synced above
            normalizers.upsert_issue(self.session, repository.id, issue)
            stats["issues"] += 1

    def _sync_commits(self, client: GitHubClient, repository: Repository, stats: dict) -> None:
        for commit in client.list_commits(repository.owner, repository.name):
            normalizers.upsert_commit(self.session, repository.id, commit)
            stats["commits"] += 1

    def _sync_milestones(self, client: GitHubClient, repository: Repository, stats: dict) -> None:
        for milestone in client.list_milestones(repository.owner, repository.name):
            normalizers.upsert_milestone(self.session, repository.id, milestone)
            stats["milestones"] += 1
