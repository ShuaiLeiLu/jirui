"""
策略调度器 —— 使用 APScheduler 在 FastAPI 生命周期内运行定时任务

不依赖 Celery / Redis，进程内调度。

调度计划（交易日）：
  - 09:25  每日调仓（execute_daily_rotation）
  - 09:15  重置 daily_pnl
  - 启动时  若在交易时段内则延迟 5 秒执行一次调仓
"""
from __future__ import annotations

import asyncio
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
_STRATEGY_JOB_TIMEOUT_SECONDS = 90

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
            result = await execute_daily_rotation(
                session,
                per_researcher_timeout=_STRATEGY_JOB_TIMEOUT_SECONDS,
            )
        logger.info("[调度器] 调仓完成: %s", result)
    except TimeoutError:
        logger.exception("[调度器] 调仓执行超时，已终止本次任务")
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
            result = await execute_intraday_confirmation(
                session,
                per_researcher_timeout=_STRATEGY_JOB_TIMEOUT_SECONDS,
            )
        logger.info("[调度器] 盘中确认完成: %s", result)
    except TimeoutError:
        logger.exception("[调度器] 盘中确认执行超时，已终止本次任务")
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
            result = await asyncio.wait_for(
                check_limit_up(session),
                timeout=_STRATEGY_JOB_TIMEOUT_SECONDS,
            )
        logger.info("[调度器] 涨停检查完成: %s", result)
    except TimeoutError:
        logger.exception("[调度器] 涨停检查执行超时，已终止本次任务")
    except Exception:
        logger.exception("[调度器] 涨停检查执行异常")


async def _run_stop_loss_check(db: DatabaseFactory) -> None:
    """14:30 检查持仓是否触发止损（仅在交易时段内执行）"""
    from app.engine.strategy_engine import check_stop_loss

    if not _is_trading_hours():
        logger.info("[调度器] 当前非交易时段，跳过止损检查")
        return

    logger.info("[调度器] 开始执行止损检查...")
    try:
        async with db.session_factory() as session:
            result = await asyncio.wait_for(
                check_stop_loss(session),
                timeout=_STRATEGY_JOB_TIMEOUT_SECONDS,
            )
        logger.info("[调度器] 止损检查完成: %s", result)
    except TimeoutError:
        logger.exception("[调度器] 止损检查执行超时，已终止本次任务")
    except Exception:
        logger.exception("[调度器] 止损检查执行异常")


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


async def _run_preopen_ai_digest(db: DatabaseFactory) -> None:
    """每日 08:30 自动生成盘前 ai-digest-v2,落库供前端读取。"""
    from app.integrations.openclaw.digest_push import discard_digest_pushes, flush_digest_pushes
    from app.modules.preopen.skill_service import run_preopen_chain

    logger.info("[调度器] 开始生成盘前 AI digest...")
    try:
        async with db.session_factory() as session:
            result = await run_preopen_chain(session)
            try:
                await session.commit()
            except Exception:
                discard_digest_pushes(session)
                raise
            await flush_digest_pushes(session)
        logger.info(
            "[调度器] 盘前 digest 完成,bias=%s",
            result.get("bias", "-"),
        )
    except Exception:
        logger.exception("[调度器] 盘前 digest 生成异常")


async def _run_daily_review_all(db: DatabaseFactory) -> None:
    """每日 16:00 对所有 active researcher 跑教练复盘。"""
    from sqlalchemy import select

    from app.integrations.openclaw.digest_push import discard_digest_pushes, flush_digest_pushes
    from app.models.researcher import Researcher
    from app.modules.trading.skill_service import run_daily_review

    logger.info("[调度器] 开始生成当日教练复盘...")
    try:
        async with db.session_factory() as session:
            q = await session.execute(
                select(Researcher.id).where(Researcher.status == "active")
            )
            researcher_ids = [row for row in q.scalars().all()]

            done = 0
            for rid in researcher_ids:
                try:
                    await run_daily_review(session, researcher_id=rid)
                    done += 1
                except Exception:
                    logger.exception(
                        "[调度器] 研究员 %s 复盘失败", rid,
                    )
            try:
                await session.commit()
            except Exception:
                discard_digest_pushes(session)
                raise
            await flush_digest_pushes(session)
            logger.info(
                "[调度器] 当日复盘完成,共 %d/%d 个研究员",
                done, len(researcher_ids),
            )
    except Exception:
        logger.exception("[调度器] 教练复盘批量任务异常")


