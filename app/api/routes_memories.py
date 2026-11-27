"""
Memory Management, Search, Lifecycle, Debugging & Analytics API Routes.

Enforces tenant isolation, user ownership/visibility rules, and connection
ownership on every endpoint.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import (
    check_shared_memory_permission,
    ensure_default_context,
    verify_connection_ownership,
)
from app.database.connection import get_db
from app.models.conversation import Conversation, Message
from app.schemas.chat import ContextDebugInfo
from app.schemas.memory import (
    MemoryAuditLogResponse,
    MemoryCreate,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResult,
    MemoryStatistics,
    MemoryUpdate,
    MemoryVersionResponse,
)
from app.services.memory_retrieval import MemoryRetrievalService
from app.services.memory_service import MemoryService

router = APIRouter(tags=["Memories"])


@router.get("/memories", response_model=List[MemoryResponse])
@router.get("/memories/", response_model=List[MemoryResponse], include_in_schema=False)
def list_memories(
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    connection_id: Optional[str] = Query(default=None),
    memory_type: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List authorized memories with optional filtering."""
    ensure_default_context(db, organization_id, user_id)
    verify_connection_ownership(db, connection_id, organization_id)
    service = MemoryService(db)
    return service.list_memories(
        org_id=organization_id,
        user_id=user_id,
        connection_id=connection_id,
        memory_type=memory_type,
        status_filter=status_filter,
        skip=skip,
        limit=limit,
    )


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
@router.post("/memories/", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_memory(
    payload: MemoryCreate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    """Manually create a business/procedural/episodic memory."""
    _, user = ensure_default_context(db, organization_id, user_id)
    verify_connection_ownership(db, payload.connection_id, organization_id)
    check_shared_memory_permission(user, payload.visibility, payload.scope)

    service = MemoryService(db)
    return service.create_memory(
        data=payload.model_dump(),
        user_id=user_id,
        org_id=organization_id,
        initial_status="approved" if payload.auto_approve else "pending",
        auto_approve=payload.auto_approve,
    )


@router.get("/memories/statistics", response_model=MemoryStatistics)
@router.get("/memory/statistics", response_model=MemoryStatistics)
def get_memory_statistics(
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    """Get memory statistics for dashboard overview."""
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    return service.get_statistics(org_id=organization_id, user_id=user_id)


@router.post("/memories/search", response_model=List[MemorySearchResult])
def search_memories(
    req: MemorySearchRequest,
    db: Session = Depends(get_db),
):
    """Hybrid semantic + keyword + recency + importance + type retrieval."""
    ensure_default_context(db, req.organization_id, req.user_id)
    verify_connection_ownership(db, req.connection_id, req.organization_id)

    retrieval = MemoryRetrievalService(db)
    results = retrieval.search_memories(
        query=req.query,
        organization_id=req.organization_id,
        user_id=req.user_id,
        connection_id=req.connection_id,
        memory_types=req.memory_types,
        limit=req.limit,
        include_superseded=req.include_superseded,
        only_approved=req.only_approved,
    )
    return [
        MemorySearchResult(
            memory_id=r["memory_id"],
            memory_type=r["memory_type"],
            subject=r.get("subject"),
            content=r["content"],
            status=r["status"],
            scope=r["scope"],
            visibility=r["visibility"],
            importance=r["importance"],
            vector_similarity=r["vector_similarity"],
            keyword_relevance=r["keyword_relevance"],
            recency_score=r["recency_score"],
            importance_score=r["importance_score"],
            type_relevance=r["type_relevance"],
            combined_score=r["combined_score"],
        )
        for r in results
    ]


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
def get_memory(
    memory_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    return service.get_memory(memory_id=memory_id, user_id=user_id, org_id=organization_id)


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
def update_memory(
    memory_id: str,
    payload: MemoryUpdate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    _, user = ensure_default_context(db, organization_id, user_id)
    if payload.visibility or payload.scope:
        check_shared_memory_permission(
            user, payload.visibility or "personal", payload.scope or "personal"
        )
    service = MemoryService(db)
    updates = payload.model_dump(exclude_unset=True)
    reason = updates.pop("change_reason", None) or "API update"
    return service.update_memory(
        memory_id=memory_id,
        updates=updates,
        user_id=user_id,
        org_id=organization_id,
        reason=reason,
    )


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: str,
    hard_delete: bool = Query(default=False),
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    service.delete_memory(
        memory_id=memory_id,
        user_id=user_id,
        org_id=organization_id,
        hard_delete=hard_delete,
    )
    return {"status": "deleted", "memory_id": memory_id}


@router.post("/memories/{memory_id}/approve", response_model=MemoryResponse)
def approve_memory(
    memory_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    return service.approve_memory(memory_id=memory_id, user_id=user_id, org_id=organization_id)


@router.post("/memories/{memory_id}/reject", response_model=MemoryResponse)
def reject_memory(
    memory_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    return service.reject_memory(memory_id=memory_id, user_id=user_id, org_id=organization_id)


@router.post("/memories/{memory_id}/supersede", response_model=MemoryResponse)
def supersede_memory(
    memory_id: str,
    payload: MemoryCreate,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    _, user = ensure_default_context(db, organization_id, user_id)
    check_shared_memory_permission(user, payload.visibility, payload.scope)
    service = MemoryService(db)
    return service.supersede_memory(
        old_memory_id=memory_id,
        new_memory_data=payload.model_dump(),
        user_id=user_id,
        org_id=organization_id,
        auto_approve=True,
    )


@router.get("/memories/{memory_id}/versions", response_model=List[MemoryVersionResponse])
def get_memory_versions(
    memory_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    return service.get_versions(memory_id=memory_id, user_id=user_id, org_id=organization_id)


@router.get("/memories/{memory_id}/audit", response_model=List[MemoryAuditLogResponse])
def get_memory_audit_log(
    memory_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    ensure_default_context(db, organization_id, user_id)
    service = MemoryService(db)
    return service.get_audit_log(memory_id=memory_id, user_id=user_id, org_id=organization_id)


@router.get("/memory/debug/{conversation_id}", response_model=ContextDebugInfo)
def debug_conversation_context(
    conversation_id: str,
    organization_id: str = Query(default="default-org"),
    user_id: str = Query(default="default-user"),
    db: Session = Depends(get_db),
):
    """Inspect the retrieved, excluded, scored memories and token budget for a conversation's latest turn."""
    ensure_default_context(db, organization_id, user_id)
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

    latest_assistant = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.context_metadata.isnot(None),
        )
        .order_by(Message.created_at.desc())
        .first()
    )
    if not latest_assistant or not latest_assistant.context_metadata:
        return ContextDebugInfo(query="")

    try:
        meta = json.loads(latest_assistant.context_metadata)
        return ContextDebugInfo(
            query=meta.get("query", ""),
            retrieved_memories=meta.get("retrieved_memories", []),
            excluded_memories=meta.get("excluded_memories", []),
            similarity_scores=meta.get("similarity_scores", []),
            keyword_scores=meta.get("keyword_scores", []),
            combined_scores=meta.get("combined_scores", []),
            token_budget=meta.get("token_budget", 0),
            tokens_used=meta.get("tokens_used", 0),
            schema_tables=meta.get("schema_tables", []),
            conversation_turns=meta.get("conversation_turns", 0),
            final_prompt_preview=meta.get("final_prompt_preview"),
        )
    except Exception:
        return ContextDebugInfo(query="")


_TESTS_DIR = Path(__file__).resolve().parents[2] / "tests"
_EVAL_RESULTS_PATH = _TESTS_DIR / "evaluation_results.json"
_EVAL_DATASET_PATH = _TESTS_DIR / "evaluation_dataset.json"


def _load_evaluation_payload(category: Optional[str] = None) -> Dict[str, Any]:
    results = None
    dataset: List[Dict[str, Any]] = []

    if _EVAL_RESULTS_PATH.exists():
        results = json.loads(_EVAL_RESULTS_PATH.read_text(encoding="utf-8"))
    if _EVAL_DATASET_PATH.exists():
        dataset = json.loads(_EVAL_DATASET_PATH.read_text(encoding="utf-8"))

    categories = sorted({item.get("category", "") for item in dataset if item.get("category")})
    filtered_dataset = (
        [item for item in dataset if item.get("category") == category]
        if category
        else dataset
    )

    return {
        "results_exists": results is not None,
        "dataset_exists": _EVAL_DATASET_PATH.exists(),
        "results": results,
        "categories": categories,
        "total_dataset_count": len(dataset),
        "dataset": filtered_dataset,
    }


@router.get("/evaluation/results")
def get_evaluation_results(category: Optional[str] = Query(default=None)):
    """Return offline benchmark metrics and dataset cases from tests/evaluation_*.json."""
    return _load_evaluation_payload(category=category)


@router.post("/evaluation/run")
def run_evaluation_benchmark():
    """Execute tests/run_evaluation.py and return updated results and dataset."""
    script_path = _TESTS_DIR / "run_evaluation.py"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Evaluation script not found")

    spec = importlib.util.spec_from_file_location("cmf_run_evaluation", script_path)
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=500, detail="Failed to load evaluation module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run_benchmark()
    return _load_evaluation_payload()


