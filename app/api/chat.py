from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import authorize_user, authorize_connection
from app.core.config import settings
from app.database.connection import SessionLocal, get_db
from app.models.conversation import Conversation, Message
from app.models.database_schema import DatabaseSchema
from app.models.query_memory import QueryMemory
from app.schemas.chat import ChatRequest, ChatResponse, ContextUsed, MemoryContext
from app.schemas.conversation import ConversationCreate, ConversationResponse, MessageResponse
from app.services.context_builder import ContextBuilder
from app.services.connector import connector
from app.services.encryption import decrypt
from app.services.llm import llm
from app.services.extraction import extractor
from app.services.memory_store import MemoryStore
from app.services.sql_guard import mask_literals, check_sql

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Context-Aware Chat"])


def _query_metadata(natural_query: str, sql: str, tables: List[str]) -> Dict[str, Any]:
    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
    order_match = re.search(r"\bORDER\s+BY\s+([^\n;]+?)(?:\bLIMIT\b|$)", sql, re.IGNORECASE)
    where_match = re.search(r"\bWHERE\s+(.+?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)", sql, re.IGNORECASE | re.DOTALL)

    return {
        "intent": natural_query[:120],
        "entities": tables,
        "filters": [where_match.group(1).strip()] if where_match else [],
        "sorting": order_match.group(1).strip() if order_match else None,
        "limit": int(limit_match.group(1)) if limit_match else None,
    }


def _extract_in_background(
    turns: List[Dict[str, str]],
    user_id: str,
    organization_id: str,
    connection_id: Optional[str],
    conversation_id: str,
    source_message_id: str,
    schema_hash: Optional[str],
    db_bind: Any = None,
) -> None:
    if not settings.MEMORY_AUTO_EXTRACT:
        return

    if db_bind is not None:
        from sqlalchemy.orm import sessionmaker
        session = sessionmaker(autocommit=False, autoflush=False, bind=db_bind)()
    else:
        session = SessionLocal()

    try:
        extracted = extractor.extract(
            conversation_messages=turns,
            user_id=user_id,
            organization_id=organization_id,
            connection_id=connection_id,
        )
        if extracted:
            store = MemoryStore(session)
            store.ingest(
                extracted=extracted,
                user_id=user_id,
                org_id=organization_id,
                connection_id=connection_id,
                source_conversation_id=conversation_id,
                source_message_id=source_message_id,
                schema_hash=schema_hash,
            )
    except Exception as exc:
        logger.warning("Background memory extraction failed gracefully: %s", exc)
    finally:
        session.close()


def _resolve_target(req: ChatRequest, db: Session) -> Dict[str, Any]:
    if req.connection_id:
        conn = authorize_connection(db, req.connection_id, req.organization_id)
        return {
            "db_type": conn.db_type,
            "host": conn.host,
            "port": conn.port,
            "database_name": conn.database_name,
            "username": conn.username,
            "password": decrypt(conn.encrypted_password),
            "connection_id": conn.id,
        }

    if not req.database_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either connection_id or database_name must be provided.",
        )

    return {
        "db_type": req.db_type,
        "host": req.host or "localhost",
        "port": req.port or (3306 if req.db_type == "mysql" else 5432),
        "database_name": req.database_name,
        "username": req.username or "",
        "password": req.password or "",
        "connection_id": None,
    }


