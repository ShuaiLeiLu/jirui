"""
认证路由

仅返回真实数据库数据。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_session
from app.core.security import get_current_user_id
from app.modules.auth.schemas import AuthToken, LoginRequest, RegisterRequest, UserProfile
from app.modules.auth.service import AuthService
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
service = AuthService()


@router.post("/login")
async def login(
    payload: LoginRequest,
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[AuthToken]:
    """登录接口。"""
    if not session:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="数据库不可用")
    data = await service.async_login(session, payload)
    return ApiResponse(data=data)


@router.post("/register")
async def register(
    payload: RegisterRequest,
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[UserProfile]:
    """注册接口。"""
    if not session:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="数据库不可用")
    data = await service.async_register(session, payload)
    return ApiResponse(data=data)


@router.get("/me")
async def me(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[UserProfile]:
    """获取当前用户 Profile。"""
    if not session:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="数据库不可用")
    data = await service.async_get_profile(session, user_id)
    return ApiResponse(data=data)
