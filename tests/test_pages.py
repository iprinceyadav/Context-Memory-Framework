import sqlite3
from unittest.mock import patch

import pytest

HTMX = {"HX-Request": "true"}


def _orders_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount REAL)")
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?)", [(1, "paid", 100.0), (2, "paid", 250.0), (3, "pending", 75.0)])
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def as_admin(request, client):
    if "fresh_visitor" in request.keywords:
        return
    response = client.post("/session/user-type", data={"user_type": "admin"}, headers=HTMX)
    assert response.headers["HX-Redirect"] == "/connect"


@pytest.fixture()
def connected(client, tmp_path):
    path = tmp_path / "shop.db"
    _orders_db(path)
    response = client.post("/connect", data={"db_type": "sqlite", "sqlite_path": str(path)}, headers=HTMX)
    assert response.status_code == 200
    assert "Context ready!" in response.text
    assert response.headers["HX-Retarget"] == "#connect-body"
    assert client.cookies.get("cmf_connection")
    return path


@pytest.mark.parametrize(
    "path, marker",
    [
        ("/", "Context Memory Framework"),
        ("/connect", "PostgreSQL"),
        ("/connect?db=sqlite", "File · max 50 MB"),
        ("/chat", "Ask anything"),
        ("/memories", "Memories"),
        ("/inspector", "Ask something first."),
        ("/knowledge", "Knowledge"),
        ("/evaluation", "Evaluation"),
    ],
)
def test_pages_render(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert marker in response.text


def test_static_stylesheet_is_served(client):
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert ".sidebar-left" in response.text


def test_failed_connection_reports_failed_step(client, tmp_path):
    response = client.post(
        "/connect",
        data={"db_type": "postgresql", "host": "127.0.0.1", "port": "1", "database_name": "x", "username": "u", "password": "p"},
        headers=HTMX,
    )
    assert response.status_code == 200
    assert "Connection failed" in response.text
    assert "sync-step failed" in response.text
    assert not client.cookies.get("cmf_connection")


def test_chat_ask_renders_answer_and_context(client, connected):
    sidebar = client.get("/chat").text
    assert "orders" in sidebar

    with patch("app.services.llm.llm.generate", return_value="SELECT id, amount FROM orders WHERE status = 'paid'"):
        with patch("app.services.llm.llm.summarize", return_value="Two paid orders."):
            response = client.post("/chat/ask", data={"question": "Show paid orders"}, headers=HTMX)

    assert response.status_code == 200
    assert "Two paid orders." in response.text
    assert "Context ·" in response.text
    assert "<td>250.0</td>" in response.text
    assert 'id="context-panel-wrapper"' in response.text
    conversation_id = client.cookies.get("cmf_conversation")
    assert conversation_id
    assert response.headers["HX-Push-Url"].endswith(conversation_id)

    history = client.get(f"/chat?conversation={conversation_id}").text
    assert "Show paid orders" in history
    assert "Two paid orders." in history

    inspector = client.get("/inspector").text
    assert "Prompt" in inspector
    assert "Show paid orders" in inspector


def test_upload_sqlite_file_connects(client, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    source = tmp_path / "made_with_python.db"
    _orders_db(source)

    response = client.post(
        "/connect/upload",
        files={"database_file": ("made_with_python.db", source.read_bytes(), "application/octet-stream")},
        headers=HTMX,
    )
    assert response.status_code == 200
    assert "Context ready!" in response.text
    assert "made_with_python.db" in response.text
    assert len(list((tmp_path / "uploads").glob("made_with_python-*.db"))) == 1
    assert "orders" in client.get("/chat").text


def test_upload_rejects_non_sqlite_file(client, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    response = client.post(
        "/connect/upload",
        files={"database_file": ("notes.db", b"just some text", "application/octet-stream")},
        headers=HTMX,
    )
    assert "This is not a SQLite database file." in response.text
    assert not (tmp_path / "uploads").exists()
    assert not client.cookies.get("cmf_connection")


def test_chat_without_connection_explains(client):
    response = client.post("/chat/ask", data={"question": "Show orders"}, headers=HTMX)
    assert "No database is connected" in response.text


def test_knowledge_and_memory_approval(client):
    response = client.post(
        "/knowledge",
        data={"memory_type": "semantic", "subject": "revenue", "content": "Revenue excludes cancelled orders.", "scope": "personal", "visibility": "personal", "importance": "8"},
        headers=HTMX,
    )
    assert response.headers["HX-Redirect"] == "/memories?status=pending&notice=queued"

    page = client.get("/memories?status=pending").text
    assert "Revenue excludes cancelled orders." in page
    memory_id = client.get("/api/memories?status=pending").json()[0]["id"]

    detail = client.get(f"/memories/{memory_id}/detail")
    assert "Version History" in detail.text

    approved = client.post(f"/memories/{memory_id}/approve", data={"status": "approved"}, headers=HTMX)
    assert "badge-status-approved" in approved.text
    assert client.get(f"/api/memories/{memory_id}").json()["status"] == "approved"

    edited = client.post(f"/memories/{memory_id}/edit", data={"content": "Revenue excludes cancelled and refunded orders."}, headers=HTMX)
    assert "refunded" in edited.text


def test_change_request_flow(client, connected):
    with patch("app.services.llm.llm.generate", return_value="UPDATE orders SET status = 'shipped' WHERE id = 3"):
        response = client.post("/chat/ask", data={"question": "Mark order 3 as shipped"}, headers=HTMX)
    assert ">Change<" in response.text
    assert ">Apply</button>" in response.text

    changes = client.get("/api/changes?status=pending").json()
    assert len(changes) == 1
    change_id = changes[0]["id"]

    approved = client.post(f"/changes/{change_id}/approve", headers=HTMX)
    assert "Applied" in approved.text

    conn = sqlite3.connect(connected)
    try:
        assert conn.execute("SELECT status FROM orders WHERE id = 3").fetchone()[0] == "shipped"
    finally:
        conn.close()


@pytest.mark.fresh_visitor
def test_first_visit_asks_for_user_type(client):
    landing = client.get("/").text
    assert "Choose your role" in landing
    for user_type in ("admin", "finance", "sales", "operations", "hr", "employee"):
        assert f'value="{user_type}"' in landing

    for path in ("/chat", "/memories", "/connect"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_removed_pages_are_gone(client):
    for path in ("/approvals", "/team"):
        assert client.get(path).status_code == 404
    chat = client.get("/chat").text
    assert 'href="/approvals"' not in chat
    assert 'href="/team"' not in chat


def test_employee_is_read_only(client, connected):
    switched = client.post("/session/user-type", data={"user_type": "employee"}, headers=HTMX)
    assert switched.headers["HX-Redirect"] == "/chat"

    chat = client.get("/chat").text
    assert "Employee" in chat
    assert "Read-only" in chat

    with patch("app.services.llm.llm.generate", return_value="UPDATE orders SET status = 'shipped' WHERE id = 3"):
        response = client.post("/chat/ask", data={"question": "Mark order 3 as shipped"}, headers=HTMX)
    assert "Employee is read-only." in response.text
    assert ">Apply</button>" not in response.text
    assert client.get("/api/changes?status=pending").json() == []

    conn = sqlite3.connect(connected)
    try:
        assert conn.execute("SELECT status FROM orders WHERE id = 3").fetchone()[0] == "pending"
    finally:
        conn.close()

    back = client.post("/session/user-type", data={"user_type": "admin"}, headers=HTMX)
    assert back.headers["HX-Redirect"] == "/chat"
    assert "Full access" in client.get("/chat").text


def test_department_change_needs_admin_approval(client, connected):
    client.post("/session/user-type", data={"user_type": "finance"}, headers=HTMX)
    chat = client.get("/chat").text
    assert "Finance" in chat
    assert 'href="/requests"' not in chat

    with patch("app.services.llm.llm.generate", return_value="UPDATE orders SET status = 'shipped' WHERE id = 3"):
        response = client.post("/chat/ask", data={"question": "Mark order 3 as shipped"}, headers=HTMX)
    assert "Sent for approval" in response.text
    assert ">Apply</button>" not in response.text

    with patch("app.services.llm.llm.generate", return_value="DELETE FROM orders WHERE id = 3"):
        denied = client.post("/chat/ask", data={"question": "Delete order 3"}, headers=HTMX)
    assert "Not allowed" in denied.text

    client.post("/session/user-type", data={"user_type": "admin"}, headers=HTMX)
    inbox = client.get("/requests").text
    assert "Mark order 3 as shipped" in inbox
    change_id = client.get("/api/changes?status=pending").json()[0]["id"]
    assert "Applied" in client.post(f"/changes/{change_id}/approve", headers=HTMX).text

    conn = sqlite3.connect(connected)
    try:
        assert conn.execute("SELECT status FROM orders WHERE id = 3").fetchone()[0] == "shipped"
    finally:
        conn.close()


def test_read_only_department_cannot_request_changes(client, connected):
    client.post("/session/user-type", data={"user_type": "hr"}, headers=HTMX)
    assert client.get("/requests", follow_redirects=False).status_code == 303
    with patch("app.services.llm.llm.generate", return_value="UPDATE orders SET status = 'shipped' WHERE id = 3"):
        response = client.post("/chat/ask", data={"question": "Mark order 3 as shipped"}, headers=HTMX)
    assert "HR is read-only." in response.text
