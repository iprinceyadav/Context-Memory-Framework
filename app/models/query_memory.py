from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, generate_uuid


class QueryMemory(Base):
    """Tracks structured representations of previous analytical queries for follow-up intelligence."""
    __tablename__ = "query_memories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("database_connections.id"), nullable=True, index=True
    )
    conversation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True
    )

    natural_language_query: Mapped[str] = mapped_column(Text)
    normalized_intent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sanitized_sql_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured_query_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    execution_status: Mapped[str] = mapped_column(String, default="success")  # success, failed, partial
    schema_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

