from datetime import datetime, timedelta, timezone

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


def test_approve_evicts_weakest_active_rule_at_ceiling(db_session):
    for i in range(12):
        db_session.add(PlaybookRule(
            rule_text=f"active-{i}", status="testing", version=1,
            median_after=float(i), introduced_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
        ))
    new_rule = PlaybookRule(rule_text="new proposal", status="proposed", version=2)
    db_session.add(new_rule)
    db_session.commit()

    response = client.post(f"/playbook/{new_rule.id}/approve", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "testing"

    weakest = db_session.query(PlaybookRule).filter_by(rule_text="active-0").one()
    assert weakest.status == "rejected"  # median_after=0.0 was the lowest

    still_active = db_session.query(PlaybookRule).filter_by(rule_text="active-11").one()
    assert still_active.status == "testing"  # untouched


def test_approve_below_ceiling_does_not_evict(db_session):
    for i in range(5):
        db_session.add(PlaybookRule(rule_text=f"active-{i}", status="testing", version=1, median_after=float(i)))
    new_rule = PlaybookRule(rule_text="new proposal", status="proposed", version=2)
    db_session.add(new_rule)
    db_session.commit()

    client.post(f"/playbook/{new_rule.id}/approve", headers=AUTH)

    assert db_session.query(PlaybookRule).filter_by(status="rejected").count() == 0

def test_approve_proposed_removal_sets_rejected(db_session):
    rule = PlaybookRule(rule_text="to remove", status="proposed_removal", version=1)
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/approve", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_reject_proposed_removal_reverts_to_testing_when_threshold_not_met(db_session):
    rule = PlaybookRule(rule_text="keep me", status="proposed_removal", version=1, evidence_n=5)
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/reject", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "testing"


def test_reject_proposed_removal_reverts_to_confirmed_when_threshold_was_met(db_session):
    rule = PlaybookRule(
        rule_text="keep me confirmed", status="proposed_removal", version=1,
        evidence_n=25, median_before=10.0, median_after=15.0,  # +50% >= 30%
    )
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/reject", headers=AUTH)

    assert response.json()["status"] == "confirmed"
