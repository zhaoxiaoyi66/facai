from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

from data.prices import CACHE_PATH


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_USER_AGENT = "zhx-research/0.1 contact@example.com"

SBC_TAGS = (
    "ShareBasedCompensation",
    "ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsGrantsInPeriodTotal",
    "ShareBasedCompensationArrangementByShareBasedPaymentAwardOptionsGrantsInPeriodGross",
)
REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
OPERATING_INCOME_TAGS = (
    "OperatingIncomeLoss",
)
NET_INCOME_TAGS = (
    "NetIncomeLoss",
    "ProfitLoss",
)
OPERATING_CASH_FLOW_TAGS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
CAPEX_TAGS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CapitalExpenditures",
)
DILUTED_SHARES_TAGS = (
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
)
DEBT_TAGS = (
    "DebtCurrent",
    "ShortTermBorrowings",
    "LongTermDebtCurrent",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
    "LongTermDebtNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
)
CASH_TAGS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
RPO_TAGS = (
    "RemainingPerformanceObligation",
    "RemainingPerformanceObligations",
    "RevenueRemainingPerformanceObligation",
    "ContractWithCustomerLiabilityRevenueRemainingPerformanceObligation",
)
DEFERRED_REVENUE_TAGS = (
    "ContractWithCustomerLiability",
    "DeferredRevenueCurrent",
    "DeferredRevenue",
    "DeferredRevenueAndCreditsCurrent",
)
HOOD_INTEREST_REVENUE_TAGS = (
    "InterestIncomeExpenseNet",
    "InterestIncomeOperating",
)


