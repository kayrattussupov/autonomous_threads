from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import StyleVariant

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_styles_requires_bearer_token():
    response = client.get("/styles")
    assert response.status_code == 401


def test_get_styles_returns_all_variants(db_session):
    db_session.add(StyleVariant(name="v1", genome="g1", status="active", created_by="human"))
    db_session.commit()

    response = client.get("/styles", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_approve_promotes_draft_to_active_when_fewer_than_two_active(db_session):
    draft = StyleVariant(name="v-new", genome="g", status="draft", created_by="analyst", rationale="better hooks")
    db_session.add(draft)
    db_session.commit()

    response = client.post(f"/styles/{draft.id}/approve", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_approve_retires_worse_median_active_variant_when_guard_satisfied(db_session):
    weak = StyleVariant(name="weak", genome="g", status="active", created_by="human", posts_n=25, median_score=10)
    strong = StyleVariant(name="strong", genome="g", status="active", created_by="human", posts_n=25, median_score=50)
    candidate = StyleVariant(name="candidate", genome="g", status="draft", created_by="analyst", rationale="radical rewrite")
    db_session.add_all([strong, weak, candidate])
    db_session.commit()

    response = client.post(f"/styles/{candidate.id}/approve", headers=AUTH)
    assert response.status_code == 200

    db_session.refresh(weak)
    db_session.refresh(strong)
    assert weak.status == "retired"
    assert strong.status == "active"


def test_approve_blocked_when_retirement_candidate_has_too_few_posts(db_session):
    weak = StyleVariant(name="weak", genome="g", status="active", created_by="human", posts_n=5, median_score=10)
    strong = StyleVariant(name="strong", genome="g", status="active", created_by="human", posts_n=30, median_score=50)
    candidate = StyleVariant(name="candidate", genome="g", status="draft", created_by="analyst")
    db_session.add_all([weak, strong, candidate])
    db_session.commit()

    response = client.post(f"/styles/{candidate.id}/approve", headers=AUTH)
    assert response.status_code == 422

    db_session.refresh(weak)
    assert weak.status == "active"


def test_approve_on_non_draft_returns_409(db_session):
    already_active = StyleVariant(name="a", genome="g", status="active", created_by="human")
    db_session.add(already_active)
    db_session.commit()

    response = client.post(f"/styles/{already_active.id}/approve", headers=AUTH)
    assert response.status_code == 409


def test_reject_sets_status_rejected(db_session):
    draft = StyleVariant(name="v-new", genome="g", status="draft", created_by="analyst")
    db_session.add(draft)
    db_session.commit()

    response = client.post(f"/styles/{draft.id}/reject", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
