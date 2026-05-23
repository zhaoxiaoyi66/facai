export type WatchlistCategory = "core" | "extended" | "opportunistic" | "avoid";

export interface Stock {
  symbol: string;
  name?: string | null;
  exchange?: string | null;
  sector?: string | null;
  industry?: string | null;
  category?: WatchlistCategory;
}

export interface DailyPrice {
  symbol: string;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

export interface QuoteLatest {
  symbol: string;
  price: number | null;
  marketCap: number | null;
  yearHigh: number | null;
  yearLow: number | null;
  pe: number | null;
  eps: number | null;
  fetchedAt: string;
}

export interface FinancialSnapshot {
  symbol: string;
  revenue: number | null;
  netIncome: number | null;
  freeCashFlow: number | null;
  operatingCashFlow: number | null;
  totalDebt: number | null;
  totalCash: number | null;
  fiscalYear?: string | null;
  period?: string | null;
}

export interface RatioSnapshot {
  symbol: string;
  pe: number | null;
  ps: number | null;
  priceToFcf: number | null;
  grossMargin: number | null;
  operatingMargin: number | null;
  fcfMargin: number | null;
  roe: number | null;
}

export interface KeyMetricSnapshot {
  symbol: string;
  enterpriseValue: number | null;
  evToSales: number | null;
  evToFcf: number | null;
  fcfYield: number | null;
  roic: number | null;
  netDebtToEbitda: number | null;
  currentRatio: number | null;
}

export interface GrowthMetrics {
  symbol: string;
  revenueGrowth: number | null;
  fcfGrowth: number | null;
  earningsGrowth: number | null;
  revenueGrowthSlowing: boolean | null;
  fcfMarginDeteriorating: boolean | null;
}

export interface AnalystEstimate {
  symbol: string;
  fiscalYear?: string | null;
  estimatedRevenueAvg: number | null;
  estimatedEpsAvg: number | null;
  analystCountRevenue: number | null;
  analystCountEps: number | null;
}

export interface NewsItem {
  id: string;
  symbol: string;
  title: string;
  url: string | null;
  publishedAt: string | null;
}

export interface TechnicalIndicators {
  symbol: string;
  rsi14: number | null;
  ema20: number | null;
  ema50: number | null;
  ema200: number | null;
  return20d: number | null;
  distanceTo52WeekHigh: number | null;
}

export interface ValuationMetrics {
  symbol: string;
  pe: number | null;
  ps: number | null;
  evToFcf: number | null;
  evToSales: number | null;
  fcfYield: number | null;
  psVsHistoricalMedian: number | null;
}

export interface ScoreBreakdown {
  score: number;
  label: string;
  reasons: string[];
}

export interface ResearchScores {
  symbol: string;
  companyQualityScore: ScoreBreakdown;
  entryScore: ScoreBreakdown;
  riskScore: ScoreBreakdown;
  rating: string;
  antiFomoWarnings: string[];
}

export type BuyZoneMethod = "eps" | "fcf" | "revenue";

export interface BuyZoneTranche {
  name: "starter" | "normal" | "heavy" | "panic";
  weight: number;
  buyPrice: number | null;
  dollarAmount: number | null;
  estimatedShares: number | null;
}

export interface BuyZone {
  symbol: string;
  method?: BuyZoneMethod;
  fairValue: number | null;
  adjustedFairValue?: number | null;
  starterPrice: number | null;
  normalBuyPrice: number | null;
  heavyBuyPrice: number | null;
  panicBuyPrice: number | null;
  tranches?: BuyZoneTranche[];
  totalShares?: number | null;
  weightedAverageCost?: number | null;
  upsideToFairValue?: number | null;
  downsideToPanicPrice?: number | null;
}

export interface StockResearchRow {
  symbol: string;
  price: number | null;
  marketCap: number | null;
  revenueGrowth: number | null;
  fcfMargin: number | null;
  pe: number | null;
  ps: number | null;
  evToFcf: number | null;
  rsi: number | null;
  distanceTo52WeekHigh: number | null;
  qualityScore: number;
  entryScore: number;
  riskScore: number;
  rating: string;
  antiFomoWarnings: string[];
  category?: WatchlistCategory;
}

export interface DashboardSections {
  bestCurrentOpportunities: StockResearchRow[];
  highQualityNearBuyZones: StockResearchRow[];
  overheatedAvoidChasing: StockResearchRow[];
  highGrowthHighRisk: StockResearchRow[];
  cheapDeteriorating: StockResearchRow[];
  watchlistByCategory: Record<string, StockResearchRow[]>;
  recentEarningsMovers: StockResearchRow[];
  apiRefreshStatus: RefreshJob[];
}

export type RefreshJobType =
  | "quote"
  | "historicalPrice"
  | "fundamentals"
  | "ratios"
  | "keyMetrics"
  | "analystEstimates"
  | "news"
  | "scores"
  | "buyZones";

export type RefreshJobStatus = "queued" | "running" | "success" | "failed";

export interface RefreshJob {
  id: string;
  type: RefreshJobType;
  symbol?: string;
  status: RefreshJobStatus;
  priority: number;
  runAfter: Date;
  attempts: number;
  lastError?: string;
  createdAt: Date;
  updatedAt: Date;
}
