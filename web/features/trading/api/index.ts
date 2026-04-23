/**
 * 模拟交易 API 层
 *
 * 对接后端 /trading 接口：
 *  - getTradingAccount()   获取模拟账户概况（按研究员）
 *  - getTradingPositions() 获取持仓列表（按研究员）
 *  - getTradingRecords()   获取成交记录（按研究员）
 */
import { http } from '@/lib/request/http-client';
import { ApiResponse, ListResponse } from '@/types/api';
import { PositionItem, TradeLogItem, TradeRecord, TradingAccount, TradingAllData, TradingStats } from '@/types/trading';

const API_BASE = '/trading';

/** 构建 researcher_id 查询参数 */
function withRid(rid?: string): string {
  return rid ? `?researcher_id=${rid}` : '';
}

/** 获取模拟账户概况 */
export const getTradingAccount = async (researcherId?: string): Promise<TradingAccount> => {
  const response = await http<ApiResponse<TradingAccount>>(`${API_BASE}/account${withRid(researcherId)}`);
  return response.data;
};

/** 获取持仓列表 */
export const getTradingPositions = async (researcherId?: string): Promise<PositionItem[]> => {
  const response = await http<ApiResponse<ListResponse<PositionItem>>>(`${API_BASE}/positions${withRid(researcherId)}`);
  return response.data.items;
};

/** 获取成交记录 */
export const getTradingRecords = async (researcherId?: string): Promise<TradeRecord[]> => {
  const response = await http<ApiResponse<ListResponse<TradeRecord>>>(`${API_BASE}/records${withRid(researcherId)}`);
  return response.data.items;
};

/** 获取交易日志（trade 表格 + analysis 富文本） */
export const getTradingLogs = async (researcherId?: string): Promise<TradeLogItem[]> => {
  const response = await http<ApiResponse<ListResponse<TradeLogItem>>>(`${API_BASE}/logs${withRid(researcherId)}`);
  return response.data.items;
};

/** 获取历史交易统计（收益曲线、月度收益、风控指标、日收益序列） */
export const getTradingStats = async (researcherId?: string): Promise<TradingStats> => {
  const response = await http<ApiResponse<TradingStats>>(`${API_BASE}/stats${withRid(researcherId)}`);
  return response.data;
};

/** 聚合接口 —— 一次获取 account + positions + records + logs */
export const getTradingAll = async (researcherId?: string): Promise<TradingAllData> => {
  const response = await http<ApiResponse<TradingAllData>>(`${API_BASE}/all${withRid(researcherId)}`);
  return response.data;
};

