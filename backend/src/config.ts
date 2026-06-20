export const FMP_RATE_LIMIT = {
  plan: "starter",
  maxPerMinute: 300,
  safePerSecond: 4,
  burstPerMinute: 240,
} as const;

export const CACHE_TTL = {
  quote: 5 * 60 * 1000,
  profile: 7 * 24 * 60 * 60 * 1000,
  financials: 7 * 24 * 60 * 60 * 1000,
  ratios: 7 * 24 * 60 * 60 * 1000,
  keyMetrics: 7 * 24 * 60 * 60 * 1000,
  historicalPrice: 24 * 60 * 60 * 1000,
  news: 30 * 60 * 1000,
  analystEstimates: 24 * 60 * 60 * 1000,
  scores: 24 * 60 * 60 * 1000,
} as const;

export type CacheBucket = keyof typeof CACHE_TTL;

export const FMP_ENDPOINT_CACHE_BUCKET: Record<string, CacheBucket> = {
  quote: "quote",
  profile: "profile",
  "income-statement": "financials",
  "balance-sheet-statement": "financials",
  "cash-flow-statement": "financials",
  "income-statement-growth": "financials",
  "cash-flow-statement-growth": "financials",
  "ratios-ttm": "ratios",
  "key-metrics-ttm": "keyMetrics",
  "historical-price-eod/full": "historicalPrice",
  "analyst-estimates": "analystEstimates",
  "stock-news": "news",
};

export const REFRESH_STRATEGY = {
  coreQuotes: "every 5 minutes during market hours",
  extendedQuotes: "every 30 minutes",
  historicalPrices: "once daily after market close",
  fundamentals: "weekly and after earnings",
  ratiosAndKeyMetrics: "weekly and after earnings",
  news: "every 30 minutes for watchlist stocks",
  scores: "daily after market close",
  buyZones: "daily after market close",
} as const;

export const FMP_BASE_URL = "https://financialmodelingprep.com/stable";

export const ANTI_FOMO_WARNING =
  "这不是买区，而是叙事热区。没有明确仓位大小和下行计划，不要追高。";
