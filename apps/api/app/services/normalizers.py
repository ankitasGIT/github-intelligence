"""Converts raw GitHub API JSON into our relational model and upserts it, keyed by
(repository_id | pull_request_id, github_node_id) per docs/adr/0001-data-model.md.
Every upsert here is idempotent -- re-running a sync must never create duplicate rows."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.commit import Commit
from app.models.issue import Issue
from app.models.milestone import Milestone
from app.models.pull_request import PullRequest
from app.models.review import Review
from app.models.review_request import ReviewRequest


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _upsert_stmt(model: type[Base], values: dict[str, Any], index_elements: list[str]) -> Any:
    stmt = pg_insert(model).values(**values)
    update_cols: dict[str, Any] = {k: stmt.excluded[k] for k in values if k not in index_elements}
    update_cols["synced_at"] = func.now()
    return stmt.on_conflict_do_update(index_elements=index_elements, set_=update_cols)


def upsert_pull_request(session: Session, repository_id: UUID, gh_pr: dict[str, Any]) -> UUID:
    updated_at = _parse(gh_pr["updated_at"])
    values = {
        "repository_id": repository_id,
        "github_node_id": gh_pr["node_id"],
        "number": gh_pr["number"],
        "title": gh_pr["title"],
        "author_login": (gh_pr.get("user") or {}).get("login"),
        "is_draft": bool(gh_pr.get("draft", False)),
        "created_at": _parse(gh_pr["created_at"]),
        "updated_at": updated_at,
        "closed_at": _parse(gh_pr.get("closed_at")),
        "merged_at": _parse(gh_pr.get("merged_at")),
        "additions": gh_pr.get("additions", 0),
        "deletions": gh_pr.get("deletions", 0),
        "changed_files": gh_pr.get("changed_files", 0),
        "source_updated_at": updated_at,
    }
    stmt = _upsert_stmt(PullRequest, values, ["repository_id", "github_node_id"])
    stmt = stmt.returning(PullRequest.id)
    return session.execute(stmt).scalar_one()


def upsert_review(session: Session, pull_request_id: UUID, gh_review: dict[str, Any]) -> None:
    if gh_review.get("state") == "PENDING" or not gh_review.get("submitted_at"):
        return  # not yet submitted -- not a review event
    values = {
        "pull_request_id": pull_request_id,
        "github_node_id": gh_review["node_id"],
        "reviewer_login": (gh_review.get("user") or {}).get("login") or "unknown",
        "state": gh_review["state"],
        "submitted_at": _parse(gh_review["submitted_at"]),
    }
    stmt = _upsert_stmt(Review, values, ["pull_request_id", "github_node_id"])
    session.execute(stmt)


def upsert_review_request(session: Session, pull_request_id: UUID, reviewer_login: str) -> None:
    values = {
        "pull_request_id": pull_request_id,
        "requested_reviewer_login": reviewer_login,
        "is_outstanding": True,
    }
    stmt = _upsert_stmt(ReviewRequest, values, ["pull_request_id", "requested_reviewer_login"])
    session.execute(stmt)


def resolve_stale_review_requests(
    session: Session, pull_request_id: UUID, still_outstanding_logins: set[str]
) -> None:
    """Clears is_outstanding for any previously-synced request GitHub no longer reports as
    outstanding (the reviewer submitted a review, or the request was removed)."""
    stmt = (
        update(ReviewRequest)
        .where(
            ReviewRequest.pull_request_id == pull_request_id,
            ReviewRequest.requested_reviewer_login.notin_(still_outstanding_logins),
        )
        .values(is_outstanding=False, synced_at=func.now())
    )
    session.execute(stmt)


def upsert_issue(session: Session, repository_id: UUID, gh_issue: dict[str, Any]) -> None:
    updated_at = _parse(gh_issue["updated_at"])
    values = {
        "repository_id": repository_id,
        "github_node_id": gh_issue["node_id"],
        "number": gh_issue["number"],
        "title": gh_issue["title"],
        "created_at": _parse(gh_issue["created_at"]),
        "updated_at": updated_at,
        "closed_at": _parse(gh_issue.get("closed_at")),
        "source_updated_at": updated_at,
    }
    stmt = _upsert_stmt(Issue, values, ["repository_id", "github_node_id"])
    session.execute(stmt)


def upsert_commit(session: Session, repository_id: UUID, gh_commit: dict[str, Any]) -> None:
    commit_info = gh_commit.get("commit", {})
    values = {
        "repository_id": repository_id,
        "github_node_id": gh_commit["node_id"],
        "sha": gh_commit["sha"],
        "author_login": (gh_commit.get("author") or {}).get("login"),
        "authored_at": _parse((commit_info.get("author") or {}).get("date")),
        "message": commit_info.get("message", ""),
    }
    stmt = _upsert_stmt(Commit, values, ["repository_id", "github_node_id"])
    session.execute(stmt)


def upsert_milestone(session: Session, repository_id: UUID, gh_milestone: dict[str, Any]) -> None:
    values = {
        "repository_id": repository_id,
        "github_node_id": gh_milestone["node_id"],
        "number": gh_milestone["number"],
        "title": gh_milestone["title"],
        "state": gh_milestone["state"],
        "due_on": _parse(gh_milestone.get("due_on")),
        "open_issues": gh_milestone.get("open_issues", 0),
        "closed_issues": gh_milestone.get("closed_issues", 0),
        "created_at": _parse(gh_milestone.get("created_at")),
    }
    stmt = _upsert_stmt(Milestone, values, ["repository_id", "github_node_id"])
    session.execute(stmt)
