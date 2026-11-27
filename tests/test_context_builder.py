import json
from app.models.conversation import Conversation, Message
from app.models.query_memory import QueryMemory
from app.services.context_builder import ContextBuilder
from app.services.memory_store import MemoryStore


def test_build_combines_all_sections(
    db_session, sample_org, sample_user, sample_connection
):
    store = MemoryStore(db_session)
    store.create(
        data={
            "memory_type": "semantic",
            "subject": "revenue",
            "content": "Revenue excludes cancelled orders.",
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
            "memory_type": "procedural",
            "subject": "date_column",
            "content": "Always use invoice_date for financial reports.",
            "connection_id": sample_connection.id,
            "scope": "connection",
            "importance": 8,
        },
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )

    conversation = Conversation(
        title="Revenue Analysis",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add(Message(conversation_id=conversation.id, role="user", content="Show top 10 customers by revenue"))
    db_session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content="Returned top 10 customers.",
            sql_query="SELECT c.name, SUM(o.amount) FROM customers c JOIN orders o ON c.id=o.customer_id GROUP BY c.name LIMIT 10",
        )
    )
    db_session.add(
        QueryMemory(
            organization_id=sample_org.id,
            user_id=sample_user.id,
            connection_id=sample_connection.id,
            conversation_id=conversation.id,
            natural_language_query="Show top 10 customers by revenue",
            normalized_intent="top_customers_by_revenue",
            sanitized_sql_template="SELECT c.name, SUM(o.amount) FROM customers c JOIN orders o ON c.id=o.customer_id GROUP BY c.name LIMIT 10",
            structured_query_metadata=json.dumps({"entities": ["customers", "orders"], "limit": 10}),
        )
    )
    db_session.commit()

    schema_json = json.dumps({
        "customers": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "TEXT"}, {"name": "city", "type": "TEXT"}],
        "orders": [{"name": "id", "type": "INTEGER"}, {"name": "customer_id", "type": "INTEGER"}, {"name": "amount", "type": "REAL"}, {"name": "status", "type": "TEXT"}, {"name": "invoice_date", "type": "TEXT"}],
    })

    builder = ContextBuilder(db_session)
    found = builder.build(
        natural_query="Now only show customers from Delhi for financial revenue report",
        schema_json_str=schema_json,
        db_type="sqlite",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        connection_id=sample_connection.id,
        conversation_id=conversation.id,
    )
    prompt = found["prompt"]
    assert "LIVE DATABASE SCHEMA" in prompt
    assert "RETRIEVED BUSINESS MEMORIES" in prompt
    assert "USER & DATABASE PREFERENCES" in prompt
    assert "RECENT CONVERSATION" in prompt
    assert "PREVIOUS ANALYTICAL CONTEXT" in prompt
    assert "Revenue excludes cancelled orders" in prompt
    assert "invoice_date" in prompt
    assert "UNTRUSTED CONTEXT DATA" in prompt
    assert len(found["memory_ids"]) == 2


def test_build_respects_token_budget(db_session, sample_org, sample_user):
    store = MemoryStore(db_session)
    topics = [
        "revenue", "expenses", "customers", "vendors", "invoices",
        "payments", "refunds", "discounts", "regions", "products",
    ]
    for i, topic in enumerate(topics):
        filler = " ".join(f"{topic}_detail_{j}" for j in range(45))
        store.create(
            data={"memory_type": "semantic", "content": f"Business definition for {topic}: {filler}"},
            user_id=sample_user.id,
            org_id=sample_org.id,
            auto_approve=True,
        )

    builder = ContextBuilder(db_session)
    found = builder.build(
        natural_query="business definition",
        schema_json_str=json.dumps({"orders": [{"name": "id", "type": "INT"}]}),
        db_type="sqlite",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        token_budget=500,
    )
    assert len(found["memory_ids"]) < 10
    assert len(found["context_debug"]["excluded_memories"]) > 0
