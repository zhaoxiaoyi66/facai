import { FmpClient } from "./fmpClient";
import { StockRepository } from "./stockRepository";
import {
  AnalystEstimate,
  DailyPrice,
  FinancialSnapshot,
  KeyMetricSnapshot,
  NewsItem,
  QuoteLatest,
  RatioSnapshot,
  RefreshJob,
  RefreshJobType,
} from "./types";
import { createHash, randomUUID } from "node:crypto";

type FmpRecord = Record<string, unknown>;

export class RefreshQueue {
  private readonly jobs: RefreshJob[] = [];
  private running = false;

  constructor(
    private readonly repository: StockRepository,
    private readonly fmpClient: FmpClient,
  ) {}

  enqueue(type: RefreshJobType, symbol?: string, priority = 50, runAfter = new Date()): RefreshJob {
    const job: RefreshJob = {
      id: randomUUID(),
      type,
      symbol,
      status: "queued",
      priority,
      runAfter,
      attempts: 0,
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    this.jobs.push(job);
    void this.repository.saveRefreshJob(job);
    void this.run();
    return job;
  }

  enqueueCoreQuoteRefresh(symbols: string[]): void {
    for (const symbol of symbols) this.enqueue("quote", symbol, 10);
  }

  enqueueExtendedQuoteRefresh(symbols: string[]): void {
    for (const symbol of symbols) this.enqueue("quote", symbol, 40);
  }

  enqueueDailyAfterClose(symbols: string[]): void {
    for (const symbol of symbols) {
      this.enqueue("historicalPrice", symbol, 30);
      this.enqueue("scores", symbol, 35);
      this.enqueue("buyZones", symbol, 35);
    }
  }

  enqueueWeeklyFundamentals(symbols: string[]): void {
    for (const symbol of symbols) {
      this.enqueue("fundamentals", symbol, 20);
      this.enqueue("ratios", symbol, 20);
      this.enqueue("keyMetrics", symbol, 20);
      this.enqueue("analystEstimates", symbol, 25);
    }
  }

  enqueueNewsRefresh(symbols: string[]): void {
    for (const symbol of symbols) this.enqueue("news", symbol, 45);
  }

  private async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      while (true) {
        const job = this.nextJob();
        if (!job) return;
        await this.runJob(job);
      }
    } finally {
      this.running = false;
    }
  }

  private nextJob(): RefreshJob | undefined {
    const now = Date.now();
    const candidates = this.jobs
      .filter((job) => job.status === "queued" && job.runAfter.getTime() <= now)
      .sort((a, b) => a.priority - b.priority);
    return candidates[0];
  }

  private async runJob(job: RefreshJob): Promise<void> {
    job.status = "running";
    job.updatedAt = new Date();
    job.attempts += 1;
    await this.repository.saveRefreshJob(job);

    try {
      if (!job.symbol) throw new Error("Refresh job requires symbol.");
      await this.dispatch(job.type, job.symbol);
      job.status = "success";
    } catch (error) {
      job.status = "failed";
      job.lastError = error instanceof Error ? error.message : String(error);
    } finally {
      job.updatedAt = new Date();
      await this.repository.saveRefreshJob(job);
    }
  }

  private async dispatch(type: RefreshJobType, symbol: string): Promise<void> {
    switch (type) {
      case "quote":
        await this.refreshQuote(symbol);
        return;
      case "historicalPrice":
        await this.refreshHistoricalPrices(symbol);
        return;
      case "fundamentals":
        await this.refreshFundamentals(symbol);
        return;
      case "ratios":
        await this.refreshRatios(symbol);
        return;
      case "keyMetrics":
        await this.refreshKeyMetrics(symbol);
        return;
      case "analystEstimates":
        await this.refreshAnalystEstimates(symbol);
        return;
      case "news":
        await this.refreshNews(symbol);
        return;
      case "scores":
      case "buyZones":
        return;
    }
  }

  private async refreshQuote(symbol: string): Promise<void> {
    const payload = await this.fmpClient.getQuote(symbol, true);
    const record = firstRecord(payload);
    const quote: QuoteLatest = {
      symbol,
      price: num(record, "price"),
      marketCap: num(record, "marketCap", "market_cap"),
      yearHigh: num(record, "yearHigh", "yearHighPrice", "52WeekHigh"),
      yearLow: num(record, "yearLow", "yearLowPrice", "52WeekLow"),
      pe: num(record, "pe", "peRatio", "priceEarningsRatio"),
      eps: num(record, "eps"),
      fetchedAt: new Date().toISOString(),
    };
    await this.repository.saveQuote(quote);
  }

  private async refreshHistoricalPrices(symbol: string): Promise<void> {
    const payload = await this.fmpClient.getHistoricalDaily(symbol, true);
    const prices = historicalRecords(payload)
      .map<DailyPrice>((record) => ({
        symbol,
        date: str(record, "date") ?? "",
        open: num(record, "open"),
        high: num(record, "high"),
        low: num(record, "low"),
        close: num(record, "close", "adjClose"),
        volume: num(record, "volume"),
      }))
      .filter((price) => price.date.length > 0);
    await this.repository.saveDailyPrices(symbol, prices);
  }

  private async refreshFundamentals(symbol: string): Promise<void> {
    const [incomePayload, balancePayload, cashFlowPayload] = await Promise.all([
      this.fmpClient.getAnnualIncomeStatement(symbol, true),
      this.fmpClient.getAnnualBalanceSheet(symbol, true),
      this.fmpClient.getAnnualCashFlow(symbol, true),
    ]);
    const snapshots = new Map<string, FinancialSnapshot>();

    for (const record of asRecords(incomePayload)) {
      const fiscalYear = fiscalYearFrom(record);
      const snapshot = getOrCreateFundamentalSnapshot(snapshots, symbol, fiscalYear);
      snapshot.revenue = num(record, "revenue");
      snapshot.netIncome = num(record, "netIncome");
    }
    for (const record of asRecords(cashFlowPayload)) {
      const fiscalYear = fiscalYearFrom(record);
      const snapshot = getOrCreateFundamentalSnapshot(snapshots, symbol, fiscalYear);
      snapshot.freeCashFlow = num(record, "freeCashFlow");
      snapshot.operatingCashFlow = num(record, "operatingCashFlow", "netCashProvidedByOperatingActivities");
    }
    for (const record of asRecords(balancePayload)) {
      const fiscalYear = fiscalYearFrom(record);
      const snapshot = getOrCreateFundamentalSnapshot(snapshots, symbol, fiscalYear);
      snapshot.totalDebt = num(record, "totalDebt", "shortTermDebtAndCapitalLeaseObligation");
      snapshot.totalCash = num(record, "cashAndCashEquivalents", "cashAndShortTermInvestments");
    }

    for (const snapshot of snapshots.values()) {
      await this.repository.saveFundamentals(snapshot);
    }
  }

  private async refreshRatios(symbol: string): Promise<void> {
    const record = firstRecord(await this.fmpClient.getRatiosTtm(symbol, true));
    const snapshot: RatioSnapshot = {
      symbol,
      pe: num(record, "peRatioTTM", "peRatio", "priceEarningsRatioTTM"),
      ps: num(record, "priceToSalesRatioTTM", "priceToSalesRatio"),
      priceToFcf: num(record, "priceToFreeCashFlowsRatioTTM", "priceToFreeCashFlowsRatio"),
      grossMargin: num(record, "grossProfitMarginTTM", "grossProfitMargin"),
      operatingMargin: num(record, "operatingProfitMarginTTM", "operatingProfitMargin"),
      fcfMargin: num(record, "freeCashFlowMarginTTM", "freeCashFlowMargin"),
      roe: num(record, "returnOnEquityTTM", "returnOnEquity"),
    };
    await this.repository.saveRatios(snapshot);
  }

  private async refreshKeyMetrics(symbol: string): Promise<void> {
    const record = firstRecord(await this.fmpClient.getKeyMetricsTtm(symbol, true));
    const snapshot: KeyMetricSnapshot = {
      symbol,
      enterpriseValue: num(record, "enterpriseValueTTM", "enterpriseValue"),
      evToSales: num(record, "enterpriseValueOverRevenueTTM", "evToSales"),
      evToFcf: num(record, "enterpriseValueOverFreeCashFlowTTM", "evToFreeCashFlow"),
      fcfYield: num(record, "freeCashFlowYieldTTM", "freeCashFlowYield"),
      roic: num(record, "roicTTM", "roic"),
      netDebtToEbitda: num(record, "netDebtToEBITDATTM", "netDebtToEBITDA"),
      currentRatio: num(record, "currentRatioTTM", "currentRatio"),
    };
    await this.repository.saveKeyMetrics(snapshot);
  }

  private async refreshAnalystEstimates(symbol: string): Promise<void> {
    const estimates: AnalystEstimate[] = asRecords(await this.fmpClient.getAnalystEstimates(symbol, true)).map((record) => ({
      symbol,
      fiscalYear: fiscalYearFrom(record),
      estimatedRevenueAvg: num(record, "estimatedRevenueAvg", "revenueAvg"),
      estimatedEpsAvg: num(record, "estimatedEpsAvg", "epsAvg"),
      analystCountRevenue: num(record, "numberAnalystEstimatedRevenue", "numberAnalystsEstimatedRevenue"),
      analystCountEps: num(record, "numberAnalystEstimatedEps", "numberAnalystsEstimatedEps"),
    }));
    await this.repository.saveAnalystEstimates(estimates);
  }

  private async refreshNews(symbol: string): Promise<void> {
    const news: NewsItem[] = asRecords(await this.fmpClient.getNews(symbol, true))
      .map((record) => {
        const title = str(record, "title") ?? "";
        const url = str(record, "url", "link");
        return {
          id: str(record, "id") ?? stableNewsId(symbol, title, url),
          symbol,
          title,
          url,
          publishedAt: str(record, "publishedDate", "date"),
        };
      })
      .filter((item) => item.title.length > 0);
    await this.repository.saveNews(news);
  }
}

