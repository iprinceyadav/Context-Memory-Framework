# Context Memory Framework (CMF)

Academic project: a context-aware Natural Language → SQL assistant. Employees ask questions about a relational database in plain English; the system turns them into safe SQL, runs them, and explains the result. Its research contribution is a **persistent, governed context-memory layer** plus **context engineering** that sits between the user and the LLM.

## Problem and motivation

A plain NL2SQL setup (schema + question → LLM → SQL) breaks down in an organization because:

1. **Business knowledge is not in the schema.** "Revenue excludes cancelled orders" or "use `invoice_date` for financial reports" live in people's heads, so the model guesses and users repeat themselves every session.
2. **It is stateless.** Follow-ups like "now only the top 5" or "same for last month" lose the previous query.
3. **Large schemas don't fit.** Sending every table wastes tokens and dilutes the prompt.
4. **LLM-written SQL against a live database is risky.** Writes, DDL, multi-statement payloads, sensitive columns, and cross-tenant leakage all need hard guards, and stored memories are themselves a prompt-injection surface.

CMF's answer: remember business context across sessions (with human review), retrieve only what is relevant and authorized, assemble it into a prioritized, token-budgeted prompt, and put every generated statement through a deterministic SQL guard.

## End-to-end flow (`POST /api/chat/ask`, [app/api/chat.py](app/api/chat.py))

1. **Identify** org + user ([app/core/auth.py](app/core/auth.py)); resolve the target DB from a saved `connection_id` or inline credentials.
2. **Working memory**: create/load the `Conversation`, store the user `Message`.
3. **Schema**: load the cached schema by connection fingerprint (SHA-256 of type/user/host/port/db), or introspect it with SQLAlchemy (columns, FK `references`, sampled text values) — [app/services/connector.py](app/services/connector.py).
4. **Context build** ([app/services/context_builder.py](app/services/context_builder.py)): retrieve memories, pick schema tables, add recent turns and the last `QueryMemory`, fit everything into `MEMORY_TOKEN_BUDGET`. If this fails, falls back to a schema-only prompt.
5. **Generate SQL** with the LLM ([app/services/llm.py](app/services/llm.py)).
6. **Plan/guard** with `sqlglot` ([app/services/sql_guard.py](app/services/sql_guard.py)): classify as read or single-table change, validate against the live schema.
7. **Read** → read-only transaction that is always rolled back, capped at `MAX_QUERY_ROWS`, sensitive columns masked for non-admins → LLM writes a plain-language answer (falls back to `describe_results` stats).
   **Write** → permission check → preview → `ChangeRequest` (pending admin approval) ([app/services/changes.py](app/services/changes.py)).
8. **Persist** assistant message, `QueryMemory` (literal-masked SQL template + structured metadata) for follow-ups.
9. **Background extraction** ([app/services/extraction.py](app/services/extraction.py)): LLM extracts candidate memories from the turn → `MemoryStore.ingest` saves them as `pending`, superseding a conflicting memory when the user states a correction.

## Memory model ([app/models/memory.py](app/models/memory.py), [app/services/memory_store.py](app/services/memory_store.py))

| Category | Stored as | Example |
|---|---|---|
| Semantic | `memory_type="semantic"` | "Revenue excludes cancelled orders" |
| Procedural | `memory_type="procedural"` | "Display monetary results in INR" |
| Episodic | `memory_type="episodic"` | "User explored top 10 vendors by outstanding payment" |
| Working | `Conversation`/`Message` + `QueryMemory` | previous question, SQL template, filters, sort, limit |

- Each memory has `scope` (`personal` / `connection` / `organization`), `visibility` (`personal` / `shared`), `importance`, `confidence`, `valid_from`/`valid_to`, `schema_hash`, and source message/conversation ids.
- Lifecycle `status`: `pending` → `approved` / `rejected`; later `superseded`, `expired`, `deleted`. Only `approved` memories reach the prompt (superseded ones only when the question asks about history).
- Every edit writes a `MemoryVersion`; every action writes a `MemoryAuditLog`.
- One-time overrides ("just this once", "for this report") are detected by regex and **never** stored as permanent rules unless the message also signals a rule change ("from now on", "permanently").

## Retrieval ([app/services/retrieval.py](app/services/retrieval.py))

Authorization filters are applied **before** ranking: same organization, own or `shared` memories, matching connection (or non-connection-scoped).

