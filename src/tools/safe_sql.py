import sqlglot
from sqlglot import exp
from sqlalchemy import text
from sqlalchemy.orm import Session

ALLOWED_TABLES = {"posts", "swipe_file", "style_variants", "playbook_rules", "replies", "leads"}
DEFAULT_ROW_LIMIT = 200


class UnsafeQueryError(Exception):
    """Raised when a query fails the read-only/whitelist validation."""


def _validate(query: str) -> str:
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError(f"could not parse query: {exc}") from exc

    if len(statements) != 1:
        raise UnsafeQueryError("exactly one SQL statement is allowed")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise UnsafeQueryError("only SELECT statements are allowed")

    tables = {t.name.lower() for t in stmt.find_all(exp.Table)}
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        raise UnsafeQueryError(f"tables not allowed: {sorted(disallowed)}")

    safe_sql = stmt.sql(dialect="postgres")
    if stmt.args.get("limit") is None:
        # Wrap rather than mutate the parsed tree with a builder method — avoids
        # depending on a specific sqlglot version's Select.limit() signature.
        safe_sql = f"SELECT * FROM ({safe_sql}) AS _bounded LIMIT {DEFAULT_ROW_LIMIT}"
    return safe_sql


def execute_readonly(session: Session, query: str) -> list[dict] | dict:
    try:
        safe_query = _validate(query)
    except UnsafeQueryError as exc:
        return {"error": str(exc)}

    result = session.execute(text(safe_query))
    columns = list(result.keys())
    return [dict(zip(columns, row)) for row in result.fetchall()]
