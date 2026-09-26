from knowledge_service.qdrant import scoped_filter
from fastapi import HTTPException
import pytest


def test_scoped_filter_requires_knowledge_base_ids():
    with pytest.raises(HTTPException) as exc:
        scoped_filter([])
    assert exc.value.status_code == 400
    assert "never global" in exc.value.detail


def test_scoped_filter_includes_enabled_and_optional_client():
    filt = scoped_filter([3, 8], source_ids=[11], client_id="agent-1")
    must = filt["must"]
    assert {"key": "knowledgeBaseId", "match": {"any": [3, 8]}} in must
    assert {"key": "enabled", "match": {"value": True}} in must
    assert {"key": "sourceId", "match": {"any": [11]}} in must
    assert {"key": "clientId", "match": {"value": "agent-1"}} in must
