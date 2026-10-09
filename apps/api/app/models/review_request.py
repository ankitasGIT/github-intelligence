import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewRequest(Base):
    """An outstanding (or since-resolved) review request. `is_outstanding` is cleared when the
    reviewer submits a review or GitHub reports the request removed -- see
    services.normalizers.resolve_stale_review_requests."""

    __tablename__ = "review_requests"
    __table_args__ = (
        UniqueConstraint(
            "pull_request_id", "requested_reviewer_login", name="uq_review_requests_pr_reviewer"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id"), nullable=False, index=True
    )
    requested_reviewer_login: Mapped[str] = mapped_column(String, nullable=False)
    is_outstanding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
