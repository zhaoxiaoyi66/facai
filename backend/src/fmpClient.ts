import { FMP_BASE_URL, FMP_ENDPOINT_CACHE_BUCKET, CacheBucket } from "./config";
import { apiCallLogger, ApiCallLogger } from "./apiCallLogger";
import { cacheService, CacheService } from "./cacheService";
import { fmpRateLimiter, RateLimiter, sleep } from "./rateLimiter";

type Query = Record<string, string | number | boolean | undefined | null>;

export interface FmpClientOptions {
  apiKey?: string;
  cache?: CacheService;
  rateLimiter?: RateLimiter;
  logger?: ApiCallLogger;
}

export class FmpClient {
  private readonly apiKey: string;
  private readonly cache: CacheService;
  private readonly rateLimiter: RateLimiter;
  private readonly logger: ApiCallLogger;

  constructor(options: FmpClientOptions = {}) {
    assertServerSideOnly();
    this.apiKey = options.apiKey ?? process.env.FMP_API_KEY ?? "";
    this.cache = options.cache ?? cacheService;
    this.rateLimiter = options.rateLimiter ?? fmpRateLimiter;
    this.logger = options.logger ?? apiCallLogger;

    if (!this.apiKey) {
      throw new Error("Missing FMP_API_KEY. Configure it on the backend only.");
    }
  }

  getQuote(symbol: string, forceRefresh = false) {
    return this.get("quote", { symbol }, forceRefresh);
  }

  getProfile(symbol: string, forceRefresh = false) {
    return this.get("profile", { symbol }, forceRefresh);
  }

  getRatiosTtm(symbol: string, forceRefresh = false) {
    return this.get("ratios-ttm", { symbol }, forceRefresh);
  }

  getKeyMetricsTtm(symbol: string, forceRefresh = false) {
    return this.get("key-metrics-ttm", { symbol }, forceRefresh);
  }

  getAnnualIncomeStatement(symbol: string, forceRefresh = false) {
    return this.get("income-statement", { symbol, limit: 5 }, forceRefresh);
  }

  getAnnualBalanceSheet(symbol: string, forceRefresh = false) {
    return this.get("balance-sheet-statement", { symbol, limit: 5 }, forceRefresh);
  }

  getAnnualCashFlow(symbol: string, forceRefresh = false) {
    return this.get("cash-flow-statement", { symbol, limit: 5 }, forceRefresh);
  }

  getHistoricalDaily(symbol: string, forceRefresh = false) {
    return this.get("historical-price-eod/full", { symbol }, forceRefresh);
  }

  getNews(symbol: string, forceRefresh = false) {
    return this.get("stock-news", { symbols: symbol, limit: 20 }, forceRefresh);
  }

  getAnalystEstimates(symbol: string, forceRefresh = false) {
    return this.get("analyst-estimates", { symbol, period: "annual", page: 0, limit: 10 }, forceRefresh);
  }

  async get<T = unknown>(endpoint: string, query: Query, forceRefresh = false): Promise<T> {
    const bucket = bucketForEndpoint(endpoint);
    const cacheKey = [endpoint, query];
    const symbol = typeof query.symbol === "string" ? query.symbol : undefined;

    if (!forceRefresh) {
      const cached = await this.cache.get<T>(bucket, cacheKey);
      if (cached !== null) {
        await this.logger.log({
          provider: "FMP",
          endpoint,
          symbol,
          status: "cache_hit",
          durationMs: 0,
          attempt: 0,
          createdAt: new Date().toISOString(),
        });
        return cached;
      }
    }

    const started = Date.now();
    const payload = await this.requestWithRetry<T>(endpoint, query, symbol);
    await this.cache.set(bucket, cacheKey, payload);
    await this.logger.log({
      provider: "FMP",
      endpoint,
      symbol,
      status: "success",
      durationMs: Date.now() - started,
      attempt: 1,
      createdAt: new Date().toISOString(),
    });
    return payload;
  }

  private async requestWithRetry<T>(endpoint: string, query: Query, symbol?: string): Promise<T> {
    const maxAttempts = 3;
    let lastError: unknown;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const started = Date.now();
      try {
        return await this.rateLimiter.schedule(() => this.fetchJson<T>(endpoint, query));
      } catch (error) {
        lastError = error;
        await this.logger.log({
          provider: "FMP",
          endpoint,
          symbol,
          status: "failed",
          durationMs: Date.now() - started,
          attempt,
          error: error instanceof Error ? error.message : String(error),
          createdAt: new Date().toISOString(),
        });
        if (attempt < maxAttempts) {
          await sleep(500 * 2 ** (attempt - 1));
        }
      }
    }

    throw lastError instanceof Error ? lastError : new Error(String(lastError));
  }

  private async fetchJson<T>(endpoint: string, query: Query): Promise<T> {
    const url = new URL(`${FMP_BASE_URL}/${endpoint}`);
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
    url.searchParams.set("apikey", this.apiKey);

    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`FMP ${response.status}: ${response.statusText}`);
    }
    return (await response.json()) as T;
  }
}

export function bucketForEndpoint(endpoint: string): CacheBucket {
  return FMP_ENDPOINT_CACHE_BUCKET[endpoint] ?? "quote";
}

function assertServerSideOnly(): void {
  if (typeof (globalThis as { window?: unknown }).window !== "undefined") {
    throw new Error("FmpClient is backend-only. Never instantiate it in frontend code.");
  }
}
