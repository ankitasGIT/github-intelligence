import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_node_id", name="uq_pull_requests_repo_node"),
        Index("ix_pull_requests_repo_created", "repository_id", "created_at"),
        Index("ix_pull_requests_repo_updated", "repository_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False
    )
    github_node_id: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author_login: Mapped[str | None] = mapped_column(String, nullable=True)

    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Null until a follow-up timeline-events sync populates it; metric-definitions.md's
    # convention (null => use created_at) is exactly the correct fallback until then.
    ready_for_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Not derivable from the REST pulls API without a timeline-events sync (follow-up); defaults
    # to false/0, meaning duration metrics currently never apply the reopened-exclusion in
    # practice on real synced data -- only in the Phase 1 synthetic fixtures.
    reopened: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
