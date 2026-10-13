"""Health model and weekly trend, asserted against hand-calculated values."""

import pytest
from fixture_loader import FIXTURE_NAMES, load_fixture

from analytics.analysis import TREND_WEEKS, run_analysis
from analytics.health import HEALTH_VERSION, WEIGHTS, compute_health
from analytics.metrics import INSUFFICIENT_DATA, OK, compute_metrics


def _dimensions(name: str) -> dict[str, tuple[float | None, str]]:
    fixture = load_fixture(name)
    health = compute_health(compute_metrics(fixture.data, fixture.windows))
    return {d.dimension: (d.score, d.status) for d in health.dimensions}


def test_review_bottleneck_health_matches_hand_calculation() -> None:
    fixture = load_fixture("review_bottleneck")
    health = compute_health(compute_metrics(fixture.data, fixture.windows))
    # delivery: cycle time +175% -> 0.4 * 175 = 70, capped at 40; merge-wait flat -> 60.
    # reviews: review-wait +233% capped at 40; concentration (71.43 - 50) = 21.43; one stale
    #          PR = 5 -> 100 - 66.43 = 33.6.
    # issues: no issues at all -> insufficient. pr_quality: 0 oversized -> 100.
    # activity: 5 merged vs 6 * 7/30 = 1.4 expected -> throughput up, no penalty -> 100.
    assert _dimensions("review_bottleneck") == {
        "delivery": (60.0, OK),
        "reviews": (33.6, OK),
        "issues": (None, INSUFFICIENT_DATA),
        "pr_quality": (100.0, OK),
        "activity": (100.0, OK),
    }
    # (0.30*60 + 0.25*33.6 + 0.15*100 + 0.10*100) / 0.80 = 64.25
    assert health.overall == pytest.approx(64.25, abs=0.05)
    assert health.scored_weight == pytest.approx(0.80)
    assert health.status == OK
    assert health.model_version == HEALTH_VERSION


def test_penalties_record_value_points_cap_and_rule() -> None:
    fixture = load_fixture("review_bottleneck")
    health = compute_health(compute_metrics(fixture.data, fixture.windows))
    reviews = next(d for d in health.dimensions if d.dimension == "reviews")
    concentration = next(p for p in reviews.penalties if p.signal == "review_concentration")
    assert concentration.value == pytest.approx(0.7143)
    assert concentration.points == pytest.approx(21.43)
    assert concentration.cap == 25.0
    assert "above 50%" in concentration.rule


@pytest.mark.parametrize("name", [n for n in FIXTURE_NAMES if n != "review_bottleneck"])
def test_small_fixtures_withhold_overall_score(name: str) -> None:
    """Every other fixture is below n_min for delivery/reviews, so no overall score is shown."""
    fixture = load_fixture(name)
    health = compute_health(compute_metrics(fixture.data, fixture.windows))
    assert health.overall is None
    assert health.status == INSUFFICIENT_DATA


def test_empty_repository_scores_nothing() -> None:
    dims = _dimensions("empty_repository")
    assert all(status == INSUFFICIENT_DATA for _, status in dims.values())


def test_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_trend_newest_point_equals_current_window() -> None:
    fixture = load_fixture("review_bottleneck")
    result = run_analysis(fixture.data, fixture.windows)
    assert len(result.trend) == TREND_WEEKS
    newest = result.trend[-1]
    current = fixture.windows.current
    assert (newest.start, newest.end) == (current.start, current.end)
    assert newest.review_wait_median_hours == result.metrics.review_wait.current.median_hours
    assert newest.cycle_time_median_hours == result.metrics.pr_cycle_time.current.median_hours
    assert [p.start for p in result.trend] == sorted(p.start for p in result.trend)
    # Baseline PRs (Aug 20 - Sep 10) land in earlier weeks: 6 review-wait samples in total.
    assert sum(p.review_wait_n for p in result.trend[:-1]) == 6
