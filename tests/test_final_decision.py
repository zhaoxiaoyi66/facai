from __future__ import annotations

import unittest
from types import SimpleNamespace

from buy_zone_engine import BuyZoneEstimate
from scoring.final_decision import BUY_ACTIONS, derive_final_decision
from scoring.final_decision_adapter import build_final_decision_bundle


class FinalDecisionTests(unittest.TestCase):
    def test_final_decision_blocks_observe_valuation_and_plan_add(self) -> None:
        score = SimpleNamespace(
            action="可小仓分批",
            valuationStatus="只观察",
            entryRating="A",
            riskRating="低",
            dataConfidence="high",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )
        buy_zone_estimate = SimpleNamespace(currentZone="tranche_buy")
        position_plan = SimpleNamespace(currentAddLimitPercent=8, maxPortfolioWeightPercent=20)

        decision = derive_final_decision(score, buy_zone_estimate, position_plan)

        self.assertEqual(decision.finalAction, "待复核，暂不新增")
        self.assertFalse(decision.isActionable)
        self.assertEqual(decision.currentAddLimitPercent, 0)
        self.assertIn("buy_zone", decision.blockReasons)
        self.assertEqual(decision.buyZoneAction, "DATA_INSUFFICIENT")

    def test_unified_buy_zone_setup_overrides_legacy_valuation_buy_zone_block(self) -> None:
        score = SimpleNamespace(
            action="只观察",
            valuationStatus="只观察",
            entryRating="A",
            riskRating="低",
            dataConfidence="high",
            currentAddLimitPercent=3,
            maxPortfolioWeightPercent=6,
        )
        buy_zone_context = {
            "current_action": "ALLOW_SMALL_BUY",
            "action_text": "允许小仓观察",
            "primary_zone_text": "回踩买区",
            "setup_score": 67.5,
        }

        decision = derive_final_decision(score, buy_zone_context)

        self.assertEqual(decision.finalAction, "可小仓分批")
        self.assertTrue(decision.isActionable)
        self.assertEqual(decision.currentAddLimitPercent, 3)
        self.assertEqual(decision.setupScore, 67.5)
        self.assertNotIn("valuation_status", decision.blockReasons)

    def test_unified_buy_zone_missing_technical_data_blocks_clear_buy_zone(self) -> None:
        score = SimpleNamespace(
            action="可小仓分批",
            valuationStatus="fair",
            entryRating="A",
            riskRating="低",
            dataConfidence="high",
            currentAddLimitPercent=3,
            maxPortfolioWeightPercent=6,
        )
        buy_zone_context = {
            "current_action": "DATA_INSUFFICIENT",
            "action_text": "技术承接数据不足",
            "primary_zone_text": "技术承接数据不足",
            "setup_score": 0,
            "missing_fields": ["volume_acceptance"],
        }

        decision = derive_final_decision(score, buy_zone_context)

        self.assertFalse(decision.isActionable)
        self.assertEqual(decision.currentAddLimitPercent, 0)
        self.assertIn("buy_zone", decision.blockReasons)
        self.assertEqual(decision.buyZoneAction, "DATA_INSUFFICIENT")

    def test_final_decision_blocks_c_or_d_entry_from_actionable(self) -> None:
        for entry_rating in ["C - 只观察", "D - 剔除"]:
            with self.subTest(entry_rating=entry_rating):
                score = SimpleNamespace(
                    action="可小仓分批",
                    valuationStatus="击球区附近",
                    entryRating=entry_rating,
                    riskRating="低",
                    dataConfidence="high",
                    currentAddLimitPercent=5,
                    maxPortfolioWeightPercent=15,
                )

                decision = derive_final_decision(score)

                self.assertFalse(decision.isActionable)
                self.assertEqual(decision.currentAddLimitPercent, 0)
                self.assertIn("entry_rating", decision.blockReasons)

    def test_final_decision_caps_medium_high_risk_normal_batch(self) -> None:
        score = SimpleNamespace(
            action="可正常分批",
            valuationStatus="击球区附近",
            entryRating="A",
            riskRating="中高",
            dataConfidence="high",
            currentAddLimitPercent=10,
            maxPortfolioWeightPercent=20,
        )

        decision = derive_final_decision(score)

        self.assertNotEqual(decision.finalAction, "可正常分批")
        self.assertFalse(decision.isActionable)
        self.assertEqual(decision.currentAddLimitPercent, 0)
        self.assertIn("risk_rating", decision.reviewReasons)

    def test_final_decision_blocks_low_confidence_buy_and_add(self) -> None:
        score = SimpleNamespace(
            action="可小仓分批",
            valuationStatus="击球区附近",
            entryRating="A",
            riskRating="低",
            dataConfidence="low",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )
        position_plan = SimpleNamespace(currentAddLimitPercent=5, maxPortfolioWeightPercent=20)

        decision = derive_final_decision(score, position_plan=position_plan)

        self.assertNotIn(decision.finalAction, {"可小仓分批", "可正常分批"})
        self.assertFalse(decision.isActionable)
        self.assertEqual(decision.currentAddLimitPercent, 0)
        self.assertIn("data_confidence", decision.blockReasons)

    def test_final_decision_blocks_no_chase_and_invalid_zones(self) -> None:
        for zone in ["no_chase", "invalid_zone", "data_insufficient"]:
            with self.subTest(zone=zone):
                score = SimpleNamespace(
                    action="可小仓分批",
                    valuationStatus="击球区附近",
                    entryRating="A",
                    riskRating="低",
                    dataConfidence="high",
                    currentAddLimitPercent=5,
                    maxPortfolioWeightPercent=15,
                )
                buy_zone_estimate = SimpleNamespace(currentZone=zone)
                position_plan = SimpleNamespace(currentAddLimitPercent=5, maxPortfolioWeightPercent=20)

                decision = derive_final_decision(score, buy_zone_estimate, position_plan)

                self.assertFalse(decision.isActionable)
                self.assertEqual(decision.currentAddLimitPercent, 0)
                self.assertIn("buy_zone", decision.blockReasons)

    def test_final_decision_adapter_builds_score_only_bundle(self) -> None:
        buy_action = sorted(BUY_ACTIONS)[0]
        score = SimpleNamespace(
            action=buy_action,
            valuationStatus="fair",
            entryRating="A",
            riskRating="low",
            dataConfidence="high",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )

        bundle = build_final_decision_bundle(score)

        self.assertEqual(bundle.finalAction, "待复核，暂不新增")
        self.assertFalse(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 0)
        self.assertEqual(bundle.maxPortfolioWeightPercent, 15)
        self.assertIn("buy_zone", bundle.blockReasons)
        self.assertEqual(bundle.buyZoneAction, "DATA_INSUFFICIENT")

    def test_final_decision_adapter_builds_bundle_from_score_zone_and_plan(self) -> None:
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

        self.assertFalse(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 0)
        self.assertEqual(bundle.maxPortfolioWeightPercent, 20)
        self.assertEqual(bundle.positionPlanCurrentAddLimitPercent, 6)
        self.assertIn("buy_zone", bundle.blockReasons)
        self.assertEqual(bundle.buyZoneAction, "DATA_INSUFFICIENT")

    def test_final_decision_adapter_blocks_no_chase_add(self) -> None:
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
        plan = SimpleNamespace(currentAddLimitPercent=5, maxPortfolioWeightPercent=20)

        bundle = build_final_decision_bundle(score, zone, plan)

        self.assertFalse(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 0)
        self.assertIn("buy_zone", bundle.blockReasons)

    def test_final_decision_adapter_zeroes_low_confidence_add(self) -> None:
        score = SimpleNamespace(
            action=sorted(BUY_ACTIONS)[0],
            valuationStatus="fair",
            entryRating="A",
            riskRating="low",
            dataConfidence="low",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )

        bundle = build_final_decision_bundle(score)

        self.assertFalse(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 0)
        self.assertIn("data_confidence", bundle.blockReasons)

    def test_final_decision_adapter_rebuilds_plan_after_manual_override(self) -> None:
        score = SimpleNamespace(
            action=sorted(BUY_ACTIONS)[0],
            valuationStatus="fair",
            qualityRating="A",
            entryRating="A",
            riskRating="low",
            dataConfidence="high",
            currentAddLimitPercent=10,
            maxPortfolioWeightPercent=15,
        )
        zone = BuyZoneEstimate(
            "MAN",
            "GENERIC",
            140,
            130,
            105,
            120,
            90,
            100,
            70,
            "no_chase",
            "high",
            "blended",
            ["P/FCF"],
            [],
            [],
            "now",
        )
        manual_plan = {
            "no_chase_above": 170,
            "fair_value_low": 130,
            "fair_value_high": 150,
            "tranche_buy_low": 110,
            "tranche_buy_high": 125,
            "heavy_buy_below": 95,
        }

        bundle = build_final_decision_bundle(score, zone, manual_plan_override=manual_plan)

        self.assertFalse(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 0)
        self.assertGreater(bundle.positionPlanCurrentAddLimitPercent or 0, 0)
        self.assertIn("buy_zone", bundle.blockReasons)
        self.assertEqual(bundle.buyZoneAction, "DATA_INSUFFICIENT")

    def test_final_decision_adapter_output_fields_are_stable(self) -> None:
        score = SimpleNamespace(
            action=sorted(BUY_ACTIONS)[0],
            valuationStatus="fair",
            entryRating="A",
            riskRating="low",
            dataConfidence="high",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )

        fields = set(build_final_decision_bundle(score).as_dict())

        self.assertEqual(
            fields,
            {
                "executionSource",
                "finalAction",
                "decisionLane",
                "displayCategory",
                "isActionable",
                "currentAddLimitPercent",
                "maxPortfolioWeightPercent",
                "blockReasons",
                "reviewReasons",
                "dataConfidence",
                "buyZoneStatus",
                "legacyAction",
                "scoreCurrentAddLimitPercent",
                "scoreMaxPortfolioWeightPercent",
                "positionPlanCurrentAddLimitPercent",
                "positionPlanMaxPortfolioWeightPercent",
                "setupScore",
                "buyZoneAction",
                "buyZoneActionText",
                "buyZonePrimaryZone",
                "buyZoneContext",
                "buyZoneDisplay",
                "sizingAction",
                "sizingActionText",
                "mainActionText",
            },
        )

    def test_final_decision_adapter_accepts_unified_buy_zone_context(self) -> None:
        score = SimpleNamespace(
            action="只观察",
            valuationStatus="只观察",
            entryRating="A",
            riskRating="low",
            dataConfidence="high",
            currentAddLimitPercent=2,
            maxPortfolioWeightPercent=5,
        )

        bundle = build_final_decision_bundle(
            score,
            buy_zone_context={
                "current_action": "ALLOW_SMALL_BUY",
                "action_text": "允许小仓观察",
                "primary_zone_text": "回踩买区",
                "setup_score": 66,
            },
        )

        self.assertTrue(bundle.isActionable)
        self.assertEqual(bundle.finalAction, "可小仓分批")
        self.assertEqual(bundle.buyZoneAction, "ALLOW_SMALL_BUY")
        self.assertEqual(bundle.setupScore, 66)

    def test_final_decision_adapter_keeps_legacy_values_debug_only(self) -> None:
        buy_action = sorted(BUY_ACTIONS)[0]
        score = SimpleNamespace(
            action=buy_action,
            valuationStatus="fair",
            entryRating="A",
            riskRating="low",
            dataConfidence="high",
            currentAddLimitPercent=5,
            maxPortfolioWeightPercent=15,
        )
        zone = SimpleNamespace(currentZone="no_chase")
        plan = SimpleNamespace(currentAddLimitPercent=5, maxPortfolioWeightPercent=20)

        bundle = build_final_decision_bundle(score, zone, plan)

        self.assertEqual(bundle.executionSource, "finalDecisionBundle")
        self.assertEqual(bundle.legacyAction, buy_action)
        self.assertEqual(bundle.scoreCurrentAddLimitPercent, 5)
        self.assertEqual(bundle.positionPlanCurrentAddLimitPercent, 5)
        self.assertFalse(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 0)
        self.assertNotEqual(bundle.finalAction, bundle.legacyAction)

