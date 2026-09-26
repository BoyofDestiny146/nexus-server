"""Knowledge → Revel action decision. Default deny. Never scans LLM or document text.

`revelTag` is metadata until this gate passes. Does not call apply_device_tags.
Keyword phrase matching stays in revel_command.evaluate_voice_command.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .knowledge import resolve_device_knowledge_context
from .knowledge_retrieval import device_knowledge_search
from .models import (
    ClientIntegration,
    ClientKnowledgeBase,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeSource,
    KnowledgeTopic,
)
from .revel_command import PROVIDER_REVEL, evaluate_configured_tag
from .revel_config import load_meta
from .settings import settings

log = logging.getLogger("knowledge_revel")


def knowledge_revel_enabled() -> bool:
    return bool(getattr(settings, "knowledge_revel_enabled", False))


def _tag(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _score(hit: dict[str, Any]) -> float:
    raw = hit.get("score")
    if raw is None:
        raw = hit.get("vectorScore")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def action_for_tag(
    actions: list[dict[str, Any]] | None, tag: str
) -> dict[str, Any] | None:
    """Exact stripped revelTag match. Does not inspect phrases or document text."""
    target = _tag(tag)
    if not target:
        return None
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if _tag(action.get("revelTag")) == target:
            return action
    return None


def _decision(
    *,
    execute: bool,
    reason: str,
    revel_tag: str | None = None,
    topic_id: Any = None,
    score: float | None = None,
    auto_trigger: bool | None = None,
    configured: bool = False,
    enabled: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "execute": bool(execute),
        "executed": False,
        "revelTag": revel_tag or None,
        "reason": reason,
        "source": "knowledge",
        "topicId": topic_id,
        "score": score,
        "autoTrigger": bool(auto_trigger) if auto_trigger is not None else False,
        "configured": bool(configured),
        "enabled": bool(enabled),
    }
    if extra:
        out.update(extra)
    return out


def resolve_knowledge_revel(
    hits: list[dict[str, Any]] | None,
    actions: list[dict[str, Any]] | None,
    *,
    min_score: float = 0.35,
    already_executed_tag: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Deterministic resolver. Does not read hit text or assistant text."""
    if not enabled:
        return _decision(execute=False, reason="feature_disabled")
    rows = [row for row in (hits or []) if isinstance(row, dict)]
    if not rows:
        return _decision(execute=False, reason="no_results")
    already = _tag(already_executed_tag)
    last_deny: dict[str, Any] | None = None
    for hit in rows:
        tag = _tag(hit.get("revelTag"))
        score = _score(hit)
        auto = bool(hit.get("revelAutoTrigger"))
        topic_id = hit.get("topicId")
        action = action_for_tag(actions, tag)
        configured = action is not None
        action_enabled = bool(action.get("enabled", True)) if action is not None else False
        deny_kwargs = dict(
            revel_tag=tag or None,
            topic_id=topic_id,
            score=score,
            auto_trigger=auto,
            configured=configured,
            enabled=action_enabled,
        )
        if score < float(min_score):
            last_deny = _decision(execute=False, reason="below_threshold", **deny_kwargs)
            continue
        if not tag:
            last_deny = _decision(execute=False, reason="missing_revel_tag", **deny_kwargs)
            continue
        if not auto:
            last_deny = _decision(execute=False, reason="auto_trigger_false", **deny_kwargs)
            continue
        if action is None:
            last_deny = _decision(execute=False, reason="no_matching_action", **deny_kwargs)
            continue
        if action.get("enabled") is False or not action_enabled:
            last_deny = _decision(execute=False, reason="action_disabled", **deny_kwargs)
            continue
        if already and tag == already:
            last_deny = _decision(
                execute=False, reason="duplicate_keyword_action", **deny_kwargs
            )
            continue
        return _decision(
            execute=True,
            reason="authorized_knowledge_match",
            revel_tag=tag,
            topic_id=topic_id,
            score=score,
            auto_trigger=True,
            configured=True,
            enabled=True,
            extra={
                "chunkId": hit.get("chunkId"),
                "sourceId": hit.get("sourceId"),
                "knowledgeBaseId": hit.get("knowledgeBaseId"),
                "intent": action.get("intent"),
            },
        )
    return last_deny or _decision(execute=False, reason="no_approved_action")


