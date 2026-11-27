from datetime import datetime, timedelta
from app.services.memory_retrieval import MemoryRetrievalService
from app.services.memory_service import MemoryService


def test_search_returns_relevant_approved_memories_only(test_db, sample_org, sample_user, sample_connection):
    svc = MemoryService(test_db)
    svc.create_memory(
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
    svc.create_memory(
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

    retrieval = MemoryRetrievalService(test_db)
    results = retrieval.search_memories(
        query="Show monthly revenue",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    assert len(results) == 1
    assert "cancelled orders" in results[0]["content"]
    assert results[0]["combined_score"] > 0


def test_search_excludes_deleted_rejected_and_expired(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    m_del = svc.create_memory(
        data={"memory_type": "semantic", "content": "Deleted revenue metric rule."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    svc.delete_memory(m_del.id, sample_user.id, sample_org.id)

    m_rej = svc.create_memory(
        data={"memory_type": "semantic", "content": "Rejected revenue metric rule."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )
    svc.reject_memory(m_rej.id, sample_user.id, sample_org.id)

    svc.create_memory(
        data={
            "memory_type": "semantic",
            "content": "Expired revenue metric rule from 2020.",
            "valid_to": datetime.utcnow() - timedelta(days=1),
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )

    retrieval = MemoryRetrievalService(test_db)
    results = retrieval.search_memories(
        query="revenue metric rule",
        organization_id=sample_org.id,
        user_id=sample_user.id,
    )
    assert results == []


def test_temporal_superseded_recall(test_db, sample_org, sample_user, sample_connection):
    svc = MemoryService(test_db)
    old_mem = svc.create_memory(
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
    svc.supersede_memory(
        old_memory_id=old_mem.id,
        new_memory_data={"content": "PayLens revenue reports use MongoDB."},
        user_id=sample_user.id,
        org_id=sample_org.id,
    )

    retrieval = MemoryRetrievalService(test_db)
    current = retrieval.search_memories(
        query="Which database do PayLens revenue reports use?",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    assert len(current) == 1
    assert "MongoDB" in current[0]["content"]

    historical = retrieval.search_memories(
        query="What did PayLens revenue reports previously use?",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    assert len(historical) == 2
    assert any("PostgreSQL" in r["content"] for r in historical)


def test_duplicate_and_contradiction_detection(test_db, sample_org, sample_user):
    svc = MemoryService(test_db)
    svc.create_memory(
        data={"memory_type": "semantic", "subject": "revenue", "content": "Revenue excludes cancelled orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    retrieval = MemoryRetrievalService(test_db)
    dups = retrieval.detect_duplicates("Revenue excludes cancelled orders.", sample_org.id, sample_user.id)
    assert len(dups) == 1

    contras = retrieval.detect_contradictions(
        "Revenue includes all orders even if refunded.",
        "revenue",
        sample_org.id,
        sample_user.id,
    )
    assert len(contras) == 1

