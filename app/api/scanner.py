"""
Scanner runtime status REST endpoint.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_scanner_service
from app.api.schemas import ScannerStatusResponse

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.get("/status", response_model=ScannerStatusResponse)
async def get_scanner_status(scanner_service=Depends(get_scanner_service)) -> ScannerStatusResponse:
    if scanner_service is None:
        return ScannerStatusResponse(
            running=False,
            shutdown_requested=False,
            cycles_completed=0,
            last_cycle_started_at=None,
            last_cycle_completed_at=None,
            last_cycle_id=None,
            last_error="Waiting for scanner",
        )

    runtime_status = scanner_service.get_runtime_status()
    return ScannerStatusResponse(
        running=runtime_status.running,
        shutdown_requested=runtime_status.shutdown_requested,
        cycles_completed=runtime_status.cycles_completed,
        last_cycle_started_at=runtime_status.last_cycle_started_at,
        last_cycle_completed_at=runtime_status.last_cycle_completed_at,
        last_cycle_id=runtime_status.last_cycle_id,
        last_error=runtime_status.last_error,
    )