async def assigned_enabled_revel_tags(
    db: AsyncSession, knowledge_base_id: int
) -> set[str]:
    """Enabled Revel action tags on clients assigned this Knowledge Base."""
    agent_ids = [
        row[0]
        for row in (
            await db.execute(
                select(ClientKnowledgeBase.agent_id).where(
                    ClientKnowledgeBase.knowledge_base_id == knowledge_base_id,
                    ClientKnowledgeBase.enabled == 1,
                )
            )
        ).all()
    ]
    if not agent_ids:
        return set()
    rows = (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id.in_(agent_ids),
                ClientIntegration.provider == PROVIDER_REVEL,
                ClientIntegration.status == "connected",
            )
        )
    ).scalars().all()
    tags: set[str] = set()
    for row in rows:
        meta = load_meta(row)
        for action in meta.get("actions") or []:
            if not isinstance(action, dict):
                continue
            if action.get("enabled") is False:
                continue
            tag = _tag(action.get("revelTag"))
            if tag:
                tags.add(tag)
    return tags


def matching_revel_action_label(tag: str | None, configured_tags: set[str]) -> str:
    value = _tag(tag)
    if value and value in configured_tags:
        return "available"
    return "not_configured"


async def _load_client_actions(db: AsyncSession, agent_id: str) -> list[dict[str, Any]]:
    row = (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_REVEL,
            )
        )
    ).scalar_one_or_none()
    if row is None or (row.status or "") != "connected":
        return []
    return list(load_meta(row).get("actions") or [])


async def approve_knowledge_hit(
    db: AsyncSession,
    *,
    client_id: str,
    authorized_kb_ids: list[int],
    hit: dict[str, Any],
    min_score: float,
) -> dict[str, Any] | None:
    """Reload DB rows. Returns a deny decision or None if the hit is still approved."""
    tag = _tag(hit.get("revelTag"))
    score = _score(hit)
    topic_id = hit.get("topicId")
    source_id = hit.get("sourceId")
    kb_id = hit.get("knowledgeBaseId")
    chunk_id = hit.get("chunkId")
    base = dict(
        revel_tag=tag or None,
        topic_id=topic_id,
        score=score,
        auto_trigger=bool(hit.get("revelAutoTrigger")),
        configured=True,
        enabled=True,
    )
    if score < float(min_score):
        return _decision(execute=False, reason="below_threshold", **base)
    if kb_id is None or int(kb_id) not in {int(x) for x in authorized_kb_ids}:
        return _decision(execute=False, reason="unauthorized_kb", **base)
    kb = await db.get(KnowledgeBase, int(kb_id))
    if kb is None or not kb.enabled:
        return _decision(execute=False, reason="disabled_kb", **base)
    link = (
        await db.execute(
            select(ClientKnowledgeBase).where(
                ClientKnowledgeBase.agent_id == client_id,
                ClientKnowledgeBase.knowledge_base_id == int(kb_id),
            )
        )
    ).scalar_one_or_none()
    if link is None or not link.enabled:
        return _decision(execute=False, reason="disabled_kb", **base)
    if chunk_id is not None:
        chunk = await db.get(KnowledgeChunk, int(chunk_id))
        if chunk is None or int(chunk.knowledge_base_id) != int(kb_id):
            return _decision(execute=False, reason="unauthorized_kb", **base)
        if source_id is None:
            source_id = chunk.source_id
        if topic_id is None:
            topic_id = chunk.topic_id
            base["topic_id"] = topic_id
    if topic_id is None:
        return _decision(execute=False, reason="disabled_topic", **base)
    topic = await db.get(KnowledgeTopic, int(topic_id))
    if topic is None or not topic.enabled:
        return _decision(execute=False, reason="disabled_topic", **base)
    if int(topic.knowledge_base_id) != int(kb_id):
        return _decision(execute=False, reason="unauthorized_kb", **base)
    if _tag(topic.revel_tag) != tag:
        return _decision(execute=False, reason="missing_revel_tag", **base)
    if not topic.revel_auto_trigger:
        return _decision(execute=False, reason="auto_trigger_false", **base)
    if source_id is None:
        return _decision(execute=False, reason="disabled_source", **base)
    source = await db.get(KnowledgeSource, int(source_id))
    if source is None or not source.enabled:
        return _decision(execute=False, reason="disabled_source", **base)
    if int(source.knowledge_base_id) != int(kb_id):
        return _decision(execute=False, reason="unauthorized_kb", **base)
    return None


