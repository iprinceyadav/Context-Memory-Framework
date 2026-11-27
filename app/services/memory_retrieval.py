"""
Memory Retrieval Service for the Context Memory Framework.

Provides authorization-first, hybrid-ranked search, duplicate detection,
and contradiction detection over the persistent Memory store.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.memory import Memory
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

_RECENCY_HALF_LIFE_DAYS: float = 30.0
_DUPLICATE_SIMILARITY_THRESHOLD: float = 0.96

_STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "is", "it", "by", "with", "as", "was", "that", "this", "are",
    "be", "has", "had", "have", "not", "from", "will", "can", "do", "does",
    "did", "been", "if", "so", "no", "what", "when", "where", "which",
    "who", "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "just", "about", "show",
    "me", "my", "get", "give", "list",
}

_TYPE_INTENT_MAP: Dict[str, List[str]] = {
    "revenue": ["semantic", "business_rule"],
    "profit": ["semantic", "business_rule"],
    "metric": ["semantic", "business_rule"],
    "define": ["semantic", "business_rule"],
    "exclude": ["semantic", "business_rule"],
    "customer": ["semantic", "episodic"],
    "financial": ["semantic", "procedural"],
    "report": ["procedural", "semantic"],
    "format": ["procedural", "preference"],
    "chart": ["procedural", "preference"],
    "currency": ["procedural", "preference"],
    "always": ["procedural", "preference"],
    "prefer": ["procedural", "preference"],
    "use": ["procedural", "semantic"],
    "previous": ["episodic", "query_pattern"],
    "last": ["episodic", "query_pattern"],
    "earlier": ["episodic", "query_pattern"],
    "history": ["episodic", "query_pattern"],
    "schema": ["semantic", "schema_knowledge"],
    "table": ["semantic", "schema_knowledge"],
    "column": ["semantic", "schema_knowledge"],
}

_EXCLUDED_STATUSES: Set[str] = {"rejected", "deleted", "expired"}


def _tokenize(text: str) -> Set[str]:
    """Lower-case word tokenisation with stop-word removal."""
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    return words - _STOP_WORDS


def _parse_embedding(raw: Optional[str]) -> List[float]:
    """Safely parse a JSON-encoded embedding string."""
    if not raw:
        return []
    try:
        vec = json.loads(raw)
        if isinstance(vec, list):
            return vec
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _recency_score(created_at: Optional[datetime]) -> float:
    """Exponential-decay score based on age in days (1.0 = brand-new)."""
    if created_at is None:
        return 0.5

    now = datetime.utcnow()
    if created_at.tzinfo is not None:
        now = datetime.now(timezone.utc)

    delta_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return math.pow(0.5, delta_days / _RECENCY_HALF_LIFE_DAYS)


def _importance_score(importance: Optional[int]) -> float:
    """Normalise an importance rating (1-10) to 0.0-1.0."""
    if importance is None:
        return 0.5
    clamped = max(1, min(int(importance), 10))
    return (clamped - 1) / 9.0


def _keyword_relevance(query_tokens: Set[str], content_tokens: Set[str]) -> float:
    """Word-overlap ratio between query and content tokens."""
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = query_tokens & content_tokens
    return len(overlap) / len(query_tokens)


def _type_relevance(query_text: str, memory_type: Optional[str]) -> float:
    """Score how well a memory's type matches the inferred intent of a query."""
    if not memory_type:
        return 0.5

    query_lower = query_text.lower()
    memory_type_lower = memory_type.lower()

    matched_any = False
    for keyword, relevant_types in _TYPE_INTENT_MAP.items():
        if keyword in query_lower:
            matched_any = True
            for rt in relevant_types:
                if rt in memory_type_lower or memory_type_lower in rt:
                    return 1.0
    if matched_any:
        return 0.4
    return 0.6


def _is_historical_question(query_text: str) -> bool:
    """Heuristic check for whether the user is asking about past/historical definitions."""
    q = query_text.lower()
    markers = ("previously", "historically", "used to", "old definition", "past", "before migration", "was previously")
    return any(m in q for m in markers)


