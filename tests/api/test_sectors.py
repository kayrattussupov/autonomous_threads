from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import Post, Sector

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_sectors_requires_bearer_token():
    response = client.get("/sectors")
    assert response.status_code == 401


def test_get_sectors_returns_stats_and_planner_probabilities(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="horeca", source="seed"),
        Sector(name="архив", source="llm", active=False),
        Post(text="a", category="utp_cta", status="published", sector="производство", score=10),
        Post(text="b", category="utp_cta", status="published", sector="производство", score=20),
    ])
    db_session.commit()

    response = client.get("/sectors", headers=AUTH)
    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}

    assert rows["производство"]["published_n"] == 2
    assert rows["производство"]["mean_score"] == 15.0
    assert rows["horeca"]["mean_score"] is None
    assert rows["horeca"]["weight"] > 0
    assert rows["архив"]["active"] is False
    assert rows["архив"]["probability"] is None
    assert abs(sum(r["probability"] or 0 for r in rows.values()) - 1.0) < 1e-6
