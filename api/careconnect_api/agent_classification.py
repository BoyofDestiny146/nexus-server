"""Classify care clients and virtual agents from stored records.

Both kinds of record live in ``ai_agent``. A device-free row is not assumed
to be a virtual agent. The binding table is the relationship: ``client_id``
is the care client, ``virtual_agent_id`` is the attached agent. An unbound
software agent in the Portal AI Agents organization remains a virtual agent
so it can be attached. A row that is already the client side of a binding
stays a client.

No agent id is special-cased. The BridgingCare compatibility setting is read
from ``settings.sam_client_id`` by the model status helper, not compared here.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .client_profile import dump_profile, load_profile
from .models import AiAgent, CcVirtualAgentBinding
from .settings import settings

# Organization used by Create Client for a software agent. Not an agent id.
PORTAL_AI_AGENTS_ORGANIZATION_ID = "d51af0ec95ac4b4a949f8d5b64cdd30a"

ASK_SAM_MODEL_KEY = "askSamModel"
_MODEL_TAG = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def is_virtual_agent(
    *,
    organization_id: str | None,
    device_count: int,
    bound_as_virtual_agent: bool,
    bound_as_client: bool,
) -> bool:
    """True when this record is managed as a virtual agent.

    A Watcher always stays a care client. A row attached as ``virtual_agent_id``
    is a virtual agent. A row that only appears as ``client_id`` is the care
    client, even when it has no Watcher. Otherwise only an unbound member of
    the software-agent organization qualifies.
    """
    if device_count != 0:
        return False
    if bound_as_virtual_agent:
        return True
    if bound_as_client:
        return False
    return organization_id == PORTAL_AI_AGENTS_ORGANIZATION_ID


async def binding_membership(
    db: AsyncSession, agent_ids: list[str]
) -> tuple[set[str], set[str]]:
    """Return ids used as virtual agents and ids used as care clients."""
    if not agent_ids:
        return set(), set()
    virtual_rows = (
        await db.execute(
            select(CcVirtualAgentBinding.virtual_agent_id).where(
                CcVirtualAgentBinding.virtual_agent_id.in_(agent_ids)
            )
        )
    ).scalars().all()
    client_rows = (
        await db.execute(
            select(CcVirtualAgentBinding.client_id).where(
                CcVirtualAgentBinding.client_id.in_(agent_ids)
            )
        )
    ).scalars().all()
    return {str(item) for item in virtual_rows if item}, {str(item) for item in client_rows if item}


async def primary_bound_client(
    db: AsyncSession, virtual_agent_ids: list[str]
) -> dict[str, tuple[str, str]]:
    """Map each virtual agent to its earliest bound care client (id, agent_name).

    Used by the Virtual Agents roster Client column. The virtual agent's own
    ``agent_name`` is never returned here.
    """
    ids = [item for item in virtual_agent_ids if item]
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                CcVirtualAgentBinding.virtual_agent_id,
                CcVirtualAgentBinding.client_id,
                AiAgent.agent_name,
            )
            .join(AiAgent, AiAgent.id == CcVirtualAgentBinding.client_id)
            .where(CcVirtualAgentBinding.virtual_agent_id.in_(ids))
            .order_by(
                CcVirtualAgentBinding.virtual_agent_id.asc(),
                CcVirtualAgentBinding.created_at.asc(),
                CcVirtualAgentBinding.client_id.asc(),
            )
        )
    ).all()
    bound: dict[str, tuple[str, str]] = {}
    for virtual_agent_id, client_id, agent_name in rows:
        key = str(virtual_agent_id)
        if key in bound:
            continue
        name = (agent_name or "").strip() or str(client_id)
        bound[key] = (str(client_id), name)
    return bound


def stored_ask_sam_model(profile_json: Any) -> str | None:
    value = load_profile(profile_json).get(ASK_SAM_MODEL_KEY)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def configured_integration_agent_id() -> str:
    """Integration agent selected by the existing server setting.

    The value is configuration. This does not name a client or an agent.
    An empty setting fails closed at the conversation route.
    """
    return (settings.sam_client_id or "").strip()


def conversation_model_for_profile(profile_json: Any) -> str:
    """Saved conversational tag, or the server default when none is stored.

    A missing tag does not select another installed model.
    """
    stored = stored_ask_sam_model(profile_json)
    if stored:
        return stored
    return (settings.llm_model or "").strip()


def write_ask_sam_model(profile_json: Any, model: str | None) -> str:
    """Store or clear the conversational tag. Other profile keys stay."""
    loaded = load_profile(profile_json)
    if model:
        loaded[ASK_SAM_MODEL_KEY] = model
    else:
        loaded.pop(ASK_SAM_MODEL_KEY, None)
    return dump_profile(loaded)


def normalize_model_tag(value: str | None) -> str | None:
    """Return a cleared selection, or a syntactically valid tag.

    ``None`` means the stored selection is cleared. Callers still have to
    check the tag against the live Ollama list before saving.
    """
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    if _MODEL_TAG.fullmatch(text) is None:
        raise ValueError("invalid model")
    return text


def exact_model_tag(names: list[str], target: str) -> bool:
    """True only when Ollama listed this exact tag."""
    want = target.strip()
    if want == "":
        return False
    return any((name or "").strip() == want for name in names)


def ask_sam_model_status(agent_id: str, profile_json: Any) -> dict[str, Any]:
    """Describe the saved tag and what Ask Sam actually calls.

    The conversation route still uses the server model, and only for the
    agent selected by ``CC_SAM_CLIENT_ID``. A saved tag is not reported as
    the serving model.
    """
    stored = stored_ask_sam_model(profile_json)
    server = (settings.llm_model or "").strip() or None
    configured = configured_integration_agent_id()
    active = configured != "" and agent_id == configured
    return {
        "storedModel": stored,
        "serverModel": server,
        "effectiveModel": (stored or server) if active else None,
        "isActiveIntegrationAgent": active,
    }


def tag_roles(name: str) -> list[str]:
    """Mark server, triage, and embedding tags. Other tags stay available."""
    from .routers.health import model_is_listed

    roles: list[str] = []
    if model_is_listed([name], settings.llm_model or ""):
        roles.append("server-default")
    if model_is_listed([name], settings.ollama_triage_model or ""):
        roles.append("triage")
    if model_is_listed([name], settings.ollama_embed_model or ""):
        roles.append("embedding")
    if not roles:
        roles.append("available")
    return roles
