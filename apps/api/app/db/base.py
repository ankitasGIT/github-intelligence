from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. Phase 2 models (pull_requests, reviews, ...) inherit from this."""
