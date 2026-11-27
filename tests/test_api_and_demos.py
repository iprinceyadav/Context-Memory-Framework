import sqlite3
from unittest.mock import patch


def _create_sales_sqlite(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT)")
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL, status TEXT, invoice_date TEXT)"
    )
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [(1, "Acme", "Delhi"), (2, "Beta", "Mumbai"), (3, "Gamma", "Delhi")],
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 500.0, "paid", "2026-04-10"),
            (2, 1, 200.0, "cancelled", "2026-04-12"),
            (3, 2, 300.0, "paid", "2026-04-15"),
            (4, 3, 150.0, "refunded", "2026-04-18"),
        ],
    )
    conn.commit()
    conn.close()


def test_end_to_end_demos_1_through_10(test_client, tmp_path):
    ext_db = tmp_path / "sales_e2e.db"
    _create_sales_sqlite(ext_db)

    org_id = "default-org"
    user_id = "default-user"

    # DEMO 10: Original /api/databases/ask backward compatibility
    with patch("app.services.llm_service.llm_service.generate_sql", return_value="SELECT COUNT(*) AS cnt FROM customers"):
        orig_resp = test_client.post(
            "/api/databases/ask",
            json={
                "db_type": "sqlite",
                "host": "localhost",
                "port": 0,
                "database_name": str(ext_db),
                "username": "sqlite",
                "password": "",
                "natural_query": "Count customers",
            },
        )
    assert orig_resp.status_code == 200
    assert orig_resp.json()["data"][0]["cnt"] == 3

    # DEMO 1: Extract semantic memory & approve it
    extract_payload = '{"memories":[{"type":"semantic","subject":"revenue","content":"For all revenue reports, exclude cancelled orders.","scope":"personal","visibility":"personal","confidence":0.95,"importance":9}]}'

    def fake_llm_context(prompt: str) -> str:
        if "Context Memory Extraction" in prompt:
            return extract_payload
        if "exclude cancelled orders" in prompt and "refunded" not in prompt:
            return "SELECT SUM(amount) AS revenue FROM orders WHERE status != 'cancelled'"
        if "exclude both cancelled and refunded" in prompt:
            return "SELECT SUM(amount) AS revenue FROM orders WHERE status NOT IN ('cancelled', 'refunded')"
        return "SELECT SUM(amount) AS revenue FROM orders"

    with patch("app.services.llm_service.llm_service.generate_with_context", side_effect=fake_llm_context):
        with patch("app.services.llm_service.llm_service.generate_natural_response", return_value="Done."):
            r1 = test_client.post(
                "/api/chat/ask",
                json={
                    "natural_query": "For all revenue reports, exclude cancelled orders. Show total revenue.",
                    "db_type": "sqlite",
                    "database_name": str(ext_db),
                    "organization_id": org_id,
                    "user_id": user_id,
                },
            )
    assert r1.status_code == 200
    conv1_id = r1.json()["conversation_id"]

    # Approve extracted pending memory
    pending_list = test_client.get(f"/api/memories/?organization_id={org_id}&user_id={user_id}&status=pending").json()
    assert len(pending_list) >= 1
    mem_id = pending_list[0]["id"]
    app_resp = test_client.post(f"/api/memories/{mem_id}/approve?organization_id={org_id}&user_id={user_id}")
    assert app_resp.status_code == 200
    assert app_resp.json()["status"] == "approved"

    # DEMO 2: Cross-session recall in a brand new conversation
    captured_prompts = []

    def capture_and_sql(prompt: str) -> str:
        if "Context Memory Extraction" in prompt:
            return '{"memories":[]}'
        captured_prompts.append(prompt)
        if "include cancelled" in prompt.lower():
            return "SELECT SUM(amount) AS revenue FROM orders"
        if "Delhi" in prompt and "PREVIOUS ANALYTICAL CONTEXT" in prompt:
            return "SELECT c.name, SUM(o.amount) AS rev FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.city = 'Delhi' AND o.status != 'cancelled' GROUP BY c.name"
        if "exclude cancelled orders" in prompt:
            return "SELECT SUM(amount) AS revenue FROM orders WHERE status != 'cancelled'"
        return "SELECT c.name, SUM(o.amount) AS rev FROM customers c JOIN orders o ON c.id=o.customer_id GROUP BY c.name LIMIT 10"

    with patch("app.services.llm_service.llm_service.generate_with_context", side_effect=capture_and_sql):
        with patch("app.services.llm_service.llm_service.generate_natural_response", return_value="Revenue computed."):
            r2 = test_client.post(
                "/api/chat/ask",
                json={
                    "natural_query": "Show monthly revenue.",
                    "db_type": "sqlite",
                    "database_name": str(ext_db),
                    "organization_id": org_id,
                    "user_id": user_id,
                },
            )
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] != conv1_id
    assert len(r2.json()["context_used"]["memories"]) >= 1
    assert r2.json()["data"][0]["revenue"] == 950.0  # 500 + 300 + 150 (200 cancelled excluded)

    # DEMO 9: Context Inspector debug endpoint
    dbg = test_client.get(f"/api/memory/debug/{r2.json()['conversation_id']}?organization_id={org_id}&user_id={user_id}")
    assert dbg.status_code == 200
    assert len(dbg.json()["retrieved_memories"]) >= 1

    # DEMO 4: Follow-up intelligence
    with patch("app.services.llm_service.llm_service.generate_with_context", side_effect=capture_and_sql):
        with patch("app.services.llm_service.llm_service.generate_natural_response", return_value="Top customers."):
            r_top = test_client.post(
                "/api/chat/ask",
                json={
                    "natural_query": "Show the top 10 customers by revenue.",
                    "db_type": "sqlite",
                    "database_name": str(ext_db),
                    "organization_id": org_id,
                    "user_id": user_id,
                },
            )
            r_followup = test_client.post(
                "/api/chat/ask",
                json={
                    "natural_query": "Only show Delhi customers.",
                    "conversation_id": r_top.json()["conversation_id"],
                    "db_type": "sqlite",
                    "database_name": str(ext_db),
                    "organization_id": org_id,
                    "user_id": user_id,
                },
            )
    assert r_followup.status_code == 200
    assert "Delhi" in r_followup.json()["sql_query"]

    # DEMO 5: Temporary override does NOT mutate saved rule
    with patch("app.services.llm_service.llm_service.generate_with_context", side_effect=capture_and_sql):
        with patch("app.services.llm_service.llm_service.generate_natural_response", return_value="Included cancelled."):
            r_temp = test_client.post(
                "/api/chat/ask",
                json={
                    "natural_query": "For this report, include cancelled orders.",
                    "db_type": "sqlite",
                    "database_name": str(ext_db),
                    "organization_id": org_id,
                    "user_id": user_id,
                },
            )
    assert r_temp.json()["data"][0]["revenue"] == 1150.0
    saved = test_client.get(f"/api/memories/{mem_id}?organization_id={org_id}&user_id={user_id}").json()
    assert "exclude cancelled orders" in saved["content"]

    # DEMO 6: Permanent correction with versioning
    patch_resp = test_client.patch(
        f"/api/memories/{mem_id}?organization_id={org_id}&user_id={user_id}",
        json={
            "content": "Our revenue definition changed: exclude both cancelled and refunded orders.",
            "change_reason": "Permanent definition update",
        },
    )
    assert patch_resp.status_code == 200
    vers = test_client.get(f"/api/memories/{mem_id}/versions?organization_id={org_id}&user_id={user_id}").json()
    assert len(vers) == 2

    # DEMO 8: Deletion removes memory from future retrieval
    del_resp = test_client.delete(f"/api/memories/{mem_id}?organization_id={org_id}&user_id={user_id}")
    assert del_resp.status_code == 200
    search_after = test_client.post(
        "/api/memories/search",
        json={"query": "exclude cancelled orders", "organization_id": org_id, "user_id": user_id},
    )
    assert search_after.json() == []


def test_evaluation_results_endpoint(test_client):
    resp = test_client.get("/api/evaluation/results")
    assert resp.status_code == 200
    body = resp.json()
    assert "results_exists" in body
    assert "dataset_exists" in body
    assert "dataset" in body


