import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.api import access as access_api
from app.api import chat as chat_api
from app.api import databases as databases_api
from app.api import memories as memories_api
from app.core.config import settings
from app.schemas.access import ReviewRequest
from app.schemas.chat import ChatRequest
from app.schemas.database_connection import (
    DatabaseConnectionCreate,
    DatabaseConnectionTest,
    SyncSchemaRequest,
)
from app.schemas.memory import MemoryCreate, MemorySearchRequest, MemoryUpdate
from app.web.templating import DB_LABELS, tables_in_sql, templates
from app.web.workspace import (
    CONNECTION_COOKIE,
    CONVERSATION_COOKIE,
    COOKIE_MAX_AGE,
    ORGANIZATION_ID,
    USER_COOKIE,
    USER_TYPES,
    Workspace,
    ensure_user_type,
    error_message,
    load_workspace,
)

router = APIRouter(include_in_schema=False)

SQLITE_HEADER = b"SQLite format 3\x00"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MEMORY_TYPES = ("semantic", "procedural", "episodic")
MEMORY_STATUSES = ("pending", "approved", "rejected", "superseded")
MEMORY_NOTICES = {
    "activated": "Business knowledge saved and activated.",
    "queued": "Business knowledge saved to the pending review queue.",
}


def render(
    request: Request,
    ws: Optional[Workspace],
    template: str,
    page: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    **context: Any,
):
    if page and not ws.onboarded:
        return redirect(request, "/")
    return templates.TemplateResponse(
        request,
        template,
        {"ws": ws, "page": page, **context},
        headers=headers,
    )


def redirect(request: Request, url: str) -> Response:
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def remember(response: Response, name: str, value: str) -> None:
    response.set_cookie(name, value, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, ws: Workspace = Depends(load_workspace)):
    return render(request, ws, "landing.html", user_types=USER_TYPES)


@router.post("/session/user-type")
def choose_user_type(request: Request, user_type: str = Form(...), ws: Workspace = Depends(load_workspace)):
    user = ensure_user_type(ws.db, user_type)
    response = redirect(request, "/chat" if ws.connection else "/connect")
    remember(response, USER_COOKIE, user.id)
    response.delete_cookie(CONVERSATION_COOKIE)
    return response


@router.post("/disconnect")
def disconnect(request: Request):
    response = redirect(request, "/")
    response.delete_cookie(CONNECTION_COOKIE)
    response.delete_cookie(CONVERSATION_COOKIE)
    return response


def connection_payload(
    db_type: str,
    host: str,
    port: str,
    database_name: str,
    username: str,
    password: str,
    sqlite_path: str,
) -> Dict[str, Any]:
    if db_type == "sqlite":
        return {
            "db_type": "sqlite",
            "host": "localhost",
            "port": 0,
            "database_name": sqlite_path.strip() or "cmf_platform.db",
            "username": "sqlite",
            "password": "",
        }
    default_port = 3306 if db_type == "mysql" else 5432
    try:
        port_number = int(port) if port.strip() else default_port
    except ValueError:
        port_number = default_port
    return {
        "db_type": db_type,
        "host": host.strip() or "localhost",
        "port": port_number,
        "database_name": database_name.strip(),
        "username": username.strip(),
        "password": password,
    }


@router.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, db: Optional[str] = Query(None), ws: Workspace = Depends(load_workspace)):
    if not ws.onboarded:
        return redirect(request, "/")
    return render(request, ws, "connect.html", db_type=db if db in DB_LABELS else None)


@router.post("/connect/test", response_class=HTMLResponse)
def connect_test(
    request: Request,
    db_type: str = Form("postgresql"),
    host: str = Form(""),
    port: str = Form(""),
    database_name: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    sqlite_path: str = Form(""),
):
    payload = connection_payload(db_type, host, port, database_name, username, password, sqlite_path)
    try:
        databases_api.check_connection(DatabaseConnectionTest(**payload))
        return render(request, None, "partials/test_result.html", ok=True)
    except (HTTPException, ValidationError) as exc:
        return render(request, None, "partials/test_result.html", ok=False, message=error_message(exc))


@router.post("/connect", response_class=HTMLResponse)
def connect_submit(
    request: Request,
    db_type: str = Form("postgresql"),
    host: str = Form(""),
    port: str = Form(""),
    database_name: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    sqlite_path: str = Form(""),
    ws: Workspace = Depends(load_workspace),
):
    payload = connection_payload(db_type, host, port, database_name, username, password, sqlite_path)
    return connect_and_sync(request, ws, payload, payload["database_name"])


