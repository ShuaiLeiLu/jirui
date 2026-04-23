/**
 * AI研究员工作台 —— 完全对标目标站截图
 *
 * 布局：
 *  - 左侧面板：页面标题 + 总览入口 + 已雇佣研究员列表（彩色头像）+ 底部链接
 *  - 右侧主区域：
 *    - 未选中时 → 首页（标题说明 + 热门文档 + 排行榜）
 *    - 选中时 → 研究员详情（Header + 最新制品横向卡片 + 模拟账户持仓表）
 *
 * 数据流：
 *  - useHiredResearchers()  已雇佣研究员列表
 *  - useHotDocuments()      热门研究文档
 *  - usePublicRank()        公开排行榜
 *  - useTradingAll()        模拟账户聚合快照
 */
'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import {
  Badge,
  Button,
  Empty,
  Segmented,
  Skeleton,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import {
  AppstoreOutlined,
  ClockCircleOutlined,
  EyeOutlined,
  FileTextOutlined,
  FormOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined,
  RocketOutlined,
  SendOutlined,
  SettingOutlined,
} from '@ant-design/icons';

import { useHiredResearchers, useHotDocuments, usePublicRank } from '@/features/researcher-workbench/hooks';
import {
  useTradingAll,
  useTradingRealtimeStream,
} from '@/features/trading/hooks';
import { routes } from '@/lib/constants/routes';
import { useUserSessionStore } from '@/stores/user-session.store';
import type { HiredResearcher, HotDocument, PublicRankItem, RankSortBy } from '@/types/researcher-workbench';

// ──────────── 常量与工具函数 ────────────

/** 研究员头像映射 —— 根据名称关键词匹配 SVG 头像 */
const AVATAR_MAP: Record<string, string> = {
  '阿平': '/avatars/researcher-aping.svg',
  '阿发': '/avatars/researcher-afa.svg',
  '阿龙': '/avatars/researcher-along.svg',
};

/** 研究员头像背景色映射 */
const AVATAR_BG_MAP: Record<string, string> = {
  '阿平': 'bg-orange-100',
  '阿发': 'bg-purple-100',
  '阿龙': 'bg-blue-100',
};

/** 根据研究员名称获取头像路径 */
function getAvatarSrc(name: string): string {
  for (const [key, src] of Object.entries(AVATAR_MAP)) {
    if (name.includes(key)) return src;
  }
  return '/avatars/researcher-aping.svg';
}

/** 根据研究员名称获取头像背景色 */
function getAvatarBg(name: string): string {
  for (const [key, bg] of Object.entries(AVATAR_BG_MAP)) {
    if (name.includes(key)) return bg;
  }
  return 'bg-slate-100';
}

/** 根据收益正负返回对应的 Tailwind 文字色 */
function yieldColor(value: number) {
  if (value > 0) return 'text-rose-500';
  if (value < 0) return 'text-emerald-600';
  return 'text-slate-500';
}

/** 将小数收益率转为百分比字符串，正数加 + 号 */
function formatPct(value: number) {
  const pct = (value * 100).toFixed(2);
  return value > 0 ? `+${pct}%` : `${pct}%`;
}

/** 格式化资产金额（万） */
function formatWan(value: number) {
  return (value / 10000).toFixed(2) + '万';
}

