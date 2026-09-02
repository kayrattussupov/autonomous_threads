from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import PlaybookRule

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_playbook_requires_bearer_token():
    response = client.get("/playbook")
    assert response.status_code == 401


def test_get_playbook_returns_all_rules(db_session):
    db_session.add(PlaybookRule(rule_text="post at 9am", status="confirmed", version=1))
    db_session.commit()

    response = client.get("/playbook", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_approve_moves_proposed_to_testing(db_session):
    rule = PlaybookRule(rule_text="new rule", status="proposed", version=1, hypothesis="h")
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/approve", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "testing"


def test_approve_on_non_proposed_returns_409(db_session):
    rule = PlaybookRule(rule_text="already testing", status="testing", version=1)
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/approve", headers=AUTH)
    assert response.status_code == 409


def test_reject_sets_status_rejected(db_session):
    rule = PlaybookRule(rule_text="bad idea", status="proposed", version=1)
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/reject", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
