import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import {
  AnalystEstimate,
  BuyZone,
  DailyPrice,
  FinancialSnapshot,
  GrowthMetrics,
  KeyMetricSnapshot,
  NewsItem,
  QuoteLatest,
  RatioSnapshot,
  RefreshJob,
  ResearchScores,
  Stock,
  TechnicalIndicators,
  ValuationMetrics,
} from "./types";

type SqlValue = string | number | bigint | null;
type SqlRow = Record<string, unknown>;

export interface StockRepository {
  upsertStock(stock: Stock): Promise<void>;
  listWatchlistSymbols(category?: string): Promise<string[]>;
  saveDailyPrices(symbol: string, prices: DailyPrice[]): Promise<void>;
  saveQuote(quote: QuoteLatest): Promise<void>;
  saveFundamentals(snapshot: FinancialSnapshot): Promise<void>;
  saveRatios(snapshot: RatioSnapshot): Promise<void>;
  saveKeyMetrics(snapshot: KeyMetricSnapshot): Promise<void>;
  saveGrowthMetrics(snapshot: GrowthMetrics): Promise<void>;
  saveAnalystEstimates(estimates: AnalystEstimate[]): Promise<void>;
  saveNews(items: NewsItem[]): Promise<void>;
  saveTechnicals(snapshot: TechnicalIndicators): Promise<void>;
  saveValuation(snapshot: ValuationMetrics): Promise<void>;
  saveScores(scores: ResearchScores): Promise<void>;
  saveBuyZone(zone: BuyZone): Promise<void>;
  saveRefreshJob(job: RefreshJob): Promise<void>;
  listRefreshJobs(): Promise<RefreshJob[]>;
}

export class InMemoryStockRepository implements StockRepository {
  private readonly stocks = new Map<string, Stock>();
  private readonly watchlists = new Map<string, Set<string>>();
  private readonly refreshJobs = new Map<string, RefreshJob>();

  async upsertStock(stock: Stock): Promise<void> {
    this.stocks.set(stock.symbol, stock);
    const category = stock.category ?? "extended";
    this.watchlists.set(category, this.watchlists.get(category) ?? new Set());
    this.watchlists.get(category)?.add(stock.symbol);
  }

  async listWatchlistSymbols(category?: string): Promise<string[]> {
    if (category) return [...(this.watchlists.get(category) ?? [])];
    return [...this.stocks.keys()];
  }

  async saveDailyPrices(): Promise<void> {}
  async saveQuote(): Promise<void> {}
  async saveFundamentals(): Promise<void> {}
  async saveRatios(): Promise<void> {}
  async saveKeyMetrics(): Promise<void> {}
  async saveGrowthMetrics(): Promise<void> {}
  async saveAnalystEstimates(): Promise<void> {}
  async saveNews(): Promise<void> {}
  async saveTechnicals(): Promise<void> {}
  async saveValuation(): Promise<void> {}
  async saveScores(): Promise<void> {}
  async saveBuyZone(): Promise<void> {}

  async saveRefreshJob(job: RefreshJob): Promise<void> {
    this.refreshJobs.set(job.id, job);
  }

  async listRefreshJobs(): Promise<RefreshJob[]> {
    return [...this.refreshJobs.values()];
  }
}

export class SqliteStockRepository implements StockRepository {
  private readonly db: DatabaseSync;

  constructor(
    dbPath = join(process.cwd(), "data", "research.sqlite"),
    schemaPath = defaultSchemaPath(),
  ) {
    mkdirSync(dirname(dbPath), { recursive: true });
    this.db = new DatabaseSync(dbPath);
    this.db.exec("PRAGMA journal_mode = WAL");
    this.db.exec("PRAGMA foreign_keys = ON");
    this.db.exec(readFileSync(schemaPath, "utf8"));
  }

