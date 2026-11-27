import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, List, Optional

from fastapi import Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.auth import ADMIN_ROLES, authorize_user
from app.database.connection import get_db
from app.models.access import AccessGrant, ChangeRequest
from app.models.conversation import Conversation
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.models.user import User
from app.services.connector import connector
from app.services.memory_store import MemoryStore

ORGANIZATION_ID = "default-org"
DEFAULT_USER_ID = "default-user"
EMPLOYEE_USER_ID = "default-employee"
USER_TYPES = {
    "admin": {"user_id": DEFAULT_USER_ID, "label": "Admin", "role": "owner", "grants": (), "summary": "Full access"},
    "finance": {
        "user_id": "default-finance",
        "label": "Finance",
        "role": "analyst",
        "grants": ("update",),
        "summary": "Read · request edits",
    },
    "sales": {
        "user_id": "default-sales",
        "label": "Sales",
        "role": "analyst",
        "grants": ("insert", "update"),
        "summary": "Read · request adds, edits",
    },
    "operations": {
        "user_id": "default-operations",
        "label": "Operations",
        "role": "analyst",
        "grants": ("insert", "update", "delete"),
        "summary": "Read · request any change",
    },
    "hr": {"user_id": "default-hr", "label": "HR", "role": "viewer", "grants": (), "summary": "Read-only"},
    "employee": {"user_id": EMPLOYEE_USER_ID, "label": "Employee", "role": "viewer", "grants": (), "summary": "Read-only"},
}
USER_TYPE_BY_ID = {meta["user_id"]: key for key, meta in USER_TYPES.items()}
USER_COOKIE = "cmf_user"
CONNECTION_COOKIE = "cmf_connection"
CONVERSATION_COOKIE = "cmf_conversation"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


@dataclass
class Workspace:
    db: Session
    user: User
    onboarded: bool
    connection: Optional[DatabaseConnection]
    conversation_id: Optional[str]
    organization_id: str = ORGANIZATION_ID
    cached: dict = field(default_factory=dict)

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def connection_id(self) -> Optional[str]:
        return self.connection.id if self.connection else None

    @property
    def is_admin(self) -> bool:
        return self.user.role in ADMIN_ROLES

    @property
    def user_type(self) -> str:
        return USER_TYPE_BY_ID.get(self.user.id, "admin" if self.is_admin else "employee")

    @property
    def user_type_label(self) -> str:
        return USER_TYPES[self.user_type]["label"]

    @property
    def access_label(self) -> str:
        return USER_TYPES[self.user_type]["summary"]

    @property
    def can_request_changes(self) -> bool:
        return self.is_admin or bool(USER_TYPES[self.user_type]["grants"])

    def pending_changes(self) -> List[ChangeRequest]:
        if "pending_changes" not in self.cached:
            self.cached["pending_changes"] = (
                self.db.query(ChangeRequest)
                .filter(
                    ChangeRequest.organization_id == self.organization_id,
                    ChangeRequest.status == "pending",
                    ChangeRequest.user_id != self.user_id,
                )
                .order_by(ChangeRequest.created_at.desc())
                .all()
                if self.is_admin
                else []
            )
        return self.cached["pending_changes"]

    def memory_stats(self) -> dict:
        if "memory_stats" not in self.cached:
            self.cached["memory_stats"] = MemoryStore(self.db).stats(
                org_id=self.organization_id, user_id=self.user_id
            )
        return self.cached["memory_stats"]

    def conversations(self, limit: int = 12) -> List[Conversation]:
        return (
            self.db.query(Conversation)
            .filter(
                Conversation.organization_id == self.organization_id,
                Conversation.user_id == self.user_id,
                Conversation.is_active == True,
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .all()
        )

    def latest_conversation_id(self) -> Optional[str]:
        if self.conversation_id:
            return self.conversation_id
        recent = self.conversations(limit=1)
        return recent[0].id if recent else None

    def schema_tables(self) -> List[str]:
        if not self.connection:
            return []
        schema_key = connector.fingerprint(
            db_type=self.connection.db_type,
            username=self.connection.username,
            host=self.connection.host,
            port=self.connection.port,
            db_name=self.connection.database_name,
        )
        row = self.db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
        if not row:
            return []
        try:
            return sorted(json.loads(row.schema_json).keys())
        except (ValueError, AttributeError):
            return []


def ensure_user_type(db: Session, user_type: str) -> User:
    if user_type not in USER_TYPES:
        raise HTTPException(status_code=404, detail="Unknown user type.")
    authorize_user(db, ORGANIZATION_ID, DEFAULT_USER_ID)
    meta = USER_TYPES[user_type]
    user = db.query(User).filter(User.id == meta["user_id"]).first()
    if user is None:
        user = User(
            id=meta["user_id"],
            email=f"{user_type}@default.local",
            full_name=meta["label"],
            hashed_password="hashed_default",
            role=meta["role"],
            organization_id=ORGANIZATION_ID,
        )
        db.add(user)
        db.flush()
    if user.role != meta["role"]:
        user.role = meta["role"]
    sync_department_grants(db, user, meta["grants"])
    db.commit()
    return user


def sync_department_grants(db: Session, user: User, actions: tuple) -> None:
    current = {
        grant.action: grant
        for grant in db.query(AccessGrant).filter(
            AccessGrant.organization_id == ORGANIZATION_ID,
            AccessGrant.user_id == user.id,
            AccessGrant.revoked_at.is_(None),
        )
    }
    for action, grant in current.items():
        if action not in actions:
            grant.revoked_at = datetime.utcnow()
    for action in actions:
        if action not in current:
            db.add(
                AccessGrant(
                    organization_id=ORGANIZATION_ID,
                    user_id=user.id,
                    connection_id=None,
                    table_name="*",
                    action=action,
                    granted_by=DEFAULT_USER_ID,
                )
            )


def load_workspace(request: Request, db: Session = Depends(get_db)) -> Workspace:
    _, default_user = authorize_user(db, ORGANIZATION_ID, DEFAULT_USER_ID)
    chosen_id = request.cookies.get(USER_COOKIE) or ""
    user = None
    if chosen_id in USER_TYPE_BY_ID:
        user = db.query(User).filter(User.id == chosen_id, User.organization_id == ORGANIZATION_ID).first()
    onboarded = user is not None
    user = user or default_user

    connection = None
    connection_id = request.cookies.get(CONNECTION_COOKIE)
    if connection_id:
        connection = (
            db.query(DatabaseConnection)
            .filter(
                DatabaseConnection.id == connection_id,
                DatabaseConnection.organization_id == ORGANIZATION_ID,
                DatabaseConnection.is_active == True,
            )
            .first()
        )

    return Workspace(
        db=db,
        user=user,
        onboarded=onboarded,
        connection=connection,
        conversation_id=request.cookies.get(CONVERSATION_COOKIE) or None,
    )


def error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail: Any = exc.detail
        if isinstance(detail, list):
            return "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
        return str(detail)
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors())
    return str(exc) or exc.__class__.__name__
