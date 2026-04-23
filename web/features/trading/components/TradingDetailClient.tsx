/**
 * 模拟交易详情页
 *
 * 布局（参照目标站截图）：
 *  - 顶部：返回链接 + 页面标题
 *  - 左上：账户总览（总资产、今日盈亏、收益率、持仓市值、可用资金）
 *  - 左侧：持仓列表侧边栏（当前持仓 / 历史记录 切换）
 *  - 右侧主区域：两个 Tab（交易日志 / 历史交易）
 *    - 交易日志：按日期分组的成交记录表格 + 操作总结文本
 *    - 历史交易：收益曲线图、月度收益柱状图、风险指标、投资日历、成交明细表
 *
 * 数据流：
 *  - useTradingAccount(researcherId) → 账户数据
 *  - useTradingPositions(researcherId) → 持仓列表
 *  - useTradingRecords(researcherId) → 成交记录
 */
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Skeleton, Tag } from 'antd';
import { LeftOutlined } from '@ant-design/icons';
import ReactEChartsCore from 'echarts-for-react';
import dayjs from 'dayjs';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  useTradingAll,
  useTradingRealtimeStream,
  useTradingStatsWhenEnabled,
} from '@/features/trading/hooks';
import { routes } from '@/lib/constants/routes';
import type { PositionItem, TradeLogItem, TradeRecord, TradingStats } from '@/types/trading';

// ──────────── 工具函数 ────────────

/** 根据正负值返回颜色类名 */
function pnlColor(v: number) {
  if (v > 0) return 'text-rose-500';
  if (v < 0) return 'text-emerald-600';
  return 'text-slate-500';
}

/** 根据正负值返回背景色类名 */
function pnlBg(v: number) {
  if (v > 0) return 'bg-rose-50';
  if (v < 0) return 'bg-emerald-50';
  return 'bg-slate-50';
}

/** 格式化为万元 */
function fmtWan(v: number) {
  return (v / 10000).toFixed(2) + '万';
}

/** 格式化百分比 */
function fmtPct(v: number) {
  const pct = (v * 100).toFixed(2);
  return v > 0 ? `+${pct}%` : `${pct}%`;
}