@router.post("/ask", response_model=ChatResponse)
def ask(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not req.natural_query or not req.natural_query.strip():
        raise HTTPException(status_code=400, detail="natural_query cannot be empty")

    _, _ = authorize_user(db, req.organization_id, req.user_id)
    target = _resolve_target(req, db)

    conversation: Optional[Conversation] = None
    if req.conversation_id:
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.id == req.conversation_id,
                Conversation.organization_id == req.organization_id,
                Conversation.user_id == req.user_id,
            )
            .first()
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found or access denied")
    else:
        conversation = Conversation(
            title=req.natural_query[:80],
            organization_id=req.organization_id,
            user_id=req.user_id,
            connection_id=target["connection_id"],
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=req.natural_query.strip(),
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    schema_key = connector.fingerprint(
        db_type=target["db_type"],
        username=target["username"],
        host=target["host"],
        port=target["port"],
        db_name=target["database_name"],
    )
    schema_row = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
    if not schema_row:
        try:
            connector.sync_schema(
                db_type=target["db_type"],
                username=target["username"],
                password=target["password"],
                host=target["host"],
                port=target["port"],
                db_name=target["database_name"],
                platform_db=db,
            )
            schema_row = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Schema extraction failed: {exc}")

    if not schema_row:
        raise HTTPException(status_code=400, detail="Failed to retrieve or generate database schema.")

    schema_json_str = schema_row.schema_json

    context: Dict[str, Any]
    try:
        builder = ContextBuilder(db)
        context = builder.build(
            natural_query=req.natural_query,
            schema_json_str=schema_json_str,
            db_type=target["db_type"],
            organization_id=req.organization_id,
            user_id=req.user_id,
            connection_id=target["connection_id"],
            conversation_id=conversation.id,
        )
    except Exception as exc:
        logger.warning("ContextBuilder failed; falling back to schema-only prompt: %s", exc)
        context = {
            "prompt": None,
            "context_debug": {"query": req.natural_query, "error": str(exc)},
            "memory_ids": [],
            "used_memories": [],
            "schema_tables": [],
            "conversation_turns": 0,
        }

    try:
        if context["prompt"]:
            sql_query = llm.generate(context["prompt"])
        else:
            sql_query = llm.generate_sql(
                natural_query=req.natural_query,
                schema_json_str=schema_json_str,
                db_type=target["db_type"],
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM SQL generation failed: {exc}")

    is_safe, safety_error, referenced_tables = check_sql(
        sql=sql_query,
        db_type=target["db_type"],
        schema_json_str=schema_json_str,
        enforce_read_only=settings.ENFORCE_READ_ONLY_SQL,
    )
    if not is_safe:
        raise HTTPException(status_code=400, detail=safety_error)

    try:
        results = connector.execute(
            db_type=target["db_type"],
            username=target["username"],
            password=target["password"],
            host=target["host"],
            port=target["port"],
            db_name=target["database_name"],
            query=sql_query,
        )
        if isinstance(results, list) and len(results) > settings.MAX_QUERY_ROWS:
            results = results[: settings.MAX_QUERY_ROWS]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL execution failed: {exc}")

    preferences = [
        m["content"]
        for m in context.get("used_memories", [])
        if m.get("memory_type") == "procedural"
    ]
    try:
        builder = ContextBuilder(db)
        summary_prompt = builder.build_summary_prompt(
            query=req.natural_query,
            sql=sql_query,
            results=results,
            db_type=target["db_type"],
            preferences=preferences,
        )
        natural_response = llm.summarize(summary_prompt)
    except Exception as exc:
        logger.warning("Natural response generation fallback: %s", exc)
        row_count = len(results) if isinstance(results, list) else 0
        natural_response = f"Executed SQL query successfully and returned {row_count} row(s)."

    context_debug = context.get("context_debug", {})
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=natural_response,
        sql_query=sql_query,
        query_results=json.dumps({"row_count": len(results) if isinstance(results, list) else 0}, default=str),
        context_metadata=json.dumps(context_debug, default=str),
    )
    db.add(assistant_msg)

    query_meta = _query_metadata(req.natural_query, sql_query, referenced_tables)
    query_record = QueryMemory(
        organization_id=req.organization_id,
        user_id=req.user_id,
        connection_id=target["connection_id"],
        conversation_id=conversation.id,
        natural_language_query=req.natural_query.strip(),
        normalized_intent=query_meta["intent"],
        sanitized_sql_template=mask_literals(sql_query),
        structured_query_metadata=json.dumps(query_meta),
        execution_status="success",
        schema_hash=schema_key,
    )
    db.add(query_record)
    conversation.active_task_summary = req.natural_query[:200]
    db.commit()
    db.refresh(assistant_msg)

    turns = [
        {"role": "user", "content": req.natural_query.strip()},
        {"role": "assistant", "content": natural_response},
    ]
    background_tasks.add_task(
        _extract_in_background,
        turns,
        req.user_id,
        req.organization_id,
        target["connection_id"],
        conversation.id,
        user_msg.id,
        schema_key,
        db.get_bind(),
    )

    memory_refs = [
        MemoryContext(
            memory_id=m["memory_id"],
            content=m["content"],
            type=m["memory_type"],
            score=m["combined_score"],
        )
        for m in context.get("used_memories", [])
    ]

    return ChatResponse(
        conversation_id=conversation.id,
        message_id=assistant_msg.id,
        natural_response=natural_response,
        sql_query=sql_query,
        data=results,
        context_used=ContextUsed(
            memories=memory_refs,
            schema_tables=context.get("schema_tables", []),
            conversation_turns=context.get("conversation_turns", 0),
            tokens_used=context_debug.get("tokens_used", 0),
            token_budget=context_debug.get("token_budget", settings.MEMORY_TOKEN_BUDGET),
        ),
        context_debug=context_debug,
        extracted_memories=[],
    )


@router.get("/conversations", response_model=List[ConversationResponse])
def list_conversations(
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    authorize_user(db, organization_id, user_id)
    return (
        db.query(Conversation)
        .filter(
            Conversation.organization_id == organization_id,
            Conversation.user_id == user_id,
            Conversation.is_active == True,
        )
        .order_by(Conversation.updated_at.desc())
        .all()
    )


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    authorize_user(db, organization_id, user_id)
    authorize_connection(db, payload.connection_id, organization_id)

    conversation = Conversation(
        title=payload.title or "New Conversation",
        organization_id=organization_id,
        user_id=user_id,
        connection_id=payload.connection_id,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
def get_conversation_messages(
    conversation_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation.messages
