"""CareConnect partner M2M API (X-Client-Id + X-Client-Secret).

GET /api/v1/integrations/careconnect/assessment

Dashboard JWT and Watcher X-API-Key are not accepted here.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ClientIntegration
from ..partner_auth import require_careconnect_client
from ..partner_payload import build_client_assessment_payload


log = logging.getLogger("partner")

router = APIRouter(prefix="/v1/integrations/careconnect", tags=["partner-careconnect"])


@router.get("/assessment", response_model=None)
async def get_assessment(
    integration: ClientIntegration = Depends(require_careconnect_client),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    public_id = integration.public_id or ""
    log.info("partner assessment public_id=%s", public_id)
    return await build_client_assessment_payload(db, integration.agent_id, public_id)