/** 格式化资产数字，保留两位小数并加千分位 */
function formatMoney(value: number) {
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 格式化资产数字，保留整数并加千分位 */
function formatAsset(value: number) {
  return Math.round(value).toLocaleString('zh-CN');
}

/** ISO 时间字符串 → "YYYY-MM-DD" */
function formatDate(value: string) {
  const d = new Date(value);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** 计算时间距离现在多久（如"13小时前"） */
function timeAgo(value: string) {
  const now = Date.now();
  const then = new Date(value).getTime();
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}小时前`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}天前`;
}

// ──────────── 左侧面板 ────────────

/**
 * 左侧研究员列表面板
 * 对标截图：页面标题 + 辅助说明 → 总览按钮 → 研究员列表（彩色头像） → 底部链接
 */
function SidePanel({
  researchers,
  loading,
  activeId,
  onSelect,
}: {
  researchers: HiredResearcher[];
  loading: boolean;
  activeId: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      {/* 页面标题 */}
      <div className="px-4 pt-5 pb-2">
        <div className="text-base font-bold text-slate-800">AI研究员</div>
        <div className="mt-0.5 text-xs text-slate-400 leading-relaxed">
          辅助您投研决策的垂直领域专家
        </div>
      </div>

      {/* 总览入口 */}
      <div className="px-3 mb-1">
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors ${
            activeId === null
              ? 'bg-amber-50 text-amber-700 font-medium'
              : 'hover:bg-slate-50 text-slate-600'
          }`}
        >
          <AppstoreOutlined className={activeId === null ? 'text-amber-500' : 'text-slate-400'} />
          总览
        </button>
      </div>

      {/* 研究员列表 */}
      <div className="flex-1 overflow-y-auto px-3">
        {loading && (
          <div className="space-y-3 p-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} avatar active paragraph={{ rows: 0 }} />
            ))}
          </div>
        )}
        {!loading && researchers.length === 0 && (
          <div className="py-8 text-center text-xs text-slate-400">暂无研究员</div>
        )}
        {!loading &&
          researchers.map((r) => {
            const active = r.researcher_id === activeId;
            return (
              <button
                key={r.researcher_id}
                type="button"
                onClick={() => onSelect(r.researcher_id)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 mb-0.5 text-left transition-colors ${
                  active ? 'bg-brand-50 text-brand-600' : 'hover:bg-slate-50'
                }`}
              >
                {/* 彩色机器人头像 */}
                <div className={`w-9 h-9 rounded-lg overflow-hidden shrink-0 ${getAvatarBg(r.name)}`}>
                  <Image
                    src={getAvatarSrc(r.name)}
                    alt={r.name}
                    width={36}
                    height={36}
                    className="w-full h-full object-cover"
                  />
                </div>
                <span className={`truncate text-sm ${active ? 'font-semibold' : 'font-medium text-slate-700'}`}>
                  {r.name}
                </span>
              </button>
            );
          })}
      </div>

      {/* 底部链接 */}
      <div className="border-t border-slate-100 px-4 py-3 space-y-2">
        <Link
          href={routes.labTalentMarket}
          className="flex items-center gap-1 text-xs text-brand-500 hover:text-brand-600 transition-colors"
        >
          招募研究员 <RightOutlined style={{ fontSize: 10 }} />
        </Link>
        <Link
          href={routes.labCreateResearcher}
          className="flex items-center gap-1 text-xs text-brand-500 hover:text-brand-600 transition-colors"
        >
          创建研究员 <RightOutlined style={{ fontSize: 10 }} />
        </Link>
      </div>
    </div>
  );
}

// ──────────── 首页视图子组件 ────────────

/** 24小时热门文档区 */
type DocTab = 'hot' | 'latest' | 'mine';

