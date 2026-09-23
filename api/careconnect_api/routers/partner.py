"""CareConnect partner M2M API (X-Client-Id + X-Client-Secret).

GET  /api/v1/integrations/careconnect/assessment  diagnostic readback
POST /api/v1/integrations/careconnect/ingest      primary write (Nexus push)

Dashboard JWT and Watcher X-API-Key are not accepted here.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..envelope import APIException
from ..models import ClientIntegration
from ..partner_auth import require_careconnect_client
from ..partner_payload import SCHEMA_VERSION, build_client_assessment_payload


log = logging.getLogger("partner")

router = APIRouter(prefix="/v1/integrations/careconnect", tags=["partner-careconnect"])


class AssessmentBlockIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    level: str | None = None
    confidence: int | None = None


class CareConnectAssessmentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schemaVersion: str
    clientId: str
    date: str | None = None
    time: str | None = None
    timestamp: str | None = None
    assessment: AssessmentBlockIn | None = None
    recommendations: list[str] = Field(default_factory=list)


def _accepted_payload(body: CareConnectAssessmentIn) -> dict[str, Any]:
    assessment = None
    if body.assessment is not None:
        assessment = {
            "level": body.assessment.level,
            "confidence": body.assessment.confidence,
        }
    return {
        "schemaVersion": body.schemaVersion,
        "clientId": body.clientId,
        "date": body.date,
        "time": body.time,
        "timestamp": body.timestamp,
        "assessment": assessment,
        "recommendations": list(body.recommendations),
    }


@router.get("/assessment", response_model=None)
async def get_assessment(
    integration: ClientIntegration = Depends(require_careconnect_client),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    public_id = integration.public_id or ""
    log.info("partner assessment public_id=%s", public_id)
    return await build_client_assessment_payload(db, integration.agent_id, public_id)


@router.post("/ingest", response_model=None)
async def ingest_assessment(
    body: CareConnectAssessmentIn,
    integration: ClientIntegration = Depends(require_careconnect_client),
) -> dict[str, Any]:
    public_id = integration.public_id or ""
    if body.schemaVersion != SCHEMA_VERSION:
        raise APIException(400, "unsupported schemaVersion")
    if body.clientId != public_id:
        raise APIException(400, "clientId does not match credentials")
    accepted = _accepted_payload(body)
    log.info("partner ingest ok public_id=%s", public_id)
    return accepted
