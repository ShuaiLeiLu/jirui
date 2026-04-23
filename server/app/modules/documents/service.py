from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document as DocumentModel
from app.modules.documents.schemas import DocumentDetail, DocumentSummary, DocumentType
from app.repositories.researcher_repo import ResearcherRepository


class DocumentService:
    """研究文档领域服务。

    当前先用内存数据支撑页面联调，后续接入文档表与检索索引。
    """

    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        # 示例文档，覆盖 market/stock 两类典型场景。
        self._documents: dict[str, DocumentDetail] = {
            "d_market_1": DocumentDetail(
                document_id="d_market_1",
                title="4月盘前市场结构速览",
                researcher_name="技术派阿龙",
                document_type="market",
                symbol=None,
                view_count=1420,
                like_count=86,
                created_at=now - timedelta(hours=5),
                content_markdown="## 市场概述\n指数震荡上行，资金偏好高景气成长与高股息防御并行。",
                tags=["盘前", "市场结构", "风险提示"],
            ),
            "d_stock_1": DocumentDetail(
                document_id="d_stock_1",
                title="东软载波短中期趋势跟踪",
                researcher_name="情绪超短阿发",
                document_type="stock",
                symbol="300183",
                view_count=1021,
                like_count=63,
                created_at=now - timedelta(hours=9),
                content_markdown="## 个股结论\n维持震荡偏强判断，关注量价背离风险。",
                tags=["个股", "技术面", "量价"],
            ),
        }

    def list_documents(
        self,
        doc_type: DocumentType | None = None,
        limit: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> tuple[list[DocumentSummary], int]:
        items = list(self._documents.values())
        if doc_type:
            items = [item for item in items if item.document_type == doc_type]

        total = len(items)
        paginated = items
        if limit is not None:
            paginated = items[:limit]
        elif page is not None or page_size is not None:
            effective_page = page or 1
            effective_page_size = page_size or 20
            start = (effective_page - 1) * effective_page_size
            end = start + effective_page_size
            paginated = items[start:end]

        # 列表接口仅返回 summary，避免一次性返回大文本正文。
        return [
            DocumentSummary(**item.model_dump(include=set(DocumentSummary.model_fields.keys())))
            for item in paginated
        ], total

    def get_document(self, document_id: str) -> DocumentDetail:
        item = self._documents.get(document_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
        return item

    def hot_documents(self, limit: int = 5) -> list[DocumentSummary]:
        sorted_items = sorted(
            self._documents.values(),
            key=lambda item: (item.view_count, item.like_count),
            reverse=True,
        )[:limit]
        return [DocumentSummary(**item.model_dump(include=set(DocumentSummary.model_fields.keys()))) for item in sorted_items]

    @staticmethod
    def _map_document_type(raw_type: str) -> DocumentType:
        mapping = {
            "report": "market",
            "analysis": "stock",
            "strategy": "industry",
            "note": "topic",
            "market": "market",
            "stock": "stock",
            "industry": "industry",
            "topic": "topic",
        }
        return mapping.get(raw_type, "topic")

    async def async_list_documents(
        self,
        session: AsyncSession,
        *,
        doc_type: DocumentType | None = None,
        limit: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> tuple[list[DocumentSummary], int]:
        stmt = select(DocumentModel).order_by(DocumentModel.created_at.desc())
        result = await session.execute(stmt)
        documents = list(result.scalars().all())

        if doc_type:
            documents = [
                item for item in documents
                if self._map_document_type(item.doc_type) == doc_type
            ]

        total = len(documents)
        if limit is not None:
            documents = documents[:limit]
        elif page is not None or page_size is not None:
            effective_page = page or 1
            effective_page_size = page_size or 20
            start = (effective_page - 1) * effective_page_size
            end = start + effective_page_size
            documents = documents[start:end]

        repo = ResearcherRepository(session)
        researcher_names: dict[str, str] = {}
        items: list[DocumentSummary] = []
        for doc in documents:
            if doc.researcher_id not in researcher_names:
                researcher = await repo.get_by_id(doc.researcher_id)
                researcher_names[doc.researcher_id] = researcher.name if researcher else "未知"
            items.append(DocumentSummary(
                document_id=doc.id,
                title=doc.title,
                researcher_name=researcher_names[doc.researcher_id],
                document_type=self._map_document_type(doc.doc_type),
                symbol=None,
                view_count=doc.view_count,
                like_count=0,
                created_at=doc.created_at,
            ))
        return items, total

    async def async_hot_documents(self, session: AsyncSession, *, limit: int = 5) -> list[DocumentSummary]:
        items, _ = await self.async_list_documents(session, limit=limit)
        return sorted(items, key=lambda item: (item.view_count, item.like_count), reverse=True)[:limit]

    async def async_get_document(self, session: AsyncSession, document_id: str) -> DocumentDetail:
        stmt = select(DocumentModel).where(DocumentModel.id == document_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

        repo = ResearcherRepository(session)
        researcher = await repo.get_by_id(doc.researcher_id)
        return DocumentDetail(
            document_id=doc.id,
            title=doc.title,
            researcher_name=researcher.name if researcher else "未知",
            document_type=self._map_document_type(doc.doc_type),
            symbol=None,
            view_count=doc.view_count,
            like_count=0,
            created_at=doc.created_at,
            content_markdown=doc.content,
            tags=[],
        )
