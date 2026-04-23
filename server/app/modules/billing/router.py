from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_session
from app.core.security import get_current_user_id
from app.modules.billing.schemas import BatteryLedgerItem, BatteryPackage, MembershipInfo
from app.modules.billing.service import BillingService
from app.schemas.common import ApiResponse, ListResponse

router = APIRouter(prefix="/billing", tags=["billing"])
service = BillingService()


@router.get("/membership")
async def get_membership(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[MembershipInfo]:
    if not session:
        return ApiResponse(data=MembershipInfo(
            level="FREE",
            display_name="未开通",
            battery_discount=1.0,
            unlocked_features=[],
        ))
    return ApiResponse(data=await service.async_get_membership(session, user_id))


@router.get("/battery/ledger")
async def list_battery_ledger(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[BatteryLedgerItem]]:
    items = await service.async_list_ledger(session, user_id, limit=limit) if session else []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/battery/packages")
async def list_battery_packages(
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[BatteryPackage]]:
    items = await service.async_list_packages(session) if session else []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))
