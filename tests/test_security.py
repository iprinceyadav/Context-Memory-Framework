import pytest
from fastapi import HTTPException
from app.services.retrieval import MemoryRetriever
from app.services.memory_store import MemoryStore
from app.services.sql_guard import check_sql


def test_cross_tenant_isolation(db_session, sample_org, sample_user, other_org, other_user):
    store = MemoryStore(db_session)
    memory_a = store.create(
        data={"memory_type": "semantic", "content": "Secret Org A formula: revenue * 0.9", "visibility": "shared", "scope": "organization"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retriever = MemoryRetriever(db_session)
    assert len(retriever.search("Secret Org A formula", other_org.id, other_user.id)) == 0
    with pytest.raises(HTTPException):
        store.get(memory_a.id, other_user.id, other_org.id)


def test_cross_user_isolation(db_session, sample_org, sample_user, second_user):
    store = MemoryStore(db_session)
    memory = store.create(
        data={"memory_type": "procedural", "content": "User A1 personal preference: use INR.", "visibility": "personal"},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retriever = MemoryRetriever(db_session)
    assert len(retriever.search("INR preference", sample_org.id, second_user.id)) == 0

    with pytest.raises(HTTPException) as exc:
        store.update(memory.id, {"content": "Hacked"}, second_user.id, sample_org.id)
    assert exc.value.status_code == 403


def test_connection_isolation(db_session, sample_org, sample_user, sample_connection, second_connection):
    store = MemoryStore(db_session)
    store.create(
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
    retriever = MemoryRetriever(db_session)
    res_a = retriever.search("exclude test_accounts", sample_org.id, sample_user.id, connection_id=sample_connection.id)
    res_b = retriever.search("exclude test_accounts", sample_org.id, sample_user.id, connection_id=second_connection.id)
    assert len(res_a) == 1
    assert len(res_b) == 0


def test_viewer_cannot_share(client, sample_org, second_user):
    response = client.post(
        f"/api/memories/?organization_id={sample_org.id}&user_id={second_user.id}",
        json={
            "memory_type": "semantic",
            "content": "Viewer trying to publish org rule",
            "scope": "organization",
            "visibility": "shared",
        },
    )
    assert response.status_code == 403


def test_sql_guard_blocks_writes():
    is_valid, err, _ = check_sql("DROP TABLE invoices;", "sqlite")
    assert not is_valid
    assert "Security policy violation" in err

    is_valid2, err2, _ = check_sql("SELECT * FROM invoices; DELETE FROM customers;", "sqlite")
    assert not is_valid2

    is_valid3, err3, _ = check_sql(
        "SELECT * FROM secret_table;",
        "sqlite",
        schema_json_str='{"invoices": [{"name": "id", "type": "INT"}]}',
    )
    assert not is_valid3
    assert "not present in live schema" in err3


def test_delete_clears_embedding(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    memory = store.create(
        data={"memory_type": "semantic", "content": "Temporary rule to delete."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    assert memory.embedding is not None
    store.delete(memory.id, sample_user.id, sample_org.id)
    db_session.refresh(memory)
    assert memory.status == "deleted"
    assert memory.embedding is None

    retriever = MemoryRetriever(db_session)
    assert retriever.search("Temporary rule to delete", sample_org.id, sample_user.id) == []
