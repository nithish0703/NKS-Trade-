"""
Signal listing and signal-details REST endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.access_tier import AccessTier, get_access_tier
from app.api.dashboard_service import DashboardService
from app.api.dependencies import get_dashboard_service, get_signal_repository
from app.api.schemas import SignalDetails
from app.models.signal import Direction, MarketRegime, Signal, SignalStatus
from app.storage.signal_repository import SignalRepository
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/signals", tags=["signals"])


class SignalListItem(BaseModel):
    """A single stored signal, safe for external listing (no institutional_reason)."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    status: SignalStatus
    market_regime: MarketRegime
    detection_time_utc: object


def _to_list_item(signal: Signal) -> SignalListItem:
    return SignalListItem(
        trade_id=signal.trade_id,
        coin=signal.coin,
        direction=signal.direction,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        risk_reward_ratio=signal.risk_reward_ratio,
        status=signal.status,
        market_regime=signal.market_regime,
        detection_time_utc=signal.detection_time_utc,
    )


@router.get("", response_model=list[SignalListItem])
async def list_signals(
    limit: int = Query(default=50, gt=0, le=1000),
    symbol: Optional[str] = None,
    repository: SignalRepository = Depends(get_signal_repository),
) -> list[SignalListItem]:
    signals = await repository.list_recent(limit=limit, symbol=symbol)
    return [_to_list_item(signal) for signal in signals]


@router.get("/{trade_id}", response_model=SignalDetails)
async def get_signal_details(
    trade_id: str,
    service: DashboardService = Depends(get_dashboard_service),
    tier: AccessTier = Depends(get_access_tier),
) -> SignalDetails:
    details = await service.get_signal_details(trade_id, tier=tier)
    if details is None:
        raise HTTPException(status_code=404, detail="Signal not found.")
    return details
