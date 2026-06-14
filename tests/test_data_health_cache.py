from __future__ import annotations

import json
import sqlite3
import unittest
import inspect
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data.cache_read_model import CacheReadModel
from data.data_health import build_data_health_summary
import data.data_health as data_health_module
from data.decision_log import DecisionLogStore, DecisionOutcomeStore
from data.portfolio import PortfolioPositionStore


class DataHealthCacheTests(unittest.TestCase):
    def _insert_price_history(
        self,
        db_path: Path,
        symbol: str,
        closes: list[tuple[str, float]],
        fetched_at: str = "2026-05-26T00:00:00+00:00",
    ) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_history (
                    ticker TEXT,
                    date TEXT,
                    close REAL,
                    fetched_at TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO price_history VALUES (?, ?, ?, ?)",
                [(symbol.upper(), day, close, fetched_at) for day, close in closes],
            )
            conn.commit()

    def _insert_quote_snapshot(self, db_path: Path, symbol: str, payload: dict, fetched_at: str = "2026-05-26T00:00:00+00:00") -> None:
        with closing(sqlite3.connect(db_path)) as conn:
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
                "INSERT OR REPLACE INTO quote_snapshots VALUES (?, ?, ?)",
                (symbol.upper(), json.dumps(payload), fetched_at),
            )
            conn.commit()

    def test_data_health_summary_reports_missing_cache(self) -> None:
        with TemporaryDirectory() as tmpdir:
            summary = build_data_health_summary(Path(tmpdir) / "missing.sqlite", watchlist=["NOW"])

            self.assertFalse(summary["cacheExists"])
            self.assertEqual(summary["healthyCount"], 0)
            self.assertEqual(summary["topIssues"][0]["category"], "cache_missing")
            self.assertEqual(summary["decisionBlockedCount"], 1)
            self.assertFalse(summary["decisionReadiness"]["NOW"]["canDecide"])

    def test_cache_read_model_prefers_quote_price_and_reports_stale_quote(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            self._insert_quote_snapshot(
                db_path,
                "NOW",
                {"currentPrice": 130, "ticker": "NOW"},
                "2026-05-24T00:00:00+00:00",
            )
            self._insert_price_history(db_path, "NOW", [("2026-05-25", 120)])

            cache = CacheReadModel(
                db_path,
                now=datetime(2026, 5, 26, tzinfo=timezone.utc),
                quote_max_age_hours=24,
            )

            self.assertEqual(cache.get_quote_payload("now")["ticker"], "NOW")
            self.assertEqual(cache.get_current_price("now"), 130)
            self.assertEqual(cache.get_latest_close("now"), 120)
            self.assertEqual(cache.get_price_status("now"), "stale_quote")
            self.assertEqual(cache.get_history_status("now"), "available")

    def test_cache_read_model_falls_back_to_latest_close_and_missing_statuses(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            self._insert_price_history(
                db_path,
                "CRM",
                [("2026-05-24", 190), ("2026-05-25", 200)],
                "2026-05-22T00:00:00+00:00",
            )

            cache = CacheReadModel(db_path, now=datetime(2026, 5, 26, tzinfo=timezone.utc), history_max_age_hours=72)

            self.assertEqual(cache.get_current_price("crm"), 200)
            self.assertEqual(cache.get_price_status("crm"), "price_history")
            self.assertEqual(cache.get_history_status("crm"), "stale_history")
            self.assertIsNone(cache.get_current_price("hood"))
            self.assertEqual(cache.get_price_status("hood"), "missing")
            self.assertEqual(cache.get_history_status("hood"), "missing")

    def test_cache_read_model_reads_fmp_history_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            self._insert_price_history(
                db_path,
                "FMP:NOW",
                [("2026-05-24", 120), ("2026-05-25", 125)],
                "2026-05-26T00:00:00+00:00",
            )

            cache = CacheReadModel(db_path, now=datetime(2026, 5, 26, tzinfo=timezone.utc), history_max_age_hours=72)

            self.assertEqual(cache.get_latest_close("now"), 125)
            self.assertEqual(cache.get_history_status("now"), "available")
            self.assertEqual(cache.get_price_history("now")["close"].tolist(), [120, 125])

    def test_cache_read_model_uses_newest_history_key_when_plain_and_fmp_exist(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            self._insert_price_history(
                db_path,
                "MSFT",
                [("2026-05-20", 410), ("2026-05-21", 412)],
                "2026-05-21T00:00:00+00:00",
            )
            self._insert_price_history(
                db_path,
                "FMP:MSFT",
                [("2026-05-24", 425), ("2026-05-25", 430)],
                "2026-05-26T00:00:00+00:00",
            )

            cache = CacheReadModel(db_path, now=datetime(2026, 5, 26, tzinfo=timezone.utc), history_max_age_hours=72)

            self.assertEqual(cache.get_latest_close("msft"), 430)
            self.assertEqual(cache.get_history_status("msft"), "available")
            self.assertEqual(cache.get_price_history("msft")["close"].tolist(), [425, 430])

    def test_data_health_summary_uses_fresh_fmp_history_before_reporting_stale_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            now = datetime(2026, 5, 26, tzinfo=timezone.utc)
            self._insert_price_history(
                db_path,
                "NVDA",
                [("2026-05-20", 190), ("2026-05-21", 192)],
                "2026-05-21T00:00:00+00:00",
            )
            self._insert_price_history(
                db_path,
                "FMP:NVDA",
                [("2026-05-24", 205), ("2026-05-25", 210)],
                "2026-05-26T00:00:00+00:00",
            )

            summary = build_data_health_summary(
                db_path,
                watchlist=["NVDA"],
                now=now,
                history_max_age_hours=72,
            )

            self.assertEqual(summary["staleHistoryCount"], 0)
            self.assertNotIn("stale_history", {item["category"] for item in summary["topIssues"]})

    def test_data_health_final_decision_inputs_prefer_market_context_price(self) -> None:
        source = inspect.getsource(data_health_module._build_final_decision_inputs)

        self.assertIn("current_price,", source)
        self.assertIn('payload["current_price"] = cached_price', source)
        self.assertIn('"daily_ohlcv": history', source)
        self.assertIn("build_buy_zone_context(stock_data", source)
        self.assertNotIn('setdefault("current_price"', source)

    def test_data_health_summary_counts_watchlist_price_history_and_decision_errors(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            now = datetime(2026, 5, 26, tzinfo=timezone.utc)
            self._insert_quote_snapshot(
                db_path,
                "NOW",
                {
                    "ticker": "NOW",
                    "current_price": 100,
                    "sector": "Technology",
                    "industry": "Software - Application",
                    "revenue_growth": 0.12,
                    "gross_margin": 0.75,
                    "operating_margin": 0.20,
                    "return_on_invested_capital": 0.12,
                    "free_cash_flow": 1_000,
                    "total_revenue": 10_000,
                    "price_to_sales": 8,
                    "price_to_fcf": 25,
                    "forward_pe": 30,
                    "total_debt": 100,
                    "total_cash": 300,
                },
                "2026-05-26T00:00:00+00:00",
            )
            self._insert_price_history(db_path, "NOW", [(f"2026-05-{day:02d}", 90 + day) for day in range(1, 27)])
            self._insert_quote_snapshot(db_path, "CRM", {"ticker": "CRM"}, "2026-05-24T00:00:00+00:00")

            summary = build_data_health_summary(db_path, watchlist=["NOW", "CRM"], now=now, quote_max_age_hours=24)

            self.assertTrue(summary["cacheExists"])
            self.assertEqual(summary["healthyCount"], 1)
            self.assertEqual(summary["stalePriceCount"], 1)
            self.assertEqual(summary["missingPriceCount"], 1)
            self.assertEqual(summary["missingHistoryCount"], 1)
            self.assertEqual(summary["finalDecisionErrorCount"], 1)
            self.assertEqual(summary["decisionBlockedCount"], 1)
            self.assertFalse(summary["decisionReadiness"]["CRM"]["canDecide"])
            self.assertFalse(summary["decisionReadiness"]["CRM"]["canShowPreciseBuyZone"])
            self.assertIn("NOW", summary["decisionReadiness"])
            categories = {item["category"] for item in summary["topIssues"]}
            self.assertIn("missing_price", categories)
            self.assertIn("stale_quote", categories)
            self.assertIn("missing_history", categories)

    def test_data_health_summary_counts_stale_price_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            now = datetime(2026, 5, 26, tzinfo=timezone.utc)
            self._insert_quote_snapshot(db_path, "NOW", {"ticker": "NOW", "current_price": 100}, "2026-05-26T00:00:00+00:00")
            self._insert_price_history(
                db_path,
                "NOW",
                [("2026-05-20", 90), ("2026-05-21", 91)],
                "2026-05-21T00:00:00+00:00",
            )

            summary = build_data_health_summary(
                db_path,
                watchlist=["NOW"],
                now=now,
                history_max_age_hours=72,
            )

            self.assertEqual(summary["staleHistoryCount"], 1)
            self.assertIn("stale_history", {item["category"] for item in summary["topIssues"]})

    def test_data_health_final_decision_uses_quote_when_history_is_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            now = datetime(2026, 5, 26, tzinfo=timezone.utc)
            self._insert_quote_snapshot(
                db_path,
                "ANET",
                {
                    "ticker": "ANET",
                    "current_price": 159.28,
                    "modelType": "NETWORKING_HARDWARE",
                    "price_to_fcf": 38,
                    "enterprise_to_revenue": 20.4,
                    "price_to_sales": 20.7,
                    "revenue_growth": 0.28,
                    "gross_margin": 0.63,
                    "operating_margin": 0.42,
                    "fcf_margin": 0.60,
                },
                "2026-05-26T00:00:00+00:00",
            )

            summary = build_data_health_summary(db_path, watchlist=["ANET"], now=now)

            self.assertEqual(summary["missingHistoryCount"], 1)
            self.assertEqual(summary["finalDecisionErrorCount"], 0)

    def test_data_health_summary_counts_portfolio_missing_price_and_outcome_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            PortfolioPositionStore(db_path).save_position("ADBE", {"quantity": 2, "average_cost": 300})
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add"},
            )
            DecisionOutcomeStore(db_path).save_outcome(snapshot["id"], "1d", {"status": "missing"})

            summary = build_data_health_summary(db_path, watchlist=[], now=datetime(2026, 5, 26, tzinfo=timezone.utc))

            self.assertEqual(summary["portfolioMissingPriceCount"], 1)
            self.assertEqual(summary["outcomeMissingCount"], 1)
            categories = {item["category"] for item in summary["topIssues"]}
            self.assertIn("portfolio_missing_price", categories)
            self.assertIn("outcome_missing", categories)
