from datetime import datetime, timedelta
from app.services.retrieval import MemoryRetriever
from app.services.memory_store import MemoryStore


def test_search_returns_approved_only(db_session, sample_org, sample_user, sample_connection):
    store = MemoryStore(db_session)
    store.create(
        data={
            "memory_type": "semantic",
            "subject": "revenue",
            "content": "Revenue excludes cancelled orders from total invoice amounts.",
            "connection_id": sample_connection.id,
            "scope": "connection",
            "importance": 9,
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    store.create(
        data={
            "memory_type": "semantic",
            "subject": "revenue_pending",
            "content": "Unverified pending revenue rule should not be auto-applied.",
            "connection_id": sample_connection.id,
            "scope": "connection",
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=False,
    )

    retriever = MemoryRetriever(db_session)
    results = retriever.search(
        query="Show monthly revenue",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    assert len(results) == 1
    assert "cancelled orders" in results[0]["content"]
    assert results[0]["combined_score"] > 0


def test_search_excludes_inactive(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    m_del = store.create(
        data={"memory_type": "semantic", "content": "Deleted revenue metric rule."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    store.delete(m_del.id, sample_user.id, sample_org.id)

    m_rej = store.create(
        data={"memory_type": "semantic", "content": "Rejected revenue metric rule."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    store.reject(m_rej.id, sample_user.id, sample_org.id)

    store.create(
        data={
            "memory_type": "semantic",
            "content": "Expired revenue metric rule from 2020.",
            "valid_to": datetime.utcnow() - timedelta(days=1),
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )

    retriever = MemoryRetriever(db_session)
    results = retriever.search(
        query="revenue metric rule",
        organization_id=sample_org.id,
        user_id=sample_user.id,
    )
    assert results == []


def test_superseded_recall(db_session, sample_org, sample_user, sample_connection):
    store = MemoryStore(db_session)
    previous = store.create(
        data={
            "memory_type": "semantic",
            "subject": "paylens",
            "content": "PayLens revenue reports use PostgreSQL.",
            "connection_id": sample_connection.id,
            "scope": "connection",
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    store.supersede(
        old_memory_id=previous.id,
        new_memory_data={"content": "PayLens revenue reports use MongoDB."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )

    retriever = MemoryRetriever(db_session)
    current = retriever.search(
        query="Which database do PayLens revenue reports use?",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    assert len(current) == 1
    assert "MongoDB" in current[0]["content"]

    historical = retriever.search(
        query="What did PayLens revenue reports previously use?",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    assert len(historical) == 2
    assert any("PostgreSQL" in r["content"] for r in historical)


def test_duplicates_and_conflicts(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    store.create(
        data={"memory_type": "semantic", "subject": "revenue", "content": "Revenue excludes cancelled orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retriever = MemoryRetriever(db_session)
    duplicates = retriever.find_duplicates("Revenue excludes cancelled orders.", sample_org.id, sample_user.id)
    assert len(duplicates) == 1

    conflicts = retriever.find_conflicts(
        "Revenue includes all orders even if refunded.",
        "revenue",
        sample_org.id,
        sample_user.id,
    )
    assert len(conflicts) == 1