@router.post("/connect/upload", response_class=HTMLResponse)
def connect_upload(
    request: Request,
    database_file: UploadFile = File(...),
    ws: Workspace = Depends(load_workspace),
):
    original_name = Path(database_file.filename or "uploaded.db").name
    content = database_file.file.read(MAX_UPLOAD_BYTES + 1)
    error = None
    if len(content) > MAX_UPLOAD_BYTES:
        error = f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
    elif not content.startswith(SQLITE_HEADER):
        error = "This is not a SQLite database file."
    if error:
        return render(request, ws, "partials/sync_result.html", steps=connect_steps(), error=error)

    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(original_name).stem)[:40] or "database"
    stored = upload_dir / f"{safe_stem}-{uuid.uuid4().hex[:8]}.db"
    stored.write_bytes(content)
    payload = connection_payload("sqlite", "", "", "", "", "", str(stored))
    return connect_and_sync(request, ws, payload, original_name)


def connect_steps() -> List[Dict[str, str]]:
    return [
        {"label": "Establish connection", "state": "pending"},
        {"label": "Retrieve schema", "state": "pending"},
        {"label": "Build context map", "state": "pending"},
    ]


def connect_and_sync(request: Request, ws: Workspace, payload: Dict[str, Any], name: str):
    steps = connect_steps()
    saved = None
    try:
        databases_api.check_connection(DatabaseConnectionTest(**payload))
        steps[0]["state"] = "done"
        databases_api.sync(SyncSchemaRequest(**payload), ws.db)
        steps[1]["state"] = "done"
        saved = databases_api.connect(
            DatabaseConnectionCreate(**payload, name=name, organization_id=ORGANIZATION_ID),
            ws.db,
        )
        steps[2]["state"] = "done"
    except (HTTPException, ValidationError) as exc:
        failed = next(step for step in steps if step["state"] == "pending")
        failed["state"] = "failed"
        return render(request, ws, "partials/sync_result.html", steps=steps, error=error_message(exc))

    response = render(
        request,
        ws,
        "partials/sync_result.html",
        headers={"HX-Retarget": "#connect-body", "HX-Reswap": "innerHTML"},
        steps=steps,
        connection=saved,
    )
    remember(response, CONNECTION_COOKIE, saved.id)
    response.delete_cookie(CONVERSATION_COOKIE)
    return response


@router.get("/connect/ready")
def connect_ready(request: Request):
    return redirect(request, "/chat")


def stored_messages(ws: Workspace, conversation_id: Optional[str]) -> List[Any]:
    if not conversation_id:
        return []
    try:
        return chat_api.get_conversation_messages(conversation_id, ws.organization_id, ws.user_id, ws.db)
    except HTTPException:
        return []


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, conversation: Optional[str] = Query(None), ws: Workspace = Depends(load_workspace)):
    messages = stored_messages(ws, conversation)
    active_conversation = conversation if messages else None
    last_sql = next((m.sql_query for m in reversed(messages) if m.role == "assistant" and m.sql_query), None)
    panel = None
    if last_sql:
        panel = {"sql": last_sql, "tables": tables_in_sql(last_sql), "memories": None, "collapsed": True}
    response = render(
        request,
        ws,
        "chat.html",
        page="chat",
        messages=messages,
        conversation_id=active_conversation,
        panel=panel,
    )
    if active_conversation:
        remember(response, CONVERSATION_COOKIE, active_conversation)
    return response


@router.post("/chat/ask", response_class=HTMLResponse)
def chat_ask(
    request: Request,
    background_tasks: BackgroundTasks,
    question: str = Form(""),
    conversation_id: str = Form(""),
    ws: Workspace = Depends(load_workspace),
):
    question = question.strip()
    if not question:
        return HTMLResponse("")
    if ws.connection is None:
        return render(
            request,
            ws,
            "partials/chat_exchange.html",
            question=question,
            error="No database is connected. Use Connect Database first.",
        )
    try:
        result = chat_api.ask(
            ChatRequest(
                natural_query=question,
                conversation_id=conversation_id or None,
                connection_id=ws.connection_id,
                organization_id=ws.organization_id,
                user_id=ws.user_id,
            ),
            background_tasks,
            ws.db,
        )
    except HTTPException as exc:
        return render(request, ws, "partials/chat_exchange.html", question=question, error=error_message(exc))

    panel = {
        "sql": result.sql_query,
        "tables": tables_in_sql(result.sql_query),
        "memories": result.context_used.memories if result.context_used else [],
        "collapsed": False,
    }
    response = render(
        request,
        ws,
        "partials/chat_exchange.html",
        headers={"HX-Push-Url": f"/chat?conversation={quote(result.conversation_id)}"},
        question=question,
        result=result,
        panel=panel,
        conversation_id=result.conversation_id,
    )
    remember(response, CONVERSATION_COOKIE, result.conversation_id)
    return response


@router.get("/fragments/pending-banner", response_class=HTMLResponse)
def pending_banner(request: Request, ws: Workspace = Depends(load_workspace)):
    return render(request, ws, "partials/pending_banner.html", with_badges=True)


@router.post("/changes/{request_id}/{decision}", response_class=HTMLResponse)
def review_change(request: Request, request_id: str, decision: str, ws: Workspace = Depends(load_workspace)):
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="Unknown decision.")
    note = unquote(request.headers.get("HX-Prompt", "")).strip() or None
    review = access_api.approve_change if decision == "approve" else access_api.reject_change
    action_error = None
    try:
        change = review(request_id, ReviewRequest(note=note), ws.organization_id, ws.user_id, ws.db)
    except (HTTPException, ValidationError) as exc:
        action_error = error_message(exc)
        try:
            change = access_api.get_change(request_id, ws.organization_id, ws.user_id, ws.db)
        except HTTPException:
            return render(request, ws, "partials/notice.html", kind="error", message=action_error)
    return render(
        request,
        ws,
        "partials/change_card_response.html",
        change=change,
        action_error=action_error,
        with_badges=True,
    )


@router.get("/requests", response_class=HTMLResponse)
def requests_page(request: Request, ws: Workspace = Depends(load_workspace)):
    if ws.onboarded and not ws.is_admin:
        return redirect(request, "/chat")
    changes = [
        change
        for change in access_api.list_changes(ws.organization_id, ws.user_id, "pending", ws.db)
        if change["user_id"] != ws.user_id
    ]
    return render(request, ws, "requests.html", page="requests", changes=changes)


def memory_items(ws: Workspace, memory_type: str, status: str, q: str) -> List[Dict[str, Any]]:
    if q.strip():
        results = memories_api.search(
            MemorySearchRequest(
                query=q.strip(),
                organization_id=ws.organization_id,
                user_id=ws.user_id,
                memory_types=[memory_type] if memory_type else None,
                include_superseded=True,
                only_approved=False,
                limit=30,
            ),
            ws.db,
        )
        return [
            {
                "id": result.memory_id,
                "memory_type": result.memory_type,
                "status": result.status,
                "scope": result.scope,
                "visibility": result.visibility,
                "subject": result.subject,
                "content": result.content,
                "score": result.combined_score,
            }
            for result in results
            if not status or result.status == status
        ]
    memories = memories_api.list_visible(
        ws.organization_id, ws.user_id, None, memory_type or None, status or None, 0, 100, ws.db
    )
    return [
        {
            "id": memory.id,
            "memory_type": memory.memory_type,
            "status": memory.status,
            "scope": memory.scope,
            "visibility": memory.visibility,
            "subject": memory.subject,
            "content": memory.content,
            "score": None,
        }
        for memory in memories
    ]


def memory_panel(
    request: Request,
    ws: Workspace,
    memory_type: str = "",
    status: str = "",
    q: str = "",
    error: Optional[str] = None,
    notice: Optional[str] = None,
    template: str = "partials/memory_panel.html",
    **extra: Any,
):
    ws.cached.pop("memory_stats", None)
    return render(
        request,
        ws,
        template,
        page="memories",
        memory_type=memory_type,
        status=status,
        q=q,
        error=error,
        notice=notice,
        items=memory_items(ws, memory_type, status, q),
        memory_types=MEMORY_TYPES,
        memory_statuses=MEMORY_STATUSES,
        with_badges=template != "memories.html",
        **extra,
    )


@router.get("/memories", response_class=HTMLResponse)
def memories_page(
    request: Request,
    memory_type: str = Query(""),
    status: str = Query(""),
    q: str = Query(""),
    notice: str = Query(""),
    ws: Workspace = Depends(load_workspace),
):
    return memory_panel(
        request, ws, memory_type, status, q, notice=MEMORY_NOTICES.get(notice), template="memories.html"
    )


@router.get("/memories/panel", response_class=HTMLResponse)
def memories_panel(
    request: Request,
    memory_type: str = Query(""),
    status: str = Query(""),
    q: str = Query(""),
    ws: Workspace = Depends(load_workspace),
):
    return memory_panel(request, ws, memory_type, status, q)


@router.post("/memories/{memory_id}/{action}", response_class=HTMLResponse)
def memory_action(
    request: Request,
    memory_id: str,
    action: str,
    memory_type: str = Form(""),
    status: str = Form(""),
    q: str = Form(""),
    content: str = Form(""),
    ws: Workspace = Depends(load_workspace),
):
    error = None
    try:
        if action == "approve":
            memories_api.approve(memory_id, ws.organization_id, ws.user_id, ws.db)
        elif action == "reject":
            memories_api.reject(memory_id, ws.organization_id, ws.user_id, ws.db)
        elif action == "delete":
            memories_api.delete(memory_id, False, ws.organization_id, ws.user_id, ws.db)
        elif action == "edit":
            if content.strip():
                memories_api.update(
                    memory_id,
                    MemoryUpdate(content=content.strip(), change_reason="Edited from Memory Dashboard"),
                    ws.organization_id,
                    ws.user_id,
                    ws.db,
                )
        else:
            raise HTTPException(status_code=404, detail="Unknown memory action.")
    except (HTTPException, ValidationError) as exc:
        error = error_message(exc)
    return memory_panel(request, ws, memory_type, status, q, error=error)


