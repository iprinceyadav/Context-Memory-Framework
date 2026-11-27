from sqlalchemy import String, ForeignKey, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, new_id
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.organization import Organization

class DatabaseConnection(Base, TimestampMixin):
    __tablename__ = "database_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    db_type: Mapped[str] = mapped_column(String)

    host: Mapped[str] = mapped_column(String)
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String)
    encrypted_password: Mapped[str] = mapped_column(String)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    organization: Mapped["Organization"] = relationship(back_populates="database_connections")
