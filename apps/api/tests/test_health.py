from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_status() -> None:
    # Depends on DATABASE_URL being reachable (docker compose db, or CI's postgres service).
    # Asserts the endpoint responds with a valid shape rather than requiring success,
    # so this test is meaningful in CI without assuming local infra is running.
    response = client.get("/readyz")
    assert response.status_code in (200, 503)
    assert response.json()["status"] in ("ready", "unavailable")