/** 格式化金额 */
function fmtMoney(v: number) {
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ──────────── 子组件 ────────────

/** 持仓列表侧边栏 */
function PositionSidebar({
  positions,
  records,
  activeSymbol,
  onSelect,
  tab,
  onTabChange,
}: {
  positions: PositionItem[];
  records: TradeRecord[];
  activeSymbol: string | null;
  onSelect: (symbol: string) => void;
  tab: 'current' | 'history';
  onTabChange: (t: 'current' | 'history') => void;
}) {
  return (
    <div className="flex flex-col h-full">
      {/* Tab 切换 */}
      <div className="flex border-b border-slate-100">
        <button
          type="button"
          onClick={() => onTabChange('current')}
          className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
            tab === 'current'
              ? 'text-violet-600 border-b-2 border-violet-500'
              : 'text-slate-400 hover:text-slate-600'
          }`}
        >
          当前持仓 <span className="text-slate-300">{positions.length}</span>
        </button>
        <button
          type="button"
          onClick={() => onTabChange('history')}
          className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
            tab === 'history'
              ? 'text-violet-600 border-b-2 border-violet-500'
              : 'text-slate-400 hover:text-slate-600'
          }`}
        >
          最近交易
        </button>
      </div>

      {/* 持仓列表 */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'history' ? (
          records.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-400">暂无最近交易</div>
          ) : (
            records.slice(0, 12).map((record) => (
              <div key={record.trade_id} className="px-3 py-3 border-b border-slate-50">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium text-slate-700">{record.name || record.symbol}</div>
                    <div className="text-xs text-slate-400">
                      {dayjs(record.created_at).format('MM-DD HH:mm')} · {record.quantity}股
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-bold ${record.side === 'buy' ? 'text-rose-500' : 'text-emerald-600'}`}>
                      {record.side === 'buy' ? '买入' : '卖出'}
                    </div>
                    <div className="text-xs text-slate-400">{record.price.toFixed(2)} 元</div>
                  </div>
                </div>
              </div>
            ))
          )
        ) : positions.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-400">暂无持仓</div>
        ) : (
          positions.map((p) => {
            const active = p.symbol === activeSymbol;
            const pctChange = p.cost_price > 0 ? (p.current_price - p.cost_price) / p.cost_price : 0;
            return (
              <button
                key={p.symbol}
                type="button"
                onClick={() => onSelect(p.symbol)}
                className={`w-full text-left px-3 py-2.5 border-b border-slate-50 transition-colors hover:bg-violet-50 ${
                  active ? 'bg-violet-50' : ''
                }`}
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-slate-700">{p.name}</div>
                    <div className="text-xs text-slate-400">
                      {p.quantity}股 · 成本 {p.cost_price.toFixed(2)} · 现价 {p.current_price.toFixed(2)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-bold ${pnlColor(p.pnl)}`}>
                      {p.pnl > 0 ? '+' : ''}{fmtMoney(p.pnl)}
                    </div>
                    <div className={`text-xs ${pnlColor(pctChange)}`}>
                      {fmtPct(pctChange)}
                    </div>
                  </div>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

/** 单条 trade 日志 → 成交表格 */
function TradeLogTradeBlock({ log }: { log: TradeLogItem }) {
  const records = log.trade_records;
  if (records.length === 0) return null;
  const isSell = records[0]?.side === 'sell';

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-slate-400 border-b border-slate-100">
            <th className="py-2 px-4 text-left font-medium">股票名称</th>
            <th className="py-2 px-4 text-left font-medium">股票代码</th>
            <th className="py-2 px-4 text-right font-medium">{isSell ? '成本价格' : '买入价格'}</th>
            {isSell && <th className="py-2 px-4 text-right font-medium">卖出价格</th>}
            <th className="py-2 px-4 text-right font-medium">{isSell ? '卖出数量' : '买入数量'}</th>
            <th className="py-2 px-4 text-right font-medium">{isSell ? '卖出金额' : '买入金额'}</th>
            {isSell && <th className="py-2 px-4 text-right font-medium">盈亏金额</th>}
            {isSell && <th className="py-2 px-4 text-right font-medium">盈亏比例</th>}
            {!isSell && <th className="py-2 px-4 text-right font-medium">仓位比例</th>}
            <th className="py-2 px-4 text-center font-medium">交易结果</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => {
            const amount = r.price * r.quantity;
            const pnlVal = r.realized_pnl ?? 0;
            const pnlPctStr = r.realized_pnl_pct !== null && r.realized_pnl_pct !== undefined
              ? fmtPct(r.realized_pnl_pct)
              : '-';
            const resultLabel = r.side === 'buy'
              ? '买入'
              : pnlVal > 0
                ? '盈利'
                : pnlVal < 0
                  ? '亏损'
                  : '保本';
            return (
              <tr key={r.trade_id} className="border-b border-slate-50 hover:bg-slate-50/50">
                <td className="py-2.5 px-4 font-medium text-slate-700">{r.name || r.symbol}</td>
                <td className="py-2.5 px-4 text-slate-500">{r.symbol}</td>
                <td className="py-2.5 px-4 text-right text-slate-600">
                  {r.cost_price ? r.cost_price.toFixed(2) + ' 元' : '-'}
                </td>
                {isSell && (
                  <td className="py-2.5 px-4 text-right text-slate-600">
                    {r.side === 'sell' ? r.price.toFixed(2) + ' 元' : '-'}
                  </td>
                )}
                <td className="py-2.5 px-4 text-right text-slate-600">{r.quantity} 股</td>
                <td className="py-2.5 px-4 text-right text-slate-600">{fmtMoney(amount)} 元</td>
                {isSell && (
                  <td className={`py-2.5 px-4 text-right font-medium ${pnlColor(pnlVal)}`}>
                    {`${pnlVal > 0 ? '+' : ''}${fmtMoney(pnlVal)} 元`}
                  </td>
                )}
                {isSell && (
                  <td className={`py-2.5 px-4 text-right ${pnlColor(pnlVal)}`}>{pnlPctStr}</td>
                )}
                {!isSell && (
                  <td className="py-2.5 px-4 text-right text-slate-600">
                    {r.position_ratio !== null && r.position_ratio !== undefined ? fmtPct(r.position_ratio) : '-'}
                  </td>
                )}
                <td className="py-2.5 px-4 text-center">
                  <Tag color={r.side === 'buy' ? 'red' : 'green'} className="!text-xs">
                    {resultLabel}
                  </Tag>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 单条 analysis 日志 → Markdown 富文本 */
function TradeLogAnalysisBlock({ log }: { log: TradeLogItem }) {
  return (
    <div className="px-4 py-3">
      {log.title && (
        <div className="text-sm font-bold text-slate-700 mb-2">{log.title}</div>
      )}
      <div className="prose prose-sm prose-slate max-w-none text-sm leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{log.content}</ReactMarkdown>
      </div>
    </div>
  );
}

/** 交易日志 Tab —— 按日期分组，交替渲染 trade 表格 + analysis 文本 */
function TradeLogTab({ logs }: { logs: TradeLogItem[] }) {
  /** 按日期分组 */
  const grouped = useMemo(() => {
    const groups: Record<string, TradeLogItem[]> = {};
    for (const log of logs) {
      const date = dayjs(log.created_at).format('YYYY/MM/DD');
      if (!groups[date]) groups[date] = [];
      groups[date].push(log);
    }
    return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]));
  }, [logs]);

  if (logs.length === 0) {
    return <div className="py-12 text-center text-sm text-slate-400">暂无交易日志</div>;
  }

  return (
    <div className="space-y-6">
      {grouped.map(([date, dayLogs]) => {
        const tradeLogs = dayLogs.filter((l) => l.log_type === 'trade');
        const tradeCount = tradeLogs.reduce((n, l) => n + l.trade_records.length, 0);

        return (
          <div key={date}>
            {/* 日期标题 */}
            <div className="mb-3 flex items-center gap-2">
              <span className="font-medium text-sm text-slate-700">{date}</span>
              {tradeCount > 0 && (
                <Tag color="blue" className="!text-xs !px-1.5 !py-0">{tradeCount}笔交易</Tag>
              )}
            </div>

            {/* 日志条目列表 */}
            <div className="space-y-3">
              {dayLogs.map((log) => (
                <div
                  key={log.log_id}
                  className="rounded-lg border border-slate-100 overflow-hidden bg-white"
                >
                  {/* 时间戳 */}
                  <div className="flex items-center gap-2 px-4 py-2 bg-slate-50/70 border-b border-slate-100">
                    <span className="text-xs text-slate-400">
                      {dayjs(log.created_at).format('HH:mm:ss')}
                    </span>
                    <Tag
                      color={log.log_type === 'trade' ? 'orange' : 'purple'}
                      className="!text-xs !px-1.5 !py-0"
                    >
                      {log.log_type === 'trade' ? 'TRADE' : 'ANALYSIS'}
                    </Tag>
                  </div>

                  {/* 内容 */}
                  {log.log_type === 'trade' ? (
                    <TradeLogTradeBlock log={log} />
                  ) : (
                    <TradeLogAnalysisBlock log={log} />
                  )}
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* 底部提示 */}
      <div className="text-center text-xs text-slate-400 py-4">
        已加载全部日志（共 {logs.length} 条）
      </div>
    </div>
  );
}

/** 历史交易 Tab —— 对标参考站：收益曲线、月度收益柱状图、投资日历、风控指标、成交明细 */
function HistoryTab({ stats, records }: { stats: TradingStats | null; records: TradeRecord[] }) {
  /** 收益曲线 ECharts 配置 */
  const equityOption = useMemo(() => {
    if (!stats || stats.equity_curve.length === 0) return null;
    const initial = stats.initial_capital;
    const dates = stats.equity_curve.map((p) => p.date);
    const values = stats.equity_curve.map((p) => p.equity);
    const yieldPct = stats.equity_curve.map((p) => ((p.equity - initial) / initial) * 100);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const p = params[0];
          const idx = p.dataIndex;
          return `${p.name}<br/>总资产: ¥${fmtMoney(values[idx])}<br/>收益率: ${yieldPct[idx].toFixed(2)}%`;
        },
      },
      grid: { left: 60, right: 20, top: 30, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 10, color: '#94a3b8' },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          fontSize: 10,
          color: '#94a3b8',
          formatter: (v: number) => ((v - initial) / initial * 100).toFixed(1) + '%',
        },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      series: [
        {
          type: 'line',
          data: values,
          smooth: true,
          symbol: 'circle',
          symbolSize: 4,
          lineStyle: { color: '#8b5cf6', width: 2 },
          itemStyle: { color: '#8b5cf6' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(139,92,246,0.15)' },
                { offset: 1, color: 'rgba(139,92,246,0.01)' },
              ],
            },
          },
        },
      ],
    };
  }, [stats]);

  /** 月度收益柱状图 ECharts 配置 */
  const monthlyOption = useMemo(() => {
    if (!stats || stats.monthly_returns.length === 0) return null;
    const months = stats.monthly_returns.map((m) => m.month.slice(5) + '月');
    const values = stats.monthly_returns.map((m) => m.pnl);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const p = params[0];
          return `${stats.monthly_returns[p.dataIndex].month}<br/>收益: ${p.value >= 0 ? '+' : ''}¥${fmtMoney(p.value)}`;
        },
      },
      grid: { left: 60, right: 20, top: 20, bottom: 30 },
      xAxis: {
        type: 'category',
        data: months,
        axisLabel: { fontSize: 10, color: '#94a3b8' },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 10, color: '#94a3b8' },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      series: [
        {
          type: 'bar',
          data: values.map((v) => ({
            value: v,
            itemStyle: { color: v >= 0 ? '#f43f5e' : '#10b981', borderRadius: [3, 3, 0, 0] },
          })),
          barWidth: '40%',
        },
      ],
    };
  }, [stats]);

  /** 投资日历数据（基于后端 daily_returns） */
  const calendarData = useMemo(() => {
    const now = dayjs();
    const startOfMonth = now.startOf('month');
    const daysInMonth = now.daysInMonth();
    const result: { day: number; weekday: number; pnl: number }[] = [];

    // 按日聚合
    const dailyMap: Record<number, number> = {};
    if (stats?.daily_returns) {
      for (const dr of stats.daily_returns) {
        const d = dayjs(dr.date);
        if (d.month() === now.month() && d.year() === now.year()) {
          dailyMap[d.date()] = dr.pnl;
        }
      }
    }

    for (let i = 1; i <= daysInMonth; i++) {
      const date = startOfMonth.date(i);
      result.push({ day: i, weekday: date.day(), pnl: dailyMap[i] || 0 });
    }
    return result;
  }, [stats]);

  /** 风控指标来自后端 */
  const risk = stats?.risk;

  return (
    <div className="space-y-6">
      {/* ── 收益曲线 ── */}
      <div className="rounded-lg border border-slate-100 bg-white p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-medium text-slate-700">收益曲线</div>
          {risk && (
            <div className="flex items-center gap-4 text-xs">
              <span className="text-slate-400">
                累计收益
                <span className={`ml-1 font-bold ${pnlColor(risk.total_return)}`}>{fmtPct(risk.total_return)}</span>
              </span>
              <span className="text-slate-400">
                年化收益
                <span className={`ml-1 font-bold ${pnlColor(risk.annual_return)}`}>{fmtPct(risk.annual_return)}</span>
              </span>
            </div>
          )}
        </div>
        {equityOption ? (
          <ReactEChartsCore option={equityOption} style={{ height: 260 }} />
        ) : (
          <div className="h-[260px] flex items-center justify-center text-sm text-slate-400">暂无数据</div>
        )}
      </div>

      {/* ── 月度收益 + 投资日历 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 月度收益 */}
        <div className="rounded-lg border border-slate-100 bg-white p-4 flex flex-col">
          <div className="text-sm font-medium text-slate-700 mb-3">月度收益</div>
          <div className="flex-1 min-h-[200px]">
            {monthlyOption ? (
              <ReactEChartsCore option={monthlyOption} style={{ height: '100%', minHeight: 200 }} />
            ) : (
              <div className="h-full flex items-center justify-center text-sm text-slate-400">暂无数据</div>
            )}
          </div>
        </div>

        {/* 投资日历 */}
        <div className="rounded-lg border border-slate-100 bg-white p-4 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-medium text-slate-700">投资日历</div>
            <div className="text-xs text-slate-400">{dayjs().format('YYYY年M月')}</div>
          </div>
          {/* 星期头 */}
          <div className="grid grid-cols-7 gap-1 mb-1">
            {['日', '一', '二', '三', '四', '五', '六'].map((d) => (
              <div key={d} className="text-center text-xs text-slate-400 py-1">{d}</div>
            ))}
          </div>
          {/* 日历格子 */}
          <div className="grid grid-cols-7 gap-1">
            {/* 月初空白 */}
            {Array.from({ length: calendarData[0]?.weekday || 0 }).map((_, i) => (
              <div key={`empty-${i}`} className="h-14" />
            ))}
            {calendarData.map((d) => {
              const isToday = d.day === dayjs().date();
              const hasTrade = d.pnl !== 0;
              /** 收益率：基于初始资金的当日 pnl 百分比 */
              const pnlPctStr = hasTrade
                ? `${d.pnl > 0 ? '+' : ''}${((d.pnl / (stats?.initial_capital || 1000000)) * 100).toFixed(2)}%`
                : '';
              return (
                <div
                  key={d.day}
                  className={`h-14 flex flex-col items-center justify-center rounded transition-colors ${
                    isToday
                      ? 'bg-violet-500 text-white font-bold'
                      : hasTrade
                        ? d.pnl > 0
                          ? 'bg-rose-50'
                          : 'bg-emerald-50'
                        : 'hover:bg-slate-50'
                  }`}
                >
                  <span className={`text-xs ${
                    isToday ? 'text-white' : 'text-slate-600'
                  }`}>
                    {d.day}
                  </span>
                  {hasTrade && (
                    <span className={`text-[10px] leading-tight mt-0.5 ${
                      isToday
                        ? 'text-white/80'
                        : d.pnl > 0
                          ? 'text-rose-500'
                          : 'text-emerald-600'
                    }`}>
                      {pnlPctStr}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          {/* 日历图例 */}
          <div className="flex items-center justify-end gap-3 mt-2 text-xs text-slate-400">
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-rose-100" />盈利</span>
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-emerald-100" />亏损</span>
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-violet-500" />今日</span>
          </div>
        </div>
      </div>

      {/* ── 风控指标 ── */}
      <div className="rounded-lg border border-slate-100 bg-white p-4">
        <div className="text-sm font-medium text-slate-700 mb-3">风控指标</div>
        {risk ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {[
              { label: '累计收益率', value: fmtPct(risk.total_return), color: pnlColor(risk.total_return) },
              { label: '年化收益率', value: fmtPct(risk.annual_return), color: pnlColor(risk.annual_return) },
              { label: '最大回撤', value: fmtPct(risk.max_drawdown), color: 'text-emerald-600' },
              { label: '夏普比率', value: risk.sharpe.toFixed(2), color: 'text-slate-700' },
              { label: '交易胜率', value: fmtPct(risk.win_rate), color: 'text-rose-500' },
              { label: '盈亏比', value: risk.profit_loss_ratio.toFixed(2), color: 'text-slate-700' },
              { label: '总交易笔数', value: String(risk.total_trades), color: 'text-slate-700' },
              { label: '盈利笔数', value: String(risk.win_trades), color: 'text-rose-500' },
              { label: '亏损笔数', value: String(risk.lose_trades), color: 'text-emerald-600' },
              { label: '最大单笔盈利', value: `¥${fmtMoney(risk.max_profit)}`, color: 'text-rose-500' },
              { label: '最大单笔亏损', value: `¥${fmtMoney(risk.max_loss)}`, color: 'text-emerald-600' },
              { label: '平均持仓天数', value: `${risk.avg_hold_days}天`, color: 'text-slate-700' },
            ].map((m) => (
              <div key={m.label} className="rounded-lg bg-slate-50 px-3 py-2.5">
                <div className="text-xs text-slate-400 mb-1">{m.label}</div>
                <div className={`text-sm font-bold ${m.color}`}>{m.value}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-6 text-center text-sm text-slate-400">暂无数据</div>
        )}
      </div>

      {/* ── 每笔交易明细 ── */}
      <div className="rounded-lg border border-slate-100 bg-white overflow-hidden">
        <div className="px-4 py-3 bg-slate-50 flex items-center justify-between">
          <span className="text-sm font-medium text-slate-700">每笔交易明细</span>
          <span className="text-xs text-slate-400">共 {records.length} 笔</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-400 border-b border-slate-100">
                <th className="py-2 px-4 text-left font-medium">时间</th>
                <th className="py-2 px-4 text-left font-medium">股票代码</th>
                <th className="py-2 px-4 text-left font-medium">股票名称</th>
                <th className="py-2 px-4 text-center font-medium">方向</th>
                <th className="py-2 px-4 text-right font-medium">成交价</th>
                <th className="py-2 px-4 text-right font-medium">数量（股）</th>
                <th className="py-2 px-4 text-right font-medium">成交额</th>
                <th className="py-2 px-4 text-right font-medium">手续费</th>
              </tr>
            </thead>
            <tbody>
              {records.length === 0 ? (
                <tr><td colSpan={8} className="py-8 text-center text-slate-400">暂无交易记录</td></tr>
              ) : (
                [...records].reverse().map((r) => (
                  <tr key={r.trade_id} className="border-b border-slate-50 hover:bg-slate-50/50">
                    <td className="py-2.5 px-4 text-slate-500 whitespace-nowrap">{dayjs(r.created_at).format('YYYY-MM-DD HH:mm')}</td>
                    <td className="py-2.5 px-4 text-slate-600">{r.symbol}</td>
                    <td className="py-2.5 px-4 font-medium text-slate-700">{r.name || '-'}</td>
                    <td className="py-2.5 px-4 text-center">
                      <Tag color={r.side === 'buy' ? 'red' : 'green'} className="!text-xs">
                        {r.side === 'buy' ? '买入' : '卖出'}
                      </Tag>
                    </td>
                    <td className="py-2.5 px-4 text-right text-slate-600">{r.price.toFixed(2)}</td>
                    <td className="py-2.5 px-4 text-right text-slate-600">{r.quantity.toLocaleString()}</td>
                    <td className="py-2.5 px-4 text-right text-slate-600">{fmtMoney(r.price * r.quantity)}</td>
                    <td className="py-2.5 px-4 text-right text-slate-400">{r.commission ? fmtMoney(r.commission) : '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ──────────── 主组件 ────────────

export function TradingDetailClient({ researcherId }: { researcherId: string }) {
  const [mainTab, setMainTab] = useState<'log' | 'history'>('log'); // 主区域 tab
  const [sideTab, setSideTab] = useState<'current' | 'history'>('current'); // 侧边栏 tab
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null); // 选中的持仓

  const realtime = useTradingRealtimeStream(researcherId);
  const allQuery = useTradingAll(researcherId);
  const statsQuery = useTradingStatsWhenEnabled(researcherId, mainTab === 'history');

  const loading = allQuery.isLoading;
  const acct = allQuery.data?.account;
  const positions = allQuery.data?.positions ?? [];
  const records = allQuery.data?.records ?? [];
  const logs = allQuery.data?.logs ?? [];
  const stats = statsQuery.data ?? null;

  /** 初始资金（以后端返回为准，缺失时兜底 100 万） */
  const INITIAL = acct?.initial_capital ?? stats?.initial_capital ?? 1_000_000;

  /** 总收益率 */
  const totalReturnPct = acct ? (acct.total_asset - INITIAL) / INITIAL : 0;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* 顶部导航栏 */}
      <div className="bg-white border-b border-slate-200 px-4 sm:px-6 py-3">
        <div className="flex items-center justify-between gap-3">
          <Link
            href={routes.aiResearcher as any}
            className="flex items-center gap-1 text-sm text-slate-500 hover:text-violet-600 transition-colors"
          >
            <LeftOutlined className="text-xs" />
            <span>模拟交易详情</span>
          </Link>
          <div className="flex items-center gap-2">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${
              realtime.status === 'live'
                ? 'bg-emerald-500'
                : realtime.status === 'connecting'
                  ? 'bg-amber-400'
                  : realtime.status === 'error'
                    ? 'bg-rose-500'
                    : 'bg-slate-300'
            }`} />
            <span className="text-xs text-slate-400">
              {realtime.status === 'live'
                ? '实时推送中'
                : realtime.status === 'connecting'
                  ? '正在连接实时行情'
                  : realtime.status === 'error'
                    ? '实时流重连中'
                    : '实时流未开启'}
            </span>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="p-6"><Skeleton active paragraph={{ rows: 10 }} /></div>
      ) : (
        <div className="flex flex-col lg:flex-row">
          {/* ── 左侧面板 ── */}
          <div className="w-full lg:w-64 shrink-0 bg-white border-r border-slate-100">
            {/* 账户概览 */}
            <div className="p-4 border-b border-slate-100">
              <div className="text-2xl font-bold text-slate-800">{fmtWan(acct?.total_asset ?? 0)}</div>
              <div className="flex items-center gap-2 mt-1 text-xs">
                <span className="text-slate-400">今日盈亏</span>
                <span className={`font-medium ${pnlColor(acct?.daily_pnl ?? 0)}`}>
                  {(acct?.daily_pnl ?? 0) > 0 ? '+' : ''}{fmtMoney(acct?.daily_pnl ?? 0)}
                </span>
                <span className={`${pnlColor(totalReturnPct)}`}>
                  收益率 {fmtPct(totalReturnPct)}
                </span>
              </div>
              <div className="flex gap-3 mt-3">
                <div className={`flex-1 rounded-lg px-3 py-2 ${pnlBg(acct?.holding_value ?? 0)}`}>
                  <div className="text-xs text-slate-400">持仓市值</div>
                  <div className="text-sm font-bold text-slate-700">{fmtWan(acct?.holding_value ?? 0)}</div>
                </div>
                <div className="flex-1 rounded-lg bg-slate-50 px-3 py-2">
                  <div className="text-xs text-slate-400">可用资金</div>
                  <div className="text-sm font-bold text-slate-700">{fmtWan(acct?.available_cash ?? 0)}</div>
                </div>
              </div>
            </div>

            {/* 持仓列表 */}
            <PositionSidebar
              positions={positions}
              records={records}
              activeSymbol={activeSymbol}
              onSelect={setActiveSymbol}
              tab={sideTab}
              onTabChange={setSideTab}
            />
          </div>

          {/* ── 右侧主区域 ── */}
          <div className="flex-1 min-w-0">
            {/* Tab 切换 */}
            <div className="bg-white border-b border-slate-100 px-4 sm:px-6">
              <div className="flex gap-6">
                <button
                  type="button"
                  onClick={() => setMainTab('log')}
                  className={`py-3 text-sm font-medium transition-colors border-b-2 ${
                    mainTab === 'log'
                      ? 'text-violet-600 border-violet-500'
                      : 'text-slate-400 border-transparent hover:text-slate-600'
                  }`}
                >
                  交易日志
                </button>
                <button
                  type="button"
                  onClick={() => setMainTab('history')}
                  className={`py-3 text-sm font-medium transition-colors border-b-2 ${
                    mainTab === 'history'
                      ? 'text-violet-600 border-violet-500'
                      : 'text-slate-400 border-transparent hover:text-slate-600'
                  }`}
                >
                  历史交易
                </button>
              </div>
            </div>

            {/* Tab 内容 */}
            <div className="p-4 sm:p-6">
              {mainTab === 'log' && <TradeLogTab logs={logs} />}
              {mainTab === 'history' && (
                <HistoryTab stats={stats} records={records} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
