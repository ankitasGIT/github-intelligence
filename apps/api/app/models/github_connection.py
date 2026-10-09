import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.repository import Repository


class GithubConnection(Base):
    """A single GitHub PAT, encrypted at rest. MVP is single-tenant: one connection,
    reused across every registered repository."""

    __tablename__ = "github_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    login: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_token: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    repositories: Mapped[list["Repository"]] = relationship(back_populates="github_connection")
