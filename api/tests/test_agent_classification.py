"""Client versus virtual-agent classification. No production ids."""
from careconnect_api.agent_classification import (
    PORTAL_AI_AGENTS_ORGANIZATION_ID,
    ask_sam_model_status,
    conversation_model_for_profile,
    exact_model_tag,
    is_virtual_agent,
    normalize_model_tag,
    stored_ask_sam_model,
    tag_roles,
    write_ask_sam_model,
)
from careconnect_api.client_profile import effective_profile
from careconnect_api.settings import settings


OTHER_ORG = "other-org"


def test_binding_role_separates_a_client_from_its_virtual_agent():
    client = is_virtual_agent(
        organization_id=PORTAL_AI_AGENTS_ORGANIZATION_ID,
        device_count=0,
        bound_as_virtual_agent=False,
        bound_as_client=True,
    )
    agent = is_virtual_agent(
        organization_id=PORTAL_AI_AGENTS_ORGANIZATION_ID,
        device_count=0,
        bound_as_virtual_agent=True,
        bound_as_client=False,
    )
    unbound = is_virtual_agent(
        organization_id=PORTAL_AI_AGENTS_ORGANIZATION_ID,
        device_count=0,
        bound_as_virtual_agent=False,
        bound_as_client=False,
    )
    watcher = is_virtual_agent(
        organization_id=PORTAL_AI_AGENTS_ORGANIZATION_ID,
        device_count=1,
        bound_as_virtual_agent=False,
        bound_as_client=False,
    )
    elsewhere = is_virtual_agent(
        organization_id=OTHER_ORG,
        device_count=0,
        bound_as_virtual_agent=False,
        bound_as_client=False,
    )
    assert client is False
    assert agent is True
    assert unbound is True
    assert watcher is False
    assert elsewhere is False


def test_model_status_uses_the_server_setting_and_does_not_serve_a_saved_tag(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "server-model")
    monkeypatch.setattr(settings, "sam_client_id", "active-agent")
    stored = write_ask_sam_model(None, "saved-model")
    active = ask_sam_model_status("active-agent", stored)
    other = ask_sam_model_status("other-agent", stored)
    inherited = ask_sam_model_status("active-agent", None)
    assert active["storedModel"] == "saved-model"
    assert active["serverModel"] == "server-model"
    assert active["effectiveModel"] == "saved-model"
    assert active["isActiveIntegrationAgent"] is True
    assert inherited["storedModel"] is None
    assert inherited["effectiveModel"] == "server-model"
    assert other["storedModel"] == "saved-model"
    assert other["effectiveModel"] is None
    assert other["isActiveIntegrationAgent"] is False
    assert conversation_model_for_profile(stored) == "saved-model"
    assert conversation_model_for_profile(None) == "server-model"
    assert conversation_model_for_profile(write_ask_sam_model(stored, None)) == "server-model"
    assert stored_ask_sam_model(write_ask_sam_model(stored, None)) is None


def test_clearing_a_model_keeps_the_rest_of_the_profile():
    original = write_ask_sam_model(
        {"condition": "steady", "personalityId": "sys_witty_tech_sidekick"},
        "saved-model",
    )
    cleared = write_ask_sam_model(original, None)
    assert stored_ask_sam_model(cleared) is None
    assert "steady" in cleared
    assert "sys_witty_tech_sidekick" in cleared


def test_saved_model_survives_prompt_recovery():
    stored = write_ask_sam_model(None, "saved-model")
    recovered = effective_profile(stored, "Be brief.")
    assert recovered["askSamModel"] == "saved-model"
    assert recovered["personaOverride"] == "Be brief."


def test_tag_validation_is_exact_and_roles_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "server-model")
    monkeypatch.setattr(settings, "ollama_triage_model", "triage-model")
    monkeypatch.setattr(settings, "ollama_embed_model", "embed-model")
    names = ["server-model", "triage-model", "embed-model", "other-model"]
    assert exact_model_tag(names, "other-model") is True
    assert exact_model_tag(names, "server") is False
    assert normalize_model_tag("  ") is None
    assert normalize_model_tag("other-model") == "other-model"
    try:
        normalize_model_tag("has space")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid tag was accepted")
    assert tag_roles("server-model") == ["server-default"]
    assert tag_roles("triage-model") == ["triage"]
    assert tag_roles("embed-model") == ["embedding"]
    assert tag_roles("other-model") == ["available"]
