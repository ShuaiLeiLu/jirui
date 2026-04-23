from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_session
from app.modules.documents.schemas import DocumentDetail, DocumentSummary, DocumentType
from app.modules.documents.service import DocumentService
from app.schemas.common import ApiResponse, ListResponse

router = APIRouter(prefix="/documents", tags=["documents"])
service = DocumentService()


@router.get("")
async def list_documents(
    doc_type: DocumentType | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[DocumentSummary]]:
    if not session:
        return ApiResponse(data=ListResponse(items=[], total=0))
    items, total = await service.async_list_documents(
        session,
        doc_type=doc_type,
        limit=limit,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data=ListResponse(items=items, total=total))


@router.get("/hot")
async def hot_documents(
    limit: int = 5,
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[ListResponse[DocumentSummary]]:
    if not session:
        return ApiResponse(data=ListResponse(items=[], total=0))
    items = await service.async_hot_documents(session, limit=limit)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[DocumentDetail]:
    if not session:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="数据库不可用")
    return ApiResponse(data=await service.async_get_document(session, document_id))
