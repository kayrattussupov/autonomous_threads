from src.db.models import Post, StyleVariant


def test_insert_and_read_post(db_session):
    variant = StyleVariant(name="v1", genome="voice: dry engineer", status="active", created_by="human")
    db_session.add(variant)
    db_session.flush()

    post = Post(
        text="Пример поста",
        category="educational",
        status="draft",
        style_variant_id=variant.id,
    )
    db_session.add(post)
    db_session.commit()

    fetched = db_session.query(Post).filter_by(text="Пример поста").one()
    assert fetched.category == "educational"
    assert fetched.style_variant_id == variant.id


def test_threads_media_id_unique(db_session):
    db_session.add(Post(text="a", category="news", status="published", threads_media_id="abc123"))
    db_session.commit()

    db_session.add(Post(text="b", category="news", status="published", threads_media_id="abc123"))
    with __import__("pytest").raises(Exception):
        db_session.commit()
