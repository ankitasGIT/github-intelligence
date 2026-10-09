import os

from cryptography.fernet import Fernet

# Must happen before the first get_settings() call anywhere in the app -- pydantic-settings
# prefers real environment variables over a .env file, so this reliably wins in tests
# regardless of what a developer's local .env contains.
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("GITHUB_TOKEN", "test-token-unused-mocked")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app import models  # noqa: E402,F401 -- populates Base.metadata before create_all/drop_all
from app.core.config import get_settings  # noqa: E402
from app.core.security import encrypt_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.github_connection import GithubConnection  # noqa: E402
from app.models.repository import Repository  # noqa: E402


@pytest.fixture(scope="session")
def _engine():
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(_engine) -> Session:
    """Each test gets a clean slate: truncate every table before running."""
    session_factory = sessionmaker(bind=_engine)
    session = session_factory()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seeded_repository(db_session: Session) -> Repository:
    connection = GithubConnection(login="octocat", encrypted_token=encrypt_token("fake-pat"))
    db_session.add(connection)
    db_session.commit()

    repository = Repository(
        github_connection_id=connection.id,
        github_node_id="R_1",
        owner="acme",
        name="backend",
        full_name="acme/backend",
    )
    db_session.add(repository)
    db_session.commit()
    db_session.refresh(repository)
    return repository
