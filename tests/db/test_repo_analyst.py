from datetime import datetime, timedelta, timezone

from src.db.models import PlaybookRule, Reply, StyleVariant
from src.db.repo import insert_post, insert_swipe_file_post, recompute_all_post_scores, recompute_style_variant_medians


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
