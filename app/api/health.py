"""
Top-level process liveness endpoint (distinct from the dashboard's
richer /api/dashboard/health, which reports scanner/database/Telegram
status). This endpoint only confirms the API process itself is up.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    server_time_utc: datetime


@router.get("/health", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok", server_time_utc=datetime.now(timezone.utc))
