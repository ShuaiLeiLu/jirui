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
from datetime import datetime as dt
from datetime import time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.database_factory import DatabaseFactory
from app.core.redis_factory import RedisFactory
from app.models.task import OrchestrationTask

logger = logging.getLogger(__name__)

# A股交易时段（含集合竞价到收盘），工作日 09:15 ~ 15:00
_TRADING_START = time(9, 15)
_TRADING_END = time(15, 0)

_scheduler: AsyncIOScheduler | None = None
_database: DatabaseFactory | None = None
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _now_shanghai() -> dt:
    return dt.now(tz=_SHANGHAI_TZ)


def _task_job_id(task_id: str) -> str:
    return f"task:{task_id}"


def _is_trading_hours() -> bool:
    """判断当前是否在A股交易时段（工作日 09:15 ~ 15:00）"""
    now = _now_shanghai()
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


async def _run_intraday_confirmation(db: DatabaseFactory) -> None:
    """执行盘中承接确认（仅在交易时段内执行）。"""
    from app.engine.strategy_engine import execute_intraday_confirmation

    if not _is_trading_hours():
        logger.info("[调度器] 当前非交易时段，跳过盘中确认")
        return

    logger.info("[调度器] 开始执行盘中承接确认...")
    try:
        async with db.session_factory() as session:
            result = await execute_intraday_confirmation(session)
        logger.info("[调度器] 盘中确认完成: %s", result)
    except Exception:
        logger.exception("[调度器] 盘中确认执行异常")


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


async def _refresh_trading_quotes(db: DatabaseFactory, redis_factory: RedisFactory) -> None:
    """刷新当前持仓行情缓存，并用缓存结果更新模拟盘快照。"""
    from sqlalchemy import select

    from app.models.trading import Position, TradingAccount
    from app.modules.trading.quote_cache import refresh_cached_quotes
    from app.modules.trading.service import TradingService

    if not _is_trading_hours():
        logger.info("[调度器] 当前非交易时段，跳过模拟盘行情缓存刷新")
        return

    try:
        async with db.session_factory() as session:
            symbol_result = await session.execute(
                select(Position.symbol).where(Position.quantity > 0).distinct()
            )
            symbols = [symbol for symbol in symbol_result.scalars().all() if symbol]
            if not symbols:
                return

            redis = redis_factory.get_client()
            await refresh_cached_quotes(redis, symbols)

            service = TradingService()
            account_result = await session.execute(select(TradingAccount))
            for account in account_result.scalars().all():
                await service._refresh_account_snapshot(session, account, cache_only=True)
            await session.commit()
            logger.info("[调度器] 行情缓存刷新完成：%d 只股票", len(symbols))
    except Exception:
        logger.exception("[调度器] 行情缓存刷新异常")


async def _snapshot_trading_accounts(db: DatabaseFactory) -> None:
    """收盘后为所有模拟账户生成真实账户快照。"""
    from app.models.trading import TradingAccount
    from app.modules.trading.service import TradingService

    logger.info("[调度器] 开始生成模拟盘账户快照...")
    try:
        async with db.session_factory() as session:
            service = TradingService()
            account_result = await session.execute(select(TradingAccount))
            count = 0
            for account in account_result.scalars().all():
                await service.async_snapshot_account(session, account)
                count += 1
            await session.commit()
            logger.info("[调度器] 模拟盘账户快照生成完成：%d 个账户", count)
    except Exception:
        logger.exception("[调度器] 模拟盘账户快照生成异常")


def _build_task_trigger(task: OrchestrationTask) -> Any:
    config = task.schedule_config or {}
    timezone = "Asia/Shanghai"
    if task.schedule_type == "cron":
        expr = str(config.get("expr") or "").strip()
        if not expr:
            raise ValueError("Cron 任务缺少 schedule_config.expr")
        return CronTrigger.from_crontab(expr, timezone=timezone)
    if task.schedule_type == "interval":
        minutes = int(config.get("minutes") or 0)
        if minutes <= 0:
            raise ValueError("间隔任务缺少有效的 schedule_config.minutes")
        return IntervalTrigger(minutes=minutes, timezone=timezone)
    if task.schedule_type == "one_time":
        raw_run_at = str(config.get("run_at") or "").strip()
        if not raw_run_at:
            raise ValueError("一次性任务缺少 schedule_config.run_at")
        run_at = dt.fromisoformat(raw_run_at.replace("Z", "+00:00"))
        return DateTrigger(run_date=run_at, timezone=timezone)
    raise ValueError(f"不支持的调度类型：{task.schedule_type}")


async def _run_orchestration_task(task_id: str) -> None:
    if _database is None:
        logger.error("[任务编排] 数据库未初始化，无法执行任务 %s", task_id)
        return

    from app.modules.tasks.service import TaskService

    try:
        async with _database.session_factory() as session:
            await TaskService().execute_task(session, task_id, trigger_source="scheduler")
    except Exception:
        logger.exception("[任务编排] 任务执行异常：%s", task_id)


def get_orchestration_task_next_run_at(task_id: str) -> dt | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(_task_job_id(task_id))
    return getattr(job, "next_run_time", None) if job else None


