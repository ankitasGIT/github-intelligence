import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_node_id", name="uq_milestones_repo_node"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False
    )
    github_node_id: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    due_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
