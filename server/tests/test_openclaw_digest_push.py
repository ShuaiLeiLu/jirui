from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from app.integrations.openclaw.client import OpenClawTradePushClient
from app.integrations.openclaw.digest_push import (
    build_daily_review_message,
    build_preopen_digest_message,
    push_daily_review_summary,
    push_preopen_digest_summary,
)


def _digest_result(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "digest_id": "digest_20260526_test",
        "trade_date": "2026-05-26",
        "main_thesis_md": (
            "## 一、今日核心矛盾\n半导体能否扩散。\n\n"
            "## 二、主线判断\n关注先进封装。\n\n"
            "- 观察中芯国际承接\n"
            "- 不追高炸板票"
        ),
        "bias": "bullish",
        "skill_outputs": {},
        "reused": False,
    }
    data.update(overrides)
    return data


def _review_result(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "report_id": "review_20260526_r1",
        "trade_date": "2026-05-26",
        "researcher_id": "r1",
        "researcher_name": "策略研究员",
        "coach_report_md": (
            "## 今日总结\n执行纪律改善，但尾盘追高仍需控制。\n\n"
            "## 问题复盘\n下午对高位票处理偏慢。\n\n"
            "- 明天降低追涨仓位\n"
            "- 优先检查止损纪律"
        ),
        "alpha_vs_index": 1.23,
        "alpha_vs_sector": -0.45,
        "win_rate": 0.6,
        "total_pnl": 3200.0,
        "generated_at": datetime(2026, 5, 26, 16, 0, tzinfo=UTC),
        "reused": False,
    }
    data.update(overrides)
    return data


def test_build_preopen_digest_message_contains_core_fields() -> None:
    message = build_preopen_digest_message(_digest_result())

    assert "【极睿智投｜盘前摘要】" in message
    assert "日期：2026-05-26" in message
    assert "方向：bullish" in message
    assert "半导体能否扩散" in message
    assert "关注先进封装" in message
    assert "观察中芯国际承接" in message
    assert "digest_20260526_test" in message
    assert "不构成投资建议" in message


def test_build_daily_review_message_contains_researcher_metrics() -> None:
    message = build_daily_review_message(_review_result())

    assert "【极睿智投｜盘后复盘摘要】" in message
    assert "研究员：策略研究员" in message
    assert "日期：2026-05-26" in message
    assert "相对指数：+1.23%" in message
    assert "相对板块：-0.45%" in message
    assert "胜率：60.0%" in message
    assert "当日盈亏：+3200.00 元" in message
    assert "执行纪律改善" in message
    assert "下午对高位票处理偏慢" in message
    assert "明天降低追涨仓位" in message


def test_build_daily_review_message_uses_readable_sections() -> None:
    message = build_daily_review_message(_review_result())

    assert "\n\n【核心指标】\n" in message
    assert "\n\n【复盘摘要】\n" in message
    assert "\n\n【行动建议】\n" in message
    assert "- 明天降低追涨仓位" in message
    assert "- 优先检查止损纪律" in message
    assert message.count("\n\n") >= 4


def test_build_preopen_digest_message_uses_readable_sections() -> None:
    message = build_preopen_digest_message(_digest_result())

    assert "\n\n【基本信息】\n" in message
    assert "\n\n【盘前摘要】\n" in message
    assert "\n\n【观察清单】\n" in message
    assert "- 观察中芯国际承接" in message
    assert "- 不追高炸板票" in message
    assert message.count("\n\n") >= 4


@pytest.mark.asyncio
async def test_push_preopen_digest_summary_skips_reused_digest() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenClawTradePushClient(
            endpoint_url="https://openclaw.example/broadcast",
            token="push-token",
            http_client=http_client,
        )
        delivered = await push_preopen_digest_summary(
            _digest_result(reused=True),
            client=client,
        )

    assert delivered is False
    assert requests == []


@pytest.mark.asyncio
async def test_push_daily_review_summary_sends_message_with_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenClawTradePushClient(
            endpoint_url="https://openclaw.example/broadcast",
            token="push-token",
            http_client=http_client,
        )
        delivered = await push_daily_review_summary(
            _review_result(),
            client=client,
        )

    assert delivered is True
    assert len(requests) == 1
    assert requests[0].headers.get("authorization") == "Bearer push-token"
    assert b"message" in requests[0].content
    assert "盘后复盘摘要" in requests[0].content.decode("utf-8")
