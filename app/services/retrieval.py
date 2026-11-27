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
from app.services.embedder import embedder

logger = logging.getLogger(__name__)

_HALF_LIFE_DAYS: float = 30.0
_DUPLICATE_THRESHOLD: float = 0.96

_STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "is", "it", "by", "with", "as", "was", "that", "this", "are",
    "be", "has", "had", "have", "not", "from", "will", "can", "do", "does",
    "did", "been", "if", "so", "no", "what", "when", "where", "which",
    "who", "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "just", "about", "show",
    "me", "my", "get", "give", "list",
}

_INTENT_TYPES: Dict[str, List[str]] = {
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

_INACTIVE_STATUSES: Set[str] = {"rejected", "deleted", "expired"}


def _tokenize(text: str) -> Set[str]:
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    return words - _STOP_WORDS


def _load_vector(raw: Optional[str]) -> List[float]:
    if not raw:
        return []
    try:
        vec = json.loads(raw)
        if isinstance(vec, list):
            return vec
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _recency(created_at: Optional[datetime]) -> float:
    if created_at is None:
        return 0.5

    now = datetime.utcnow()
    if created_at.tzinfo is not None:
        now = datetime.now(timezone.utc)

    age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return math.pow(0.5, age_days / _HALF_LIFE_DAYS)


def _importance(importance: Optional[int]) -> float:
    if importance is None:
        return 0.5
    clamped = max(1, min(int(importance), 10))
    return (clamped - 1) / 9.0


def _keyword_overlap(query_tokens: Set[str], content_tokens: Set[str]) -> float:
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = query_tokens & content_tokens
    return len(overlap) / len(query_tokens)


def _type_match(query_text: str, memory_type: Optional[str]) -> float:
    if not memory_type:
        return 0.5

    query_lower = query_text.lower()
    memory_type_lower = memory_type.lower()

    matched_any = False
    for keyword, relevant_types in _INTENT_TYPES.items():
        if keyword in query_lower:
            matched_any = True
            for candidate_type in relevant_types:
                if candidate_type in memory_type_lower or memory_type_lower in candidate_type:
                    return 1.0
    if matched_any:
        return 0.4
    return 0.6


def _asks_history(query_text: str) -> bool:
    memories_query = query_text.lower()
    markers = ("previously", "historically", "used to", "old definition", "past", "before migration", "was previously")
    return any(m in memories_query for m in markers)


class MemoryRetriever:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _authorize(
        self,
        memories_query,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
    ):
        memories_query = memories_query.filter(Memory.organization_id == organization_id)

        memories_query = memories_query.filter(
            or_(
                Memory.user_id == user_id,
                Memory.visibility == "shared",
            )
        )

        if connection_id is not None:
            memories_query = memories_query.filter(
                or_(
                    Memory.connection_id == connection_id,
                    (Memory.connection_id.is_(None)) & (Memory.scope != "connection"),
                )
            )
        else:
            memories_query = memories_query.filter(Memory.scope != "connection")

        return memories_query

    def rank(
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
        if not query or not query.strip():
            return [], []

        now = datetime.utcnow()

        memories_query = self.db.query(Memory)
        memories_query = self._authorize(memories_query, organization_id, user_id, connection_id)

        if memory_types:
            memories_query = memories_query.filter(Memory.memory_type.in_(memory_types))

        all_authorized: List[Memory] = memories_query.all()
        if not all_authorized:
            return [], []

        auto_historical = include_superseded or _asks_history(query)

        candidates: List[Memory] = []
        excluded: List[dict] = []

        for memory in all_authorized:
            if memory.status in _INACTIVE_STATUSES:
                excluded.append({"memory_id": memory.id, "content": memory.content, "reason": f"status={memory.status}"})
                continue
            if memory.status == "pending" and only_approved:
                excluded.append({"memory_id": memory.id, "content": memory.content, "reason": "pending_approval"})
                continue
            if memory.status == "superseded" and not auto_historical:
                excluded.append({"memory_id": memory.id, "content": memory.content, "reason": "superseded"})
                continue
            if memory.valid_from and memory.valid_from > now:
                excluded.append({"memory_id": memory.id, "content": memory.content, "reason": "not_yet_valid"})
                continue
            if memory.valid_to and memory.valid_to < now and not (auto_historical and memory.status == "superseded"):
                excluded.append({"memory_id": memory.id, "content": memory.content, "reason": "expired_valid_to"})
                continue
            candidates.append(memory)

        if not candidates:
            return [], excluded

        query_embedding: List[float] = embedder.embed(query)
        query_tokens: Set[str] = _tokenize(query)

        vector_weight = settings.RETRIEVAL_WEIGHT_VECTOR
        keyword_weight = settings.RETRIEVAL_WEIGHT_KEYWORD
        recency_weight = settings.RETRIEVAL_WEIGHT_RECENCY
        importance_weight = settings.RETRIEVAL_WEIGHT_IMPORTANCE
        type_weight = settings.RETRIEVAL_WEIGHT_TYPE

        scored: List[dict] = []
        seen_contents: Set[str] = set()

        for memory in candidates:
            normalized = (memory.content or "").strip().lower()
            if normalized in seen_contents:
                excluded.append({"memory_id": memory.id, "content": memory.content, "reason": "duplicate_content"})
                continue
            seen_contents.add(normalized)

            memory_vector = _load_vector(memory.embedding)
            if query_embedding and memory_vector:
                cosine = embedder.cosine_similarity(query_embedding, memory_vector)
                vector_score = max(0.0, min(1.0, (cosine + 1.0) / 2.0)) if cosine < 0 else min(1.0, cosine)
            else:
                vector_score = 0.0

            content_text = memory.content or ""
            subject_text = memory.subject or ""
            content_tokens = _tokenize(f"{content_text} {subject_text}")
            keyword_score = _keyword_overlap(query_tokens, content_tokens)
            recency_score = _recency(memory.created_at)
            importance_score = _importance(memory.importance)
            type_score = _type_match(query, memory.memory_type)

            if not query_embedding or not memory_vector:
                keyword_weight_fallback = vector_weight + keyword_weight
                combined = (
                    keyword_weight_fallback * keyword_score
                    + recency_weight * recency_score
                    + importance_weight * importance_score
                    + type_weight * type_score
                )
            else:
                combined = (
                    vector_weight * vector_score
                    + keyword_weight * keyword_score
                    + recency_weight * recency_score
                    + importance_weight * importance_score
                    + type_weight * type_score
                )

            scored.append({
                "memory_id": memory.id,
                "memory_type": memory.memory_type,
                "subject": subject_text,
                "content": content_text,
                "scope": memory.scope,
                "visibility": memory.visibility,
                "status": memory.status,
                "confidence": memory.confidence,
                "importance": memory.importance,
                "organization_id": memory.organization_id,
                "user_id": memory.user_id,
                "connection_id": memory.connection_id,
                "schema_hash": memory.schema_hash,
                "source_message_id": memory.source_message_id,
                "source_conversation_id": memory.source_conversation_id,
                "created_at": memory.created_at,
                "updated_at": memory.updated_at,
                "valid_from": memory.valid_from,
                "valid_to": memory.valid_to,
                "supersedes_id": memory.supersedes_id,
                "vector_similarity": round(vector_score, 4),
                "keyword_relevance": round(keyword_score, 4),
                "recency_score": round(recency_score, 4),
                "importance_score": round(importance_score, 4),
                "type_relevance": round(type_score, 4),
                "combined_score": round(combined, 4),
                "_model": memory,
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

    def search(
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
        selected, _ = self.rank(
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

    def find_duplicates(
        self,
        content: str,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
    ) -> List[Memory]:
        if not content or not content.strip():
            return []

        normalized = content.strip().lower()
        memories_query = self.db.query(Memory).filter(
            Memory.organization_id == organization_id,
            Memory.user_id == user_id,
            ~Memory.status.in_(_INACTIVE_STATUSES),
        )
        if connection_id is not None:
            memories_query = memories_query.filter(Memory.connection_id == connection_id)

        candidates: List[Memory] = memories_query.all()
        content_embedding = embedder.embed(content)

        duplicates: List[Memory] = []
        for memory in candidates:
            if (memory.content or "").strip().lower() == normalized:
                duplicates.append(memory)
                continue

            if content_embedding:
                memory_vector = _load_vector(memory.embedding)
                if memory_vector:
                    similarity = embedder.cosine_similarity(content_embedding, memory_vector)
                    if similarity >= _DUPLICATE_THRESHOLD:
                        duplicates.append(memory)

        return duplicates

    def find_conflicts(
        self,
        content: str,
        subject: str,
        organization_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
    ) -> List[Memory]:
        if not content or not subject:
            return []

        memories_query = self.db.query(Memory).filter(
            Memory.organization_id == organization_id,
            Memory.user_id == user_id,
            Memory.subject.ilike(subject),
            Memory.status.in_(["approved", "pending"]),
        )
        if connection_id is not None:
            memories_query = memories_query.filter(
                or_(Memory.connection_id == connection_id, Memory.connection_id.is_(None))
            )

        candidates: List[Memory] = memories_query.all()
        content_embedding = embedder.embed(content)
        contradictions: List[Memory] = []

        for memory in candidates:
            memory_text = (memory.content or "").strip()
            if memory_text.lower() == content.strip().lower():
                continue

            if content_embedding:
                memory_vector = _load_vector(memory.embedding)
                if memory_vector:
                    similarity = embedder.cosine_similarity(content_embedding, memory_vector)
                    if similarity < 0.85:
                        contradictions.append(memory)
                    continue

            contradictions.append(memory)

        return contradictions
