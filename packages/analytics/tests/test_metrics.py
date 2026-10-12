"""Asserts the metrics engine against hand-calculated ground truth.

The first half is driven entirely by the `expected` blocks in tests/fixtures/*.json; the second
half covers boundaries the fixtures don't (issue backlog, tie-breaks, exact thresholds).
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fixture_loader import FIXTURE_NAMES, Fixture, load_fixture

from analytics.metrics import (
    INSUFFICIENT_DATA,
    OK,
    DurationSample,
    MetricsReport,
    compute_metrics,
    issue_backlog_growth,
    pending_reviews,
    pr_size,
    review_concentration,
    review_wait_sample,
    stale_pull_requests,
)
from analytics.models import AnalysisWindows, Issue, MetricConfig, PullRequest, Review

AT = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
WINDOWS = AnalysisWindows.ending_at(AT)


def _report(name: str) -> tuple[Fixture, MetricsReport]:
    fixture = load_fixture(name)
    return fixture, compute_metrics(fixture.data, fixture.windows)


# --- Fixture-driven ground truth ----------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_windows_match_engine_windows(name: str) -> None:
    fixture = load_fixture(name)
    window = fixture.raw["window"]
    assert fixture.windows.current.start == datetime.fromisoformat(window["current_start"])
    assert fixture.windows.current.end == datetime.fromisoformat(window["current_end"])
    assert fixture.windows.baseline.start == datetime.fromisoformat(window["baseline_start"])
    assert fixture.windows.baseline.end == datetime.fromisoformat(window["baseline_end"])


def _assert_duration_sample(actual: DurationSample, expected: dict[str, Any]) -> None:
    assert actual.median_hours == (
        pytest.approx(expected["median_hours"]) if expected["median_hours"] is not None else None
    )
    assert actual.n == expected["n"]
    assert actual.meets_minimum_sample is expected["meets_minimum_sample"]
    assert actual.status == (OK if expected["meets_minimum_sample"] else INSUFFICIENT_DATA)
    if "included" in expected:
        assert list(actual.pr_numbers) == expected["included"]
    for key in ("excluded_draft", "excluded_reopened", "excluded_no_approval"):
        if key in expected:
            assert getattr(actual, key) == expected[key], key


@pytest.mark.parametrize("metric", ["pr_cycle_time", "review_wait", "merge_wait"])
@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_duration_metrics_match_fixture(name: str, metric: str) -> None:
    fixture, report = _report(name)
    expected = fixture.expected[metric]
    actual = getattr(report, metric)

    _assert_duration_sample(actual.current, expected["current"])
    if "baseline" in expected:
        _assert_duration_sample(actual.baseline, expected["baseline"])
    if "pct_change" in expected:
        if expected["pct_change"] is None:
            assert actual.pct_change is None
        else:
            assert actual.pct_change == pytest.approx(expected["pct_change"], abs=0.01)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_pending_review_count_matches_fixture(name: str) -> None:
    fixture, report = _report(name)
    assert report.pending_review_count.count == fixture.expected["pending_review_count"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_review_concentration_matches_fixture(name: str) -> None:
    fixture, report = _report(name)
    expected = fixture.expected["review_concentration"]
    actual = report.review_concentration
    if expected == INSUFFICIENT_DATA:
        assert actual.meets_minimum_sample is False
        assert actual.status == INSUFFICIENT_DATA
        return
    assert actual.value == pytest.approx(expected["value"], abs=1e-4)
    assert actual.top_reviewer == expected["top_reviewer"]
    assert actual.pending_with_requested_reviewer == expected["pending_with_requested_reviewer"]
    assert actual.meets_minimum_sample is expected["meets_minimum_sample"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_stale_prs_match_fixture(name: str) -> None:
    fixture, report = _report(name)
    expected = fixture.expected["stale_prs"]
    actual = report.stale_pull_requests
    assert actual.count == expected["count"]
    assert actual.threshold_days == expected["threshold_days"]
    assert [item.number for item in actual.items] == expected["items"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_issue_backlog_growth_matches_fixture(name: str) -> None:
    fixture, report = _report(name)
    assert fixture.expected["issue_backlog_growth"] == INSUFFICIENT_DATA
    assert report.issue_backlog_growth.status == INSUFFICIENT_DATA


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_pr_size_matches_fixture(name: str) -> None:
    fixture, report = _report(name)
    expected = fixture.expected["pr_size"]
    actual = report.pr_size
    current = expected["current"]
    assert actual.current.median_lines == current["median_lines"]
    assert actual.current.n == current["n"]
    if "meets_minimum_sample" in current:
        assert actual.current.meets_minimum_sample is current["meets_minimum_sample"]
    assert actual.oversized_threshold_lines == expected["oversized_threshold_lines"]
    assert actual.oversized_count == expected["oversized_count"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_report_is_json_serializable_and_deterministic(name: str) -> None:
    fixture, report = _report(name)
    first = json.dumps(report.to_dict(), sort_keys=True)
    second = json.dumps(compute_metrics(fixture.data, fixture.windows).to_dict(), sort_keys=True)
    assert first == second
    assert report.to_dict()["analysis_at"] == "2026-09-25T09:00:00+00:00"


# --- Boundaries the fixtures don't cover --------------------------------------------------


def _pr(number: int, **kwargs: Any) -> PullRequest:
    defaults: dict[str, Any] = {"created_at": AT - timedelta(days=1), "updated_at": AT}
    return PullRequest(number=number, **{**defaults, **kwargs})


def _issue(number: int, created_days_ago: float, closed_days_ago: float | None = None) -> Issue:
    created = AT - timedelta(days=created_days_ago)
    closed = AT - timedelta(days=closed_days_ago) if closed_days_ago is not None else None
    return Issue(number=number, created_at=created, updated_at=closed or created, closed_at=closed)


def test_windows_reject_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        AnalysisWindows.ending_at(datetime(2026, 9, 25, 9, 0))


def test_issue_backlog_growth_computes_pct_change() -> None:
    # Open at baseline end (7 days ago): issues 1-5 (issue 6 not created yet, issue 7 closed).
    # Open at current end (now): issues 1-4 and 6 (issue 5 closed 2 days ago) plus 8 -> 6.
    issues = [_issue(n, 20) for n in range(1, 5)] + [
        _issue(5, 20, closed_days_ago=2),
        _issue(6, 3),
        _issue(7, 20, closed_days_ago=10),
        _issue(8, 1),
    ]
    result = issue_backlog_growth(issues, WINDOWS)
    assert result.baseline_open_count == 5
    assert result.current_open_count == 6
    assert result.pct_change == pytest.approx(20.0)
    assert result.status == OK


def test_issue_backlog_growth_below_minimum_baseline_is_insufficient_but_computed() -> None:
    issues = [_issue(n, 20) for n in range(1, 5)] + [_issue(n, 2) for n in range(5, 9)]
    result = issue_backlog_growth(issues, WINDOWS)
    assert (result.baseline_open_count, result.current_open_count) == (4, 8)
    assert result.pct_change == pytest.approx(100.0)
    assert result.status == INSUFFICIENT_DATA


def test_issue_backlog_growth_with_zero_baseline_has_no_pct_change() -> None:
    result = issue_backlog_growth([_issue(1, 1)], WINDOWS)
    assert result.pct_change is None
    assert result.status == INSUFFICIENT_DATA


def test_stale_threshold_is_inclusive_at_exactly_five_days() -> None:
    prs = [
        _pr(1, updated_at=AT - timedelta(days=5)),
        _pr(2, updated_at=AT - timedelta(days=5) + timedelta(seconds=1)),
        _pr(3, updated_at=AT - timedelta(days=9), is_draft=True),
    ]
    result = stale_pull_requests(prs, AT)
    assert [item.number for item in result.items] == [1]
    assert result.items[0].idle_days == 5.0


def test_review_concentration_breaks_ties_alphabetically() -> None:
    prs = [
        _pr(1, requested_reviewers=("zoe",)),
        _pr(2, requested_reviewers=("amit",)),
        _pr(3, requested_reviewers=("zoe", "amit")),
    ]
    result = review_concentration(prs, AT)
    assert result.top_reviewer == "amit"
    assert result.value == pytest.approx(0.6667)
    assert result.status == OK


def test_review_concentration_counts_a_reviewer_once_per_pr() -> None:
    prs = [_pr(n, requested_reviewers=("rahul", "rahul")) for n in (1, 2, 3)]
    assert review_concentration(prs, AT).value == 1.0


def test_pending_excludes_prs_reviewed_since_last_update_and_closed_prs() -> None:
    updated = AT - timedelta(hours=5)
    prs = [
        _pr(1, updated_at=updated, reviews=(Review("a", "COMMENTED", updated + timedelta(1)),)),
        _pr(2, updated_at=updated, reviews=(Review("a", "COMMENTED", AT - timedelta(hours=1)),)),
        _pr(3, updated_at=updated, reviews=(Review("a", "APPROVED", updated - timedelta(1)),)),
        _pr(4, closed_at=AT - timedelta(hours=1)),
        _pr(5, created_at=AT + timedelta(seconds=1)),
    ]
    # PR 1's review is after analysis_at (future data), so it is still pending as of AT;
    # PR 2 was reviewed after its last update; PR 3's review predates its last update.
    assert pending_reviews(prs, AT).pr_numbers == (1, 3)


def test_review_wait_ignores_reviews_after_analysis_at() -> None:
    created = AT - timedelta(days=2)
    pr = _pr(1, created_at=created, reviews=(Review("a", "APPROVED", AT + timedelta(hours=1)),))
    assert review_wait_sample([pr], WINDOWS.current, AT).n == 0


def test_review_wait_on_draft_reviewed_before_ready_is_zero_not_negative() -> None:
    ready = AT - timedelta(days=1)
    pr = _pr(
        1,
        created_at=AT - timedelta(days=3),
        ready_for_review_at=ready,
        reviews=(Review("a", "COMMENTED", ready - timedelta(hours=4)),),
    )
    assert review_wait_sample([pr], WINDOWS.current, AT).median_hours == 0.0


def test_pr_size_oversized_threshold_is_strictly_greater_than() -> None:
    merged = AT - timedelta(hours=1)
    prs = [
        _pr(1, merged_at=merged, additions=400),
        _pr(2, merged_at=merged, additions=300, deletions=101),
        _pr(3, merged_at=merged, additions=10),
        _pr(4, merged_at=merged, additions=20, reopened=True),
    ]
    result = pr_size(prs, WINDOWS)
    assert result.oversized_pr_numbers == (2,)
    assert result.oversized_share == pytest.approx(0.25)
    assert result.current.n == 4  # reopened PRs still count toward size


def test_config_thresholds_are_respected() -> None:
    fixture = load_fixture("quick_review")
    report = compute_metrics(fixture.data, fixture.windows, MetricConfig(min_sample=4))
    assert report.pr_cycle_time.current.status == OK
    assert report.to_dict()["config"]["min_sample"] == 4
