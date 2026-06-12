from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from data.decision_log import (
    DECISION_ERROR_TAGS,
    DecisionErrorTagStore,
    DecisionLogStore,
    DecisionOutcomeStore,
    TradeJournalStore,
    build_decision_outcomes_from_price_history,
    build_decision_signal_stats,
    build_decision_snapshot_from_bundle,
    refresh_decision_outcomes,
    save_decision_snapshot_from_bundle,
)
from scoring.final_decision import BUY_ACTIONS
from scoring.final_decision_adapter import build_final_decision_bundle


class DecisionLogTests(unittest.TestCase):
    def _insert_price_history(
        self,
        db_path: Path,
        symbol: str,
        closes: list[tuple[str, float]],
        fetched_at: str = "now",
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


    def test_decision_snapshot_helper_builds_from_score_only_bundle(self) -> None:
        score = SimpleNamespace(
            action=sorted(BUY_ACTIONS)[0],
            valuationStatus="fair",
            entryRating="A",
            riskRating="low",
            dataConfidence="high",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )
        bundle = build_final_decision_bundle(score)

        snapshot = build_decision_snapshot_from_bundle("now", 520, bundle, "dashboard")

        self.assertEqual(snapshot["symbol"], "NOW")
        self.assertEqual(snapshot["price"], 520)
        self.assertEqual(snapshot["final_action"], bundle.finalAction)
        self.assertEqual(snapshot["decision_lane"], bundle.decisionLane)
        self.assertEqual(snapshot["current_add_pct"], 5)
        self.assertEqual(snapshot["max_position_pct"], 15)
        self.assertEqual(snapshot["data_confidence"], "high")
        self.assertEqual(snapshot["buy_zone_status"], bundle.displayCategory)
        self.assertEqual(snapshot["source_page"], "dashboard")

    def test_decision_snapshot_helper_builds_from_buy_zone_bundle(self) -> None:
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
        bundle = build_final_decision_bundle(score, zone, plan)

        snapshot = build_decision_snapshot_from_bundle("crm", 260, bundle, "buy_zone")

        self.assertEqual(snapshot["symbol"], "CRM")
        self.assertEqual(snapshot["current_add_pct"], 6)
        self.assertEqual(snapshot["max_position_pct"], 20)
        self.assertEqual(snapshot["buy_zone_status"], bundle.displayCategory)
        self.assertEqual(snapshot["source_page"], "buy_zone")

    def test_decision_snapshot_helper_serializes_block_and_review_reasons(self) -> None:
        bundle = SimpleNamespace(
            finalAction="wait",
            decisionLane="review",
            displayCategory="需复核",
            currentAddLimitPercent=0,
            maxPortfolioWeightPercent=8,
            blockReasons=["buy_zone"],
            reviewReasons=["risk_rating", "data_confidence"],
            dataConfidence="low",
            riskRating="high",
            buyZoneStatus="no_chase",
        )

        snapshot = build_decision_snapshot_from_bundle("hood", 80, bundle, "stock_detail")

        self.assertEqual(json.loads(snapshot["block_reasons_json"]), ["buy_zone"])
        self.assertEqual(json.loads(snapshot["review_reasons_json"]), ["risk_rating", "data_confidence"])
        self.assertEqual(snapshot["risk_rating"], "high")
        self.assertEqual(snapshot["buy_zone_status"], "no_chase")
        self.assertIn("buy_zone", snapshot["reason_text"])
        self.assertEqual(snapshot["source_page"], "stock_detail")

    def test_decision_snapshot_helper_output_can_be_saved_and_queried(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DecisionLogStore(Path(tmpdir) / "decision_log.sqlite")
            score = SimpleNamespace(
                action=sorted(BUY_ACTIONS)[0],
                valuationStatus="fair",
                entryRating="A",
                riskRating="low",
                dataConfidence="high",
                currentAddLimitPercent=5,
                maxPortfolioWeightPercent=15,
            )
            snapshot = build_decision_snapshot_from_bundle(
                "adbe",
                310,
                build_final_decision_bundle(score),
                "dashboard",
            )

            saved = store.save_snapshot(snapshot["symbol"], snapshot)

            loaded = store.list_snapshots("adbe")[0]
            self.assertEqual(saved["id"], loaded["id"])
            self.assertEqual(loaded["symbol"], "ADBE")
            self.assertEqual(loaded["source_page"], "dashboard")
            self.assertEqual(loaded["price"], 310)

    def test_decision_log_store_saves_and_lists_snapshots_by_symbol(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DecisionLogStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_snapshot(
                "now",
                {
                    "decision_date": "2026-05-26",
                    "price": "520",
                    "final_action": "wait",
                    "decision_lane": "blocked",
                    "current_add_pct": "0",
                    "max_position_pct": "8",
                    "risk_rating": "medium",
                    "data_confidence": "high",
                    "buy_zone_status": "no_chase",
                    "block_reasons": ["no_chase"],
                    "review_reasons": [],
                    "reason_text": "price above no chase zone",
                    "source_page": "dashboard",
                },
            )

            self.assertEqual(saved["symbol"], "NOW")
            self.assertEqual(saved["price"], 520)
            self.assertEqual(saved["current_add_pct"], 0)
            self.assertEqual(saved["block_reasons"], ["no_chase"])
            self.assertEqual(store.list_snapshots("now")[0]["id"], saved["id"])
            self.assertEqual(store.list_snapshots("CRM"), [])

    def test_save_decision_snapshot_from_bundle_uses_existing_builder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            saved = save_decision_snapshot_from_bundle(
                "now",
                520,
                {
                    "finalAction": "可小仓分批",
                    "decisionLane": "actionable",
                    "currentAddLimitPercent": 3,
                    "maxPortfolioWeightPercent": 10,
                    "dataConfidence": "high",
                    "displayCategory": "可执行",
                    "blockReasons": [],
                    "reviewReasons": [],
                },
                "dashboard",
                db_path,
            )

            self.assertEqual(saved["symbol"], "NOW")
            self.assertEqual(saved["price"], 520)
            self.assertEqual(saved["final_action"], "可小仓分批")
            self.assertEqual(saved["decision_lane"], "actionable")

    def test_trade_journal_store_saves_entries_with_snapshot_link(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "crm",
                {
                    "decision_date": "2026-05-26",
                    "price": 260,
                    "final_action": "add",
                    "decision_lane": "actionable",
                },
            )
            store = TradeJournalStore(db_path)

            saved = store.save_entry(
                "crm",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "quantity": "5",
                    "price": "260",
                    "decision_snapshot_id": snapshot["id"],
                    "notes": "followed signal",
                },
            )

            self.assertEqual(saved["symbol"], "CRM")
            self.assertEqual(saved["action_type"], "buy")
            self.assertEqual(saved["quantity"], 5)
            self.assertEqual(saved["decision_snapshot_id"], snapshot["id"])
            self.assertEqual(store.list_entries("crm")[0]["notes"], "followed signal")

    def test_trade_journal_store_saves_optional_decision_mood(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "quantity": 1,
                    "price": 420,
                    "decision_mood": "plan_execution",
                },
            )
            legacy = store.save_entry("msft", {"trade_date": "2026-05-27", "action_type": "skip"})

            self.assertEqual(saved["decision_mood"], "plan_execution")
            self.assertIsNone(legacy["decision_mood"])
            with self.assertRaises(ValueError):
                store.save_entry("msft", {"trade_date": "2026-05-28", "action_type": "buy", "decision_mood": "raw_bad"})

    def test_trade_journal_store_saves_radar_buy_gate_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "quantity": 1,
                    "price": 210,
                    "decision_mood": "fomo",
                    "radarDecision": "BLOCK_CHASE",
                    "radarBlocked": True,
                    "radarBlockReasons": ["当前价进入追高禁止区", "情绪交易风险"],
                    "moodGateBlocked": True,
                    "positionGateBlocked": False,
                    "gateCheckedAt": "2026-05-26T12:00:00+00:00",
                },
            )

            self.assertEqual(saved["radar_decision"], "BLOCK_CHASE")
            self.assertFalse(saved["radar_blocked"])
            self.assertFalse(saved["mood_gate_blocked"])
            self.assertFalse(saved["position_gate_blocked"])
            self.assertTrue(saved["radar_advisory_only"])
            self.assertEqual(saved["gate_checked_at"], "2026-05-26T12:00:00+00:00")
            self.assertEqual(saved["radar_block_reasons"], [])
            self.assertIn("当前价进入追高禁止区", saved["radar_advisory_warnings"])
            self.assertIn("情绪交易风险", saved["radar_advisory_warnings"])

    def test_missing_radar_snapshot_uses_ledger_language(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "quantity": 1,
                    "price": 210,
                },
            )

            self.assertEqual(saved["radar_block_reasons"], [])
            self.assertEqual(saved["radar_advisory_warnings"], ["Radar 买入提示缺失，需人工判断；不作为买入硬拦截。"])

    def test_trade_journal_store_saves_fresh_buy_plan_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nok",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "quantity": 50,
                    "price": 4.8,
                    "entryMode": "planned_ladder_buy",
                    "buyPlanId": "NOK",
                    "buyPlanLevel": "first",
                    "plannedLadderBuy": True,
                    "planTriggerPrice": 5,
                    "planPlannedQuantity": 100,
                    "planRemainingQuantity": 100,
                    "planMaxPositionPct": 12,
                    "planMatchStatus": "allow_planned_add",
                    "freshPlanExecution": True,
                    "planAgeMinutes": 1.5,
                    "planRecentlyCreatedOrModified": True,
                },
            )

            self.assertEqual(saved["entry_mode"], "planned_ladder_buy")
            self.assertTrue(saved["planned_ladder_buy"])
            self.assertTrue(saved["fresh_plan_execution"])
            self.assertEqual(saved["plan_age_minutes"], 1.5)
            self.assertTrue(saved["plan_recently_created_or_modified"])

    def test_trade_journal_store_saves_pre_trade_cost_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "sell",
                    "quantity": 2,
                    "price": 220,
                    "preTradeQuantity": 10,
                    "preTradeAvgCost": 180,
                    "preTradeTotalCost": 1800,
                    "preTradePositionTier": "A",
                    "preTradeTargetSellPrice": 260,
                    "preTradeUnrealizedPnl": 400,
                    "costBasisSource": "position_snapshot",
                },
            )

            self.assertEqual(saved["pre_trade_quantity"], 10)
            self.assertEqual(saved["pre_trade_avg_cost"], 180)
            self.assertEqual(saved["pre_trade_total_cost"], 1800)
            self.assertEqual(saved["pre_trade_position_tier"], "A")
            self.assertEqual(saved["pre_trade_target_sell_price"], 260)
            self.assertEqual(saved["pre_trade_unrealized_pnl"], 400)
            self.assertEqual(saved["cost_basis_source"], "position_snapshot")

    def test_trade_journal_store_saves_sell_context_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "quantity": 2,
                    "price": 220,
                    "sellContextSnapshot": {
                        "ticker": "NVDA",
                        "position_tier": "A",
                        "target_sell_price": 260,
                        "buy_zone": {"lower": 200, "upper": 230},
                        "zone_status": "IN_BUY_ZONE",
                        "holding_days_reference": 8,
                        "below_target_at_sell": True,
                        "in_or_below_buy_zone_at_sell": True,
                        "missing_snapshot_fields": [],
                    },
                },
            )

            self.assertEqual(saved["sell_context_snapshot"]["ticker"], "NVDA")
            self.assertEqual(saved["sell_context_snapshot"]["position_tier"], "A")
            self.assertEqual(saved["sell_context_snapshot"]["zone_status"], "IN_BUY_ZONE")
            self.assertIn("sell_context_snapshot_json", saved)

    def test_trade_journal_store_saves_structure_entry_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "avgo",
                {
                    "trade_date": "2026-06-12",
                    "action_type": "buy",
                    "quantity": 1,
                    "price": 372,
                    "structureStatus": "STRUCTURE_FORMING",
                    "structureScore": 68,
                    "structureReasons": ["宏观回调", "守住 EMA50"],
                    "structureWarnings": ["收盘确认不足"],
                    "structureCheckedAt": "2026-06-12T10:30:00+08:00",
                },
            )

            self.assertEqual(saved["structure_status"], "STRUCTURE_FORMING")
            self.assertEqual(saved["structure_score"], 68)
            self.assertEqual(saved["structure_reasons"], ["宏观回调", "守住 EMA50"])
            self.assertEqual(saved["structure_warnings"], ["收盘确认不足"])
            self.assertEqual(saved["structure_checked_at"], "2026-06-12T10:30:00+08:00")

    def test_trade_journal_store_backfills_radar_gate_columns_on_legacy_schema(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE trade_journal_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        quantity REAL,
                        price REAL,
                        premium REAL,
                        strike_price REAL,
                        expiry_date TEXT,
                        decision_snapshot_id INTEGER,
                        notes TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

            store = TradeJournalStore(db_path)
            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "quantity": 1,
                    "price": 210,
                    "radarDecision": "ALLOW_BUY",
                    "radarBlocked": False,
                    "radarObservationOnly": True,
                    "gateCheckedAt": "2026-05-26T12:00:00+00:00",
                },
            )

            self.assertEqual(saved["radar_decision"], "ALLOW_BUY")
            self.assertFalse(saved["radar_blocked"])
            self.assertTrue(saved["radar_observation_only"])
            self.assertEqual(saved["radar_block_reasons"], [])
            self.assertIn("sell_context_snapshot_json", saved)

    def test_trade_journal_sell_snapshot_saves_now_style_risk(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "now",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "sell",
                    "quantity": 1,
                    "price": 700,
                    "decision_mood": "anxiety",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.5,
                    "plannedSellPct": 0.4,
                    "sellReasonType": "technical",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": True,
                },
            )

            self.assertIn("now_style_error_risk", saved["blockers"])
            self.assertIn("now_style_error_risk", json.loads(saved["blockers_json"]))

    def test_trade_journal_store_updates_entry_and_recomputes_discipline_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")
            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "quantity": 1,
                    "price": 200,
                    "decision_mood": "plan_execution",
                    "positionClass": "A",
                    "plannedSellPct": 0.1,
                    "sellReasonType": "technical",
                    "hasReentryPlan": True,
                    "reentryPullbackPrice": 180,
                    "reentryBuyBackPctOnPullback": 50,
                    "reentryThesisInvalidation": "thesis broken",
                },
            )

            updated = store.update_entry(
                saved["id"],
                "nvda",
                {
                    "trade_date": "2026-05-27",
                    "action_type": "trim",
                    "quantity": 2,
                    "price": 420,
                    "decision_mood": "anxiety",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.1,
                    "plannedSellPct": 0.4,
                    "sellReasonType": "technical",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": False,
                },
            )

            self.assertEqual(updated["symbol"], "NVDA")
            self.assertEqual(updated["action_type"], "trim")
            self.assertEqual(updated["quantity"], 2)
            self.assertEqual(updated["decision_mood"], "anxiety")
            self.assertEqual(updated["discipline_status"], "blocked")
            self.assertIn("reentry_plan_required_before_trim_or_sell", updated["blockers"])
            with self.assertRaises(ValueError):
                store.update_entry(9999, "msft", {"trade_date": "2026-05-27", "action_type": "buy", "quantity": 1, "price": 1})

    def test_trade_journal_store_rejects_history_action_or_symbol_change(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")
            buy = store.save_entry("nvda", {"trade_date": "2026-05-26", "action_type": "buy", "quantity": 1, "price": 200})
            sell = store.save_entry("msft", {"trade_date": "2026-05-26", "action_type": "sell", "quantity": 1, "price": 300})

            with self.assertRaisesRegex(ValueError, "历史交易类型不可修改"):
                store.update_entry(buy["id"], "nvda", {"trade_date": "2026-05-26", "action_type": "sell", "quantity": 1, "price": 200})
            with self.assertRaisesRegex(ValueError, "历史交易类型不可修改"):
                store.update_entry(sell["id"], "msft", {"trade_date": "2026-05-26", "action_type": "buy", "quantity": 1, "price": 300})
            with self.assertRaisesRegex(ValueError, "历史交易股票不可修改"):
                store.update_entry(buy["id"], "msft", {"trade_date": "2026-05-26", "action_type": "buy", "quantity": 1, "price": 200})

    def test_trade_journal_sell_saves_trading_discipline_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "sell",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.1,
                    "plannedSellPct": 1.0,
                    "sellReasonType": "macro",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": False,
                    "reentryPlanText": "",
                },
            )

            self.assertEqual(saved["position_class"], "A")
            self.assertEqual(saved["planned_sell_pct"], 1.0)
            self.assertEqual(saved["sell_reason_type"], "macro")
            self.assertEqual(saved["sell_level"], "L1")
            self.assertEqual(saved["discipline_status"], "blocked")
            self.assertIn("a_class_core_clear_requires_thesis_break", saved["blockers"])
            self.assertIn("macro_risk_cannot_trigger_single_name_exit", saved["blockers"])
            self.assertIn("宏观风险", saved["warnings"][0])
            self.assertEqual(saved["max_allowed_sell_pct"], 0.2)
            self.assertEqual(saved["can_sell_core"], 0)
            self.assertEqual(saved["requires_reentry_plan"], 1)
            self.assertTrue(saved["reminder_text"])

    def test_trade_journal_sell_saves_structured_sell_reason_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "nvda",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "sellContextType": "fundamental_change",
                    "fundamentalChangeType": ["revenue_growth_deterioration", "margin_deterioration"],
                    "valuationCompressionReason": "risk premium up",
                    "liquidityShockReason": "market panic",
                    "positionRiskReason": "single name over target",
                    "sellThesisNote": "growth path changed",
                },
            )

            self.assertEqual(saved["sell_context_type"], "fundamental_change")
            self.assertEqual(saved["fundamental_change_types"], ["revenue_growth_deterioration", "margin_deterioration"])
            self.assertEqual(json.loads(saved["fundamental_change_type"]), saved["fundamental_change_types"])
            self.assertEqual(saved["valuation_compression_reason"], "risk premium up")
            self.assertEqual(saved["liquidity_shock_reason"], "market panic")
            self.assertEqual(saved["position_risk_reason"], "single name over target")
            self.assertEqual(saved["sell_thesis_note"], "growth path changed")

    def test_trade_journal_trim_saves_reentry_plan_requirement(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": False,
                },
            )

            self.assertEqual(saved["action_type"], "trim")
            self.assertEqual(saved["sell_level"], "L1")
            self.assertEqual(saved["requires_reentry_plan"], 1)
            self.assertEqual(saved["discipline_status"], "blocked")
            self.assertIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])
            self.assertEqual(json.loads(saved["blockers_json"]), saved["blockers"])

    def test_trade_journal_trim_saves_structured_reentry_plan(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": False,
                    "reentryPullbackPrice": 380,
                    "reentryBreakoutPrice": 420,
                    "reentryTimeStopDays": 5,
                    "reentryBuyBackPctOnPullback": 50,
                    "reentryBuyBackPctOnBreakout": 30,
                    "reentryThesisInvalidation": "thesis broken",
                    "reentryPlanText": "Pullback or reclaim buyback plan",
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 1)
            self.assertEqual(saved["reentry_pullback_price"], 380)
            self.assertEqual(saved["reentry_breakout_price"], 420)
            self.assertEqual(saved["reentry_time_stop_days"], 5)
            self.assertEqual(saved["reentry_buy_back_pct_on_pullback"], 0.5)
            self.assertEqual(saved["reentry_buy_back_pct_on_breakout"], 0.3)
            self.assertEqual(saved["reentry_thesis_invalidation"], "thesis broken")
            self.assertEqual(saved["reentry_plan_text"], "Pullback or reclaim buyback plan")
            self.assertNotIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_reentry_checkbox_without_details_does_not_pass(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": True,
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 0)
            self.assertEqual(saved["discipline_status"], "blocked")
            self.assertIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_reentry_invalidation_only_does_not_pass(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": True,
                    "reentryThesisInvalidation": "thesis broken",
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 0)
            self.assertEqual(saved["reentry_thesis_invalidation"], "thesis broken")
            self.assertEqual(saved["discipline_status"], "blocked")
            self.assertIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_reentry_plan_text_only_does_not_pass(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "A",
                    "corePositionPct": 0.7,
                    "tradingPositionPct": 0.3,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": True,
                    "reentryPlanText": "看情况买回",
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 0)
            self.assertEqual(saved["reentry_plan_text"], "看情况买回")
            self.assertEqual(saved["discipline_status"], "blocked")
            self.assertIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_accepts_pullback_reentry_plan_with_pct_and_invalidation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "B",
                    "corePositionPct": 0.0,
                    "tradingPositionPct": 1.0,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "reentryPullbackPrice": 380,
                    "reentryBuyBackPctOnPullback": 50,
                    "reentryThesisInvalidation": "thesis broken",
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 1)
            self.assertNotIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_accepts_breakout_reentry_plan_with_pct_and_invalidation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "B",
                    "corePositionPct": 0.0,
                    "tradingPositionPct": 1.0,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "reentryBreakoutPrice": 420,
                    "reentryBuyBackPctOnBreakout": 30,
                    "reentryThesisInvalidation": "thesis broken",
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 1)
            self.assertNotIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_accepts_time_stop_reentry_plan_with_pct_and_invalidation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "B",
                    "corePositionPct": 0.0,
                    "tradingPositionPct": 1.0,
                    "unrealizedGainPct": 0.4,
                    "plannedSellPct": 0.1,
                    "sellReasonType": "valuation",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "reentryTimeStopDays": 5,
                    "reentryBuyBackPctOnBreakout": 30,
                    "reentryThesisInvalidation": "thesis broken",
                },
            )

            self.assertEqual(saved["has_reentry_plan"], 1)
            self.assertNotIn("reentry_plan_required_before_trim_or_sell", saved["blockers"])

    def test_trade_journal_buy_add_skip_do_not_require_discipline_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            buy = store.save_entry("hood", {"trade_date": "2026-05-26", "action_type": "buy"})
            add = store.save_entry("hood", {"trade_date": "2026-05-27", "action_type": "add"})
            skip = store.save_entry("hood", {"trade_date": "2026-05-28", "action_type": "skip"})

            self.assertIsNone(buy["discipline_status"])
            self.assertIsNone(add["sell_level"])
            self.assertIsNone(skip["planned_sell_pct"])
            self.assertEqual(skip["blockers"], [])
            self.assertEqual(skip["warnings"], [])

    def test_trade_journal_buy_saves_classification_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            saved = store.save_entry(
                "msft",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "positionClass": "A",
                    "corePositionMinPct": 60,
                    "tradingPositionMaxPct": 40,
                    "classificationNote": "core platform",
                },
            )

            self.assertEqual(saved["position_class"], "A")
            self.assertEqual(saved["core_position_min_pct"], 0.6)
            self.assertEqual(saved["trading_position_max_pct"], 0.4)
            self.assertEqual(saved["classification_note"], "core platform")
            self.assertIsNone(saved["discipline_status"])
            self.assertEqual(saved["blockers"], [])

    def test_trade_journal_reads_persisted_discipline_snapshot_without_recomputing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")
            saved = store.save_entry(
                "now",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "trim",
                    "positionClass": "B",
                    "corePositionPct": 0.5,
                    "tradingPositionPct": 0.5,
                    "unrealizedGainPct": 0.5,
                    "plannedSellPct": 10,
                    "sellReasonType": "technical",
                    "thesisBroken": False,
                    "positionOverLimit": False,
                    "hasReentryPlan": True,
                    "reentryPlanText": "回踩 MA50 回补",
                },
            )

            loaded = store.get_entry(saved["id"])

            self.assertEqual(loaded["planned_sell_pct"], 0.1)
            self.assertEqual(loaded["discipline_status"], "blocked")
            self.assertEqual(loaded["reentry_plan_text"], "回踩 MA50 回补")
            self.assertEqual(loaded["blockers"], saved["blockers"])
            self.assertEqual(loaded["warnings"], saved["warnings"])

    def test_decision_log_store_deletes_snapshot_and_related_records(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            snapshot = decision_store.save_snapshot(
                "now",
                {
                    "decision_date": "2026-05-26",
                    "price": 100,
                    "final_action": "add",
                },
            )
            outcome_store = DecisionOutcomeStore(db_path)
            tag_store = DecisionErrorTagStore(db_path)
            trade_store = TradeJournalStore(db_path)
            outcome_store.save_outcome(snapshot["id"], "1d", {"return_pct": 5, "status": "complete"})
            tag_store.save_tag(snapshot["id"], "technical_breakdown", "lost level")
            entry = trade_store.save_entry(
                "now",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "buy",
                    "decision_snapshot_id": snapshot["id"],
                },
            )

            self.assertTrue(decision_store.delete_snapshot(snapshot["id"]))

            self.assertIsNone(decision_store.get_snapshot(snapshot["id"]))
            self.assertIsNone(outcome_store.get_outcome(snapshot["id"], "1d"))
            self.assertEqual(tag_store.list_tags_for_snapshot(snapshot["id"]), [])
            self.assertIsNone(trade_store.get_entry(entry["id"])["decision_snapshot_id"])
            self.assertFalse(decision_store.delete_snapshot(snapshot["id"]))

    def test_trade_journal_store_supports_skip_but_rejects_option_actions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            with self.assertRaises(ValueError):
                store.save_entry("hood", {"trade_date": "2026-05-26", "action_type": "sell_put"})
            skip_entry = store.save_entry("hood", {"trade_date": "2026-05-27", "action_type": "skip"})

            self.assertEqual(skip_entry["action_type"], "skip")

    def test_trade_journal_store_lists_all_entries_and_symbols(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            store.save_entry("hood", {"trade_date": "2026-05-26", "action_type": "buy"})
            store.save_entry("now", {"trade_date": "2026-05-27", "action_type": "sell"})

            self.assertEqual([entry["symbol"] for entry in store.list_entries()], ["NOW", "HOOD"])
            self.assertEqual(store.list_symbols(), ["HOOD", "NOW"])

    def test_trade_journal_store_deletes_entry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")
            saved = store.save_entry("now", {"trade_date": "2026-05-26", "action_type": "buy"})

            self.assertTrue(store.delete_entry(saved["id"]))

            self.assertIsNone(store.get_entry(saved["id"]))
            self.assertEqual(store.list_entries(), [])
            self.assertFalse(store.delete_entry(saved["id"]))

    def test_decision_log_and_trade_journal_validate_inputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            trade_store = TradeJournalStore(db_path)

            with self.assertRaises(ValueError):
                decision_store.save_snapshot("", {"decision_date": "2026-05-26"})
            with self.assertRaises(ValueError):
                decision_store.save_snapshot("NOW", {"price": -1})
            with self.assertRaises(ValueError):
                trade_store.save_entry("NOW", {"action_type": "unknown"})
            with self.assertRaises(ValueError):
                trade_store.save_entry("NOW", {"action_type": "buy", "quantity": -1})

    def test_decision_outcomes_calculate_returns_and_drawdown(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            self._insert_price_history(
                db_path,
                "NOW",
                [
                    ("2026-05-27", 110),
                    ("2026-05-28", 95),
                    ("2026-06-02", 120),
                    ("2026-06-26", 130),
                    ("2026-08-24", 150),
                    ("2026-11-22", 180),
                ],
            )
            snapshot = {"id": 1, "symbol": "NOW", "decision_date": "2026-05-26", "price": 100}

            outcomes = {item["horizon"]: item for item in build_decision_outcomes_from_price_history(snapshot, db_path)}

            self.assertEqual(set(outcomes), {"1d", "1w", "1m", "3m", "6m"})
            self.assertEqual(outcomes["1d"]["start_price"], 100)
            self.assertEqual(outcomes["1d"]["end_price"], 110)
            self.assertEqual(outcomes["1d"]["return_pct"], 10)
            self.assertEqual(outcomes["1w"]["end_price"], 120)
            self.assertEqual(outcomes["1w"]["return_pct"], 20)
            self.assertEqual(outcomes["1w"]["max_drawdown_pct"], -13.636363636363635)
            self.assertEqual(outcomes["6m"]["end_price"], 180)
            self.assertEqual(outcomes["6m"]["status"], "complete")

    def test_decision_outcome_store_saves_and_lists_by_snapshot_id(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            self._insert_price_history(db_path, "CRM", [("2026-05-27", 105), ("2026-06-25", 125)])
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "CRM",
                {
                    "decision_date": "2026-05-26",
                    "price": 100,
                    "final_action": "add",
                },
            )
            store = DecisionOutcomeStore(db_path)

            saved = store.calculate_and_save_outcomes(snapshot["id"])
            loaded = store.list_outcomes(snapshot["id"])

            self.assertEqual([row["horizon"] for row in loaded], ["1d", "1w", "1m", "3m", "6m"])
            self.assertEqual(len(saved), 5)
            self.assertEqual(store.get_outcome(snapshot["id"], "1d")["return_pct"], 5)
            self.assertEqual(store.get_outcome(snapshot["id"], "1m")["return_pct"], 25)

    def test_decision_outcomes_mark_missing_when_price_history_is_insufficient(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "ADBE",
                {
                    "decision_date": "2026-05-26",
                    "price": 300,
                    "final_action": "wait",
                },
            )
            store = DecisionOutcomeStore(db_path)

            outcomes = store.calculate_and_save_outcomes(snapshot["id"])

            self.assertEqual(len(outcomes), 5)
            self.assertTrue(all(outcome["status"] == "missing" for outcome in outcomes))
            self.assertIsNone(store.get_outcome(snapshot["id"], "1d")["end_price"])

    def test_decision_outcome_store_validates_horizon(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DecisionOutcomeStore(Path(tmpdir) / "decision_log.sqlite")

            with self.assertRaises(ValueError):
                store.save_outcome(1, "2y", {"status": "missing"})

    def test_refresh_decision_outcomes_backfills_all_snapshots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            self._insert_price_history(db_path, "NOW", [("2026-05-27", 110), ("2026-06-25", 130)])
            decision_store = DecisionLogStore(db_path)
            now_snapshot = decision_store.save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add"},
            )
            missing_snapshot = decision_store.save_snapshot(
                "ADBE",
                {"decision_date": "2026-05-26", "price": 300, "final_action": "wait"},
            )

            summary = refresh_decision_outcomes(db_path)
            outcome_store = DecisionOutcomeStore(db_path)

            self.assertEqual(summary["snapshotCount"], 2)
            self.assertEqual(summary["outcomeCount"], 10)
            self.assertEqual(summary["missingCount"], 5)
            self.assertEqual(outcome_store.get_outcome(now_snapshot["id"], "1d")["return_pct"], 10)
            self.assertEqual(outcome_store.get_outcome(now_snapshot["id"], "1m")["return_pct"], 30)
            self.assertEqual(outcome_store.get_outcome(missing_snapshot["id"], "1d")["status"], "missing")

    def test_refresh_decision_outcomes_reads_fmp_history_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            self._insert_price_history(db_path, "FMP:NVDA", [("2026-05-27", 220), ("2026-06-25", 240)])
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "NVDA",
                {"decision_date": "2026-05-26", "price": 200, "final_action": "add"},
            )

            summary = refresh_decision_outcomes(db_path)
            outcome_store = DecisionOutcomeStore(db_path)

            self.assertEqual(summary["snapshotCount"], 1)
            self.assertEqual(summary["missingCount"], 0)
            self.assertEqual(outcome_store.get_outcome(snapshot["id"], "1d")["return_pct"], 10)
            self.assertEqual(outcome_store.get_outcome(snapshot["id"], "1m")["return_pct"], 20)

    def test_decision_outcomes_use_market_context_history_key_selection(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            self._insert_price_history(db_path, "CRWV", [("2026-05-27", 105), ("2026-06-25", 110)], "2026-05-28T10:00:00+00:00")
            self._insert_price_history(db_path, "FMP:CRWV", [("2026-05-27", 120), ("2026-06-25", 140)], "2026-05-30T10:00:00+00:00")
            snapshot = {
                "id": 1,
                "symbol": "CRWV",
                "decision_date": "2026-05-26",
                "price": 100,
            }

            outcomes = {item["horizon"]: item for item in build_decision_outcomes_from_price_history(snapshot, db_path)}

            self.assertEqual(outcomes["1d"]["return_pct"], 20)
            self.assertEqual(outcomes["1m"]["return_pct"], 40)

    def test_refresh_decision_outcomes_overwrites_existing_outcomes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            self._insert_price_history(db_path, "CRM", [("2026-05-27", 105)])
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "CRM",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add"},
            )
            outcome_store = DecisionOutcomeStore(db_path)
            outcome_store.save_outcome(snapshot["id"], "1d", {"return_pct": -99, "status": "complete"})

            summary = refresh_decision_outcomes(db_path)

            self.assertEqual(summary["snapshotCount"], 1)
            self.assertEqual(summary["outcomeCount"], 5)
            self.assertEqual(outcome_store.get_outcome(snapshot["id"], "1d")["return_pct"], 5)

    def test_decision_signal_stats_group_by_final_action_and_horizon(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            outcome_store = DecisionOutcomeStore(db_path)
            add_win = decision_store.save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add", "decision_lane": "actionable"},
            )
            add_loss = decision_store.save_snapshot(
                "CRM",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add", "decision_lane": "actionable"},
            )
            wait_missing = decision_store.save_snapshot(
                "ADBE",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "wait", "decision_lane": "wait"},
            )

            outcome_store.save_outcome(add_win["id"], "1d", {"return_pct": 10, "max_drawdown_pct": -2, "status": "complete"})
            outcome_store.save_outcome(add_loss["id"], "1d", {"return_pct": -4, "max_drawdown_pct": -8, "status": "complete"})
            outcome_store.save_outcome(wait_missing["id"], "1d", {"status": "missing"})

            stats = build_decision_signal_stats(db_path)
            rows = {row["group"]: row for row in stats["byHorizon"]["1d"]["byFinalAction"]}
            summary = stats["byHorizon"]["1d"]["summary"]

            self.assertEqual(stats["horizons"], ["1d", "1w", "1m", "3m", "6m"])
            self.assertEqual(summary["sampleCount"], 2)
            self.assertEqual(summary["missingCount"], 1)
            self.assertEqual(summary["totalCount"], 3)
            self.assertEqual(summary["winRate"], 50)
            self.assertEqual(summary["averageReturnPct"], 3)
            self.assertEqual(summary["medianReturnPct"], 3)
            self.assertEqual(summary["averageMaxDrawdownPct"], -5)
            self.assertEqual(rows["add"]["sampleCount"], 2)
            self.assertEqual(rows["add"]["missingCount"], 0)
            self.assertEqual(rows["add"]["totalCount"], 2)
            self.assertEqual(rows["add"]["winRate"], 50)
            self.assertEqual(rows["add"]["averageReturnPct"], 3)
            self.assertEqual(rows["add"]["medianReturnPct"], 3)
            self.assertEqual(rows["add"]["averageMaxDrawdownPct"], -5)
            self.assertEqual(rows["wait"]["sampleCount"], 0)
            self.assertEqual(rows["wait"]["missingCount"], 1)

    def test_decision_signal_stats_group_by_decision_lane_and_count_missing_horizons(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            outcome_store = DecisionOutcomeStore(db_path)
            actionable = decision_store.save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add", "decision_lane": "actionable"},
            )
            blocked = decision_store.save_snapshot(
                "HOOD",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "wait", "decision_lane": "blocked"},
            )

            outcome_store.save_outcome(actionable["id"], "1w", {"return_pct": 12, "max_drawdown_pct": -3, "status": "complete"})

            stats = build_decision_signal_stats(db_path)
            one_week = {row["group"]: row for row in stats["byHorizon"]["1w"]["byDecisionLane"]}
            one_day = {row["group"]: row for row in stats["byHorizon"]["1d"]["byDecisionLane"]}

            self.assertEqual(one_week["actionable"]["sampleCount"], 1)
            self.assertEqual(one_week["actionable"]["missingCount"], 0)
            self.assertEqual(one_week["blocked"]["sampleCount"], 0)
            self.assertEqual(one_week["blocked"]["missingCount"], 1)
            self.assertEqual(one_day["actionable"]["sampleCount"], 0)
            self.assertEqual(one_day["actionable"]["missingCount"], 1)
            self.assertEqual(one_day["blocked"]["missingCount"], 1)

    def test_decision_signal_stats_include_error_tag_performance_and_cross_tabs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            outcome_store = DecisionOutcomeStore(db_path)
            tag_store = DecisionErrorTagStore(db_path)
            add_loss = decision_store.save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add", "decision_lane": "actionable"},
            )
            wait_loss = decision_store.save_snapshot(
                "CRM",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "wait", "decision_lane": "wait"},
            )
            missing = decision_store.save_snapshot(
                "ADBE",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add", "decision_lane": "actionable"},
            )

            tag_store.save_tag(add_loss["id"], "valuation_too_high")
            tag_store.save_tag(wait_loss["id"], "valuation_too_high")
            tag_store.save_tag(missing["id"], "low_confidence_data")
            outcome_store.save_outcome(add_loss["id"], "1d", {"return_pct": -8, "max_drawdown_pct": -12, "status": "complete"})
            outcome_store.save_outcome(wait_loss["id"], "1d", {"return_pct": -2, "max_drawdown_pct": -4, "status": "complete"})

            stats = build_decision_signal_stats(db_path)
            tag_rows = {row["group"]: row for row in stats["byHorizon"]["1d"]["byErrorTag"]}
            action_cross = {
                (row["finalAction"], row["errorTag"]): row
                for row in stats["byHorizon"]["1d"]["byFinalActionErrorTag"]
            }
            lane_cross = {
                (row["decisionLane"], row["errorTag"]): row
                for row in stats["byHorizon"]["1d"]["byDecisionLaneErrorTag"]
            }
            counts = {row["tag"]: row["count"] for row in stats["errorTags"]["counts"]}

            self.assertEqual(counts["valuation_too_high"], 2)
            self.assertEqual(counts["low_confidence_data"], 1)
            self.assertEqual(tag_rows["valuation_too_high"]["totalCount"], 2)
            self.assertEqual(tag_rows["valuation_too_high"]["sampleCount"], 2)
            self.assertEqual(tag_rows["valuation_too_high"]["averageReturnPct"], -5)
            self.assertEqual(tag_rows["valuation_too_high"]["averageMaxDrawdownPct"], -8)
            self.assertEqual(tag_rows["low_confidence_data"]["sampleCount"], 0)
            self.assertEqual(tag_rows["low_confidence_data"]["missingCount"], 1)
            self.assertEqual(action_cross[("add", "valuation_too_high")]["averageReturnPct"], -8)
            self.assertEqual(action_cross[("wait", "valuation_too_high")]["averageReturnPct"], -2)
            self.assertEqual(lane_cross[("actionable", "low_confidence_data")]["missingCount"], 1)

    def test_decision_error_tag_store_saves_updates_and_lists_by_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add"},
            )
            store = DecisionErrorTagStore(db_path)

            saved = store.save_tag(snapshot["id"], "valuation_too_high", "paid too much")
            updated = store.save_tag(snapshot["id"], "valuation_too_high", "multiple expanded")
            store.save_tag(snapshot["id"], "low_confidence_data")

            tags = store.list_tags_for_snapshot(snapshot["id"])
            self.assertEqual(saved["tag"], "valuation_too_high")
            self.assertEqual(updated["notes"], "multiple expanded")
            self.assertEqual([tag["tag"] for tag in tags], ["low_confidence_data", "valuation_too_high"])
            self.assertEqual(store.get_tag(snapshot["id"], "valuation_too_high")["notes"], "multiple expanded")

    def test_decision_error_tag_store_lists_by_symbol(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            now_snapshot = decision_store.save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add"},
            )
            crm_snapshot = decision_store.save_snapshot(
                "CRM",
                {"decision_date": "2026-05-26", "price": 200, "final_action": "wait"},
            )
            store = DecisionErrorTagStore(db_path)

            store.save_tag(now_snapshot["id"], "technical_breakdown", "lost key level")
            store.save_tag(crm_snapshot["id"], "macro_shock", "rates shock")

            now_tags = store.list_tags_for_symbol("now")
            self.assertEqual(len(now_tags), 1)
            self.assertEqual(now_tags[0]["symbol"], "NOW")
            self.assertEqual(now_tags[0]["tag"], "technical_breakdown")
            self.assertEqual(now_tags[0]["decision_date"], "2026-05-26")

    def test_decision_error_tag_store_summarizes_counts_and_recent_cases(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            decision_store = DecisionLogStore(db_path)
            now_snapshot = decision_store.save_snapshot(
                "NOW",
                {"decision_date": "2026-05-26", "price": 100, "final_action": "add", "decision_lane": "actionable"},
            )
            crm_snapshot = decision_store.save_snapshot(
                "CRM",
                {"decision_date": "2026-05-27", "price": 200, "final_action": "wait", "decision_lane": "wait"},
            )
            store = DecisionErrorTagStore(db_path)

            store.save_tag(now_snapshot["id"], "technical_breakdown", "lost key level")
            store.save_tag(crm_snapshot["id"], "technical_breakdown", "failed again")
            store.save_tag(crm_snapshot["id"], "macro_shock", "rates shock")

            counts = {row["tag"]: row["count"] for row in store.tag_counts()}
            recent = store.recent_tags(limit=2)
            snapshots = decision_store.list_recent_snapshots(limit=1)

            self.assertEqual(counts["technical_breakdown"], 2)
            self.assertEqual(counts["macro_shock"], 1)
            self.assertEqual(len(recent), 2)
            self.assertEqual(recent[0]["symbol"], "CRM")
            self.assertEqual(snapshots[0]["symbol"], "CRM")

    def test_decision_error_tag_store_deletes_and_validates_tags(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decision_log.sqlite"
            snapshot = DecisionLogStore(db_path).save_snapshot(
                "HOOD",
                {"decision_date": "2026-05-26", "price": 80, "final_action": "wait"},
            )
            store = DecisionErrorTagStore(db_path)

            store.save_tag(snapshot["id"], "ignored_system_warning", "bought anyway")
            deleted = store.delete_tag(snapshot["id"], "ignored_system_warning")

            self.assertTrue(deleted)
            self.assertFalse(store.delete_tag(snapshot["id"], "ignored_system_warning"))
            self.assertEqual(store.list_tags_for_snapshot(snapshot["id"]), [])
            self.assertIn("macro_shock", DECISION_ERROR_TAGS)
            with self.assertRaises(ValueError):
                store.save_tag(snapshot["id"], "not_a_real_tag")

