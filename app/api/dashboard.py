"""
Dashboard summary and card-data REST endpoints.
"""

from fastapi import APIRouter, Depends

from app.api.dashboard_service import DashboardService
from app.api.dependencies import get_dashboard_service
from app.api.schemas import (
    ActiveSignal,
    DashboardHealth,
    DashboardSummary,
    PremiumSignal,
    RejectionItem,
    ScanningCoin,
    StrongSignal,
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
) -> list[ActiveSignal]:
    return await service.get_active_signals()


@router.get("/premium-signals", response_model=list[PremiumSignal])
async def get_premium_signals(
    service: DashboardService = Depends(get_dashboard_service),
) -> list[PremiumSignal]:
    return await service.get_premium_signals()


@router.get("/strong-signals", response_model=list[StrongSignal])
async def get_strong_signals(
    service: DashboardService = Depends(get_dashboard_service),
) -> list[StrongSignal]:
    return await service.get_strong_signals()


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
