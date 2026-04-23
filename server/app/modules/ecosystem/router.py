from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_session
from app.core.security import get_current_user_id
from app.modules.ecosystem.schemas import KnowledgeBaseItem, McpServerItem, SkillItem
from app.modules.ecosystem.service import EcosystemService
from app.schemas.common import ApiResponse, ListResponse

router = APIRouter(prefix="/ecosystem", tags=["ecosystem"])
service = EcosystemService()


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[KnowledgeBaseItem]]:
    items = await service.async_list_knowledge_bases(session, user_id) if session else []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/skills")
async def list_skills(
    installed: bool | None = None,
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[SkillItem]]:
    items = await service.async_list_skills(session) if session else []
    if installed is not None:
        items = [item for item in items if item.installed is installed]
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/mcp-servers")
async def list_mcp_servers(
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[McpServerItem]]:
    items = await service.async_list_mcp_servers(session) if session else []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))
