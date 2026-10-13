"""Detection rules and recommendations. Fixture-driven first (`findings_expected`), then each
rule's normal, boundary, and insufficient-data cases built from small synthetic timelines."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fixture_loader import FIXTURE_NAMES, load_fixture

from analytics.analysis import run_analysis
from analytics.detection import (
    HIGH,
    ISSUE_BACKLOG_GROWTH,
    LOW,
    MEDIUM,
    MILESTONE_AT_RISK,
    OVERSIZED_PULL_REQUESTS,
    REVIEW_BOTTLENECK,
    STALE_PULL_REQUESTS,
    Finding,
    awaiting_first_review,
    detect_findings,
    pending_review_norm,
)
from analytics.metrics import compute_metrics
from analytics.models import (
    AnalysisWindows,
    Issue,
    Milestone,
    PullRequest,
    RepositoryData,
    Review,
)
from analytics.recommendations import recommend
from analytics.serialization import to_jsonable

AT = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
WINDOWS = AnalysisWindows.ending_at(AT)


def _detect(data: RepositoryData) -> tuple[Finding, ...]:
    return detect_findings(compute_metrics(data, WINDOWS), data, WINDOWS)


def _keys(data: RepositoryData) -> list[str]:
    return [f.key for f in _detect(data)]


def _pr(number: int, **kwargs: Any) -> PullRequest:
    defaults: dict[str, Any] = {"created_at": AT - timedelta(days=1), "updated_at": AT}
    return PullRequest(number=number, **{**defaults, **kwargs})


def _ago(days: float = 0, hours: float = 0) -> datetime:
    return AT - timedelta(days=days, hours=hours)


# --- Fixture-driven ground truth ----------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_findings_match_fixture(name: str) -> None:
    fixture = load_fixture(name)
    result = run_analysis(fixture.data, fixture.windows)
    assert [f.rule for f in result.findings] == fixture.expected["findings_expected"]


def test_review_bottleneck_fixture_evidence_is_traceable() -> None:
    """Phase 4 acceptance: the seeded review-delay scenario yields REVIEW_BOTTLENECK with the
    hand-calculated evidence, and every number traces to source PRs."""
    fixture = load_fixture("review_bottleneck")
    (finding,) = run_analysis(fixture.data, fixture.windows).findings
    evidence = finding.evidence

    assert finding.severity == HIGH
    assert evidence["review_wait_current_median_hours"] == 20
    assert evidence["review_wait_baseline_median_hours"] == 6
    assert evidence["review_wait_pct_change"] == pytest.approx(233.33, abs=0.01)
    assert (evidence["review_wait_current_n"], evidence["review_wait_baseline_n"]) == (5, 6)
    assert evidence["current_pr_numbers"] == [301, 302, 303, 304, 305]
    assert evidence["baseline_pr_numbers"] == [250, 251, 252, 253, 254, 255]
    assert evidence["pending_review_count"] == 7
    assert evidence["awaiting_first_review_count"] == 7
    assert evidence["awaiting_first_review_norm"] == 0
    assert evidence["review_concentration"] == pytest.approx(0.7143)
    assert evidence["top_reviewer_pending"] == 5
    # Oldest-waiting first; PR 306 is the one stale PR from the fixture.
    assert [i.number for i in finding.affected_items] == [306, 307, 308, 309, 310, 311, 312]
    assert finding.affected_items[0].detail["stale"] is True
    assert finding.metric_refs == ("review_wait", "pending_review_count", "review_concentration")
    # sample 5/10 = 0.5, strength capped at 1, all three corroborators hold.
    assert finding.confidence.score == pytest.approx(0.8)
    assert finding.confidence.level == HIGH
    assert finding.confidence.corroborating == (
        "review_concentration_high",
        "cycle_time_rising",
        "stale_pending_prs",
    )


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_analysis_is_json_serializable_and_deterministic(name: str) -> None:
    fixture = load_fixture(name)

    def dump() -> str:
        result = run_analysis(fixture.data, fixture.windows)
        payload = {
            "findings": to_jsonable(result.findings),
            "recommendations": to_jsonable(result.recommendations),
            "health": result.health.to_dict(),
            "trend": result.trend_dict(),
        }
        return json.dumps(payload, sort_keys=True, allow_nan=False)

    assert dump() == dump()


# --- REVIEW_BOTTLENECK --------------------------------------------------------------------


def _bottleneck_data(
    current_wait_hours: float, *, current_n: int = 5, pending: int = 3, baseline_pending: int = 0
) -> RepositoryData:
    """Baseline: 6 PRs reviewed after 10h. Current: `current_n` PRs reviewed after
    `current_wait_hours`, plus `pending` unreviewed open PRs."""
    prs: list[PullRequest] = []
    for i in range(6):
        created = _ago(days=30 - i * 3)
        prs.append(
            _pr(
                100 + i,
                created_at=created,
                updated_at=created + timedelta(hours=12),
                merged_at=created + timedelta(hours=12),
                reviews=(Review("r", "APPROVED", created + timedelta(hours=10)),),
            )
        )
    for i in range(current_n):
        created = _ago(days=6, hours=-i)
        reviewed = created + timedelta(hours=current_wait_hours)
        merged = reviewed + timedelta(hours=1)
        prs.append(
            _pr(
                200 + i,
                created_at=created,
                updated_at=merged,
                merged_at=merged,
                reviews=(Review("r", "COMMENTED", reviewed),),
            )
        )
    for i in range(pending):
        prs.append(_pr(300 + i, created_at=_ago(days=1, hours=i), updated_at=_ago(days=1)))
    # PRs that sat unreviewed through the whole baseline window raise its norm.
    for i in range(baseline_pending):
        prs.append(
            _pr(400 + i, created_at=_ago(days=40), updated_at=_ago(days=1), closed_at=_ago(days=6))
        )
    return RepositoryData(pull_requests=tuple(prs))


def test_review_bottleneck_fires_at_exactly_fifty_percent() -> None:
    assert _keys(_bottleneck_data(15)) == [REVIEW_BOTTLENECK]


def test_review_bottleneck_does_not_fire_just_below_fifty_percent() -> None:
    assert _keys(_bottleneck_data(14.9)) == []


def test_review_bottleneck_needs_minimum_current_sample() -> None:
    assert _keys(_bottleneck_data(30, current_n=4)) == []


def test_review_bottleneck_needs_minimum_pending_count() -> None:
    assert _keys(_bottleneck_data(30, pending=2)) == []


def test_review_bottleneck_needs_pending_elevated_over_its_norm() -> None:
    # 4 PRs sat unreviewed through the baseline window -> norm 4; 5 pending < 1.5 * 4.
    data = _bottleneck_data(30, pending=5, baseline_pending=4)
    assert pending_review_norm(data.pull_requests, WINDOWS) == 4
    assert _keys(data) == []
    # 6 pending == 1.5 * 4 clears it.
    assert _keys(_bottleneck_data(30, pending=6, baseline_pending=4)) == [REVIEW_BOTTLENECK]


def test_review_bottleneck_severity_is_medium_below_double_threshold() -> None:
    (finding,) = _detect(_bottleneck_data(15))
    assert finding.severity == MEDIUM
    # sample 5/10 = 0.5; strength exactly at threshold = 0.5; of the corroborators only
    # cycle time rose (12h -> 16h): 0.4 * 0.5 + 0.4 * 0.5 + 0.2 * 1/3.
    assert finding.confidence.corroborating == ("cycle_time_rising",)
    assert finding.confidence.score == pytest.approx(0.4667)
    assert finding.confidence.level == LOW


def test_awaiting_first_review_reconstructs_past_instants() -> None:
    created = _ago(days=10)
    pr = _pr(1, created_at=created, reviews=(Review("a", "COMMENTED", _ago(days=8)),))
    draft_then = _pr(2, created_at=created, ready_for_review_at=_ago(days=3))
    assert [p.number for p in awaiting_first_review([pr, draft_then], _ago(days=9))] == [1]
    assert [p.number for p in awaiting_first_review([pr, draft_then], _ago(days=2))] == [2]


# --- STALE_PULL_REQUESTS ------------------------------------------------------------------


def _stale_prs(count: int, idle_days: float = 6) -> RepositoryData:
    prs = [_pr(n, created_at=_ago(days=20), updated_at=_ago(days=idle_days)) for n in range(count)]
    return RepositoryData(pull_requests=tuple(prs))


def test_stale_prs_fire_at_three() -> None:
    (finding,) = _detect(_stale_prs(3))
    assert finding.rule == STALE_PULL_REQUESTS
    assert finding.severity == MEDIUM
    assert finding.evidence["stale_count"] == 3
    assert finding.evidence["never_reviewed_count"] == 3


def test_stale_prs_do_not_fire_at_two() -> None:
    assert _keys(_stale_prs(2)) == []


def test_stale_prs_ignore_drafts_and_rank_longest_idle_first() -> None:
    prs = (
        _pr(1, created_at=_ago(days=20), updated_at=_ago(days=6)),
        _pr(2, created_at=_ago(days=20), updated_at=_ago(days=12)),
        _pr(3, created_at=_ago(days=20), updated_at=_ago(days=8)),
        _pr(4, created_at=_ago(days=20), updated_at=_ago(days=30), is_draft=True),
    )
    (finding,) = _detect(RepositoryData(pull_requests=prs))
    assert [i.number for i in finding.affected_items] == [2, 3, 1]
    assert "long_idle_prs" in finding.confidence.corroborating


def test_stale_prs_severity_high_at_double_minimum() -> None:
    (finding,) = _detect(_stale_prs(6))
    assert finding.severity == HIGH


# --- ISSUE_BACKLOG_GROWTH -----------------------------------------------------------------


def _issues(baseline_open: int, new: int) -> RepositoryData:
    old = [Issue(n, _ago(days=20), _ago(days=20)) for n in range(baseline_open)]
    fresh = [Issue(100 + n, _ago(days=2), _ago(days=2)) for n in range(new)]
    return RepositoryData(issues=tuple(old + fresh))


def test_backlog_growth_fires_at_exactly_twenty_five_percent() -> None:
    (finding,) = _detect(_issues(8, 2))
    assert finding.rule == ISSUE_BACKLOG_GROWTH
    assert finding.evidence["pct_change"] == 25.0
    assert finding.evidence["age_buckets"] == {
        "under_7d": 2,
        "7_to_30d": 8,
        "30_to_90d": 0,
        "over_90d": 0,
    }
    # Oldest first.
    assert finding.affected_items[0].number == 0
    assert finding.affected_items[0].detail["stale"] is True


def test_backlog_growth_below_threshold_does_not_fire() -> None:
    assert ISSUE_BACKLOG_GROWTH not in _keys(_issues(10, 2))


def test_backlog_growth_needs_minimum_baseline() -> None:
    # 4 -> 8 is +100%, but a baseline of 4 issues is too small to mean anything.
    assert ISSUE_BACKLOG_GROWTH not in _keys(_issues(4, 4))


# --- OVERSIZED_PULL_REQUESTS --------------------------------------------------------------


def _sized(merged: list[int], open_: list[int], start: int = 0) -> RepositoryData:
    prs = [
        _pr(start + n, merged_at=_ago(hours=2), updated_at=_ago(hours=2), additions=lines)
        for n, lines in enumerate(merged)
    ] + [_pr(start + 100 + n, additions=lines) for n, lines in enumerate(open_)]
    return RepositoryData(pull_requests=tuple(prs))


def test_oversized_fires_at_exactly_twenty_five_percent_including_open_prs() -> None:
    (finding,) = _detect(_sized([50, 60, 70, 900, 80, 90], [30, 401]))
    assert finding.rule == OVERSIZED_PULL_REQUESTS
    assert finding.evidence["sample_size"] == 8
    assert finding.evidence["oversized_share"] == 0.25
    assert finding.severity == LOW
    assert [(i.number, i.detail["state"]) for i in finding.affected_items] == [
        (3, "merged"),
        (101, "open"),
    ]


def test_oversized_threshold_is_strictly_greater_than_400() -> None:
    assert _keys(_sized([50, 60, 70, 400, 80, 90], [30, 400])) == []


def test_oversized_needs_minimum_sample() -> None:
    assert _keys(_sized([900, 900, 50, 60], [])) == []


# --- MILESTONE_AT_RISK --------------------------------------------------------------------


def _milestone(**kwargs: Any) -> RepositoryData:
    defaults: dict[str, Any] = {
        "number": 3,
        "title": "v2.0",
        "state": "open",
        "due_on": AT + timedelta(days=7),
        "open_issues": 10,
        "closed_issues": 10,
        "created_at": _ago(days=20),
    }
    return RepositoryData(milestones=(Milestone(**{**defaults, **kwargs}),))


def test_milestone_behind_pace_is_at_risk() -> None:
    # Pace so far 10 / 20d = 0.5/day; needed 10 / 7d = 1.43/day.
    (finding,) = _detect(_milestone())
    assert finding.key == f"{MILESTONE_AT_RISK}:3"
    assert finding.severity == HIGH  # needed pace is >= 2x achieved pace
    assert finding.evidence["observed_items_per_day"] == 0.5
    assert finding.evidence["required_items_per_day"] == pytest.approx(1.4286)
    assert finding.evidence["projected_days_to_finish"] == 20.0


def test_milestone_on_pace_is_not_at_risk() -> None:
    assert _keys(_milestone(open_issues=3)) == []


def test_overdue_milestone_with_open_items_is_at_risk_even_without_created_at() -> None:
    (finding,) = _detect(_milestone(due_on=_ago(days=2), created_at=None))
    assert finding.evidence["overdue"] is True
    assert finding.severity == HIGH


@pytest.mark.parametrize(
    "overrides",
    [
        {"due_on": AT + timedelta(days=15)},  # beyond the 14-day horizon
        {"due_on": None},
        {"state": "closed"},
        {"open_issues": 0},
        {"created_at": None},  # not overdue and pace unknowable: never guess
    ],
)
def test_milestone_not_evaluated(overrides: dict[str, Any]) -> None:
    assert _keys(_milestone(**overrides)) == []


# --- Ranking and recommendations ----------------------------------------------------------


def test_findings_rank_by_severity_then_confidence_then_rule_order() -> None:
    stale = _stale_prs(6)  # HIGH, confidence 0.9
    # 3 of 12 PRs (6 merged + the 6 open stale ones) are oversized: exactly 25% -> LOW.
    sized = _sized([900, 900, 900, 50, 60, 70], [], start=500)
    # HIGH (needs 40x its pace so far), confidence 0.62: only 3 items, not overdue.
    milestone = _milestone(open_issues=2, closed_issues=1, due_on=AT + timedelta(days=1))
    data = RepositoryData(
        pull_requests=stale.pull_requests + sized.pull_requests,
        milestones=milestone.milestones,
    )
    findings = _detect(data)
    assert [(f.rule, f.severity, f.confidence.score) for f in findings] == [
        (STALE_PULL_REQUESTS, HIGH, 0.9),
        (MILESTONE_AT_RISK, HIGH, 0.62),
        (OVERSIZED_PULL_REQUESTS, LOW, 0.6),
    ]


def test_recommendations_follow_finding_rank_and_cite_items() -> None:
    fixture = load_fixture("review_bottleneck")
    result = run_analysis(fixture.data, fixture.windows)
    assert [r.action for r in result.recommendations] == [
        "REVIEW_BOTTLENECK.redistribute_reviews",
        "REVIEW_BOTTLENECK.review_oldest_first",
        "REVIEW_BOTTLENECK.set_review_target",
    ]
    assert [r.rank for r in result.recommendations] == [1, 2, 3]
    assert result.recommendations[1].item_numbers == (306, 307, 308)
    assert "#306, #307, #308" in result.recommendations[1].detail


def test_redistribute_is_skipped_without_concentration() -> None:
    (finding,) = _detect(_bottleneck_data(30))  # pending PRs have no requested reviewers
    actions = [r.action for r in recommend((finding,))]
    assert actions[0] == "REVIEW_BOTTLENECK.review_oldest_first"


def test_every_rule_has_a_playbook() -> None:
    data = RepositoryData(
        pull_requests=_stale_prs(3).pull_requests,
        issues=_issues(8, 4).issues,
        milestones=_milestone().milestones,
    )
    findings = _detect(data)
    assert {f.rule for f in findings} == {
        STALE_PULL_REQUESTS,
        ISSUE_BACKLOG_GROWTH,
        MILESTONE_AT_RISK,
    }
    recommendations = recommend(findings)
    assert {r.finding_key for r in recommendations} == {f.key for f in findings}