1. **Candidates**: Qdrant hybrid search (dense MiniLM embedding + sparse BM25-style hashed terms, fused with RRF) with the same auth filters in the payload filter — [app/services/vector_store.py](app/services/vector_store.py), [app/services/text.py](app/services/text.py). If Qdrant is unavailable it falls back to scanning authorized rows in SQL.
2. **First-stage score**: `S = 0.45·vector + 0.20·keyword + 0.15·recency + 0.10·importance + 0.10·type` (weights in `.env`; recency = 30-day half-life; type = keyword→memory-type intent map; with no embedding the vector weight moves to keyword).
3. **Rerank**: cross-encoder `ms-marco-MiniLM-L-6-v2` ([app/services/reranker.py](app/services/reranker.py)); final = `RERANK_WEIGHT·rerank + (1−RERANK_WEIGHT)·S`. Skipped if the model can't load.
4. Every excluded memory is recorded with a reason (`pending_approval`, `superseded`, `below_top_k_cutoff`, `token_budget_exceeded`, …) and returned in `context_debug` for the Context Inspector.

## Context engineering

Prompt sections, in priority order (stated explicitly in the system section):
1. Security rules (read-only by default; a change is one approved statement on one table)
2. Live schema (authoritative over memories)
3. The current question's explicit instructions (one-time overrides)
4. Semantic memories → 5. Procedural memories → 6. Episodic / recent conversation / previous analytical context

Retrieved memories are framed as **untrusted data**, not instructions. Schemas with more than `RAG_SCHEMA_FULL_TABLES` tables are reduced to the top `RAG_SCHEMA_TOP_TABLES` (vector or keyword match, reranked) plus FK-referenced tables and tables from the previous query. Token counting is approximate (`len(text) // 4`).

## Safety guardrails

- **Reads**: exactly one `SELECT`/`UNION`; any write node or write keyword rejects it; tables must exist in the live schema.
- **Changes** (`UPDATE`/`INSERT`/`DELETE`): one table, no joins/`FROM`/`USING`, `UPDATE`/`DELETE` require `WHERE`, columns must exist, at most `WRITE_MAX_ROWS` rows. DDL and admin commands are always refused. `ALLOW_DATA_CHANGES=False` disables changes.
- **Permissions** ([app/services/access.py](app/services/access.py)): roles `owner`/`admin` (admin), `analyst` (contributor), `viewer`. Non-admins need an `AccessGrant` for the action + table (or `*`).
- **Approval**: an admin approves → the matching rows are recounted; if the count changed nothing runs; the write runs in a transaction that must affect exactly the previewed row count, otherwise it rolls back.
- **Masking**: columns matching `SENSITIVE_COLUMNS` are masked for non-admins.

## Tech stack

Python 3.10+, FastAPI, SQLAlchemy 2 (platform DB: SQLite `cmf_platform.db` by default; target DBs: PostgreSQL / MySQL / SQLite), Pydantic v2 + pydantic-settings, sqlglot, sentence-transformers (`all-MiniLM-L6-v2`, cross-encoder reranker), Qdrant (`qdrant-client`, embedded at `./vector_store` unless `VECTOR_STORE_URL` is set), Google Gemini via `google-generativeai`, pytest. The frontend is server-rendered: Jinja2 templates plus htmx (2.0.3 from unpkg) for partial updates, served by FastAPI page routes in [app/web/](app/web/).

## Layout

```
app/
  main.py          FastAPI app, JSON routers under /api, page router at /, static files at /static
  core/            config.py (all settings), auth.py (org/user/role checks)
  api/             chat, memories (+ /evaluation), databases (legacy read-only ask/query-direct, connect, sync-schema),
                   access (team, grants, change requests), organizations, users, health
  models/          SQLAlchemy models (memory, conversation, query_memory, access, ...)
  schemas/         Pydantic request/response models
  services/        pipeline logic (see sections above)
  database/        engine/session; migrations.py = create_all + small ALTERs (no Alembic)
  web/             pages.py (HTML routes + htmx fragments), workspace.py (user type/connection/conversation from cookies), templating.py (Jinja env, filters)
  templates/       one page per tab (chat, memories, inspector, knowledge, evaluation, landing, connect); partials/ for htmx fragments
  static/          css/style.css, js/app.js (panel toggle, Enter-to-send, copy SQL, modal close only)
tests/             pytest suite + run_evaluation.py benchmark
docs/              ARCHITECTURE.md, API.md
```

## Commands (Windows dev machine)

```powershell
copy .env.example .env          # then set LLM_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000   # or run.bat (creates venv)
pytest -v tests/
python tests/run_evaluation.py  # writes tests/evaluation_results.json
```

## Testing conventions

- [tests/conftest.py](tests/conftest.py) autouse fixtures replace the embedder with a 64-dim hash embedder, the reranker with a token-overlap scorer, and Qdrant with `:memory:`; DB is in-memory SQLite. Tests need no model downloads.
- LLM calls are patched (`patch("app.services.llm.llm.generate", ...)`); tests never need an API key. Keep it that way.
- `sentence-transformers` (torch) is only imported lazily, so the suite runs without it installed.
- RAG wiring (examples, schema vector search, follow-up rewrite, startup rebuild) is covered in [tests/test_rag_pipeline.py](tests/test_rag_pipeline.py).
- Fixtures provide two orgs, users with different roles, and two connections — use them for isolation tests.

