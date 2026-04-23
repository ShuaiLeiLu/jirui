"""
策略调度器 —— 使用 APScheduler 在 FastAPI 生命周期内运行定时任务

不依赖 Celery / Redis，进程内调度。

调度计划（交易日）：
  - 09:25  每日调仓（execute_daily_rotation）
  - 09:15  重置 daily_pnl
  - 启动时  若在交易时段内则延迟 5 秒执行一次调仓
"""
from __future__ import annotations

import logging
from datetime import datetime as dt, timedelta, time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.database_factory import DatabaseFactory

logger = logging.getLogger(__name__)

# A股交易时段（含集合竞价到收盘），工作日 09:15 ~ 15:00
_TRADING_START = time(9, 15)
_TRADING_END = time(15, 0)

_scheduler: AsyncIOScheduler | None = None


def _is_trading_hours() -> bool:
    """判断当前是否在A股交易时段（工作日 09:15 ~ 15:00）"""
    from zoneinfo import ZoneInfo
    now = dt.now(tz=ZoneInfo("Asia/Shanghai"))
    # 周一=0 ~ 周五=4，周末不交易
    if now.weekday() > 4:
        return False
    return _TRADING_START <= now.time() <= _TRADING_END


async def _run_daily_rotation(db: DatabaseFactory) -> None:
    """执行每日轮动调仓（仅在交易时段内执行）"""
    from app.engine.strategy_engine import execute_daily_rotation  # noqa: E501

    if not _is_trading_hours():
        logger.info("[调度器] 当前非交易时段，跳过调仓")
        return

    logger.info("[调度器] 开始执行每日轮动调仓...")
    try:
        async with db.session_factory() as session:
            result = await execute_daily_rotation(session)
        logger.info("[调度器] 调仓完成: %s", result)
    except Exception:
        logger.exception("[调度器] 调仓执行异常")


async def _run_limit_up_check(db: DatabaseFactory) -> None:
    """14:00 检查昨日涨停持仓是否打开（仅在交易时段内执行）"""
    from app.engine.strategy_engine import check_limit_up

    if not _is_trading_hours():
        logger.info("[调度器] 当前非交易时段，跳过涨停检查")
        return

    logger.info("[调度器] 开始执行涨停打开检查...")
    try:
        async with db.session_factory() as session:
            result = await check_limit_up(session)
        logger.info("[调度器] 涨停检查完成: %s", result)
    except Exception:
        logger.exception("[调度器] 涨停检查执行异常")


async def _reset_daily_pnl(db: DatabaseFactory) -> None:
    """每日开盘前重置所有账户的 daily_pnl"""
    from sqlalchemy import text

    logger.info("[调度器] 重置每日盈亏...")
    try:
        async with db.session_factory() as session:
            await session.execute(text("UPDATE trading_accounts SET daily_pnl = 0"))
            await session.execute(text("UPDATE researchers SET today_pnl = 0"))
            await session.commit()
        logger.info("[调度器] 重置完成")
    except Exception:
        logger.exception("[调度器] 重置 daily_pnl 异常")


def start_scheduler(db: DatabaseFactory) -> AsyncIOScheduler:
    """启动策略调度器，返回 scheduler 实例"""
    global _scheduler

    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每个交易日 09:15 重置 daily_pnl
    _scheduler.add_job(
        _reset_daily_pnl,
        trigger=CronTrigger(hour=9, minute=15, day_of_week="mon-fri"),
        args=[db],
        id="reset_daily_pnl",
        name="重置每日盈亏",
        replace_existing=True,
    )

    # 每个交易日 09:25 执行调仓
    _scheduler.add_job(
        _run_daily_rotation,
        trigger=CronTrigger(hour=9, minute=25, day_of_week="mon-fri"),
        args=[db],
        id="daily_rotation",
        name="每日轮动调仓",
        replace_existing=True,
    )

    # 每个交易日 14:00 检查昨日涨停持仓是否打开
    _scheduler.add_job(
        _run_limit_up_check,
        trigger=CronTrigger(hour=14, minute=0, day_of_week="mon-fri"),
        args=[db],
        id="check_limit_up",
        name="涨停打开检查",
        replace_existing=True,
    )

    # 启动后延迟 5 秒执行一次调仓（仅交易时段内生效，非交易时段自动跳过）
    if _is_trading_hours():
        _scheduler.add_job(
            _run_daily_rotation,
            trigger="date",
            run_date=dt.now() + timedelta(seconds=5),
            args=[db],
            id="initial_rotation",
            name="启动后首次调仓",
            replace_existing=True,
        )
    else:
        logger.info("[调度器] 当前非交易时段，跳过启动后首次调仓")

    _scheduler.start()
    logger.info("[调度器] APScheduler 已启动，已注册 %d 个定时任务", len(_scheduler.get_jobs()))

    return _scheduler


def stop_scheduler() -> None:
    """停止调度器"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[调度器] APScheduler 已停止")
