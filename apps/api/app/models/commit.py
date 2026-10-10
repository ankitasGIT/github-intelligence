import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_node_id", name="uq_commits_repo_node"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False
    )
    github_node_id: Mapped[str] = mapped_column(String, nullable=False)
    sha: Mapped[str] = mapped_column(String, nullable=False)
    author_login: Mapped[str | None] = mapped_column(String, nullable=True)
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
