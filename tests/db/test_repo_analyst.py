from datetime import datetime, timedelta, timezone

from src.db.models import PlaybookRule, Reply, StyleVariant, SwipeFilePost
from src.db.repo import insert_post, insert_swipe_file_post, recompute_all_post_scores, recompute_style_variant_medians, recompute_playbook_evidence, get_swipe_stats, propose_playbook_diff, propose_style_variant


def test_recompute_all_post_scores_uses_replies_kind_and_view_weight(db_session):
    post = insert_post(db_session, text="p1", category="educational", status="published", views=1000, replies_count=3)
    db_session.commit()
    db_session.add(Reply(threads_reply_id="r1", post_id=post.id, kind="lead", status="new"))
    db_session.add(Reply(threads_reply_id="r2", post_id=post.id, kind="question", status="new"))
    db_session.add(Reply(threads_reply_id="r3", post_id=post.id, kind="objection", status="new"))
    db_session.add(Reply(threads_reply_id="r4", post_id=post.id, kind="spam", status="ignored"))
    db_session.commit()

    updated = recompute_all_post_scores(db_session)
    db_session.commit()
    db_session.refresh(post)

    # 1 lead*100 + 2 conversations(question+objection)*10 + 3 replies*1 + 0.01*1000 views = 100+20+3+10 = 133
    assert updated == 1
    assert float(post.score) == 133.0


def test_recompute_all_post_scores_ignores_non_published_posts(db_session):
    insert_post(db_session, text="draft one", category="educational", status="draft", views=1000)
    db_session.commit()

    updated = recompute_all_post_scores(db_session)

    assert updated == 0


def test_recompute_style_variant_medians_computes_median_of_published_scored_posts(db_session):
    variant = StyleVariant(name="v1", genome="g", status="active", created_by="human", posts_n=3)
    db_session.add(variant)
    db_session.commit()
    for score in (10, 20, 30):
        insert_post(db_session, text=f"p{score}", category="educational", status="published", style_variant_id=variant.id, score=score)
    insert_post(db_session, text="draft not counted", category="educational", status="draft", style_variant_id=variant.id, score=999)
    db_session.commit()

    recompute_style_variant_medians(db_session)
    db_session.commit()
    db_session.refresh(variant)

    assert float(variant.median_score) == 20.0


def test_recompute_style_variant_medians_sets_median_to_none_for_zero_posts(db_session):
    variant = StyleVariant(name="v1", genome="g", status="active", created_by="human", posts_n=0, median_score=99.0)
    db_session.add(variant)
    db_session.commit()
    # Create a draft post that won't be counted
    insert_post(db_session, text="draft not counted", category="educational", status="draft", style_variant_id=variant.id, score=999)
    db_session.commit()

    recompute_style_variant_medians(db_session)
    db_session.commit()
    db_session.refresh(variant)

    assert variant.median_score is None


def test_recompute_style_variant_medians_scopes_to_each_variant(db_session):
    variant_a = StyleVariant(name="variant_a", genome="g_a", status="active", created_by="human", posts_n=3)
    variant_b = StyleVariant(name="variant_b", genome="g_b", status="active", created_by="human", posts_n=3)
    db_session.add(variant_a)
    db_session.add(variant_b)
    db_session.commit()

    # Variant A: scores 10, 20, 30 (median 20)
    for score in (10, 20, 30):
        insert_post(db_session, text=f"variant_a_p{score}", category="educational", status="published", style_variant_id=variant_a.id, score=score)

    # Variant B: scores 100, 200, 300 (median 200)
    for score in (100, 200, 300):
        insert_post(db_session, text=f"variant_b_p{score}", category="educational", status="published", style_variant_id=variant_b.id, score=score)

    db_session.commit()

    recompute_style_variant_medians(db_session)
    db_session.commit()
    db_session.refresh(variant_a)
    db_session.refresh(variant_b)

    assert float(variant_a.median_score) == 20.0
    assert float(variant_b.median_score) == 200.0


def _seed_published_post(db_session, score, posted_at):
    return insert_post(
        db_session, text=f"p-{score}-{posted_at.isoformat()}", category="educational",
        status="published", score=score, posted_at=posted_at,
    )


def test_recompute_playbook_evidence_promotes_when_threshold_met(db_session):
    introduced = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = PlaybookRule(rule_text="post more news", status="testing", version=1, introduced_at=introduced)
    db_session.add(rule)
    _seed_published_post(db_session, score=10, posted_at=introduced - timedelta(days=1))
    for i in range(20):
        _seed_published_post(db_session, score=20, posted_at=introduced + timedelta(days=i + 1))
    db_session.commit()

    promoted = recompute_playbook_evidence(db_session)
    db_session.commit()
    db_session.refresh(rule)

    assert rule in promoted
    assert rule.status == "confirmed"
    assert rule.evidence_n == 20
    assert float(rule.median_before) == 10.0
    assert float(rule.median_after) == 20.0  # +100% >= 30% required


