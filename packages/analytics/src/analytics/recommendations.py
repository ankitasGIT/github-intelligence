"""The recommendation engine: maps each finding to a fixed, ordered playbook of actions.

Actions are templated from the finding's own evidence and affected items -- no free-form text
generation, and nothing here mutates GitHub. Ranking follows the findings' rank, then each
playbook's own order, so the same findings always produce the same list.
"""

from collections.abc import Callable
from dataclasses import dataclass

from analytics.detection import (
    ISSUE_BACKLOG_GROWTH,
    MILESTONE_AT_RISK,
    OVERSIZED_PULL_REQUESTS,
    REVIEW_BOTTLENECK,
    STALE_PULL_REQUESTS,
    Finding,
)


@dataclass(frozen=True)
class Recommendation:
    action: str  # stable id, e.g. "REVIEW_BOTTLENECK.redistribute_reviews"
    finding_key: str
    rule: str
    title: str
    detail: str
    item_numbers: tuple[int, ...]  # the source items this action points at, if any
    rank: int  # 1 = do first, across all findings


@dataclass(frozen=True)
class _Action:
    action: str
    title: str
    detail: str
    item_numbers: tuple[int, ...] = ()


def _refs(numbers: tuple[int, ...]) -> str:
    return ", ".join(f"#{n}" for n in numbers)


def _first(finding: Finding, count: int) -> tuple[int, ...]:
    """The first `count` affected items -- rules already order them most-urgent first."""
    return tuple(item.number for item in finding.affected_items[:count])


def _review_bottleneck(finding: Finding) -> list[_Action]:
    evidence = finding.evidence
    oldest = _first(finding, 3)
    actions = []
    if "review_concentration_high" in finding.confidence.corroborating:
        actions.append(
            _Action(
                f"{REVIEW_BOTTLENECK}.redistribute_reviews",
                "Redistribute pending review requests",
                f"{evidence['top_reviewer_pending']} of "
                f"{evidence['pending_with_requested_reviewer']} pending PRs with a requested "
                "reviewer are waiting on the same person. Reassign some of those requests so "
                "no single reviewer gates the queue.",
            )
        )
    actions.append(
        _Action(
            f"{REVIEW_BOTTLENECK}.review_oldest_first",
            "Review the longest-waiting PRs first",
            f"Start with {_refs(oldest)}: they have waited longest for a first review.",
            oldest,
        )
    )
    actions.append(
        _Action(
            f"{REVIEW_BOTTLENECK}.set_review_target",
            "Agree a first-review target",
            "Set a team expectation for time to first review (for example, one working day) "
            "and check the review queue at a fixed time each day.",
        )
    )
    return actions


def _stale_pull_requests(finding: Finding) -> list[_Action]:
    oldest = _first(finding, 3)
    return [
        _Action(
            f"{STALE_PULL_REQUESTS}.triage_oldest",
            "Review, close, or split the oldest idle PRs",
            f"Decide on {_refs(oldest)} first: merge what is ready, close what is abandoned, "
            "and split anything too large to finish.",
            oldest,
        ),
        _Action(
            f"{STALE_PULL_REQUESTS}.limit_wip",
            "Limit work in progress",
            "Agree to finish or close an open PR before starting new work, so idle PRs do not "
            "accumulate.",
        ),
    ]


def _issue_backlog_growth(finding: Finding) -> list[_Action]:
    oldest = _first(finding, 5)
    return [
        _Action(
            f"{ISSUE_BACKLOG_GROWTH}.triage_oldest",
            "Triage the oldest open issues",
            f"Label, prioritize, or close the oldest open issues, starting with {_refs(oldest)}.",
            oldest,
        ),
        _Action(
            f"{ISSUE_BACKLOG_GROWTH}.assign_triage_owner",
            "Set a recurring triage owner",
            "Rotate a triage owner each week so new issues are labeled and prioritized as they "
            "arrive.",
        ),
    ]


def _oversized_pull_requests(finding: Finding) -> list[_Action]:
    open_large = tuple(
        item.number for item in finding.affected_items if item.detail.get("state") == "open"
    )
    threshold = finding.evidence["oversized_threshold_lines"]
    actions = []
    if open_large:
        actions.append(
            _Action(
                f"{OVERSIZED_PULL_REQUESTS}.review_large_open_first",
                "Give the large open PRs focused review time",
                f"Schedule dedicated review for {_refs(open_large[:3])}, or split them before "
                "review if they bundle unrelated changes.",
                open_large[:3],
            )
        )
    actions.append(
        _Action(
            f"{OVERSIZED_PULL_REQUESTS}.split_future_work",
            "Split future work into smaller PRs",
            f"Aim for PRs under {threshold} changed lines: land refactors separately from "
            "behavior changes and use feature flags for incremental delivery.",
        )
    )
    return actions


def _milestone_at_risk(finding: Finding) -> list[_Action]:
    evidence = finding.evidence
    number = (evidence["milestone_number"],)
    return [
        _Action(
            f"{MILESTONE_AT_RISK}.rescope",
            "Re-scope or move the milestone",
            f"Decide which of the {evidence['open_issues']} open items in "
            f"“{evidence['title']}” must ship by the due date, and move the rest or "
            "the date.",
            number,
        ),
        _Action(
            f"{MILESTONE_AT_RISK}.prioritize_remaining",
            "Prioritize the remaining milestone items",
            "Make the milestone's open items the team's first pick until it is back on pace.",
            number,
        ),
    ]


PLAYBOOKS: dict[str, Callable[[Finding], list[_Action]]] = {
    REVIEW_BOTTLENECK: _review_bottleneck,
    STALE_PULL_REQUESTS: _stale_pull_requests,
    ISSUE_BACKLOG_GROWTH: _issue_backlog_growth,
    OVERSIZED_PULL_REQUESTS: _oversized_pull_requests,
    MILESTONE_AT_RISK: _milestone_at_risk,
}


def recommend(findings: tuple[Finding, ...]) -> tuple[Recommendation, ...]:
    """Actions for already-ranked findings, in order. `rank` is global and starts at 1."""
    recommendations: list[Recommendation] = []
    for finding in findings:
        for action in PLAYBOOKS[finding.rule](finding):
            recommendations.append(
                Recommendation(
                    action=action.action,
                    finding_key=finding.key,
                    rule=finding.rule,
                    title=action.title,
                    detail=action.detail,
                    item_numbers=action.item_numbers,
                    rank=len(recommendations) + 1,
                )
            )
    return tuple(recommendations)
