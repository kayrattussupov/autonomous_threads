from datetime import date

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import DailySpend

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_spend_requires_bearer_token():
    response = client.get("/spend")
    assert response.status_code == 401


def test_get_spend_returns_month_to_date_and_cap(db_session):
    db_session.add(DailySpend(date=date.today(), model="glm-4.7", tokens_in=1000, tokens_out=500, cost_usd=1.23))
    db_session.commit()

    response = client.get("/spend", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["month_to_date_usd"] == 1.23
    assert body["cap_usd"] == 10.0
