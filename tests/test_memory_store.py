import pytest
from fastapi import HTTPException
from app.services.memory_store import MemoryStore


def test_create_defaults_to_pending(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    memory = store.create(
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
    assert memory.id is not None
    assert memory.status == "pending"
    assert memory.embedding is not None
    assert len(store.versions_of(memory.id, sample_user.id, sample_org.id)) == 1
    audits = store.audit_trail(memory.id, sample_user.id, sample_org.id)
    assert any(a.action == "created" for a in audits)


def test_get_denies_other_org(db_session, sample_org, sample_user, other_org, other_user):
    store = MemoryStore(db_session)
    memory = store.create(
        data={"memory_type": "semantic", "content": "Active customers have status = 'active'."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    fetched = store.get(memory.id, sample_user.id, sample_org.id)
    assert fetched.id == memory.id

    with pytest.raises(HTTPException) as exc:
        store.get(memory.id, other_user.id, other_org.id)
    assert exc.value.status_code == 403


def test_update_keeps_versions(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    memory = store.create(
        data={"memory_type": "semantic", "subject": "revenue", "content": "Revenue excludes cancelled orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    updated = store.update(
        memory_id=memory.id,
        updates={"content": "Revenue excludes both cancelled and refunded orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        reason="Updated policy",
    )
    assert "refunded" in updated.content
    versions = store.versions_of(memory.id, sample_user.id, sample_org.id)
    assert len(versions) == 2
    assert versions[0].updated_content == "Revenue excludes cancelled orders."
    assert versions[1].previous_content == "Revenue excludes cancelled orders."
    assert versions[1].updated_content == "Revenue excludes both cancelled and refunded orders."


def test_approve_reject_delete(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    m1 = store.create(
        data={"memory_type": "procedural", "content": "Use invoice_date for financial reports."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    approved = store.approve(m1.id, sample_user.id, sample_org.id)
    assert approved.status == "approved"

    m2 = store.create(
        data={"memory_type": "procedural", "content": "Always sort ascending by id."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    rejected = store.reject(m2.id, sample_user.id, sample_org.id)
    assert rejected.status == "rejected"

    assert store.delete(m1.id, sample_user.id, sample_org.id) is True
    with pytest.raises(HTTPException) as exc:
        store.get(m1.id, sample_user.id, sample_org.id)
    assert exc.value.status_code == 404


def test_supersede(db_session, sample_org, sample_user, sample_connection):
    store = MemoryStore(db_session)
    previous = store.create(
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
    replacement = store.supersede(
        old_memory_id=previous.id,
        new_memory_data={"content": "PayLens revenue reports now use MongoDB."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    db_session.refresh(previous)
    assert previous.status == "superseded"
    assert previous.valid_to is not None
    assert replacement.supersedes_id == previous.id
    assert replacement.status == "approved"


def test_list_and_statistics(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    store.create(
        data={"memory_type": "semantic", "content": "Rule one for revenue calculation"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    store.create(
        data={"memory_type": "procedural", "content": "Display monetary values in INR"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=False,
    )
    stats = store.stats(sample_org.id, sample_user.id)
    assert stats["total"] == 2
    assert stats["approved"] == 1
    assert stats["pending"] == 1
    assert stats["by_type"]["semantic"] == 1
    assert stats["by_type"]["procedural"] == 1
