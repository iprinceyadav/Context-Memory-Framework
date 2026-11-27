from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.memory import Memory, MemoryAuditLog, MemoryVersion
from app.services.embedder import embedder
from app.services.indexer import sync_memory
from app.services.vector_store import MEMORIES, vector_store
from app.services.retrieval import MemoryRetriever

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.retriever = MemoryRetriever(db)

    def _audit(
        self,
        memory_id: str,
        action: str,
        performed_by: str,
        details: Optional[str] = None,
    ) -> None:
        entry = MemoryAuditLog(
            memory_id=memory_id,
            action=action,
            performed_by=performed_by,
            details=details,
        )
        self.db.add(entry)

    def _embed_json(self, content: str) -> Optional[str]:
        try:
            vec = embedder.embed(content)
            return json.dumps(vec) if vec else None
        except Exception as exc:
            logger.warning("Embedding generation failed during memory save: %s", exc)
            return None

    def _load_authorized(
        self,
        memory_id: str,
        user_id: str,
        organization_id: str,
        require_write: bool = False,
    ) -> Memory:
        memory = self.db.query(Memory).filter(Memory.id == memory_id).first()
        if not memory or memory.status == "deleted":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory not found",
            )

        if memory.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: memory belongs to another organization",
            )

        if require_write:
            if memory.user_id != user_id and memory.visibility != "shared":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: cannot modify another user's personal memory",
                )
        else:
            if memory.user_id != user_id and memory.visibility != "shared":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: cannot read another user's personal memory",
                )

        return memory

    def create(
        self,
        data: Dict[str, Any],
        user_id: str,
        org_id: str,
        initial_status: str = "pending",
        auto_approve: bool = False,
    ) -> Memory:
        content = (data.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Memory content cannot be empty")

        connection_id = data.get("connection_id")
        duplicates = self.retriever.find_duplicates(
            content=content,
            organization_id=org_id,
            user_id=user_id,
            connection_id=connection_id,
        )
        if duplicates:
            existing = duplicates[0]
            self._audit(existing.id, "retrieved", user_id, "Duplicate creation skipped; returned existing")
            self.db.commit()
            return existing

        final_status = "approved" if auto_approve else initial_status
        embedding_json = self._embed_json(content)

        memory = Memory(
            organization_id=org_id,
            user_id=user_id,
            connection_id=connection_id,
            memory_type=data.get("memory_type") or data.get("type") or "semantic",
            content=content,
            subject=data.get("subject"),
            scope=data.get("scope", "personal"),
            visibility=data.get("visibility", "personal"),
            confidence=float(data.get("confidence", 0.8)),
            importance=int(data.get("importance", 5)),
            source_message_id=data.get("source_message_id"),
            source_conversation_id=data.get("source_conversation_id"),
            schema_hash=data.get("schema_hash"),
            embedding=embedding_json,
            status=final_status,
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
            supersedes_id=data.get("supersedes_id"),
        )
        self.db.add(memory)
        self.db.flush()

        first_version = MemoryVersion(
            memory_id=memory.id,
            version_number=1,
            previous_content=None,
            updated_content=content,
            changed_by=user_id,
            change_reason="Initial creation",
        )
        self.db.add(first_version)
        self._audit(memory.id, "created", user_id, f"Created with status={final_status}")
        if final_status == "approved":
            self._audit(memory.id, "approved", user_id, "Auto-approved on creation")

        self.db.commit()
        self.db.refresh(memory)
        sync_memory(memory)
        return memory

    def get(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        return self._load_authorized(memory_id, user_id, org_id, require_write=False)

    def update(
        self,
        memory_id: str,
        updates: Dict[str, Any],
        user_id: str,
        org_id: str,
        reason: str = "User edit",
    ) -> Memory:
        memory = self._load_authorized(memory_id, user_id, org_id, require_write=True)
        previous_content = memory.content
        new_content = updates.get("content")

        if new_content is not None and new_content.strip() and new_content.strip() != previous_content:
            cleaned = new_content.strip()
            existing_versions = (
                self.db.query(MemoryVersion)
                .filter(MemoryVersion.memory_id == memory.id)
                .count()
            )
            version = MemoryVersion(
                memory_id=memory.id,
                version_number=existing_versions + 1,
                previous_content=previous_content,
                updated_content=cleaned,
                changed_by=user_id,
                change_reason=reason,
            )
            self.db.add(version)
            memory.content = cleaned
            memory.embedding = self._embed_json(cleaned)
            self._audit(memory.id, "corrected", user_id, f"Updated content (v{existing_versions + 1}): {reason}")

        for field in ("subject", "scope", "visibility", "confidence", "importance", "status", "valid_from", "valid_to"):
            if field in updates and updates[field] is not None:
                setattr(memory, field, updates[field])

        memory.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(memory)
        sync_memory(memory)
        return memory

    def delete(self, memory_id: str, user_id: str, org_id: str, hard_delete: bool = False) -> bool:
        memory = self._load_authorized(memory_id, user_id, org_id, require_write=True)

        if hard_delete:
            memory_id = memory.id
            self.db.delete(memory)
            self.db.commit()
            vector_store.delete(MEMORIES, [memory_id])
            return True

        memory.status = "deleted"
        memory.embedding = None
        memory.updated_at = datetime.utcnow()
        self._audit(memory.id, "deleted", user_id, "Memory deleted and embedding cleared")
        self.db.commit()
        sync_memory(memory)
        return True

    def approve(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        memory = self._load_authorized(memory_id, user_id, org_id, require_write=True)
        memory.status = "approved"
        if not memory.embedding:
            memory.embedding = self._embed_json(memory.content)
        memory.updated_at = datetime.utcnow()
        self._audit(memory.id, "approved", user_id, "Approved by user")
        self.db.commit()
        self.db.refresh(memory)
        sync_memory(memory)
        return memory

    def reject(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        memory = self._load_authorized(memory_id, user_id, org_id, require_write=True)
        memory.status = "rejected"
        memory.updated_at = datetime.utcnow()
        self._audit(memory.id, "rejected", user_id, "Rejected by user")
        self.db.commit()
        self.db.refresh(memory)
        sync_memory(memory)
        return memory

    def expire(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        memory = self._load_authorized(memory_id, user_id, org_id, require_write=True)
        memory.status = "expired"
        memory.valid_to = datetime.utcnow()
        memory.updated_at = datetime.utcnow()
        self._audit(memory.id, "expired", user_id, "Memory expired")
        self.db.commit()
        self.db.refresh(memory)
        sync_memory(memory)
        return memory

    def supersede(
        self,
        old_memory_id: str,
        new_memory_data: Dict[str, Any],
        user_id: str,
        org_id: str,
        auto_approve: bool = True,
    ) -> Memory:
        previous = self._load_authorized(old_memory_id, user_id, org_id, require_write=True)
        now = datetime.utcnow()
        previous.status = "superseded"
        previous.valid_to = now
        previous.updated_at = now
        self._audit(previous.id, "superseded", user_id, "Superseded by newer definition")

        new_payload = dict(new_memory_data)
        new_payload.setdefault("connection_id", previous.connection_id)
        new_payload.setdefault("subject", previous.subject)
        new_payload.setdefault("memory_type", previous.memory_type)
        new_payload.setdefault("scope", previous.scope)
        new_payload.setdefault("visibility", previous.visibility)
        new_payload["supersedes_id"] = previous.id
        new_payload["valid_from"] = now

        replacement = self.create(
            data=new_payload,
            user_id=user_id,
            org_id=org_id,
            initial_status="approved" if auto_approve else "pending",
            auto_approve=auto_approve,
        )
        self.db.refresh(previous)
        sync_memory(previous)
        return replacement

    def list_visible(
        self,
        org_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Memory]:
        memories_query = self.db.query(Memory).filter(
            Memory.organization_id == org_id,
            or_(Memory.user_id == user_id, Memory.visibility == "shared"),
            Memory.status != "deleted",
        )
        if connection_id is not None:
            memories_query = memories_query.filter(
                or_(Memory.connection_id == connection_id, Memory.connection_id.is_(None))
            )
        if memory_type:
            memories_query = memories_query.filter(Memory.memory_type == memory_type)
        if status_filter:
            memories_query = memories_query.filter(Memory.status == status_filter)

        return memories_query.order_by(Memory.updated_at.desc()).offset(skip).limit(limit).all()

    def stats(self, org_id: str, user_id: str) -> Dict[str, Any]:
        memories = (
            self.db.query(Memory)
            .filter(
                Memory.organization_id == org_id,
                or_(Memory.user_id == user_id, Memory.visibility == "shared"),
                Memory.status != "deleted",
            )
            .all()
        )
        total = len(memories)
        pending = sum(1 for m in memories if m.status == "pending")
        approved = sum(1 for m in memories if m.status == "approved")
        rejected = sum(1 for m in memories if m.status == "rejected")
        superseded = sum(1 for m in memories if m.status == "superseded")

        by_type: Dict[str, int] = {}
        for m in memories:
            by_type[m.memory_type] = by_type.get(m.memory_type, 0) + 1

        return {
            "total": total,
            "active": approved,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "superseded": superseded,
            "by_type": by_type,
        }

    def versions_of(self, memory_id: str, user_id: str, org_id: str) -> List[MemoryVersion]:
        self._load_authorized(memory_id, user_id, org_id, require_write=False)
        return (
            self.db.query(MemoryVersion)
            .filter(MemoryVersion.memory_id == memory_id)
            .order_by(MemoryVersion.version_number.asc())
            .all()
        )

    def audit_trail(self, memory_id: str, user_id: str, org_id: str) -> List[MemoryAuditLog]:
        self._load_authorized(memory_id, user_id, org_id, require_write=False)
        return (
            self.db.query(MemoryAuditLog)
            .filter(MemoryAuditLog.memory_id == memory_id)
            .order_by(MemoryAuditLog.created_at.desc())
            .all()
        )

    def ingest(
        self,
        extracted: List[Dict[str, Any]],
        user_id: str,
        org_id: str,
        connection_id: Optional[str] = None,
        source_conversation_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        schema_hash: Optional[str] = None,
    ) -> List[Memory]:
        created_memories: List[Memory] = []
        for item in extracted:
            content = (item.get("content") or "").strip()
            subject = item.get("subject")
            is_correction = bool(item.get("is_correction", False))

            payload = {
                "memory_type": item.get("type", "semantic"),
                "subject": subject,
                "content": content,
                "scope": item.get("scope", "connection" if connection_id else "personal"),
                "visibility": item.get("visibility", "personal"),
                "confidence": item.get("confidence", 0.8),
                "importance": item.get("importance", 5),
                "connection_id": connection_id,
                "source_conversation_id": source_conversation_id,
                "source_message_id": source_message_id,
                "schema_hash": schema_hash,
            }

            if is_correction and subject:
                contradictions = self.retriever.find_conflicts(
                    content=content,
                    subject=subject,
                    organization_id=org_id,
                    user_id=user_id,
                    connection_id=connection_id,
                )
                if contradictions:
                    previous = contradictions[0]
                    replacement = self.supersede(
                        old_memory_id=previous.id,
                        new_memory_data=payload,
                        user_id=user_id,
                        org_id=org_id,
                        auto_approve=False,
                    )
                    created_memories.append(replacement)
                    continue

            memory = self.create(
                data=payload,
                user_id=user_id,
                org_id=org_id,
                initial_status="pending",
                auto_approve=False,
            )
            created_memories.append(memory)

        return created_memories
