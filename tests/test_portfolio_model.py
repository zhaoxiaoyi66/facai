from __future__ import annotations

import inspect
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from data.portfolio import (
    PortfolioPositionStore,
    PortfolioSettingsStore,
    calculate_portfolio_position,
    calculate_portfolio_positions,
    format_position_tier_label,
)
from data.portfolio_view_model import build_portfolio_view_model
import data.portfolio_view_model as portfolio_view_model_module
from scoring.final_decision import BUY_ACTIONS


class PortfolioModelTests(unittest.TestCase):
    def test_portfolio_position_store_crud_and_active_filter(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            store = PortfolioPositionStore(db_path)

            created = store.save_position(
                "now",
                {
                    "quantity": "10",
                    "average_cost": "500",
                    "target_position_pct": "8",
                    "max_acceptable_position_pct": "12",
                    "planned_sell_price": "720",
                    "first_trim_price": "680",
                    "second_trim_price": "760",
                    "review_price": "450",
                    "notes": "core position",
                },
            )
            self.assertEqual(created["symbol"], "NOW")
            self.assertEqual(created["quantity"], 10)
            self.assertEqual(created["average_cost"], 500)
            self.assertTrue(created["is_active"])

            updated = store.save_position("NOW", {"quantity": 12, "average_cost": 480})
            self.assertEqual(updated["quantity"], 12)
            self.assertEqual(updated["average_cost"], 480)
            self.assertEqual(store.list_active_positions()[0]["symbol"], "NOW")

            inactive = store.deactivate_position("now")
            self.assertIsNotNone(inactive)
            self.assertFalse(inactive["is_active"])
            self.assertEqual(store.list_active_positions(), [])

    def test_portfolio_position_store_rejects_negative_quantity_and_cost(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = PortfolioPositionStore(Path(tmpdir) / "portfolio.sqlite")

            with self.assertRaises(ValueError):
                store.save_position("NOW", {"quantity": -1, "average_cost": 500})
            with self.assertRaises(ValueError):
                store.save_position("NOW", {"quantity": 1, "average_cost": -500})

    def test_portfolio_position_tier_saves_only_manual_abc_values(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = PortfolioPositionStore(Path(tmpdir) / "portfolio.sqlite")

            created = store.save_position("NVDA", {"quantity": 2, "average_cost": 100, "position_tier": "a"})

            self.assertEqual(created["position_tier"], "A")
            self.assertEqual(format_position_tier_label(created["position_tier"]), "A类")

            with self.assertRaises(ValueError):
                store.save_position("NVDA", {"quantity": 2, "average_cost": 100, "position_tier": "UNCLASSIFIED"})

    def test_portfolio_position_missing_tier_is_legacy_safe_prompt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = PortfolioPositionStore(Path(tmpdir) / "portfolio.sqlite")

            created = store.save_position("LEGACY", {"quantity": 1, "average_cost": 50})

            self.assertIsNone(created.get("position_tier"))
            self.assertEqual(format_position_tier_label(created.get("position_tier")), "需设置等级")

    def test_portfolio_settings_store_saves_and_loads_defaults(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = PortfolioSettingsStore(Path(tmpdir) / "portfolio.sqlite")

            self.assertEqual(store.get_settings()["base_currency"], "USD")
            saved = store.save_settings(
                {
                    "total_portfolio_value": "100000",
                    "cash_balance": "12000",
                    "base_currency": "usd",
                }
            )

            self.assertEqual(saved["total_portfolio_value"], 100000)
            self.assertEqual(saved["cash_balance"], 12000)
            self.assertEqual(saved["base_currency"], "USD")
            self.assertIsNotNone(saved["updated_at"])

    def test_portfolio_position_calculator_values_and_total_value_pct(self) -> None:
        calculated = calculate_portfolio_position(
            {"symbol": "now", "quantity": 10, "average_cost": 100},
            125,
            5000,
        )

        self.assertEqual(calculated["symbol"], "NOW")
        self.assertEqual(calculated["marketValue"], 1250)
        self.assertEqual(calculated["costBasis"], 1000)
        self.assertEqual(calculated["unrealizedPnl"], 250)
        self.assertEqual(calculated["unrealizedPnlPct"], 25)
        self.assertEqual(calculated["positionPct"], 25)

    def test_position_tier_does_not_change_portfolio_calculation(self) -> None:
        calculated = calculate_portfolio_position(
            {"symbol": "now", "quantity": 10, "average_cost": 100, "position_tier": "B"},
            125,
            5000,
        )

        self.assertEqual(calculated["marketValue"], 1250)
        self.assertEqual(calculated["costBasis"], 1000)
        self.assertEqual(calculated["positionPct"], 25)
        self.assertEqual(calculated["position_tier"], "B")

    def test_position_tier_badge_renders_table_label(self) -> None:
        from ui.portfolio import _position_tier_badge_html

        self.assertIn("B类", _position_tier_badge_html("B"))
        self.assertIn("需设置等级", _position_tier_badge_html(None))


    def test_portfolio_positions_calculator_uses_settings_total_value(self) -> None:
        positions = [{"symbol": "NOW", "quantity": 10, "average_cost": 100}]

        calculated = calculate_portfolio_positions(
            positions,
            {"NOW": 125},
            settings={"total_portfolio_value": 10000},
        )

        self.assertEqual(calculated[0]["positionPct"], 12.5)

    def test_portfolio_positions_calculator_falls_back_to_market_value_total(self) -> None:
        positions = [
            {"symbol": "NOW", "quantity": 10, "average_cost": 100},
            {"symbol": "CRM", "quantity": 5, "average_cost": 100},
        ]

        calculated = calculate_portfolio_positions(
            positions,
            {"NOW": 100, "CRM": 200},
            settings={},
        )

        by_symbol = {row["symbol"]: row for row in calculated}
        self.assertEqual(by_symbol["NOW"]["positionPct"], 50)
        self.assertEqual(by_symbol["CRM"]["positionPct"], 50)

    def test_portfolio_position_calculator_flags_overweight_limits(self) -> None:
        calculated = calculate_portfolio_position(
            {
                "symbol": "NOW",
                "quantity": 10,
                "average_cost": 100,
                "max_acceptable_position_pct": 15,
            },
            200,
            10000,
            {"systemMaxPosition": 10},
        )

        self.assertEqual(calculated["positionPct"], 20)
        self.assertTrue(calculated["overweightSystem"])
        self.assertTrue(calculated["overweightPersonal"])

    def test_portfolio_position_calculator_flags_near_trim_and_review(self) -> None:
        near_trim = calculate_portfolio_position(
            {
                "symbol": "NOW",
                "quantity": 10,
                "average_cost": 100,
                "first_trim_price": 200,
                "review_price": 90,
            },
            191,
            5000,
        )
        review_price = calculate_portfolio_position(
            {"symbol": "CRM", "quantity": 10, "average_cost": 100, "review_price": 90},
            89,
            5000,
        )
        system_review = calculate_portfolio_position(
            {"symbol": "ADBE", "quantity": 10, "average_cost": 100},
            120,
            5000,
            {"systemStatus": "blocked"},
        )

        self.assertTrue(near_trim["nearTrimPrice"])
        self.assertFalse(near_trim["needsReview"])
        self.assertTrue(review_price["needsReview"])
        self.assertTrue(system_review["needsReview"])

    def test_portfolio_position_calculator_missing_price_does_not_crash(self) -> None:
        calculated = calculate_portfolio_position(
            {"symbol": "NOW", "quantity": 10, "average_cost": 100},
            None,
            5000,
        )

        self.assertTrue(calculated["missingPrice"])
        self.assertIsNone(calculated["marketValue"])
        self.assertEqual(calculated["costBasis"], 1000)
        self.assertIsNone(calculated["unrealizedPnl"])
        self.assertIsNone(calculated["positionPct"])

    def test_portfolio_view_model_handles_empty_positions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            view = build_portfolio_view_model(Path(tmpdir) / "portfolio.sqlite")

        self.assertEqual(view["summary"]["positionCount"], 0)
        self.assertEqual(view["summary"]["marketValue"], 0)
        self.assertEqual(view["summary"]["costBasis"], 0)
        self.assertEqual(view["summary"]["overweightCount"], 0)
        self.assertEqual(view["summary"]["needsReviewCount"], 0)
        self.assertEqual(view["rows"], [])

    def test_portfolio_view_model_summarizes_normal_holding(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioSettingsStore(db_path).save_settings({"total_portfolio_value": 10000, "cash_balance": 500})
            PortfolioPositionStore(db_path).save_position(
                "now",
                {
                    "quantity": 10,
                    "average_cost": 100,
                    "target_position_pct": 20,
                    "max_acceptable_position_pct": 25,
                },
            )

            view = build_portfolio_view_model(db_path, {"NOW": 120})

        self.assertEqual(view["summary"]["positionCount"], 1)
        self.assertEqual(view["summary"]["marketValue"], 1200)
        self.assertEqual(view["summary"]["costBasis"], 1000)
        self.assertEqual(view["summary"]["totalPortfolioValue"], 10000)
        self.assertEqual(view["summary"]["cashBalance"], 8800)
        self.assertEqual(view["summary"]["cashBalanceSource"], "derived")
        self.assertEqual(view["summary"]["unrealizedPnl"], 200)
        self.assertEqual(view["summary"]["unrealizedPnlPct"], 20)
        self.assertEqual(view["rows"][0]["positionPct"], 12)
        self.assertEqual(view["rows"][0]["actionGroup"], "addable")
        self.assertEqual(view["rows"][0]["priceStatus"], "provided")

    def test_portfolio_view_model_prefers_quote_snapshot_price(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioPositionStore(db_path).save_position("NOW", {"quantity": 10, "average_cost": 100})
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE quote_snapshots (
                        ticker TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        fetched_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE price_history (
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        close REAL,
                        fetched_at TEXT NOT NULL,
                        PRIMARY KEY (ticker, date)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO quote_snapshots VALUES (?, ?, ?)",
                    ("NOW", json.dumps({"current_price": 130}), "now"),
                )
                conn.execute(
                    "INSERT INTO price_history VALUES (?, ?, ?, ?)",
                    ("NOW", "2026-05-24", 120, "now"),
                )
                conn.commit()

            view = build_portfolio_view_model(db_path)

        self.assertEqual(view["rows"][0]["currentPrice"], 130)
        self.assertEqual(view["rows"][0]["marketValue"], 1300)
        self.assertEqual(view["rows"][0]["priceStatus"], "quote_snapshot")

    def test_portfolio_view_model_falls_back_to_latest_history_close(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioPositionStore(db_path).save_position("CRM", {"quantity": 5, "average_cost": 100})
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE price_history (
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        close REAL,
                        fetched_at TEXT NOT NULL,
                        PRIMARY KEY (ticker, date)
                    )
                    """
                )
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?, ?)", ("CRM", "2026-05-23", 190, "now"))
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?, ?)", ("CRM", "2026-05-24", 200, "now"))
                conn.commit()

            view = build_portfolio_view_model(db_path)

        self.assertEqual(view["rows"][0]["currentPrice"], 200)
        self.assertEqual(view["rows"][0]["marketValue"], 1000)
        self.assertEqual(view["rows"][0]["priceStatus"], "price_history")

    def test_portfolio_view_model_uses_market_context_history_key_selection(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioPositionStore(db_path).save_position("CRWV", {"quantity": 5, "average_cost": 100})
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE price_history (
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        close REAL,
                        fetched_at TEXT NOT NULL,
                        PRIMARY KEY (ticker, date)
                    )
                    """
                )
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?, ?)", ("CRWV", "2026-05-27", 60, "2026-05-28T10:00:00+00:00"))
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?, ?)", ("FMP:CRWV", "2026-05-29", 70, "2026-05-30T10:00:00+00:00"))
                conn.commit()

            view = build_portfolio_view_model(db_path)

        self.assertEqual(view["rows"][0]["currentPrice"], 70)
        self.assertEqual(view["rows"][0]["marketValue"], 350)
        self.assertEqual(view["rows"][0]["priceStatus"], "price_history")

    def test_portfolio_system_reference_uses_market_context_history(self) -> None:
        source = inspect.getsource(portfolio_view_model_module._system_ref_from_local_cache)

        self.assertIn("build_market_history", source)
        self.assertNotIn("get_price_history", source)
        self.assertIn('snapshot["current_price"] = current_price', source)
        self.assertNotIn('setdefault("current_price"', source)

    def test_portfolio_view_model_flags_overweight(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioSettingsStore(db_path).save_settings({"total_portfolio_value": 10000})
            PortfolioPositionStore(db_path).save_position(
                "CRM",
                {
                    "quantity": 10,
                    "average_cost": 100,
                    "max_acceptable_position_pct": 15,
                },
            )

            view = build_portfolio_view_model(db_path, {"CRM": 200})

        self.assertEqual(view["summary"]["overweightCount"], 1)
        self.assertEqual(view["rows"][0]["actionGroup"], "overweight")
        self.assertIn("overweight_personal", view["rows"][0]["deviationWarnings"])
        groups = {group["key"]: group for group in view["actionGroups"]}
        self.assertEqual(groups["overweight"]["symbols"], ["CRM"])

    def test_portfolio_view_model_outputs_final_decision_system_reference(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioSettingsStore(db_path).save_settings({"total_portfolio_value": 10000})
            PortfolioPositionStore(db_path).save_position(
                "NOW",
                {"quantity": 10, "average_cost": 100, "target_position_pct": 20},
            )
            score = SimpleNamespace(
                action=sorted(BUY_ACTIONS)[0],
                valuationStatus="fair",
                entryRating="A",
                riskRating="low",
                dataConfidence="high",
                currentAddLimitPercent=10,
                maxPortfolioWeightPercent=15,
            )
            zone = SimpleNamespace(currentZone="tranche_buy")
            plan = SimpleNamespace(currentAddLimitPercent=6, maxPortfolioWeightPercent=20)

            view = build_portfolio_view_model(
                db_path,
                {"NOW": 120},
                {"NOW": {"score": score, "buy_zone": zone, "position_plan": plan}},
            )

        row = view["rows"][0]
        self.assertEqual(row["systemAction"], sorted(BUY_ACTIONS)[0])
        self.assertEqual(row["systemMaxPosition"], 20)
        self.assertEqual(row["systemCurrentAdd"], 6)
        self.assertEqual(row["buyZoneStatus"], "tranche_buy")
        self.assertEqual(row["decisionLane"], "actionable")
        self.assertEqual(row["blockReasons"], [])
        self.assertEqual(row["reviewReasons"], [])
        self.assertEqual(row["executionSource"], "finalDecisionBundle")
        self.assertEqual(row["finalDecision"]["finalAction"], row["systemAction"])
        self.assertEqual(row["finalDecision"]["currentAddLimitPercent"], row["systemCurrentAdd"])

    def test_portfolio_view_model_flags_system_overweight(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioSettingsStore(db_path).save_settings({"total_portfolio_value": 10000})
            PortfolioPositionStore(db_path).save_position("VST", {"quantity": 10, "average_cost": 100})
            score = SimpleNamespace(
                action=sorted(BUY_ACTIONS)[0],
                valuationStatus="fair",
                entryRating="A",
                riskRating="low",
                dataConfidence="high",
                currentAddLimitPercent=5,
                maxPortfolioWeightPercent=10,
            )

            view = build_portfolio_view_model(db_path, {"VST": 200}, {"VST": {"score": score}})

        self.assertTrue(view["rows"][0]["overweightSystem"])
        self.assertIn("overweight_system", view["rows"][0]["deviationWarnings"])
        self.assertEqual(view["summary"]["overweightCount"], 1)

    def test_portfolio_view_model_flags_held_position_when_system_not_addable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioPositionStore(db_path).save_position("HOOD", {"quantity": 10, "average_cost": 20})
            score = SimpleNamespace(
                action=sorted(BUY_ACTIONS)[0],
                valuationStatus="fair",
                entryRating="A",
                riskRating="low",
                dataConfidence="high",
                currentAddLimitPercent=5,
                maxPortfolioWeightPercent=15,
            )
            zone = SimpleNamespace(currentZone="no_chase")
            plan = SimpleNamespace(currentAddLimitPercent=5, maxPortfolioWeightPercent=15)

            view = build_portfolio_view_model(
                db_path,
                {"HOOD": 30},
                {"HOOD": {"score": score, "buy_zone": zone, "position_plan": plan}},
            )

        row = view["rows"][0]
        self.assertEqual(row["decisionLane"], "blocked")
        self.assertEqual(row["systemCurrentAdd"], 0)
        self.assertEqual(row["finalDecision"]["legacyAction"], sorted(BUY_ACTIONS)[0])
        self.assertEqual(row["finalDecision"]["currentAddLimitPercent"], 0)
        self.assertIn("buy_zone", row["blockReasons"])
        self.assertIn("system_not_addable", row["deviationWarnings"])

    def test_portfolio_view_model_flags_near_trim_price(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioPositionStore(db_path).save_position(
                "ADBE",
                {
                    "quantity": 5,
                    "average_cost": 100,
                    "first_trim_price": 200,
                },
            )

            view = build_portfolio_view_model(db_path, {"ADBE": 191})

        self.assertTrue(view["rows"][0]["nearTrimPrice"])
        self.assertIn("near_trim_price", view["rows"][0]["deviationWarnings"])
        self.assertEqual(view["rows"][0]["actionGroup"], "nearTrim")
        groups = {group["key"]: group for group in view["actionGroups"]}
        self.assertEqual(groups["nearTrim"]["symbols"], ["ADBE"])

    def test_portfolio_view_model_flags_missing_price_as_review(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            PortfolioPositionStore(db_path).save_position(
                "HOOD",
                {"quantity": 10, "average_cost": 20},
            )

            view = build_portfolio_view_model(db_path, {})

        self.assertEqual(view["summary"]["needsReviewCount"], 1)
        self.assertTrue(view["rows"][0]["missingPrice"])
        self.assertEqual(view["rows"][0]["priceStatus"], "missing")
        self.assertEqual(view["rows"][0]["marketValue"], None)
        self.assertEqual(view["rows"][0]["actionGroup"], "review")

