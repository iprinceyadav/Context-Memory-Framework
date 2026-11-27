from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.auth import ADMIN_ROLES
from app.core.config import settings
from app.models.access import AccessGrant
from app.models.user import User

WRITE_ACTIONS = ("update", "insert", "delete")
MASK = "••••••"


def is_admin(user: User) -> bool:
    return user.role in ADMIN_ROLES


def active_grants(db: Session, organization_id: str, user_id: Optional[str] = None) -> List[AccessGrant]:
    query = db.query(AccessGrant).filter(
        AccessGrant.organization_id == organization_id,
        AccessGrant.revoked_at.is_(None),
    )
    if user_id:
        query = query.filter(AccessGrant.user_id == user_id)
    return query.order_by(AccessGrant.created_at.asc()).all()


def can_change(
    db: Session,
    user: User,
    action: str,
    table_name: str,
    connection_id: Optional[str],
) -> bool:
    if action not in WRITE_ACTIONS:
        return False
    if is_admin(user):
        return True
    match = (
        db.query(AccessGrant)
        .filter(
            AccessGrant.organization_id == user.organization_id,
            AccessGrant.user_id == user.id,
            AccessGrant.action == action,
            AccessGrant.revoked_at.is_(None),
            or_(AccessGrant.table_name == "*", AccessGrant.table_name == table_name.lower()),
            or_(AccessGrant.connection_id.is_(None), AccessGrant.connection_id == connection_id),
        )
        .first()
    )
    return match is not None


def sensitive_columns() -> List[str]:
    return [name.strip().lower() for name in settings.SENSITIVE_COLUMNS.split(",") if name.strip()]


def is_sensitive(column: str) -> bool:
    name = column.lower()
    return any(pattern in name for pattern in sensitive_columns())


def mask_rows(rows: List[Dict[str, Any]], user: Optional[User] = None) -> tuple[List[Dict[str, Any]], List[str]]:
    if not rows or (user is not None and is_admin(user)):
        return rows, []
    hidden = [column for column in rows[0].keys() if is_sensitive(str(column))]
    if not hidden:
        return rows, []
    masked = [
        {key: (MASK if key in hidden and value is not None else value) for key, value in row.items()}
        for row in rows
    ]
    return masked, hidden
