# Metric Definitions

This document is the contract for the analytics engine (Phase 3), and the detection engine and health model (Phase 4). Every metric below must be implemented exactly as defined here — if a definition needs to change, this file changes first, and the change is versioned (see "Metric versioning").

## Conventions

- **All timestamps are UTC.** Every fixture, model, and computation uses UTC exclusively; there is no per-user timezone conversion in the MVP.
- **`analysis_at`** is the fixed instant a metric snapshot is computed "as of." It is not always wall-clock `now` — in tests and fixtures it's a fixed timestamp so results are reproducible.
- **Current window** = `[analysis_at - 7d, analysis_at)`. **Baseline window** = `[analysis_at - 37d, analysis_at - 7d)` — the 30 days immediately preceding the current window, non-overlapping.
- **Minimum sample size (`n_min`).** A metric's raw value (median, count, etc.) is always computed and always testable — the math never refuses to run. Separately, a **display policy** decides whether the UI/API shows the number or `"insufficient_data"`. This document's `expected` blocks in `tests/fixtures/*.json` always report both: the raw computed value and whether it clears the display threshold. Do not conflate "couldn't compute" with "computed, but too small a sample to trust."
- **Draft PRs** are excluded from every metric until they leave draft state. A PR's `ready_for_review_at` (null while draft) stands in for `created_at` in every formula below once set.
- **Reopened PRs** (`reopened: true`) are excluded from every duration metric — PR cycle time, review-wait time, and merge-wait time — because a PR that was closed and later reopened has an elapsed span that includes an unknown closed period, which would distort any of these. They still count normally in pending-review count and staleness (which look at current state, not elapsed duration), and in PR size (a line-count, not a duration).

## Metric versioning

Every stored `metric_snapshot` records a `model_version` string alongside its inputs. The current version is **`metrics-v1`** (`analytics.metrics.METRICS_VERSION`). Changing a formula or threshold is a new version, never a silent edit to historical snapshots.

---

## 1. PR Cycle Time

**Definition:** Wall-clock time from a PR's creation (or, for a PR that was a draft, from when it left draft) to merge.

**Formula:** `merged_at - (ready_for_review_at or created_at)`, in hours.

**Included:** PRs merged within the selected window.
**Excluded:** PRs not merged (open or closed-without-merge); reopened PRs (see conventions).