function asRecords(payload: unknown): FmpRecord[] {
  if (Array.isArray(payload)) return payload.filter(isRecord);
  if (isRecord(payload)) {
    if (Array.isArray(payload.historical)) return payload.historical.filter(isRecord);
    if (Array.isArray(payload.data)) return payload.data.filter(isRecord);
    return [payload];
  }
  return [];
}

function historicalRecords(payload: unknown): FmpRecord[] {
  if (isRecord(payload) && Array.isArray(payload.historical)) return payload.historical.filter(isRecord).slice(0, 1260);
  return asRecords(payload).slice(0, 1260);
}

function firstRecord(payload: unknown): FmpRecord {
  return asRecords(payload)[0] ?? {};
}

function isRecord(value: unknown): value is FmpRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(record: FmpRecord, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim().length > 0) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function str(record: FmpRecord, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim().length > 0) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return null;
}

function fiscalYearFrom(record: FmpRecord): string {
  const direct = str(record, "calendarYear", "fiscalYear", "year", "period");
  if (direct) return direct;
  const date = str(record, "date", "fillingDate", "acceptedDate");
  return date ? date.slice(0, 4) : "unknown";
}

function getOrCreateFundamentalSnapshot(
  snapshots: Map<string, FinancialSnapshot>,
  symbol: string,
  fiscalYear: string,
): FinancialSnapshot {
  const existing = snapshots.get(fiscalYear);
  if (existing) return existing;
  const snapshot: FinancialSnapshot = {
    symbol,
    fiscalYear,
    revenue: null,
    netIncome: null,
    freeCashFlow: null,
    operatingCashFlow: null,
    totalDebt: null,
    totalCash: null,
  };
  snapshots.set(fiscalYear, snapshot);
  return snapshot;
}

function stableNewsId(symbol: string, title: string, url: string | null): string {
  return createHash("sha256").update(`${symbol}:${title}:${url ?? ""}`).digest("hex");
}
