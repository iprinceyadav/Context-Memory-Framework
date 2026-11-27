from unittest.mock import patch
from app.services.memory_extraction import (
    MemoryExtractionService,
    is_permanent_correction,
    is_temporary_override,
)


def test_temporary_override_not_extracted_as_permanent_rule():
    svc = MemoryExtractionService()
    with patch("app.services.memory_extraction.llm_service.generate_with_context") as mock_llm:
        extracted = svc.extract_memories(
            conversation_messages=[
                {"role": "user", "content": "For this particular report, include cancelled orders too."}
            ],
            user_id="user-a1",
            organization_id="org-a",
        )
        assert extracted == []
        mock_llm.assert_not_called()


def test_extract_valid_business_definition_with_pydantic():
    svc = MemoryExtractionService()
    mock_json = """```json
    {
      "memories": [
        {
          "type": "semantic",
          "subject": "revenue",
          "content": "Exclude cancelled orders from all revenue reports.",
          "scope": "connection",
          "visibility": "personal",
          "confidence": 0.96,
          "importance": 9
        }
      ]
    }
    ```"""
    with patch("app.services.memory_extraction.llm_service.generate_with_context", return_value=mock_json):
        extracted = svc.extract_memories(
            conversation_messages=[
                {"role": "user", "content": "For all revenue reports, exclude cancelled orders."}
            ],
            user_id="user-a1",
            organization_id="org-a",
            connection_id="conn-a1",
        )
    assert len(extracted) == 1
    assert extracted[0]["type"] == "semantic"
    assert extracted[0]["subject"] == "revenue"
    assert extracted[0]["confidence"] == 0.96


def test_permanent_correction_flag_detected():
    assert is_permanent_correction("Our revenue definition has changed. Now exclude both cancelled and refunded orders.")
    assert is_temporary_override("Just this once, include cancelled orders.")

