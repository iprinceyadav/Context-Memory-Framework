import sqlite3
from unittest.mock import patch
from app.services.embedding_service import EmbeddingProvider, embedding_service
from app.services.memory_extraction import MemoryExtractionService


class FailingEmbeddingProvider(EmbeddingProvider):
    @property
    def dimension(self) -> int:
        return 384

    def encode(self, texts):
        raise RuntimeError("Embedding backend crashed")


def test_extraction_failure_returns_empty_without_crashing():
    svc = MemoryExtractionService()
    with patch("app.services.memory_extraction.llm_service.generate_with_context", side_effect=RuntimeError("API down")):
        res = svc.extract_memories([{"role": "user", "content": "Revenue excludes cancelled."}], "u1", "o1")
        assert res == []

    with patch("app.services.memory_extraction.llm_service.generate_with_context", return_value="not valid json {{"):
        res = svc.extract_memories([{"role": "user", "content": "Revenue excludes cancelled."}], "u1", "o1")
        assert res == []


def test_chatbot_survives_memory_retrieval_and_embedding_failure(test_client, tmp_path):
    ext_db = tmp_path / "ext_fail.db"
    conn = sqlite3.connect(ext_db)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL, status TEXT)")
    conn.execute("INSERT INTO orders VALUES (1, 100.0, 'completed')")
    conn.commit()
    conn.close()

    with patch("app.services.context_builder.ContextBuilder.build_context", side_effect=RuntimeError("Memory DB error")):
        with patch("app.services.llm_service.llm_service.generate_sql", return_value="SELECT SUM(amount) AS total FROM orders"):
            with patch("app.services.llm_service.llm_service.generate_natural_response", return_value="Total is 100."):
                resp = test_client.post(
                    "/api/chat/ask",
                    json={
                        "natural_query": "Show total order amount",
                        "db_type": "sqlite",
                        "database_name": str(ext_db),
                        "organization_id": "default-org",
                        "user_id": "default-user",
                    },
                )
    assert resp.status_code == 200
    assert resp.json()["data"][0]["total"] == 100.0


def test_missing_memory_returns_404(test_client):
    resp = test_client.get("/api/memories/non-existent-id?organization_id=default-org&user_id=default-user")
    assert resp.status_code == 404

