import datetime
from decimal import Decimal

import sqlglot
from sqlglot import exp
from sqlalchemy import text
from sqlalchemy.orm import Session

ALLOWED_TABLES = {"posts", "swipe_file", "style_variants", "playbook_rules", "replies", "leads"}
DEFAULT_ROW_LIMIT = 200


class UnsafeQueryError(Exception):
    """Raised when a query fails the read-only/whitelist validation."""


def _json_safe(value):
    """Convert raw DB driver values into JSON-serializable equivalents.

    Numeric columns come back as decimal.Decimal and timestamp/date columns
    come back as datetime.datetime/date, neither of which the stdlib json
    module can serialize. Mirrors the Decimal -> float convention already
    used at the ORM boundary in src/db/repo.py, generalized here since this
    function doesn't know column types ahead of time.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


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

    # Whitelist of allowed aggregate + basic date/scalar functions. Exact class
    # names verified against this repo's installed sqlglot (26.33.0) by
    # directly parsing sample queries — note date_trunc(...) parses as
    # TimestampTrunc, not DateTrunc.
    ALLOWED_FUNCTIONS = (
        exp.Avg,
        exp.Coalesce,
        exp.Count,
        exp.Max,
        exp.Min,
        exp.PercentileCont,
        exp.PercentileDisc,
        exp.Sum,
        exp.TimestampTrunc,
        exp.Extract,
        exp.CurrentTimestamp,
        exp.Cast,
        exp.Round,
        exp.Abs,
        exp.Length,
        exp.Lower,
    )

    # Validate function calls - only allowed functions permitted
    for func in stmt.find_all(exp.Func):
        if not isinstance(func, ALLOWED_FUNCTIONS):
            raise UnsafeQueryError(f"function calls are not allowed: {func.sql()}")

    # Clamp the row limit: any explicit LIMIT above DEFAULT_ROW_LIMIT (or no
    # LIMIT at all) is capped to DEFAULT_ROW_LIMIT. Limits already at or below
    # the cap are left untouched.
    existing_limit = stmt.args.get("limit")
    limit_value = None
    if existing_limit is not None:
        try:
            limit_value = int(existing_limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            limit_value = None

    if existing_limit is None:
        safe_sql = stmt.sql(dialect="postgres")
        # Append LIMIT as trailing text to preserve ORDER BY semantics
        # (appending keeps LIMIT in the same SELECT scope as ORDER BY)
        safe_sql = f"{safe_sql} LIMIT {DEFAULT_ROW_LIMIT}"
    elif limit_value is None or limit_value > DEFAULT_ROW_LIMIT:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(DEFAULT_ROW_LIMIT)))
        safe_sql = stmt.sql(dialect="postgres")
    else:
        safe_sql = stmt.sql(dialect="postgres")
    return safe_sql


def execute_readonly(session: Session, query: str) -> list[dict] | dict:
    try:
        safe_query = _validate(query)
    except UnsafeQueryError as exc:
        return {"error": str(exc)}

    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        result = session.execute(text(safe_query))
        columns = list(result.keys())
        return [
            {col: _json_safe(val) for col, val in zip(columns, row)}
            for row in result.fetchall()
        ]
    except Exception as exc:
        # Execution-time failure (e.g. bad column name) leaves the session's
        # transaction needing rollback — without this, the caller's next use
        # of this same session (e.g. session_scope()'s commit()) raises a
        # generic PendingRollbackError instead of surfacing this message.
        session.rollback()
        return {"error": str(exc)}
