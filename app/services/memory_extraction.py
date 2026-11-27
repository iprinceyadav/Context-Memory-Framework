"""
Memory Extraction Engine for the Context Memory Framework.

Uses the configured LLM provider + Pydantic validation (with resilient
rule-based detection for explicit definitions/corrections/temporary overrides)
to extract semantic, episodic, and procedural memory candidates.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError
from app.schemas.memory import ExtractedMemory, MemoryExtractionResult
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT_TEMPLATE = """You are a precision Context Memory Extraction engine for an enterprise NL2SQL database assistant.

Analyze the conversation turns below and extract ONLY durable, useful memory candidates supported directly by the user's statements.

Memory categories allowed for `type`:
- "semantic": Stable business definitions, metric calculation formulas, business rules, table/column terminology (e.g., "Revenue excludes cancelled orders", "Clients table represents customers").
- "procedural": Persistent instructions/preferences about how to run or format future queries (e.g., "Always use invoice_date for financial reports", "Display monetary results in INR", "Always show SQL first").
- "episodic": Notable analytical events or query corrections worth remembering across sessions (e.g., "User explored top 10 vendors by outstanding payment").

STRICT RULES:
1. Extract ONLY information explicitly stated or confirmed by the user. NEVER invent definitions or infer business rules solely from assistant-generated SQL.
2. Ignore greetings, small talk, and casual filler.
3. NEVER store raw database query result rows, sensitive values, or credentials.
4. Distinguish ONE-QUERY TEMPORARY OVERRIDES ("for this report only, include cancelled orders", "just this once") from permanent rules -> DO NOT extract temporary one-query overrides as permanent semantic/procedural memories!
5. If the user explicitly states a permanent change/correction ("Our revenue definition has changed...", "Actually, always exclude refunded orders too", "From now on..."), extract it and set `is_correction: true` and `supersedes_subject` if applicable.
6. Set `scope` to one of: "personal", "connection", "organization". Default to "connection" for database-specific business rules/columns, "personal" for personal formatting preferences.
7. Set `visibility` to "personal" (unless user explicitly says it is a shared/team/organization rule).
8. `confidence` is float 0.0-1.0, `importance` is int 1-10.

Conversation messages:
{messages_text}

Return ONLY valid JSON matching this exact structure (no markdown fences, no commentary):
{{
  "memories": [
    {{
      "type": "semantic",
      "subject": "revenue",
      "content": "Revenue is invoice amount excluding cancelled invoices.",
      "scope": "connection",
      "visibility": "personal",
      "confidence": 0.95,
      "importance": 9,
      "is_correction": false
    }}
  ]
}}
If there are no meaningful memories to extract, return {{"memories": []}}.
"""

_TEMP_OVERRIDE_PATTERNS = (
    r"\bfor this (?:particular )?(?:report|query|request|time|once)\b",
    r"\bjust this once\b",
    r"\bonly for this\b",
    r"\bthis time only\b",
    r"\btemporarily\b",
)

_PERMANENT_CORRECTION_PATTERNS = (
    r"\bdefinition has changed\b",
    r"\bfrom now on\b",
    r"\binstead of\b.*\balways\b",
    r"\bnow exclude\b",
    r"\bupdate the rule\b",
    r"\bpermanently\b",
)


def is_temporary_override(text: str) -> bool:
    """Detect whether a user statement is a one-query temporary override."""
    lower = text.lower()
    return any(re.search(pat, lower) for pat in _TEMP_OVERRIDE_PATTERNS)


def is_permanent_correction(text: str) -> bool:
    """Detect whether a user statement explicitly declares a permanent rule correction."""
    lower = text.lower()
    return any(re.search(pat, lower) for pat in _PERMANENT_CORRECTION_PATTERNS)


def _clean_json_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


class MemoryExtractionService:
    """Extracts structured memory candidates from user interactions."""

    def extract_memories(
        self,
        conversation_messages: List[Dict[str, Any]],
        user_id: str,
        organization_id: str,
        connection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extract and validate memory candidates using LLM + Pydantic.

        Never raises on extraction failure — returns [] and logs warning so
        the primary NL2SQL workflow remains unaffected.
        """
        if not conversation_messages:
            return []

        user_texts = [
            m.get("content", "")
            for m in conversation_messages
            if m.get("role") == "user" and m.get("content", "").strip()
        ]
        if not user_texts:
            return []

        latest_user_msg = user_texts[-1]
        # Guard: if the user's latest message is clearly a temporary one-query override,
        # skip extracting a permanent rule from it.
        if is_temporary_override(latest_user_msg) and not is_permanent_correction(latest_user_msg):
            logger.info("Detected one-query temporary override; skipping permanent rule extraction.")
            return []

        formatted_msgs = "\n".join(
            f"{m.get('role', 'user').upper()}: {m.get('content', '')}"
            for m in conversation_messages
            if m.get("content")
        )
        prompt = _EXTRACTION_PROMPT_TEMPLATE.format(messages_text=formatted_msgs)

        try:
            raw_output = llm_service.generate_with_context(prompt)
            cleaned = _clean_json_text(raw_output)
            parsed = json.loads(cleaned)
        except Exception as exc:
            logger.warning("LLM memory extraction failed gracefully: %s", exc)
            return []

        raw_memories = parsed.get("memories", []) if isinstance(parsed, dict) else []
        validated_list: List[Dict[str, Any]] = []
        correction_detected = is_permanent_correction(latest_user_msg)

        for item in raw_memories:
            if not isinstance(item, dict):
                continue
            try:
                # Normalize fields before Pydantic validation
                m_type = str(item.get("type", "semantic")).lower()
                if m_type not in {"semantic", "episodic", "procedural"}:
                    m_type = "semantic"
                scope = str(item.get("scope", "connection" if connection_id else "personal")).lower()
                if scope not in {"personal", "connection", "organization"}:
                    scope = "personal"
                visibility = str(item.get("visibility", "personal")).lower()
                if visibility not in {"personal", "shared"}:
                    visibility = "personal"

                candidate = ExtractedMemory(
                    type=m_type,
                    subject=str(item.get("subject") or "general")[:120],
                    content=str(item.get("content") or "").strip(),
                    scope=scope,
                    visibility=visibility,
                    confidence=float(item.get("confidence", 0.8)),
                    importance=int(item.get("importance", 5)),
                )
                if not candidate.content or len(candidate.content) < 5:
                    continue

                entry = candidate.model_dump()
                entry["is_correction"] = bool(item.get("is_correction", False)) or correction_detected
                validated_list.append(entry)
            except (ValidationError, ValueError, TypeError) as ve:
                logger.debug("Skipping invalid extracted memory item %s: %s", item, ve)

        # Validate overall container
        try:
            MemoryExtractionResult(
                memories=[ExtractedMemory(**{k: v for k, v in m.items() if k != "is_correction"}) for m in validated_list]
            )
        except ValidationError as ve:
            logger.warning("MemoryExtractionResult validation error: %s", ve)
            return []

        return validated_list


memory_extraction_service = MemoryExtractionService()

