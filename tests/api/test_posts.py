from src.api.main import app
from src.db.models import Post
from fastapi.testclient import TestClient

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_posts_requires_bearer_token():
    response = client.get("/posts")
    assert response.status_code == 401


def test_get_posts_filters_by_status_and_reports_total(db_session):
    db_session.add(Post(text="published one", category="news", status="published", views=100, score=5))
    db_session.add(Post(text="scheduled one", category="educational", status="scheduled", views=50, score=3))
    db_session.commit()

    response = client.get("/posts", params={"status": "published"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["text"] == "published one"
    assert body["page"] == 1
    assert body["page_size"] == 25


def test_get_posts_median_score_reflects_filtered_set(db_session):
    db_session.add(Post(text="a", category="news", status="published", score=10))
    db_session.add(Post(text="b", category="news", status="published", score=20))
    db_session.add(Post(text="c", category="educational", status="published", score=1000))
    db_session.commit()

    response = client.get("/posts", params={"category": "news"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["median_score"] == 15.0
