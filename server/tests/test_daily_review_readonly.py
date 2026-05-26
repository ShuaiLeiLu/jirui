from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.trading import DailyReviewReport
from app.modules.trading import skill_service as trading_skill_service
from app.skills.base import SkillContext, SkillResult


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _FakeSession:
    def __init__(self, value: object | None) -> None:
        self.value = value
        self.flushed = False
        self.info: dict[str, object] = {}
        self.added: list[object] = []

    async def execute(self, _stmt: object) -> _ScalarResult:
        return _ScalarResult(self.value)

    async def flush(self) -> None:
        self.flushed = True

    def add(self, item: object) -> None:
        self.added.append(item)


def _report() -> DailyReviewReport:
    return DailyReviewReport(
        id="review_20260525_r1",
        researcher_id="r1",
        trade_date=date(2026, 5, 25),
        generated_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC),
        coach_report_md="## 一、今日总结\n小胜，但追高问题仍在。",
        skill_outputs={"daily_coach": {"structured": {"rating": "small_win"}}},
        alpha_vs_index=1.2,
        alpha_vs_sector=0.4,
        win_rate=0.6,
        total_pnl=3200,
        embedding=None,
        tokens_used=456,
    )


@pytest.mark.asyncio
async def test_get_existing_daily_review_report_reads_stored_report() -> None:
    report = await trading_skill_service.get_existing_daily_review_report(
        _FakeSession(_report()),  # type: ignore[arg-type]
        researcher_id="r1",
        trade_date=date(2026, 5, 25),
    )

    assert report is not None
    assert report.id == "review_20260525_r1"
    assert report.coach_report_md.startswith("## 一、今日总结")
    assert report.alpha_vs_index == 1.2


@pytest.mark.asyncio
async def test_get_existing_daily_review_report_returns_none_when_missing() -> None:
    report = await trading_skill_service.get_existing_daily_review_report(
        _FakeSession(None),  # type: ignore[arg-type]
        researcher_id="r1",
        trade_date=date(2026, 5, 25),
    )

    assert report is None


@pytest.mark.asyncio
async def test_run_daily_review_reuses_existing_report_without_running_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return "acct1", "研究员"

    def fail_build_orchestrator() -> object:
        raise AssertionError("existing report should skip orchestrator")

    monkeypatch.setattr(trading_skill_service, "_resolve_account_and_researcher", fake_resolve)
    monkeypatch.setattr(trading_skill_service, "_build_orchestrator", fail_build_orchestrator)

    session = _FakeSession(_report())
    data = await trading_skill_service.run_daily_review(
        session,  # type: ignore[arg-type]
        researcher_id="r1",
        trade_date=date(2026, 5, 25),
    )

    assert data["report_id"] == "review_20260525_r1"
    assert data["reused"] is True
    assert data["coach_report_md"] == "## 一、今日总结\n小胜，但追高问题仍在。"
    assert "openclaw_digest_pushes" not in session.info


@pytest.mark.asyncio
async def test_run_daily_review_queues_push_for_new_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOrchestrator:
        async def run(self, ctx: SkillContext) -> dict[str, SkillResult]:
            return {
                "pnl_attribution": SkillResult(
                    skill_name="pnl_attribution",
                    success=True,
                    structured={"win_rate": 0.6, "total_pnl": 3200.0},
                ),
                "alpha_analysis": SkillResult(
                    skill_name="alpha_analysis",
                    success=True,
                    structured={"alpha_vs_index": 1.2, "alpha_vs_sector": 0.4},
                ),
                "daily_coach": SkillResult(
                    skill_name="daily_coach",
                    success=True,
                    narrative="## 今日总结\n执行纪律改善。",
                ),
            }

    async def fake_resolve(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return "acct1", "策略研究员"

    async def fake_embed(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        trading_skill_service, "_resolve_account_and_researcher", fake_resolve
    )
    monkeypatch.setattr(
        trading_skill_service, "_build_orchestrator", lambda: FakeOrchestrator()
    )
    monkeypatch.setattr(trading_skill_service, "_safe_embed", fake_embed)

    session = _FakeSession(None)
    data = await trading_skill_service.run_daily_review(
        session,  # type: ignore[arg-type]
        researcher_id="r1",
        trade_date=date(2026, 5, 26),
    )

    assert data["reused"] is False
    assert data["researcher_name"] == "策略研究员"
    messages = session.info["openclaw_digest_pushes"]
    assert len(messages) == 1
    assert "【极睿智投｜盘后复盘摘要】" in messages[0]
    assert "研究员：策略研究员" in messages[0]
    assert "执行纪律改善" in messages[0]


@pytest.mark.asyncio
async def test_stream_daily_review_reuses_existing_report_without_running_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return "acct1", "研究员"

    def fail_build_orchestrator() -> object:
        raise AssertionError("existing report should skip streaming orchestrator")

    monkeypatch.setattr(trading_skill_service, "_resolve_account_and_researcher", fake_resolve)
    monkeypatch.setattr(trading_skill_service, "_build_orchestrator", fail_build_orchestrator)

    chunks = [
        chunk
        async for chunk in trading_skill_service.stream_daily_review(
            _FakeSession(_report()),  # type: ignore[arg-type]
            researcher_id="r1",
            trade_date=date(2026, 5, 25),
        )
    ]

    payload = "".join(chunks)
    assert "review_20260525_r1" in payload
    assert '"reused": true' in payload
