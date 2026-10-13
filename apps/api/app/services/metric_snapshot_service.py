"""Runs the analytics engine over a repository's stored GitHub data and persists the result as
an immutable metric_snapshots row with its findings and recommendations. All metric, health,
and rule logic lives in packages/analytics; this module only loads rows, maps them to the
engine's plain inputs, and records provenance."""

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from analytics import (
    DEFAULT_CONFIG,
    HEALTH_VERSION,
    AnalysisWindows,
    MetricConfig,
    RepositoryData,
    run_analysis,
    trend_start,
)
from analytics import Issue as IssueInput
from analytics import Milestone as MilestoneInput
from analytics import PullRequest as PullRequestInput
from analytics import Review as ReviewInput
from analytics.serialization import to_jsonable
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finding import Finding
from app.models.issue import Issue
from app.models.metric_snapshot import MetricSnapshot
from app.models.milestone import Milestone
from app.models.pull_request import PullRequest
from app.models.recommendation import Recommendation
from app.models.repository import Repository
from app.models.review import Review
from app.models.review_request import ReviewRequest
from app.models.sync_run import SyncRun, SyncRunStatus


class RepositoryNotFoundError(LookupError):
    pass


class MetricSnapshotService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        repository_id: UUID,
        analysis_at: datetime | None = None,
        config: MetricConfig = DEFAULT_CONFIG,
    ) -> MetricSnapshot:
        if self.session.get(Repository, repository_id) is None:
            raise RepositoryNotFoundError(f"Repository {repository_id} not found")

        analysis_at = (analysis_at or datetime.now(UTC)).replace(microsecond=0)
        windows = AnalysisWindows.ending_at(
            analysis_at, config.current_window_days, config.baseline_window_days
        )
        data = self._load(repository_id, windows, config)
        result = run_analysis(data, windows, config)
        report = result.metrics
        source_sync = self.last_successful_sync(repository_id)

        snapshot = MetricSnapshot(
            repository_id=repository_id,
            analysis_at=analysis_at,
            window_start=windows.current.start,
            window_end=windows.current.end,
            baseline_start=windows.baseline.start,
            baseline_end=windows.baseline.end,
            model_version=report.metrics_version,
            inputs={
                "config": asdict(config),
                "pull_requests": len(data.pull_requests),
                "issues": len(data.issues),
                "milestones": len(data.milestones),
                "source_sync_run_id": str(source_sync.id) if source_sync else None,
                "source_synced_at": (
                    source_sync.finished_at.isoformat()
                    if source_sync and source_sync.finished_at
                    else None
                ),
            },
            metrics=report.to_dict(),
            detection_version=result.detection_version,
            health_version=HEALTH_VERSION,
            health=result.health.to_dict(),
            trend=result.trend_dict(),
        )
        self.session.add(snapshot)
        self.session.flush()

        finding_ids: dict[str, UUID] = {}
        for rank, finding in enumerate(result.findings, start=1):
            row = Finding(
                snapshot_id=snapshot.id,
                repository_id=repository_id,
                key=finding.key,
                rule=finding.rule,
                rule_version=finding.rule_version,
                rank=rank,
                severity=finding.severity,
                confidence=finding.confidence.score,
                confidence_detail=to_jsonable(finding.confidence),
                title=finding.title,
                summary=finding.summary,
                evidence=to_jsonable(finding.evidence),
                affected_items=to_jsonable(finding.affected_items),
                metric_refs=list(finding.metric_refs),
            )
            self.session.add(row)
            self.session.flush()
            finding_ids[finding.key] = row.id
        for recommendation in result.recommendations:
            self.session.add(
                Recommendation(
                    finding_id=finding_ids[recommendation.finding_key],
                    snapshot_id=snapshot.id,
                    action=recommendation.action,
                    rank=recommendation.rank,
                    title=recommendation.title,
                    detail=recommendation.detail,
                    item_numbers=list(recommendation.item_numbers),
                )
            )
        self.session.commit()
        self.session.refresh(snapshot)
        return snapshot

    def latest(self, repository_id: UUID) -> MetricSnapshot | None:
        stmt = (
            select(MetricSnapshot)
            .where(MetricSnapshot.repository_id == repository_id)
            .order_by(MetricSnapshot.analysis_at.desc(), MetricSnapshot.computed_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def last_successful_sync(self, repository_id: UUID) -> SyncRun | None:
        stmt = (
            select(SyncRun)
            .where(
                SyncRun.repository_id == repository_id,
                SyncRun.status == SyncRunStatus.success.value,
            )
            .order_by(SyncRun.finished_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def _load(
        self, repository_id: UUID, windows: AnalysisWindows, config: MetricConfig
    ) -> RepositoryData:
        # The weekly trend reaches further back than the baseline window.
        since = min(windows.baseline.start, trend_start(windows.analysis_at, config))
        # Anything that could land in either window, plus everything still open (pending and
        # stale metrics are point-in-time and look at PRs of any age).
        pr_rows = list(
            self.session.execute(
                select(PullRequest).where(
                    PullRequest.repository_id == repository_id,
                    PullRequest.created_at <= windows.analysis_at,
                    or_(
                        PullRequest.closed_at.is_(None),
                        PullRequest.closed_at >= since,
                        PullRequest.merged_at >= since,
                        PullRequest.created_at >= since,
                        PullRequest.ready_for_review_at >= since,
                    ),
                )
            ).scalars()
        )
        pr_ids = [pr.id for pr in pr_rows]

        reviews: dict[UUID, list[ReviewInput]] = defaultdict(list)
        requested: dict[UUID, list[str]] = defaultdict(list)
        if pr_ids:
            for review in self.session.execute(
                select(Review).where(Review.pull_request_id.in_(pr_ids))
            ).scalars():
                reviews[review.pull_request_id].append(
                    ReviewInput(review.reviewer_login, review.state, review.submitted_at)
                )
            for request in self.session.execute(
                select(ReviewRequest).where(
                    ReviewRequest.pull_request_id.in_(pr_ids),
                    ReviewRequest.is_outstanding.is_(True),
                )
            ).scalars():
                requested[request.pull_request_id].append(request.requested_reviewer_login)

        # Backlog growth needs every issue open at the end of the baseline window.
        issue_rows = self.session.execute(
            select(Issue).where(
                Issue.repository_id == repository_id,
                Issue.created_at <= windows.analysis_at,
                or_(Issue.closed_at.is_(None), Issue.closed_at > windows.baseline.end),
            )
        ).scalars()

        return RepositoryData(
            pull_requests=tuple(
                PullRequestInput(
                    number=pr.number,
                    created_at=pr.created_at,
                    updated_at=pr.updated_at,
                    is_draft=pr.is_draft,
                    ready_for_review_at=pr.ready_for_review_at,
                    closed_at=pr.closed_at,
                    merged_at=pr.merged_at,
                    reopened=pr.reopened,
                    additions=pr.additions,
                    deletions=pr.deletions,
                    changed_files=pr.changed_files,
                    reviews=tuple(sorted(reviews[pr.id], key=lambda r: r.submitted_at)),
                    requested_reviewers=tuple(sorted(requested[pr.id])),
                )
                for pr in sorted(pr_rows, key=lambda pr: pr.number)
            ),
            issues=tuple(
                IssueInput(
                    number=issue.number,
                    created_at=issue.created_at,
                    updated_at=issue.updated_at,
                    closed_at=issue.closed_at,
                )
                for issue in issue_rows
            ),
            milestones=tuple(
                MilestoneInput(
                    number=m.number,
                    title=m.title,
                    state=m.state,
                    due_on=m.due_on,
                    open_issues=m.open_issues,
                    closed_issues=m.closed_issues,
                    created_at=m.created_at,
                )
                for m in self.session.execute(
                    select(Milestone)
                    .where(Milestone.repository_id == repository_id)
                    .order_by(Milestone.number)
                ).scalars()
            ),
        )
