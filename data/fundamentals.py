from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


FUNDAMENTAL_FIELDS = [
    "company_name",
    "sector",
    "industry",
    "exchange",
    "beta",
    "current_price",
    "market_cap",
    "fifty_two_week_high",
    "fifty_two_week_low",
    "trailing_pe",
    "forward_pe",
    "price_to_sales",
    "enterprise_value",
    "enterprise_to_revenue",
    "enterprise_to_ebitda",
    "price_to_book",
    "price_to_fcf",
    "free_cash_flow_yield",
    "free_cash_flow",
    "operating_cash_flow",
    "total_revenue",
    "operating_income",
    "net_income",
    "ebitda",
    "capital_expenditures",
    "total_debt",
    "total_cash",
    "diluted_shares",
    "shares_outstanding",
    "debt_to_equity",
    "net_debt_to_ebitda",
    "current_ratio",
    "quick_ratio",
    "revenue_growth",
    "forward_revenue_growth",
    "earnings_growth",
    "free_cash_flow_growth",
    "gross_margin",
    "operating_margin",
    "profit_margin",
    "prior_operating_margin",
    "prior_profit_margin",
    "return_on_equity",
    "return_on_invested_capital",
    "fcf_margin",
    "peg_ratio",
    "forward_revenue_multiple",
    "forward_eps_growth_estimate",
    "eps_ttm",
    "forward_eps_estimate",
    "forward_revenue_estimate",
    "analyst_count_eps",
    "analyst_count_revenue",
    "adjustedEbitda",
    "adjustedEbitdaGrowth",
    "adjustedFcfBeforeGrowth",
    "hedgeCoverageCurrentYear",
    "hedgeCoverageNextYear",
    "buybackAmount",
    "shareCountReduction",
    "generationMix",
    "nuclearCapacityExposure",
    "dataCenterPowerDemandExposure",
    "regulatoryRisk",
    "commodityPriceExposure",
    "manualAdjustedEbitda",
    "manualAdjustedEbitdaGrowth",
    "manualAdjustedFcfBeforeGrowth",
    "manualNetDebtToAdjustedEbitda",
    "manualHedgeCoverageCurrentYear",
    "manualHedgeCoverageNextYear",
    "manualBuybackAmount",
    "manualShareCountReduction",
    "modelType",
    "manualArrGrowth",
    "manualRpoGrowth",
    "manualNetRetention",
    "manualSubscriptionRevenueGrowth",
    "manualNonGaapOperatingMargin",
    "manualLargeCustomerGrowth",
    "manualSbcRatio",
    "manualCustomerConcentration",
    "manualDilutionRisk",
    "manualInventoryRisk",
    "manualSemiconductorCycleRisk",
    "manualAcquisitionIntegrationRisk",
    "manualAiDisruptionRisk",
    "subscription_revenue_growth",
    "crpo_growth",
    "non_gaap_operating_margin",
    "non_gaap_fcf_margin",
    "net_retention_rate",
    "large_customer_growth",
    "customers_over_100k_arr",
    "customers_over_1m_arr",
    "ir_kpi_status",
    "irKpiMapping",
    "stock_based_compensation",
    "stock_based_compensation_ratio",
    "sbc_ratio",
    "rpo",
    "rpo_growth",
    "deferred_revenue",
    "deferred_revenue_growth",
    "secSupplementSource",
    "sec_supplement_status",
    "sec_supplement_note",
    "metric_sources",
    "metric_statuses",
    "availableCriticalMetrics",
    "missingCriticalMetrics",
    "notDisclosedMetrics",
    "estimatedMetrics",
    "vendorUnavailableMetrics",
    "requiresIrScrapeMetrics",
    "requiresEstimatesMetrics",
    "dataConfidence",
    "dataConfidencePct",
    "disclosureMetrics",
    "manualAffo",
    "manualAffoGrowth",
    "manualOccupancy",
    "manualCet1Ratio",
    "manualNim",
    "manualCreditLossRatio",
    "manualCashRunwayMonths",
    "manualPatentCliffRisk",
    "manualPipelineRisk",
    "manualBacklogGrowth",
    "manualBookToBill",
    "manualNarrativeNotes",
    "data_quality_notes",
]


