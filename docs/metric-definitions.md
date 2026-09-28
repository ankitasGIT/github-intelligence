# Metric Definitions

This document is the contract for the analytics engine (Phase 3) and detection engine (Phase 4). Every metric below must be implemented exactly as defined here — if a definition needs to change, this file changes first, and the change is versioned (see "Metric versioning").

## Conventions

- **All timestamps are UTC.** Every fixture, model, and computation uses UTC exclusively; there is no per-user timezone conversion in the MVP.
- **`analysis_at`** is the fixed instant a metric snapshot is computed "as of." It is not always wall-clock `now` — in tests and fixtures it's a fixed timestamp so results are reproducible.
- **Current window** = `[analysis_at - 7d, analysis_at)`. **Baseline window** = `[analysis_at - 37d, analysis_at - 7d)` — the 30 days immediately preceding the current window, non-overlapping.
- **Minimum sample size (`n_min`).** A metric's raw value (median, count, etc.) is always computed and always testable — the math never refuses to run. Separately, a **display policy** decides whether the UI/API shows the number or `"insufficient_data"`. This document's `expected` blocks in `tests/fixtures/*.json` always report both: the raw computed value and whether it clears the display threshold. Do not conflate "couldn't compute" with "computed, but too small a sample to trust."
- **Draft PRs** are excluded from every metric until they leave draft state. A PR's `ready_for_review_at` (null while draft) stands in for `created_at` in every formula below once set.
- **Reopened PRs** (`reopened: true`) are excluded from every duration metric — PR cycle time, review-wait time, and merge-wait time — because a PR that was closed and later reopened has an elapsed span that includes an unknown closed period, which would distort any of these. They still count normally in pending-review count and staleness (which look at current state, not elapsed duration), and in PR size (a line-count, not a duration).

## Metric versioning

Every stored `metric_snapshot` records the version of this document (or a `model_version` string once formulas stabilize) alongside its inputs. Changing a formula or threshold is a new version, never a silent edit to historical snapshots.

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

**Included:** Merged PRs with at least one `APPROVED` review.
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

**Definition:** Lines changed per merged PR.

**Formula:** `additions + deletions`, per PR; reported as median across merged, non-draft PRs in the window, plus the count/share exceeding an oversized threshold.

**Oversized threshold:** `400` lines changed (additions + deletions). This is a documented, tunable constant — not derived per-repository in the MVP.

**Display threshold:** `n_min = 5` for the median; the oversized count/share is shown regardless of `n` since it's a simple count, not a trend.

---

## Detection rule thresholds (cross-reference)

These consume the metrics above. Full detector implementation is Phase 4; thresholds are fixed here so fixtures can encode expected findings now.

| Rule | Trigger |
|---|---|
| `REVIEW_BOTTLENECK` | Review-wait median rises ≥50% vs. baseline, current sample `n >= 5`, and pending-review count is elevated relative to its own recent norm. |
| `STALE_PULL_REQUESTS` | At least 3 open, non-draft PRs are stale (see §6). |
| `ISSUE_BACKLOG_GROWTH` | Open issues increase ≥25% vs. baseline, and baseline count `>= 5` (see §7). |
| `OVERSIZED_PULL_REQUESTS` | At least 25% of a window's merged/open non-draft PRs exceed the oversized threshold, with `n >= 5` PRs in the sample. |
| `MILESTONE_AT_RISK` | A milestone's due date is near and its trailing completion rate is below what's required to finish on time. Full formula defined when Phase 4 implements this rule — not covered by a Phase 1 fixture. |

## How to read `expected` blocks in fixtures

Each file in `tests/fixtures/*.json` carries an `"expected"` object with the hand-calculated ground truth for that scenario, matching the shapes above (`{value, n, meets_minimum_sample}` per metric) plus a top-level `findings_expected` array and a short `findings_rationale`. Phase 3 and Phase 4 unit tests assert directly against these numbers — see `tests/fixtures/README.md` for the full schema.