async def _backfill_thesis_log_actuals(db: DatabaseFactory) -> None:
    """T+1 凌晨回填昨日 thesis_log 实际结果。

    简化实现:基于昨日 PreopenAiDigest 的 bias 和今日大盘涨跌做对照。
    详细评估留待 Phase 4 thesis_scorecard。
    """
    from datetime import timedelta as _td

    from sqlalchemy import select

    from app.integrations.akshare.client import get_index_daily_bars
    from app.models.researcher import ResearcherThesisLog

    logger.info("[调度器] 开始回填 thesis_log T+1 实际结果...")
    try:
        async with db.session_factory() as session:
            today = _now_shanghai().date()
            yesterday = today - _td(days=1)
            sh_bars = get_index_daily_bars("sh000001", 2)
            sh_chg = 0.0
            if sh_bars and len(sh_bars) >= 2:
                sh_chg = (
                    (sh_bars[-1].close - sh_bars[-2].close)
                    / sh_bars[-2].close * 100
                )

            q = await session.execute(
                select(ResearcherThesisLog).where(
                    ResearcherThesisLog.trade_date == yesterday,
                    ResearcherThesisLog.correctness == "pending",
                )
            )
            logs = list(q.scalars().all())
            for log in logs:
                actual = {
                    "sh_change_pct_t+1": round(sh_chg, 2),
                    "evaluated_at": today.isoformat(),
                }
                # 简单评估:bias 和大盘涨跌方向是否一致
                bias = log.direction_call or ""
                if bias == "bullish":
                    log.correctness = "correct" if sh_chg > 0.5 else (
                        "partial" if sh_chg > -0.5 else "wrong"
                    )
                elif bias == "bearish":
                    log.correctness = "correct" if sh_chg < -0.5 else (
                        "partial" if sh_chg < 0.5 else "wrong"
                    )
                else:
                    log.correctness = (
                        "correct" if abs(sh_chg) <= 0.5 else "partial"
                    )
                log.actual_result = actual
            await session.commit()
            logger.info("[调度器] thesis_log 回填完成,共 %d 条", len(logs))
    except Exception:
        logger.exception("[调度器] thesis_log 回填异常")


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
            accounts = list(account_result.scalars().all())
            for account in accounts:
                await service._refresh_account_snapshot(session, account, cache_only=True)

            # 分钟级权益快照(只在交易时段写)
            await _persist_minute_snapshots(session, accounts)

            await session.commit()
            logger.info("[调度器] 行情缓存刷新完成：%d 只股票", len(symbols))
    except Exception:
        logger.exception("[调度器] 行情缓存刷新异常")


async def _refresh_page_data_caches() -> None:
    """Refresh page-facing caches that should not block user requests."""
    try:
        from app.modules.news_analysis.router import refresh_ai_panels_cache, refresh_news_analysis_cache

        await refresh_news_analysis_cache()
        await refresh_ai_panels_cache()
        logger.info("[调度器] 资讯分析缓存刷新完成")
    except Exception:
        logger.exception("[调度器] 资讯分析缓存刷新异常")

    try:
        from app.modules.event_driven.router import refresh_event_driven_cache

        await refresh_event_driven_cache()
        logger.info("[调度器] 题材掘金缓存刷新完成")
    except Exception:
        logger.exception("[调度器] 题材掘金缓存刷新异常")


async def _settle_pending_orders_job(db: DatabaseFactory) -> None:
    """每 30 秒在交易时段扫描挂单池,把可成交的限价单撮合掉。"""
    if not _is_trading_hours():
        return
    from app.modules.trading.pending_order_service import settle_pending_orders

    try:
        async with db.session_factory() as session:
            result = await settle_pending_orders(session)
            await session.commit()
            if result["filled"]:
                logger.info(
                    "[挂单撮合] checked=%d filled=%d skipped=%d",
                    result["checked"], result["filled"], result["skipped"],
                )
    except Exception:
        logger.exception("[挂单撮合] 异常")


async def _apply_corporate_actions_job(db: DatabaseFactory) -> None:
    """凌晨 02:30 扫所有持仓 → 应用除权除息(现金分红 / 送转股)。"""
    from app.modules.trading.corporate_action_service import (
        apply_corporate_actions_for_today,
    )

    try:
        async with db.session_factory() as session:
            stats = await apply_corporate_actions_for_today(session)
            await session.commit()
            if stats["dividends_applied"] or stats["splits_applied"]:
                logger.info(
                    "[除权除息] 持仓扫描 %d 笔,现金分红 %d,送转 %d",
                    stats["checked"], stats["dividends_applied"], stats["splits_applied"],
                )
    except Exception:
        logger.exception("[除权除息] 异常")


async def _expire_pending_orders_job(db: DatabaseFactory) -> None:
    """收盘后(15:05)把所有未成交挂单标记为 EXPIRED。"""
    from app.modules.trading.pending_order_service import expire_pending_orders

    try:
        async with db.session_factory() as session:
            n = await expire_pending_orders(session)
            await session.commit()
            if n:
                logger.info("[挂单过期] 标记 %d 笔 ACTIVE 挂单为 EXPIRED", n)
    except Exception:
        logger.exception("[挂单过期] 异常")


