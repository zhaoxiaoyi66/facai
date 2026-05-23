CREATE TABLE IF NOT EXISTS stocks (
  symbol TEXT PRIMARY KEY,
  name TEXT,
  exchange TEXT,
  sector TEXT,
  industry TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  symbol TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(name, symbol)
);

CREATE TABLE IF NOT EXISTS prices_daily (
  symbol TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(symbol, date)
);

CREATE TABLE IF NOT EXISTS quotes_latest (
  symbol TEXT PRIMARY KEY,
  price REAL,
  market_cap REAL,
  year_high REAL,
  year_low REAL,
  pe REAL,
  eps REAL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fundamentals_annual (
  symbol TEXT NOT NULL,
  fiscal_year TEXT NOT NULL,
  revenue REAL,
  net_income REAL,
  free_cash_flow REAL,
  operating_cash_flow REAL,
  total_debt REAL,
  total_cash REAL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(symbol, fiscal_year)
);

CREATE TABLE IF NOT EXISTS fundamentals_ttm (
  symbol TEXT PRIMARY KEY,
  revenue_ttm REAL,
  net_income_ttm REAL,
  free_cash_flow_ttm REAL,
  operating_cash_flow_ttm REAL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ratios (
  symbol TEXT PRIMARY KEY,
  pe REAL,
  ps REAL,
  price_to_fcf REAL,
  gross_margin REAL,
  operating_margin REAL,
  fcf_margin REAL,
  roe REAL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS key_metrics (
  symbol TEXT PRIMARY KEY,
  enterprise_value REAL,
  ev_to_sales REAL,
  ev_to_fcf REAL,
  fcf_yield REAL,
  roic REAL,
  net_debt_to_ebitda REAL,
  current_ratio REAL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS growth_metrics (
  symbol TEXT PRIMARY KEY,
  revenue_growth REAL,
  fcf_growth REAL,
  earnings_growth REAL,
  revenue_growth_slowing INTEGER,
  fcf_margin_deteriorating INTEGER,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_manual_overrides (
  symbol TEXT PRIMARY KEY,
  model_type TEXT,
  manual_arr_growth REAL,
  manual_rpo_growth REAL,
  manual_net_retention REAL,
  manual_adjusted_ebitda REAL,
  manual_adjusted_ebitda_growth REAL,
  manual_adjusted_fcf_before_growth REAL,
  manual_net_debt_to_adjusted_ebitda REAL,
  manual_hedge_coverage_current_year REAL,
  manual_hedge_coverage_next_year REAL,
  manual_buyback_amount REAL,
  manual_share_count_reduction REAL,
  manual_affo REAL,
  manual_affo_growth REAL,
  manual_occupancy REAL,
  manual_cet1_ratio REAL,
  manual_nim REAL,
  manual_credit_loss_ratio REAL,
  manual_cash_runway_months REAL,
  manual_patent_cliff_risk REAL,
  manual_pipeline_risk REAL,
  manual_backlog_growth REAL,
  manual_book_to_bill REAL,
  manual_narrative_notes TEXT,
  updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO stock_manual_overrides (
  symbol,
  model_type,
  updated_at
)
VALUES ('VST', 'POWER_GENERATION', datetime('now'));

CREATE TABLE IF NOT EXISTS disclosure_metric_values (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  metricKey TEXT NOT NULL,
  displayName TEXT,
  value REAL NOT NULL,
  unit TEXT,
  period TEXT,
  fiscalYear INTEGER,
  fiscalQuarter TEXT,
  sourceType TEXT NOT NULL,
  sourceUrl TEXT,
  sourceDocumentTitle TEXT,
  accessionNumber TEXT,
  extractedText TEXT,
  confidence TEXT NOT NULL,
  reviewStatus TEXT NOT NULL DEFAULT 'pending_review'
    CHECK (reviewStatus IN ('pending_review', 'approved', 'rejected', 'manually_corrected', 'stale')),
  reviewedAt TEXT,
  reviewedBy TEXT,
  correctionNotes TEXT,
  updatedAt TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_disclosure_metric_values_symbol_metric
ON disclosure_metric_values(symbol, metricKey);

CREATE TABLE IF NOT EXISTS disclosure_fetch_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  sourceType TEXT NOT NULL,
  url TEXT,
  status TEXT NOT NULL,
  errorMessage TEXT,
  fetchedAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missing_metric_resolution (
  symbol TEXT NOT NULL,
  metricKey TEXT NOT NULL,
  status TEXT NOT NULL,
  sourceTried TEXT,
  reason TEXT,
  recommendedAction TEXT,
  updatedAt TEXT NOT NULL,
  PRIMARY KEY (symbol, metricKey)
);

CREATE TABLE IF NOT EXISTS analyst_estimates (
  symbol TEXT NOT NULL,
  fiscal_year TEXT NOT NULL,
  estimated_revenue_avg REAL,
  estimated_eps_avg REAL,
  analyst_count_revenue INTEGER,
  analyst_count_eps INTEGER,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(symbol, fiscal_year)
);

CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  published_at TEXT,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS technical_indicators (
  symbol TEXT PRIMARY KEY,
  rsi14 REAL,
  ema20 REAL,
  ema50 REAL,
  ema200 REAL,
  return_20d REAL,
  distance_to_52w_high REAL,
  calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_metrics (
  symbol TEXT PRIMARY KEY,
  pe REAL,
  ps REAL,
  ev_to_fcf REAL,
  ev_to_sales REAL,
  fcf_yield REAL,
  ps_vs_historical_median REAL,
  calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_scores (
  symbol TEXT PRIMARY KEY,
  score REAL NOT NULL,
  label TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_scores (
  symbol TEXT PRIMARY KEY,
  score REAL NOT NULL,
  label TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_scores (
  symbol TEXT PRIMARY KEY,
  score REAL NOT NULL,
  label TEXT NOT NULL,
  flags_json TEXT NOT NULL,
  anti_fomo_warnings_json TEXT NOT NULL,
  calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buy_zones (
  symbol TEXT PRIMARY KEY,
  method TEXT,
  fair_value REAL,
  adjusted_fair_value REAL,
  starter_price REAL,
  normal_buy_price REAL,
  heavy_buy_price REAL,
  panic_buy_price REAL,
  total_shares REAL,
  weighted_average_cost REAL,
  upside_to_fair_value REAL,
  downside_to_panic_price REAL,
  tranches_json TEXT,
  calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_call_logs (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  symbol TEXT,
  status TEXT NOT NULL,
  http_status INTEGER,
  duration_ms INTEGER NOT NULL,
  attempt INTEGER NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  symbol TEXT,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL,
  run_after TEXT NOT NULL,
  attempts INTEGER NOT NULL,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
