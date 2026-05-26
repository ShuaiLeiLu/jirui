"""盘后 daily_review skill chain 服务。

负责:
  1) 组装 pnl_attribution / alpha_analysis / opportunity_cost / daily_coach
  2) 同步 / SSE 流式两种调用
  3) 跑完后落库 DailyReviewReport(含 alpha 指标和后续可选 embedding)
  4) 同步写 ResearcherThesisLog(供 T+1 回填实际结果)
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.openclaw.digest_push import flush_digest_pushes, queue_daily_review_summary
from app.models.researcher import Researcher
from app.models.trading import DailyReviewReport, TradingAccount
from app.skills.base import SkillContext, SkillEvent
from app.skills.orchestrator import SkillOrchestrator
from app.skills.registry import get_skill_registry
from app.skills.shared.run_log import write_skill_run_logs

logger = logging.getLogger(__name__)


_DEFAULT_SKILL_NAMES = [
    "pnl_attribution",
    "alpha_analysis",
    "opportunity_cost",
    "daily_coach",
]


def _build_orchestrator() -> SkillOrchestrator:
    registry = get_skill_registry()
    names = list(_DEFAULT_SKILL_NAMES)
    # Phase 4 可选 skill 若已注册自动并入(在 daily_coach 之前)
    if registry.get_optional("pattern_match") is not None:
        names.insert(-1, "pattern_match")
    if registry.get_optional("thesis_scorecard") is not None:
        names.insert(-1, "thesis_scorecard")
    skills = [registry.get(n) for n in names]
    return SkillOrchestrator(skills)


async def run_daily_review(
    session: AsyncSession,
    *,
    researcher_id: str,
    account_id: str | None = None,
    trade_date: date | None = None,
    candidate_pool: list[dict] | None = None,
) -> dict:
    """非流式版本(调度任务 / 测试用)。"""
    account_id, researcher_name = await _resolve_account_and_researcher(
        session, researcher_id, account_id,
    )
    target_date = trade_date or date.today()
    existing = await get_existing_daily_review_report(
        session, researcher_id=researcher_id, trade_date=target_date,
    )
    if existing is not None:
        return _report_to_result(existing, reused=True)

    orch = _build_orchestrator()
    ctx = SkillContext(
        trade_date=target_date,
        researcher_id=researcher_id,
        extra={
            "session": session,
            "account_id": account_id,
            "researcher_name": researcher_name,
            "candidate_pool": candidate_pool or [],
        },
    )
    outputs = await orch.run(ctx)
    report = await _persist_report(session, ctx, outputs)
    await write_skill_run_logs(
        session, chain_kind="daily_review",
        trade_date=ctx.trade_date, outputs=outputs,
        researcher_id=ctx.researcher_id,
    )
    result = _report_to_result(report, reused=False, researcher_name=researcher_name)
    queue_daily_review_summary(session, result)
    return result


async def get_existing_daily_review_report(
    session: AsyncSession,
    *,
    researcher_id: str,
    trade_date: date,
) -> DailyReviewReport | None:
    result = await session.execute(
        select(DailyReviewReport).where(
            DailyReviewReport.researcher_id == researcher_id,
            DailyReviewReport.trade_date == trade_date,
        )
    )
    return result.scalar_one_or_none()


def _report_to_result(
    report: DailyReviewReport, *, reused: bool, researcher_name: str | None = None,
) -> dict:
    data = {
        "report_id": report.id,
        "trade_date": report.trade_date.isoformat(),
        "researcher_id": report.researcher_id,
        "coach_report_md": report.coach_report_md,
        "alpha_vs_index": report.alpha_vs_index,
        "alpha_vs_sector": report.alpha_vs_sector,
        "win_rate": report.win_rate,
        "total_pnl": report.total_pnl,
        "generated_at": report.generated_at,
        "reused": reused,
    }
    if researcher_name:
        data["researcher_name"] = researcher_name
    return data


async def stream_daily_review(
    session: AsyncSession,
    *,
    researcher_id: str,
    account_id: str | None = None,
    trade_date: date | None = None,
    candidate_pool: list[dict] | None = None,
) -> AsyncIterator[str]:
    """SSE 流式版本。"""
    account_id, researcher_name = await _resolve_account_and_researcher(
        session, researcher_id, account_id,
    )
    target_date = trade_date or date.today()
    existing = await get_existing_daily_review_report(
        session, researcher_id=researcher_id, trade_date=target_date,
    )
    if existing is not None:
        yield _format_sse_event(
            "persisted",
            {
                "report_id": existing.id,
                "trade_date": existing.trade_date.isoformat(),
                "alpha_vs_index": existing.alpha_vs_index,
                "reused": True,
            },
        )
        return

    orch = _build_orchestrator()
    ctx = SkillContext(
        trade_date=target_date,
        researcher_id=researcher_id,
        extra={
            "session": session,
            "account_id": account_id,
            "researcher_name": researcher_name,
            "candidate_pool": candidate_pool or [],
        },
    )
    try:
        async for event in orch.run_stream(ctx):
            yield _format_sse(event)
    except Exception as exc:
        logger.exception("daily review skill chain 流式异常")
        yield _format_sse_event("error", {"error": str(exc)})
        return

    try:
        report = await _persist_report(session, ctx, ctx.outputs)
        await write_skill_run_logs(
            session, chain_kind="daily_review",
            trade_date=ctx.trade_date, outputs=ctx.outputs,
            researcher_id=ctx.researcher_id,
        )
        result = _report_to_result(report, reused=False, researcher_name=researcher_name)
        queue_daily_review_summary(session, result)
        await session.commit()
        await flush_digest_pushes(session)
        yield _format_sse_event(
            "persisted",
            {
                "report_id": report.id,
                "trade_date": report.trade_date.isoformat(),
                "alpha_vs_index": report.alpha_vs_index,
                "reused": False,
            },
        )
    except Exception as exc:
        await session.rollback()
        logger.exception("daily_review_report 落库失败")
        yield _format_sse_event("error", {"error": f"落库失败: {exc}"})


async def _resolve_account_and_researcher(
    session: AsyncSession, researcher_id: str, account_id: str | None,
) -> tuple[str | None, str]:
    if account_id is None:
        q = await session.execute(
            select(TradingAccount.id).where(
                TradingAccount.researcher_id == researcher_id,
            ).limit(1)
        )
        row = q.scalar_one_or_none()
        account_id = row
    name_q = await session.execute(
        select(Researcher.name).where(Researcher.id == researcher_id)
    )
    researcher_name = name_q.scalar_one_or_none() or "未命名研究员"
    return account_id, researcher_name


async def _persist_report(
    session: AsyncSession, ctx: SkillContext, outputs: dict,
) -> DailyReviewReport:
    """落库或更新当日 DailyReviewReport。"""
    coach = outputs.get("daily_coach")
    coach_md = coach.narrative if coach and coach.success else ""

    alpha = outputs.get("alpha_analysis")
    alpha_struct = alpha.structured if alpha and alpha.success else {}

    pnl = outputs.get("pnl_attribution")
    pnl_struct = pnl.structured if pnl and pnl.success else {}

    skill_outputs_serial = {
        name: {
            "success": r.success,
            "narrative": r.narrative,
            "structured": r.structured if isinstance(r.structured, dict) else {},
            "error": r.error,
            "duration_ms": r.duration_ms,
        }
        for name, r in outputs.items()
    }

    existing_q = await session.execute(
        select(DailyReviewReport).where(
            DailyReviewReport.researcher_id == ctx.researcher_id,
            DailyReviewReport.trade_date == ctx.trade_date,
        )
    )
    report = existing_q.scalar_one_or_none()
    now = datetime.now(tz=UTC)
    alpha_idx = float(alpha_struct.get("alpha_vs_index", 0.0) or 0.0)
    alpha_sec = float(alpha_struct.get("alpha_vs_sector", 0.0) or 0.0)
    win_rate = float(pnl_struct.get("win_rate", 0.0) or 0.0)
    total_pnl = float(pnl_struct.get("total_pnl", 0.0) or 0.0)
    tokens_used = sum(r.tokens_used for r in outputs.values())

    # 计算 embedding 供 pattern_match RAG;失败不阻断落库
    embedding = await _safe_embed(coach_md) if coach_md else None

    if report is None:
        report = DailyReviewReport(
            id=f"review_{ctx.trade_date.strftime('%Y%m%d')}_"
               f"{(ctx.researcher_id or 'anon')[:8]}_{uuid4().hex[:6]}",
            researcher_id=ctx.researcher_id or "",
            trade_date=ctx.trade_date,
            generated_at=now,
            coach_report_md=coach_md,
            skill_outputs=skill_outputs_serial,
            alpha_vs_index=alpha_idx,
            alpha_vs_sector=alpha_sec,
            win_rate=win_rate,
            total_pnl=total_pnl,
            embedding=embedding,
            tokens_used=tokens_used,
        )
        session.add(report)
    else:
        report.generated_at = now
        report.coach_report_md = coach_md
        report.skill_outputs = skill_outputs_serial
        report.alpha_vs_index = alpha_idx
        report.alpha_vs_sector = alpha_sec
        report.win_rate = win_rate
        report.total_pnl = total_pnl
        if embedding is not None:
            report.embedding = embedding
        report.tokens_used = tokens_used
    await session.flush()
    return report


async def _safe_embed(text: str) -> list[float] | None:
    """生成 embedding,失败时静默返回 None。"""
    try:
        from app.skills.postclose.pattern_match import embed_text
        return await embed_text(text)
    except Exception:
        logger.exception("safe_embed 失败")
        return None


# ── SSE helpers ──
def _format_sse(event: SkillEvent) -> str:
    payload = {
        "skill_name": event.skill_name,
        "chunk": event.chunk,
        "narrative": event.narrative,
        "structured": event.structured,
        "error": event.error,
        "meta": event.meta,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return f"event: {event.type.value}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
