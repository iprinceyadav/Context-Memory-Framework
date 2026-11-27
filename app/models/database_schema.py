from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, new_id

class DatabaseSchema(Base):
    __tablename__ = "database_schemas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    connection_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    schema_json: Mapped[str] = mapped_column(Text)