MANUAL_OVERRIDE_COLUMNS = [
    ("model_type", "modelType", "TEXT"),
    ("manual_arr_growth", "manualArrGrowth", "REAL"),
    ("manual_rpo_growth", "manualRpoGrowth", "REAL"),
    ("manual_net_retention", "manualNetRetention", "REAL"),
    ("manual_subscription_revenue_growth", "manualSubscriptionRevenueGrowth", "REAL"),
    ("manual_non_gaap_operating_margin", "manualNonGaapOperatingMargin", "REAL"),
    ("manual_large_customer_growth", "manualLargeCustomerGrowth", "REAL"),
    ("manual_sbc_ratio", "manualSbcRatio", "REAL"),
    ("manual_customer_concentration", "manualCustomerConcentration", "REAL"),
    ("manual_dilution_risk", "manualDilutionRisk", "REAL"),
    ("manual_inventory_risk", "manualInventoryRisk", "REAL"),
    ("manual_semiconductor_cycle_risk", "manualSemiconductorCycleRisk", "REAL"),
    ("manual_acquisition_integration_risk", "manualAcquisitionIntegrationRisk", "REAL"),
    ("manual_ai_disruption_risk", "manualAiDisruptionRisk", "REAL"),
    ("manual_adjusted_ebitda", "manualAdjustedEbitda", "REAL"),
    ("manual_adjusted_ebitda_growth", "manualAdjustedEbitdaGrowth", "REAL"),
    ("manual_adjusted_fcf_before_growth", "manualAdjustedFcfBeforeGrowth", "REAL"),
    ("manual_net_debt_to_adjusted_ebitda", "manualNetDebtToAdjustedEbitda", "REAL"),
    ("manual_hedge_coverage_current_year", "manualHedgeCoverageCurrentYear", "REAL"),
    ("manual_hedge_coverage_next_year", "manualHedgeCoverageNextYear", "REAL"),
    ("manual_buyback_amount", "manualBuybackAmount", "REAL"),
    ("manual_share_count_reduction", "manualShareCountReduction", "REAL"),
    ("manual_affo", "manualAffo", "REAL"),
    ("manual_affo_growth", "manualAffoGrowth", "REAL"),
    ("manual_occupancy", "manualOccupancy", "REAL"),
    ("manual_cet1_ratio", "manualCet1Ratio", "REAL"),
    ("manual_nim", "manualNim", "REAL"),
    ("manual_credit_loss_ratio", "manualCreditLossRatio", "REAL"),
    ("manual_cash_runway_months", "manualCashRunwayMonths", "REAL"),
    ("manual_patent_cliff_risk", "manualPatentCliffRisk", "REAL"),
    ("manual_pipeline_risk", "manualPipelineRisk", "REAL"),
    ("manual_backlog_growth", "manualBacklogGrowth", "REAL"),
    ("manual_book_to_bill", "manualBookToBill", "REAL"),
    ("manual_narrative_notes", "manualNarrativeNotes", "TEXT"),
]


