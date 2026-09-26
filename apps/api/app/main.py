from fastapi import FastAPI, Response, status
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import engine

configure_logging()
settings = get_settings()

app = FastAPI(title=settings.app_name)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: process is up. No dependency checks."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, str]:
    """Readiness: process is up and can reach the database."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ready"}
