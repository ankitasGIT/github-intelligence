from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import encrypt_token
from app.models.github_connection import GithubConnection
from app.services.github_client import GitHubClient


def get_or_create_connection(session: Session, token: str) -> GithubConnection:
    """MVP is single-tenant: reuse the one existing connection if present, rather than
    creating a new github_connections row per repository registration."""
    existing = session.execute(select(GithubConnection).limit(1)).scalar_one_or_none()
    if existing is not None:
        return existing

    with GitHubClient(token=token) as client:
        user = client.get_authenticated_user()

    connection = GithubConnection(login=user["login"], encrypted_token=encrypt_token(token))
    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection
