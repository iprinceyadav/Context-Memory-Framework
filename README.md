# Context Memory Framework (CMF) — Context-Aware NL2SQL Assistant

An upgraded enterprise Natural Language to SQL (NL2SQL) platform featuring a modular **Persistent Context Memory and Context Engineering Architecture**.

## Key Capabilities

1. **Four-Category Memory Architecture**
   - **Semantic Memory**: Business definitions, metric formulas, table/column conventions.
   - **Episodic Memory**: Past analytical interactions, query intent, and sanitized SQL templates.
   - **Procedural Memory**: User and database-specific instructions (e.g., date columns, currency, formatting).
   - **Working Memory**: Multi-turn conversation history and structured `QueryMemory` for follow-up queries.
2. **Hybrid Ranked Retrieval (`S = 0.45V + 0.20K + 0.15R + 0.10I + 0.10T`)**
   - Combines `all-MiniLM-L6-v2` embeddings, keyword overlap, exponential recency decay, importance, and query-intent type relevance.
   - Strict **authorization-first** filtering (organization/tenant, user/visibility, connection scope) before ranking.
3. **Memory Extraction & Human-in-the-Loop Governance**
   - Non-blocking background extraction with Pydantic validation (`ExtractedMemory`).
   - Extracted memories enter `pending` review until approved; one-query temporary overrides are never promoted into permanent rules.
   - Full versioning (`MemoryVersion`), superseding, expiration, deletion (clearing derived embeddings), and audit trails (`MemoryAuditLog`).
4. **Context Engineering & SQL Safety**
   - Token-budgeted `ContextBuilder` with explicit instruction priority hierarchy and untrusted-data framing.
   - `sqlglot`-powered SQL planning: every statement is classified as a read or a single-table change and checked against the live schema.
5. **Guardrails for Viewing and Changing Data**
   - **Reads**: one statement, read-only transaction that is always rolled back, capped at `MAX_QUERY_ROWS`, and sensitive columns (`SENSITIVE_COLUMNS`) masked for non-admins.
   - **Changes**: on first visit you pick a role. An **Admin** can ask for an update, insert or delete; the chat shows a preview of the rows that will change, and nothing runs until the admin clicks **Apply**. **Finance**, **Sales** and **Operations** can request specific kinds of change, which wait in the admin's **Requests** page. **HR** and **Employee** are read-only.
   - On approval the matching rows are counted again; if the data moved, nothing runs. The statement executes in a transaction that must touch exactly the previewed rows, otherwise it rolls back.
   - Never allowed: schema changes or admin commands, updates/deletes without `WHERE`, multiple statements, joins in changes, or more than `WRITE_MAX_ROWS` rows. `ALLOW_DATA_CHANGES=False` turns changes off.
   - Without sign-in the user type is chosen on the landing page, so these rules are not yet a security boundary.
6. **Plain-Language Insights**
   - Answers are written for non-technical employees: a direct answer, up to three insights, column statistics, the business definitions applied, the previous question, and a note when results were cut off or masked.
7. **Web UI (server-rendered)**
   - FastAPI page routes ([app/web/](app/web/)) render Jinja2 templates ([app/templates/](app/templates/)); htmx handles partial updates such as asking a question or approving a request. Static CSS/JS are served from [app/static/](app/static/) at `/static`.
   - SQLite databases can be connected by file path or by uploading a `.db` file (for example one created with Python's `sqlite3`); uploads are stored in `UPLOAD_DIR` (default `./uploads`).
   - Context-Aware SQL Assistant with collapsible **Context** panel & conversation history.
   - **Memory Dashboard**, **Memory Details & Version History Modal**, **Context Inspector**, and **Business Knowledge Manager**.

## Quick Start

1. **Configure Environment**
   ```bash
   cp .env.example .env
   # Add your LLM_API_KEY in .env
   ```
2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run Server**
   ```bash
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```
   Or on Windows: double-click `run.bat` and open `http://127.0.0.1:8000`.

## Running Tests & Evaluation

```bash
# Run full automated test suite (functional, security, failure-handling, E2E demos)
pytest -v tests/

# Run 120-case retrieval & context benchmark
python tests/run_evaluation.py
```

## Documentation

- [Architecture Documentation](docs/ARCHITECTURE.md)
- [API Documentation](docs/API.md)

