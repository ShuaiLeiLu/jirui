from __future__ import annotations

import logging
import re
from typing import Any

from app.integrations.openclaw.client import OpenClawTradePushClient

logger = logging.getLogger(__name__)
_SESSION_KEY = "openclaw_digest_pushes"


def build_preopen_digest_message(result: dict[str, Any]) -> str:
    trade_date = str(result.get("trade_date") or "")
    bias = str(result.get("bias") or "unknown")
    digest_id = str(result.get("digest_id") or "")
    summary = _markdown_summary(str(result.get("main_thesis_md") or ""))
    return (
        "【极睿智投｜盘前摘要】\n"
        f"日期：{trade_date}\n"
        f"方向：{bias}\n"
        "摘要：\n"
        f"{summary or '盘前摘要已生成，请打开极睿智投查看完整内容。'}\n"
        f"编号：{digest_id}\n"
        "提示：以上为 AI 盘前投研摘要，不构成投资建议。"
    )


def build_daily_review_message(result: dict[str, Any]) -> str:
    trade_date = str(result.get("trade_date") or "")
    researcher_name = str(
        result.get("researcher_name") or result.get("researcher_id") or "未命名研究员"
    )
    summary = _markdown_summary(str(result.get("coach_report_md") or ""))
    alpha_index = _format_pct(result.get("alpha_vs_index"))
    alpha_sector = _format_pct(result.get("alpha_vs_sector"))
    win_rate = _format_win_rate(result.get("win_rate"))
    total_pnl = _format_money(result.get("total_pnl"))
    return (
        "【极睿智投｜盘后复盘摘要】\n"
        f"研究员：{researcher_name}\n"
        f"日期：{trade_date}\n"
        f"相对指数：{alpha_index}\n"
        f"相对板块：{alpha_sector}\n"
        f"胜率：{win_rate}\n"
        f"当日盈亏：{total_pnl} 元\n"
        "摘要：\n"
        f"{summary or '盘后复盘已生成，请打开极睿智投查看完整内容。'}\n"
        "提示：以上为模拟盘盘后复盘信息，不构成投资建议。"
    )


async def push_preopen_digest_summary(
    result: dict[str, Any],
    *,
    client: OpenClawTradePushClient | None = None,
) -> bool:
    if result.get("reused"):
        return False
    return await _push_message(build_preopen_digest_message(result), client=client)


async def push_daily_review_summary(
    result: dict[str, Any],
    *,
    client: OpenClawTradePushClient | None = None,
) -> bool:
    if result.get("reused"):
        return False
    return await _push_message(build_daily_review_message(result), client=client)


def queue_preopen_digest_summary(session: Any, result: dict[str, Any]) -> bool:
    if result.get("reused"):
        return False
    return _queue_message(session, build_preopen_digest_message(result))


def queue_daily_review_summary(session: Any, result: dict[str, Any]) -> bool:
    if result.get("reused"):
        return False
    return _queue_message(session, build_daily_review_message(result))


async def flush_digest_pushes(
    session: Any,
    *,
    client: OpenClawTradePushClient | None = None,
) -> int:
    messages = list(getattr(session, "info", {}).get(_SESSION_KEY) or [])
    if not messages:
        return 0
    session.info[_SESSION_KEY] = []

    owns_client = client is None
    push_client = client or OpenClawTradePushClient()
    delivered = 0
    try:
        for message in messages:
            try:
                await push_client.push_trade({"message": message})
                delivered += 1
            except Exception as exc:
                logger.warning("OpenClaw 摘要推送失败: %s", exc)
    finally:
        if owns_client:
            await push_client.close()
    return delivered


def discard_digest_pushes(session: Any) -> None:
    if hasattr(session, "info"):
        session.info[_SESSION_KEY] = []


async def _push_message(message: str, *, client: OpenClawTradePushClient | None) -> bool:
    owns_client = client is None
    push_client = client or OpenClawTradePushClient()
    try:
        await push_client.push_trade({"message": message})
        return push_client.is_configured
    except Exception as exc:
        logger.warning("OpenClaw 摘要推送失败: %s", exc)
        return False
    finally:
        if owns_client:
            await push_client.close()


def _queue_message(session: Any, message: str) -> bool:
    if not hasattr(session, "info"):
        return False
    session.info.setdefault(_SESSION_KEY, []).append(message)
    return True


def _markdown_summary(markdown: str, *, max_chars: int = 1200) -> str:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            text = re.sub(r"^#+\s*", "", line).strip()
        else:
            text = re.sub(r"^[>*_\-`#\s]+", "", line).strip()
        text = re.sub(r"[*_`]+", "", text)
        text = re.sub(r"\s+", " ", text)
        if text:
            lines.append(text)
        joined = "\n".join(lines)
        if len(joined) >= max_chars:
            return joined[:max_chars].rstrip() + "..."
    return "\n".join(lines)


def _format_pct(value: object) -> str:
    number = _to_float(value)
    return f"{number:+.2f}%"


def _format_win_rate(value: object) -> str:
    number = _to_float(value)
    return f"{number * 100:.1f}%"


def _format_money(value: object) -> str:
    number = _to_float(value)
    return f"{number:+.2f}"


def _to_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
