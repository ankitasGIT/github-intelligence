"""Phase 4 acceptance: seeded scenarios produce the expected findings through the API, and every
dashboard card traces back to source items, recommendations, and metric definitions."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from seeding import FIXTURE_NAMES, FIXTURES_DIR, seed
from sqlalchemy.orm import Session

from app.models.github_connection import GithubConnection
from app.models.repository import Repository
from app.models.sync_run import SyncRun, SyncRunStatus

AT = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def _snapshot(client: TestClient, repository: Repository, at: str | datetime = AT) -> dict:
    analysis_at = at.isoformat() if isinstance(at, datetime) else at
    response = client.post(
        f"/repositories/{repository.id}/metrics/snapshots", params={"analysis_at": analysis_at}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def bottleneck(client: TestClient, db_session: Session, seeded_repository: Repository) -> dict:
    """The review_bottleneck fixture, seeded and analyzed; returns the dashboard payload."""
    fixture = _load("review_bottleneck")
    seed(db_session, seeded_repository, fixture)
    _snapshot(client, seeded_repository, fixture["analysis_at"])
    response = client.get(f"/repositories/{seeded_repository.id}/dashboard")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_findings_through_api_match_fixture(
    name: str, client: TestClient, db_session: Session, seeded_repository: Repository
) -> None:
    fixture = _load(name)
    seed(db_session, seeded_repository, fixture)
    _snapshot(client, seeded_repository, fixture["analysis_at"])

    response = client.get(f"/repositories/{seeded_repository.id}/findings")
    assert response.status_code == 200
    assert [f["rule"] for f in response.json()] == fixture["expected"]["findings_expected"]


def test_review_bottleneck_dashboard_evidence_traces_to_sources(
    bottleneck: dict, seeded_repository: Repository
) -> None:
    analysis = bottleneck["analysis"]
    (finding,) = analysis["findings"]
    assert finding["rule"] == "REVIEW_BOTTLENECK"
    assert finding["rank"] == 1
    assert finding["severity"] == "high"
    assert finding["confidence"]["score"] == pytest.approx(0.8)
    evidence = finding["evidence"]
    assert evidence["review_wait_current_median_hours"] == 20
    assert evidence["review_wait_baseline_median_hours"] == 6
    assert evidence["review_wait_pct_change"] == pytest.approx(233.33, abs=0.01)
    assert evidence["pending_review_count"] == 7
    assert evidence["review_concentration"] == pytest.approx(0.7143)
    assert evidence["current_pr_numbers"] == [301, 302, 303, 304, 305]

    first = finding["affected_items"][0]
    assert first["number"] == 306
    assert first["title"] == "PR 306"
    assert first["url"] == "https://github.com/acme/backend/pull/306"
    assert [i["number"] for i in finding["affected_items"]] == [306, 307, 308, 309, 310, 311, 312]

    assert [r["rank"] for r in finding["recommendations"]] == [1, 2, 3]
    assert finding["recommendations"][1]["item_numbers"] == [306, 307, 308]
    assert analysis["recommendations"] == finding["recommendations"]


def test_dashboard_carries_health_trend_queue_and_versions(bottleneck: dict) -> None:
    analysis = bottleneck["analysis"]
    assert analysis["analysis_version"] == "metrics-v1"
    assert analysis["detection_version"] == "rules-v1"
    assert analysis["health_version"] == "health-v1"
    assert analysis["window"]["current_end"].startswith("2026-09-25T09:00:00")

    health = analysis["health"]
    assert health["overall"] == pytest.approx(64.25, abs=0.05)
    dims = {d["dimension"]: d for d in health["dimensions"]}
    assert dims["issues"]["status"] == "insufficient_data"
    assert dims["reviews"]["score"] == pytest.approx(33.6)

    assert len(analysis["trend"]) == 8
    assert analysis["trend"][-1]["review_wait_median_hours"] == 20

    queue = analysis["pending_queue"]
    assert [q["number"] for q in queue] == [306, 307, 308, 309, 310, 311, 312]
    assert queue[0]["requested_reviewers"] == ["rahul"]
    assert queue[0]["age_days"] == 5.0
    assert queue[0]["author"] == "frank"


def test_finding_detail_includes_definitions(
    client: TestClient, bottleneck: dict, seeded_repository: Repository
) -> None:
    finding_id = bottleneck["analysis"]["findings"][0]["id"]
    response = client.get(f"/repositories/{seeded_repository.id}/findings/{finding_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["rule_definition"]["key"] == "REVIEW_BOTTLENECK"
    assert [d["key"] for d in detail["metric_definitions"]] == detail["metric_refs"]
    assert detail["metric_definitions"][0]["doc_anchor"] == "2-review-wait-time"
    assert len(detail["recommendations"]) == 3


def test_finding_from_another_repository_is_404(
    client: TestClient, db_session: Session, bottleneck: dict, seeded_repository: Repository
) -> None:
    connection = db_session.get(GithubConnection, seeded_repository.github_connection_id)
    assert connection is not None
    other = Repository(
        github_connection_id=connection.id,
        github_node_id="R_2",
        owner="acme",
        name="frontend",
        full_name="acme/frontend",
    )
    db_session.add(other)
    db_session.commit()
    finding_id = bottleneck["analysis"]["findings"][0]["id"]
    assert client.get(f"/repositories/{other.id}/findings/{finding_id}").status_code == 404
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/repositories/{seeded_repository.id}/findings/{missing}").status_code == 404


def test_recommendations_endpoint_is_ranked(
    client: TestClient, bottleneck: dict, seeded_repository: Repository
) -> None:
    response = client.get(f"/repositories/{seeded_repository.id}/recommendations")
    assert response.status_code == 200
    assert [r["action"] for r in response.json()] == [
        "REVIEW_BOTTLENECK.redistribute_reviews",
        "REVIEW_BOTTLENECK.review_oldest_first",
        "REVIEW_BOTTLENECK.set_review_target",
    ]


def test_older_findings_stay_addressable_after_a_new_snapshot(
    client: TestClient, bottleneck: dict, seeded_repository: Repository
) -> None:
    old_id = bottleneck["analysis"]["findings"][0]["id"]
    # Same instant, so the rule fires again -- but as a new, separate finding row.
    _snapshot(client, seeded_repository, AT)
    (latest,) = client.get(f"/repositories/{seeded_repository.id}/findings").json()
    assert latest["rule"] == "REVIEW_BOTTLENECK"
    assert latest["id"] != old_id
    old = client.get(f"/repositories/{seeded_repository.id}/findings/{old_id}").json()
    assert old["analysis_at"].startswith("2026-09-25T09:00:00")


def test_dashboard_before_any_snapshot_shows_sync_state(
    client: TestClient, db_session: Session, seeded_repository: Repository
) -> None:
    db_session.add(SyncRun(repository_id=seeded_repository.id, status=SyncRunStatus.running.value))
    db_session.commit()
    response = client.get(f"/repositories/{seeded_repository.id}/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] is None
    assert body["latest_sync"]["status"] == "running"
    assert body["repository"]["full_name"] == "acme/backend"
    assert client.get(f"/repositories/{seeded_repository.id}/findings").status_code == 404


def test_dashboard_rejects_unsupported_window(
    client: TestClient, seeded_repository: Repository
) -> None:
    response = client.get(f"/repositories/{seeded_repository.id}/dashboard?window=30d")
    assert response.status_code == 422


def test_milestone_at_risk_through_database(
    client: TestClient, db_session: Session, seeded_repository: Repository
) -> None:
    milestone = {
        "number": 3,
        "title": "v2.0",
        "state": "open",
        "due_on": (AT + timedelta(days=7)).isoformat(),
        "open_issues": 10,
        "closed_issues": 10,
        "created_at": (AT - timedelta(days=20)).isoformat(),
    }
    seed(
        db_session,
        seeded_repository,
        {"pull_requests": [], "issues": [], "milestones": [milestone]},
    )
    _snapshot(client, seeded_repository)

    (finding,) = client.get(f"/repositories/{seeded_repository.id}/findings").json()
    assert finding["key"] == "MILESTONE_AT_RISK:3"
    assert finding["evidence"]["required_items_per_day"] == pytest.approx(1.4286)
    item = finding["affected_items"][0]
    assert (item["kind"], item["title"]) == ("milestone", "v2.0")
    assert item["url"] == "https://github.com/acme/backend/milestone/3"


def test_definitions_cover_every_reference(client: TestClient) -> None:
    body = client.get("/metric-definitions").json()
    metric_keys = {d["key"] for d in body["metrics"]}
    assert {
        "review_wait",
        "pending_review_count",
        "review_concentration",
        "stale_items",
        "issue_backlog_growth",
        "pr_size",
        "milestone_pace",
    } <= metric_keys
    assert {d["key"] for d in body["rules"]} == {
        "REVIEW_BOTTLENECK",
        "STALE_PULL_REQUESTS",
        "ISSUE_BACKLOG_GROWTH",
        "OVERSIZED_PULL_REQUESTS",
        "MILESTONE_AT_RISK",
    }
