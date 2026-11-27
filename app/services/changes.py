from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.access import ChangeRequest
from app.models.database_connection import DatabaseConnection
from app.models.user import User
from app.services.access import can_change, is_admin, mask_rows
from app.services.connector import connector
from app.services.sql_guard import SqlPlan, dialect_for, plan_sql

logger = logging.getLogger(__name__)

PREVIEW_ROWS = 5
VERBS = {"update": "update", "insert": "add", "delete": "delete"}


class ChangeRefused(Exception):
    def __init__(self, message: str, outcome: str = "not_allowed"):
        super().__init__(message)
        self.message = message
        self.outcome = outcome


class ReviewError(Exception):
    pass


def _preview_queries(plan: SqlPlan, db_type: str) -> Tuple[Optional[str], Optional[str], int]:
    dialect = dialect_for(db_type)
    statement = plan.statement
    if plan.kind in ("update", "delete"):
        condition = statement.args["where"].this
        count_sql = sqlglot.select("COUNT(*) AS affected").from_(statement.this.copy()).where(condition.copy())
        sample_sql = sqlglot.select("*").from_(statement.this.copy()).where(condition.copy()).limit(PREVIEW_ROWS)
        return count_sql.sql(dialect), sample_sql.sql(dialect), 0
    source = statement.expression
    if isinstance(source, exp.Values):
        return None, None, len(source.expressions)
    if isinstance(source, exp.Select):
        count_sql = sqlglot.select("COUNT(*) AS affected").from_(source.copy().subquery("incoming"))
        return count_sql.sql(dialect), source.copy().limit(PREVIEW_ROWS).sql(dialect), 0
    raise ChangeRefused("I can only add rows from listed values or a simple query.")


def _describe(plan: SqlPlan, db_type: str) -> Dict[str, Any]:
    dialect = dialect_for(db_type)
    statement = plan.statement
    if plan.kind == "update":
        return {
            "changes": [
                {"column": assignment.this.name, "value": assignment.expression.sql(dialect)}
                for assignment in statement.expressions
            ]
        }
    if plan.kind == "insert" and isinstance(statement.expression, exp.Values):
        columns = (
            [column.name for column in statement.this.expressions]
            if isinstance(statement.this, exp.Schema)
            else []
        )
        rows = []
        for values in statement.expression.expressions[:PREVIEW_ROWS]:
            cells = [value.sql(dialect) for value in values.expressions]
            keys = columns or [f"column_{index + 1}" for index in range(len(cells))]
            rows.append(dict(zip(keys, cells)))
        return {"new_rows": rows}
    return {}


def summarize_request(request: ChangeRequest) -> str:
    verb = VERBS.get(request.action, request.action)
    noun = "row" if request.affected_rows == 1 else "rows"
    return f"{verb.capitalize()} {request.affected_rows} {noun} in {request.table_name}"


class ChangeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def propose(
        self,
        user: User,
        record: Optional[DatabaseConnection],
        plan: SqlPlan,
        question: str,
        conversation_id: Optional[str],
    ) -> ChangeRequest:
        verb = VERBS[plan.kind]
        if not settings.ALLOW_DATA_CHANGES:
            raise ChangeRefused("Changing data from chat is turned off for this workspace.")
        if record is None:
            raise ChangeRefused("Changes need a saved database connection. Reconnect the database and try again.")
        if not can_change(self.db, user, plan.kind, plan.target, record.id):
            raise ChangeRefused(
                f"You don't have permission to {verb} rows in {plan.target}. Ask an admin to give you access."
            )

        engine = connector.engine_for(record)
        count_sql, sample_sql, listed = _preview_queries(plan, record.db_type)
        affected = connector.count(engine, count_sql) if count_sql else listed

        if affected == 0:
            raise ChangeRefused("No rows match that, so nothing would change.", outcome="no_match")
        if affected > settings.WRITE_MAX_ROWS:
            raise ChangeRefused(
                f"That would {verb} {affected} rows; the limit for one request is {settings.WRITE_MAX_ROWS}. "
                "Narrow it down and ask again."
            )

        preview = _describe(plan, record.db_type)
        if sample_sql:
            rows, _ = connector.read(engine, sample_sql, PREVIEW_ROWS)
            preview["rows"], preview["hidden_columns"] = mask_rows(rows, user)

        request = ChangeRequest(
            organization_id=user.organization_id,
            user_id=user.id,
            connection_id=record.id,
            conversation_id=conversation_id,
            question=question,
            sql=plan.sql,
            action=plan.kind,
            table_name=plan.target,
            affected_rows=affected,
            preview=json.dumps(preview, default=str),
            status="pending",
        )
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        return request

    def visible_to(self, user: User, status: Optional[str] = None) -> List[ChangeRequest]:
        query = self.db.query(ChangeRequest).filter(ChangeRequest.organization_id == user.organization_id)
        if not is_admin(user):
            query = query.filter(ChangeRequest.user_id == user.id)
        if status:
            query = query.filter(ChangeRequest.status == status)
        return query.order_by(ChangeRequest.created_at.desc()).limit(200).all()

    def get(self, user: User, request_id: str) -> ChangeRequest:
        request = self.db.query(ChangeRequest).filter(ChangeRequest.id == request_id).first()
        if not request or request.organization_id != user.organization_id:
            raise ReviewError("Change request not found.")
        if not is_admin(user) and request.user_id != user.id:
            raise ReviewError("Change request not found.")
        return request

    def reject(self, reviewer: User, request_id: str, note: Optional[str] = None) -> ChangeRequest:
        request = self._pending_for_review(reviewer, request_id)
        request.status = "rejected"
        self._stamp(request, reviewer, note)
        self.db.commit()
        self.db.refresh(request)
        return request

    def approve(self, reviewer: User, request_id: str, note: Optional[str] = None) -> ChangeRequest:
        request = self._pending_for_review(reviewer, request_id)
        self._stamp(request, reviewer, note)
        record = self.db.query(DatabaseConnection).filter(DatabaseConnection.id == request.connection_id).first()

        try:
            if record is None:
                raise ReviewError("The database connection for this request no longer exists.")
            plan = plan_sql(request.sql, record.db_type)
            if not plan.ok or plan.kind != request.action:
                raise ReviewError(plan.error or "The stored statement no longer matches the request.")
            engine = connector.engine_for(record)
            count_sql, _, listed = _preview_queries(plan, record.db_type)
            current = connector.count(engine, count_sql) if count_sql else listed
            if current != request.affected_rows:
                raise ReviewError(
                    f"The data changed since this was requested ({current} matching rows now, "
                    f"{request.affected_rows} before). Nothing was changed; ask again."
                )
            connector.write(engine, request.sql, request.affected_rows)
            request.status = "executed"
            request.executed_at = datetime.utcnow()
        except Exception as exc:
            logger.warning("Change request %s failed: %s", request.id, exc)
            request.status = "failed"
            request.error = str(exc)

        self.db.commit()
        self.db.refresh(request)
        return request

    def _pending_for_review(self, reviewer: User, request_id: str) -> ChangeRequest:
        if not is_admin(reviewer):
            raise ReviewError("Only an admin can approve or reject changes.")
        request = self.get(reviewer, request_id)
        if request.status != "pending":
            raise ReviewError(f"This request is already {request.status}.")
        return request

    @staticmethod
    def _stamp(request: ChangeRequest, reviewer: User, note: Optional[str]) -> None:
        request.reviewed_by = reviewer.id
        request.reviewed_at = datetime.utcnow()
        request.review_note = (note or "").strip() or None


def serialize(db: Session, request: ChangeRequest) -> Dict[str, Any]:
    ids = {request.user_id, request.reviewed_by} - {None}
    names = {user.id: user.full_name for user in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    try:
        preview = json.loads(request.preview or "{}")
    except ValueError:
        preview = {}
    return {
        "id": request.id,
        "user_id": request.user_id,
        "requester": names.get(request.user_id),
        "question": request.question,
        "sql": request.sql,
        "action": request.action,
        "table_name": request.table_name,
        "affected_rows": request.affected_rows,
        "summary": summarize_request(request),
        "preview": preview,
        "status": request.status,
        "reviewed_by": request.reviewed_by,
        "reviewer": names.get(request.reviewed_by),
        "review_note": request.review_note,
        "error": request.error,
        "created_at": request.created_at,
        "reviewed_at": request.reviewed_at,
        "executed_at": request.executed_at,
    }
