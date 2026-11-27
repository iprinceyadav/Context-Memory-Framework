from unittest.mock import patch

from app.database.connection import get_db
from app.main import app, sync_vector_index
from app.models.conversation import Conversation
from app.models.query_memory import QueryMemory
from app.services.context_builder import ContextBuilder
from app.services.indexer import index_schema
from app.services.memory_store import MemoryStore
from app.services.retrieval import MemoryRetriever
from app.services.text import follow_up_query, is_follow_up
from app.services.vector_store import MEMORIES, QUERY_EXAMPLES, vector_store
from tests.test_api import _sales_db


def test_follow_up_detection():
    assert is_follow_up("Now only Delhi customers")
    assert is_follow_up("What about last month?")
    assert is_follow_up("Sort those by amount")
    assert is_follow_up("Top 5")
    assert not is_follow_up("Show total revenue by city for paid orders")
    assert follow_up_query("Only Delhi", "Top customers by revenue") == "Top customers by revenue Only Delhi"
    assert follow_up_query("Only Delhi", None) == "Only Delhi"
    assert follow_up_query("Show paid revenue by city", "Top customers") == "Show paid revenue by city"


def test_follow_up_retrieval_uses_previous_question(db_session, sample_org, sample_user):
    conversation = Conversation(title="t", organization_id=sample_org.id, user_id=sample_user.id)
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        QueryMemory(
            organization_id=sample_org.id,
            user_id=sample_user.id,
            conversation_id=conversation.id,
            natural_language_query="Top customers by revenue",
            sanitized_sql_template="SELECT 1",
            execution_status="success",
        )
    )
    db_session.commit()

    context = ContextBuilder(db_session).build(
        natural_query="Only Delhi",
        schema_json_str='{"customers": [{"name": "city", "type": "TEXT"}]}',
        db_type="sqlite",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        conversation_id=conversation.id,
    )
    assert context["context_debug"]["rag"]["retrieval_query"] == "Top customers by revenue Only Delhi"
    assert "CURRENT QUESTION:\nOnly Delhi" in context["prompt"]


def test_large_schema_uses_vector_table_search(db_session, sample_org, sample_user):
    schema = {f"table_{i}": [{"name": "id", "type": "INT"}, {"name": f"field_{i}", "type": "TEXT"}] for i in range(15)}
    schema["vendor_payments"] = [
        {"name": "vendor_name", "type": "TEXT"},
        {"name": "outstanding_amount", "type": "REAL"},
    ]
    index_schema("schema-k", schema)

    context = ContextBuilder(db_session).build(
        natural_query="Which vendor has the highest outstanding amount?",
        schema_json_str=__import__("json").dumps(schema),
        db_type="sqlite",
        organization_id=sample_org.id,
        user_id=sample_user.id,
        schema_key="schema-k",
    )
    trace = context["context_debug"]["rag"]["schema"]
    assert trace["mode"].startswith("vector")
    assert context["schema_tables"][0] == "vendor_payments"


def _ask(client, db_path, question, user_id="default-user"):
    return client.post(
        "/api/chat/ask",
        json={
            "natural_query": question,
            "db_type": "sqlite",
            "database_name": str(db_path),
            "organization_id": "default-org",
            "user_id": user_id,
        },
    )


def test_answered_questions_become_examples(client, tmp_path):
    external_db = tmp_path / "sales_rag.db"
    _sales_db(external_db)
    prompts = []

    def fake_generate(prompt: str) -> str:
        if "Context Memory Extraction" in prompt:
            return '{"memories":[]}'
        prompts.append(prompt)
        return "SELECT SUM(amount) AS revenue FROM orders WHERE status = 'paid'"

    with patch("app.services.llm.llm.generate", side_effect=fake_generate), patch(
        "app.services.llm.llm.summarize", return_value="Done."
    ):
        first = _ask(client, external_db, "Show total revenue from paid orders")
        assert first.status_code == 200
        assert vector_store.count(QUERY_EXAMPLES) == 1

        second = _ask(client, external_db, "What is the total revenue?")
        assert second.status_code == 200

    assert "SIMILAR PAST QUESTIONS" not in prompts[0]
    assert "SIMILAR PAST QUESTIONS" in prompts[1]
    assert "Q: Show total revenue from paid orders" in prompts[1]
    assert "status = '?'" in prompts[1]
    examples = second.json()["context_debug"]["rag"]["examples"]
    assert examples and examples[0]["question"] == "Show total revenue from paid orders"


def test_examples_are_not_shared_between_users(client, db_session, tmp_path):
    from app.models.user import User

    external_db = tmp_path / "sales_iso.db"
    _sales_db(external_db)
    prompts = []

    def fake_generate(prompt: str) -> str:
        if "Context Memory Extraction" in prompt:
            return '{"memories":[]}'
        prompts.append(prompt)
        return "SELECT SUM(amount) AS revenue FROM orders"

    with patch("app.services.llm.llm.generate", side_effect=fake_generate), patch(
        "app.services.llm.llm.summarize", return_value="Done."
    ):
        assert _ask(client, external_db, "Show total revenue").status_code == 200
        db_session.add(
            User(
                id="other-user",
                email="other@default.local",
                full_name="Other",
                hashed_password="h",
                role="analyst",
                organization_id="default-org",
            )
        )
        db_session.commit()
        assert _ask(client, external_db, "Show total revenue", user_id="other-user").status_code == 200

    assert "SIMILAR PAST QUESTIONS" not in prompts[1]


def test_startup_rebuilds_stale_index(db_session, sample_org, sample_user):
    MemoryStore(db_session).create(
        data={"memory_type": "semantic", "subject": "revenue", "content": "Revenue excludes cancelled orders."},
        user_id=sample_user.id,
        org_id=sample_org.id,
        auto_approve=True,
    )
    vector_store.reset(MEMORIES)
    assert not vector_store.count(MEMORIES)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        sync_vector_index(app)
    finally:
        app.dependency_overrides.clear()

    assert vector_store.count(MEMORIES) == 1
    retriever = MemoryRetriever(db_session)
    found = retriever.search("revenue cancelled", sample_org.id, sample_user.id)
    assert retriever.trace["mode"] == "vector"
    assert found and found[0]["content"] == "Revenue excludes cancelled orders."