  async upsertStock(stock: Stock): Promise<void> {
    const now = isoNow();
    this.db
      .prepare(
        `INSERT INTO stocks(symbol, name, exchange, sector, industry, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           name = excluded.name,
           exchange = excluded.exchange,
           sector = excluded.sector,
           industry = excluded.industry,
           updated_at = excluded.updated_at`,
      )
      .run(stock.symbol, nullable(stock.name), nullable(stock.exchange), nullable(stock.sector), nullable(stock.industry), now, now);

    const category = stock.category ?? "extended";
    this.db
      .prepare(
        `INSERT INTO watchlists(id, name, category, symbol, created_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(name, symbol) DO UPDATE SET category = excluded.category`,
      )
      .run(`${category}:${stock.symbol}`, "default", category, stock.symbol, now);
  }

  async listWatchlistSymbols(category?: string): Promise<string[]> {
    const rows = category
      ? this.db.prepare("SELECT symbol FROM watchlists WHERE category = ? ORDER BY symbol").all(category)
      : this.db.prepare("SELECT DISTINCT symbol FROM watchlists ORDER BY symbol").all();
    const symbols = rows.map((row) => String(row.symbol));
    if (symbols.length > 0) return symbols;
    return this.db.prepare("SELECT symbol FROM stocks ORDER BY symbol").all().map((row) => String(row.symbol));
  }

  async saveDailyPrices(symbol: string, prices: DailyPrice[]): Promise<void> {
    const statement = this.db.prepare(
      `INSERT INTO prices_daily(symbol, date, open, high, low, close, volume, fetched_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(symbol, date) DO UPDATE SET
         open = excluded.open,
         high = excluded.high,
         low = excluded.low,
         close = excluded.close,
         volume = excluded.volume,
         fetched_at = excluded.fetched_at`,
    );
    const now = isoNow();
    this.db.exec("BEGIN");
    try {
      for (const price of prices) {
        statement.run(
          symbol,
          price.date,
          nullable(price.open),
          nullable(price.high),
          nullable(price.low),
          nullable(price.close),
          nullable(price.volume),
          now,
        );
      }
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  async saveQuote(quote: QuoteLatest): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO quotes_latest(symbol, price, market_cap, year_high, year_low, pe, eps, fetched_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           price = excluded.price,
           market_cap = excluded.market_cap,
           year_high = excluded.year_high,
           year_low = excluded.year_low,
           pe = excluded.pe,
           eps = excluded.eps,
           fetched_at = excluded.fetched_at`,
      )
      .run(
        quote.symbol,
        nullable(quote.price),
        nullable(quote.marketCap),
        nullable(quote.yearHigh),
        nullable(quote.yearLow),
        nullable(quote.pe),
        nullable(quote.eps),
        quote.fetchedAt,
      );
  }

  async saveFundamentals(snapshot: FinancialSnapshot): Promise<void> {
    const fiscalYear = snapshot.fiscalYear ?? snapshot.period ?? "unknown";
    this.db
      .prepare(
        `INSERT INTO fundamentals_annual(
           symbol, fiscal_year, revenue, net_income, free_cash_flow,
           operating_cash_flow, total_debt, total_cash, fetched_at
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol, fiscal_year) DO UPDATE SET
           revenue = excluded.revenue,
           net_income = excluded.net_income,
           free_cash_flow = excluded.free_cash_flow,
           operating_cash_flow = excluded.operating_cash_flow,
           total_debt = excluded.total_debt,
           total_cash = excluded.total_cash,
           fetched_at = excluded.fetched_at`,
      )
      .run(
        snapshot.symbol,
        fiscalYear,
        nullable(snapshot.revenue),
        nullable(snapshot.netIncome),
        nullable(snapshot.freeCashFlow),
        nullable(snapshot.operatingCashFlow),
        nullable(snapshot.totalDebt),
        nullable(snapshot.totalCash),
        isoNow(),
      );
  }

  async saveRatios(snapshot: RatioSnapshot): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO ratios(symbol, pe, ps, price_to_fcf, gross_margin, operating_margin, fcf_margin, roe, fetched_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           pe = excluded.pe,
           ps = excluded.ps,
           price_to_fcf = excluded.price_to_fcf,
           gross_margin = excluded.gross_margin,
           operating_margin = excluded.operating_margin,
           fcf_margin = excluded.fcf_margin,
           roe = excluded.roe,
           fetched_at = excluded.fetched_at`,
      )
      .run(
        snapshot.symbol,
        nullable(snapshot.pe),
        nullable(snapshot.ps),
        nullable(snapshot.priceToFcf),
        nullable(snapshot.grossMargin),
        nullable(snapshot.operatingMargin),
        nullable(snapshot.fcfMargin),
        nullable(snapshot.roe),
        isoNow(),
      );
  }

  async saveKeyMetrics(snapshot: KeyMetricSnapshot): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO key_metrics(
           symbol, enterprise_value, ev_to_sales, ev_to_fcf, fcf_yield,
           roic, net_debt_to_ebitda, current_ratio, fetched_at
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           enterprise_value = excluded.enterprise_value,
           ev_to_sales = excluded.ev_to_sales,
           ev_to_fcf = excluded.ev_to_fcf,
           fcf_yield = excluded.fcf_yield,
           roic = excluded.roic,
           net_debt_to_ebitda = excluded.net_debt_to_ebitda,
           current_ratio = excluded.current_ratio,
           fetched_at = excluded.fetched_at`,
      )
      .run(
        snapshot.symbol,
        nullable(snapshot.enterpriseValue),
        nullable(snapshot.evToSales),
        nullable(snapshot.evToFcf),
        nullable(snapshot.fcfYield),
        nullable(snapshot.roic),
        nullable(snapshot.netDebtToEbitda),
        nullable(snapshot.currentRatio),
        isoNow(),
      );
  }

  async saveGrowthMetrics(snapshot: GrowthMetrics): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO growth_metrics(
           symbol, revenue_growth, fcf_growth, earnings_growth,
           revenue_growth_slowing, fcf_margin_deteriorating, fetched_at
         )
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           revenue_growth = excluded.revenue_growth,
           fcf_growth = excluded.fcf_growth,
           earnings_growth = excluded.earnings_growth,
           revenue_growth_slowing = excluded.revenue_growth_slowing,
           fcf_margin_deteriorating = excluded.fcf_margin_deteriorating,
           fetched_at = excluded.fetched_at`,
      )
      .run(
        snapshot.symbol,
        nullable(snapshot.revenueGrowth),
        nullable(snapshot.fcfGrowth),
        nullable(snapshot.earningsGrowth),
        booleanToSql(snapshot.revenueGrowthSlowing),
        booleanToSql(snapshot.fcfMarginDeteriorating),
        isoNow(),
      );
  }

