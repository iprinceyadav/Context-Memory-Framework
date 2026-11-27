import json
import sqlite3
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.sql_guard import plan_sql

ORG = "default-org"
ADMIN = "default-user"


def _orders_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount REAL)")
    conn.execute("CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT, password TEXT)")
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?)",
        [(1, "paid", 100.0), (2, "paid", 250.0), (3, "pending", 75.0)],
    )
    conn.execute("INSERT INTO staff VALUES (1, 'Asha', 'hunter2')")
    conn.commit()
    conn.close()


def _status(path, order_id):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
    finally:
        conn.close()


def _params(user_id):
    return {"organization_id": ORG, "user_id": user_id}


@pytest.fixture()
def workspace(client, tmp_path):
    path = tmp_path / "shop.db"
    _orders_db(path)
    saved = client.post(
        "/api/databases/connect",
        json={
            "name": "Shop",
            "db_type": "sqlite",
            "host": "localhost",
            "port": 0,
            "database_name": str(path),
            "username": "sqlite",
            "password": "",
            "organization_id": ORG,
        },
    )
    assert saved.status_code == 201, saved.text
    connection_id = saved.json()["id"]

    members = {}
    for name, role in (("Emp A", "analyst"), ("Emp B", "analyst")):
        created = client.post(
            "/api/team/members",
            params=_params(ADMIN),
            json={"full_name": name, "email": f"{name.replace(' ', '').lower()}@shop.test", "role": role},
        )
        assert created.status_code == 201, created.text
        members[name] = created.json()["id"]

    return {"path": path, "connection_id": connection_id, "emp_a": members["Emp A"], "emp_b": members["Emp B"]}


def _ask(client, workspace, user_id, sql, question="Mark order 1 as shipped"):
    with patch("app.services.llm.llm.generate", return_value=sql):
        with patch("app.services.llm.llm.summarize", return_value="Answer."):
            response = client.post(
                "/api/chat/ask",
                json={
                    "natural_query": question,
                    "connection_id": workspace["connection_id"],
                    "organization_id": ORG,
                    "user_id": user_id,
                },
            )
    assert response.status_code == 200, response.text
    return response.json()


