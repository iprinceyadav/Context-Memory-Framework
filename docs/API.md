# API Documentation — Context Memory Framework

## Context-Aware Chat & Conversations

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/chat/ask` | Context-aware NL2SQL chat (retrieves memories, validates SQL, executes, logs QueryMemory, extracts candidate memories in background) |
| `GET` | `/api/chat/conversations` | List conversations for `organization_id` & `user_id` |
| `POST` | `/api/chat/conversations` | Create a new conversation |
| `GET` | `/api/chat/conversations/{conversation_id}/messages` | Get message history for a conversation |

## Memory Management & Lifecycle

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/memories` | List memories (filter by `memory_type`, `status`, `connection_id`) |
| `POST` | `/api/memories` | Create a memory manually (with optional `auto_approve`) |
| `GET` | `/api/memories/{memory_id}` | Get memory details |
| `PATCH` | `/api/memories/{memory_id}` | Update memory (preserves `MemoryVersion` history) |
| `DELETE` | `/api/memories/{memory_id}` | Delete memory and clear derived embedding |
| `POST` | `/api/memories/{memory_id}/approve` | Approve a pending memory |
| `POST` | `/api/memories/{memory_id}/reject` | Reject a pending memory |
| `POST` | `/api/memories/{memory_id}/supersede` | Supersede an outdated memory with a new definition |
| `POST` | `/api/memories/search` | Hybrid semantic + keyword + recency + importance + type search |
| `GET` | `/api/memories/{memory_id}/versions` | Inspect version history |
| `GET` | `/api/memories/{memory_id}/audit` | Inspect audit trail |

## Memory Debugging & Analytics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/memory/debug/{conversation_id}` | Inspect retrieved/excluded memories, individual scores, and token usage for latest turn |
| `GET` | `/api/memories/statistics` / `/api/memory/statistics` | Memory counts by status and category |

## Preserved Legacy Endpoints

- `/api/health`, `/api/organizations/`, `/api/users/`, `/api/databases/*` (including `/api/databases/ask`) remain unchanged and backward-compatible.

