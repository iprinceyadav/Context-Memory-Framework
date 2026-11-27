from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class AccessGrant(Base, TimestampMixin):
    __tablename__ = "access_grants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("database_connections.id"), nullable=True, index=True
    )
    table_name: Mapped[str] = mapped_column(String, default="*")
    action: Mapped[str] = mapped_column(String)
    granted_by: Mapped[str] = mapped_column(String)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ChangeRequest(Base, TimestampMixin):
    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("database_connections.id"), index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("conversations.id"), nullable=True)

    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String)
    table_name: Mapped[str] = mapped_column(String)
    affected_rows: Mapped[int] = mapped_column(Integer)
    preview: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
