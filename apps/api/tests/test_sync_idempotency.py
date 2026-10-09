import httpx
import respx
from sqlalchemy import func, select

from app.models.commit import Commit
from app.models.issue import Issue
from app.models.milestone import Milestone
from app.models.pull_request import PullRequest
from app.models.review import Review
from app.models.review_request import ReviewRequest
from app.models.sync_run import SyncRunStatus
from app.services.sync_service import SyncService

BASE = "https://api.github.com"


def _mock_github_repo_data() -> None:
    respx.get(f"{BASE}/repos/acme/backend/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "node_id": "PR_1",
                    "number": 1,
                    "title": "Add payment retry logic",
                    "draft": False,
                }
            ],
        )
    )
    respx.get(f"{BASE}/repos/acme/backend/pulls/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "node_id": "PR_1",
                "number": 1,
                "title": "Add payment retry logic",
                "user": {"login": "alice"},
                "draft": False,
                "created_at": "2026-09-20T09:00:00Z",
                "updated_at": "2026-09-20T15:00:00Z",
                "closed_at": None,
                "merged_at": "2026-09-20T15:00:00Z",
                "additions": 40,
                "deletions": 10,
                "changed_files": 3,
            },
        )
    )
    respx.get(f"{BASE}/repos/acme/backend/pulls/1/reviews").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "node_id": "RV_1",
                    "user": {"login": "rahul"},
                    "state": "APPROVED",
                    "submitted_at": "2026-09-20T13:00:00Z",
                }
            ],
        )
    )
    respx.get(f"{BASE}/repos/acme/backend/pulls/1/requested_reviewers").mock(
        return_value=httpx.Response(200, json={"users": [{"login": "priya"}], "teams": []})
    )
    respx.get(f"{BASE}/repos/acme/backend/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                # A PR-shaped entry (has `pull_request`) -- must be filtered out, not synced twice.
                {
                    "node_id": "PR_1",
                    "number": 1,
                    "title": "Add payment retry logic",
                    "pull_request": {"url": "..."},
                    "created_at": "2026-09-20T09:00:00Z",
                    "updated_at": "2026-09-20T15:00:00Z",
                    "closed_at": None,
                },
                # A genuine issue.
                {
                    "node_id": "I_1",
                    "number": 2,
                    "title": "Flaky payment webhook test",
                    "created_at": "2026-09-19T09:00:00Z",
                    "updated_at": "2026-09-19T10:00:00Z",
                    "closed_at": None,
                },
            ],
        )
    )
    respx.get(f"{BASE}/repos/acme/backend/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "node_id": "C_1",
                    "sha": "abc123",
                    "author": {"login": "alice"},
                    "commit": {
                        "author": {"date": "2026-09-20T08:00:00Z"},
                        "message": "Add retry logic",
                    },
                }
            ],
        )
    )
    respx.get(f"{BASE}/repos/acme/backend/milestones").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "node_id": "M_1",
                    "number": 1,
                    "title": "v1.0",
                    "state": "open",
                    "due_on": None,
                    "open_issues": 1,
                    "closed_issues": 0,
                }
            ],
        )
    )


@respx.mock
def test_sync_is_idempotent(db_session, seeded_repository) -> None:
    _mock_github_repo_data()
    service = SyncService(db_session)

    first_run = service.run(seeded_repository.id)
    assert first_run.status == SyncRunStatus.success.value
    assert first_run.stats == {
        "pull_requests": 1,
        "reviews": 1,
        "review_requests": 1,
        "issues": 1,
        "commits": 1,
        "milestones": 1,
    }

    def counts() -> dict[str, int]:
        return {
            "pull_requests": db_session.execute(select(func.count()).select_from(PullRequest)).
            scalar_one(),
            "reviews": db_session.execute(select(func.count()).select_from(Review)).scalar_one(),
            "review_requests": db_session.execute(
                select(func.count()).select_from(ReviewRequest)
            ).scalar_one(),
            "issues": db_session.execute(select(func.count()).select_from(Issue)).scalar_one(),
            "commits": db_session.execute(select(func.count()).select_from(Commit)).scalar_one(),
            "milestones": db_session.execute(select(func.count()).select_from(Milestone)).
            scalar_one(),
        }

    after_first = counts()
    assert after_first == {
        "pull_requests": 1,
        "reviews": 1,
        "review_requests": 1,
        "issues": 1,
        "commits": 1,
        "milestones": 1,
    }

    pr_id_after_first = db_session.execute(select(PullRequest.id)).scalar_one()

    second_run = service.run(seeded_repository.id)
    assert second_run.status == SyncRunStatus.success.value
    assert second_run.id != first_run.id  # a distinct sync_runs row per run

    after_second = counts()
    assert after_second == after_first, "re-running sync must not create duplicate rows"

    pr_id_after_second = db_session.execute(select(PullRequest.id)).scalar_one()
    assert pr_id_after_second == pr_id_after_first, (
        "the same PR must be updated in place, not re-inserted"
    )


@respx.mock
def test_failed_sync_surfaces_actionable_error(db_session, seeded_repository) -> None:
    respx.get(f"{BASE}/repos/acme/backend/pulls").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    service = SyncService(db_session)

    run = service.run(seeded_repository.id)

    assert run.status == SyncRunStatus.failed.value
    assert run.error_message is not None
    assert "404" in run.error_message
    assert run.finished_at is not None