def test_recompute_playbook_evidence_stays_testing_below_evidence_threshold(db_session):
    introduced = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = PlaybookRule(rule_text="r", status="testing", version=1, introduced_at=introduced)
    db_session.add(rule)
    _seed_published_post(db_session, score=10, posted_at=introduced - timedelta(days=1))
    for i in range(5):  # < 20
        _seed_published_post(db_session, score=50, posted_at=introduced + timedelta(days=i + 1))
    db_session.commit()

    promoted = recompute_playbook_evidence(db_session)
    db_session.commit()
    db_session.refresh(rule)

    assert promoted == []
    assert rule.status == "testing"
    assert rule.evidence_n == 5


def test_recompute_playbook_evidence_stays_testing_when_improvement_below_30_percent(db_session):
    introduced = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = PlaybookRule(rule_text="r", status="testing", version=1, introduced_at=introduced)
    db_session.add(rule)
    _seed_published_post(db_session, score=100, posted_at=introduced - timedelta(days=1))
    for i in range(20):
        _seed_published_post(db_session, score=110, posted_at=introduced + timedelta(days=i + 1))  # +10%, below 30%
    db_session.commit()

    promoted = recompute_playbook_evidence(db_session)
    db_session.commit()
    db_session.refresh(rule)

    assert promoted == []
    assert rule.status == "testing"


def test_recompute_playbook_evidence_freezes_median_before_after_first_computation(db_session):
    introduced = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = PlaybookRule(rule_text="r", status="testing", version=1, introduced_at=introduced, median_before=42.0)
    db_session.add(rule)
    _seed_published_post(db_session, score=999, posted_at=introduced - timedelta(days=1))  # would change it if recomputed
    db_session.commit()

    recompute_playbook_evidence(db_session)
    db_session.commit()
    db_session.refresh(rule)

    assert float(rule.median_before) == 42.0  # untouched — was already set


def test_propose_playbook_diff_adds_proposed_rules_and_marks_removals(db_session):
    old_rule = PlaybookRule(rule_text="old", status="testing", version=1)
    db_session.add(old_rule)
    db_session.commit()

    result = propose_playbook_diff(
        db_session,
        add=[{"rule_text": "post twice daily", "hypothesis": "more reach", "target_metric": "views"}],
        remove=[old_rule.id],
        rationale="test rationale",
    )
    db_session.commit()

    new_rule = db_session.query(PlaybookRule).filter_by(rule_text="post twice daily").one()
    assert new_rule.status == "proposed"
    assert new_rule.version == 2  # max(1) + 1

    db_session.refresh(old_rule)
    assert old_rule.status == "proposed_removal"
    assert result == {"added_ids": [new_rule.id], "removed_ids": [old_rule.id], "rationale": "test rationale"}


def test_propose_playbook_diff_ignores_remove_id_not_active(db_session):
    already_rejected = PlaybookRule(rule_text="dead", status="rejected", version=1)
    db_session.add(already_rejected)
    db_session.commit()

    result = propose_playbook_diff(db_session, add=[], remove=[already_rejected.id], rationale="r")

    assert result["removed_ids"] == []
    db_session.refresh(already_rejected)
    assert already_rejected.status == "rejected"  # untouched


def test_propose_style_variant_creates_draft_authored_by_analyst(db_session):
    parent = StyleVariant(name="v1", genome="g", status="active", created_by="human")
    db_session.add(parent)
    db_session.commit()

    variant = propose_style_variant(db_session, name="v2", genome="NEW GENOME", rationale="radical shift", parent_id=parent.id)
    db_session.commit()

    assert variant.status == "draft"
    assert variant.created_by == "analyst"
    assert variant.parent_id == parent.id
    assert variant.rationale == "radical shift"


def test_get_swipe_stats_aggregates_by_topic_within_window(db_session):
    now = datetime.now(timezone.utc)
    insert_swipe_file_post(db_session, threads_post_id="a", text="t1", topic="automation", views=100, likes=10)
    db_session.commit()
    a = db_session.query(SwipeFilePost).filter_by(threads_post_id="a").one()
    a.collected_at = now - timedelta(days=5)
    insert_swipe_file_post(db_session, threads_post_id="b", text="t2", topic="automation", views=200, likes=20)
    db_session.commit()
    b = db_session.query(SwipeFilePost).filter_by(threads_post_id="b").one()
    b.collected_at = now - timedelta(days=40)  # outside 30-day window
    db_session.commit()

    stats = get_swipe_stats(db_session, days=30)

    assert stats == [{"topic": "automation", "count": 1, "median_views": 100.0, "median_likes": 10.0}]
