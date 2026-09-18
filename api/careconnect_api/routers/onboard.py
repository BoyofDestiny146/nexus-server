"""Client onboarding + device-attach write endpoints.

Mounted at /api by main.py. These power the multi-step "Add Client" wizard
in the Next.js dashboard, which replaces the manual ``scripts/add_client.py``
CLI flow. Column defaults here mirror that script so a wizard-created client
is indistinguishable from a CLI-created one at the DB layer.

Endpoints
---------

* ``POST /api/agent/onboard``  — wizard "Create Client" submit. All-or-nothing
  insert of ai_agent (+ optional ai_device + cc_admin_client_access).
* ``POST /api/device/attach``  — standalone Watcher attach for an existing
  client. Idempotent if the EUI is already bound to the same agent;
  ``force=true`` allows re-binding from another client.
* ``DELETE /api/device/{deviceId}`` — unbind + hard-delete a Watcher row.
* ``GET /api/onboard/template``    — empty wizard template (defaults).

RBAC
----

* Onboard is open to any authenticated user. If a non-root admin creates a
  client we auto-grant them an ``cc_admin_client_access`` row so they can
  see the client they just made (otherwise the scoped-list filter would
  hide it from them immediately).
* Device attach + delete check ``assert_can_access_agent`` against the agent
  the device is (or will be) bound to.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from ..auth import ROLE_ROOT, CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..models import (
    AdminClientAccess,
    AiAgent,
    AiAgentChatHistory,
    AiDevice,
    AiMedicalAssessment,
)
from ..rbac import assert_can_access_agent
from ..settings import settings


log = logging.getLogger("onboard")


router = APIRouter(tags=["onboard"])


# ---------- request / response models ----------

DeviceType = Literal["W1-A", "W1-B"]
FirmwareType = Literal["sensecraft", "xiaozhi"]


def _validate_device_combo(device_type: DeviceType | None, firmware_type: FirmwareType | None) -> None:
    """Cross-field validation for the device_type / firmware_type pair.

    Rules:
      * W1-B is always SenseCraft (Seeed only sells it that way) — reject
        the (W1-B, xiaozhi) combination outright.
      * The XiaoZhi option is gated by the feature flag
        ``settings.xiaozhi_ingest_enabled`` until the xiaozhi-server ingest
        path is verified end-to-end. Reject with 400 if the caller asks for
        ``xiaozhi`` while the flag is off.
    """
    if device_type == "W1-B" and firmware_type == "xiaozhi":
        raise APIException(
            400,
            "device_type=W1-B is incompatible with firmware_type=xiaozhi "
            "(Seeed ships W1-B only with SenseCraft factory firmware)",
        )
    if firmware_type == "xiaozhi" and not settings.xiaozhi_ingest_enabled:
        raise APIException(
            400,
            "firmware_type=xiaozhi is disabled on this server "
            "(set CC_XIAOZHI_INGEST_ENABLED=true to re-enable)",
        )


class OnboardRequest(BaseModel):
    name: str
    dob: str | None = None
    age: int | None = None
    condition: str | None = None
    tags: list[str] = Field(default_factory=list)
    escalationPhrases: list[str] = Field(default_factory=list)
    topicsToAvoid: list[str] = Field(default_factory=list)
    personaOverride: str | None = None
    # Optional device attach in same call (the wizard's last step)
    eui: str | None = None
    deviceAlias: str | None = None
    clientDeviceId: str | None = None  # optional external id from the client's system
    deviceType: DeviceType | None = None
    firmwareType: FirmwareType | None = None

    @model_validator(mode="after")
    def _check_device_combo(self) -> "OnboardRequest":
        _validate_device_combo(self.deviceType, self.firmwareType)
        return self


class DeviceAttachRequest(BaseModel):
    agentId: str
    eui: str
    alias: str | None = None
    clientDeviceId: str | None = None  # optional external id from the client's system
    force: bool = False
    deviceType: DeviceType | None = None
    firmwareType: FirmwareType | None = None

    @model_validator(mode="after")
    def _check_device_combo(self) -> "DeviceAttachRequest":
        _validate_device_combo(self.deviceType, self.firmwareType)
        return self


# ---------- helpers ----------

# Accepts either contiguous hex (e.g. ``2CF7F1C972900104``) or colon/dash
# separated MACs (e.g. ``D8:3A:DD:00:00:01``). After normalization we accept
# 12 hex (6-byte MAC) or 16 hex (8-byte EUI-64).
_EUI_HEX_RE = re.compile(r"^[0-9A-F]+$")


def _normalize_eui(raw: str) -> str:
    """Uppercase + strip separators. Raise 400 if not 12 or 16 hex chars."""
    if raw is None:
        raise APIException(400, "eui is required")
    cleaned = raw.strip().upper().replace(":", "").replace("-", "").replace(" ", "")
    if not cleaned:
        raise APIException(400, "eui is empty")
    if not _EUI_HEX_RE.match(cleaned):
        raise APIException(400, f"eui contains non-hex characters: {raw!r}")
    if len(cleaned) not in (12, 16):
        raise APIException(
            400,
            f"eui must be 12 (MAC) or 16 (EUI-64) hex chars after stripping separators, got {len(cleaned)}",
        )
    return cleaned


def _device_id_for(eui: str) -> str:
    """Mirror add_client.py's id convention: ``watcher-<lowercase-eui[:24]>``."""
    return f"watcher-{eui.lower()[:24]}"


