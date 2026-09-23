from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, generate_uuid

class DatabaseSchema(Base):
    __tablename__ = "database_schemas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    connection_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    schema_json: Mapped[str] = mapped_column(Text) # JSON string of the schema