## Evaluation (be accurate when reporting it)

[tests/run_evaluation.py](tests/run_evaluation.py) builds 120 synthetic cases across 9 categories (exact/semantic fact recall, business rules, cross-session, follow-ups, temporal, conflicting memories, unrelated-memory exclusion, user isolation) and uses the real MiniLM embedder. It measures retrieval Precision@3, Recall@3, MRR and latency. **`rule_aware_sql_accuracy` does not execute an LLM**: it checks whether the target memory text appears in the built prompt (the baseline is a string heuristic). Describe it as context-inclusion accuracy, not SQL accuracy. Results in `tests/evaluation_results.json` predate the Qdrant/reranker changes.

## Known limitations / bottlenecks

- **No real authentication**: identity is the `organization_id`/`user_id` sent with each request; `default-org`/`default-user` are auto-created as owner. The user type chosen on the landing page (stored in the `cmf_user` cookie) is a demo, not a security boundary.
- **Connection passwords are base64-encoded**, not encrypted ([app/services/encryption.py](app/services/encryption.py)).
- **LLM is Gemini-only**. `LLM_PROVIDER`, `ENABLE_LOCAL_LLM`, `MAX_RETRIES`, `ENFORCE_READ_ONLY_SQL`, `MEMORY_DEFAULT_STATUS` exist in config but nothing reads them.
- **Latency**: up to three LLM calls per question (SQL, summary, background extraction) plus lazy-loaded embedding and cross-encoder models on first use.
- Without Qdrant, retrieval scans every authorized memory row (linear in memory count).
- Embeddings are stored twice: JSON in `memories.embedding` and in Qdrant.
- CORS is `*`; no schema migration tool.

## RAG index ([app/services/indexer.py](app/services/indexer.py))

Three Qdrant collections:
- `memories`: synced on every `MemoryStore` change.
- `schema_tables`: one point per table, written on schema sync.
- `query_examples`: each successfully answered question with its literal-masked SQL, written at the end of `/api/chat/ask`. They are retrieved per user and schema and shown in the prompt as "SIMILAR PAST QUESTIONS".

On startup the app lifespan ([app/main.py](app/main.py)) runs `ensure_index`, which rebuilds any collection whose count doesn't match the platform DB. With `RAG_REWRITE_QUERY=True`, a follow-up question (starts with "now/only/also…", says "what about", refers to "those/same/previous", or has ≤2 content words) is retrieved with the previous question prepended (`text.follow_up_query`). Only retrieval changes; the prompt keeps the user's exact words.

## Web frontend ([app/web/pages.py](app/web/pages.py))

- Page routes call the `/api` handler functions directly (e.g. `chat_api.ask`, `access_api.approve_change`), so auth, guardrails and approval logic stay in one place. Do not change the `/api` JSON API for the UI's sake; tests use it.
- Onboarding: the landing page asks for a role (`USER_TYPES` in [app/web/workspace.py](app/web/workspace.py)). **Admin** maps to `default-user` (owner): change requests from chat show a preview with Apply/Discard buttons (`/changes/{id}/approve|reject` → the same `access_api` approval path). Department roles map to `default-<key>` users: **Finance** (analyst, `update` grant), **Sales** (analyst, `insert`+`update`), **Operations** (analyst, all three), **HR** and **Employee** (viewer, read-only). `ensure_user_type` creates the user and syncs its `*`-table grants on every switch. Department change requests stay pending and appear in the admin-only **Requests** page (`/requests`). Non-admins get sensitive columns masked. Workspace pages redirect to `/` until a role is chosen. The Approvals and Team pages were removed from the UI; the `/api` access endpoints still exist.
- UI copy is deliberately minimal (short labels, no explanatory subtitles); fonts are Geist / Geist Mono from Google Fonts.
- State lives in cookies: `cmf_user` (chosen user type's user id), `cmf_connection` (saved connection id), `cmf_conversation` (last conversation, used by the Context Inspector).
- htmx requests get HTML fragments; out-of-band swaps (`hx-swap-oob`) refresh the context panel, conversation list and nav badges. Redirects after htmx posts use the `HX-Redirect` header.
- SQLite files can be uploaded on the connect page (`POST /connect/upload`): the file must start with the SQLite header and be at most 50 MB; it is saved under `UPLOAD_DIR` with a random suffix and then connected like any SQLite path.
- Page tests are in [tests/test_pages.py](tests/test_pages.py).

## Working rules

- Keep the security order intact: authorization filters before ranking; `plan_sql` before any execution; reads always rolled back; changes only through `ChangeService`.
- Never let memory content become instructions; keep the untrusted-data framing in prompts.
- New settings go in [app/core/config.py](app/core/config.py) and [.env.example](.env.example).
- Code style in this repo: descriptive names, no explanatory comments in code (removed deliberately in the clean-naming refactor).
- Update [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) / [docs/API.md](docs/API.md) when behavior or endpoints change.
