export type TradeSide = 'buy' | 'sell';

export interface TradingAccount {
  account_id: string;
  initial_capital: number;
  total_asset: number;
  available_cash: number;
  holding_value: number;
  daily_pnl: number;
}

export interface PositionItem {
  symbol: string;
  name: string;
  quantity: number;
  cost_price: number;
  current_price: number;
  pnl: number;
}

export interface TradeRecord {
  trade_id: string;
  symbol: string;
  name: string;
  side: TradeSide;
  quantity: number;
  price: number;
  amount: number;
  commission: number;
  cost_price?: number | null;
  realized_pnl?: number | null;
  realized_pnl_pct?: number | null;
  hold_days?: number | null;
  position_ratio?: number | null;
  created_at: string;
}

/** 收益曲线数据点 */
export interface EquityPoint {
  date: string;
  equity: number;
  benchmark: number;
}

/** 月度收益 */
export interface MonthlyReturn {
  month: string;
  pnl: number;
  pct: number;
}

/** 每日收益（投资日历用） */
export interface DailyReturn {
  date: string;
  pnl: number;
}

/** 风控指标 */
export interface RiskMetrics {
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  profit_loss_ratio: number;
  total_trades: number;
  win_trades: number;
  lose_trades: number;
  max_profit: number;
  max_loss: number;
  avg_hold_days: number;
}

/** 历史交易统计（聚合数据） */
export interface TradingStats {
  initial_capital: number;
  total_asset: number;
  equity_curve: EquityPoint[];
  monthly_returns: MonthlyReturn[];
  daily_returns: DailyReturn[];
  risk: RiskMetrics;
}

export interface TradingAllData {
  account: TradingAccount;
  positions: PositionItem[];
  records: TradeRecord[];
  logs: TradeLogItem[];
}

export interface TradingStreamSnapshot {
  generated_at: string;
  account: TradingAccount;
  positions: PositionItem[];
}

/** 交易日志条目（trade 表格 / analysis 富文本） */
export interface TradeLogItem {
  log_id: string;
  log_type: 'trade' | 'analysis';
  trade_records: TradeRecord[];
  title: string;
  content: string;           // Markdown
  created_at: string;
}
