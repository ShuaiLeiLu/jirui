"""
LLM 客户端 —— 基于 httpx 实现 OpenAI 兼容的聊天补全

功能：
  - chat(): 一次性返回完整回复
  - chat_stream(): SSE 流式输出，逐 token yield
  - 支持 OpenAI / DeepSeek / Qwen / 月之暗面等兼容 API
  - 自动重试和超时处理

使用方式：
  client = LLMClient(base_url="...", api_key="...", model="...")
  reply = await client.chat([LLMMessage(role="user", content="你好")])
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    """聊天消息"""
    role: str          # system / user / assistant
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMConfig:
    """LLM 配置（可从 Settings 自动加载）"""
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: float = 60.0

    @classmethod
    def from_settings(cls) -> LLMConfig:
        """从全局 Settings 加载 LLM 配置"""
        settings = get_settings()
        return cls(
            base_url=settings.openai_base_url or "",
            api_key=settings.openai_api_key or "",
            model=settings.openai_model or "gpt-4o-mini",
        )


class LLMClient:
    """OpenAI 兼容的 LLM 聊天客户端

    支持一次性返回和 SSE 流式输出两种模式。
    当 base_url 或 api_key 未配置时，直接报错，由上层决定如何处理错误。
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_settings()
        self._client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        """LLM 是否已配置（base_url 和 api_key 均非空）"""
        return bool(self.config.base_url and self.config.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 异步客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        return self._client

    def _build_payload(
        self,
        messages: list[LLMMessage],
        *,
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """构建 OpenAI 兼容的请求体"""
        return {
            "model": self.config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": stream,
        }

    # ── 一次性回复 ──

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """发送聊天请求，返回完整回复文本
        """
        if not self.is_configured:
            logger.warning("LLM 未配置（base_url 或 api_key 为空）")
            raise RuntimeError("LLM 服务未配置")

        client = await self._get_client()
        payload = self._build_payload(
            messages, stream=False, temperature=temperature, max_tokens=max_tokens
        )

        try:
            resp = await client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            logger.error("LLM API 返回错误: %s %s", e.response.status_code, e.response.text[:200])
            raise
        except Exception as e:
            logger.error("LLM 请求失败: %s", e)
            raise

    # ── SSE 流式输出 ──

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """发送聊天请求，以 SSE 流式逐 token 返回
        """
        if not self.is_configured:
            logger.warning("LLM 未配置")
            raise RuntimeError("LLM 服务未配置")

        client = await self._get_client()
        payload = self._build_payload(
            messages, stream=True, temperature=temperature, max_tokens=max_tokens
        )

        try:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    # SSE 格式：data: {...}
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]  # 去掉 "data: " 前缀
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.HTTPStatusError as e:
            logger.error("LLM 流式 API 返回错误: %s", e.response.status_code)
            raise
        except Exception as e:
            logger.error("LLM 流式请求失败: %s", e)
            raise

    # ── 资源清理 ──

    async def close(self) -> None:
        """关闭底层 httpx 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

# ── 全局单例 ──

_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
