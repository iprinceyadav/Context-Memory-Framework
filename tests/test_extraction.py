from unittest.mock import patch
from app.services.extraction import (
    MemoryExtractor,
    is_rule_change,
    is_override,
)


def test_override_not_extracted():
    extractor = MemoryExtractor()
    with patch("app.services.extraction.llm.generate") as mock_llm:
        extracted = extractor.extract(
            conversation_messages=[
                {"role": "user", "content": "For this particular report, include cancelled orders too."}
            ],
            user_id="user-a1",
            organization_id="org-a",
        )
        assert extracted == []
        mock_llm.assert_not_called()


def test_extracts_business_definition():
    extractor = MemoryExtractor()
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
    with patch("app.services.extraction.llm.generate", return_value=mock_json):
        extracted = extractor.extract(
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


def test_rule_change_detected():
    assert is_rule_change("Our revenue definition has changed. Now exclude both cancelled and refunded orders.")
    assert is_override("Just this once, include cancelled orders.")