async def _persist_minute_snapshots(session, accounts) -> None:
    """把当前所有账户状态打一份分钟快照。

    调用前提:accounts 的 holding_value / total_asset / daily_pnl 已经经过
    _refresh_account_snapshot 盯市更新。本函数只负责落库。
    """
    from datetime import datetime as _dt
    from uuid import uuid4

    from app.models.trading import TradingAccountMinuteSnapshot

    if not _is_trading_hours():
        return

    now = _now_shanghai().replace(second=0, microsecond=0)
    snapshot_at = _dt.fromtimestamp(now.timestamp(), tz=now.tzinfo)
    for account in accounts:
        row = TradingAccountMinuteSnapshot(
            id=f"tms_{uuid4().hex[:12]}",
            account_id=account.id,
            snapshot_at=snapshot_at,
            total_asset=round(float(account.total_asset), 2),
            available_cash=round(float(account.available_cash), 2),
            holding_value=round(float(account.holding_value), 2),
            daily_pnl=round(float(account.daily_pnl), 2),
        )
        session.add(row)


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


async def _snapshot_preopen_market(db: DatabaseFactory) -> None:
    """为盘前趋势生成或更新当天市场结构快照。"""
    from app.modules.preopen.service import PreopenService

    logger.info("[调度器] 开始生成盘前市场快照...")
    try:
        async with db.session_factory() as session:
            await PreopenService().async_record_market_snapshot(session)
            await session.commit()
        logger.info("[调度器] 盘前市场快照生成完成")
    except Exception:
        logger.exception("[调度器] 盘前市场快照生成异常")


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
        _run_stop_loss_check,
        trigger=CronTrigger(hour=14, minute=30, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="check_stop_loss",
        name="持仓止损检查",
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

    _scheduler.add_job(
        _snapshot_preopen_market,
        trigger=CronTrigger(hour=9, minute=20, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="snapshot_preopen_market_morning",
        name="生成盘前市场快照",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _snapshot_preopen_market,
        trigger=CronTrigger(hour=15, minute=10, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="snapshot_preopen_market_close",
        name="更新收盘市场快照",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Skill 框架:每个交易日 08:30 自动生成盘前 AI digest
    _scheduler.add_job(
        _run_preopen_ai_digest,
        trigger=CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="preopen_ai_digest_v2",
        name="盘前 AI digest v2 自动生成",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Skill 框架:每个交易日 16:00 对所有 active researcher 跑教练复盘
    _scheduler.add_job(
        _run_daily_review_all,
        trigger=CronTrigger(hour=16, minute=0, day_of_week="mon-fri", timezone="Asia/Shanghai"),
        args=[db],
        id="daily_review_all",
        name="盘后教练复盘(所有研究员)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Skill 框架:T+1 凌晨 02:00 回填 thesis_log 实际结果
    _scheduler.add_job(
        _backfill_thesis_log_actuals,
        trigger=CronTrigger(hour=2, minute=0, day_of_week="tue-sat", timezone="Asia/Shanghai"),
        args=[db],
        id="backfill_thesis_log_actuals",
        name="T+1 回填 thesis_log",
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

        # 挂单撮合循环:交易时段每 30 秒扫一次
        _scheduler.add_job(
            _settle_pending_orders_job,
            trigger=IntervalTrigger(seconds=30, timezone="Asia/Shanghai"),
            args=[db],
            id="settle_pending_orders",
            name="挂单池盘中撮合",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        # 收盘后挂单过期:每天 15:05
        _scheduler.add_job(
            _expire_pending_orders_job,
            trigger=CronTrigger(
                day_of_week="mon-fri", hour=15, minute=5,
                timezone="Asia/Shanghai",
            ),
            args=[db],
            id="expire_pending_orders",
            name="挂单过期清理",
            replace_existing=True,
        )
        # 除权除息自动调仓:每天 02:30
        _scheduler.add_job(
            _apply_corporate_actions_job,
            trigger=CronTrigger(
                hour=2, minute=30,
                timezone="Asia/Shanghai",
            ),
            args=[db],
            id="apply_corporate_actions",
            name="除权除息自动调仓",
            replace_existing=True,
        )
        _scheduler.add_job(
            _refresh_page_data_caches,
            trigger=IntervalTrigger(seconds=60, timezone="Asia/Shanghai"),
            id="refresh_page_data_caches",
            name="刷新页面数据缓存",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            _refresh_page_data_caches,
            trigger="date",
            run_date=_now_shanghai() + timedelta(seconds=12),
            id="initial_page_data_cache_refresh",
            name="启动后首次刷新页面数据缓存",
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
                kwargs={"force": True},  # 启动时强制刷新一次，忽略交易时段限制
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
