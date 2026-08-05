"""
Dashboard summary and card-data REST endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.access_tier import AccessTier, get_access_tier
from app.api.dashboard_service import DashboardService
from app.api.dependencies import get_dashboard_service
from app.api.schemas import (
    ActiveSignal,
    DashboardHealth,
    DashboardSummary,
    PremiumSignal,
    RejectionItem,
    ScanningCoin,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    service: DashboardService = Depends(get_dashboard_service),
) -> DashboardSummary:
    return await service.get_summary()


@router.get("/scanning-coins", response_model=list[ScanningCoin])
async def get_scanning_coins(
    service: DashboardService = Depends(get_dashboard_service),
) -> list[ScanningCoin]:
    return await service.get_scanning_coins()


@router.get("/active-signals", response_model=list[ActiveSignal])
async def get_active_signals(
    service: DashboardService = Depends(get_dashboard_service),
    tier: AccessTier = Depends(get_access_tier),
) -> list[ActiveSignal]:
    return await service.get_active_signals(tier=tier)


@router.get("/premium-signals", response_model=list[PremiumSignal])
async def get_premium_signals(
    service: DashboardService = Depends(get_dashboard_service),
    tier: AccessTier = Depends(get_access_tier),
) -> list[PremiumSignal]:
    return await service.get_premium_signals(tier=tier)


@router.post("/signals/{trade_id}/activate", response_model=ActiveSignal)
async def activate_signal(
    trade_id: str,
    service: DashboardService = Depends(get_dashboard_service),
    tier: AccessTier = Depends(get_access_tier),
) -> ActiveSignal:
    """
    Mark a stored CONFIRMED signal as ACTIVE (dashboard-only state
    transition, moving it from Premium into Active Signals). Never
    places an order, calls an exchange, or affects strategy, risk, or
    Telegram behaviour.
    """
    result = await service.activate_signal(trade_id, tier=tier)
    if result is None:
        raise HTTPException(status_code=404, detail="Signal not found.")
    return result


@router.get("/recent-rejections", response_model=list[RejectionItem])
async def get_recent_rejections(
    service: DashboardService = Depends(get_dashboard_service),
) -> list[RejectionItem]:
    return await service.get_recent_rejections()


@router.get("/health", response_model=DashboardHealth)
async def get_health(
    service: DashboardService = Depends(get_dashboard_service),
) -> DashboardHealth:
    return await service.get_health()
