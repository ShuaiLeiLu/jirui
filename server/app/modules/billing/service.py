"""计费领域服务。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.schemas import BatteryLedgerItem, BatteryPackage, MembershipInfo
from app.repositories.billing_repo import BatteryLedgerRepository
from app.repositories.user_repo import UserRepository


class BillingService:
    """会员与电池账本服务，只保留真实数据库路径。"""

    async def async_list_ledger(
        self, session: AsyncSession, user_id: str, *, limit: int = 50
    ) -> list[BatteryLedgerItem]:
        repo = BatteryLedgerRepository(session)
        records = await repo.list_by_user(user_id, limit=limit)
        return [
            BatteryLedgerItem(
                item_id=record.id,
                change=record.change,
                reason=record.reason,
                created_at=record.created_at,
            )
            for record in records
        ]

    async def async_get_membership(self, session: AsyncSession, user_id: str) -> MembershipInfo:
        repo = UserRepository(session)
        user = await repo.get_by_id(user_id)

        level_map = {
            "普通用户": "FREE",
            "FREE": "FREE",
            "VIP1": "VIP1",
            "VIP2": "VIP2",
            "VIP3": "VIP3",
        }
        display_map = {
            "FREE": "未开通",
            "VIP1": "基础会员",
            "VIP2": "高级会员",
            "VIP3": "旗舰会员",
        }
        discount_map = {
            "FREE": 1.0,
            "VIP1": 0.95,
            "VIP2": 0.9,
            "VIP3": 0.85,
        }
        feature_map = {
            "FREE": [],
            "VIP1": ["高级研究报告"],
            "VIP2": ["高级研究报告", "更多并发任务"],
            "VIP3": ["高级研究报告", "更多并发任务", "优先支持"],
        }

        level = level_map.get(user.membership_level, "FREE") if user else "FREE"
        return MembershipInfo(
            level=level,
            display_name=display_map[level],
            battery_discount=discount_map[level],
            unlocked_features=feature_map[level],
        )

    async def async_list_packages(self, session: AsyncSession) -> list[BatteryPackage]:
        del session
        return []