**Reported as:** median and sample size `n` (not mean — a handful of very slow PRs shouldn't dominate the number).

**Display threshold:** `n_min = 5`. Below that, the median is still computed and stored, but the API/UI shows `"insufficient_data"`.

**Worked example:** Four PRs merge in the current window with cycle times of 3h, 4h, 6h, 7h. Median = (4h + 6h) / 2 = **5h**, n = 4 (below `n_min`, so this example alone would display as insufficient data — see `tests/fixtures/quick_review.json` for the full picture including its baseline).

---

## 2. Review-Wait Time

**Definition:** Time from a PR becoming reviewable to its first submitted review (any state: `COMMENTED`, `CHANGES_REQUESTED`, or `APPROVED` — whichever comes first).

**Formula:** `first_review_submitted_at - (ready_for_review_at or created_at)`, in hours.

**Included:** PRs *created* within the selected window that have received at least one review (the review itself may land after the window closes).
**Excluded:** PRs with zero reviews so far (they show up in **Pending-Review Count** instead, not here) — this keeps review-wait from being biased downward by simply ignoring the PRs waiting the longest. Reopened PRs excluded (see conventions).

**Reported as:** median and sample size `n`.

**Display threshold:** `n_min = 5`.

**Worked example:** see `tests/fixtures/review_bottleneck.json` — baseline median 6h (n=6) vs current median 20h (n=5) is a **+233% change**, the canonical trigger for `REVIEW_BOTTLENECK`.

---

## 3. Merge-Wait Time

**Definition:** Time from first approval to merge.

**Formula:** `merged_at - first_approved_at`, in hours.

**Included:** PRs merged within the selected window (same window rule as §1) with at least one `APPROVED` review submitted before the merge.
**Excluded:** PRs merged without any approval (e.g. an admin override) — the count of excluded PRs is always disclosed alongside the metric, never silently dropped. Reopened PRs excluded (see conventions).

**Reported as:** median, sample size `n`, and `excluded_no_approval` count.

**Display threshold:** `n_min = 5`.

---

## 4. Pending-Review Count

**Definition:** A live, point-in-time snapshot (not windowed by creation date) of open, non-draft PRs that have received **zero** reviews since their last meaningful update (`updated_at`).

**Formula:** `count(PR where is_draft = false and status = open and no review.submitted_at > PR.updated_at)`.

**Note:** unlike the duration metrics above, this looks at *all currently open PRs regardless of when they were created* — a PR opened 20 days ago and still unreviewed is still "pending" today.

**Reported as:** a single integer, always displayed (no minimum sample — zero is a valid, meaningful answer).

---

## 5. Review Concentration

**Definition:** Among the PRs counted in Pending-Review Count, the share of outstanding review requests held by the single most-loaded reviewer.

**Formula:** `max(count of pending PRs requesting reviewer X) / count(pending PRs with at least one requested reviewer)`.

**Display threshold:** requires at least 3 pending PRs with a requested reviewer; below that, `"insufficient_data"` (a single PR "concentrating" 100% of requests on one person is not a meaningful signal).

**Worked example:** 7 pending PRs, 5 of them requesting `rahul` → concentration = 5 / 7 = **71.4%**.

---

## 6. Stale PR / Issue

**Definition:** An open, non-draft PR or open issue whose `updated_at` is at least `staleness_threshold_days` (default **5**) before `analysis_at`.

**Formula:** `analysis_at - item.updated_at >= 5 days`.

**Reported as:** count and the list of affected item numbers/ages. Feeds the `STALE_PULL_REQUESTS` rule, which requires **at least 3** qualifying PRs to fire — a single old PR is normal, not a finding.

---

## 7. Issue Backlog Growth

**Definition:** Percentage change in open-issue count, comparing the count at the end of the current window to the count at the end of the baseline window.

**Formula:** `(current_open_count - baseline_open_count) / baseline_open_count * 100`.

**Display threshold:** requires `baseline_open_count >= 5` (a minimum absolute count — "backlog doubled from 2 to 4 issues" is not a meaningful 100% growth signal).

---

## 8. PR Size

**Definition:** Lines changed per merged PR, for PRs merged within the selected window (same window rule as §1). Reopened PRs are included — a line count isn't distorted by a closed period.

**Formula:** `additions + deletions`, per PR; reported as median across merged, non-draft PRs in the window, plus the count/share exceeding an oversized threshold.

**Oversized threshold:** strictly more than `400` lines changed (additions + deletions). This is a documented, tunable constant — not derived per-repository in the MVP.

**Display threshold:** `n_min = 5` for the median; the oversized count/share is shown regardless of `n` since it's a simple count, not a trend.

---

## Implementation clarifications (metrics-v1)

Decisions the definitions above left open, fixed here so the engine and fixtures agree:

- **Window anchor per metric.** Cycle time, merge-wait, and PR size bucket a PR by **`merged_at`** ("merged within the window"). Review-wait buckets by **reviewable time** (`ready_for_review_at or created_at`). A PR can therefore be baseline for review-wait but current for cycle time — `timezone_boundary.json`'s PR_802 exercises exactly this.
- **No future data.** Only reviews submitted at or before `analysis_at` count, for every metric, so a snapshot computed "as of" a past instant is reproducible.
- **Review on a draft.** If a PR's first review predates `ready_for_review_at`, its review-wait is `0`, never negative.
- **Disclosed exclusions.** Every duration sample reports `excluded_draft` (PRs still in draft whose reviewable time falls in the window), `excluded_reopened`, and — merge-wait only — `excluded_no_approval`, plus the `pr_numbers` it included.
- **Display status.** Every sample-based metric carries `status: "ok" | "insufficient_data"` next to its raw value. `pct_change` is computed whenever both values exist and the baseline is non-zero, rounded to 4 decimal places; it is `null` otherwise.
- **Review concentration tie-break.** Equal counts resolve alphabetically by login; a reviewer requested twice on one PR counts once.
- **Staleness** is inclusive (`>=` threshold) and is reported for both open non-draft PRs and open issues.
- **"Open at instant T"** (pending count, staleness, backlog): `created_at <= T` and not yet merged/closed at `T`.
- **Known limitation.** GitHub bumps a PR's `updated_at` when a review is submitted, so on real synced data §4's "no review after the latest update" is true of nearly every open PR. Pending-review count therefore currently approximates "open, non-draft PRs". Refining this needs timeline events (author pushes/comments) and is a future version.

## Detection rules (rules-v1)

The detection engine (`analytics.detection`, version **`rules-v1`**) evaluates these rules against a computed metrics report. A rule never fires on a metric whose `status` is `insufficient_data`. Thresholds live in `analytics.detection.DetectionConfig`; changing one is a new rules version.

Every finding stores: `severity`, a deterministic `confidence`, the `evidence` numbers that triggered it, the `affected_items` (PRs, issues, or milestones) behind those numbers, `metric_refs` (the sections of this document it relies on), and its `rule_version`. Findings are worded as likely contributing factors, never causes, and describe queues and distribution, not individual performance.

### `REVIEW_BOTTLENECK`

Fires when **all** hold:

1. Review-wait (§2) current **and** baseline samples both meet `n_min`, and the median rose **≥ 50%**.
2. The **awaiting-first-review count** now is **≥ 3** and **≥ 1.5×** its **baseline norm**.

*Awaiting first review at instant T* = open, non-draft PRs that were reviewable at T (`ready_for_review_at or created_at <= T`) with no review submitted at or before T. Unlike §4, this can be reconstructed for any past instant from stored timestamps, because it doesn't depend on a PR's *current* `updated_at`. The **baseline norm** is the median of this count sampled once a day at `baseline_start + k days` for each of the 30 baseline days. Evidence also reports the §4 pending-review count and §5 review concentration.

Severity: **high** if the review-wait increase is ≥ 100% (twice the threshold), else **medium**. Corroborating indicators: review concentration ≥ 50% (status ok); PR cycle time rising (current sample ok); at least one pending PR is also stale.

Affected items: PRs awaiting first review, oldest-waiting first.

### `STALE_PULL_REQUESTS`

Fires when **at least 3** open, non-draft PRs are stale (§6). Severity **high** at ≥ 6, else **medium**. Corroborating: at least one stale PR has never been reviewed; the longest-idle PR has been idle ≥ 2× the staleness threshold. Affected items: the stale PRs, longest-idle first.

### `ISSUE_BACKLOG_GROWTH`

Fires when §7 has status ok (baseline open count ≥ 5) and growth is **≥ 25%**. Severity **high** at ≥ 50%, else **medium**. Corroborating: stale issues exist. Evidence includes open-issue age buckets (`under_7d`, `7_to_30d`, `30_to_90d`, `over_90d`). Affected items: the 10 oldest open issues.

### `OVERSIZED_PULL_REQUESTS`

Population: non-draft PRs merged in the current window (as §8) **plus** every open non-draft PR. Fires when the population has **≥ 5** PRs and **≥ 25%** exceed the §8 oversized threshold (> 400 lines). Severity **medium** at ≥ 50%, else **low**. Corroborating: §8 median size rose vs. baseline (both samples ok); PR cycle time rising. Evidence includes a size distribution (`up_to_100`, `101_to_400`, `401_to_1000`, `over_1000`). Affected items: the oversized PRs, largest first.

### `MILESTONE_AT_RISK`

Evaluated per milestone; the finding key is `MILESTONE_AT_RISK:<number>`. A milestone is evaluated only if it is open, has a `due_on`, has at least one open item, and is due within **14 days** (or is already overdue). It is at risk if:

- it is **overdue** (`due_on <= analysis_at`), or
- **observed pace < required pace**, where observed pace = `closed_issues / max(1, days since created_at)` and required pace = `open_issues / days until due_on`.

GitHub reports only current open/closed totals for a milestone, not their history, so "trailing completion rate" in the MVP is the average pace since the milestone was created. A milestone with no `created_at` (synced before Phase 4) is evaluated only for being overdue. Its pace is never guessed. Milestone counts are as of the last sync, not reconstructed as of `analysis_at`.

Severity **high** if overdue or the required pace is ≥ 2× the observed pace, else **medium**. Corroborating: overdue; more items open than closed.

### Confidence

```text
confidence = 0.4 * sample + 0.4 * strength + 0.2 * corroboration

sample        = min(1, n / (2 * n_min))           -- 1.0 at twice the minimum sample
strength      = min(1, observed / threshold / 2)  -- 0.5 exactly at the trigger, 1.0 at twice it
corroboration = share of the rule's corroborating indicators that hold
```

Level: **high** ≥ 0.75, **medium** ≥ 0.5, otherwise **low**. Per rule, `n` / `n_min` are: review-wait current sample / `n_min`; stale PR count / 3; baseline open issues / 5; oversized population / 5; milestone total items / 5. For a milestone, `observed / threshold` is required pace ÷ observed pace (treated as maximal when overdue or nothing has closed yet).

### Ranking and recommendations

Findings rank by severity (high first), then confidence (descending), then the rule order above. Each rule maps to a fixed playbook of recommended actions (`analytics.recommendations`), templated from that finding's evidence and affected items. Recommendations inherit the finding order and get a global `rank` starting at 1.

## Health model (health-v1)

`analytics.health`, version **`health-v1`**. Five dimensions each start at 100 and lose capped penalty points. A penalty applies only when its signal's metric has status ok. Improvements never add points.

| Dimension (weight) | Signal | Penalty | Cap | Primary |
|---|---|---|---|---|
| Delivery (0.30) | PR cycle time % change vs baseline | 0.4 / +1% | 40 | yes |
| | Merge-wait % change vs baseline | 0.2 / +1% | 20 | yes |
| Reviews (0.25) | Review-wait % change vs baseline | 0.4 / +1% | 40 | yes |
| | Review concentration above 50% | 1 / percentage point | 25 | no |
| | Stale open PRs | 5 each | 25 | no |
| Issues (0.20) | Issue backlog growth % | 0.8 / +1% | 40 | yes |
| | Stale open issues | 3 each | 30 | no |
| PR quality (0.15) | Share of merged PRs oversized (§8, current sample ok) | 1 / percentage point | 50 | yes |
| Activity (0.10) | Merged-PR throughput vs. baseline weekly rate (`baseline n * 7/30`, baseline sample ok) | 0.5 / -1% | 50 | yes |

Duration signals require both current and baseline samples to meet `n_min`. A dimension is **scored** only if at least one *primary* signal is evaluable. Otherwise it is `insufficient_data`, and its secondary penalties are not applied. The **overall** score is the weighted mean of scored dimensions, with weights renormalized over those dimensions. It is reported only when **delivery and reviews** are both scored; otherwise the overall score is `insufficient_data`. Every snapshot stores each penalty's signal value, points, cap, and formula.

Worked example (`review_bottleneck.json`): delivery 60 (cycle time +175% → 40, capped), reviews 33.6 (review-wait capped 40 + concentration 21.43 + one stale PR 5), issues insufficient, PR quality 100, activity 100 → overall `(0.30·60 + 0.25·33.6 + 0.15·100 + 0.10·100) / 0.80 ≈ 64.2`.

## Weekly trend

Each snapshot stores eight consecutive 7-day windows ending at `analysis_at`, with the PR cycle time (§1) and review-wait (§2) median, `n`, and status for each. All are computed as of `analysis_at`, so the newest point equals the snapshot's current-window values.

## How to read `expected` blocks in fixtures

Each file in `tests/fixtures/*.json` carries an `"expected"` object with the hand-calculated ground truth for that scenario, matching the shapes above (`{value, n, meets_minimum_sample}` per metric) plus a top-level `findings_expected` array and a short `findings_rationale`. Phase 3 and Phase 4 unit tests assert directly against these numbers — see `tests/fixtures/README.md` for the full schema.
