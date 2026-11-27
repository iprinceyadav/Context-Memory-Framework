from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Text, Float, Integer, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, generate_uuid


class Memory(Base, TimestampMixin):
    """Persistent long-term memory entry (semantic, episodic, or procedural)."""
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("database_connections.id"), nullable=True, index=True
    )

    memory_type: Mapped[str] = mapped_column(String, index=True)  # semantic, episodic, procedural
    content: Mapped[str] = mapped_column(Text)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String, default="personal")  # personal, connection, organization
    visibility: Mapped[str] = mapped_column(String, default="personal")  # personal, shared

    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    importance: Mapped[int] = mapped_column(Integer, default=5)

    source_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_conversation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schema_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    embedding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array of floats

    # Lifecycle status: pending, approved, rejected, superseded, expired, deleted
    status: Mapped[str] = mapped_column(String, default="pending", index=True)

    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    supersedes_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("memories.id"), nullable=True
    )

    versions: Mapped[List["MemoryVersion"]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        order_by="MemoryVersion.version_number",
    )
    audit_logs: Mapped[List["MemoryAuditLog"]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        order_by="MemoryAuditLog.created_at",
    )


class MemoryVersion(Base):
    """Stores historical versions of corrected or updated memories."""
    __tablename__ = "memory_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    previous_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_content: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String)
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memory: Mapped["Memory"] = relationship(back_populates="versions")


class MemoryAuditLog(Base):
    """Audit trail for all memory lifecycle events."""
    __tablename__ = "memory_audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    action: Mapped[str] = mapped_column(String, index=True)  # created, retrieved, approved, rejected, corrected, superseded, expired, deleted
    performed_by: Mapped[str] = mapped_column(String)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memory: Mapped["Memory"] = relationship(back_populates="audit_logs")

