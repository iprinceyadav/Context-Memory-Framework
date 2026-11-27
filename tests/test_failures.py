import sqlite3
from unittest.mock import patch
from app.services.embedder import EmbeddingProvider, embedder
from app.services.extraction import MemoryExtractor


class FailingEmbeddingProvider(EmbeddingProvider):
    @property
    def dimension(self) -> int:
        return 384

    def encode(self, texts):
        raise RuntimeError("Embedding backend crashed")


def test_extraction_failure_returns_empty():
    store = MemoryExtractor()
    with patch("app.services.extraction.llm.extract", side_effect=RuntimeError("API down")):
        found = store.extract([{"role": "user", "content": "Revenue excludes cancelled."}], "u1", "o1")
        assert found == []

    with patch("app.services.extraction.llm.extract", return_value="not valid json {{"):
        found = store.extract([{"role": "user", "content": "Revenue excludes cancelled."}], "u1", "o1")
        assert found == []


def test_chat_survives_retrieval_failure(client, tmp_path):
    external_db = tmp_path / "ext_fail.db"
    conn = sqlite3.connect(external_db)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL, status TEXT)")
    conn.execute("INSERT INTO orders VALUES (1, 100.0, 'completed')")
    conn.commit()
    conn.close()

    with patch("app.services.context_builder.ContextBuilder.build", side_effect=RuntimeError("Memory DB error")):
        with patch("app.services.llm.llm.generate_sql", return_value="SELECT SUM(amount) AS total FROM orders"):
            with patch("app.services.llm.llm.summarize", return_value="Total is 100."):
                response = client.post(
                    "/api/chat/ask",
                    json={
                        "natural_query": "Show total order amount",
                        "db_type": "sqlite",
                        "database_name": str(external_db),
                        "organization_id": "default-org",
                        "user_id": "default-user",
                    },
                )
    assert response.status_code == 200
    assert response.json()["data"][0]["total"] == 100.0


def test_missing_memory_returns_404(client):
    response = client.get("/api/memories/non-existent-id?organization_id=default-org&user_id=default-user")
    assert response.status_code == 404


def test_llm_strips_any_code_fence():
    from app.services.llm import llm

    assert llm._strip_fences("```sqlite\nSELECT 1\n```") == "SELECT 1"
    assert llm._strip_fences("```sql SELECT 2 ```") == "SELECT 2"
    assert llm._strip_fences("SELECT 3") == "SELECT 3"