# careconnect: the deployed voice (Piper) is English-only. Pin EVERY persona to
# English so a slip into Chinese/other scripts can never reach the speaker. The
# marker "ENGLISH-ONLY" is also used by the DB re-pin migration to detect which
# existing agents still need this prepended.
_ENGLISH_ONLY_PIN = (
    "# ENGLISH-ONLY (ABSOLUTE RULE)\n"
    "ALWAYS reply in English. Never reply in Chinese, Japanese, Korean, or any "
    "other language or script, even if the user writes in another language. "
    "Do not use emoji or non-Latin characters. This rule overrides everything "
    "below."
)


def _build_persona_prompt(req: OnboardRequest) -> str:
    """Compose the agent's ``system_prompt`` from wizard fields.

    The bridge currently ignores per-agent ``system_prompt`` (Phase 2 deferred —
    the global caregiver persona in xiaozhi-server's ``data/.config.yaml`` is
    what actually frames every chat). We still write a useful prompt now so
    that when per-agent personas land we don't need to backfill anything.

    Section legend:
      * ``# Client context``         — name + condition + tags (clinical hints)
      * ``# Escalation triggers``     — phrases that should bump risk_level
      * ``# Topics to avoid``         — soft content guard for the model
      * ``# Operator override``       — verbatim ``personaOverride`` body if set
    """
    if req.personaOverride and req.personaOverride.strip():
        # When an override is supplied it wins outright — operator knows best.
        # We still annotate the file so it's obvious downstream that this was
        # a wizard-authored override (vs a hand-edited ai_agent.system_prompt).
        return (
            f"{_ENGLISH_ONLY_PIN}\n\n"
            "# Operator override (custom persona)\n"
            f"# Client: {req.name}\n\n"
            f"{req.personaOverride.strip()}\n"
        )

    # Every generated persona starts with the absolute English-only pin so no
    # onboard can ever produce an agent that the (English-only) Piper voice
    # would garble.
    parts: list[str] = [_ENGLISH_ONLY_PIN]

    # Client context
    ctx_lines = [f"Client name: {req.name}."]
    if req.condition and req.condition.strip():
        ctx_lines.append(f"Clinical context: {req.condition.strip()}")
    if req.tags:
        ctx_lines.append("Tags: " + ", ".join(t.strip() for t in req.tags if t.strip()))
    parts.append("# Client context\n" + "\n".join(ctx_lines))

    # Escalation triggers
    if req.escalationPhrases:
        phrases = "\n".join(f"- {p.strip()}" for p in req.escalationPhrases if p.strip())
        parts.append(
            "# Escalation triggers\n"
            "If the client mentions any of the following, raise the assessment "
            "risk_level and surface a clear concern in your reply:\n"
            f"{phrases}"
        )

    # Topics to avoid
    if req.topicsToAvoid:
        topics = "\n".join(f"- {t.strip()}" for t in req.topicsToAvoid if t.strip())
        parts.append(
            "# Topics to avoid\n"
            "Steer the conversation away from these topics; do not volunteer them:\n"
            f"{topics}"
        )

    # Always-on caregiver framing tail so a minimal wizard input still gets a
    # complete prompt (the bridge will eventually merge this with the global
    # persona, but the per-agent block has to stand on its own too).
    parts.append(
        "# Caregiver framing\n"
        "You are a soothing, plain-language companion for an elderly client. "
        "Keep replies short. Confirm what you heard before recommending action. "
        "If anything indicates a medical emergency, recommend calling for help."
    )

    return "\n\n".join(parts) + "\n"


