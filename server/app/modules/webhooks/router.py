from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.modules.webhooks.schemas import (
    WebhookCreateRequest,
    WebhookEndpoint,
    WebhookToggleRequest,
)
from app.modules.webhooks.service import WebhookService
from app.schemas.common import ApiResponse, ListResponse

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
service = WebhookService()


@router.get("")
async def list_endpoints() -> ApiResponse[ListResponse[WebhookEndpoint]]:
    items: list[WebhookEndpoint] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.post("")
async def create_endpoint(payload: WebhookCreateRequest) -> ApiResponse[WebhookEndpoint]:
    del payload
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Webhook 接口暂未接入真实数据源")


@router.patch("/{webhook_id}/toggle")
async def toggle_endpoint(
    webhook_id: str,
    payload: WebhookToggleRequest,
) -> ApiResponse[WebhookEndpoint]:
    del webhook_id, payload
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Webhook 接口暂未接入真实数据源")


@router.delete("/{webhook_id}")
async def delete_endpoint(webhook_id: str) -> ApiResponse[WebhookEndpoint]:
    del webhook_id
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Webhook 接口暂未接入真实数据源")