def _grant(client, user_id, action="update", table="orders"):
    response = client.post(
        "/api/team/grants",
        params=_params(ADMIN),
        json={"user_id": user_id, "action": action, "table_name": table},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_plan_sql_classifies_and_blocks():
    schema = json.dumps({"orders": [{"name": "id", "type": "INT"}, {"name": "status", "type": "TEXT"}]})
    assert plan_sql("SELECT * FROM orders", "sqlite", schema).kind == "read"
    update = plan_sql("UPDATE orders SET status = 'shipped' WHERE id = 1", "sqlite", schema)
    assert update.ok and update.kind == "update" and update.target == "orders"
    assert not plan_sql("UPDATE orders SET status = 'x'", "sqlite", schema).ok
    assert not plan_sql("DELETE FROM orders", "sqlite", schema).ok
    assert not plan_sql("DROP TABLE orders", "sqlite", schema).ok
    assert not plan_sql("UPDATE orders SET colour = 'x' WHERE id = 1", "sqlite", schema).ok
    assert not plan_sql("SELECT 1; DELETE FROM orders WHERE id = 1", "sqlite", schema).ok
    note = plan_sql("INSERT INTO orders (id, status) VALUES (9, 'please create it')", "sqlite", schema)
    assert note.ok and note.kind == "insert"


def test_employee_without_permission_is_refused(client, workspace):
    body = _ask(client, workspace, workspace["emp_b"], "UPDATE orders SET status = 'shipped' WHERE id = 1")
    assert body["outcome"] == "not_allowed"
    assert "permission" in body["natural_response"]
    assert body["change_request"] is None
    assert _status(workspace["path"], 1) == "paid"


def test_granted_employee_needs_admin_approval(client, workspace):
    _grant(client, workspace["emp_a"])
    body = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'shipped' WHERE id = 1")

    assert body["outcome"] == "change_requested"
    request = body["change_request"]
    assert request["status"] == "pending"
    assert request["affected_rows"] == 1
    assert request["preview"]["changes"] == [{"column": "status", "value": "'shipped'"}]
    assert request["preview"]["rows"][0]["status"] == "paid"
    assert _status(workspace["path"], 1) == "paid"

    by_employee = client.post(f"/api/changes/{request['id']}/approve", params=_params(workspace["emp_a"]))
    assert by_employee.status_code == 403
    assert _status(workspace["path"], 1) == "paid"

    other = client.get("/api/changes", params=_params(workspace["emp_b"])).json()
    assert other == []

    approved = client.post(f"/api/changes/{request['id']}/approve", params=_params(ADMIN), json={"note": "ok"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert approved.json()["reviewer"]
    assert _status(workspace["path"], 1) == "shipped"

    again = client.post(f"/api/changes/{request['id']}/approve", params=_params(ADMIN))
    assert again.status_code == 409


def test_grant_is_per_table_and_action(client, workspace):
    _grant(client, workspace["emp_a"], action="update", table="orders")
    delete = _ask(client, workspace, workspace["emp_a"], "DELETE FROM orders WHERE id = 3")
    assert delete["outcome"] == "not_allowed"

    staff = _ask(client, workspace, workspace["emp_a"], "UPDATE staff SET name = 'A' WHERE id = 1")
    assert staff["outcome"] == "not_allowed"


def test_rejected_request_changes_nothing(client, workspace):
    _grant(client, workspace["emp_a"])
    body = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'void' WHERE id = 2")
    rejected = client.post(
        f"/api/changes/{body['change_request']['id']}/reject",
        params=_params(ADMIN),
        json={"note": "Not this one"},
    )
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["review_note"] == "Not this one"
    assert _status(workspace["path"], 2) == "paid"


def test_approval_stops_when_data_changed(client, workspace):
    _grant(client, workspace["emp_a"])
    body = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'archived' WHERE status = 'paid'")
    assert body["change_request"]["affected_rows"] == 2

    conn = sqlite3.connect(workspace["path"])
    conn.execute("UPDATE orders SET status = 'paid' WHERE id = 3")
    conn.commit()
    conn.close()

    result = client.post(f"/api/changes/{body['change_request']['id']}/approve", params=_params(ADMIN)).json()
    assert result["status"] == "failed"
    assert "changed since" in result["error"]
    assert _status(workspace["path"], 1) == "paid"


def test_revoked_permission_is_refused(client, workspace):
    grant = _grant(client, workspace["emp_a"])
    revoked = client.delete(f"/api/team/grants/{grant['id']}", params=_params(ADMIN))
    assert revoked.status_code == 200
    body = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'shipped' WHERE id = 1")
    assert body["outcome"] == "not_allowed"


def test_unsafe_and_oversized_changes_are_refused(client, workspace):
    _grant(client, workspace["emp_a"], table="*")
    every_row = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'x'")
    assert every_row["outcome"] == "not_allowed"

    schema_change = _ask(client, workspace, workspace["emp_a"], "DROP TABLE orders")
    assert schema_change["outcome"] == "not_allowed"

    nothing = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'x' WHERE id = 999")
    assert nothing["outcome"] == "no_match"

    with patch.object(settings, "WRITE_MAX_ROWS", 1):
        too_many = _ask(client, workspace, workspace["emp_a"], "UPDATE orders SET status = 'x' WHERE amount > 50")
    assert too_many["outcome"] == "not_allowed"
    assert "limit" in too_many["natural_response"]


def test_only_admin_manages_access(client, workspace):
    response = client.post(
        "/api/team/grants",
        params=_params(workspace["emp_a"]),
        json={"user_id": workspace["emp_b"], "action": "update", "table_name": "orders"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/team/members",
        params=_params(workspace["emp_a"]),
        json={"full_name": "X", "email": "x@shop.test", "role": "admin"},
    )
    assert response.status_code == 403


def test_reads_mask_sensitive_columns_and_cap_rows(client, workspace):
    employee = _ask(client, workspace, workspace["emp_b"], "SELECT * FROM staff", question="Show staff")
    assert employee["outcome"] == "answer"
    assert employee["data"][0]["password"] != "hunter2"
    assert employee["hidden_columns"] == ["password"]

    admin = _ask(client, workspace, ADMIN, "SELECT * FROM staff", question="Show staff")
    assert admin["data"][0]["password"] == "hunter2"

    with patch.object(settings, "MAX_QUERY_ROWS", 2):
        capped = _ask(client, workspace, workspace["emp_b"], "SELECT * FROM orders", question="List orders")
    assert len(capped["data"]) == 2
    assert capped["truncated"] is True


def test_read_query_cannot_write(client, workspace):
    response = client.post(
        "/api/databases/query-direct",
        json={
            "db_type": "sqlite",
            "host": "localhost",
            "port": 0,
            "database_name": str(workspace["path"]),
            "username": "sqlite",
            "password": "",
            "query": "UPDATE orders SET status = 'x' WHERE id = 1",
        },
    )
    assert response.status_code == 400
    assert _status(workspace["path"], 1) == "paid"


def test_insert_and_delete_after_approval(client, workspace):
    _grant(client, workspace["emp_a"], action="insert")
    _grant(client, workspace["emp_a"], action="delete")

    added = _ask(client, workspace, workspace["emp_a"], "INSERT INTO orders (id, status, amount) VALUES (4, 'new', 10), (5, 'new', 20)")
    assert added["change_request"]["affected_rows"] == 2
    assert len(added["change_request"]["preview"]["new_rows"]) == 2
    applied = client.post(f"/api/changes/{added['change_request']['id']}/approve", params=_params(ADMIN)).json()
    assert applied["status"] == "executed"

    removed = _ask(client, workspace, workspace["emp_a"], "DELETE FROM orders WHERE status = 'new'")
    assert removed["change_request"]["affected_rows"] == 2
    applied = client.post(f"/api/changes/{removed['change_request']['id']}/approve", params=_params(ADMIN)).json()
    assert applied["status"] == "executed"

    conn = sqlite3.connect(workspace["path"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3
    finally:
        conn.close()
