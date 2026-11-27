import pytest
from fastapi import HTTPException
from app.services.memory_retrieval import MemoryRetrievalService
from app.services.memory_service import MemoryService
from app.services.sql_validator import validate_sql_safety


def test_cross_tenant_isolation(test_db, sample_org, sample_user, other_org, other_user):
    svc = MemoryService(test_db)
    mem_a = svc.create_memory(
        data={"memory_type": "semantic", "content": "Secret Org A formula: revenue * 0.9", "visibility": "shared", "scope": "organization"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retrieval = MemoryRetrievalService(test_db)
    assert len(retrieval.search_memories("Secret Org A formula", other_org.id, other_user.id)) == 0
    with pytest.raises(HTTPException):
        svc.get_memory(mem_a.id, other_user.id, other_org.id)


def test_cross_user_personal_memory_isolation(test_db, sample_org, sample_user, second_user):
    svc = MemoryService(test_db)
    mem = svc.create_memory(
        data={"memory_type": "procedural", "content": "User A1 personal preference: use INR.", "visibility": "personal"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retrieval = MemoryRetrievalService(test_db)
    assert len(retrieval.search_memories("INR preference", sample_org.id, second_user.id)) == 0

    with pytest.raises(HTTPException) as exc:
        svc.update_memory(mem.id, {"content": "Hacked"}, second_user.id, sample_org.id)
    assert exc.value.status_code == 403


def test_database_connection_isolation(test_db, sample_org, sample_user, sample_connection, second_connection):
    svc = MemoryService(test_db)
    svc.create_memory(
        data={
            "memory_type": "semantic",
            "content": "Sales DB rule: exclude test_accounts.",
            "connection_id": sample_connection.id,
            "scope": "connection",
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retrieval = MemoryRetrievalService(test_db)
    res_a = retrieval.search_memories("exclude test_accounts", sample_org.id, sample_user.id, connection_id=sample_connection.id)
    res_b = retrieval.search_memories("exclude test_accounts", sample_org.id, sample_user.id, connection_id=second_connection.id)
    assert len(res_a) == 1
    assert len(res_b) == 0


def test_viewer_cannot_create_shared_org_memory(test_client, sample_org, second_user):
    resp = test_client.post(
        f"/api/memories/?organization_id={sample_org.id}&user_id={second_user.id}",
        json={
            "memory_type": "semantic",
            "content": "Viewer trying to publish org rule",
            "scope": "organization",
            "visibility": "shared",
        },
    )
    assert resp.status_code == 403


def test_sql_safety_blocks_destructive_and_injected_queries():
    is_valid, err, _ = validate_sql_safety("DROP TABLE invoices;", "sqlite")
    assert not is_valid
    assert "Security policy violation" in err

    is_valid2, err2, _ = validate_sql_safety("SELECT * FROM invoices; DELETE FROM customers;", "sqlite")
    assert not is_valid2

    is_valid3, err3, _ = validate_sql_safety(
        "SELECT * FROM secret_table;",
        "sqlite",
        schema_json_str='{"invoices": [{"name": "id", "type": "INT"}]}',
    )
    assert not is_valid3
    assert "not present in live schema" in err3


def test_deletion_clears_embedding_and_excludes_from_retrieval(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    mem = svc.create_memory(
        data={"memory_type": "semantic", "content": "Temporary rule to delete."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    assert mem.embedding is not None
    svc.delete_memory(mem.id, sample_user.id, sample_org.id)
    test_db.refresh(mem)
    assert mem.status == "deleted"
    assert mem.embedding is None

    retrieval = MemoryRetrievalService(test_db)
    assert retrieval.search_memories("Temporary rule to delete", sample_org.id, sample_user.id) == []

