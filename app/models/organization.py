from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, new_id
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.database_connection import DatabaseConnection

class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    domain: Mapped[str | None] = mapped_column(String, unique=True, index=True)

    users: Mapped[List["User"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    database_connections: Mapped[List["DatabaseConnection"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
