"""Builds analytics inputs from the hand-calculated timelines in tests/fixtures/*.json."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from analytics.models import (
    AnalysisWindows,
    Issue,
    Milestone,
    PullRequest,
    RepositoryData,
    Review,
)

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
FIXTURE_NAMES = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _ts(value: str) -> datetime:
    parsed = parse_ts(value)
    assert parsed is not None
    return parsed


@dataclass(frozen=True)
class Fixture:
    name: str
    data: RepositoryData
    windows: AnalysisWindows
    expected: dict[str, Any]
    raw: dict[str, Any]


def _pull_request(pr: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=pr["number"],
        created_at=_ts(pr["created_at"]),
        updated_at=_ts(pr["updated_at"]),
        is_draft=pr["is_draft"],
        ready_for_review_at=parse_ts(pr["ready_for_review_at"]),
        closed_at=parse_ts(pr["closed_at"]),
        merged_at=parse_ts(pr["merged_at"]),
        reopened=pr["reopened"],
        additions=pr["additions"],
        deletions=pr["deletions"],
        changed_files=pr["changed_files"],
        reviews=tuple(
            Review(r["reviewer"], r["state"], _ts(r["submitted_at"])) for r in pr["reviews"]
        ),
        requested_reviewers=tuple(pr["requested_reviewers"]),
    )


def _issue(issue: dict[str, Any]) -> Issue:
    return Issue(
        number=issue["number"],
        created_at=_ts(issue["created_at"]),
        updated_at=_ts(issue["updated_at"]),
        closed_at=parse_ts(issue["closed_at"]),
    )


def _milestone(milestone: dict[str, Any]) -> Milestone:
    return Milestone(
        number=milestone["number"],
        title=milestone["title"],
        state=milestone["state"],
        due_on=parse_ts(milestone["due_on"]),
        open_issues=milestone["open_issues"],
        closed_issues=milestone["closed_issues"],
        created_at=parse_ts(milestone["created_at"]),
    )


def load_fixture(name: str) -> Fixture:
    raw = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return Fixture(
        name=name,
        data=RepositoryData(
            pull_requests=tuple(_pull_request(pr) for pr in raw["pull_requests"]),
            issues=tuple(_issue(i) for i in raw["issues"]),
            milestones=tuple(_milestone(m) for m in raw.get("milestones", [])),
        ),
        windows=AnalysisWindows.ending_at(_ts(raw["analysis_at"])),
        expected=raw["expected"],
        raw=raw,
    )
