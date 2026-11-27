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
   - `sqlglot`-powered read-only SQL & live-schema validation prior to execution.
5. **UI Extensions**
   - Context-Aware SQL Assistant with collapsible **Context Used** panel & conversation history.
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

