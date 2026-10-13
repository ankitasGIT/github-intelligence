"""Read-side assembly of findings, recommendations, and the dashboard from stored snapshots.

Nothing here recomputes analytics: every number comes from the immutable snapshot and its
findings rows. This module only joins them with source rows (titles, authors) and builds
GitHub links so each card on the dashboard traces back to the items behind it."""

from collections import defaultdict
from datetime import datetime
from uuid import UUID

from analytics import METRIC_DEFINITIONS, RULE_DEFINITIONS, Definition
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finding import Finding
from app.models.issue import Issue
from app.models.metric_snapshot import MetricSnapshot
from app.models.milestone import Milestone
from app.models.pull_request import PullRequest
from app.models.recommendation import Recommendation
from app.models.repository import Repository
from app.models.review_request import ReviewRequest
from app.models.sync_run import SyncRun
from app.schemas.dashboard import DashboardAnalysisRead, DashboardRead, QueueItemRead
from app.schemas.finding import (
    AffectedItemRead,
    DefinitionRead,
    FindingDetailRead,
    FindingRead,
    RecommendationRead,
)
from app.schemas.metric_snapshot import AnalysisWindowRead, DataCompletenessRead
from app.schemas.repository import RepositoryRead
from app.schemas.sync_run import SyncRunRead
from app.services.metric_snapshot_service import MetricSnapshotService

_URL_PATHS = {"pull_request": "pull", "issue": "issues", "milestone": "milestone"}


def github_url(repository: Repository, kind: str, number: int) -> str:
    return f"https://github.com/{repository.full_name}/{_URL_PATHS[kind]}/{number}"


def definition_read(definition: Definition) -> DefinitionRead:
    return DefinitionRead(
        key=definition.key,
        name=definition.name,
        definition=definition.definition,
        formula=definition.formula,
        unit=definition.unit,
        doc_anchor=definition.doc_anchor,
    )


def _days(later: datetime, earlier: datetime) -> float:
    return round((later - earlier).total_seconds() / 86400, 2)


class FindingsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Findings and recommendations -------------------------------------------------------

    def findings_for(
        self,
        repository: Repository,
        snapshot: MetricSnapshot,
        recommendations: list[RecommendationRead] | None = None,
    ) -> list[FindingRead]:
        rows = list(
            self.session.execute(
                select(Finding).where(Finding.snapshot_id == snapshot.id).order_by(Finding.rank)
            ).scalars()
        )
        by_finding: dict[UUID, list[RecommendationRead]] = defaultdict(list)
        if recommendations is None:
            recommendations = self.recommendations_for(snapshot)
        for rec in recommendations:
            by_finding[rec.finding_id].append(rec)
        titles = self._titles(repository, rows)
        return [
            self._finding_read(repository, snapshot, row, titles, by_finding[row.id])
            for row in rows
        ]

    def finding_detail(
        self, repository: Repository, finding_id: UUID
    ) -> FindingDetailRead | None:
        row = self.session.get(Finding, finding_id)
        if row is None or row.repository_id != repository.id:
            return None
        snapshot = self.session.get(MetricSnapshot, row.snapshot_id)
        assert snapshot is not None
        recommendations = [
            rec for rec in self.recommendations_for(snapshot) if rec.finding_id == row.id
        ]
        base = self._finding_read(
            repository, snapshot, row, self._titles(repository, [row]), recommendations
        )
        return FindingDetailRead(
            **base.model_dump(),
            rule_definition=definition_read(RULE_DEFINITIONS[row.rule]),
            metric_definitions=[
                definition_read(METRIC_DEFINITIONS[key])
                for key in row.metric_refs
                if key in METRIC_DEFINITIONS
            ],
        )

    def recommendations_for(self, snapshot: MetricSnapshot) -> list[RecommendationRead]:
        rows = self.session.execute(
            select(Recommendation)
            .where(Recommendation.snapshot_id == snapshot.id)
            .order_by(Recommendation.rank)
        ).scalars()
        return [
            RecommendationRead(
                id=row.id,
                finding_id=row.finding_id,
                action=row.action,
                rank=row.rank,
                title=row.title,
                detail=row.detail,
                item_numbers=row.item_numbers,
            )
            for row in rows
        ]

    def _finding_read(
        self,
        repository: Repository,
        snapshot: MetricSnapshot,
        row: Finding,
        titles: dict[tuple[str, int], str],
        recommendations: list[RecommendationRead],
    ) -> FindingRead:
        return FindingRead.model_validate(
            {
                "id": row.id,
                "snapshot_id": row.snapshot_id,
                "key": row.key,
                "rule": row.rule,
                "rule_version": row.rule_version,
                "rank": row.rank,
                "severity": row.severity,
                "confidence": row.confidence_detail,
                "title": row.title,
                "summary": row.summary,
                "evidence": row.evidence,
                "affected_items": [
                    AffectedItemRead(
                        kind=item["kind"],
                        number=item["number"],
                        title=titles.get((item["kind"], item["number"])),
                        url=github_url(repository, item["kind"], item["number"]),
                        detail=item["detail"],
                    )
                    for item in row.affected_items
                ],
                "metric_refs": row.metric_refs,
                "recommendations": recommendations,
                "analysis_at": snapshot.analysis_at,
            }
        )

    def _titles(self, repository: Repository, rows: list[Finding]) -> dict[tuple[str, int], str]:
        """One query per item kind, however many findings reference it."""
        numbers: dict[str, set[int]] = defaultdict(set)
        for row in rows:
            for item in row.affected_items:
                numbers[item["kind"]].add(item["number"])
        titles: dict[tuple[str, int], str] = {}
        for kind, model in (
            ("pull_request", PullRequest),
            ("issue", Issue),
            ("milestone", Milestone),
        ):
            if not numbers[kind]:
                continue
            for number, title in self.session.execute(
                select(model.number, model.title).where(
                    model.repository_id == repository.id, model.number.in_(numbers[kind])
                )
            ):
                titles[(kind, number)] = title
        return titles

    # --- Dashboard --------------------------------------------------------------------------

    def pending_queue(
        self, repository: Repository, snapshot: MetricSnapshot
    ) -> list[QueueItemRead]:
        numbers = snapshot.metrics["pending_review_count"]["pr_numbers"]
        if not numbers:
            return []
        prs = list(
            self.session.execute(
                select(PullRequest).where(
                    PullRequest.repository_id == repository.id, PullRequest.number.in_(numbers)
                )
            ).scalars()
        )
        requested: dict[UUID, list[str]] = defaultdict(list)
        for request in self.session.execute(
            select(ReviewRequest).where(
                ReviewRequest.pull_request_id.in_([pr.id for pr in prs]),
                ReviewRequest.is_outstanding.is_(True),
            )
        ).scalars():
            requested[request.pull_request_id].append(request.requested_reviewer_login)

        at = snapshot.analysis_at
        oldest_first = sorted(
            prs, key=lambda pr: (pr.ready_for_review_at or pr.created_at, pr.number)
        )
        return [
            QueueItemRead(
                number=pr.number,
                title=pr.title,
                url=github_url(repository, "pull_request", pr.number),
                author=pr.author_login,
                age_days=_days(at, pr.ready_for_review_at or pr.created_at),
                idle_days=_days(at, pr.updated_at),
                requested_reviewers=sorted(requested[pr.id]),
            )
            for pr in oldest_first
        ]

    def dashboard(self, repository: Repository) -> DashboardRead:
        latest_sync = self.session.execute(
            select(SyncRun)
            .where(SyncRun.repository_id == repository.id)
            .order_by(SyncRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        snapshot = MetricSnapshotService(self.session).latest(repository.id)
        analysis = None
        if snapshot is not None:
            recommendations = self.recommendations_for(snapshot)
            analysis = DashboardAnalysisRead.model_validate(
                {
                    "snapshot_id": snapshot.id,
                    "analysis_at": snapshot.analysis_at,
                    "computed_at": snapshot.computed_at,
                    "analysis_version": snapshot.model_version,
                    "detection_version": snapshot.detection_version,
                    "health_version": snapshot.health_version,
                    "window": AnalysisWindowRead.from_snapshot(snapshot),
                    "data_completeness": DataCompletenessRead.from_snapshot(snapshot),
                    "health": snapshot.health,
                    "metrics": snapshot.metrics,
                    "findings": self.findings_for(repository, snapshot, recommendations),
                    "recommendations": recommendations,
                    "trend": snapshot.trend or [],
                    "pending_queue": self.pending_queue(repository, snapshot),
                }
            )
        return DashboardRead(
            repository=RepositoryRead.model_validate(repository),
            latest_sync=SyncRunRead.model_validate(latest_sync) if latest_sync else None,
            analysis=analysis,
        )