def schedule_orchestration_task(task: OrchestrationTask) -> dt | None:
    """Register or replace an ACTIVE orchestration task job."""
    if _scheduler is None:
        logger.info("[任务编排] 调度器尚未启动，跳过注册任务 %s", task.id)
        return None
    if task.lifecycle_status != "ACTIVE":
        unschedule_orchestration_task(task.id)
        return None

    job = _scheduler.add_job(
        _run_orchestration_task,
        trigger=_build_task_trigger(task),
        args=[task.id],
        id=_task_job_id(task.id),
        name=f"任务编排：{task.title}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    next_run_at = getattr(job, "next_run_time", None)
    logger.info("[任务编排] 已注册任务 %s，下次执行：%s", task.id, next_run_at)
    return next_run_at


def unschedule_orchestration_task(task_id: str) -> None:
    if _scheduler is None:
        return
    job_id = _task_job_id(task_id)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info("[任务编排] 已移除任务 %s", task_id)


async def load_active_orchestration_tasks(db: DatabaseFactory) -> None:
    try:
        async with db.session_factory() as session:
            result = await session.execute(
                select(OrchestrationTask).where(OrchestrationTask.lifecycle_status == "ACTIVE")
            )
            tasks = list(result.scalars().all())
            for task in tasks:
                try:
                    task.next_run_at = schedule_orchestration_task(task)
                except Exception:
                    logger.exception("[任务编排] 注册任务失败：%s", task.id)
                    task.last_run_status = "FAILED"
                    task.next_run_at = None
            await session.commit()
            logger.info("[任务编排] ACTIVE 任务加载完成：%d 个", len(tasks))
    except Exception:
        logger.exception("[任务编排] ACTIVE 任务加载异常")


def start_scheduler(db: DatabaseFactory, redis: RedisFactory | None = None) -> AsyncIOScheduler:
    """启动策略调度器，返回 scheduler 实例"""
    global _scheduler, _database

    if _scheduler is not None:
        return _scheduler

    _database = db
    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每个交易日 09:15 重置 daily_pnl
    _scheduler.add_job(
        _reset_daily_pnl,
        trigger=CronTrigger(hour=9, minute=15, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="reset_daily_pnl",
        name="重置每日盈亏",
        replace_existing=True,
    )

    # 每个交易日 09:25 执行调仓
    _scheduler.add_job(
        _run_daily_rotation,
        trigger=CronTrigger(hour=9, minute=25, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="daily_rotation",
        name="每日轮动调仓",
        replace_existing=True,
    )

    # 每个交易日 09:35 追加一次盘中确认，供情绪超短等需要开盘承接验证的策略使用。
    _scheduler.add_job(
        _run_intraday_confirmation,
        trigger=CronTrigger(hour=9, minute=35, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="intraday_confirmation",
        name="盘中承接确认",
        replace_existing=True,
    )

    # 每个交易日 14:00 检查昨日涨停持仓是否打开
    _scheduler.add_job(
        _run_limit_up_check,
        trigger=CronTrigger(hour=14, minute=0, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="check_limit_up",
        name="涨停打开检查",
        replace_existing=True,
    )

    _scheduler.add_job(
        _snapshot_trading_accounts,
        trigger=CronTrigger(hour=15, minute=5, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="snapshot_trading_accounts",
        name="生成模拟盘账户快照",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    if redis is not None:
        _scheduler.add_job(
            _refresh_trading_quotes,
            trigger=IntervalTrigger(seconds=60, timezone="Asia/Shanghai"),
            args=[db, redis],
            id="refresh_trading_quotes",
            name="刷新模拟盘行情缓存",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            _refresh_trading_quotes,
            trigger="date",
            run_date=_now_shanghai() + timedelta(seconds=10),
            args=[db, redis],
            id="initial_trading_quote_refresh",
            name="启动后首次刷新模拟盘行情缓存",
            replace_existing=True,
            max_instances=1,
        )

        from app.modules.preopen.snapshot_refresher import PREOPEN_REFRESH_GROUPS, refresh_preopen_group

        for index, group in enumerate(PREOPEN_REFRESH_GROUPS.values()):
            _scheduler.add_job(
                refresh_preopen_group,
                trigger=IntervalTrigger(seconds=group.interval_seconds, timezone="Asia/Shanghai"),
                args=[redis, group.name],
                id=f"refresh_preopen_snapshot_{group.name}",
                name=f"刷新盘前快照：{group.name}",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            _scheduler.add_job(
                refresh_preopen_group,
                trigger="date",
                run_date=_now_shanghai() + timedelta(seconds=15 + index * 5),
                args=[redis, group.name],
                id=f"initial_preopen_snapshot_{group.name}",
                name=f"启动后首次刷新盘前快照：{group.name}",
                replace_existing=True,
                max_instances=1,
            )

    # 启动后延迟 5 秒执行一次调仓（仅交易时段内生效，非交易时段自动跳过）
    if _is_trading_hours():
        _scheduler.add_job(
            _run_daily_rotation,
            trigger="date",
            run_date=_now_shanghai() + timedelta(seconds=5),
            args=[db],
            id="initial_rotation",
            name="启动后首次调仓",
            replace_existing=True,
        )
    else:
        logger.info("[调度器] 当前非交易时段，跳过启动后首次调仓")

    _scheduler.start()
    _scheduler.add_job(
        load_active_orchestration_tasks,
        trigger="date",
        run_date=_now_shanghai() + timedelta(seconds=1),
        args=[db],
        id="load_active_orchestration_tasks",
        name="加载任务编排 ACTIVE 任务",
        replace_existing=True,
    )
    logger.info("[调度器] APScheduler 已启动，已注册 %d 个定时任务", len(_scheduler.get_jobs()))

    return _scheduler


def stop_scheduler() -> None:
    """停止调度器"""
    global _scheduler, _database
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        _database = None
        logger.info("[调度器] APScheduler 已停止")
