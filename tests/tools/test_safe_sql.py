import pytest

from src.db.models import Post
from src.tools.safe_sql import UnsafeQueryError, _validate, execute_readonly


def test_execute_readonly_returns_rows_for_whitelisted_select(db_session):
    db_session.add(Post(text="hello", category="educational", status="published", score=5))
    db_session.commit()

    rows = execute_readonly(db_session, "SELECT text, score FROM posts")

    assert rows == [{"text": "hello", "score": 5}]


@pytest.mark.parametrize("query", [
    "INSERT INTO posts (text, category, status) VALUES ('x', 'educational', 'draft')",
    "UPDATE posts SET score = 0",
    "DELETE FROM posts",
    "DROP TABLE posts",
])
def test_execute_readonly_rejects_non_select_statements(db_session, query):
    result = execute_readonly(db_session, query)
    assert "error" in result


def test_execute_readonly_rejects_table_outside_whitelist(db_session):
    result = execute_readonly(db_session, "SELECT * FROM daily_spend")
    assert "error" in result


def test_execute_readonly_rejects_multiple_statements(db_session):
    result = execute_readonly(db_session, "SELECT 1; DROP TABLE posts;")
    assert "error" in result


def test_validate_injects_default_limit_when_missing():
    safe_sql = _validate("SELECT * FROM posts")
    assert "LIMIT 200" in safe_sql


def test_validate_preserves_explicit_limit():
    safe_sql = _validate("SELECT * FROM posts LIMIT 5")
    assert safe_sql.count("LIMIT") == 1
    assert "LIMIT 5" in safe_sql


def test_validate_rejects_unparseable_query():
    with pytest.raises(UnsafeQueryError):
        _validate("SELEKT * FORM posts")