class MemoryRetrievalService:
    """Authorization-first ranked retrieval, duplicate detection, and
    contradiction detection over the persistent Memory store.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _apply_authorization_filters(
        self,
        q,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
    ):
        """Enforce tenant, user/visibility, and database connection isolation BEFORE retrieval."""
        # Strict tenant isolation
        q = q.filter(Memory.organization_id == organization_id)

        # User visibility isolation: personal memories only for owner; shared visible to same org
        q = q.filter(
            or_(
                Memory.user_id == user_id,
                Memory.visibility == "shared",
            )
        )

        # Database connection isolation: never leak Database A memories into Database B
        if connection_id is not None:
            q = q.filter(
                or_(
                    Memory.connection_id == connection_id,
                    (Memory.connection_id.is_(None)) & (Memory.scope != "connection"),
                )
            )
        else:
            q = q.filter(Memory.scope != "connection")

        return q

    def search_memories_with_diagnostics(
        self,
        query: str,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
        memory_types: Optional[List[str]] = None,
        limit: int = 10,
        include_superseded: bool = False,
        only_approved: bool = True,
    ) -> Tuple[List[dict], List[dict]]:
        """Search memories and return both (selected, excluded) diagnostics."""
        if not query or not query.strip():
            return [], []

        now = datetime.utcnow()

        q = self.db.query(Memory)
        q = self._apply_authorization_filters(q, organization_id, user_id, connection_id)

        if memory_types:
            q = q.filter(Memory.memory_type.in_(memory_types))

        all_authorized: List[Memory] = q.all()
        if not all_authorized:
            return [], []

        auto_historical = include_superseded or _is_historical_question(query)

        candidates: List[Memory] = []
        excluded: List[dict] = []

        for mem in all_authorized:
            if mem.status in _EXCLUDED_STATUSES:
                excluded.append({"memory_id": mem.id, "content": mem.content, "reason": f"status={mem.status}"})
                continue
            if mem.status == "pending" and only_approved:
                excluded.append({"memory_id": mem.id, "content": mem.content, "reason": "pending_approval"})
                continue
            if mem.status == "superseded" and not auto_historical:
                excluded.append({"memory_id": mem.id, "content": mem.content, "reason": "superseded"})
                continue
            if mem.valid_from and mem.valid_from > now:
                excluded.append({"memory_id": mem.id, "content": mem.content, "reason": "not_yet_valid"})
                continue
            if mem.valid_to and mem.valid_to < now and not (auto_historical and mem.status == "superseded"):
                excluded.append({"memory_id": mem.id, "content": mem.content, "reason": "expired_valid_to"})
                continue
            candidates.append(mem)

        if not candidates:
            return [], excluded

        query_embedding: List[float] = embedding_service.generate_embedding(query)
        query_tokens: Set[str] = _tokenize(query)

        w_v = settings.RETRIEVAL_WEIGHT_VECTOR
        w_k = settings.RETRIEVAL_WEIGHT_KEYWORD
        w_r = settings.RETRIEVAL_WEIGHT_RECENCY
        w_i = settings.RETRIEVAL_WEIGHT_IMPORTANCE
        w_t = settings.RETRIEVAL_WEIGHT_TYPE

        scored: List[dict] = []
        seen_contents: Set[str] = set()

        for mem in candidates:
            norm_content = (mem.content or "").strip().lower()
            if norm_content in seen_contents:
                excluded.append({"memory_id": mem.id, "content": mem.content, "reason": "duplicate_content"})
                continue
            seen_contents.add(norm_content)

            mem_embedding = _parse_embedding(mem.embedding)
            if query_embedding and mem_embedding:
                raw_sim = embedding_service.cosine_similarity(query_embedding, mem_embedding)
                # Normalize cosine similarity from [-1, 1] to [0, 1]
                v_score = max(0.0, min(1.0, (raw_sim + 1.0) / 2.0)) if raw_sim < 0 else min(1.0, raw_sim)
            else:
                v_score = 0.0

            content_text = mem.content or ""
            subject_text = mem.subject or ""
            content_tokens = _tokenize(f"{content_text} {subject_text}")
            k_score = _keyword_relevance(query_tokens, content_tokens)
            r_score = _recency_score(mem.created_at)
            i_score = _importance_score(mem.importance)
            t_score = _type_relevance(query, mem.memory_type)

            # When embedding vector is unavailable, redistribute vector weight to keyword so ranking stays meaningful
            if not query_embedding or not mem_embedding:
                effective_k_weight = w_v + w_k
                combined = (
                    effective_k_weight * k_score
                    + w_r * r_score
                    + w_i * i_score
                    + w_t * t_score
                )
            else:
                combined = (
                    w_v * v_score
                    + w_k * k_score
                    + w_r * r_score
                    + w_i * i_score
                    + w_t * t_score
                )

            scored.append({
                "memory_id": mem.id,
                "memory_type": mem.memory_type,
                "subject": subject_text,
                "content": content_text,
                "scope": mem.scope,
                "visibility": mem.visibility,
                "status": mem.status,
                "confidence": mem.confidence,
                "importance": mem.importance,
                "organization_id": mem.organization_id,
                "user_id": mem.user_id,
                "connection_id": mem.connection_id,
                "schema_hash": mem.schema_hash,
                "source_message_id": mem.source_message_id,
                "source_conversation_id": mem.source_conversation_id,
                "created_at": mem.created_at,
                "updated_at": mem.updated_at,
                "valid_from": mem.valid_from,
                "valid_to": mem.valid_to,
                "supersedes_id": mem.supersedes_id,
                "vector_similarity": round(v_score, 4),
                "keyword_relevance": round(k_score, 4),
                "recency_score": round(r_score, 4),
                "importance_score": round(i_score, 4),
                "type_relevance": round(t_score, 4),
                "combined_score": round(combined, 4),
                "_model": mem,
            })

        scored.sort(key=lambda x: x["combined_score"], reverse=True)
        selected = scored[:limit]
        for overflow in scored[limit:]:
            excluded.append({
                "memory_id": overflow["memory_id"],
                "content": overflow["content"],
                "reason": "below_top_k_cutoff",
                "combined_score": overflow["combined_score"],
            })

        return selected, excluded

    def search_memories(
        self,
        query: str,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
        memory_types: Optional[List[str]] = None,
        limit: int = 10,
        include_superseded: bool = False,
        only_approved: bool = True,
    ) -> List[dict]:
        """Search memories using a multi-signal ranking function."""
        selected, _ = self.search_memories_with_diagnostics(
            query=query,
            organization_id=organization_id,
            user_id=user_id,
            connection_id=connection_id,
            memory_types=memory_types,
            limit=limit,
            include_superseded=include_superseded,
            only_approved=only_approved,
        )
        return selected

    def detect_duplicates(
        self,
        content: str,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
    ) -> List[Memory]:
        """Find existing memories whose content is exact or semantically near-identical."""
        if not content or not content.strip():
            return []

        norm_target = content.strip().lower()
        q = self.db.query(Memory).filter(
            Memory.organization_id == organization_id,
            Memory.user_id == user_id,
            ~Memory.status.in_(_EXCLUDED_STATUSES),
        )
        if connection_id is not None:
            q = q.filter(Memory.connection_id == connection_id)

        candidates: List[Memory] = q.all()
        content_embedding = embedding_service.generate_embedding(content)

        duplicates: List[Memory] = []
        for mem in candidates:
            if (mem.content or "").strip().lower() == norm_target:
                duplicates.append(mem)
                continue

            if content_embedding:
                mem_embedding = _parse_embedding(mem.embedding)
                if mem_embedding:
                    similarity = embedding_service.cosine_similarity(content_embedding, mem_embedding)
                    if similarity >= _DUPLICATE_SIMILARITY_THRESHOLD:
                        duplicates.append(mem)

        return duplicates

    def detect_contradictions(
        self,
        content: str,
        subject: str,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
    ) -> List[Memory]:
        """Find active/approved memories sharing the same subject with conflicting content."""
        if not content or not subject:
            return []

        q = self.db.query(Memory).filter(
            Memory.organization_id == organization_id,
            Memory.user_id == user_id,
            Memory.subject.ilike(subject),
            Memory.status.in_(["approved", "pending"]),
        )
        if connection_id is not None:
            q = q.filter(
                or_(Memory.connection_id == connection_id, Memory.connection_id.is_(None))
            )

        candidates: List[Memory] = q.all()
        content_embedding = embedding_service.generate_embedding(content)
        contradictions: List[Memory] = []

        for mem in candidates:
            mem_content = (mem.content or "").strip()
            if mem_content.lower() == content.strip().lower():
                continue

            if content_embedding:
                mem_embedding = _parse_embedding(mem.embedding)
                if mem_embedding:
                    similarity = embedding_service.cosine_similarity(content_embedding, mem_embedding)
                    if similarity < 0.85:
                        contradictions.append(mem)
                    continue

            contradictions.append(mem)

        return contradictions