class SECSupplementClient:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sec_response_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )

    def get_supplement(self, ticker: str, total_revenue: float | None = None, force_refresh: bool = False) -> dict:
        ticker = ticker.upper()
        try:
            cik = self._cik_for_ticker(ticker, force_refresh=force_refresh)
            if cik is None:
                return {"sec_supplement_status": "ticker_not_found"}
            facts = self._companyfacts(cik, force_refresh=force_refresh)
        except Exception as exc:
            return {"sec_supplement_status": "unavailable", "sec_supplement_note": _short_error(exc)}

        supplement = extract_sec_saas_metrics(facts, total_revenue=total_revenue)
        if ticker == "HOOD":
            supplement = _merge_metric_supplement(supplement, extract_sec_hood_metrics(facts))
        supplement["sec_supplement_status"] = "available"
        supplement["sec_cik"] = cik
        return supplement

    def _cik_for_ticker(self, ticker: str, force_refresh: bool = False) -> str | None:
        data = self._cached_json("sec_company_tickers", SEC_TICKERS_URL, ttl_hours=24 * 7, force_refresh=force_refresh)
        for row in data.values() if isinstance(data, dict) else []:
            if str(row.get("ticker", "")).upper() == ticker:
                cik = int(row["cik_str"])
                return f"{cik:010d}"
        return None

    def _companyfacts(self, cik: str, force_refresh: bool = False) -> dict:
        url = SEC_COMPANYFACTS_URL.format(cik=cik)
        return self._cached_json(f"sec_companyfacts_{cik}", url, ttl_hours=12, force_refresh=force_refresh)

    def _cached_json(self, cache_key: str, url: str, ttl_hours: float, force_refresh: bool = False):
        if not force_refresh:
            cached = self._get_cached(cache_key, ttl_hours=ttl_hours)
            if cached is not None:
                return cached
        request = Request(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "identity"})
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._set_cached(cache_key, payload)
        time.sleep(0.12)
        return payload

    def _get_cached(self, cache_key: str, ttl_hours: float):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json, fetched_at FROM sec_response_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        payload_json, fetched_at = row
        try:
            fetched = datetime.fromisoformat(fetched_at)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - fetched > timedelta(hours=ttl_hours):
            return None
        return json.loads(payload_json)

    def _set_cached(self, cache_key: str, payload) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sec_response_cache (cache_key, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (cache_key, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
            )


def extract_sec_saas_metrics(companyfacts: dict, total_revenue: float | None = None) -> dict:
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    revenue = _latest_duration_value(us_gaap, REVENUE_TAGS)
    revenue_value = revenue["value"] if revenue else total_revenue
    operating_income = _latest_duration_value(us_gaap, OPERATING_INCOME_TAGS)
    net_income = _latest_duration_value(us_gaap, NET_INCOME_TAGS)
    operating_cash_flow = _latest_duration_value(us_gaap, OPERATING_CASH_FLOW_TAGS)
    capex = _latest_duration_value(us_gaap, CAPEX_TAGS)
    sbc = _latest_duration_value(us_gaap, SBC_TAGS)
    diluted_shares = _latest_duration_value(us_gaap, DILUTED_SHARES_TAGS, units=("shares", "Shares"))
    debt_values = _recent_point_values(us_gaap, DEBT_TAGS)
    cash = _latest_point_value(us_gaap, CASH_TAGS)
    rpo_values = _recent_point_values(us_gaap, RPO_TAGS)
    deferred_values = _recent_point_values(us_gaap, DEFERRED_REVENUE_TAGS)

    supplement: dict = {
        "secSupplementSource": "SEC companyfacts",
        "metric_sources": {},
    }
    metric_sources = supplement["metric_sources"]
    if revenue:
        supplement["total_revenue"] = revenue["value"]
        supplement["sec_revenue"] = revenue["value"]
        metric_sources["total_revenue"] = {"sourceType": "reported_sec", "source": revenue["tag"]}
    if operating_income:
        supplement["operating_income"] = operating_income["value"]
        metric_sources["operating_income"] = {"sourceType": "reported_sec", "source": operating_income["tag"]}
    if net_income:
        supplement["net_income"] = net_income["value"]
        metric_sources["net_income"] = {"sourceType": "reported_sec", "source": net_income["tag"]}
    if operating_cash_flow:
        supplement["operating_cash_flow"] = operating_cash_flow["value"]
        metric_sources["operating_cash_flow"] = {"sourceType": "reported_sec", "source": operating_cash_flow["tag"]}
    if capex:
        capex_value = abs(capex["value"])
        supplement["capital_expenditures"] = capex_value
        metric_sources["capital_expenditures"] = {"sourceType": "reported_sec", "source": capex["tag"]}
    if operating_cash_flow and capex:
        fcf = operating_cash_flow["value"] - abs(capex["value"])
        supplement["free_cash_flow"] = fcf
        metric_sources["free_cash_flow"] = {"sourceType": "calculated", "formula": "operating_cash_flow - capex"}
        fcf_margin = _ratio(fcf, revenue_value)
        if fcf_margin is not None:
            supplement["fcf_margin"] = fcf_margin
            metric_sources["fcf_margin"] = {"sourceType": "calculated", "formula": "FCF / revenue"}
    if operating_income:
        gaap_margin = _ratio(operating_income["value"], revenue_value)
        if gaap_margin is not None:
            supplement["operating_margin"] = gaap_margin
            metric_sources["operating_margin"] = {"sourceType": "calculated", "formula": "operating_income / revenue"}
    if diluted_shares:
        supplement["diluted_shares"] = diluted_shares["value"]
        supplement["shares_outstanding"] = diluted_shares["value"]
        metric_sources["diluted_shares"] = {"sourceType": "reported_sec", "source": diluted_shares["tag"]}
    if debt_values:
        total_debt = sum(row["value"] for row in _latest_points_by_tag(debt_values))
        supplement["total_debt"] = total_debt
        metric_sources["total_debt"] = {"sourceType": "reported_sec", "source": " + ".join(sorted({row["tag"] for row in _latest_points_by_tag(debt_values)}))}
    if cash:
        supplement["total_cash"] = cash["value"]
        metric_sources["total_cash"] = {"sourceType": "reported_sec", "source": cash["tag"]}

    if sbc:
        supplement["stock_based_compensation"] = sbc["value"]
        supplement["stock_based_compensation_source"] = sbc["tag"]
        metric_sources["stock_based_compensation"] = {"sourceType": "reported_sec", "source": sbc["tag"]}
        ratio = _ratio(sbc["value"], revenue_value)
        if ratio is not None:
            supplement["sbc_ratio"] = ratio
            supplement["stock_based_compensation_ratio"] = ratio
            metric_sources["sbc_ratio"] = {"sourceType": "calculated", "formula": "share_based_compensation / revenue"}
            metric_sources["stock_based_compensation_ratio"] = {"sourceType": "calculated", "formula": "share_based_compensation / revenue"}

    if rpo_values:
        latest = rpo_values[0]
        supplement["rpo"] = latest["value"]
        supplement["rpo_source"] = latest["tag"]
        metric_sources["rpo"] = {"sourceType": "reported_sec", "source": latest["tag"]}
        growth = _growth_from_recent_values(rpo_values)
        if growth is not None:
            supplement["rpo_growth"] = growth
            metric_sources["rpo_growth"] = {"sourceType": "calculated", "formula": "latest RPO / prior comparable RPO - 1"}

    if deferred_values:
        latest = deferred_values[0]
        supplement["deferred_revenue"] = latest["value"]
        supplement["deferred_revenue_source"] = latest["tag"]
        metric_sources["deferred_revenue"] = {"sourceType": "reported_sec", "source": latest["tag"]}
        growth = _growth_from_recent_values(deferred_values)
        if growth is not None:
            supplement["deferred_revenue_growth"] = growth
            metric_sources["deferred_revenue_growth"] = {"sourceType": "calculated", "formula": "latest deferred revenue / prior comparable deferred revenue - 1"}

    return supplement


def extract_sec_hood_metrics(companyfacts: dict) -> dict:
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    interest_revenue = _latest_duration_value(us_gaap, HOOD_INTEREST_REVENUE_TAGS)
    supplement: dict = {
        "secSupplementSource": "SEC companyfacts",
        "metric_sources": {},
    }
    metric_sources = supplement["metric_sources"]
    if interest_revenue:
        supplement["hood_interest_revenue"] = interest_revenue["value"]
        metric_sources["hood_interest_revenue"] = {
            "sourceType": "reported_sec",
            "source": interest_revenue["tag"],
            "sourceDocumentTitle": "SEC companyfacts / 10-Q / 10-K",
        }
    return supplement


def _merge_metric_supplement(base: dict, extra: dict) -> dict:
    merged = dict(base)
    base_sources = base.get("metric_sources") if isinstance(base.get("metric_sources"), dict) else {}
    extra_sources = extra.get("metric_sources") if isinstance(extra.get("metric_sources"), dict) else {}
    merged_sources = {**base_sources, **extra_sources}
    for key, value in extra.items():
        if key == "metric_sources":
            continue
        merged[key] = value
    if merged_sources:
        merged["metric_sources"] = merged_sources
    return merged


def _latest_duration_value(us_gaap: dict, tags: tuple[str, ...], units: tuple[str, ...] = ("USD", "usd")) -> dict | None:
    values: list[dict] = []
    for tag in tags:
        for item in _units(us_gaap, tag, units):
            if item.get("fp") not in {"FY", "Q1", "Q2", "Q3", "Q4"}:
                continue
            value = _number(item.get("val"))
            if value is None:
                continue
            values.append({"tag": tag, "value": value, "end": item.get("end"), "fy": item.get("fy"), "fp": item.get("fp")})
    return _latest(values)


def _recent_point_values(us_gaap: dict, tags: tuple[str, ...]) -> list[dict]:
    values: list[dict] = []
    for tag in tags:
        for item in _usd_units(us_gaap, tag):
            value = _number(item.get("val"))
            if value is None:
                continue
            values.append({"tag": tag, "value": value, "end": item.get("end"), "fy": item.get("fy"), "fp": item.get("fp")})
    values.sort(key=lambda row: str(row.get("end") or ""), reverse=True)
    return values[:8]


def _latest_point_value(us_gaap: dict, tags: tuple[str, ...]) -> dict | None:
    values = _recent_point_values(us_gaap, tags)
    return values[0] if values else None


def _latest_points_by_tag(values: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for row in values:
        tag = row["tag"]
        if tag not in latest:
            latest[tag] = row
    return list(latest.values())


def _usd_units(us_gaap: dict, tag: str) -> list[dict]:
    return _units(us_gaap, tag, ("USD", "usd"))


def _units(us_gaap: dict, tag: str, unit_keys: tuple[str, ...]) -> list[dict]:
    units = (us_gaap.get(tag) or {}).get("units") or {}
    for unit_key in unit_keys:
        if isinstance(units.get(unit_key), list):
            return units[unit_key]
    return []


def _growth_from_recent_values(values: list[dict]) -> float | None:
    if len(values) < 2:
        return None
    latest = values[0]["value"]
    current_fp = values[0].get("fp")
    candidate = None
    for item in values[1:]:
        if current_fp and item.get("fp") == current_fp:
            candidate = item
            break
    if candidate is None:
        candidate = values[1]
    return _growth(latest, candidate["value"])


def _latest(values: list[dict]) -> dict | None:
    if not values:
        return None
    values.sort(key=lambda row: str(row.get("end") or ""), reverse=True)
    return values[0]


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return current / previous - 1


def _number(value) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _short_error(exc: Exception, limit: int = 120) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
