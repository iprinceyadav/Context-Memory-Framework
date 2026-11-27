from datetime import datetime
from typing import List, Optional, TYPE_CHECKING
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, new_id

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.database_connection import DatabaseConnection


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("database_connections.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    active_task_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    organization: Mapped["Organization"] = relationship()
    user: Mapped["User"] = relationship()
    connection: Mapped[Optional["DatabaseConnection"]] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    sql_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    query_results: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