def _device_response(dev: AiDevice, *, previous_agent_id: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "deviceId": dev.id,
        "eui": dev.mac_address,
        "clientDeviceId": dev.client_device_id,
        "agentId": dev.agent_id,
        "alias": dev.alias,
        "deviceType": dev.device_type,
        "firmwareType": dev.firmware_type,
    }
    if previous_agent_id is not None:
        out["previousAgentId"] = previous_agent_id
    return out


# ---------- endpoints ----------

@router.post("/agent/onboard", response_model=None)
async def onboard_agent(
    payload: OnboardRequest,
    force: bool = Query(False, description="Allow re-binding the EUI if already attached elsewhere"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a client (ai_agent), optionally bind a Watcher (ai_device), and
    grant the creating admin access (cc_admin_client_access). Single
    transaction — if the device insert fails the agent insert is rolled back
    so we don't leak orphan agents into the dashboard."""
    name = (payload.name or "").strip()
    if not name:
        raise APIException(400, "name is required")

    # Pre-validate EUI before opening the transaction so we 400 early.
    normalized_eui: str | None = None
    if payload.eui:
        normalized_eui = _normalize_eui(payload.eui)

    agent_id = uuid.uuid4().hex
    agent_code = f"AGT_{int(time.time() * 1000)}"
    system_prompt = _build_persona_prompt(payload)

    # Single all-or-nothing unit of work. AsyncSession auto-begins on first
    # use, so we just commit at the end and rely on get_db() / the request
    # scope to roll back if we raise. Using `async with db.begin()` here
    # collides with the session's autobegun transaction.
    device_id: str | None = None
    previous_agent_id: str | None = None

    try:
        # 1. Insert agent. Match add_client.py's column defaults exactly.
        agent = AiAgent(
            id=agent_id,
            user_id=user.id,
            agent_code=agent_code,
            agent_name=name,
            asr_model_id="ASR_FunASR",
            vad_model_id="VAD_SileroVAD",
            llm_model_id="LLM_OllamaLLM",
            vllm_model_id="VLLM_ChatGLMVLLM",
            tts_model_id="TTS_CustomTTS",
            mem_model_id="Memory_mem_local_vector",
            intent_model_id="Intent_nointent",
            system_prompt=system_prompt,
            chat_history_conf=1,
            lang_code="en",
            language="English",
            sort=0,
            creator=user.id,
            created_at=func.now(),
            updated_at=func.now(),
        )
        db.add(agent)
        await db.flush()

        # 2. Optional Watcher bind.
        if normalized_eui is not None:
            existing = (
                await db.execute(
                    select(AiDevice).where(AiDevice.mac_address == normalized_eui)
                )
            ).scalar_one_or_none()

            if existing is not None:
                if existing.agent_id and existing.agent_id != agent_id and not force:
                    # Note the conflict before the rollback fires from the raise.
                    raise APIException(
                        409,
                        f"eui {normalized_eui} is already bound to agent "
                        f"{existing.agent_id}; pass ?force=true to re-bind",
                        data={"existingAgentId": existing.agent_id, "deviceId": existing.id},
                    )
                # Same agent (vanishingly unlikely on a fresh uuid) or force
                # re-bind: update the existing row.
                previous_agent_id = existing.agent_id if existing.agent_id != agent_id else None
                existing.agent_id = agent_id
                existing.alias = payload.deviceAlias or f"{name}'s Watcher"
                if payload.clientDeviceId is not None:
                    existing.client_device_id = payload.clientDeviceId
                existing.user_id = user.id
                existing.updater = user.id
                existing.update_date = func.now()
                device_id = existing.id
            else:
                device_id = _device_id_for(normalized_eui)
                device = AiDevice(
                    id=device_id,
                    user_id=user.id,
                    mac_address=normalized_eui,
                    agent_id=agent_id,
                    alias=payload.deviceAlias or f"{name}'s Watcher",
                    client_device_id=payload.clientDeviceId,
                    board="sensecap_watcher",
                    device_type=payload.deviceType or "W1-A",
                    firmware_type=payload.firmwareType or "xiaozhi",
                    sort=0,
                    creator=user.id,
                    create_date=func.now(),
                    update_date=func.now(),
                )
                db.add(device)
                await db.flush()

        # 3. Auto-grant scope to non-root creators so they can see the client
        #    they just made (root ignores cc_admin_client_access entirely).
        if user.role != ROLE_ROOT:
            db.add(AdminClientAccess(
                admin_user_id=user.id,
                agent_id=agent_id,
                granted_by=user.id,
            ))
            await db.flush()

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    out: dict[str, Any] = {"agentId": agent_id}
    if device_id is not None:
        out["deviceId"] = device_id
    if previous_agent_id is not None:
        out["previousAgentId"] = previous_agent_id
    return out


@router.post("/device/attach", response_model=None)
async def attach_device(
    payload: DeviceAttachRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Bind a Watcher EUI to an existing client.

    Idempotent: re-attaching the same EUI to the same agent updates the
    alias (if provided) and returns the existing row. ``force=true`` is
    required to re-bind from a different agent.
    """
    await assert_can_access_agent(db, user, payload.agentId)

    eui = _normalize_eui(payload.eui)

    # Make sure the target agent actually exists — otherwise we'd happily
    # create a device that points at nothing.
    agent_exists = (
        await db.execute(select(AiAgent.id).where(AiAgent.id == payload.agentId))
    ).scalar_one_or_none()
    if agent_exists is None:
        raise APIException(404, f"agent {payload.agentId} not found")

    existing = (
        await db.execute(select(AiDevice).where(AiDevice.mac_address == eui))
    ).scalar_one_or_none()

    previous_agent_id: str | None = None
    try:
        if existing is not None:
            if existing.agent_id == payload.agentId:
                # Idempotent same-agent attach — update alias / client id if asked.
                if payload.alias is not None or payload.clientDeviceId is not None:
                    if payload.alias is not None:
                        existing.alias = payload.alias
                    if payload.clientDeviceId is not None:
                        existing.client_device_id = payload.clientDeviceId
                    existing.updater = user.id
                    existing.update_date = func.now()
                    await db.commit()
                return _device_response(existing)

            # Different agent. RBAC: also confirm the user can act on the
            # currently-bound agent (so a scoped admin can't yank a Watcher
            # from a client they can't see). force=true is still required.
            if not payload.force:
                raise APIException(
                    409,
                    f"eui {eui} is already bound to agent {existing.agent_id}; "
                    "pass force=true to re-bind",
                    data={"existingAgentId": existing.agent_id, "deviceId": existing.id},
                )
            if existing.agent_id:
                await assert_can_access_agent(db, user, existing.agent_id)
            previous_agent_id = existing.agent_id
            existing.agent_id = payload.agentId
            if payload.alias is not None:
                existing.alias = payload.alias
            if payload.clientDeviceId is not None:
                existing.client_device_id = payload.clientDeviceId
            existing.user_id = user.id
            existing.updater = user.id
            existing.update_date = func.now()
            device = existing
        else:
            device = AiDevice(
                id=_device_id_for(eui),
                user_id=user.id,
                mac_address=eui,
                agent_id=payload.agentId,
                alias=payload.alias,
                client_device_id=payload.clientDeviceId,
                board="sensecap_watcher",
                device_type=payload.deviceType or "W1-A",
                firmware_type=payload.firmwareType or "xiaozhi",
                sort=0,
                creator=user.id,
                create_date=func.now(),
                update_date=func.now(),
            )
            db.add(device)
            await db.flush()

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return _device_response(device, previous_agent_id=previous_agent_id)


@router.delete("/device/{device_id}", response_model=None)
async def detach_device(
    device_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Hard-delete an ai_device row (no soft-delete column today).

    RBAC is checked against whichever agent the device is currently bound
    to. Unbound devices are deletable by any authenticated user (there's
    nothing client-scoped to gate on).
    """
    device = (
        await db.execute(select(AiDevice).where(AiDevice.id == device_id))
    ).scalar_one_or_none()
    if device is None:
        raise APIException(404, f"device {device_id} not found")

    if device.agent_id:
        await assert_can_access_agent(db, user, device.agent_id)

    try:
        await db.execute(delete(AiDevice).where(AiDevice.id == device_id))
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "deviceId": device_id,
        "eui": device.mac_address,
        "agentId": device.agent_id,
        "deleted": True,
    }


@router.delete("/agent/{agent_id}", response_model=None)
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_root),
) -> dict[str, Any]:
    """Hard-delete a client (root only). Cascades:

      • cc_admin_client_access  — removes RBAC grants pointing at this agent
      • ai_medical_assessment    — removes triage rows
      • ai_agent_chat_history    — removes conversation rows
      • ai_device                — UNBINDS (sets agent_id=NULL) so the
                                   physical Watcher row survives and can be
                                   re-attached to another client
      • ai_agent                 — finally, deletes the client row itself

    All in one transaction. Devices intentionally survive — losing the
    physical hardware binding when a client row is removed would be
    surprising.
    """
    agent = (
        await db.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise APIException(404, f"client {agent_id} not found")

    name = agent.agent_name or "(unnamed)"

    try:
        # 1. count what we're touching, for the response payload
        device_count = (
            await db.execute(
                select(func.count()).select_from(AiDevice).where(AiDevice.agent_id == agent_id)
            )
        ).scalar_one() or 0
        chat_count = (
            await db.execute(
                select(func.count()).select_from(AiAgentChatHistory)
                .where(AiAgentChatHistory.agent_id == agent_id)
            )
        ).scalar_one() or 0
        assessment_count = (
            await db.execute(
                select(func.count()).select_from(AiMedicalAssessment)
                .where(AiMedicalAssessment.agent_id == agent_id)
            )
        ).scalar_one() or 0

        # 2. cascade deletes (RBAC grants, triage, history)
        await db.execute(
            delete(AdminClientAccess).where(AdminClientAccess.agent_id == agent_id)
        )
        await db.execute(
            delete(AiMedicalAssessment).where(AiMedicalAssessment.agent_id == agent_id)
        )
        await db.execute(
            delete(AiAgentChatHistory).where(AiAgentChatHistory.agent_id == agent_id)
        )

        # 3. unbind devices (don't delete — keep the physical Watcher rows)
        if device_count > 0:
            from sqlalchemy import update as _update
            await db.execute(
                _update(AiDevice).where(AiDevice.agent_id == agent_id).values(agent_id=None)
            )

        # 4. finally, delete the agent itself
        await db.execute(delete(AiAgent).where(AiAgent.id == agent_id))
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "agentId": agent_id,
        "agentName": name,
        "deleted": True,
        "cascade": {
            "devicesUnbound": device_count,
            "chatRowsDeleted": chat_count,
            "assessmentsDeleted": assessment_count,
        },
    }


