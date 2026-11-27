from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import authorize_connection, authorize_user, require_admin
from app.database.connection import get_db
from app.models.access import AccessGrant
from app.models.user import User
from app.schemas.access import (
    ChangeRequestOut,
    GrantCreate,
    GrantOut,
    MemberCreate,
    MemberOut,
    MemberUpdate,
    ReviewRequest,
)
from app.services.access import active_grants, is_admin
from app.services.changes import ChangeService, ReviewError, serialize

router = APIRouter(tags=["Team & Approvals"])


@router.get("/team/members", response_model=List[MemberOut])
def list_members(
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    authorize_user(db, organization_id, user_id)
    return (
        db.query(User)
        .filter(User.organization_id == organization_id)
        .order_by(User.created_at.asc())
        .all()
    )


@router.post("/team/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    payload: MemberCreate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    require_admin(db, organization_id, user_id)
    email = payload.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Someone with this email is already registered.")
    member = User(
        email=email,
        full_name=payload.full_name.strip(),
        hashed_password="",
        role=payload.role,
        organization_id=organization_id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.patch("/team/members/{member_id}", response_model=MemberOut)
def change_role(
    member_id: str,
    payload: MemberUpdate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    require_admin(db, organization_id, user_id)
    member = _member(db, organization_id, member_id)
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="The owner's role can't be changed.")
    member.role = payload.role
    db.commit()
    db.refresh(member)
    return member


@router.get("/team/grants", response_model=List[GrantOut])
def list_grants(
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    _, user = authorize_user(db, organization_id, user_id)
    return active_grants(db, organization_id, None if is_admin(user) else user.id)


@router.post("/team/grants", response_model=GrantOut, status_code=status.HTTP_201_CREATED)
def grant(
    payload: GrantCreate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    admin = require_admin(db, organization_id, user_id)
    member = _member(db, organization_id, payload.user_id)
    authorize_connection(db, payload.connection_id, organization_id)
    table_name = payload.table_name.strip().lower() or "*"

    existing = (
        db.query(AccessGrant)
        .filter(
            AccessGrant.organization_id == organization_id,
            AccessGrant.user_id == member.id,
            AccessGrant.action == payload.action,
            AccessGrant.table_name == table_name,
            AccessGrant.connection_id.is_(None)
            if payload.connection_id is None
            else AccessGrant.connection_id == payload.connection_id,
            AccessGrant.revoked_at.is_(None),
        )
        .first()
    )
    if existing:
        return existing

    record = AccessGrant(
        organization_id=organization_id,
        user_id=member.id,
        connection_id=payload.connection_id,
        table_name=table_name,
        action=payload.action,
        granted_by=admin.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/team/grants/{grant_id}")
def revoke(
    grant_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    require_admin(db, organization_id, user_id)
    record = (
        db.query(AccessGrant)
        .filter(AccessGrant.id == grant_id, AccessGrant.organization_id == organization_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Permission not found.")
    if record.revoked_at is None:
        record.revoked_at = datetime.utcnow()
        db.commit()
    return {"status": "revoked", "id": record.id}


@router.get("/changes", response_model=List[ChangeRequestOut])
def list_changes(
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    _, user = authorize_user(db, organization_id, user_id)
    service = ChangeService(db)
    return [serialize(db, request) for request in service.visible_to(user, status_filter)]


@router.get("/changes/{request_id}", response_model=ChangeRequestOut)
def get_change(
    request_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    _, user = authorize_user(db, organization_id, user_id)
    try:
        return serialize(db, ChangeService(db).get(user, request_id))
    except ReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/changes/{request_id}/approve", response_model=ChangeRequestOut)
def approve_change(
    request_id: str,
    payload: Optional[ReviewRequest] = None,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    reviewer = require_admin(db, organization_id, user_id)
    try:
        request = ChangeService(db).approve(reviewer, request_id, payload.note if payload else None)
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return serialize(db, request)


@router.post("/changes/{request_id}/reject", response_model=ChangeRequestOut)
def reject_change(
    request_id: str,
    payload: Optional[ReviewRequest] = None,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    reviewer = require_admin(db, organization_id, user_id)
    try:
        request = ChangeService(db).reject(reviewer, request_id, payload.note if payload else None)
    except ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return serialize(db, request)


def _member(db: Session, organization_id: str, member_id: str) -> User:
    member = db.query(User).filter(User.id == member_id, User.organization_id == organization_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found.")
    return member
