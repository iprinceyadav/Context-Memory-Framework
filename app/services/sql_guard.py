from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

_WRITE_NODES = tuple(
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

_WRITE_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|GRANT|REVOKE|ATTACH|PRAGMA|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def dialect_for(db_type: str) -> str:
    mapping = {
        "postgresql": "postgres",
        "postgres": "postgres",
        "mysql": "mysql",
        "sqlite": "sqlite",
    }
    return mapping.get((db_type or "").lower(), "postgres")


def check_sql(
    sql: str,
    db_type: str = "postgresql",
    schema_json_str: Optional[str] = None,
    enforce_read_only: bool = True,
) -> Tuple[bool, Optional[str], List[str]]:
    if not sql or not sql.strip():
        return False, "Generated SQL query is empty.", []

    cleaned = sql.strip().rstrip(";")
    if enforce_read_only and _WRITE_KEYWORDS.search(cleaned):
        return False, "Security policy violation: only read-only SELECT queries are permitted.", []

    dialect = dialect_for(db_type)
    try:
        parsed_statements = sqlglot.parse(cleaned, read=dialect)
    except Exception as exc:
        logger.warning("sqlglot parse warning for dialect %s: %s", dialect, exc)
        if enforce_read_only and not re.match(r"^\s*(SELECT|WITH)\b", cleaned, re.IGNORECASE):
            return False, "Security policy violation: query must start with SELECT or WITH.", []
        return True, None, []

    if len(parsed_statements) > 1:
        return False, "Multiple SQL statements are not permitted in a single query.", []

    stmt = parsed_statements[0]
    if stmt is None:
        return False, "Could not parse SQL statement.", []

    if enforce_read_only:
        for forbidden in _WRITE_NODES:
            if stmt.find(forbidden) is not None:
                return False, f"Security policy violation: {forbidden.__name__} statements are forbidden.", []
        if not isinstance(stmt, (exp.Select, exp.Union, exp.Subquery)):
            return False, "Security policy violation: only SELECT queries are allowed.", []

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE) if cte.alias_or_name}
    referenced_tables = [
        t.name.lower()
        for t in stmt.find_all(exp.Table)
        if t.name and t.name.lower() not in cte_names
    ]

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


def mask_literals(sql: str) -> str:
    if not sql:
        return ""
    template = re.sub(r"'[^']*'", "'?'", sql)
    return template.strip()


_WRITE_KINDS = {exp.Update: "update", exp.Insert: "insert", exp.Delete: "delete"}


@dataclass
class SqlPlan:
    sql: str
    kind: str = "read"
    error: Optional[str] = None
    tables: List[str] = field(default_factory=list)
    target: Optional[str] = None
    statement: Optional[exp.Expression] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def is_write(self) -> bool:
        return self.kind in ("update", "insert", "delete")


def _live_columns(schema_json_str: Optional[str]) -> Dict[str, Set[str]]:
    if not schema_json_str:
        return {}
    try:
        schema = json.loads(schema_json_str)
    except Exception:
        return {}
    return {
        table.lower(): {str(col.get("name", "")).lower() for col in columns}
        for table, columns in schema.items()
    }


def _read_tables(statement: exp.Expression) -> List[str]:
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE) if cte.alias_or_name}
    return [
        table.name.lower()
        for table in statement.find_all(exp.Table)
        if table.name and table.name.lower() not in cte_names
    ]


def _target_table(statement: exp.Expression) -> Optional[exp.Table]:
    node = statement.this
    if isinstance(node, exp.Schema):
        node = node.this
    return node if isinstance(node, exp.Table) else None


def plan_sql(sql: str, db_type: str = "postgresql", schema_json_str: Optional[str] = None) -> SqlPlan:
    cleaned = (sql or "").strip().rstrip(";").strip()
    plan = SqlPlan(sql=cleaned)
    if not cleaned:
        plan.error = "I couldn't turn that into a database query. Try rephrasing."
        return plan

    try:
        statements = [s for s in sqlglot.parse(cleaned, read=dialect_for(db_type)) if s is not None]
    except Exception:
        plan.error = "I couldn't build a valid query for that. Try rephrasing."
        return plan

    if len(statements) != 1:
        plan.error = "Only one statement can run at a time."
        return plan

    statement = statements[0]
    plan.statement = statement
    live = _live_columns(schema_json_str)

    kind = next((name for node_type, name in _WRITE_KINDS.items() if isinstance(statement, node_type)), None)

    if kind is None:
        if not isinstance(statement, (exp.Select, exp.Union, exp.Subquery)):
            plan.error = "Schema changes and admin commands are never allowed from chat."
            return plan
        if any(statement.find(node) is not None for node in _WRITE_NODES) or _WRITE_KEYWORDS.search(cleaned):
            plan.error = "A read query can't also change data."
            return plan
        plan.tables = _read_tables(statement)
    else:
        plan.kind = kind
        nested = [node for node in statement.find_all(*_WRITE_NODES) if node is not statement]
        if nested:
            plan.error = "Only one simple change can be made at a time."
            return plan
        table = _target_table(statement)
        if table is None or not table.name:
            plan.error = "I couldn't tell which table to change."
            return plan
        plan.target = table.name.lower()
        if kind in ("update", "delete"):
            if statement.args.get("from") or statement.args.get("joins") or statement.args.get("using"):
                plan.error = "Changes that join other tables aren't supported. Name the rows directly."
                return plan
            if not statement.args.get("where"):
                plan.error = f"Refusing to {kind} every row in {plan.target}. Say which rows to change."
                return plan
        plan.tables = sorted({plan.target, *_read_tables(statement)})
        if live and plan.target in live:
            if kind == "update":
                columns = [assignment.this.name.lower() for assignment in statement.expressions]
            elif kind == "insert" and isinstance(statement.this, exp.Schema):
                columns = [column.name.lower() for column in statement.this.expressions]
            else:
                columns = []
            unknown = [column for column in columns if column not in live[plan.target]]
            if unknown:
                plan.error = f"{plan.target} has no column named {', '.join(unknown)}."
                return plan

    if live:
        unknown_tables = sorted({t for t in plan.tables if t not in live})
        if unknown_tables:
            plan.error = f"These tables don't exist in this database: {', '.join(unknown_tables)}."
    return plan
