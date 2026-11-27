from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import authorize_user, authorize_connection
from app.core.config import settings
from app.database.connection import SessionLocal, get_db
from app.models.conversation import Conversation, Message
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.models.query_memory import QueryMemory
from app.schemas.chat import ChatRequest, ChatResponse, ContextUsed, MemoryContext
from app.schemas.conversation import ConversationCreate, ConversationResponse, MessageResponse
from app.services.access import is_admin, mask_rows
from app.services.changes import ChangeRefused, ChangeService, serialize, summarize_request
from app.services.context_builder import ContextBuilder, describe_results
from app.services.connector import connector
from app.services.encryption import decrypt
from app.services.indexer import index_example
from app.services.llm import llm
from app.services.extraction import extractor
from app.services.memory_store import MemoryStore
from app.services.sql_guard import mask_literals, plan_sql

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


def _resolve_target(req: ChatRequest, db: Session) -> Tuple[Dict[str, Any], Optional[DatabaseConnection]]:
    if req.connection_id:
        record = authorize_connection(db, req.connection_id, req.organization_id)
        return {
            "db_type": record.db_type,
            "host": record.host,
            "port": record.port,
            "database_name": record.database_name,
            "username": record.username,
            "password": decrypt(record.encrypted_password),
            "connection_id": record.id,
        }, record

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
    }, None


def _load_schema(db: Session, target: Dict[str, Any]) -> Tuple[str, str]:
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
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Schema extraction failed: {exc}")
        schema_row = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
    if not schema_row:
        raise HTTPException(status_code=400, detail="Failed to retrieve or generate database schema.")
    return schema_key, schema_row.schema_json


def _previous_question(db: Session, conversation_id: str) -> Optional[str]:
    last = (
        db.query(QueryMemory)
        .filter(QueryMemory.conversation_id == conversation_id)
        .order_by(QueryMemory.created_at.desc())
        .first()
    )
    return last.natural_language_query if last else None


def _run_read(
    target: Dict[str, Any], record: Optional[DatabaseConnection], sql: str
) -> Tuple[List[Dict[str, Any]], bool]:
    if record is not None:
        return connector.read(connector.engine_for(record), sql, settings.MAX_QUERY_ROWS)
    engine = connector.open_engine(
        db_type=target["db_type"],
        username=target["username"],
        password=target["password"],
        host=target["host"],
        port=target["port"],
        db_name=target["database_name"],
    )
    try:
        return connector.read(engine, sql, settings.MAX_QUERY_ROWS)
    finally:
        engine.dispose()


