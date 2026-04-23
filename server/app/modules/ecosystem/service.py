"""生态系统领域服务。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ecosystem.schemas import KnowledgeBaseItem, McpServerItem, SkillItem
from app.repositories.ecosystem_repo import KnowledgeBaseRepository, McpServerRepository, SkillPackRepository


class EcosystemService:
    """生态系统服务，只保留真实数据库路径。"""

    async def async_list_knowledge_bases(self, session: AsyncSession, owner_id: str) -> list[KnowledgeBaseItem]:
        repo = KnowledgeBaseRepository(session)
        items = await repo.list_by_owner(owner_id)
        return [
            KnowledgeBaseItem(
                kb_id=item.id,
                name=item.name,
                document_count=item.doc_count,
                updated_at=item.updated_at,
            )
            for item in items
        ]

    async def async_list_skills(self, session: AsyncSession) -> list[SkillItem]:
        repo = SkillPackRepository(session)
        items = await repo.list_all(limit=100)
        return [
            SkillItem(
                skill_id=item.id,
                name=item.name,
                description=item.description,
                installed=False,
            )
            for item in items
        ]

    async def async_list_mcp_servers(self, session: AsyncSession) -> list[McpServerItem]:
        repo = McpServerRepository(session)
        items = await repo.list_all(limit=100)
        return [
            McpServerItem(
                server_id=item.id,
                name=item.name,
                category=item.category,
                connected=False,
            )
            for item in items
        ]
