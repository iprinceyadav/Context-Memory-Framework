"""
Idempotent database migration utility for CMF platform internal database.

Creates all tables registered on Base.metadata and safely applies additive
column migrations for existing SQLite/PostgreSQL databases.
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from app.models import Base

logger = logging.getLogger(__name__)


def run_migrations(target_engine: Engine) -> None:
    """Create missing tables and add any newly introduced columns idempotently."""
    Base.metadata.create_all(bind=target_engine)

    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())

    # Additive column migrations if tables pre-existed without new columns
    migrations = [
        ("conversations", "active_task_summary", "TEXT"),
        ("messages", "context_metadata", "TEXT"),
    ]

    with target_engine.begin() as conn:
        for table_name, column_name, col_type in migrations:
            if table_name in existing_tables:
                cols = {c["name"] for c in inspector.get_columns(table_name)}
                if column_name not in cols:
                    logger.info("Migrating table %s: adding column %s %s", table_name, column_name, col_type)
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {col_type}"))

