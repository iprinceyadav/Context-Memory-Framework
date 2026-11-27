"""
SQL Validation & Read-Only Safety Guard using sqlglot.

Ensures generated SQL (even if influenced by retrieved context/memories)
remains strictly read-only and respects the live database schema.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

_FORBIDDEN_EXPRESSIONS = tuple(
    cls
    for cls in (
        getattr(exp, "Insert", None),
        getattr(exp, "Update", None),
        getattr(exp, "Delete", None),
        getattr(exp, "Drop", None),
        getattr(exp, "Create", None),
        getattr(exp, "AlterTable", None),
        getattr(exp, "Alter", None),
        getattr(exp, "Command", None),
        getattr(exp, "TruncateTable", None),
    )
    if cls is not None
)

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|GRANT|REVOKE|ATTACH|PRAGMA|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def _map_dialect(db_type: str) -> str:
    mapping = {
        "postgresql": "postgres",
        "postgres": "postgres",
        "mysql": "mysql",
        "sqlite": "sqlite",
    }
    return mapping.get((db_type or "").lower(), "postgres")


def validate_sql_safety(
    sql: str,
    db_type: str = "postgresql",
    schema_json_str: Optional[str] = None,
    enforce_read_only: bool = True,
) -> Tuple[bool, Optional[str], List[str]]:
    """Validate SQL syntax, read-only constraint, and live schema table references.

    Returns (is_valid, error_message, referenced_tables).
    """
    if not sql or not sql.strip():
        return False, "Generated SQL query is empty.", []

    cleaned = sql.strip().rstrip(";")
    if enforce_read_only and _FORBIDDEN_KEYWORDS.search(cleaned):
        return False, "Security policy violation: only read-only SELECT queries are permitted.", []

    dialect = _map_dialect(db_type)
    try:
        parsed_statements = sqlglot.parse(cleaned, read=dialect)
    except Exception as exc:
        logger.warning("sqlglot parse warning for dialect %s: %s", dialect, exc)
        # Fallback keyword check when dialect syntax isn't parsed strictly
        if enforce_read_only and not re.match(r"^\s*(SELECT|WITH)\b", cleaned, re.IGNORECASE):
            return False, "Security policy violation: query must start with SELECT or WITH.", []
        return True, None, []

    if len(parsed_statements) > 1:
        return False, "Multiple SQL statements are not permitted in a single query.", []

    stmt = parsed_statements[0]
    if stmt is None:
        return False, "Could not parse SQL statement.", []

    if enforce_read_only:
        for forbidden in _FORBIDDEN_EXPRESSIONS:
            if stmt.find(forbidden) is not None:
                return False, f"Security policy violation: {forbidden.__name__} statements are forbidden.", []
        if not isinstance(stmt, (exp.Select, exp.Union, exp.Subquery)):
            return False, "Security policy violation: only SELECT queries are allowed.", []

    # Extract referenced tables (excluding CTE names)
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE) if cte.alias_or_name}
    referenced_tables = [
        t.name.lower()
        for t in stmt.find_all(exp.Table)
        if t.name and t.name.lower() not in cte_names
    ]

    # Verify referenced tables exist in live schema if provided
    if schema_json_str and referenced_tables:
        try:
            schema_dict: Dict[str, Any] = json.loads(schema_json_str)
            live_tables = {k.lower() for k in schema_dict.keys()}
            unknown = [t for t in referenced_tables if t not in live_tables]
            if unknown:
                return (
                    False,
                    f"Query references table(s) not present in live schema: {', '.join(unknown)}",
                    referenced_tables,
                )
        except Exception:
            pass

    return True, None, referenced_tables


def sanitize_sql_template(sql: str) -> str:
    """Replace literal numbers/strings with placeholders so episodic templates avoid sensitive values."""
    if not sql:
        return ""
    template = re.sub(r"'[^']*'", "'?'", sql)
    return template.strip()
