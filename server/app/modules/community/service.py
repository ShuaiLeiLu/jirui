"""社区领域服务。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.community import Post as PostModel
from app.modules.community.schemas import CommunityCreatePostRequest, CommunityPost, CommunityPostDetail
from app.repositories.community_repo import PostRepository


class CommunityService:
    """社区领域服务，只保留真实数据库路径。"""

    async def async_list_posts(self, session: AsyncSession, *, q: str | None = None) -> list[CommunityPost]:
        repo = PostRepository(session)
        posts = await repo.list_posts(sort="latest")
        keyword = (q or "").strip().lower()
        result: list[CommunityPost] = []
        for post in posts:
            if keyword and keyword not in post.title.lower() and keyword not in post.content.lower():
                continue
            result.append(
                CommunityPost(
                    post_id=post.id,
                    title=post.title,
                    author="",
                    excerpt=post.content[:80],
                    likes=post.like_count,
                    comments=post.comment_count,
                    created_at=post.created_at,
                )
            )
        return result

    async def async_get_post(self, session: AsyncSession, post_id: str) -> CommunityPostDetail:
        repo = PostRepository(session)
        post = await repo.get_by_id(post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="帖子不存在")
        return CommunityPostDetail(
            post_id=post.id,
            title=post.title,
            author="",
            excerpt=post.content[:80],
            likes=post.like_count,
            comments=post.comment_count,
            created_at=post.created_at,
            content=post.content,
            tags=[],
            comment_list=[],
        )

    async def async_create_post(
        self, session: AsyncSession, user_id: str, payload: CommunityCreatePostRequest
    ) -> CommunityPostDetail:
        repo = PostRepository(session)
        post = PostModel(
            id=f"p_{uuid4().hex[:10]}",
            author_id=user_id,
            title=payload.title,
            content=payload.content,
            category="discussion",
        )
        await repo.create(post)
        await session.commit()
        return CommunityPostDetail(
            post_id=post.id,
            title=post.title,
            author="",
            excerpt=post.content[:80],
            likes=0,
            comments=0,
            created_at=post.created_at,
            content=post.content,
            tags=payload.tags,
            comment_list=[],
        )
