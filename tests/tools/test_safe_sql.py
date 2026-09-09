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


# Fix 1: Schema/catalog-qualified table names bypass the whitelist
def test_validate_rejects_schema_qualified_tables(db_session):
    """Schema-qualified table references should be rejected."""
    result = execute_readonly(db_session, "SELECT * FROM pg_catalog.leads")
    assert "error" in result
    assert "schema-qualified" in result["error"].lower()


# Fix 2: Unrestricted function calls
def test_validate_rejects_disallowed_functions(db_session):
    """Disallowed function calls should be rejected."""
    result = execute_readonly(db_session, "SELECT setval('posts_id_seq', 1)")
    assert "error" in result
    assert "function" in result["error"].lower()


# Fix 3: Execute_readonly doesn't catch execution-time database errors
def test_execute_readonly_catches_execution_time_errors(db_session):
    """Execution-time errors should return {"error": ...} not raise."""
    # This query passes validation but fails at execution (column doesn't exist)
    result = execute_readonly(db_session, "SELECT nonexistent_column FROM posts")
    assert isinstance(result, dict)
    assert "error" in result
    assert "nonexistent_column" in result["error"] or "does not exist" in result["error"].lower()


# Fix 4: LIMIT-wrapping breaks ORDER BY semantics
def test_validate_preserves_order_by_semantics():
    """LIMIT should be appended, not wrapped in subquery, to preserve ORDER BY."""
    safe_sql = _validate("SELECT * FROM posts ORDER BY score DESC")
    # Should contain ORDER BY and LIMIT in the same SELECT scope (no subquery wrapping)
    assert "ORDER BY" in safe_sql
    assert "LIMIT 200" in safe_sql
    assert "AS _bounded" not in safe_sql  # No subquery wrapping
    # ORDER BY should come before LIMIT
    order_by_pos = safe_sql.find("ORDER BY")
    limit_pos = safe_sql.find("LIMIT")
    assert order_by_pos < limit_pos


# Fix 5 (Minor): SELECT ... FOR UPDATE should be rejected
def test_validate_rejects_locking_clauses(db_session):
    """Locking clauses (FOR UPDATE/FOR SHARE) should be rejected."""
    result = execute_readonly(db_session, "SELECT * FROM posts FOR UPDATE")
    assert "error" in result
    assert "locking" in result["error"].lower()


# Additional test: Allowed aggregate functions should pass validation
def test_validate_allows_aggregate_functions(db_session):
    """Allowed aggregate functions should pass validation."""
    db_session.add(Post(text="test1", category="educational", status="published", score=10))
    db_session.add(Post(text="test2", category="educational", status="published", score=20))
    db_session.commit()

    # These should all pass validation and execute
    rows = execute_readonly(db_session, "SELECT COUNT(*) as count FROM posts")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert "count" in rows[0]

    rows = execute_readonly(db_session, "SELECT MAX(score) as max_score FROM posts")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert "max_score" in rows[0]

    rows = execute_readonly(db_session, "SELECT SUM(score) as total FROM posts")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert "total" in rows[0]
