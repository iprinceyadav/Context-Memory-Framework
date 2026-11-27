"""
Context-Aware NL2SQL Chat & Conversation API Routes.

Integrates ContextBuilder, SQL safety validation, QueryMemory tracking,
and non-blocking background memory extraction into the NL2SQL pipeline
while keeping the original /api/databases/ask endpoint untouched.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import ensure_default_context, verify_connection_ownership
from app.core.config import settings
from app.database.connection import SessionLocal, get_db
from app.models.conversation import Conversation, Message
from app.models.database_schema import DatabaseSchema
from app.models.query_memory import QueryMemory
from app.schemas.chat import ChatRequest, ChatResponse, ContextUsed, MemoryContext
from app.schemas.conversation import ConversationCreate, ConversationResponse, MessageResponse
from app.services.context_builder import ContextBuilder
from app.services.database_manager import db_manager
from app.services.encryption import decrypt_password
from app.services.llm_service import llm_service
from app.services.memory_extraction import memory_extraction_service
from app.services.memory_service import MemoryService
from app.services.sql_validator import sanitize_sql_template, validate_sql_safety

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Context-Aware Chat"])


def _extract_structured_query_metadata(natural_query: str, sql: str, tables: List[str]) -> Dict[str, Any]:
    """Derive lightweight structured metadata from natural query and SQL for follow-ups."""
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


def _background_extract_and_save(
    messages_payload: List[Dict[str, str]],
    user_id: str,
    organization_id: str,
    connection_id: Optional[str],
    conversation_id: str,
    source_message_id: str,
    schema_hash: Optional[str],
    db_bind: Any = None,
) -> None:
    """Run memory extraction and candidate storage in the background without blocking SQL response."""
    if not settings.MEMORY_AUTO_EXTRACT:
        return

    # Use same engine bind when provided (important for in-memory SQLite tests)
    if db_bind is not None:
        from sqlalchemy.orm import sessionmaker
        session = sessionmaker(autocommit=False, autoflush=False, bind=db_bind)()
    else:
        session = SessionLocal()

    try:
        extracted = memory_extraction_service.extract_memories(
            conversation_messages=messages_payload,
            user_id=user_id,
            organization_id=organization_id,
            connection_id=connection_id,
        )
        if extracted:
            mem_service = MemoryService(session)
            mem_service.process_extracted_memories(
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


def _resolve_db_credentials(req: ChatRequest, db: Session) -> Dict[str, Any]:
    """Resolve database connection details from connection_id or direct request credentials."""
    if req.connection_id:
        conn = verify_connection_ownership(db, req.connection_id, req.organization_id)
        return {
            "db_type": conn.db_type,
            "host": conn.host,
            "port": conn.port,
            "database_name": conn.database_name,
            "username": conn.username,
            "password": decrypt_password(conn.encrypted_password),
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
def ask_with_context(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Context-aware NL2SQL chat endpoint."""
    if not req.natural_query or not req.natural_query.strip():
        raise HTTPException(status_code=400, detail="natural_query cannot be empty")

    # 1. Authentication & Tenant Verification
    _, _ = ensure_default_context(db, req.organization_id, req.user_id)
    db_info = _resolve_db_credentials(req, db)

    # 2. Load or create Conversation
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
            connection_id=db_info["connection_id"],
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    # 3. Save incoming User Message
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=req.natural_query.strip(),
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # 4. Load Live Database Schema (sync if missing)
    conn_hash = db_manager.generate_connection_hash(
        db_type=db_info["db_type"],
        username=db_info["username"],
        host=db_info["host"],
        port=db_info["port"],
        db_name=db_info["database_name"],
    )
    schema_model = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == conn_hash).first()
    if not schema_model:
        try:
            db_manager.extract_and_save_schema(
                db_type=db_info["db_type"],
                username=db_info["username"],
                password=db_info["password"],
                host=db_info["host"],
                port=db_info["port"],
                db_name=db_info["database_name"],
                cmf_db=db,
            )
            schema_model = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == conn_hash).first()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Schema extraction failed: {exc}")

    if not schema_model:
        raise HTTPException(status_code=400, detail="Failed to retrieve or generate database schema.")

    schema_json_str = schema_model.schema_json

    # 5. Build Context-Aware Prompt (retrieval degrades gracefully on failure)
    context_result: Dict[str, Any]
    try:
        builder = ContextBuilder(db)
        context_result = builder.build_context(
            natural_query=req.natural_query,
            schema_json_str=schema_json_str,
            db_type=db_info["db_type"],
            organization_id=req.organization_id,
            user_id=req.user_id,
            connection_id=db_info["connection_id"],
            conversation_id=conversation.id,
        )
    except Exception as exc:
        logger.warning("ContextBuilder failed; falling back to schema-only prompt: %s", exc)
        context_result = {
            "prompt": None,
            "context_debug": {"query": req.natural_query, "error": str(exc)},
            "memory_ids": [],
            "used_memories": [],
            "schema_tables": [],
            "conversation_turns": 0,
        }

    # 6. Generate SQL via LLM
    try:
        if context_result["prompt"]:
            sql_query = llm_service.generate_with_context(context_result["prompt"])
        else:
            sql_query = llm_service.generate_sql(
                natural_query=req.natural_query,
                schema_json_str=schema_json_str,
                db_type=db_info["db_type"],
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM SQL generation failed: {exc}")

    # 7. Validate SQL Safety & Live Schema Compliance
    is_safe, safety_error, referenced_tables = validate_sql_safety(
        sql=sql_query,
        db_type=db_info["db_type"],
        schema_json_str=schema_json_str,
        enforce_read_only=settings.ENFORCE_READ_ONLY_SQL,
    )
    if not is_safe:
        raise HTTPException(status_code=400, detail=safety_error)

    # 8. Execute SQL against External Database
    try:
        results = db_manager.execute_direct_query(
            db_type=db_info["db_type"],
            username=db_info["username"],
            password=db_info["password"],
            host=db_info["host"],
            port=db_info["port"],
            db_name=db_info["database_name"],
            query=sql_query,
        )
        if isinstance(results, list) and len(results) > settings.MAX_QUERY_ROWS:
            results = results[: settings.MAX_QUERY_ROWS]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL execution failed: {exc}")

    # 9. Generate Natural Language Response
    procedural_prefs = [
        m["content"]
        for m in context_result.get("used_memories", [])
        if m.get("memory_type") == "procedural"
    ]
    try:
        builder = ContextBuilder(db)
        nl_prompt = builder.build_natural_response_prompt(
            query=req.natural_query,
            sql=sql_query,
            results=results,
            db_type=db_info["db_type"],
            preferences=procedural_prefs,
        )
        natural_response = llm_service.generate_natural_response(nl_prompt)
    except Exception as exc:
        logger.warning("Natural response generation fallback: %s", exc)
        row_count = len(results) if isinstance(results, list) else 0
        natural_response = f"Executed SQL query successfully and returned {row_count} row(s)."

    # 10. Save Assistant Message & QueryMemory (Working/Episodic task tracking)
    context_debug = context_result.get("context_debug", {})
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=natural_response,
        sql_query=sql_query,
        query_results=json.dumps({"row_count": len(results) if isinstance(results, list) else 0}, default=str),
        context_metadata=json.dumps(context_debug, default=str),
    )
    db.add(assistant_msg)

    query_meta = _extract_structured_query_metadata(req.natural_query, sql_query, referenced_tables)
    qm = QueryMemory(
        organization_id=req.organization_id,
        user_id=req.user_id,
        connection_id=db_info["connection_id"],
        conversation_id=conversation.id,
        natural_language_query=req.natural_query.strip(),
        normalized_intent=query_meta["intent"],
        sanitized_sql_template=sanitize_sql_template(sql_query),
        structured_query_metadata=json.dumps(query_meta),
        execution_status="success",
        schema_hash=conn_hash,
    )
    db.add(qm)
    conversation.active_task_summary = req.natural_query[:200]
    db.commit()
    db.refresh(assistant_msg)

    # 11. Schedule Non-Blocking Memory Extraction
    recent_msgs = [
        {"role": "user", "content": req.natural_query.strip()},
        {"role": "assistant", "content": natural_response},
    ]
    background_tasks.add_task(
        _background_extract_and_save,
        recent_msgs,
        req.user_id,
        req.organization_id,
        db_info["connection_id"],
        conversation.id,
        user_msg.id,
        conn_hash,
        db.get_bind(),
    )

    used_mems_models = [
        MemoryContext(
            memory_id=m["memory_id"],
            content=m["content"],
            type=m["memory_type"],
            score=m["combined_score"],
        )
        for m in context_result.get("used_memories", [])
    ]

    return ChatResponse(
        conversation_id=conversation.id,
        message_id=assistant_msg.id,
        natural_response=natural_response,
        sql_query=sql_query,
        data=results,
        context_used=ContextUsed(
            memories=used_mems_models,
            schema_tables=context_result.get("schema_tables", []),
            conversation_turns=context_result.get("conversation_turns", 0),
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
    """List conversations belonging to the authenticated user & organization."""
    ensure_default_context(db, organization_id, user_id)
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
    """Create a new conversation session."""
    ensure_default_context(db, organization_id, user_id)
    verify_connection_ownership(db, payload.connection_id, organization_id)

    conv = Conversation(
        title=payload.title or "New Conversation",
        organization_id=organization_id,
        user_id=user_id,
        connection_id=payload.connection_id,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
def get_conversation_messages(
    conversation_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    """Return ordered messages for a conversation owned by the user/org."""
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.messages