@router.get("/onboard/template", response_model=None)
async def onboard_template(
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Empty defaults for the wizard form. The Next.js client fetches this
    once on mount rather than hard-coding the field shape so changes here
    don't require a frontend deploy."""
    return {
        "name": "",
        "dob": None,
        "age": None,
        "condition": "",
        "tags": [],
        "escalationPhrases": [
            "I fell",
            "chest pain",
            "I can't breathe",
            "I'm dizzy",
        ],
        "topicsToAvoid": [],
        "personaOverride": None,
        "eui": None,
        "deviceAlias": None,
        "clientDeviceId": None,
        # Hints for the wizard — server-side authoritative so the UI doesn't
        # accept inputs the API will reject.
        "_hints": {
            "tagSuggestions": [
                "fall-risk",
                "mobility-aid",
                "low-sodium",
                "exercise-recommended",
                "diabetic",
                "memory-care",
                "post-op",
            ],
            "euiPattern": "12 or 16 hex chars (separators :/-/space allowed)",
        },
    }


# ---------- AI-assist: draft guardrails ----------

class DraftGuardrailsRequest(BaseModel):
    name: str
    age: int | None = None
    condition: str | None = None
    tags: list[str] = Field(default_factory=list)


class ClientPatchRequest(BaseModel):
    name: str | None = None
    systemPrompt: str | None = None


_GUARDRAIL_FALLBACK = {
    "personaOverride": "",
    "escalationPhrases": ["I need help", "Call my family"],
    "topicsToAvoid": ["medical diagnosis", "prescription advice"],
}


_GUARDRAIL_SYSTEM_PROMPT = (
    "You are an assistant that helps clinicians configure a soothing AI "
    "companion for an elderly client. Given a client's name, age, "
    "condition, and tags, produce a JSON object with three fields:\n"
    "  - personaOverride: 3-5 warm, plain-English sentences addressing the "
    "named client. Mention their condition gently if provided. Set the tone "
    "as calm, unhurried, and reassuring. No medical advice.\n"
    "  - escalationPhrases: a list of 3-6 short phrases (each <= 8 words) that, "
    "if the client says them, should trigger a caregiver alert. Tailor to "
    "the condition (e.g. fall-risk clients should include fall-related cues).\n"
    "  - topicsToAvoid: a list of 2-5 short topic labels the companion should "
    "steer away from (e.g. 'medical diagnosis', 'prescription dosage', "
    "'end-of-life decisions').\n"
    "Return ONLY a JSON object with exactly those three keys. No prose, no "
    "markdown fences."
)


def _build_guardrail_user_prompt(req: DraftGuardrailsRequest) -> str:
    parts = [f"Client name: {req.name}"]
    if req.age is not None:
        parts.append(f"Age: {req.age}")
    if req.condition and req.condition.strip():
        parts.append(f"Condition: {req.condition.strip()}")
    if req.tags:
        cleaned = [t.strip() for t in req.tags if t and t.strip()]
        if cleaned:
            parts.append("Tags: " + ", ".join(cleaned))
    return "\n".join(parts)


def _coerce_guardrail_payload(parsed: Any) -> dict[str, Any]:
    """Normalize the LLM's parsed JSON into the response shape, falling back
    to defaults for any missing/invalid field."""
    if not isinstance(parsed, dict):
        return dict(_GUARDRAIL_FALLBACK)

    persona = parsed.get("personaOverride") or parsed.get("persona_override") or ""
    if not isinstance(persona, str):
        persona = ""

    def _coerce_list(v: Any, default: list[str]) -> list[str]:
        if isinstance(v, list):
            out = [str(x).strip() for x in v if x is not None and str(x).strip()]
            return out or list(default)
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return list(default)

    return {
        "personaOverride": persona.strip(),
        "escalationPhrases": _coerce_list(
            parsed.get("escalationPhrases") or parsed.get("escalation_phrases"),
            _GUARDRAIL_FALLBACK["escalationPhrases"],
        ),
        "topicsToAvoid": _coerce_list(
            parsed.get("topicsToAvoid") or parsed.get("topics_to_avoid"),
            _GUARDRAIL_FALLBACK["topicsToAvoid"],
        ),
    }


@router.post("/agent/draft-guardrails", response_model=None)
async def draft_guardrails(
    payload: DraftGuardrailsRequest,
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate a draft persona override + escalation phrases + topics to avoid
    via Ollama qwen2.5:7b. Used by the wizard's AI-assist button.

    On any LLM error or parse failure, returns a sensible static default so
    the wizard never blocks on a flaky model — the operator can always edit
    the fields by hand."""
    body = {
        "model": settings.ollama_triage_model,
        "messages": [
            {"role": "system", "content": _GUARDRAIL_SYSTEM_PROMPT},
            {"role": "user", "content": _build_guardrail_user_prompt(payload)},
        ],
        "format": "json",
        "stream": False,
        "options": {"num_predict": 600, "temperature": 0.4},
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(settings.ollama_url + "/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("message") or {}).get("content") or ""
        if not content.strip():
            log.warning("draft-guardrails: empty Ollama content; using fallback")
            return dict(_GUARDRAIL_FALLBACK)
        # Tolerate the model wrapping its JSON in stray prose.
        first = content.find("{")
        last = content.rfind("}")
        if first < 0 or last <= first:
            log.warning("draft-guardrails: no JSON object in Ollama reply; fallback")
            return dict(_GUARDRAIL_FALLBACK)
        parsed = json.loads(content[first : last + 1])
        return _coerce_guardrail_payload(parsed)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        log.warning("draft-guardrails: Ollama call failed (%s); using fallback", exc)
        return dict(_GUARDRAIL_FALLBACK)


# ---------- PATCH /agent/{agent_id} — Edit Client modal ----------

@router.patch("/agent/{agent_id}", response_model=None)
async def patch_agent(
    agent_id: str,
    payload: ClientPatchRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update a subset of fields on ai_agent. Currently supports renaming the
    client and overriding the system prompt verbatim. RBAC-checked: scoped
    admins can only edit clients they have access to."""
    await assert_can_access_agent(db, user, agent_id)

    name = payload.name.strip() if payload.name is not None else None
    system_prompt = payload.systemPrompt if payload.systemPrompt is not None else None

    if not name and system_prompt is None:
        raise APIException(400, "at least one of name or systemPrompt is required")
    if payload.name is not None and not name:
        raise APIException(400, "name cannot be blank")

    agent = (
        await db.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")

    fields_changed: list[str] = []
    if name is not None and name != agent.agent_name:
        agent.agent_name = name
        fields_changed.append("name")
    if system_prompt is not None and system_prompt != agent.system_prompt:
        agent.system_prompt = system_prompt
        fields_changed.append("systemPrompt")

    if fields_changed:
        agent.updater = user.id
        agent.updated_at = func.now()
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "agentId": agent_id,
        "updated": bool(fields_changed),
        "fields": fields_changed,
    }
