import json
import re
from pathlib import Path
from typing import Any, Iterable, List

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

ACTION_LABELS = {"update": "Update", "insert": "Add", "delete": "Delete"}
CHANGE_STATUS_LABELS = {
    "pending": "Pending",
    "executed": "Applied",
    "rejected": "Discarded",
    "failed": "Failed",
}
DB_LABELS = {"postgresql": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}
DB_BADGES = {"postgresql": "PG", "mysql": "MY", "sqlite": "SQ"}

SQL_KEYWORDS = [
    "GROUP BY", "ORDER BY", "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "ON",
    "LIMIT", "OFFSET", "AS", "AND", "OR", "NOT", "IN", "IS", "NULL", "DESC", "ASC", "HAVING",
    "WITH", "DISTINCT", "UPDATE", "SET", "INSERT", "INTO", "VALUES", "DELETE", "UNION",
]
SQL_FUNCTIONS = ["SUM", "COUNT", "AVG", "MIN", "MAX", "COALESCE", "DATE_TRUNC", "STRFTIME"]
SQL_TOKEN_PATTERN = re.compile(
    r"(?P<string>'[^']*')"
    r"|(?P<keyword>\b(?:" + "|".join(k.replace(" ", r"\s+") for k in SQL_KEYWORDS) + r")\b)"
    r"|(?P<function>\b(?:" + "|".join(SQL_FUNCTIONS) + r")\b)"
    r"|(?P<number>\b\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)
TABLE_REFERENCE_PATTERN = re.compile(r"(?:FROM|JOIN|UPDATE|INTO)\s+([a-zA-Z0-9_]+)", re.IGNORECASE)


def highlight_sql(sql: Any) -> Markup:
    if not sql:
        return Markup("")
    text = str(sql)
    pieces: List[str] = []
    position = 0
    for match in SQL_TOKEN_PATTERN.finditer(text):
        pieces.append(str(escape(text[position:match.start()])))
        pieces.append(f'<span class="sql-{match.lastgroup}">{escape(match.group(0))}</span>')
        position = match.end()
    pieces.append(str(escape(text[position:])))
    return Markup("".join(pieces))


def tables_in_sql(sql: Any) -> List[str]:
    if not sql:
        return []
    seen: List[str] = []
    for name in TABLE_REFERENCE_PATTERN.findall(str(sql)):
        lowered = name.lower()
        if lowered not in seen:
            seen.append(lowered)
    return seen


def format_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def result_columns(rows: Iterable[dict]) -> List[str]:
    rows = list(rows or [])
    return list(rows[0].keys()) if rows else []


def percent(value: Any) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "—"


def plural(count: int, singular: str, plural_form: str) -> str:
    return singular if count == 1 else plural_form


def field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters.update(
    highlight_sql=highlight_sql,
    tables_in_sql=tables_in_sql,
    cell=format_cell,
    columns=result_columns,
    percent=percent,
)
templates.env.globals.update(
    ACTION_LABELS=ACTION_LABELS,
    CHANGE_STATUS_LABELS=CHANGE_STATUS_LABELS,
    DB_LABELS=DB_LABELS,
    DB_BADGES=DB_BADGES,
    plural=plural,
    field=field,
)
