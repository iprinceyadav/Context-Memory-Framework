import logging
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from app.models import Base

logger = logging.getLogger(__name__)


def run_migrations(target_engine: Engine) -> None:
    Base.metadata.create_all(bind=target_engine)

    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())

    migrations = [
        ("conversations", "active_task_summary", "TEXT"),
        ("messages", "context_metadata", "TEXT"),
    ]

    with target_engine.begin() as conn:
        for table_name, column_name, col_type in migrations:
            if table_name in existing_tables:
                columns = {c["name"] for c in inspector.get_columns(table_name)}
                if column_name not in columns:
                    logger.info("Migrating table %s: adding column %s %s", table_name, column_name, col_type)
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {col_type}"))
