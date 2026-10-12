"""Phase 3 acceptance: for every fixture timeline, seeding it into Postgres and computing a
snapshot through the API reproduces the hand-calculated values in tests/fixtures/*.json."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Review
from app.models.review_request import ReviewRequest
from app.models.sync_run import SyncRun, SyncRunStatus

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
FIXTURE_NAMES = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _seed(session: Session, repository: Repository, fixture: dict[str, Any]) -> None:
    for pr in fixture["pull_requests"]:
        row = PullRequest(
            repository_id=repository.id,
            github_node_id=pr["github_node_id"],
            number=pr["number"],
            title=f"PR {pr['number']}",
            author_login=pr["author"],
            is_draft=pr["is_draft"],
            ready_for_review_at=_ts(pr["ready_for_review_at"]),
            created_at=_ts(pr["created_at"]),
            updated_at=_ts(pr["updated_at"]),
            closed_at=_ts(pr["closed_at"]),
            merged_at=_ts(pr["merged_at"]),
            reopened=pr["reopened"],
            reopen_count=pr["reopen_count"],
            additions=pr["additions"],
            deletions=pr["deletions"],
            changed_files=pr["changed_files"],
            source_updated_at=_ts(pr["updated_at"]),
        )
        session.add(row)
        session.flush()
        for i, review in enumerate(pr["reviews"]):
            session.add(
                Review(
                    pull_request_id=row.id,
                    github_node_id=f"{pr['github_node_id']}_RV{i}",
                    reviewer_login=review["reviewer"],
                    state=review["state"],
                    submitted_at=_ts(review["submitted_at"]),
                )
            )
        for login in pr["requested_reviewers"]:
            session.add(ReviewRequest(pull_request_id=row.id, requested_reviewer_login=login))
    for issue in fixture["issues"]:
        session.add(
            Issue(
                repository_id=repository.id,
                github_node_id=issue["github_node_id"],
                number=issue["number"],
                title=f"Issue {issue['number']}",
                created_at=_ts(issue["created_at"]),
                updated_at=_ts(issue["updated_at"]),
                closed_at=_ts(issue["closed_at"]),
                source_updated_at=_ts(issue["updated_at"]),
            )
        )
    session.commit()


def _assert_sample(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    if expected["median_hours"] is None:
        assert actual["median_hours"] is None
    else:
        assert actual["median_hours"] == pytest.approx(expected["median_hours"])
    assert actual["n"] == expected["n"]
    assert actual["meets_minimum_sample"] is expected["meets_minimum_sample"]
    assert actual["status"] == ("ok" if expected["meets_minimum_sample"] else "insufficient_data")
    for key in ("excluded_draft", "excluded_reopened", "excluded_no_approval"):
        if key in expected:
            assert actual[key] == expected[key], key
    if "included" in expected:
        assert actual["pr_numbers"] == expected["included"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_api_snapshot_matches_hand_calculated_fixture(
    name: str, client: TestClient, db_session: Session, seeded_repository: Repository
) -> None:
    fixture = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    expected = fixture["expected"]
    _seed(db_session, seeded_repository, fixture)

    response = client.post(
        f"/repositories/{seeded_repository.id}/metrics/snapshots",
        params={"analysis_at": fixture["analysis_at"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    window = fixture["window"]
    for key in ("current_start", "current_end", "baseline_start", "baseline_end"):
        assert _ts(body["window"][key]) == _ts(window[key])
    metrics = body["metrics"]

    for metric in ("pr_cycle_time", "review_wait", "merge_wait"):
        _assert_sample(metrics[metric]["current"], expected[metric]["current"])
        if "baseline" in expected[metric]:
            _assert_sample(metrics[metric]["baseline"], expected[metric]["baseline"])
        if expected[metric].get("pct_change") is not None:
            assert metrics[metric]["pct_change"] == pytest.approx(
                expected[metric]["pct_change"], abs=0.01
            )

    assert metrics["pending_review_count"]["count"] == expected["pending_review_count"]

    concentration = metrics["review_concentration"]
    if expected["review_concentration"] == "insufficient_data":
        assert concentration["status"] == "insufficient_data"
    else:
        assert concentration["value"] == pytest.approx(expected["review_concentration"]["value"])
        assert concentration["top_reviewer"] == expected["review_concentration"]["top_reviewer"]

    stale = metrics["stale_pull_requests"]
    assert [item["number"] for item in stale["items"]] == expected["stale_prs"]["items"]
    assert metrics["issue_backlog_growth"]["status"] == "insufficient_data"
    assert metrics["pr_size"]["current"]["median_lines"] == (
        expected["pr_size"]["current"]["median_lines"]
    )
    assert metrics["pr_size"]["oversized_count"] == expected["pr_size"]["oversized_count"]


def test_get_metrics_returns_latest_snapshot(
    client: TestClient, db_session: Session, seeded_repository: Repository
) -> None:
    fixture = json.loads((FIXTURES_DIR / "review_bottleneck.json").read_text())
    _seed(db_session, seeded_repository, fixture)
    finished = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    db_session.add(
        SyncRun(
            repository_id=seeded_repository.id,
            status=SyncRunStatus.success.value,
            started_at=finished - timedelta(minutes=2),
            finished_at=finished,
        )
    )
    db_session.commit()
    url = f"/repositories/{seeded_repository.id}/metrics"

    older = client.post(f"{url}/snapshots", params={"analysis_at": "2026-09-24T09:00:00Z"})
    newer = client.post(f"{url}/snapshots", params={"analysis_at": fixture["analysis_at"]})
    assert older.status_code == newer.status_code == 201

    response = client.get(url)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == newer.json()["id"]
    assert body["analysis_version"] == "metrics-v1"
    assert body["data_completeness"]["has_synced"] is True
    assert _ts(body["data_completeness"]["source_synced_at"]) == finished
    assert body["data_completeness"]["pull_requests_analyzed"] == len(fixture["pull_requests"])
    assert body["metrics"]["review_wait"]["pct_change"] == pytest.approx(233.33, abs=0.01)


def test_issue_backlog_growth_through_database(
    client: TestClient, db_session: Session, seeded_repository: Repository
) -> None:
    at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

    def issue(n: int, created_days_ago: int, closed_days_ago: int | None = None) -> dict:
        created = (at - timedelta(days=created_days_ago)).isoformat()
        closed = (at - timedelta(days=closed_days_ago)).isoformat() if closed_days_ago else None
        return {
            "github_node_id": f"I_{n}", "number": n, "created_at": created,
            "updated_at": closed or created, "closed_at": closed,
        }

    # Open at baseline end (7d ago): 1-6. Issue 7 closed before then, so the loader may skip it.
    # Open now: 1-5 plus 8, 9 (issue 6 closed 3 days ago) -> 7 vs 6 = +16.67%.
    issues = [issue(n, 30) for n in range(1, 6)] + [
        issue(6, 30, closed_days_ago=3),
        issue(7, 30, closed_days_ago=20),
        issue(8, 2),
        issue(9, 1),
    ]
    _seed(db_session, seeded_repository, {"pull_requests": [], "issues": issues})

    response = client.post(
        f"/repositories/{seeded_repository.id}/metrics/snapshots",
        params={"analysis_at": at.isoformat()},
    )
    growth = response.json()["metrics"]["issue_backlog_growth"]
    assert growth["baseline_open_count"] == 6
    assert growth["current_open_count"] == 7
    assert growth["pct_change"] == pytest.approx(16.6667, abs=1e-3)
    assert growth["status"] == "ok"


def test_get_metrics_without_snapshot_is_explicit_404(
    client: TestClient, seeded_repository: Repository
) -> None:
    response = client.get(f"/repositories/{seeded_repository.id}/metrics")
    assert response.status_code == 404
    assert "No metric snapshot yet" in response.json()["detail"]


def test_empty_repository_snapshot_degrades_to_insufficient_data(
    client: TestClient, seeded_repository: Repository
) -> None:
    response = client.post(f"/repositories/{seeded_repository.id}/metrics/snapshots")
    assert response.status_code == 201
    body = response.json()
    assert body["data_completeness"]["has_synced"] is False
    assert body["metrics"]["pr_cycle_time"]["current"]["status"] == "insufficient_data"
    assert body["metrics"]["pending_review_count"]["count"] == 0


def test_unknown_repository_is_404(client: TestClient, db_session: Session) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/repositories/{missing}/metrics").status_code == 404
    assert client.post(f"/repositories/{missing}/metrics/snapshots").status_code == 404


def test_future_analysis_at_is_rejected(
    client: TestClient, seeded_repository: Repository
) -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = client.post(
        f"/repositories/{seeded_repository.id}/metrics/snapshots", params={"analysis_at": future}
    )
    assert response.status_code == 422