def _log_decision(mac: str, client_id: Any, decision: dict[str, Any]) -> None:
    log.info(
        "revel decision mac=%s client=%s source=knowledge tag=%s topic=%s score=%s "
        "auto_trigger=%s configured=%s enabled=%s execute=%s reason=%s",
        mac,
        client_id,
        decision.get("revelTag"),
        decision.get("topicId"),
        decision.get("score"),
        decision.get("autoTrigger"),
        decision.get("configured"),
        decision.get("enabled"),
        decision.get("execute"),
        decision.get("reason"),
    )


async def evaluate_knowledge_revel(
    db: AsyncSession,
    mac: str,
    query: str,
    *,
    already_executed_tag: str | None = None,
    limit: int = 5,
    hits: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Server-side Knowledge Revel decision. Fail-open. Does not scan text."""
    flag_on = knowledge_revel_enabled() if enabled is None else bool(enabled)
    context = await resolve_device_knowledge_context(db, mac)
    client_id = context.get("clientId")
    kb_ids = [int(row["id"]) for row in (context.get("knowledgeBases") or [])]
    empty = _decision(execute=False, reason="feature_disabled" if not flag_on else "no_client")
    if not flag_on:
        empty["reason"] = "feature_disabled"
        _log_decision(mac, client_id, empty)
        return empty
    if not client_id:
        empty["reason"] = "no_client"
        _log_decision(mac, client_id, empty)
        return empty
    if hits is None:
        searched = await device_knowledge_search(db, mac, query, limit)
        hits = list(searched.get("results") or [])
        kb_ids = [int(x) for x in (searched.get("knowledgeBaseIds") or kb_ids)]
        client_id = searched.get("clientId") or client_id
    actions = await _load_client_actions(db, str(client_id))
    min_score = float(settings.knowledge_min_score)
    decision = resolve_knowledge_revel(
        hits,
        actions,
        min_score=min_score,
        already_executed_tag=already_executed_tag,
        enabled=True,
    )
    if not decision.get("execute"):
        _log_decision(mac, client_id, decision)
        return decision
    hit = next(
        (
            row
            for row in hits
            if isinstance(row, dict)
            and _tag(row.get("revelTag")) == _tag(decision.get("revelTag"))
            and row.get("topicId") == decision.get("topicId")
        ),
        next((row for row in hits if isinstance(row, dict)), {}),
    )
    denied = await approve_knowledge_hit(
        db,
        client_id=str(client_id),
        authorized_kb_ids=kb_ids,
        hit=hit,
        min_score=min_score,
    )
    if denied is not None:
        _log_decision(mac, client_id, denied)
        return denied
    try:
        revel = await evaluate_configured_tag(
            db, agent_id=str(client_id), tag=_tag(decision.get("revelTag"))
        )
    except Exception as exc:
        log.warning("knowledge revel command path failed (non-fatal): %s", exc)
        decision["executed"] = False
        decision["revelReason"] = "revel_failed"
        _log_decision(mac, client_id, decision)
        return decision
    decision["executed"] = bool(revel.get("executed"))
    decision["intent"] = revel.get("intent") or decision.get("intent")
    decision["revelReason"] = revel.get("reason")
    decision["executeEnabled"] = revel.get("executeEnabled")
    _log_decision(mac, client_id, decision)
    return decision
