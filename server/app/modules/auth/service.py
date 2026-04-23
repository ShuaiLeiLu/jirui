"""认证领域服务。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.modules.auth.schemas import AuthToken, LoginRequest, RegisterRequest, UserProfile
from app.repositories.user_repo import UserRepository


class AuthService:
    """认证领域服务，只保留真实数据库路径。"""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def async_login(self, session: AsyncSession, payload: LoginRequest) -> AuthToken:
        repo = UserRepository(session)
        user = await repo.get_by_phone(payload.phone)
        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或密码错误")

        token = create_access_token(subject=user.id)
        return AuthToken(
            access_token=token,
            expires_in=self.settings.access_token_expire_minutes * 60,
            user=self._model_to_profile(user),
        )

    async def async_register(self, session: AsyncSession, payload: RegisterRequest) -> UserProfile:
        repo = UserRepository(session)
        existing = await repo.get_by_phone(payload.phone)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="手机号已注册")

        user = User(
            id=f"u_{uuid4().hex[:10]}",
            phone=payload.phone,
            password_hash=hash_password(payload.password),
            nickname=payload.nickname,
            membership_level="普通用户",
            battery_balance=300,
        )
        await repo.create(user)
        await session.commit()
        return self._model_to_profile(user)

    async def async_get_profile(self, session: AsyncSession, user_id: str) -> UserProfile:
        repo = UserRepository(session)
        user = await repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        return self._model_to_profile(user)

    @staticmethod
    def _model_to_profile(user: User) -> UserProfile:
        return UserProfile(
            user_id=user.id,
            phone=user.phone,
            nickname=user.nickname,
            membership_level=user.membership_level,
            battery_balance=user.battery_balance,
        )
