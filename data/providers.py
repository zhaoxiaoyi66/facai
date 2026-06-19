from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from data.calculated_metrics import apply_calculated_metrics_to_snapshot
from data.fmp_cache import CACHE_TTL_SECONDS, FMPResponseCache
from data.fmp_queue import get_fmp_request_queue
from data.data_confidence import enrich_data_confidence
from data.disclosure_store import DisclosureStore
from data.fundamentals import FUNDAMENTAL_FIELDS, FundamentalCache
from data.ir_kpi_scraper import IRKPIClient
from data.prices import PriceCache
from data.sec_supplement import SECSupplementClient
from settings import PROJECT_ROOT


FMP_CACHE_SCHEMA_VERSION = 5


class MarketDataProvider(ABC):
    @abstractmethod
    def get_price_history(self, ticker: str, period: str = "2y", force_refresh: bool = False) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, ticker: str, force_refresh: bool = False) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_income_statement(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_balance_sheet(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_cash_flow(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_company_profile(self, ticker: str) -> dict:
        raise NotImplementedError


class PlaceholderProvider(MarketDataProvider):
    provider_name = "placeholder"

    def get_price_history(self, ticker: str, period: str = "2y", force_refresh: bool = False) -> pd.DataFrame:
        raise NotImplementedError(f"{self.provider_name} 数据源尚未实现。")

    def get_quote(self, ticker: str, force_refresh: bool = False) -> dict:
        raise NotImplementedError(f"{self.provider_name} 数据源尚未实现。")

    def get_income_statement(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError(f"{self.provider_name} 数据源尚未实现。")

    def get_balance_sheet(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError(f"{self.provider_name} 数据源尚未实现。")

    def get_cash_flow(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError(f"{self.provider_name} 数据源尚未实现。")

    def get_company_profile(self, ticker: str) -> dict:
        raise NotImplementedError(f"{self.provider_name} 数据源尚未实现。")


class FMPProvider(PlaceholderProvider):
    provider_name = "FMP"

    def __init__(
        self,
        api_key: str | None = None,
        price_cache: PriceCache | None = None,
        fundamental_cache: FundamentalCache | None = None,
        response_cache: FMPResponseCache | None = None,
        sec_supplement: SECSupplementClient | None = None,
        ir_kpi_client: IRKPIClient | None = None,
        disclosure_store: DisclosureStore | None = None,
        full_fundamentals: bool = True,
    ) -> None:
        self.api_key = api_key or get_secret("FMP_API_KEY")
        self.price_cache = price_cache or PriceCache()
        self.fundamental_cache = fundamental_cache or FundamentalCache()
        self.response_cache = response_cache or FMPResponseCache()
        self.sec_supplement = sec_supplement or SECSupplementClient()
        self.ir_kpi_client = ir_kpi_client or IRKPIClient()
        self.disclosure_store = disclosure_store or DisclosureStore()
        self.full_fundamentals = full_fundamentals

    def get_price_history(self, ticker: str, period: str = "2y", force_refresh: bool = False) -> pd.DataFrame:
        ticker = ticker.upper()
        cache_key = f"FMP:{ticker}"
        if not force_refresh:
            cached = self.price_cache.get_history(
                cache_key,
                max_age_hours=CACHE_TTL_SECONDS["historicalPrice"] / 3600,
            )
            if cached is not None:
                return cached

        try:
            raw = self._get_json(
                "historical-price-eod/full",
                {"symbol": ticker},
                force_refresh=force_refresh,
            )
        except RuntimeError as exc:
            if "缺少 FMP_API_KEY" in str(exc):
                raise
            stale = self.price_cache.get_history(cache_key, max_age_hours=24 * 30, min_rows=20)
            if stale is not None:
                return stale
            raise
        history = normalize_fmp_price_history(raw)
        self.price_cache.set_history(cache_key, history)
        return history

    def get_quote(self, ticker: str, force_refresh: bool = False) -> dict:
        ticker = ticker.upper()
        if not force_refresh:
            cached = self.fundamental_cache.get_snapshot(
                ticker,
                max_age_hours=CACHE_TTL_SECONDS["quote"] / 3600,
            )
            if (
                cached
                and cached.get("data_source") == "FMP"
                and cached.get("current_price") is not None
                and cached.get("cache_schema_version") == FMP_CACHE_SCHEMA_VERSION
                and (not self.full_fundamentals or cached.get("fundamental_depth") == "full")
            ):
                return self._with_supplements(ticker, cached, force_refresh=False)

        data_notes: list[str] = []
        try:
            quote = _first_row(
                self._get_json("quote", {"symbol": ticker}, force_refresh=force_refresh)
            )
        except RuntimeError as exc:
            if "缺少 FMP_API_KEY" in str(exc):
                raise
            stale = self.fundamental_cache.get_snapshot(ticker, max_age_hours=24 * 30)
            if stale and stale.get("data_source") == "FMP" and stale.get("current_price") is not None:
                stale["cache_note"] = "FMP 请求失败，当前显示的是本地缓存数据。"
                return self._with_supplements(ticker, stale, force_refresh=False)
            raise

        sections = self._load_fundamental_sections(ticker, force_refresh=force_refresh)
        data_notes.extend(sections.pop("notes", []))
        profile = sections["profile"]
        ratios = sections["ratios"]
        metrics = sections["metrics"]
        income = sections["income"]
        balance = sections["balance"]
        cash_flow = sections["cash_flow"]
        income_growth = sections["income_growth"]
        cash_flow_growth = sections["cash_flow_growth"]
        analyst_estimates = sections["analyst_estimates"]

        latest_income = income[0] if income else {}
        previous_income = income[1] if len(income) > 1 else {}
        latest_balance = balance[0] if balance else {}
        latest_cash_flow = cash_flow[0] if cash_flow else {}

        analyst_estimate = _first_estimate(analyst_estimates)

        current_price = _first_number(quote.get("price"))
        forward_eps = _first_number(
            analyst_estimate.get("estimatedEpsAvg"),
            analyst_estimate.get("epsAvg"),
            analyst_estimate.get("estimatedEps"),
        )
        forward_revenue = _first_number(
            analyst_estimate.get("estimatedRevenueAvg"),
            analyst_estimate.get("revenueAvg"),
            analyst_estimate.get("estimatedRevenue"),
        )
        revenue = _first_number(
            metrics.get("revenueTTM"),
            latest_income.get("revenue"),
        )
        previous_revenue = _first_number(previous_income.get("revenue"))
        net_income = _first_number(
            metrics.get("netIncomeTTM"),
            latest_income.get("netIncome"),
        )
        previous_net_income = _first_number(previous_income.get("netIncome"))
        total_debt = _first_number(latest_balance.get("totalDebt"))
        net_debt = _first_number(metrics.get("netDebtTTM"), metrics.get("netDebt"))
        total_cash = _first_number(
            latest_balance.get("cashAndCashEquivalents"),
            latest_balance.get("cashAndShortTermInvestments"),
            metrics.get("cashAndCashEquivalentsTTM"),
            metrics.get("cashAndShortTermInvestmentsTTM"),
        )
        if total_debt is None and net_debt is not None and total_cash is not None:
            total_debt = net_debt + total_cash
        equity = _first_number(
            latest_balance.get("totalStockholdersEquity"),
            latest_balance.get("totalEquity"),
        )
        gross_profit = _first_number(latest_income.get("grossProfit"))
        operating_income = _first_number(latest_income.get("operatingIncome"))
        ebitda = _first_number(
            metrics.get("ebitdaTTM"),
            metrics.get("ebitda"),
            latest_income.get("ebitda"),
            latest_income.get("ebitdaIncome"),
        )
        free_cash_flow = _first_number(
            metrics.get("freeCashFlowTTM"),
            latest_cash_flow.get("freeCashFlow"),
        )
        operating_cash_flow = _first_number(
            metrics.get("operatingCashFlowTTM"),
            latest_cash_flow.get("operatingCashFlow"),
            latest_cash_flow.get("netCashProvidedByOperatingActivities"),
        )
        stock_based_compensation = _first_number(
            latest_cash_flow.get("stockBasedCompensation"),
            latest_cash_flow.get("shareBasedCompensation"),
            latest_cash_flow.get("stockBasedCompensationExpense"),
        )
        interest_expense = _first_number(
            latest_income.get("interestExpense"),
            latest_income.get("interestExpenseNonOperating"),
        )
        market_cap = _first_number(quote.get("marketCap"), metrics.get("marketCap"), profile.get("mktCap"))
        enterprise_value = _first_number(metrics.get("enterpriseValueTTM"), _enterprise_value(market_cap, total_debt, total_cash))
        shares_outstanding = _first_number(
            quote.get("sharesOutstanding"),
            profile.get("sharesOutstanding"),
            latest_income.get("weightedAverageShsOutDil"),
            latest_income.get("weightedAverageShsOut"),
        )

        revenue_growth = _first_number(income_growth.get("growthRevenue"), _growth_rate(revenue, previous_revenue))
        earnings_growth = _first_number(income_growth.get("growthNetIncome"), _growth_rate(net_income, previous_net_income))
        fcf_growth = _first_number(cash_flow_growth.get("growthFreeCashFlow"))
        forward_revenue_growth = _growth_rate(forward_revenue, revenue)
        gross_margin = _first_number(ratios.get("grossProfitMarginTTM"), _ratio(gross_profit, revenue))
        operating_margin = _first_number(
            ratios.get("operatingProfitMarginTTM"),
            ratios.get("operatingMarginTTM"),
            _ratio(operating_income, revenue),
        )
        profit_margin = _first_number(
            ratios.get("netProfitMarginTTM"),
            ratios.get("bottomLineProfitMarginTTM"),
            _ratio(net_income, revenue),
        )
        return_on_equity = _first_number(
            ratios.get("returnOnEquityTTM"),
            metrics.get("returnOnEquityTTM"),
            _ratio(net_income, equity),
        )
        roic = _first_number(
            metrics.get("returnOnInvestedCapitalTTM"),
            metrics.get("returnOnCapitalEmployedTTM"),
            metrics.get("roicTTM"),
            ratios.get("returnOnCapitalEmployedTTM"),
        )
        debt_to_equity = _debt_to_equity_percent(
            metrics.get("debtToEquityTTM"),
            ratios.get("debtEquityRatioTTM"),
            _ratio(total_debt, equity),
        )
        current_ratio = _first_number(metrics.get("currentRatioTTM"), ratios.get("currentRatioTTM"))
        quick_ratio = _first_number(ratios.get("quickRatioTTM"))
        net_debt_to_ebitda = _first_number(metrics.get("netDebtToEBITDATTM"), _ratio(net_debt, ebitda))
        free_cash_flow_yield = _first_number(metrics.get("freeCashFlowYieldTTM"), _ratio(free_cash_flow, market_cap))
        price_to_fcf = _first_number(
            ratios.get("priceToFreeCashFlowRatioTTM"),
            metrics.get("pfcfRatioTTM"),
            _ratio(market_cap, free_cash_flow),
        )
        price_to_sales = _first_number(
            ratios.get("priceToSalesRatioTTM"),
            metrics.get("priceToSalesRatioTTM"),
            _ratio(market_cap, revenue),
        )
        enterprise_to_revenue = _first_number(metrics.get("evToSalesTTM"), _ratio(enterprise_value, revenue))
        enterprise_to_ebitda = _first_number(
            metrics.get("enterpriseValueOverEBITDATTM"),
            metrics.get("evToEBITDATTM"),
            _ratio(enterprise_value, ebitda),
        )
        trailing_pe = _first_number(
            quote.get("pe"),
            ratios.get("priceToEarningsRatioTTM"),
            metrics.get("peRatioTTM"),
        )
        forward_pe = _ratio(current_price, forward_eps) if forward_eps and forward_eps > 0 else None
        eps_ttm = _first_number(quote.get("eps"), latest_income.get("eps"), latest_income.get("epsdiluted"))
        forward_eps_growth = _growth_rate(forward_eps, eps_ttm)
        peg_ratio = None
        if forward_pe is not None and forward_eps_growth is not None and forward_eps_growth > 0:
            peg_ratio = forward_pe / (forward_eps_growth * 100)
        prior_operating_margin = _ratio(_first_number(previous_income.get("operatingIncome")), previous_revenue)
        prior_profit_margin = _ratio(previous_net_income, previous_revenue)

        snapshot = {
            "ticker": ticker,
            "company_name": profile.get("companyName") or quote.get("name"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "exchange": quote.get("exchange") or profile.get("exchangeShortName"),
            "beta": _first_number(profile.get("beta")),
            "current_price": current_price,
            "market_cap": market_cap,
            "fifty_two_week_high": _first_number(quote.get("yearHigh")),
            "fifty_two_week_low": _first_number(quote.get("yearLow")),
            "trailing_pe": trailing_pe,
            "forward_pe": forward_pe,
            "price_to_sales": price_to_sales,
            "enterprise_value": enterprise_value,
            "enterprise_to_revenue": enterprise_to_revenue,
            "enterprise_to_ebitda": enterprise_to_ebitda,
            "price_to_book": _first_number(quote.get("pb")),
            "price_to_fcf": price_to_fcf,
            "free_cash_flow_yield": free_cash_flow_yield,
            "free_cash_flow": free_cash_flow,
            "operating_cash_flow": operating_cash_flow,
            "total_revenue": revenue,
            "operating_income": operating_income,
            "ebit": _first_number(latest_income.get("ebit"), operating_income),
            "interest_expense": interest_expense,
            "net_income": net_income,
            "ebitda": ebitda,
            "stock_based_compensation": stock_based_compensation,
            "capital_expenditures": abs(
                _first_number(
                    latest_cash_flow.get("capitalExpenditure"),
                    latest_cash_flow.get("capitalExpenditures"),
                    metrics.get("capitalExpenditureTTM"),
                    metrics.get("capitalExpendituresTTM"),
                )
                or 0
            )
            or None,
            "total_debt": total_debt,
            "total_cash": total_cash,
            "shares_outstanding": shares_outstanding,
            "debt_to_equity": debt_to_equity,
            "net_debt_to_ebitda": net_debt_to_ebitda,
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "revenue_growth": revenue_growth,
            "forward_revenue_growth": forward_revenue_growth,
            "earnings_growth": earnings_growth,
            "free_cash_flow_growth": fcf_growth,
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "profit_margin": profit_margin,
            "prior_operating_margin": prior_operating_margin,
            "prior_profit_margin": prior_profit_margin,
            "return_on_equity": return_on_equity,
            "return_on_invested_capital": roic,
            "peg_ratio": peg_ratio,
            "forward_eps_growth_estimate": forward_eps_growth,
            "eps_ttm": eps_ttm,
            "forward_eps_estimate": forward_eps,
            "forward_revenue_estimate": forward_revenue,
            "analyst_count_eps": _first_number(
                analyst_estimate.get("numberAnalystsEstimatedEps"),
                analyst_estimate.get("numberAnalystEstimatedEps"),
            ),
            "analyst_count_revenue": _first_number(analyst_estimate.get("numberAnalystEstimatedRevenue")),
            "data_quality_notes": data_notes,
            "metric_sources": _initial_metric_sources(peg_ratio, forward_eps_growth),
            "metric_statuses": _initial_metric_statuses(peg_ratio),
            "fundamental_depth": "full" if self.full_fundamentals else "summary",
            "cache_schema_version": FMP_CACHE_SCHEMA_VERSION,
            "data_source": "FMP",
        }

        for field in FUNDAMENTAL_FIELDS:
            snapshot.setdefault(field, None)

        snapshot = self._with_supplements(ticker, snapshot, force_refresh=force_refresh)
        self.fundamental_cache.set_snapshot(ticker, snapshot)
        return snapshot

    def _with_supplements(self, ticker: str, snapshot: dict, force_refresh: bool = False) -> dict:
        enriched = dict(snapshot)
        if self.full_fundamentals:
            enriched = _merge_supplement(
                enriched,
                self.sec_supplement.get_supplement(
                    ticker,
                    total_revenue=enriched.get("total_revenue"),
                    force_refresh=force_refresh,
                ),
                prefer_existing_values=True,
            )
            enriched = _merge_supplement(
                enriched,
                self.ir_kpi_client.get_supplement(ticker, force_refresh=force_refresh),
                prefer_existing_values=False,
            )
            enriched = _merge_disclosure_supplement(enriched, self.disclosure_store.metric_supplement(ticker, scoring_only=True))
        manual_overrides = self.fundamental_cache.get_manual_overrides(ticker)
        enriched.update(manual_overrides)
        _tag_manual_sources(enriched, manual_overrides)
        enriched = apply_calculated_metrics_to_snapshot(enriched)
        return enrich_data_confidence(enriched)

    def get_income_statement(self, ticker: str) -> pd.DataFrame:
        return pd.DataFrame(self._get_json("income-statement", {"symbol": ticker, "limit": 5}))

    def get_balance_sheet(self, ticker: str) -> pd.DataFrame:
        return pd.DataFrame(self._get_json("balance-sheet-statement", {"symbol": ticker, "limit": 5}))

    def get_cash_flow(self, ticker: str) -> pd.DataFrame:
        return pd.DataFrame(self._get_json("cash-flow-statement", {"symbol": ticker, "limit": 5}))

    def get_company_profile(self, ticker: str) -> dict:
        return _first_row(self._get_json("profile", {"symbol": ticker}))

    def _load_fundamental_sections(self, ticker: str, force_refresh: bool = False) -> dict:
        tasks = [
            ("profile", "first", "公司资料", "profile", {"symbol": ticker}, 6, 0),
            ("ratios", "first", "TTM 比率", "ratios-ttm", {"symbol": ticker}, 6, 0),
            ("metrics", "first", "TTM 关键指标", "key-metrics-ttm", {"symbol": ticker}, 6, 0),
            ("income_growth", "first", "利润表增长率", "income-statement-growth", {"symbol": ticker, "limit": 3}, 6, 0),
        ]
        if self.full_fundamentals:
            tasks.extend(
                [
                    ("income", "records", "利润表", "income-statement", {"symbol": ticker, "limit": 5}, 8, 0),
                    ("balance", "records", "资产负债表", "balance-sheet-statement", {"symbol": ticker, "limit": 3}, 6, 0),
                    ("cash_flow", "records", "现金流量表", "cash-flow-statement", {"symbol": ticker, "limit": 5}, 8, 0),
                    ("cash_flow_growth", "first", "现金流增长率", "cash-flow-statement-growth", {"symbol": ticker, "limit": 3}, 6, 0),
                    (
                        "analyst_estimates",
                        "records",
                        "分析师预期",
                        "analyst-estimates",
                        {"symbol": ticker, "period": "annual", "page": 0, "limit": 5},
                        4,
                        0,
                    ),
                ]
            )

        sections = {
            "profile": {},
            "ratios": {},
            "metrics": {},
            "income": [],
            "balance": [],
            "cash_flow": [],
            "income_growth": {},
            "cash_flow_growth": {},
            "analyst_estimates": [],
            "notes": [],
        }
        with ThreadPoolExecutor(max_workers=min(6, len(tasks))) as executor:
            future_map = {
                executor.submit(
                    self._load_section,
                    kind,
                    label,
                    endpoint,
                    params,
                    timeout,
                    retries,
                    force_refresh,
                ): name
                for name, kind, label, endpoint, params, timeout, retries in tasks
            }
            for future in as_completed(future_map):
                name = future_map[future]
                value, note = future.result()
                sections[name] = value
                if note:
                    sections["notes"].append(note)
        return sections

    def _load_section(
        self,
        kind: str,
        label: str,
        endpoint: str,
        params: dict,
        timeout_seconds: int,
        retries: int,
        force_refresh: bool = False,
    ) -> tuple[dict | list[dict], str | None]:
        try:
            data = self._get_json(
                endpoint,
                params,
                timeout_seconds=timeout_seconds,
                retries=retries,
                force_refresh=force_refresh,
            )
        except RuntimeError as exc:
            return ([], f"{label}不可用：{_short_error(exc)}") if kind == "records" else ({}, f"{label}不可用：{_short_error(exc)}")
        if kind == "records":
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)], None
            return ([data], None) if isinstance(data, dict) else ([], None)
        return _first_row(data), None

    def _get_json(
        self,
        endpoint: str,
        params: dict,
        timeout_seconds: int = 20,
        retries: int = 2,
        force_refresh: bool = False,
    ) -> list | dict:
        if not self.api_key:
            raise RuntimeError("缺少 FMP_API_KEY。请在环境变量或 .env 文件中配置后再使用 FMP 数据源。")

        if not force_refresh:
            cached = self.response_cache.get(endpoint, params)
            if cached is not None:
                return cached

        query = urlencode({**params, "apikey": self.api_key})
        url = f"https://financialmodelingprep.com/stable/{endpoint}?{query}"
        request = Request(url, headers={"User-Agent": "ZHX-Research/1.0"})
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                payload = get_fmp_request_queue().submit(
                    lambda: _read_url(request, timeout_seconds=timeout_seconds),
                    timeout_seconds=timeout_seconds + 8,
                )
                break
            except HTTPError as exc:
                raise RuntimeError(_friendly_fmp_http_error(exc)) from exc
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    raise RuntimeError(f"FMP 请求失败：{exc}") from exc
                time.sleep(0.6 * (attempt + 1))
        else:
            raise RuntimeError(f"FMP 请求失败：{last_error}")

        data = json.loads(payload)
        if isinstance(data, dict) and data.get("Error Message"):
            raise RuntimeError(f"FMP 返回错误：{data['Error Message']}")
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"FMP 返回错误：{data['error']}")
        self.response_cache.set(endpoint, params, data)
        return data


STANDARD_SEC_FIELDS = {
    "total_revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capital_expenditures",
    "free_cash_flow",
    "operating_margin",
    "fcf_margin",
    "total_debt",
    "total_cash",
    "shares_outstanding",
    "diluted_shares",
}


def _merge_supplement(snapshot: dict, supplement: dict, prefer_existing_values: bool = False) -> dict:
    enriched = dict(snapshot)
    metric_sources = dict(enriched.get("metric_sources") or {})
    metric_statuses = dict(enriched.get("metric_statuses") or {})
    skipped_source_keys: set[str] = set()

    for key, value in supplement.items():
        if key == "metric_sources" and isinstance(value, dict):
            metric_sources.update(value)
            continue
        if key == "metric_statuses" and isinstance(value, dict):
            metric_statuses.update(value)
            continue
        if prefer_existing_values and key in STANDARD_SEC_FIELDS and enriched.get(key) is not None:
            skipped_source_keys.add(key)
            continue
        enriched[key] = value

    for key in skipped_source_keys:
        metric_sources.pop(key, None)
    if metric_sources:
        enriched["metric_sources"] = metric_sources
    if metric_statuses:
        enriched["metric_statuses"] = metric_statuses
    return enriched


def _merge_disclosure_supplement(snapshot: dict, supplement: dict) -> dict:
    if not supplement:
        return snapshot

    enriched = dict(snapshot)
    metric_sources = dict(enriched.get("metric_sources") or {})
    metric_statuses = dict(enriched.get("metric_statuses") or {})
    supplement_sources = supplement.get("metric_sources") if isinstance(supplement.get("metric_sources"), dict) else {}
    skipped_source_keys: set[str] = set()

    for key, value in supplement.items():
        if key == "metric_sources" and isinstance(value, dict):
            metric_sources.update(value)
            continue
        if key == "metric_statuses" and isinstance(value, dict):
            metric_statuses.update(value)
            continue
        if key == "disclosureMetrics" and isinstance(value, list):
            enriched[key] = value
            continue

        source = supplement_sources.get(key) if isinstance(supplement_sources, dict) else None
        confidence = str(source.get("confidence")) if isinstance(source, dict) and source.get("confidence") else ""
        if confidence == "low" and enriched.get(key) is not None:
            skipped_source_keys.add(key)
            continue
        enriched[key] = value

    for key in skipped_source_keys:
        metric_sources.pop(key, None)
    if metric_sources:
        enriched["metric_sources"] = metric_sources
    if metric_statuses:
        enriched["metric_statuses"] = metric_statuses
    return enriched


def _tag_manual_sources(snapshot: dict, manual_overrides: dict) -> None:
    if not manual_overrides:
        return
    metric_sources = dict(snapshot.get("metric_sources") or {})
    manual_source_types = {
        "manualSubscriptionRevenueGrowth": "reported_ir",
        "manualRpoGrowth": "reported_ir",
        "manualArrGrowth": "reported_ir",
        "manualNetRetention": "reported_ir",
        "manualLargeCustomerGrowth": "reported_ir",
        "manualNonGaapOperatingMargin": "non_gaap_reported",
        "manualSbcRatio": "calculated",
    }
    for key, source_type in manual_source_types.items():
        if key in manual_overrides and manual_overrides[key] is not None:
            metric_sources[key] = {
                "sourceType": source_type,
                "source": "manual override",
                "reviewStatus": "manually_corrected",
                "reviewedBy": "local_user",
                "scoringAllowed": True,
            }
            snapshot[f"{key}_sourceType"] = source_type
    snapshot["metric_sources"] = metric_sources


def _initial_metric_sources(peg_ratio: float | None, forward_eps_growth: float | None) -> dict:
    sources = {
        "forward_eps_estimate": {"sourceType": "estimated", "source": "FMP analyst estimates"},
        "forward_revenue_estimate": {"sourceType": "estimated", "source": "FMP analyst estimates"},
    }
    if forward_eps_growth is not None:
        sources["forward_eps_growth_estimate"] = {"sourceType": "estimated", "formula": "forward_eps / eps_ttm - 1"}
    if peg_ratio is not None:
        sources["peg_ratio"] = {"sourceType": "estimated", "formula": "forward_pe / forward EPS growth %"}
    return sources


def _initial_metric_statuses(peg_ratio: float | None) -> dict:
    if peg_ratio is not None:
        return {"peg_ratio": {"status": "available", "sourceType": "estimated"}}
    return {"peg_ratio": {"status": "requires_estimates", "sourceType": "requires_estimates"}}


class PolygonProvider(PlaceholderProvider):
    provider_name = "Polygon"


class SECEdgarProvider(PlaceholderProvider):
    provider_name = "SEC Edgar"


def get_market_data_provider(provider_name: str = "fmp", full_fundamentals: bool = True) -> MarketDataProvider:
    normalized = provider_name.strip().lower()
    if normalized in {"fmp", "financialmodelingprep"}:
        return FMPProvider(full_fundamentals=full_fundamentals)
    if normalized == "polygon":
        return PolygonProvider()
    if normalized in {"sec", "sec-edgar", "edgar"}:
        return SECEdgarProvider()
    raise ValueError(f"不支持的市场数据源：{provider_name}")


def _friendly_fmp_http_error(exc: HTTPError) -> str:
    if exc.code == 402:
        return "FMP 返回 402：免费额度可能已用完，或当前接口需要付费。请稍后再试，或使用本地缓存/待补数据。"
    if exc.code == 429:
        return "FMP 请求过快：请稍等一会儿再刷新。"
    return f"FMP 请求失败：HTTP {exc.code} {exc.reason}"


def _read_url(request: Request, timeout_seconds: int) -> str:
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def _safe_first_row(loader, notes: list[str], label: str) -> dict:
    try:
        return _first_row(loader())
    except RuntimeError as exc:
        notes.append(f"{label}不可用：{_short_error(exc)}")
        return {}


def _safe_records(loader, notes: list[str], label: str) -> list[dict]:
    try:
        data = loader()
    except RuntimeError as exc:
        notes.append(f"{label}不可用：{_short_error(exc)}")
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first_estimate(records: list[dict]) -> dict:
    for record in records:
        if _first_number(record.get("estimatedEpsAvg"), record.get("estimatedRevenueAvg")) is not None:
            return record
    return {}


def _short_error(exc: Exception, limit: int = 90) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message if len(message) <= limit else message[: limit - 1] + "…"


def normalize_fmp_price_history(raw: list | dict) -> pd.DataFrame:
    rows = raw.get("historical", raw) if isinstance(raw, dict) else raw
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows).rename(
        columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "adjClose": "adjusted_close",
            "adjustedClose": "adjusted_close",
            "adj_close": "adjusted_close",
            "adjusted_close": "adjusted_close",
            "volume": "volume",
        }
    )
    keep = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]
    df = df[[column for column in keep if column in df.columns]].copy()
    for column in ["open", "high", "low", "close", "adjusted_close", "volume"]:
        if column not in df.columns:
            df[column] = None
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df[keep].sort_values("date").dropna(subset=["date", "close"])


def _first_number(*values: object) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if pd.isna(number):
            continue
        return number
    return None


def _first_row(data: list | dict) -> dict:
    if isinstance(data, list):
        return data[0] if data else {}
    return data


def _ratio(numerator: float | None, denominator: float | None, multiplier: float = 1.0) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator * multiplier


def _debt_to_equity_percent(*values: object) -> float | None:
    value = _first_number(*values)
    if value is None:
        return None
    if abs(value) <= 10:
        return value * 100
    return value


def _growth_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return current / previous - 1


def _enterprise_value(market_cap: float | None, total_debt: float | None, total_cash: float | None) -> float | None:
    if market_cap is None:
        return None
    return market_cap + (total_debt or 0) - (total_cash or 0)


def get_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value.strip()

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip().lstrip("\ufeff") == name:
            return raw_value.strip().strip('"').strip("'")
    return None