class FundamentalCache:
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
                CREATE TABLE IF NOT EXISTS quote_snapshots (
                    ticker TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_manual_overrides (
                    ticker TEXT PRIMARY KEY,
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
                )
                """
            )
            self._ensure_manual_override_columns(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO stock_manual_overrides (
                    ticker,
                    model_type,
                    updated_at
                )
                VALUES ('VST', 'POWER_GENERATION', ?)
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )

    def _ensure_manual_override_columns(self, conn: sqlite3.Connection) -> None:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(stock_manual_overrides)").fetchall()
        }
        for column_name, _, sql_type in MANUAL_OVERRIDE_COLUMNS:
            if column_name not in existing:
                conn.execute(f"ALTER TABLE stock_manual_overrides ADD COLUMN {column_name} {sql_type}")

    def get_snapshot(self, ticker: str, max_age_hours: float = 12) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json, fetched_at FROM quote_snapshots WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()

        if not row:
            return None

        payload_json, fetched_at = row
        if not _is_fresh(fetched_at, max_age_hours):
            return None
        return json.loads(payload_json)

    def set_snapshot(self, ticker: str, snapshot: dict) -> None:
        fetched_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO quote_snapshots (ticker, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (ticker.upper(), json.dumps(snapshot, default=str), fetched_at),
            )

    def get_snapshot_fetched_at(self, ticker: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT fetched_at FROM quote_snapshots WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()
        return row[0] if row else None

    def get_manual_overrides(self, ticker: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM stock_manual_overrides WHERE ticker = ?",
                (ticker.upper(),),
            )
            record = row.fetchone()
            columns = [description[0] for description in row.description] if row.description else []
        if not record:
            return {}

        column_map = {db_name: camel_name for db_name, camel_name, _ in MANUAL_OVERRIDE_COLUMNS}
        values = {}
        for column, value in zip(columns, record):
            if column in {"ticker", "updated_at"} or value is None:
                continue
            values[column_map.get(column, column)] = value
        return values

    def get_manual_power_overrides(self, ticker: str) -> dict:
        return self.get_manual_overrides(ticker)

    def set_manual_overrides(self, ticker: str, **overrides: object) -> None:
        db_columns_by_camel = {camel_name: db_name for db_name, camel_name, _ in MANUAL_OVERRIDE_COLUMNS}
        values = {
            db_columns_by_camel[key]: value
            for key, value in overrides.items()
            if key in db_columns_by_camel
        }
        updated_at = datetime.now(timezone.utc).isoformat()
        columns = ["ticker", *values.keys(), "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{column} = excluded.{column}" for column in values.keys())
        if assignments:
            assignments += ", "
        assignments += "updated_at = excluded.updated_at"

        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO stock_manual_overrides ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(ticker) DO UPDATE SET
                    {assignments}
                """,
                (ticker.upper(), *values.values(), updated_at),
            )

    def set_manual_power_overrides(
        self,
        ticker: str,
        manual_adjusted_ebitda: float | None = None,
        manual_adjusted_fcf_before_growth: float | None = None,
        manual_net_debt_to_adjusted_ebitda: float | None = None,
        manual_hedge_coverage_current_year: float | None = None,
        manual_hedge_coverage_next_year: float | None = None,
        manual_buyback_amount: float | None = None,
        manual_share_count_reduction: float | None = None,
        manual_narrative_notes: str | None = None,
    ) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO stock_manual_overrides (
                    ticker,
                    manual_adjusted_ebitda,
                    manual_adjusted_fcf_before_growth,
                    manual_net_debt_to_adjusted_ebitda,
                    manual_hedge_coverage_current_year,
                    manual_hedge_coverage_next_year,
                    manual_buyback_amount,
                    manual_share_count_reduction,
                    manual_narrative_notes,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    manual_adjusted_ebitda = excluded.manual_adjusted_ebitda,
                    manual_adjusted_fcf_before_growth = excluded.manual_adjusted_fcf_before_growth,
                    manual_net_debt_to_adjusted_ebitda = excluded.manual_net_debt_to_adjusted_ebitda,
                    manual_hedge_coverage_current_year = excluded.manual_hedge_coverage_current_year,
                    manual_hedge_coverage_next_year = excluded.manual_hedge_coverage_next_year,
                    manual_buyback_amount = excluded.manual_buyback_amount,
                    manual_share_count_reduction = excluded.manual_share_count_reduction,
                    manual_narrative_notes = excluded.manual_narrative_notes,
                    updated_at = excluded.updated_at
                """,
                (
                    ticker.upper(),
                    manual_adjusted_ebitda,
                    manual_adjusted_fcf_before_growth,
                    manual_net_debt_to_adjusted_ebitda,
                    manual_hedge_coverage_current_year,
                    manual_hedge_coverage_next_year,
                    manual_buyback_amount,
                    manual_share_count_reduction,
                    manual_narrative_notes,
                    updated_at,
                ),
            )


def get_fundamentals(
    ticker: str,
    force_refresh: bool = False,
    cache: FundamentalCache | None = None,
) -> dict:
    from data.providers import get_market_data_provider

    provider = get_market_data_provider()
    return provider.get_quote(ticker, force_refresh=force_refresh)


def missing_fundamental_fields(snapshot: dict) -> list[str]:
    fields = [
        "free_cash_flow",
        "total_revenue",
        "net_income",
        "revenue_growth",
        "forward_revenue_growth",
        "earnings_growth",
        "operating_margin",
        "profit_margin",
        "debt_to_equity",
        "free_cash_flow_yield",
        "return_on_invested_capital",
    ]
    return [field for field in fields if snapshot.get(field) is None]


def _is_fresh(fetched_at: str, max_age_hours: float) -> bool:
    fetched_dt = datetime.fromisoformat(fetched_at)
    if fetched_dt.tzinfo is None:
        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_dt <= timedelta(hours=max_age_hours)
