from datetime import datetime, timedelta, timezone

from src.db.models import PlaybookRule, Reply, StyleVariant
from src.db.repo import insert_post, insert_swipe_file_post, recompute_all_post_scores


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
