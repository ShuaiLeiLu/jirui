"""盘前 skill chain 服务 —— 编排 + 落库。

把 skill registry/orchestrator 和 PreopenAiDigest 持久化串起来:
  1) 装配本次需要运行的 skill 列表
  2) 同步模式:跑完一次性返回完整 digest
  3) 流式模式:yield SkillEvent,供 SSE 端点用
  4) 跑完后落库,供次日 yesterday_review 召回
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.openclaw.digest_push import flush_digest_pushes, queue_preopen_digest_summary
from app.models.preopen import PreopenAiDigest
from app.models.researcher import Researcher, ResearcherThesisLog
from app.skills.base import SkillContext, SkillEvent
from app.skills.orchestrator import SkillOrchestrator
from app.skills.registry import get_skill_registry
from app.skills.shared.run_log import write_skill_run_logs

logger = logging.getLogger(__name__)


# Phase 1 跑 3 个,Phase 2 上线后可换成 FULL_PREOPEN_SKILL_NAMES
_DEFAULT_SKILL_NAMES = [
    "limit_up_structure",
    "yesterday_review",
    "main_thesis",
]


def _resolve_skill_names() -> list[str]:
    """根据 registry 中已注册的 skill 自动挑选。

    若 Phase 2 数据 skill 都已注册,自动用完整链;否则降级到 Phase 1。
    """
    registry = get_skill_registry()
    full_names = [
        "overseas_market",
        "capital_flow",
        "longhubang",
        "limit_up_structure",
        "sector_rotation",
        "news_catalyst",
        "catalyst_calendar",
        "index_technical",
        "yesterday_review",
        "main_thesis",
    ]
    if all(registry.get_optional(n) is not None for n in full_names):
        return full_names
    return _DEFAULT_SKILL_NAMES


def _build_orchestrator() -> SkillOrchestrator:
    registry = get_skill_registry()
    names = _resolve_skill_names()
    skills = [registry.get(n) for n in names]
    return SkillOrchestrator(skills)


async def run_preopen_chain(
    session: AsyncSession, trade_date: date | None = None,
) -> dict:
    """非流式版本:跑完返回 dict(供测试/调度任务使用)。"""
    target_date = trade_date or date.today()
    existing = await get_existing_preopen_digest(session, target_date)
    if existing is not None:
        return _digest_to_result(existing, reused=True)

    orch = _build_orchestrator()
    ctx = SkillContext(
        trade_date=target_date,
        extra={"session": session},
    )
    outputs = await orch.run(ctx)
    digest = await _persist_digest(session, ctx, outputs)
    await _persist_thesis_logs_for_active_researchers(session, digest)
    await write_skill_run_logs(
        session, chain_kind="preopen", trade_date=ctx.trade_date, outputs=outputs,
    )
    result = _digest_to_result(digest, reused=False)
    queue_preopen_digest_summary(session, result)
    return result


async def get_existing_preopen_digest(
    session: AsyncSession, trade_date: date,
) -> PreopenAiDigest | None:
    result = await session.execute(
        select(PreopenAiDigest).where(PreopenAiDigest.trade_date == trade_date)
    )
    return result.scalar_one_or_none()


def _digest_to_result(digest: PreopenAiDigest, *, reused: bool) -> dict:
    return {
        "digest_id": digest.id,
        "trade_date": digest.trade_date.isoformat(),
        "main_thesis_md": digest.main_thesis_md,
        "bias": digest.bias,
        "skill_outputs": digest.skill_outputs,
        "reused": reused,
    }


async def stream_preopen_chain(
    session: AsyncSession, trade_date: date | None = None,
) -> AsyncIterator[str]:
    """流式版本:yield SSE 文本行。

    SSE 格式: `event: <type>\\ndata: <json>\\n\\n`
    """
    target_date = trade_date or date.today()
    existing = await get_existing_preopen_digest(session, target_date)
    if existing is not None:
        yield _format_sse_event(
            "persisted",
            {
                "digest_id": existing.id,
                "trade_date": existing.trade_date.isoformat(),
                "bias": existing.bias,
                "reused": True,
            },
        )
        return

    orch = _build_orchestrator()
    ctx = SkillContext(
        trade_date=target_date,
        extra={"session": session},
    )
    try:
        async for event in orch.run_stream(ctx):
            yield _format_sse(event)
    except Exception as exc:
        logger.exception("preopen skill chain 流式异常")
        yield _format_sse_error(str(exc))
        return

    try:
        digest = await _persist_digest(session, ctx, ctx.outputs)
        await _persist_thesis_logs_for_active_researchers(session, digest)
        await write_skill_run_logs(
            session, chain_kind="preopen", trade_date=ctx.trade_date,
            outputs=ctx.outputs,
        )
        result = _digest_to_result(digest, reused=False)
        queue_preopen_digest_summary(session, result)
        await session.commit()
        await flush_digest_pushes(session)
        yield _format_sse_event(
            "persisted",
            {
                "digest_id": digest.id,
                "trade_date": digest.trade_date.isoformat(),
                "bias": digest.bias,
                "reused": False,
            },
        )
    except Exception as exc:
        await session.rollback()
        logger.exception("digest 落库失败")
        yield _format_sse_error(f"落库失败: {exc}")


async def _persist_digest(
    session: AsyncSession, ctx: SkillContext, outputs: dict,
) -> PreopenAiDigest:
    """把 chain 结果写入 PreopenAiDigest(upsert by trade_date)。"""
    main_result = outputs.get("main_thesis")
    main_thesis_md = main_result.narrative if main_result and main_result.success else ""
    structured = main_result.structured if main_result and main_result.success else {}
    bias = str(structured.get("bias", "")) if isinstance(structured, dict) else ""
    falsification = (
        structured.get("falsification_signals", [])
        if isinstance(structured, dict)
        else []
    )

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
    tokens_used = sum(r.tokens_used for r in outputs.values())

    existing_q = await session.execute(
        select(PreopenAiDigest).where(
            PreopenAiDigest.trade_date == ctx.trade_date,
        )
    )
    digest = existing_q.scalar_one_or_none()
    now = datetime.now(tz=UTC)
    if digest is None:
        digest = PreopenAiDigest(
            id=f"digest_{ctx.trade_date.strftime('%Y%m%d')}_{uuid4().hex[:6]}",
            trade_date=ctx.trade_date,
            generated_at=now,
            main_thesis_md=main_thesis_md,
            skill_outputs=skill_outputs_serial,
            falsification_signals=list(falsification) if isinstance(falsification, list) else [],
            bias=bias,
            tokens_used=tokens_used,
        )
        session.add(digest)
    else:
        digest.generated_at = now
        digest.main_thesis_md = main_thesis_md
        digest.skill_outputs = skill_outputs_serial
        digest.falsification_signals = (
            list(falsification) if isinstance(falsification, list) else []
        )
        digest.bias = bias
        digest.tokens_used = tokens_used
    await session.flush()
    return digest


async def _persist_thesis_logs_for_active_researchers(
    session: AsyncSession, digest: PreopenAiDigest,
) -> int:
    """digest 落库后,给每个 active researcher 写一条 thesis_log。

    bias / falsification_signals 直接复制 digest 中的全局值;
    T+1 凌晨调度任务会回填 actual_result 和 correctness。
    """
    q = await session.execute(
        select(Researcher.id).where(Researcher.status == "active")
    )
    researcher_ids = [row for row in q.scalars().all()]
    if not researcher_ids:
        return 0

    main_struct = (
        digest.skill_outputs.get("main_thesis", {}) if digest.skill_outputs else {}
    ).get("structured", {})
    key_drivers = (
        main_struct.get("intraday_checkpoints", [])
        if isinstance(main_struct, dict)
        else []
    )

    for rid in researcher_ids:
        # 同日同 researcher 已存在则跳过(由 trade_date + researcher_id 唯一确定)
        existing = await session.execute(
            select(ResearcherThesisLog.id).where(
                ResearcherThesisLog.researcher_id == rid,
                ResearcherThesisLog.trade_date == digest.trade_date,
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            continue
        log = ResearcherThesisLog(
            id=f"thesis_{digest.trade_date.strftime('%Y%m%d')}_{rid[:8]}_{uuid4().hex[:6]}",
            researcher_id=rid,
            trade_date=digest.trade_date,
            direction_call=digest.bias or "",
            key_drivers=list(key_drivers) if isinstance(key_drivers, list) else [],
            falsification_signals=list(digest.falsification_signals or []),
            actual_result={},
            correctness="pending",
        )
        session.add(log)
    await session.flush()
    return len(researcher_ids)


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
    # 删除 None 字段减小 payload
    payload = {k: v for k, v in payload.items() if v is not None}
    return f"event: {event.type.value}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_sse_error(message: str) -> str:
    return _format_sse_event("error", {"error": message})
