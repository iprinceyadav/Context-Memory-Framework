# Architecture Documentation — Context Memory Framework

## System Architecture

```
User Question
   │
   ▼
[FastAPI /api/chat/ask] ──► Auth & Tenant Verification (app/core/auth.py)
   │
   ├─► Load Conversation & Working Memory (Conversation, Message, QueryMemory)
   ├─► Load Live Schema (DatabaseManager / DatabaseSchema)
   ├─► Hybrid Memory Retrieval (MemoryRetrievalService + EmbeddingService)
   │       └─ Filters FIRST: organization_id, user_id/visibility, connection_id, status=approved, valid_from/valid_to
   ├─► Context Engineering (ContextBuilder)
   │       └─ Priority Hierarchy + Token Budget + Untrusted Context Framing + Source IDs
   │
   ▼
[LLMService.generate_with_context] ──► SQL Safety Validation (sqlglot read-only + live schema)
   │
   ▼
[DatabaseManager.execute_direct_query] ──► External User DB (PostgreSQL / MySQL / SQLite)
   │
   ▼
[LLMService.generate_natural_response] ──► Save Message & QueryMemory
   │
   └─► Background Task: MemoryExtractionService ──► MemoryService (pending queue + dedup + contradiction check)
```

## Instruction Priority Hierarchy

1. System security & read-only SQL rules
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

