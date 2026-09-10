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
            rule_text=f"active-{i}", status="testing", version=1, evidence_n=25,
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


def test_approve_at_ceiling_blocked_when_weakest_rule_has_no_evidence_yet(db_session):
    # 11 well-evidenced active rules plus one just approved minutes ago
    # (evidence_n unset, ranks at -inf) — that freshest rule must not be
    # evicted the instant a 13th proposal shows up; it hasn't had a fair
    # trial period yet, mirroring approve_style_variant's posts_n < 20 guard.
    for i in range(11):
        db_session.add(PlaybookRule(
            rule_text=f"active-{i}", status="testing", version=1,
            evidence_n=25, median_after=float(i + 1),
            introduced_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
        ))
    db_session.add(PlaybookRule(
        rule_text="brand-new", status="testing", version=1,
        introduced_at=datetime.now(timezone.utc),
    ))
    new_rule = PlaybookRule(rule_text="new proposal", status="proposed", version=2)
    db_session.add(new_rule)
    db_session.commit()

    response = client.post(f"/playbook/{new_rule.id}/approve", headers=AUTH)

    assert response.status_code == 422
    still_pending = db_session.query(PlaybookRule).filter_by(rule_text="new proposal").one()
    assert still_pending.status == "proposed"
    brand_new = db_session.query(PlaybookRule).filter_by(rule_text="brand-new").one()
    assert brand_new.status == "testing"  # not evicted


def test_reverting_a_proposed_removal_does_not_breach_the_ceiling(db_session):
    # Reproduces the exact sequence the ceiling-breach finding described:
    # 12 active -> propose removal of A (drops to 11, under ceiling) ->
    # approve an unrelated proposal B while under ceiling (no eviction,
    # back to 12) -> reject A's removal (revert to active) -> active count
    # must stay at 12, not rise to 13.
    for i in range(11):
        db_session.add(PlaybookRule(
            rule_text=f"active-{i}", status="testing", version=1,
            evidence_n=25, median_after=float(i + 1),
            introduced_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
        ))
    rule_a = PlaybookRule(
        rule_text="rule-a", status="proposed_removal", version=1,
        evidence_n=25, median_before=10.0, median_after=100.0,  # clearly not the weakest; +30% threshold met
        introduced_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    rule_b = PlaybookRule(rule_text="rule-b", status="proposed", version=2)
    db_session.add_all([rule_a, rule_b])
    db_session.commit()

    approve_resp = client.post(f"/playbook/{rule_b.id}/approve", headers=AUTH)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "testing"

    # Time passes; a nightly recompute_playbook_evidence run gives rule-b its
    # own evidence, same as any other testing rule — otherwise it would be
    # the only zero-evidence rule in the active set and the eviction the
    # revert below needs to perform would be correctly blocked by the
    # zero-evidence guard (a different, also-real protection) rather than
    # exercising the ceiling-breach path this test targets.
    db_session.query(PlaybookRule).filter_by(rule_text="rule-b").update(
        {"evidence_n": 25, "median_after": 50.0}
    )
    db_session.commit()

    reject_resp = client.post(f"/playbook/{rule_a.id}/reject", headers=AUTH)
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "confirmed"  # threshold met

    active_count = db_session.query(PlaybookRule).filter(
        PlaybookRule.status.in_(["testing", "confirmed"])
    ).count()
    assert active_count == 12
    weakest = db_session.query(PlaybookRule).filter_by(rule_text="active-0").one()
    assert weakest.status == "rejected"  # evicted to make room for rule-a's revert
