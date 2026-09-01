from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.deps import require_bearer_token


def _client_with_protected_route() -> TestClient:
    app = FastAPI()

    @app.get("/protected")
    def protected_route(_: None = Depends(require_bearer_token)):
        return {"ok": True}

    return TestClient(app)


def test_require_bearer_token_rejects_missing_header():
    client = _client_with_protected_route()
    response = client.get("/protected")
    assert response.status_code == 401


def test_require_bearer_token_rejects_wrong_token():
    client = _client_with_protected_route()
    response = client.get("/protected", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_require_bearer_token_accepts_correct_token():
    client = _client_with_protected_route()
    response = client.get("/protected", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
