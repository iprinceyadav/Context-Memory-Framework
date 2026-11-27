"""
Lightweight authentication & authorization context helpers.

Preserves backward compatibility with existing endpoints while enforcing
tenant isolation, user verification, and role-based permissions for memory
and shared-knowledge operations.
"""

from typing import Optional
from fastapi import Header, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.models.organization import Organization
from app.models.user import User
from app.models.database_connection import DatabaseConnection

ADMIN_ROLES = {"owner", "admin"}
CONTRIBUTOR_ROLES = {"owner", "admin", "analyst"}


def ensure_default_context(db: Session, organization_id: str, user_id: str) -> tuple[Organization, User]:
    """Verify organization and user exist and belong to the same tenant.

    For seamless local/demo compatibility when using the default workspace IDs
    ('default-org' / 'default-user'), provisions them automatically if missing.
    For any other IDs, strictly validates existence and tenant membership.
    """
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        if organization_id == "default-org":
            org = Organization(id="default-org", name="Default Organization", domain="default.local")
            db.add(org)
            db.flush()
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found",
            )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        if user_id == "default-user" and organization_id == "default-org":
            user = User(
                id="default-user",
                email="analyst@default.local",
                full_name="Default Analyst",
                hashed_password="hashed_default",
                role="owner",
                organization_id="default-org",
            )
            db.add(user)
            db.flush()
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

    if user.organization_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to the specified organization",
        )

    db.commit()
    return org, user


def verify_connection_ownership(
    db: Session, connection_id: Optional[str], organization_id: str
) -> Optional[DatabaseConnection]:
    """Verify that a connection_id exists and belongs to the given organization."""
    if not connection_id:
        return None
    conn = db.query(DatabaseConnection).filter(DatabaseConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database connection not found",
        )
    if conn.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: connection belongs to a different organization",
        )
    return conn


def check_shared_memory_permission(user: User, visibility: str, scope: str) -> None:
    """Verify user role allows creating/approving shared or organization-wide memories."""
    if visibility == "shared" or scope == "organization":
        if user.role not in CONTRIBUTOR_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role permissions to manage shared or organization-wide memories",
            )

