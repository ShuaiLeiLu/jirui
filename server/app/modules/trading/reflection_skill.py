"""交易反思 skill。

在每次模拟盘成交后输出一段结构化复盘，包含：
  - 交易动作与原因回放
  - 风险/执行反思
  - 次日观察与展望
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.llm.client import LLMMessage, get_llm_client

logger = logging.getLogger(__name__)


class TradingReflectionSkill:
    """统一生成交易复盘内容，优先走 LLM，失败时回退模板。"""

    def build_trade_log_title(self, trade_context: dict[str, Any]) -> str:
        action = "买入复盘" if trade_context.get("side") == "buy" else "卖出复盘"
        name = str(trade_context.get("name") or trade_context.get("symbol") or "交易")
        symbol = str(trade_context.get("symbol") or "").strip()
        return f"{action}｜{name}({symbol})" if symbol else f"{action}｜{name}"

    def build_fallback_reflection(
        self,
        *,
        researcher_name: str,
        researcher_prompt: str,
        trade_context: dict[str, Any],
    ) -> str:
        side = str(trade_context.get("side") or "buy")
        action_label = "买入" if side == "buy" else "卖出"
        symbol = str(trade_context.get("symbol") or "-")
        name = str(trade_context.get("name") or symbol)
        quantity = int(trade_context.get("quantity") or 0)
        price = float(trade_context.get("price") or 0.0)
        amount = float(trade_context.get("amount") or 0.0)
        commission = float(trade_context.get("commission") or 0.0)
        reason = str(trade_context.get("reason") or "按既定交易计划执行")
        realized_pnl = trade_context.get("realized_pnl")
        realized_pnl_pct = trade_context.get("realized_pnl_pct")
        total_asset = float(trade_context.get("total_asset") or 0.0)
        available_cash = float(trade_context.get("available_cash") or 0.0)
        position_ratio = float(trade_context.get("position_ratio") or 0.0)
        style_hint = researcher_prompt.strip() or "围绕小市值轮动纪律做交易复盘"

        pnl_line = ""
        if realized_pnl is not None:
            pnl_val = float(realized_pnl)
            pnl_pct_text = (
                f"{float(realized_pnl_pct) * 100:+.2f}%"
                if realized_pnl_pct is not None
                else "-"
            )
            pnl_line = (
                f"- 收益回顾：本次平仓收益 {pnl_val:+,.2f} 元，收益率 {pnl_pct_text}。"
            )

        risk_line = (
            "- 风险提醒：继续关注仓位集中度、成交量变化与次日开盘承接，避免情绪化追涨杀跌。"
            if side == "buy"
            else "- 风险提醒：卖出后关注原逻辑是否被证伪，以及腾出的资金是否有更高胜率的去向。"
        )
        outlook_line = (
            f"- 次日展望：观察 {name}({symbol}) 是否延续资金关注，重点看开盘强弱、量价匹配和同题材联动；"
            "若不及预期，优先遵守纪律而不是主观加仓。"
            if side == "buy"
            else f"- 次日展望：继续跟踪 {name}({symbol}) 卖出后的走势，验证这次退出是否提升了组合效率；"
            "若板块仍强，寻找更优的小市值替代标的。"
        )

        lines = [
            "## 交易复盘",
            f"- 研究员：{researcher_name}",
            f"- 执行动作：{action_label} {name}({symbol}) {quantity} 股，成交价 {price:.2f} 元，成交额 {amount:,.2f} 元，手续费 {commission:,.2f} 元。",
            f"- 触发原因：{reason}。",
            f"- 研究设定：{style_hint}。",
        ]
        if position_ratio > 0:
            lines.append(f"- 仓位观察：本笔成交约占初始资金的 {position_ratio * 100:.2f}%。")
        if pnl_line:
            lines.append(pnl_line)

        lines.extend(
            [
                "",
                "## 执行反思",
                "- 本次操作需要回到策略约束本身，确认是因子/风控驱动，还是被盘中情绪带偏。",
                risk_line,
                "",
                "## 次日展望",
                outlook_line,
            ]
        )

        if total_asset > 0 or available_cash > 0:
            lines.extend(
                [
                    "",
                    "## 账户状态",
                    f"- 当前总资产：{total_asset:,.2f} 元",
                    f"- 当前可用资金：{available_cash:,.2f} 元",
                ]
            )

        return "\n".join(lines)

    async def build_trade_reflection(
        self,
        *,
        researcher_name: str,
        researcher_prompt: str,
        trade_context: dict[str, Any],
    ) -> str:
        llm = get_llm_client()
        fallback = self.build_fallback_reflection(
            researcher_name=researcher_name,
            researcher_prompt=researcher_prompt,
            trade_context=trade_context,
        )
        if not llm.is_configured:
            return fallback

        system_prompt = (
            "你是一名A股交易复盘研究员，需要在每次模拟盘成交后输出专业、克制、可执行的交易反思。"
            "请严格使用 Markdown，包含以下标题："
            "`## 交易复盘`、`## 执行反思`、`## 次日展望`。"
            "语言保持简洁、具体，避免空泛口号。"
        )
        user_prompt = (
            f"研究员名称：{researcher_name}\n"
            f"研究员提示词：{researcher_prompt or '未额外配置'}\n"
            f"交易上下文：{trade_context}\n\n"
            "请围绕这次成交，输出一次真实的交易复盘，既要解释本次操作，也要给出下一个交易日的观察重点。"
        )

        try:
            reply = await llm.chat(
                [
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
                temperature=0.4,
                max_tokens=900,
            )
            text = reply.strip()
            if "## 次日展望" not in text:
                return fallback
            return text
        except Exception as exc:
            logger.warning("交易复盘 LLM 生成失败，回退模板: %s", exc)
            return fallback
