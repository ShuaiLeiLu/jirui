"""研究员领域服务。"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.client import LLMMessage, get_llm_client
from app.models.researcher import Researcher as ResearcherModel
from app.models.researcher import ResearcherHire as HireModel
from app.repositories.researcher_repo import ResearcherHireRepository, ResearcherRepository

from app.modules.researchers.schemas import (
    ResearcherCreateRequest,
    ResearcherDetail,
    ResearcherMarketCard,
    ResearcherMarketDetail,
    ResearcherMineItem,
    ResearcherPublishRecord,
    ResearcherSummary,
    ResearcherTestChatResponse,
    ResearcherUpdateRequest,
    WorkbenchHiredResearcher,
    WorkbenchHotDocument,
    WorkbenchOverview,
    WorkbenchPublicRankItem,
    WorkbenchQuickAction,
    WorkbenchRankSortBy,
)


class ResearcherService:
    """研究员领域服务，只保留真实数据库路径。"""

    def __init__(self) -> None:
        self._workbench_quick_actions: list[WorkbenchQuickAction] = [
            WorkbenchQuickAction(
                action_key="new_chat",
                title="发起研究会话",
                description="和研究员快速讨论盘前计划或持仓调整。",
            ),
            WorkbenchQuickAction(
                action_key="create_document",
                title="新建研究文档",
                description="沉淀观点、跟踪假设并输出结构化报告。",
            ),
            WorkbenchQuickAction(
                action_key="risk_scan",
                title="一键风险体检",
                description="检查持仓暴露与近期回撤风险提示。",
            ),
        ]
        self._workbench_risk_disclaimer = "以上内容仅为研究观点展示，不构成投资建议。市场有风险，投资需谨慎。"

    async def async_list_researchers(self, session: AsyncSession) -> list[ResearcherSummary]:
        repo = ResearcherRepository(session)
        researchers = await repo.list_all(limit=200)
        return [self._model_to_summary(item) for item in researchers]

    async def async_get_researcher(self, session: AsyncSession, researcher_id: str) -> ResearcherDetail:
        repo = ResearcherRepository(session)
        researcher = await repo.get_by_id(researcher_id)
        if not researcher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究员不存在")
        return self._model_to_detail(researcher)

    async def async_get_market_detail(
        self, session: AsyncSession, researcher_id: str
    ) -> ResearcherMarketDetail:
        repo = ResearcherRepository(session)
        researcher = await repo.get_by_id(researcher_id)
        if not researcher or researcher.visibility != "public":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="市场中不存在该研究员")

        return ResearcherMarketDetail(
            id=researcher.id,
            name=researcher.name,
            avatar=researcher.avatar_url,
            introduction=researcher.description,
            level=researcher.level,
            hire_count=researcher.hire_count,
            version=researcher.version,
            tags=list(researcher.tags or []),
            template_visible=True,
            is_hired=False,
            resume="",
            prompt=researcher.prompt,
        )

    async def async_create_researcher(
        self, session: AsyncSession, owner_id: str, payload: ResearcherCreateRequest
    ) -> ResearcherDetail:
        repo = ResearcherRepository(session)
        model = ResearcherModel(
            id=f"r_{uuid4().hex[:10]}",
            owner_id=owner_id,
            name=payload.name,
            title=payload.title,
            style=payload.style,
            description=payload.description,
            prompt=payload.prompt,
            visibility=payload.visibility,
            skills=payload.skills,
            knowledge_bases=payload.knowledge_bases,
            mcp_servers=payload.mcp_servers,
            self_drive_tasks=payload.self_drive_tasks,
            strategy_config=payload.strategy_config,
            tags=["自定义"],
        )
        await repo.create(model)
        await session.commit()
        return self._model_to_detail(model)

    async def async_update_researcher(
        self, session: AsyncSession, researcher_id: str, payload: ResearcherUpdateRequest
    ) -> ResearcherDetail:
        repo = ResearcherRepository(session)
        researcher = await repo.get_by_id(researcher_id)
        if not researcher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究员不存在")

        updates: dict[str, object] = {}
        if payload.title is not None:
            updates["title"] = payload.title
        if payload.style is not None:
            updates["style"] = payload.style
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.prompt is not None:
            updates["prompt"] = payload.prompt
        if payload.visibility is not None:
            updates["visibility"] = payload.visibility
        if payload.skills is not None:
            updates["skills"] = payload.skills
        if payload.knowledge_bases is not None:
            updates["knowledge_bases"] = payload.knowledge_bases
        if payload.mcp_servers is not None:
            updates["mcp_servers"] = payload.mcp_servers
        if payload.self_drive_tasks is not None:
            updates["self_drive_tasks"] = payload.self_drive_tasks
        if payload.strategy_config is not None:
            updates["strategy_config"] = payload.strategy_config

        if updates:
            await repo.update(researcher, **updates)
            await session.commit()
        return self._model_to_detail(researcher)

    async def async_duplicate_researcher(
        self, session: AsyncSession, researcher_id: str, owner_id: str
    ) -> ResearcherDetail:
        repo = ResearcherRepository(session)
        source = await repo.get_by_id(researcher_id)
        if not source:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究员不存在")

        duplicated = ResearcherModel(
            id=f"r_{uuid4().hex[:10]}",
            owner_id=owner_id,
            name=f"{source.name} 副本",
            title=source.title,
            style=source.style,
            description=source.description,
            prompt=source.prompt,
            avatar_url=source.avatar_url,
            status="idle",
            visibility="draft",
            publish_status="draft",
            published_version=None,
            version="v0",
            level=source.level,
            today_pnl=0.0,
            win_rate_30d=0.0,
            skills=list(source.skills or []),
            knowledge_bases=list(source.knowledge_bases or []),
            mcp_servers=list(source.mcp_servers or []),
            tags=list(source.tags or []),
            self_drive_tasks=list(source.self_drive_tasks or []),
            strategy_config=source.strategy_config,
            is_system=False,
            hire_count=0,
        )
        await repo.create(duplicated)
        await session.commit()
        return self._model_to_detail(duplicated)

    async def async_list_mine(self, session: AsyncSession, owner_id: str) -> list[ResearcherMineItem]:
        repo = ResearcherRepository(session)
        researchers = await repo.list_by_owner(owner_id)
        return [
            ResearcherMineItem(
                id=researcher.id,
                name=researcher.name,
                avatar=researcher.avatar_url,
                introduction=researcher.description,
                level=researcher.level,
                visibility=researcher.visibility,
                published_version=researcher.published_version,
                publish_status=researcher.publish_status,
                version=researcher.version,
                updated_at=researcher.updated_at,
            )
            for researcher in researchers
        ]

    async def async_list_market(
        self, session: AsyncSession, *, q: str | None, page: int, page_size: int
    ) -> tuple[list[ResearcherMarketCard], int]:
        repo = ResearcherRepository(session)
        researchers = await repo.list_public(limit=200)

        keyword = (q or "").strip().lower()
        filtered: list[ResearcherModel] = []
        for researcher in researchers:
            tags = researcher.tags or []
            searchable = f"{researcher.name} {researcher.description} {' '.join(tags)}".lower()
            if keyword and keyword not in searchable:
                continue
            filtered.append(researcher)

        total = len(filtered)
        start = (page - 1) * page_size
        page_items = filtered[start:start + page_size]

        cards = [
            ResearcherMarketCard(
                id=researcher.id,
                name=researcher.name,
                avatar=researcher.avatar_url,
                introduction=researcher.description,
                level=researcher.level,
                hire_count=researcher.hire_count,
                version=researcher.version,
                tags=list(researcher.tags or []),
                template_visible=researcher.visibility == "public",
                is_hired=False,
            )
            for researcher in page_items
        ]
        return cards, total

    async def async_publish(self, session: AsyncSession, researcher_id: str) -> ResearcherPublishRecord:
        repo = ResearcherRepository(session)
        researcher = await repo.get_by_id(researcher_id)
        if not researcher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究员不存在")

        version_num = int(researcher.version.lstrip("v") or "0") + 1
        new_version = f"v{version_num}"
        publish_time = datetime.now(tz=UTC)

        await repo.update(
            researcher,
            visibility="public",
            publish_status="published",
            published_version=new_version,
            version=new_version,
        )
        await session.commit()
        return ResearcherPublishRecord(version=new_version, publish_time=publish_time, status="published")

    async def async_unpublish(self, session: AsyncSession, researcher_id: str) -> ResearcherPublishRecord:
        repo = ResearcherRepository(session)
        researcher = await repo.get_by_id(researcher_id)
        if not researcher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究员不存在")

        publish_time = datetime.now(tz=UTC)
        await repo.update(researcher, visibility="private", publish_status="unpublished")
        await session.commit()
        return ResearcherPublishRecord(
            version=researcher.published_version or researcher.version,
            publish_time=publish_time,
            status="unpublished",
        )

    async def async_hire(self, session: AsyncSession, user_id: str, researcher_id: str) -> None:
        from app.models.trading import TradingAccount as AccountModel

        researcher_repo = ResearcherRepository(session)
        researcher = await researcher_repo.get_by_id(researcher_id)
        if not researcher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究员不存在")

        hire_repo = ResearcherHireRepository(session)
        existing = await hire_repo.find_hire(user_id, researcher_id)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已雇佣该研究员")

        hire = HireModel(
            id=f"h_{uuid4().hex[:10]}",
            user_id=user_id,
            researcher_id=researcher_id,
            status="hired",
        )
        await hire_repo.create(hire)

        account_stmt = select(AccountModel).where(AccountModel.researcher_id == researcher_id)
        account_result = await session.execute(account_stmt)
        if account_result.scalar_one_or_none() is None:
            initial_cash = 1_000_000.0
            account = AccountModel(
                id=f"acct_{uuid4().hex[:10]}",
                user_id=user_id,
                researcher_id=researcher_id,
                total_asset=initial_cash,
                available_cash=initial_cash,
                holding_value=0.0,
                daily_pnl=0.0,
            )
            session.add(account)

        await researcher_repo.update(researcher, hire_count=researcher.hire_count + 1, status="active")
        await session.commit()

    async def async_dismiss(self, session: AsyncSession, user_id: str, researcher_id: str) -> None:
        hire_repo = ResearcherHireRepository(session)
        hire = await hire_repo.find_hire(user_id, researcher_id)
        if not hire:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到雇佣关系")

        await hire_repo.update(hire, status="dismissed")

        researcher_repo = ResearcherRepository(session)
        researcher = await researcher_repo.get_by_id(researcher_id)
        if researcher and researcher.hire_count > 0:
            await researcher_repo.update(researcher, hire_count=researcher.hire_count - 1)
        await session.commit()

    async def async_list_workbench_hired(
        self, session: AsyncSession, user_id: str
    ) -> list[WorkbenchHiredResearcher]:
        researcher_repo = ResearcherRepository(session)

        system_stmt = select(ResearcherModel).where(ResearcherModel.is_system.is_(True))
        system_result = await session.execute(system_stmt)
        system_researchers = system_result.scalars().all()

        seen_ids: set[str] = set()
        result: list[WorkbenchHiredResearcher] = []
        for researcher in system_researchers:
            seen_ids.add(researcher.id)
            result.append(self._researcher_to_hired_card(researcher))

        hire_repo = ResearcherHireRepository(session)
        hires = await hire_repo.list_hired_by_user(user_id)
        for hire in hires:
            if hire.researcher_id in seen_ids:
                continue
            researcher = await researcher_repo.get_by_id(hire.researcher_id)
            if not researcher:
                continue
            seen_ids.add(researcher.id)
            result.append(self._researcher_to_hired_card(researcher))

        return result

    @staticmethod
    def _researcher_to_hired_card(researcher: ResearcherModel) -> WorkbenchHiredResearcher:
        return WorkbenchHiredResearcher(
            researcher_id=researcher.id,
            avatar_url=researcher.avatar_url,
            name=researcher.name,
            summary=researcher.description,
            status=researcher.status,
            tags=list(researcher.tags or []),
            today_yield=researcher.today_pnl,
            win_rate_30d=researcher.win_rate_30d,
            level=researcher.level,
        )

    async def async_list_public_rankings(
        self, session: AsyncSession, *, sort_by: WorkbenchRankSortBy = "today", limit: int = 20
    ) -> list[WorkbenchPublicRankItem]:
        from app.models.trading import TradingAccount

        order_col = TradingAccount.daily_pnl.desc() if sort_by == "today" else TradingAccount.total_asset.desc()
        stmt = (
            select(ResearcherModel, TradingAccount)
            .join(TradingAccount, TradingAccount.researcher_id == ResearcherModel.id)
            .where(ResearcherModel.visibility == "public")
            .order_by(order_col)
            .limit(limit)
        )
        result = await session.execute(stmt)

        rankings: list[WorkbenchPublicRankItem] = []
        initial_asset = 1_000_000.0
        for researcher, account in result.all():
            total_asset = float(account.total_asset) if account else initial_asset
            daily_pnl = float(account.daily_pnl) if account else 0.0
            rankings.append(
                WorkbenchPublicRankItem(
                    researcher_id=researcher.id,
                    name=researcher.name,
                    total_asset=total_asset,
                    today_yield_rate=daily_pnl / total_asset if total_asset > 0 else 0.0,
                    month_yield_rate=(total_asset - initial_asset) / initial_asset if initial_asset > 0 else 0.0,
                    risk_note="模拟盘",
                )
            )
        return rankings

    async def async_test_chat(
        self, session: AsyncSession, researcher_id: str, question: str
    ) -> ResearcherTestChatResponse:
        detail = await self.async_get_researcher(session, researcher_id)
        version_used = detail.published_version or "v0"

        system_prompt = (
            f"你是一名名叫「{detail.name}」的 AI 研究员。\n"
            f"职位：{detail.title}\n"
            f"风格：{detail.style}\n"
            f"简介：{detail.description}\n\n"
        )
        if detail.prompt:
            system_prompt += f"特殊指令：{detail.prompt}\n\n"
        system_prompt += "请基于以上角色设定回答用户的问题。回复应专业、有条理，语言简洁，适当使用结构化输出。"

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=question),
        ]

        llm = get_llm_client()
        if not llm.is_configured:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM 服务未配置")

        answer = await llm.chat(messages)
        return ResearcherTestChatResponse(
            researcher_id=researcher_id,
            question=question,
            answer=answer,
            version_used=version_used,
            reply_time=datetime.now(tz=UTC),
        )

    async def async_get_workbench_overview(
        self, session: AsyncSession, user_id: str, *, sort_by: WorkbenchRankSortBy = "today"
    ) -> WorkbenchOverview:
        from app.models.document import Document as DocModel

        partial_failures: list[str] = []
        hired = await self.async_list_workbench_hired(session, user_id)

        hot_documents: list[WorkbenchHotDocument] = []
        try:
            researcher_repo = ResearcherRepository(session)
            stmt = select(DocModel).order_by(DocModel.view_count.desc()).limit(6)
            document_result = await session.execute(stmt)
            for document in document_result.scalars().all():
                researcher = await researcher_repo.get_by_id(document.researcher_id)
                hot_documents.append(
                    WorkbenchHotDocument(
                        id=document.id,
                        title=document.title,
                        summary=document.summary,
                        researcher_name=researcher.name if researcher else "未知",
                        create_time=document.created_at,
                        view_count=document.view_count,
                        comment_count=document.comment_count,
                    )
                )
        except Exception:
            partial_failures.append("hot_documents")

        rankings: list[WorkbenchPublicRankItem] = []
        try:
            rankings = await self.async_list_public_rankings(session, sort_by=sort_by)
        except Exception:
            partial_failures.append("rankings")

        return WorkbenchOverview(
            hired=hired,
            hot_documents=hot_documents,
            rankings=rankings,
            quick_actions=list(self._workbench_quick_actions),
            risk_disclaimer=self._workbench_risk_disclaimer,
            partial_failures=partial_failures,
        )

    @staticmethod
    def _model_to_summary(researcher: ResearcherModel) -> ResearcherSummary:
        return ResearcherSummary(
            researcher_id=researcher.id,
            name=researcher.name,
            title=researcher.title,
            style=researcher.style,
            status=researcher.status,
            today_pnl=researcher.today_pnl,
            win_rate_30d=researcher.win_rate_30d,
            level=researcher.level,
        )

    @staticmethod
    def _model_to_detail(researcher: ResearcherModel) -> ResearcherDetail:
        return ResearcherDetail(
            researcher_id=researcher.id,
            name=researcher.name,
            title=researcher.title,
            style=researcher.style,
            status=researcher.status,
            today_pnl=researcher.today_pnl,
            win_rate_30d=researcher.win_rate_30d,
            level=researcher.level,
            avatar_url=researcher.avatar_url,
            description=researcher.description,
            prompt=researcher.prompt,
            visibility=researcher.visibility,
            published_version=researcher.published_version,
            skills=list(researcher.skills or []),
            knowledge_bases=list(researcher.knowledge_bases or []),
            mcp_servers=list(researcher.mcp_servers or []),
            self_drive_tasks=list(researcher.self_drive_tasks or []),
            strategy_config=researcher.strategy_config,
            created_at=researcher.created_at,
            updated_at=researcher.updated_at,
        )