@router.get("/memories/{memory_id}/edit", response_class=HTMLResponse)
def memory_edit_form(request: Request, memory_id: str, ws: Workspace = Depends(load_workspace)):
    try:
        memory = memories_api.get(memory_id, ws.organization_id, ws.user_id, ws.db)
    except HTTPException as exc:
        return render(request, ws, "partials/notice.html", kind="error", message=error_message(exc))
    return render(request, ws, "partials/memory_edit.html", memory=memory)


@router.get("/memories/{memory_id}/detail", response_class=HTMLResponse)
def memory_detail(request: Request, memory_id: str, ws: Workspace = Depends(load_workspace)):
    try:
        memory = memories_api.get(memory_id, ws.organization_id, ws.user_id, ws.db)
        versions = memories_api.memory_versions(memory_id, ws.organization_id, ws.user_id, ws.db)
        audits = memories_api.memory_audit(memory_id, ws.organization_id, ws.user_id, ws.db)
    except HTTPException as exc:
        return render(request, ws, "partials/notice.html", kind="error", message=error_message(exc))
    return render(request, ws, "partials/memory_detail.html", memory=memory, versions=versions, audits=audits)


@router.get("/inspector", response_class=HTMLResponse)
def inspector_page(request: Request, ws: Workspace = Depends(load_workspace)):
    debug = None
    conversation_id = ws.latest_conversation_id()
    if conversation_id:
        try:
            trace = memories_api.context_trace(conversation_id, ws.organization_id, ws.user_id, ws.db)
            if trace.query:
                debug = trace
        except HTTPException:
            debug = None
    return render(request, ws, "inspector.html", page="inspector", debug=debug)


KNOWLEDGE_DEFAULTS = {
    "memory_type": "semantic",
    "subject": "",
    "content": "",
    "scope": "connection",
    "visibility": "personal",
    "importance": "8",
    "auto_approve": False,
}


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request, ws: Workspace = Depends(load_workspace)):
    return render(request, ws, "knowledge.html", page="knowledge", form=KNOWLEDGE_DEFAULTS)


@router.post("/knowledge", response_class=HTMLResponse)
def knowledge_submit(
    request: Request,
    memory_type: str = Form("semantic"),
    subject: str = Form(""),
    content: str = Form(""),
    scope: str = Form("connection"),
    visibility: str = Form("personal"),
    importance: str = Form("8"),
    auto_approve: Optional[str] = Form(None),
    ws: Workspace = Depends(load_workspace),
):
    form = {
        "memory_type": memory_type,
        "subject": subject,
        "content": content,
        "scope": scope,
        "visibility": visibility,
        "importance": importance,
        "auto_approve": bool(auto_approve),
    }
    try:
        memories_api.create(
            MemoryCreate(
                memory_type=memory_type,
                subject=subject.strip(),
                content=content.strip(),
                scope=scope,
                visibility=visibility,
                importance=int(importance or 8),
                confidence=0.98,
                connection_id=ws.connection_id,
                auto_approve=bool(auto_approve),
            ),
            ws.organization_id,
            ws.user_id,
            ws.db,
        )
    except (HTTPException, ValidationError, ValueError) as exc:
        return render(request, ws, "partials/knowledge_form.html", form=form, error=error_message(exc))
    if auto_approve:
        return redirect(request, "/memories?status=approved&notice=activated")
    return redirect(request, "/memories?status=pending&notice=queued")


@router.get("/evaluation", response_class=HTMLResponse)
def evaluation_page(request: Request, category: str = Query(""), ws: Workspace = Depends(load_workspace)):
    return render(
        request,
        ws,
        "evaluation.html",
        page="evaluation",
        data=memories_api.evaluation_results(category or None),
        category=category,
    )


@router.get("/evaluation/body", response_class=HTMLResponse)
def evaluation_body(request: Request, category: str = Query("")):
    return render(
        request,
        None,
        "partials/evaluation_body.html",
        data=memories_api.evaluation_results(category or None),
        category=category,
    )


@router.post("/evaluation/run", response_class=HTMLResponse)
def evaluation_run(request: Request):
    error = None
    try:
        data = memories_api.run_evaluation()
    except Exception as exc:
        error = f"Benchmark failed: {error_message(exc)}"
        data = memories_api.evaluation_results(None)
    return render(request, None, "partials/evaluation_body.html", data=data, category="", error=error)