  async saveAnalystEstimates(estimates: AnalystEstimate[]): Promise<void> {
    const statement = this.db.prepare(
      `INSERT INTO analyst_estimates(
         symbol, fiscal_year, estimated_revenue_avg, estimated_eps_avg,
         analyst_count_revenue, analyst_count_eps, fetched_at
       )
       VALUES (?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(symbol, fiscal_year) DO UPDATE SET
         estimated_revenue_avg = excluded.estimated_revenue_avg,
         estimated_eps_avg = excluded.estimated_eps_avg,
         analyst_count_revenue = excluded.analyst_count_revenue,
         analyst_count_eps = excluded.analyst_count_eps,
         fetched_at = excluded.fetched_at`,
    );
    const now = isoNow();
    this.db.exec("BEGIN");
    try {
      for (const estimate of estimates) {
        statement.run(
          estimate.symbol,
          estimate.fiscalYear ?? "unknown",
          nullable(estimate.estimatedRevenueAvg),
          nullable(estimate.estimatedEpsAvg),
          nullable(estimate.analystCountRevenue),
          nullable(estimate.analystCountEps),
          now,
        );
      }
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  async saveNews(items: NewsItem[]): Promise<void> {
    const statement = this.db.prepare(
      `INSERT INTO news(id, symbol, title, url, published_at, fetched_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         title = excluded.title,
         url = excluded.url,
         published_at = excluded.published_at,
         fetched_at = excluded.fetched_at`,
    );
    const now = isoNow();
    this.db.exec("BEGIN");
    try {
      for (const item of items) {
        statement.run(item.id, item.symbol, item.title, nullable(item.url), nullable(item.publishedAt), now);
      }
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  async saveTechnicals(snapshot: TechnicalIndicators): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO technical_indicators(symbol, rsi14, ema20, ema50, ema200, return_20d, distance_to_52w_high, calculated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           rsi14 = excluded.rsi14,
           ema20 = excluded.ema20,
           ema50 = excluded.ema50,
           ema200 = excluded.ema200,
           return_20d = excluded.return_20d,
           distance_to_52w_high = excluded.distance_to_52w_high,
           calculated_at = excluded.calculated_at`,
      )
      .run(
        snapshot.symbol,
        nullable(snapshot.rsi14),
        nullable(snapshot.ema20),
        nullable(snapshot.ema50),
        nullable(snapshot.ema200),
        nullable(snapshot.return20d),
        nullable(snapshot.distanceTo52WeekHigh),
        isoNow(),
      );
  }

  async saveValuation(snapshot: ValuationMetrics): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO valuation_metrics(symbol, pe, ps, ev_to_fcf, ev_to_sales, fcf_yield, ps_vs_historical_median, calculated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           pe = excluded.pe,
           ps = excluded.ps,
           ev_to_fcf = excluded.ev_to_fcf,
           ev_to_sales = excluded.ev_to_sales,
           fcf_yield = excluded.fcf_yield,
           ps_vs_historical_median = excluded.ps_vs_historical_median,
           calculated_at = excluded.calculated_at`,
      )
      .run(
        snapshot.symbol,
        nullable(snapshot.pe),
        nullable(snapshot.ps),
        nullable(snapshot.evToFcf),
        nullable(snapshot.evToSales),
        nullable(snapshot.fcfYield),
        nullable(snapshot.psVsHistoricalMedian),
        isoNow(),
      );
  }

  async saveScores(scores: ResearchScores): Promise<void> {
    const now = isoNow();
    this.db
      .prepare(
        `INSERT INTO quality_scores(symbol, score, label, reasons_json, calculated_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           score = excluded.score,
           label = excluded.label,
           reasons_json = excluded.reasons_json,
           calculated_at = excluded.calculated_at`,
      )
      .run(
        scores.symbol,
        scores.companyQualityScore.score,
        scores.companyQualityScore.label,
        JSON.stringify(scores.companyQualityScore.reasons),
        now,
      );
    this.db
      .prepare(
        `INSERT INTO entry_scores(symbol, score, label, reasons_json, calculated_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           score = excluded.score,
           label = excluded.label,
           reasons_json = excluded.reasons_json,
           calculated_at = excluded.calculated_at`,
      )
      .run(scores.symbol, scores.entryScore.score, scores.entryScore.label, JSON.stringify(scores.entryScore.reasons), now);
    this.db
      .prepare(
        `INSERT INTO risk_scores(symbol, score, label, flags_json, anti_fomo_warnings_json, calculated_at)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           score = excluded.score,
           label = excluded.label,
           flags_json = excluded.flags_json,
           anti_fomo_warnings_json = excluded.anti_fomo_warnings_json,
           calculated_at = excluded.calculated_at`,
      )
      .run(
        scores.symbol,
        scores.riskScore.score,
        scores.riskScore.label,
        JSON.stringify(scores.riskScore.reasons),
        JSON.stringify(scores.antiFomoWarnings),
        now,
      );
  }

  async saveBuyZone(zone: BuyZone): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO buy_zones(
           symbol, method, fair_value, adjusted_fair_value, starter_price,
           normal_buy_price, heavy_buy_price, panic_buy_price, total_shares,
           weighted_average_cost, upside_to_fair_value, downside_to_panic_price,
           tranches_json, calculated_at
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(symbol) DO UPDATE SET
           method = excluded.method,
           fair_value = excluded.fair_value,
           adjusted_fair_value = excluded.adjusted_fair_value,
           starter_price = excluded.starter_price,
           normal_buy_price = excluded.normal_buy_price,
           heavy_buy_price = excluded.heavy_buy_price,
           panic_buy_price = excluded.panic_buy_price,
           total_shares = excluded.total_shares,
           weighted_average_cost = excluded.weighted_average_cost,
           upside_to_fair_value = excluded.upside_to_fair_value,
           downside_to_panic_price = excluded.downside_to_panic_price,
           tranches_json = excluded.tranches_json,
           calculated_at = excluded.calculated_at`,
      )
      .run(
        zone.symbol,
        nullable(zone.method),
        nullable(zone.fairValue),
        nullable(zone.adjustedFairValue ?? null),
        nullable(zone.starterPrice),
        nullable(zone.normalBuyPrice),
        nullable(zone.heavyBuyPrice),
        nullable(zone.panicBuyPrice),
        nullable(zone.totalShares ?? null),
        nullable(zone.weightedAverageCost ?? null),
        nullable(zone.upsideToFairValue ?? null),
        nullable(zone.downsideToPanicPrice ?? null),
        JSON.stringify(zone.tranches ?? []),
        isoNow(),
      );
  }

  async saveRefreshJob(job: RefreshJob): Promise<void> {
    this.db
      .prepare(
        `INSERT INTO refresh_jobs(id, type, symbol, status, priority, run_after, attempts, last_error, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET
           type = excluded.type,
           symbol = excluded.symbol,
           status = excluded.status,
           priority = excluded.priority,
           run_after = excluded.run_after,
           attempts = excluded.attempts,
           last_error = excluded.last_error,
           updated_at = excluded.updated_at`,
      )
      .run(
        job.id,
        job.type,
        nullable(job.symbol),
        job.status,
        job.priority,
        job.runAfter.toISOString(),
        job.attempts,
        nullable(job.lastError),
        job.createdAt.toISOString(),
        job.updatedAt.toISOString(),
      );
  }

  async listRefreshJobs(): Promise<RefreshJob[]> {
    return this.db
      .prepare("SELECT * FROM refresh_jobs ORDER BY updated_at DESC")
      .all()
      .map((row) => ({
        id: String(row.id),
        type: String(row.type) as RefreshJob["type"],
        symbol: row.symbol === null || row.symbol === undefined ? undefined : String(row.symbol),
        status: String(row.status) as RefreshJob["status"],
        priority: Number(row.priority),
        runAfter: new Date(String(row.run_after)),
        attempts: Number(row.attempts),
        lastError: row.last_error === null || row.last_error === undefined ? undefined : String(row.last_error),
        createdAt: new Date(String(row.created_at)),
        updatedAt: new Date(String(row.updated_at)),
      }));
  }

  saveApiCallLog(entry: {
    provider: string;
    endpoint: string;
    symbol?: string;
    status: string;
    httpStatus?: number;
    durationMs: number;
    attempt: number;
    error?: string;
    createdAt: string;
  }): void {
    this.db
      .prepare(
        `INSERT INTO api_call_logs(
           id, provider, endpoint, symbol, status, http_status,
           duration_ms, attempt, error, created_at
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        randomUUID(),
        entry.provider,
        entry.endpoint,
        nullable(entry.symbol),
        entry.status,
        nullable(entry.httpStatus ?? null),
        entry.durationMs,
        entry.attempt,
        nullable(entry.error),
        entry.createdAt,
      );
  }
}

function defaultSchemaPath(): string {
  const rootSchemaPath = join(process.cwd(), "backend", "schema.sql");
  if (existsSync(rootSchemaPath)) return rootSchemaPath;
  return join(process.cwd(), "schema.sql");
}

function nullable(value: string | number | bigint | undefined | null): SqlValue {
  return value === undefined ? null : value;
}

function booleanToSql(value: boolean | null): number | null {
  if (value === null) return null;
  return value ? 1 : 0;
}

function isoNow(): string {
  return new Date().toISOString();
}
