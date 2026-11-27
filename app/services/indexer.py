from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from app.models.database_schema import DatabaseSchema
from app.models.memory import Memory
from app.models.query_memory import QueryMemory
from app.services.embedder import embedder
from app.services.vector_store import (
    MEMORIES,
    QUERY_EXAMPLES,
    SCHEMA_TABLES,
    Document,
    all_of,
    match,
    vector_store,
)

logger = logging.getLogger(__name__)

INDEXED_STATUSES = ("approved", "pending", "superseded")


def memory_text(memory: Memory) -> str:
    return f"{memory.subject}: {memory.content}" if memory.subject else memory.content


def _stored_vector(raw: Optional[str]) -> Optional[List[float]]:
    if not raw:
        return None
    try:
        vector = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return vector if isinstance(vector, list) and len(vector) == embedder.dimension else None


def _memory_document(memory: Memory) -> Document:
    return Document(
        id=memory.id,
        text=memory_text(memory),
        vector=_stored_vector(memory.embedding),
        payload={
            "organization_id": memory.organization_id,
            "user_id": memory.user_id,
            "visibility": memory.visibility or "personal",
            "scope": memory.scope or "personal",
            "connection_id": memory.connection_id or "",
            "status": memory.status,
            "memory_type": memory.memory_type,
        },
    )


def sync_memory(memory: Memory) -> None:
    if memory.status in INDEXED_STATUSES:
        vector_store.upsert(MEMORIES, [_memory_document(memory)])
    else:
        vector_store.delete(MEMORIES, [memory.id])


def sync_memories(memories: Iterable[Memory]) -> None:
    for memory in memories:
        sync_memory(memory)


def table_text(table: str, columns: List[Dict[str, Any]]) -> str:
    parts = []
    for column in columns:
        detail = str(column.get("type", ""))
        if column.get("references"):
            detail += f", references {column['references']}"
        if column.get("values"):
            detail += f", values: {', '.join(str(v) for v in column['values'])}"
        parts.append(f"{column.get('name')} ({detail})")
    return f"Table {table}. Columns: " + "; ".join(parts)


def schema_point_id(schema_key: str, table: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"schema:{schema_key}:{table}"))


def index_schema(schema_key: str, schema: Dict[str, List[Dict[str, Any]]]) -> None:
    vector_store.delete_where(SCHEMA_TABLES, all_of(match("schema_key", schema_key)))
    vector_store.upsert(
        SCHEMA_TABLES,
        [
            Document(
                id=schema_point_id(schema_key, table),
                text=table_text(table, columns),
                payload={"schema_key": schema_key, "table": table},
            )
            for table, columns in schema.items()
        ],
    )


def _example_document(record: QueryMemory) -> Document:
    return Document(
        id=record.id,
        text=record.natural_language_query,
        payload={
            "organization_id": record.organization_id,
            "user_id": record.user_id,
            "schema_key": record.schema_hash or "",
            "sql": record.sanitized_sql_template or "",
        },
    )


def index_example(record: QueryMemory) -> None:
    if record.execution_status == "success" and record.sanitized_sql_template and record.schema_hash:
        vector_store.upsert(QUERY_EXAMPLES, [_example_document(record)])


def _indexable_memories(db: Session) -> List[Memory]:
    return db.query(Memory).filter(Memory.status.in_(INDEXED_STATUSES)).all()


def _indexable_examples(db: Session) -> List[QueryMemory]:
    return (
        db.query(QueryMemory)
        .filter(
            QueryMemory.execution_status == "success",
            QueryMemory.sanitized_sql_template.isnot(None),
            QueryMemory.schema_hash.isnot(None),
        )
        .all()
    )


def _schemas(db: Session) -> List[tuple]:
    loaded = []
    for row in db.query(DatabaseSchema).all():
        try:
            loaded.append((row.connection_hash, json.loads(row.schema_json)))
        except (TypeError, ValueError):
            continue
    return loaded


def rebuild(db: Session, names: Iterable[str] = (MEMORIES, SCHEMA_TABLES, QUERY_EXAMPLES)) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for name in names:
        vector_store.reset(name)
        if name == MEMORIES:
            memories = _indexable_memories(db)
            vector_store.upsert(MEMORIES, [_memory_document(m) for m in memories])
            counts[name] = len(memories)
        elif name == SCHEMA_TABLES:
            schemas = _schemas(db)
            for schema_key, schema in schemas:
                index_schema(schema_key, schema)
            counts[name] = sum(len(schema) for _, schema in schemas)
        elif name == QUERY_EXAMPLES:
            examples = _indexable_examples(db)
            vector_store.upsert(QUERY_EXAMPLES, [_example_document(r) for r in examples])
            counts[name] = len(examples)
    return counts


def ensure_index(db: Session) -> Dict[str, int]:
    if not vector_store.available:
        return {}
    expected = {
        MEMORIES: len(_indexable_memories(db)),
        SCHEMA_TABLES: sum(len(schema) for _, schema in _schemas(db)),
        QUERY_EXAMPLES: len(_indexable_examples(db)),
    }
    stale = [name for name, total in expected.items() if vector_store.count(name) != total]
    if not stale:
        return {}
    logger.info("Rebuilding vector index for %s", ", ".join(stale))
    return rebuild(db, stale)