@router.post("/ask", response_model=ChatResponse)
def ask(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not req.natural_query or not req.natural_query.strip():
        raise HTTPException(status_code=400, detail="natural_query cannot be empty")

    question = req.natural_query.strip()
    _, user = authorize_user(db, req.organization_id, req.user_id)
    target, record = _resolve_target(req, db)

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
            title=question[:80],
            organization_id=req.organization_id,
            user_id=req.user_id,
            connection_id=target["connection_id"],
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    previous_question = _previous_question(db, conversation.id)
    user_msg = Message(conversation_id=conversation.id, role="user", content=question)
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    schema_key, schema_json_str = _load_schema(db, target)

    context: Dict[str, Any]
    try:
        context = ContextBuilder(db).build(
            natural_query=question,
            schema_json_str=schema_json_str,
            db_type=target["db_type"],
            organization_id=req.organization_id,
            user_id=req.user_id,
            connection_id=target["connection_id"],
            conversation_id=conversation.id,
            schema_key=schema_key,
        )
    except Exception as exc:
        logger.warning("ContextBuilder failed; falling back to schema-only prompt: %s", exc)
        context = {
            "prompt": None,
            "context_debug": {"query": question, "error": str(exc)},
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
                natural_query=question,
                schema_json_str=schema_json_str,
                db_type=target["db_type"],
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM SQL generation failed: {exc}")

    context_debug = context.get("context_debug", {})
    used_memories = context.get("used_memories", [])
    context_used = ContextUsed(
        memories=[
            MemoryContext(
                memory_id=m["memory_id"],
                content=m["content"],
                type=m["memory_type"],
                score=m["combined_score"],
            )
            for m in used_memories
        ],
        schema_tables=context.get("schema_tables", []),
        conversation_turns=context.get("conversation_turns", 0),
        tokens_used=context_debug.get("tokens_used", 0),
        token_budget=context_debug.get("token_budget", settings.MEMORY_TOKEN_BUDGET),
    )

    def reply(outcome: str, text: str, change_request: Optional[Dict[str, Any]] = None) -> ChatResponse:
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=text,
            sql_query=sql_query,
            context_metadata=json.dumps(context_debug, default=str),
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return ChatResponse(
            conversation_id=conversation.id,
            message_id=message.id,
            natural_response=text,
            sql_query=sql_query,
            data=None,
            context_used=context_used,
            context_debug=context_debug,
            extracted_memories=[],
            outcome=outcome,
            change_request=change_request,
        )

    plan = plan_sql(sql_query, target["db_type"], schema_json_str)
    if not plan.ok:
        return reply("not_allowed", plan.error)

    if plan.is_write:
        try:
            request = ChangeService(db).propose(user, record, plan, question, conversation.id)
        except ChangeRefused as refusal:
            return reply(refusal.outcome, refusal.message)
        except Exception as exc:
            logger.warning("Could not prepare change request: %s", exc)
            return reply("not_allowed", f"I couldn't prepare that change: {exc}")
        follow_up = (
            "Review it and approve it to apply the change."
            if is_admin(user)
            else "An admin has to approve it before anything changes."
        )
        return reply(
            "change_requested",
            f"{summarize_request(request)} is ready. {follow_up}",
            serialize(db, request),
        )

    try:
        rows, truncated = _run_read(target, record, plan.sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL execution failed: {exc}")

    rows, hidden_columns = mask_rows(rows, user)
    definitions = [m["content"] for m in used_memories if m.get("memory_type") == "semantic"]
    preferences = [m["content"] for m in used_memories if m.get("memory_type") == "procedural"]
    try:
        summary_prompt = ContextBuilder(db).build_summary_prompt(
            query=question,
            sql=plan.sql,
            results=rows,
            db_type=target["db_type"],
            preferences=preferences,
            definitions=definitions,
            truncated=truncated,
            hidden_columns=hidden_columns,
            previous_question=previous_question,
        )
        natural_response = llm.summarize(summary_prompt)
    except Exception as exc:
        logger.warning("Natural response generation fallback: %s", exc)
        natural_response = describe_results(rows, truncated)

    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=natural_response,
        sql_query=sql_query,
        query_results=json.dumps({"row_count": len(rows), "truncated": truncated}),
        context_metadata=json.dumps(context_debug, default=str),
    )
    db.add(assistant_msg)

    query_meta = _query_metadata(question, plan.sql, plan.tables)
    query_record = QueryMemory(
        organization_id=req.organization_id,
        user_id=req.user_id,
        connection_id=target["connection_id"],
        conversation_id=conversation.id,
        natural_language_query=question,
        normalized_intent=query_meta["intent"],
        sanitized_sql_template=mask_literals(plan.sql),
        structured_query_metadata=json.dumps(query_meta),
        execution_status="success",
        schema_hash=schema_key,
    )
    db.add(query_record)
    conversation.active_task_summary = question[:200]
    db.commit()
    db.refresh(assistant_msg)
    index_example(query_record)

    background_tasks.add_task(
        _extract_in_background,
        [
            {"role": "user", "content": question},
            {"role": "assistant", "content": natural_response},
        ],
        req.user_id,
        req.organization_id,
        target["connection_id"],
        conversation.id,
        user_msg.id,
        schema_key,
        db.get_bind(),
    )

    return ChatResponse(
        conversation_id=conversation.id,
        message_id=assistant_msg.id,
        natural_response=natural_response,
        sql_query=sql_query,
        data=rows,
        context_used=context_used,
        context_debug=context_debug,
        extracted_memories=[],
        outcome="answer",
        truncated=truncated,
        hidden_columns=hidden_columns,
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
