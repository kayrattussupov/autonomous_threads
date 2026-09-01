from src.db.repo import insert_swipe_file_post, swipe_file_post_exists


def test_swipe_file_post_exists_false_then_true(db_session):
    assert swipe_file_post_exists(db_session, "abc123") is False

    insert_swipe_file_post(db_session, threads_post_id="abc123", text="Пример поста", topic="автоматизация")
    db_session.commit()

    assert swipe_file_post_exists(db_session, "abc123") is True
    assert swipe_file_post_exists(db_session, "does-not-exist") is False


def test_insert_swipe_file_post_returns_row_with_id(db_session):
    post = insert_swipe_file_post(db_session, threads_post_id="xyz789", text="Другой пост", topic="маркетинг")
    db_session.commit()

    assert post.id is not None
    assert post.threads_post_id == "xyz789"
    assert post.topic == "маркетинг"
