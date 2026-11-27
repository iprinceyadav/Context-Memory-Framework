import pytest
from fastapi import HTTPException
from app.services.memory_service import MemoryService


def test_create_memory_defaults_to_pending(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    mem = svc.create_memory(
        data={
            "memory_type": "semantic",
            "subject": "revenue",
            "content": "Revenue excludes cancelled orders.",
            "scope": "connection",
            "visibility": "personal",
            "importance": 9,
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    assert mem.id is not None
    assert mem.status == "pending"
    assert mem.embedding is not None
    assert len(svc.get_versions(mem.id, sample_user.id, sample_org.id)) == 1
    audits = svc.get_audit_log(mem.id, sample_user.id, sample_org.id)
    assert any(a.action == "created" for a in audits)


def test_get_memory_and_cross_org_denied(test_db, sample_org, sample_user, other_org, other_user):
    svc = MemoryService(test_db)
    mem = svc.create_memory(
        data={"memory_type": "semantic", "content": "Active customers have status = 'active'."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    fetched = svc.get_memory(mem.id, sample_user.id, sample_org.id)
    assert fetched.id == mem.id

    with pytest.raises(HTTPException) as exc:
        svc.get_memory(mem.id, other_user.id, other_org.id)
    assert exc.value.status_code == 403


def test_update_memory_preserves_version_history(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    mem = svc.create_memory(
        data={"memory_type": "semantic", "subject": "revenue", "content": "Revenue excludes cancelled orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    updated = svc.update_memory(
        memory_id=mem.id,
        updates={"content": "Revenue excludes both cancelled and refunded orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        reason="Updated policy",
    )
    assert "refunded" in updated.content
    versions = svc.get_versions(mem.id, sample_user.id, sample_org.id)
    assert len(versions) == 2
    assert versions[0].updated_content == "Revenue excludes cancelled orders."
    assert versions[1].previous_content == "Revenue excludes cancelled orders."
    assert versions[1].updated_content == "Revenue excludes both cancelled and refunded orders."


def test_approve_reject_and_delete_memory(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    m1 = svc.create_memory(
        data={"memory_type": "procedural", "content": "Use invoice_date for financial reports."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    approved = svc.approve_memory(m1.id, sample_user.id, sample_org.id)
    assert approved.status == "approved"

    m2 = svc.create_memory(
        data={"memory_type": "procedural", "content": "Always sort ascending by id."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    rejected = svc.reject_memory(m2.id, sample_user.id, sample_org.id)
    assert rejected.status == "rejected"

    assert svc.delete_memory(m1.id, sample_user.id, sample_org.id) is True
    with pytest.raises(HTTPException) as exc:
        svc.get_memory(m1.id, sample_user.id, sample_org.id)
    assert exc.value.status_code == 404


def test_supersede_memory(test_db, sample_org, sample_user, sample_connection):
    svc = MemoryService(test_db)
    old_mem = svc.create_memory(
        data={
            "memory_type": "semantic",
            "subject": "paylens_db",
            "content": "PayLens revenue reports use PostgreSQL.",
            "connection_id": sample_connection.id,
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    new_mem = svc.supersede_memory(
        old_memory_id=old_mem.id,
        new_memory_data={"content": "PayLens revenue reports now use MongoDB."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    test_db.refresh(old_mem)
    assert old_mem.status == "superseded"
    assert old_mem.valid_to is not None
    assert new_mem.supersedes_id == old_mem.id
    assert new_mem.status == "approved"


def test_list_and_statistics(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    svc.create_memory(
        data={"memory_type": "semantic", "content": "Rule one for revenue calculation"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    svc.create_memory(
        data={"memory_type": "procedural", "content": "Display monetary values in INR"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=False,
    )
    stats = svc.get_statistics(sample_org.id, sample_user.id)
    assert stats["total"] == 2
    assert stats["approved"] == 1
    assert stats["pending"] == 1
    assert stats["by_type"]["semantic"] == 1
    assert stats["by_type"]["procedural"] == 1