function HotDocumentsSection({
  documents,
  loading,
}: {
  documents: HotDocument[];
  loading: boolean;
}) {
  const [docTab, setDocTab] = useState<DocTab>('hot');

  return (
    <div className="rounded-lg bg-white p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Typography.Title level={5} className="!mb-0">24小时内热门文档</Typography.Title>
        <Link href={routes.documents} className="text-xs text-brand-500 hover:text-brand-600 flex items-center gap-0.5">
          查看更多 <RightOutlined style={{ fontSize: 10 }} />
        </Link>
      </div>
      <div className="mb-4 flex items-center gap-4">
        <Segmented
          size="small"
          value={docTab}
          options={[
            { label: '热门', value: 'hot' },
            { label: '最新', value: 'latest' },
            { label: '我的', value: 'mine' },
          ]}
          onChange={(v) => setDocTab(v as DocTab)}
        />
      </div>
      {loading ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : documents.length === 0 ? (
        <div className="py-10">
          <Empty description="暂无热门文档" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      ) : (
        <div className="space-y-3">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-100 p-3 transition-colors hover:bg-slate-50"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-slate-800 line-clamp-1">{doc.title}</div>
                <div className="mt-1 text-xs text-slate-400 line-clamp-1">{doc.summary}</div>
              </div>
              <div className="shrink-0 text-right text-xs text-slate-400">
                <div>{doc.researcher_name}</div>
                <div>{doc.view_count} 浏览</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 排行榜单行 */
function RankRow({ item, sortBy }: { item: PublicRankItem; sortBy: RankSortBy }) {
  const yieldRate = sortBy === 'today' ? item.today_yield_rate : item.month_yield_rate;
  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-b-0">
      {/* 彩色头像 */}
      <div className={`w-8 h-8 rounded-lg overflow-hidden shrink-0 ${getAvatarBg(item.name)}`}>
        <Image src={getAvatarSrc(item.name)} alt={item.name} width={32} height={32} className="w-full h-full object-cover" />
      </div>
      <div className="min-w-0 flex-1">
        <span className="truncate text-sm font-medium text-slate-700">{item.name}</span>
        <div className="flex items-center gap-2 text-xs">
          <span className={yieldColor(yieldRate)}>{formatPct(yieldRate)}</span>
          <span className="text-slate-400">
            {sortBy === 'today' ? formatPct(item.month_yield_rate) : formatPct(item.today_yield_rate)}
          </span>
        </div>
      </div>
      <div className="shrink-0 text-right text-sm text-slate-600">{formatAsset(item.total_asset)}</div>
    </div>
  );
}

/** 模拟交易排名区 */
function RankingSection() {
  const [sortBy, setSortBy] = useState<RankSortBy>('today');
  const rankQuery = usePublicRank(sortBy, true);
  const rankings = rankQuery.data ?? [];
  const leftCol = rankings.filter((_, i) => i % 2 === 0);
  const rightCol = rankings.filter((_, i) => i % 2 === 1);

  return (
    <div className="rounded-lg bg-white p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <Typography.Title level={5} className="!mb-0">模拟交易排名</Typography.Title>
          <Tag color="purple" className="!text-xs">全市场研究员</Tag>
        </div>
        <div className="flex items-center gap-2">
          <Segmented
            size="small"
            value={sortBy}
            options={[
              { label: '今日排名', value: 'today' },
              { label: '本月排名', value: 'month' },
            ]}
            onChange={(v) => setSortBy(v as RankSortBy)}
          />
          <Link href="#" className="text-xs text-brand-500 hover:text-brand-600 flex items-center gap-0.5 ml-2">
            全部排名 <RightOutlined style={{ fontSize: 10 }} />
          </Link>
        </div>
      </div>
      {rankQuery.isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : rankings.length === 0 ? (
        <Empty description="暂无排名数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div className="grid grid-cols-1 gap-x-6 lg:grid-cols-2">
          <div>{leftCol.map((item) => <RankRow key={item.researcher_id} item={item} sortBy={sortBy} />)}</div>
          <div>{rightCol.map((item) => <RankRow key={item.researcher_id} item={item} sortBy={sortBy} />)}</div>
        </div>
      )}
    </div>
  );
}

// ──────────── 研究员详情视图子组件 ────────────

/**
 * 最新制品 —— 横向滚动文档卡片
 * 对标截图：紫色引号图标 + 日期标题 + 内容摘要 + 底部作者/浏览/评论/时间
 */
function LatestDocuments({
  documents,
  loading,
  researcherName,
}: {
  documents: HotDocument[];
  loading: boolean;
  researcherName: string;
}) {
  if (loading) {
    return (
      <div className="flex gap-4 overflow-x-auto pb-2">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="w-60 shrink-0 rounded-xl border border-slate-200 p-4">
            <Skeleton active paragraph={{ rows: 3 }} />
          </div>
        ))}
      </div>
    );
  }
  if (documents.length === 0) {
    return <div className="py-8 text-center text-sm text-slate-400">暂无最新制品</div>;
  }
  return (
    <div className="flex gap-4 overflow-x-auto pb-2 scrollbar-thin">
      {documents.map((doc) => (
        <div
          key={doc.id}
          className="w-60 shrink-0 cursor-pointer rounded-xl border border-slate-200 bg-white p-4 transition-shadow hover:shadow-md flex flex-col"
        >
          {/* 顶部：引号图标 + 日期标题 */}
          <div className="flex items-start gap-2.5 mb-3">
            <div className="w-8 h-8 rounded-full bg-brand-500 flex items-center justify-center shrink-0">
              <span className="text-white font-bold text-lg leading-none" style={{ fontFamily: 'Georgia, serif' }}>&ldquo;</span>
            </div>
            <div className="text-sm font-semibold text-slate-800 leading-snug line-clamp-2">
              {formatDate(doc.create_time)} {doc.title}
            </div>
          </div>

          {/* 内容摘要 */}
          <div className="flex-1 text-xs text-slate-500 leading-relaxed line-clamp-3 mb-3">
            {doc.summary}
          </div>

          {/* 底部信息 */}
          <div className="border-t border-slate-100 pt-2.5 space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-brand-500">
              <div className={`w-4 h-4 rounded overflow-hidden shrink-0 ${getAvatarBg(researcherName)}`}>
                <Image src={getAvatarSrc(researcherName)} alt="" width={16} height={16} />
              </div>
              <span>{researcherName}</span>
            </div>
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span className="flex items-center gap-2">
                <span className="flex items-center gap-0.5"><EyeOutlined /> {doc.view_count}</span>
                <span className="flex items-center gap-0.5"><MessageOutlined /> {doc.comment_count}</span>
              </span>
              <span>自筹 {timeAgo(doc.create_time)}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * 模拟账户区块
 * 对标截图：左侧显示总资产/今日盈亏/收益率 + 持仓资金/可用资金
 *           右侧显示持仓表格（股票/数量/成本价）
 */
function PortfolioSection({ researcher }: { researcher: HiredResearcher }) {
  const rid = researcher.researcher_id;
  const realtime = useTradingRealtimeStream(rid);
  const snapshotQuery = useTradingAll(rid);

  const loading = snapshotQuery.isLoading && !snapshotQuery.data;
  const acct = snapshotQuery.data?.account;
  const positions = snapshotQuery.data?.positions ?? [];

  /** 今日收益率 = 今日盈亏 / 总资产 */
  const totalPnlPct = acct && acct.total_asset > 0
    ? acct.daily_pnl / acct.total_asset
    : 0;

  /** 当前月份 */
  const currentMonth = `${new Date().getMonth() + 1}月`;

  return (
    <div className="rounded-xl bg-white p-4 sm:p-5">
      {/* 标题行 —— 对标截图样式 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Typography.Title level={5} className="!mb-0">模拟账户</Typography.Title>
          <span className="text-xs text-slate-400">当前持仓</span>
          <span className="text-xs text-slate-400">{currentMonth}</span>
          <span className="flex items-center gap-1.5 text-xs text-slate-400">
            <span className={`inline-block h-2 w-2 rounded-full ${
              realtime.status === 'live'
                ? 'bg-emerald-500'
                : realtime.status === 'connecting'
                  ? 'bg-amber-400'
                  : realtime.status === 'error'
                    ? 'bg-rose-500'
                    : 'bg-slate-300'
            }`} />
            {realtime.status === 'live'
              ? '实时更新中'
              : realtime.status === 'connecting'
                ? '连接中'
                : realtime.status === 'error'
                  ? '重连中'
                  : '未连接'}
          </span>
        </div>
        <Link
          href={routes.tradingDetail(rid)}
          className="text-xs text-brand-500 hover:text-brand-600 flex items-center gap-0.5 transition-colors"
        >
          查看详情 <RightOutlined style={{ fontSize: 10 }} />
        </Link>
      </div>

      {/* 加载态 */}
      {loading && <Skeleton active paragraph={{ rows: 5 }} />}

      {/* 数据态 —— 左右分栏，左侧顶部对齐避免空白 */}
      {!loading && acct && (
        <div className="flex flex-col lg:flex-row items-start gap-5">
          {/* ── 左侧：账户概览（紧凑布局） ── */}
          <div className="w-full lg:w-56 shrink-0 space-y-3">
            {/* 总资产 */}
            <div>
              <div className="text-xs text-slate-400 mb-0.5">总资产</div>
              <div className="text-2xl font-bold text-slate-800 tracking-tight">
                {formatWan(acct.total_asset)}
              </div>
            </div>

            {/* 今日盈亏 + 收益率（折行显示） */}
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="text-xs text-slate-400">今日盈亏</span>
              <span className={`text-base font-bold ${yieldColor(acct.daily_pnl)}`}>
                {acct.daily_pnl > 0 ? '+' : ''}{formatMoney(acct.daily_pnl)}
              </span>
              <span className="text-xs text-slate-400">今日收益率</span>
              <span className={`text-sm font-semibold ${yieldColor(totalPnlPct)}`}>
                {formatPct(totalPnlPct)}
              </span>
            </div>

            {/* 持仓资金 / 可用资金 */}
            <div className="flex gap-2">
              <div className="flex-1 rounded-lg bg-slate-50 px-2.5 py-2">
                <div className="text-xs text-slate-400 mb-0.5">持仓资金</div>
                <div className="text-sm font-bold text-slate-700">{formatWan(acct.holding_value)}</div>
              </div>
              <div className="flex-1 rounded-lg bg-slate-50 px-2.5 py-2">
                <div className="text-xs text-slate-400 mb-0.5">可用资金</div>
                <div className="text-sm font-bold text-brand-600">{formatWan(acct.available_cash)}</div>
              </div>
            </div>
          </div>

          {/* ── 右侧：持仓表格（最多显示5行，超出滚动） ── */}
          <div className="flex-1 min-w-0">
            <div className="overflow-x-auto overflow-y-auto max-h-[284px]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white z-10">
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-400">
                    <th className="py-2 px-2 font-medium">股票</th>
                    <th className="py-2 px-2 font-medium text-right">数量</th>
                    <th className="py-2 px-2 font-medium text-right">成本价</th>
                    <th className="py-2 px-2 font-medium text-right">现价</th>
                    <th className="py-2 px-2 font-medium text-right">盈亏</th>
                    <th className="py-2 px-2 font-medium text-right">盈亏%</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-sm text-slate-400">
                        暂无持仓 — 策略待执行或尚未开盘
                      </td>
                    </tr>
                  ) : positions.map((p) => (
                    <tr key={p.symbol} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                      <td className="py-2.5 px-2">
                        <div className="font-medium text-slate-800">{p.name}</div>
                        <div className="text-xs text-slate-400">{p.symbol}</div>
                      </td>
                      <td className="py-2.5 px-2 text-right text-slate-600">{p.quantity}</td>
                      <td className="py-2.5 px-2 text-right text-slate-600">{p.cost_price.toFixed(2)}</td>
                      <td className="py-2.5 px-2 text-right text-slate-600">{p.current_price.toFixed(2)}</td>
                      <td className="py-2.5 px-2 text-right">
                        <div className={`font-semibold ${yieldColor(p.pnl)}`}>
                          {p.pnl > 0 ? '+' : ''}{p.pnl.toFixed(2)}
                        </div>
                      </td>
                      <td className="py-2.5 px-2 text-right">
                        <div className={`text-xs font-semibold ${yieldColor(p.pnl)}`}>
                          {p.cost_price > 0
                            ? `${((p.current_price - p.cost_price) / p.cost_price * 100).toFixed(2)}%`
                            : '-'}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 工作日志时间线 */
function WorkLogSection() {
  const logs = [
    { time: '2026-04-18 09:31:25', type: '任务执行', content: '今天是新的交易日, 需要全面分析市场情况, 判断大盘走势, 挖掘投资机会并给出具体的建议. 重点关注板块轮动和市场热点' },
    { time: '2026-04-17 15:02:10', type: '定时任务', content: '收盘后复盘涨停梯队与炸板数据, 需要对今日市场情绪进行评分, 并给出明日预期和仓位建议.' },
    { time: '2026-04-17 09:30:00', type: '盘前策略', content: '盘前检查行业强弱, 结合北向资金流向与竞价强度, 确认今日操作策略方向.' },
  ];

  return (
    <div className="rounded-xl bg-white p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between">
        <Typography.Title level={5} className="!mb-0">工作日志</Typography.Title>
        <Link href="#" className="text-xs text-brand-500 hover:text-brand-600 flex items-center gap-0.5">
          查看全部 <RightOutlined style={{ fontSize: 10 }} />
        </Link>
      </div>
      <Timeline
        items={logs.map((log) => ({
          dot: <ClockCircleOutlined className="text-brand-500" />,
          children: (
            <div>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <span>{log.time}</span>
                <Tag className="!text-xs !px-1.5 !py-0">{log.type}</Tag>
              </div>
              <div className="mt-1 text-sm text-slate-600 line-clamp-2">{log.content}</div>
            </div>
          ),
        }))}
      />
    </div>
  );
}

/**
 * 研究员详情视图 —— 选中某个研究员后显示
 * 对标截图：Header（彩色头像 + 名称 + 黄色等级标签 + 状态） → 最新制品 → 模拟账户 → 工作日志
 */
function ResearcherDetailView({
  researcher,
  documents,
  docsLoading,
}: {
  researcher: HiredResearcher;
  documents: HotDocument[];
  docsLoading: boolean;
}) {
  const [tab, setTab] = useState<'overview' | 'settings'>('overview');

  return (
    <div className="space-y-4">
      {/* ── Header：研究员信息 ── */}
      <div className="rounded-xl bg-white p-4 sm:p-5">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {/* 彩色头像 */}
            <div className={`w-10 h-10 rounded-lg overflow-hidden ${getAvatarBg(researcher.name)}`}>
              <Image
                src={getAvatarSrc(researcher.name)}
                alt={researcher.name}
                width={40}
                height={40}
                className="w-full h-full object-cover"
              />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold text-slate-800">{researcher.name}</span>
                {/* 黄色等级标签 —— 对标截图 "中国研究员" */}
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-400 px-2.5 py-0.5 text-xs font-medium text-white">
                  {researcher.level || '中国研究员'}
                </span>
              </div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <Badge status={researcher.status === 'active' ? 'processing' : 'default'} />
                <span className="text-xs text-slate-400">
                  {researcher.status === 'active' ? '努力工作中' : '空闲'}
                </span>
              </div>
            </div>
          </div>

          {/* 操作按钮 */}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="small" icon={<RocketOutlined />}>执行任务</Button>
            <Button size="small" icon={<FormOutlined />}>制造文稿</Button>
            <Button size="small" icon={<SendOutlined />}>发送任务</Button>
            <Button size="small" type="primary" icon={<PlusOutlined />}>新增任务</Button>
          </div>
        </div>

        {/* Tab 切换 —— 概览 / 设置 */}
        <div className="mt-4">
          <Segmented
            value={tab}
            options={[
              { label: '概览', value: 'overview', icon: <FileTextOutlined /> },
              { label: '设置', value: 'settings', icon: <SettingOutlined /> },
            ]}
            onChange={(v) => setTab(v as typeof tab)}
          />
        </div>
      </div>

      {/* ── Tab 内容 ── */}
      {tab === 'overview' && (
        <>
          {/* 最新制品 —— 横向滚动卡片 */}
          <div className="rounded-xl bg-white p-4 sm:p-5">
            <div className="mb-3 flex items-center justify-between">
              <Typography.Title level={5} className="!mb-0">最新制品</Typography.Title>
              <Link href="#" className="text-xs text-brand-500 hover:text-brand-600 flex items-center gap-0.5">
                所有制品 <RightOutlined style={{ fontSize: 10 }} />
              </Link>
            </div>
            <LatestDocuments
              documents={documents}
              loading={docsLoading}
              researcherName={researcher.name}
            />
          </div>

          {/* 模拟账户 */}
          <PortfolioSection researcher={researcher} />

          {/* 工作日志 */}
          <WorkLogSection />
        </>
      )}

      {tab === 'settings' && (
        <div className="rounded-xl bg-white p-6 sm:p-8 text-center">
          <SettingOutlined className="text-4xl text-slate-300" />
          <div className="mt-3 text-slate-400">研究员配置面板（技能/知识库/提示词编辑）开发中...</div>
        </div>
      )}
    </div>
  );
}

// ──────────── 页面主组件 ────────────

export default function AIResearcherWorkstationPage() {
  const [activeId, setActiveId] = useState<string | null>(null); // 选中的研究员 ID
  const hydrated = useUserSessionStore((s) => s.hydrated);
  const accessToken = useUserSessionStore((s) => s.accessToken);
  const workbenchEnabled = hydrated && Boolean(accessToken);
  const hiredQuery = useHiredResearchers(workbenchEnabled);
  const docsQuery = useHotDocuments(workbenchEnabled);

  /** 首次加载时自动选中第一个研究员（对标截图默认选中） */
  useEffect(() => {
    if (!activeId && hiredQuery.data && hiredQuery.data.length > 0) {
      setActiveId(hiredQuery.data[0].researcher_id);
    }
  }, [hiredQuery.data, activeId]);

  const activeResearcher = (hiredQuery.data ?? []).find((r) => r.researcher_id === activeId) ?? null;

  return (
    <div className="flex flex-col md:flex-row gap-4" style={{ minHeight: 'calc(100vh - 56px - 40px)' }}>
      {/* ── 左侧面板 ── */}
      <div className="w-full md:w-52 shrink-0 rounded-xl bg-white border border-slate-100">
        {/* 移动端横滑列表 */}
        <div className="md:hidden p-3 space-y-2">
          <div className="text-sm font-bold text-slate-800 mb-1">AI研究员</div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {(hiredQuery.data ?? []).map((r) => {
              const active = r.researcher_id === activeId;
              return (
                <button
                  key={r.researcher_id}
                  type="button"
                  onClick={() => setActiveId(r.researcher_id)}
                  className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors ${
                    active ? 'bg-brand-50 text-brand-600 font-semibold' : 'bg-slate-50'
                  }`}
                >
                  <div className={`w-7 h-7 rounded overflow-hidden ${getAvatarBg(r.name)}`}>
                    <Image src={getAvatarSrc(r.name)} alt={r.name} width={28} height={28} />
                  </div>
                  <span className="whitespace-nowrap">{r.name}</span>
                </button>
              );
            })}
          </div>
        </div>
        {/* 桌面端竖排面板 */}
        <div className="hidden md:flex md:flex-col md:h-full">
          <SidePanel
            researchers={hiredQuery.data ?? []}
            loading={hiredQuery.isLoading}
            activeId={activeId}
            onSelect={setActiveId}
          />
        </div>
      </div>

      {/* ── 右侧主区域 ── */}
      <div className="min-w-0 flex-1 space-y-4">
        {activeResearcher ? (
          <ResearcherDetailView
            researcher={activeResearcher}
            documents={docsQuery.data ?? []}
            docsLoading={docsQuery.isLoading}
          />
        ) : (
          <>
            <div className="rounded-xl bg-white p-4 sm:p-5">
              <Typography.Title level={4} className="!mb-1">AI研究员</Typography.Title>
              <Typography.Text type="secondary" className="text-sm">
                辅助您投研决策的垂直领域专家，管理已雇佣的AI研究员
              </Typography.Text>
            </div>
            <HotDocumentsSection documents={docsQuery.data ?? []} loading={docsQuery.isLoading} />
            <RankingSection />
          </>
        )}
      </div>
    </div>
  );
}
