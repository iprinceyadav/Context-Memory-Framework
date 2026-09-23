from sqlalchemy import String, ForeignKey, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, generate_uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.organization import Organization

class DatabaseConnection(Base, TimestampMixin):
    __tablename__ = "database_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String) # e.g. "Production Data"
    db_type: Mapped[str] = mapped_column(String) # e.g. "postgresql", "mysql"
    
    # Encrypted credentials (in a real app, never store plain text)
    host: Mapped[str] = mapped_column(String)
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String)
    encrypted_password: Mapped[str] = mapped_column(String)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    organization: Mapped["Organization"] = relationship(back_populates="database_connections")
