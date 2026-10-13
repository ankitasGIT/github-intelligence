"""Seeds the hand-calculated timelines in tests/fixtures/*.json into Postgres."""

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.issue import Issue
from app.models.milestone import Milestone
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Review
from app.models.review_request import ReviewRequest

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
FIXTURE_NAMES = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def seed(session: Session, repository: Repository, fixture: dict[str, Any]) -> None:
    for pr in fixture["pull_requests"]:
        row = PullRequest(
            repository_id=repository.id,
            github_node_id=pr["github_node_id"],
            number=pr["number"],
            title=f"PR {pr['number']}",
            author_login=pr["author"],
            is_draft=pr["is_draft"],
            ready_for_review_at=parse_ts(pr["ready_for_review_at"]),
            created_at=parse_ts(pr["created_at"]),
            updated_at=parse_ts(pr["updated_at"]),
            closed_at=parse_ts(pr["closed_at"]),
            merged_at=parse_ts(pr["merged_at"]),
            reopened=pr["reopened"],
            reopen_count=pr["reopen_count"],
            additions=pr["additions"],
            deletions=pr["deletions"],
            changed_files=pr["changed_files"],
            source_updated_at=parse_ts(pr["updated_at"]),
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
                    submitted_at=parse_ts(review["submitted_at"]),
                )
            )
        for login in pr["requested_reviewers"]:
            session.add(ReviewRequest(pull_request_id=row.id, requested_reviewer_login=login))
    for milestone in fixture.get("milestones", []):
        session.add(
            Milestone(
                repository_id=repository.id,
                github_node_id=f"M_{milestone['number']}",
                number=milestone["number"],
                title=milestone["title"],
                state=milestone["state"],
                due_on=parse_ts(milestone["due_on"]),
                open_issues=milestone["open_issues"],
                closed_issues=milestone["closed_issues"],
                created_at=parse_ts(milestone["created_at"]),
            )
        )
    for issue in fixture["issues"]:
        session.add(
            Issue(
                repository_id=repository.id,
                github_node_id=issue["github_node_id"],
                number=issue["number"],
                title=f"Issue {issue['number']}",
                created_at=parse_ts(issue["created_at"]),
                updated_at=parse_ts(issue["updated_at"]),
                closed_at=parse_ts(issue["closed_at"]),
                source_updated_at=parse_ts(issue["updated_at"]),
            )
        )
    session.commit()
