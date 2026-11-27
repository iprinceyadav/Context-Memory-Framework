# API Documentation — Context Memory Framework

## Context-Aware Chat & Conversations

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/chat/ask` | Context-aware chat. Reads are answered (`outcome: answer`, `truncated`, `hidden_columns`); change requests return `outcome: change_requested` with `change_request`, or `not_allowed` / `no_match` with the reason |
| `GET` | `/api/chat/conversations` | List conversations for `organization_id` & `user_id` |
| `POST` | `/api/chat/conversations` | Create a new conversation |
| `GET` | `/api/chat/conversations/{conversation_id}/messages` | Get message history for a conversation |

## Team, Permissions & Approvals

All take `organization_id` and `user_id` query parameters. Admin means role `owner` or `admin`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/team/members` | List members of the organization |
| `POST` | `/api/team/members` | Add a member (admin) |
| `PATCH` | `/api/team/members/{member_id}` | Change a member's role (admin) |
| `GET` | `/api/team/grants` | Active permissions (admins see all, others their own) |
| `POST` | `/api/team/grants` | Allow a member to request `update` / `insert` / `delete` on a table or `*` (admin) |
| `DELETE` | `/api/team/grants/{grant_id}` | Revoke a permission (admin) |
| `GET` | `/api/changes` | Change requests (admins see all, others their own); `status` filter |
| `GET` | `/api/changes/{request_id}` | One change request |
| `POST` | `/api/changes/{request_id}/approve` | Re-check and apply a pending change (admin) |
| `POST` | `/api/changes/{request_id}/reject` | Reject a pending change with an optional `note` (admin) |

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

- `/api/health`, `/api/organizations/`, `/api/users/`, `/api/databases/*` remain. `/api/databases/query-direct` and `/api/databases/ask` are now read-only and mask sensitive columns; `/api/databases/connect` reuses an existing saved connection for the same database.

