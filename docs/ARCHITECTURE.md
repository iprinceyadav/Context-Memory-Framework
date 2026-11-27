# Architecture Documentation — Context Memory Framework

## System Architecture

```
User Question
   │
   ▼
[FastAPI /api/chat/ask] ──► Auth & Tenant Verification (app/core/auth.py)
   │
   ├─► Load Conversation & Working Memory (Conversation, Message, QueryMemory)
   ├─► Load Live Schema (DatabaseConnector / DatabaseSchema)
   ├─► Hybrid Memory Retrieval (MemoryRetriever + Embedder)
   │       └─ Filters FIRST: organization_id, user_id/visibility, connection_id, status=approved, valid_from/valid_to
   ├─► Context Engineering (ContextBuilder)
   │       └─ Priority Hierarchy + Token Budget + Untrusted Context Framing + Source IDs
   │
   ▼
[LLMClient.generate] ──► SQL Safety Validation (sqlglot read-only + live schema)
   │
   ▼
[DatabaseConnector.execute] ──► External User DB (PostgreSQL / MySQL / SQLite)
   │
   ▼
[LLMClient.summarize] ──► Save Message & QueryMemory
   │
   └─► Background Task: MemoryExtractor ──► MemoryStore (pending queue + dedup + contradiction check)
```

## Instruction Priority Hierarchy

1. System security rules: read-only by default; a data change is one approved statement on one table
2. Current live database schema and database permissions
3. Current explicit user instruction for the current query (one-time overrides)
4. Confirmed database-specific business definitions (Semantic Memories)
5. User & database preferences (Procedural Memories)
6. Recent conversation & previous analytical task context (Working / Episodic Memory)

## Database Models (Internal Platform DB)

- `organizations`, `users`, `database_connections`, `database_schemas` (preserved)
- `conversations`, `messages` (working memory & provenance)
- `memories`, `memory_versions`, `memory_audit_logs` (long-term memory lifecycle)
- `query_memories` (structured follow-up analytical context)
- `access_grants` (which member may request which change on which table)
- `change_requests` (requested changes, their preview, review and outcome — the audit trail for data changes)

## Data Change Flow

```
Chat question ──► generated SQL ──► plan_sql (read / update / insert / delete, schema-checked)
   ├─ read  ──► read-only transaction (rolled back), row cap, sensitive columns masked ──► insights
   └─ write ──► permission check (admin, or AccessGrant for action + table)
                 └─► preview: count + current rows ──► ChangeRequest (pending)
                       └─► admin approves ──► recount (must match) ──► transaction that must touch exactly N rows
```

