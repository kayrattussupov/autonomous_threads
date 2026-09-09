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

    # Check for locking clauses (FOR UPDATE/FOR SHARE) in all nested SELECT statements
    for select_node in stmt.find_all(exp.Select):
        if select_node.args.get("locks"):
            raise UnsafeQueryError("locking clauses (FOR UPDATE/FOR SHARE) are not allowed")

    # Validate table names and schemas
    for t in stmt.find_all(exp.Table):
        table_name = t.name.lower()
        if table_name not in ALLOWED_TABLES:
            raise UnsafeQueryError(f"tables not allowed: {table_name}")
        # Check schema/catalog qualifier - only public schema (or no schema) is allowed
        if t.db and t.db.lower() != "public":
            raise UnsafeQueryError(f"schema-qualified table references are not allowed: {t.sql()}")
        # Check catalog-qualified table references (three-part names like catalog.schema.table)
        if t.catalog:
            raise UnsafeQueryError(f"catalog-qualified table references are not allowed: {t.sql()}")

    # Whitelist of allowed aggregate functions
    ALLOWED_FUNCTIONS = (
        exp.Avg,
        exp.Coalesce,
        exp.Count,
        exp.Max,
        exp.Min,
        exp.PercentileCont,
        exp.PercentileDisc,
        exp.Sum,
    )

    # Validate function calls - only allowed functions permitted
    for func in stmt.find_all(exp.Func):
        if not isinstance(func, ALLOWED_FUNCTIONS):
            raise UnsafeQueryError(f"function calls are not allowed: {func.sql()}")

    safe_sql = stmt.sql(dialect="postgres")
    if stmt.args.get("limit") is None:
        # Append LIMIT as trailing text to preserve ORDER BY semantics
        # (appending keeps LIMIT in the same SELECT scope as ORDER BY)
        safe_sql = f"{safe_sql} LIMIT {DEFAULT_ROW_LIMIT}"
    return safe_sql


def execute_readonly(session: Session, query: str) -> list[dict] | dict:
    try:
        safe_query = _validate(query)
    except UnsafeQueryError as exc:
        return {"error": str(exc)}

    try:
        result = session.execute(text(safe_query))
        columns = list(result.keys())
        return [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception as exc:
        return {"error": str(exc)}
