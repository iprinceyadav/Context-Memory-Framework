"""
Memory Lifecycle Service for the Context Memory Framework.

Handles CRUD, approval/rejection, versioning on updates, superseding,
expiration, deletion, audit logging, and post-extraction ingestion.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.memory import Memory, MemoryAuditLog, MemoryVersion
from app.services.embedding_service import embedding_service
from app.services.memory_retrieval import MemoryRetrievalService

logger = logging.getLogger(__name__)


class MemoryService:
    """Manages persistent memory records and their full lifecycle."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.retrieval = MemoryRetrievalService(db)

    def _log_audit(
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

    def _safe_generate_embedding_json(self, content: str) -> Optional[str]:
        try:
            vec = embedding_service.generate_embedding(content)
            return json.dumps(vec) if vec else None
        except Exception as exc:
            logger.warning("Embedding generation failed during memory save: %s", exc)
            return None

    def _get_authorized_memory(
        self,
        memory_id: str,
        user_id: str,
        organization_id: str,
        require_write: bool = False,
    ) -> Memory:
        mem = self.db.query(Memory).filter(Memory.id == memory_id).first()
        if not mem or mem.status == "deleted":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory not found",
            )

        if mem.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: memory belongs to another organization",
            )

        if require_write:
            if mem.user_id != user_id and mem.visibility != "shared":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: cannot modify another user's personal memory",
                )
        else:
            if mem.user_id != user_id and mem.visibility != "shared":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: cannot read another user's personal memory",
                )

        return mem

    def create_memory(
        self,
        data: Dict[str, Any],
        user_id: str,
        org_id: str,
        initial_status: str = "pending",
        auto_approve: bool = False,
    ) -> Memory:
        """Create a validated memory, compute embedding if approved/ready, and log audit."""
        content = (data.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Memory content cannot be empty")

        connection_id = data.get("connection_id")
        duplicates = self.retrieval.detect_duplicates(
            content=content,
            organization_id=org_id,
            user_id=user_id,
            connection_id=connection_id,
        )
        if duplicates:
            existing = duplicates[0]
            self._log_audit(existing.id, "retrieved", user_id, "Duplicate creation skipped; returned existing")
            self.db.commit()
            return existing

        final_status = "approved" if auto_approve else initial_status
        embedding_json = self._safe_generate_embedding_json(content)

        mem = Memory(
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
        self.db.add(mem)
        self.db.flush()

        # Initial version record
        v1 = MemoryVersion(
            memory_id=mem.id,
            version_number=1,
            previous_content=None,
            updated_content=content,
            changed_by=user_id,
            change_reason="Initial creation",
        )
        self.db.add(v1)
        self._log_audit(mem.id, "created", user_id, f"Created with status={final_status}")
        if final_status == "approved":
            self._log_audit(mem.id, "approved", user_id, "Auto-approved on creation")

        self.db.commit()
        self.db.refresh(mem)
        return mem

    def get_memory(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        return self._get_authorized_memory(memory_id, user_id, org_id, require_write=False)

    def update_memory(
        self,
        memory_id: str,
        updates: Dict[str, Any],
        user_id: str,
        org_id: str,
        reason: str = "User edit",
    ) -> Memory:
        """Update a memory while preserving previous versions in MemoryVersion."""
        mem = self._get_authorized_memory(memory_id, user_id, org_id, require_write=True)
        previous_content = mem.content
        new_content = updates.get("content")

        if new_content is not None and new_content.strip() and new_content.strip() != previous_content:
            clean_new = new_content.strip()
            existing_versions = (
                self.db.query(MemoryVersion)
                .filter(MemoryVersion.memory_id == mem.id)
                .count()
            )
            version = MemoryVersion(
                memory_id=mem.id,
                version_number=existing_versions + 1,
                previous_content=previous_content,
                updated_content=clean_new,
                changed_by=user_id,
                change_reason=reason,
            )
            self.db.add(version)
            mem.content = clean_new
            mem.embedding = self._safe_generate_embedding_json(clean_new)
            self._log_audit(mem.id, "corrected", user_id, f"Updated content (v{existing_versions + 1}): {reason}")

        for field in ("subject", "scope", "visibility", "confidence", "importance", "status", "valid_from", "valid_to"):
            if field in updates and updates[field] is not None:
                setattr(mem, field, updates[field])

        mem.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(mem)
        return mem

    def delete_memory(self, memory_id: str, user_id: str, org_id: str, hard_delete: bool = False) -> bool:
        """Delete memory and remove derived embeddings. Supports soft delete (default) or hard delete."""
        mem = self._get_authorized_memory(memory_id, user_id, org_id, require_write=True)

        if hard_delete:
            self.db.delete(mem)
            self.db.commit()
            return True

        mem.status = "deleted"
        mem.embedding = None  # Remove derived embedding on deletion
        mem.updated_at = datetime.utcnow()
        self._log_audit(mem.id, "deleted", user_id, "Memory deleted and embedding cleared")
        self.db.commit()
        return True

    def approve_memory(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        """Approve a pending memory and ensure its embedding is generated."""
        mem = self._get_authorized_memory(memory_id, user_id, org_id, require_write=True)
        mem.status = "approved"
        if not mem.embedding:
            mem.embedding = self._safe_generate_embedding_json(mem.content)
        mem.updated_at = datetime.utcnow()
        self._log_audit(mem.id, "approved", user_id, "Approved by user")
        self.db.commit()
        self.db.refresh(mem)
        return mem

    def reject_memory(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        """Reject a pending memory so it is excluded from retrieval."""
        mem = self._get_authorized_memory(memory_id, user_id, org_id, require_write=True)
        mem.status = "rejected"
        mem.updated_at = datetime.utcnow()
        self._log_audit(mem.id, "rejected", user_id, "Rejected by user")
        self.db.commit()
        self.db.refresh(mem)
        return mem

    def expire_memory(self, memory_id: str, user_id: str, org_id: str) -> Memory:
        """Mark a memory as expired."""
        mem = self._get_authorized_memory(memory_id, user_id, org_id, require_write=True)
        mem.status = "expired"
        mem.valid_to = datetime.utcnow()
        mem.updated_at = datetime.utcnow()
        self._log_audit(mem.id, "expired", user_id, "Memory expired")
        self.db.commit()
        self.db.refresh(mem)
        return mem

    def supersede_memory(
        self,
        old_memory_id: str,
        new_memory_data: Dict[str, Any],
        user_id: str,
        org_id: str,
        auto_approve: bool = True,
    ) -> Memory:
        """Mark an existing memory as superseded and create the replacement memory."""
        old_mem = self._get_authorized_memory(old_memory_id, user_id, org_id, require_write=True)
        now = datetime.utcnow()
        old_mem.status = "superseded"
        old_mem.valid_to = now
        old_mem.updated_at = now
        self._log_audit(old_mem.id, "superseded", user_id, "Superseded by newer definition")

        new_payload = dict(new_memory_data)
        new_payload.setdefault("connection_id", old_mem.connection_id)
        new_payload.setdefault("subject", old_mem.subject)
        new_payload.setdefault("memory_type", old_mem.memory_type)
        new_payload.setdefault("scope", old_mem.scope)
        new_payload.setdefault("visibility", old_mem.visibility)
        new_payload["supersedes_id"] = old_mem.id
        new_payload["valid_from"] = now

        new_mem = self.create_memory(
            data=new_payload,
            user_id=user_id,
            org_id=org_id,
            initial_status="approved" if auto_approve else "pending",
            auto_approve=auto_approve,
        )
        return new_mem

    def list_memories(
        self,
        org_id: str,
        user_id: str,
        connection_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Memory]:
        """List authorized memories for a user within an organization."""
        q = self.db.query(Memory).filter(
            Memory.organization_id == org_id,
            or_(Memory.user_id == user_id, Memory.visibility == "shared"),
            Memory.status != "deleted",
        )
        if connection_id is not None:
            q = q.filter(
                or_(Memory.connection_id == connection_id, Memory.connection_id.is_(None))
            )
        if memory_type:
            q = q.filter(Memory.memory_type == memory_type)
        if status_filter:
            q = q.filter(Memory.status == status_filter)

        return q.order_by(Memory.updated_at.desc()).offset(skip).limit(limit).all()

    def get_statistics(self, org_id: str, user_id: str) -> Dict[str, Any]:
        """Compute memory counts by status and type for dashboard display."""
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

    def get_versions(self, memory_id: str, user_id: str, org_id: str) -> List[MemoryVersion]:
        self._get_authorized_memory(memory_id, user_id, org_id, require_write=False)
        return (
            self.db.query(MemoryVersion)
            .filter(MemoryVersion.memory_id == memory_id)
            .order_by(MemoryVersion.version_number.asc())
            .all()
        )

    def get_audit_log(self, memory_id: str, user_id: str, org_id: str) -> List[MemoryAuditLog]:
        self._get_authorized_memory(memory_id, user_id, org_id, require_write=False)
        return (
            self.db.query(MemoryAuditLog)
            .filter(MemoryAuditLog.memory_id == memory_id)
            .order_by(MemoryAuditLog.created_at.desc())
            .all()
        )

    def process_extracted_memories(
        self,
        extracted: List[Dict[str, Any]],
        user_id: str,
        org_id: str,
        connection_id: Optional[str] = None,
        source_conversation_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        schema_hash: Optional[str] = None,
    ) -> List[Memory]:
        """Ingest extracted memory candidates, handling duplicates & corrections."""
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

            # Check contradictions when a permanent correction is detected
            if is_correction and subject:
                contradictions = self.retrieval.detect_contradictions(
                    content=content,
                    subject=subject,
                    organization_id=org_id,
                    user_id=user_id,
                    connection_id=connection_id,
                )
                if contradictions:
                    old_mem = contradictions[0]
                    # Create new pending/approved memory linked to old_mem if not auto-approved
                    new_mem = self.supersede_memory(
                        old_memory_id=old_mem.id,
                        new_memory_data=payload,
                        user_id=user_id,
                        org_id=org_id,
                        auto_approve=False,
                    )
                    created_memories.append(new_mem)
                    continue

            mem = self.create_memory(
                data=payload,
                user_id=user_id,
                org_id=org_id,
                initial_status="pending",
                auto_approve=False,
            )
            created_memories.append(mem)

        return created_memories

