from __future__ import annotations

import unittest
import inspect
import os
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from ai.qwen_client import (
    DEFAULT_QWEN_BASE_URL,
    EVIDENCE_ONLY_RULE,
    QwenClient,
    QwenProviderError,
    QwenSettings,
    mask_api_key,
    normalize_qwen_base_url,
    qwen_settings_from_env,
    _read_local_dotenv,
)
from ai.qwen_health_check import format_health_check_report, qwen_health_check
from ai.qwen_review_service import (
    QWEN_REVIEW_SYSTEM_PROMPT,
    QwenReviewService,
    apply_qwen_review_result,
    build_qwen_review_input,
    enforce_qwen_evidence_only,
    parse_qwen_review_json,
    qwen_review_efficiency_stats,
    qwen_review_eligibility,
    qwen_review_candidates,
    validate_qwen_review_result,
)
from ai.review_automation import (
    ReviewAutomationService,
    apply_automation_result,
    automation_effectiveness,
    classify_review_item,
)
from buy_zone import BuyZoneInputs, calculate_buy_zone_ladder, calculate_fair_value_per_share
from buy_zone_engine import (
    BuyZoneEstimate,
    buy_zone_with_manual_override,
    clear_buy_zone_override_values,
    direct_fcf_margin,
    generate_buy_zone,
    has_buy_zone_override,
    normalize_percent_metric,
    validate_buy_zone_estimate,
)
from data.ai_review_assistant import (
    AIReviewAssistant,
    AIReviewStore,
    QwenReviewClient,
    apply_ai_review_result,
    ai_review_candidates,
    create_review_client,
    enforce_evidence_only_result,
    validate_ai_review_result,
)
from data.calculated_metrics import calculate_metrics
from data.data_confidence import enrich_data_confidence
from data.disclosure_pipeline import DisclosurePipeline
from data.disclosure_store import DisclosureStore, canMetricEnterScoring
from data.evidence_backfill import backfill_evidence_for_review_item
from data.extract_metric_from_text import extractMetricFromText
from data.fmp_cache import CACHE_TTL_SECONDS, ttl_bucket_for_endpoint
from data.fmp_queue import FMP_RATE_LIMIT
from data.fundamentals import FundamentalCache
from data.decision_log import DecisionLogStore, TradeJournalStore, build_decision_snapshot_from_bundle
from data.ir_kpi_scraper import kpi_mapping_for_ticker, parse_ir_kpi_text
from data.metric_dictionary import metric_definition_by_key
from data.metric_source_map import metric_source_definition
from data.metric_variants import extract_saas_metric_variants
from data.normalize_metric_value import deterministic_precheck, normalize_metric_period, normalize_metric_value
from data.providers import (
    FMPProvider,
    MarketDataProvider,
    PolygonProvider,
    SECEdgarProvider,
    _merge_disclosure_supplement,
    get_market_data_provider,
)
from data.review_queue_builder import ReviewQueueBuilder, ReviewQueueStore, _debt_maturity_low_materiality
from data.sec_client import SECClient, SEC_MAX_REQUESTS_PER_SECOND
from data.sec_supplement import extract_sec_saas_metrics
from data.portfolio import (
    PortfolioPositionStore,
    PortfolioSettingsStore,
    calculate_portfolio_position,
    calculate_portfolio_positions,
)
from data.portfolio_view_model import build_portfolio_view_model
from data.stock_plan import StockPlanStore
from position_plan_engine import generate_position_plan
from review_autopilot import ReviewAutopilot, _human_remaining, auto_fill_capability, identify_missing_data_items
from formatting import format_currency, format_large_number, format_multiple, format_percent
from indicators.technicals import (
    add_technical_indicators,
    calculate_drawdown_from_52_week_high,
    calculate_ema20,
    calculate_ema50,
    calculate_ema200,
    calculate_gain_over_trading_days,
    calculate_rsi14,
    calculate_technical_score,
    latest_technical_snapshot,
)
from scoring.overheat import OverheatResult, calculate_overheat_score
from scoring.risk_flags import RiskFlag
from scoring.power_company import is_power_company
from scoring.metric_sources import fcf_margin_metric, metric_participates_in_score
from scoring.sector_models import ScoreContext, classifyStockModel, fcf_margin_score, _final_action, _guard_action_conflicts
from scoring.final_decision import BUY_ACTIONS, NON_BUY_VALUATION_STATUSES, derive_final_decision
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.signals import (
    ANTI_FOMO_MESSAGE,
    LEFT_SIDE_OPPORTUNITY_MESSAGE,
    build_trading_signals,
    normalize_valuation_score,
)
from scoring.total_score import calculate_total_score
from scoring.valuation import calculate_valuation_score
from ui.dashboard import (
    DASHBOARD_COLUMNS,
    _action_recommendation,
    _loading_shell_html,
    _metric_resolution_groups,
    _drawer_resolution_html,
    _render_metric_resolution_groups,
    _render_score_explanation,
    _risk_rating,
    _refresh_progress_html,
    _resolution_value_text,
    _translate_factor,
    _valuation_status,
)
from ui import buy_zone, manual_review, stock_detail
from ui.metric_labels import is_internal_metric_field, metric_label, model_type_label, resolution_status_label, unmapped_metric_labels


def _metric_resolution_by_key(result, metric_key: str) -> dict:
    for row in result.metricResolutionStatus:
        if row.get("metricKey") == metric_key:
            return row
    raise AssertionError(f"missing metric resolution row: {metric_key}")


def _metric_resolution_by_display(result, display_name: str) -> dict:
    for row in result.metricResolutionStatus:
        if row.get("displayName") == display_name:
            return row
    raise AssertionError(f"missing metric resolution row: {display_name}")


def _missing_resolution_saas_snapshot(**overrides) -> dict:
    snapshot = {
        "ticker": "NOW",
        "sector": "Technology",
        "industry": "Software - Application",
        "revenue_growth": 0.22,
        "gross_margin": 0.80,
        "operating_margin": 0.25,
        "free_cash_flow": 3_200,
        "total_revenue": 10_000,
        "price_to_sales": 7,
        "price_to_fcf": 24,
        "free_cash_flow_yield": 0.04,
        "total_debt": 4_000,
        "total_cash": 2_000,
        "ebitda": 2_500,
        "ebit": 2_000,
        "interest_expense": 100,
    }
    snapshot.update(overrides)
    return snapshot


def _missing_resolution_technicals() -> dict:
    return {
        "price": 100,
        "ema20": 98,
        "ema50": 96,
        "ema200": 92,
        "rsi14": 50,
        "drawdown_from_high_pct": -20,
        "gain_20d_pct": 1,
        "gain_60d_pct": -2,
        "fifty_two_week_low": 70,
    }


def _review_queue_snapshots() -> dict[str, dict]:
    return {
        "NOW": {
            "ticker": "NOW",
            "sector": "Technology",
            "industry": "Software - Application",
            "revenue_growth": 0.18,
            "gross_margin": 0.78,
            "operating_margin": 0.22,
            "free_cash_flow": 3_200,
            "total_revenue": 10_000,
            "stock_based_compensation": 900,
            "total_debt": 4_000,
            "total_cash": 2_000,
            "ebitda": 2_500,
            "price_to_sales": 7,
            "price_to_fcf": 24,
            "free_cash_flow_yield": 0.04,
        },
        "MSFT": {
            "ticker": "MSFT",
            "sector": "Technology",
            "industry": "Software - Infrastructure",
            "revenue_growth": 0.12,
            "operating_margin": 0.42,
            "free_cash_flow": 50_000,
            "total_revenue": 100_000,
            "return_on_invested_capital": 0.25,
            "total_cash": 100_000,
            "total_debt": 40_000,
            "forward_pe": 30,
            "price_to_fcf": 28,
            "free_cash_flow_yield": 0.035,
        },
        "VST": {
            "ticker": "VST",
            "sector": "Utilities",
            "industry": "Power Generation",
            "market_cap": 40_000_000_000,
            "enterprise_value": 55_000_000_000,
            "ebitda": 5_500_000_000,
            "free_cash_flow": 4_000_000_000,
            "net_debt_to_ebitda": 3.4,
            "enterprise_to_ebitda": 10,
            "current_ratio": 1.1,
        },
        "COIN": {
            "ticker": "COIN",
            "sector": "Financial Services",
            "industry": "Capital Markets",
            "revenue_growth": 0.18,
            "operating_margin": 0.18,
            "free_cash_flow": 2_000_000_000,
            "total_revenue": 6_000_000_000,
            "total_cash": 7_000_000_000,
            "total_debt": 3_000_000_000,
            "price_to_sales": 8,
        },
        "JPM": {
            "ticker": "JPM",
            "sector": "Financial Services",
            "industry": "Banks",
            "return_on_equity": 0.14,
            "return_on_assets": 0.012,
            "price_to_book": 1.4,
            "forward_pe": 11,
        },
    }


class ProviderTests(unittest.TestCase):
    def test_fmp_rate_limit_uses_safe_backend_queue_settings(self) -> None:
        self.assertEqual(FMP_RATE_LIMIT["plan"], "starter")
        self.assertEqual(FMP_RATE_LIMIT["max_per_minute"], 300)
        self.assertEqual(FMP_RATE_LIMIT["safe_per_second"], 4)
        self.assertEqual(FMP_RATE_LIMIT["burst_per_minute"], 240)

    def test_fmp_cache_ttl_policy_matches_backend_expectations(self) -> None:
        self.assertEqual(CACHE_TTL_SECONDS["quote"], 5 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["profile"], 7 * 24 * 60 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["financials"], 7 * 24 * 60 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["ratios"], 7 * 24 * 60 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["keyMetrics"], 7 * 24 * 60 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["historicalPrice"], 24 * 60 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["news"], 30 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["analystEstimates"], 24 * 60 * 60)
        self.assertEqual(CACHE_TTL_SECONDS["scores"], 24 * 60 * 60)

    def test_fmp_endpoints_map_to_expected_ttl_buckets(self) -> None:
        self.assertEqual(ttl_bucket_for_endpoint("quote"), "quote")
        self.assertEqual(ttl_bucket_for_endpoint("profile"), "profile")
        self.assertEqual(ttl_bucket_for_endpoint("income-statement"), "financials")
        self.assertEqual(ttl_bucket_for_endpoint("ratios-ttm"), "ratios")
        self.assertEqual(ttl_bucket_for_endpoint("key-metrics-ttm"), "keyMetrics")
        self.assertEqual(ttl_bucket_for_endpoint("historical-price-eod/full"), "historicalPrice")
        self.assertEqual(ttl_bucket_for_endpoint("analyst-estimates"), "analystEstimates")

    def test_fmp_provider_implements_market_data_interface(self) -> None:
        self.assertTrue(issubclass(FMPProvider, MarketDataProvider))

    def test_placeholder_provider_factory_returns_market_data_interfaces(self) -> None:
        self.assertIsInstance(get_market_data_provider(), FMPProvider)
        self.assertIsInstance(get_market_data_provider("fmp"), FMPProvider)
        self.assertIsInstance(get_market_data_provider("polygon"), PolygonProvider)
        self.assertIsInstance(get_market_data_provider("sec"), SECEdgarProvider)

    def test_placeholder_providers_fail_loudly_without_fake_data(self) -> None:
        for provider in [PolygonProvider(), SECEdgarProvider()]:
            with self.assertRaises(NotImplementedError):
                provider.get_quote("MSFT")

    def test_removed_provider_names_are_not_available(self) -> None:
        with self.assertRaises(ValueError):
            get_market_data_provider("removed-provider")

    def test_fmp_provider_requires_api_key(self) -> None:
        provider = FMPProvider(api_key=None)
        provider.api_key = None
        with self.assertRaises(RuntimeError):
            provider.get_quote("MSFT", force_refresh=True)

    def test_qwen_health_check_missing_key_is_safe(self) -> None:
        client = QwenClient(
            settings=QwenSettings(
                api_key=None,
                base_url=DEFAULT_QWEN_BASE_URL,
                model="qwen-flash",
                second_model="qwen-plus",
            )
        )

        result = qwen_health_check(client)

        self.assertFalse(result["configured"])
        self.assertEqual(result["result"], "missing")
        self.assertEqual(result["error"], "Qwen API key not configured")

    def test_qwen_base_url_defaults_and_strips_chat_completion_suffix(self) -> None:
        old_base = os.environ.pop("QWEN_BASE_URL", None)
        try:
            settings = qwen_settings_from_env()
            self.assertEqual(settings.base_url, DEFAULT_QWEN_BASE_URL)
            self.assertEqual(
                normalize_qwen_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
                DEFAULT_QWEN_BASE_URL,
            )
        finally:
            if old_base is not None:
                os.environ["QWEN_BASE_URL"] = old_base

    def test_qwen_health_check_handles_non_json_output(self) -> None:
        def fake_transport(url, payload, headers, timeout):
            return {"choices": [{"message": {"content": "not json"}}]}

        client = QwenClient(
            settings=QwenSettings("test-key", DEFAULT_QWEN_BASE_URL, "qwen-flash", "qwen-plus"),
            transport=fake_transport,
        )

        result = qwen_health_check(client)

        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["error"], "json_parse_failed")

    def test_qwen_health_check_handles_provider_error(self) -> None:
        def fake_transport(url, payload, headers, timeout):
            raise QwenProviderError("provider down")

        client = QwenClient(
            settings=QwenSettings("test-key", DEFAULT_QWEN_BASE_URL, "qwen-flash", "qwen-plus", max_retries=0),
            transport=fake_transport,
        )

        result = qwen_health_check(client)

        self.assertEqual(result["result"], "failed")
        self.assertIn("provider_error", result["error"])

    def test_qwen_health_check_success_and_prompt_is_evidence_only(self) -> None:
        captured = {}

        def fake_transport(url, payload, headers, timeout):
            captured["url"] = url
            captured["payload"] = payload
            captured["authorization"] = headers["Authorization"]
            return {"choices": [{"message": {"content": '{"status":"ok","provider":"qwen"}'}}]}

        client = QwenClient(
            settings=QwenSettings("sk-test-secret-value", DEFAULT_QWEN_BASE_URL, "qwen-flash", "qwen-plus"),
            transport=fake_transport,
        )

        result = qwen_health_check(client)

        self.assertEqual(result["result"], "ok")
        self.assertEqual(captured["url"], f"{DEFAULT_QWEN_BASE_URL}/chat/completions")
        self.assertEqual(captured["payload"]["model"], "qwen-flash")
        self.assertIn(EVIDENCE_ONLY_RULE, captured["payload"]["messages"][0]["content"])
        self.assertEqual(captured["payload"]["response_format"]["type"], "json_schema")

    def test_qwen_health_check_report_masks_api_key(self) -> None:
        secret = "sk-1234567890abcdef"
        report = format_health_check_report(
            {
                "configured": True,
                "base_url": DEFAULT_QWEN_BASE_URL,
                "model": "qwen-flash",
                "api_key": mask_api_key(secret),
                "result": "ok",
                "error": None,
            }
        )

        self.assertIn("sk-1...cdef", report)
        self.assertNotIn(secret, report)

    def test_qwen_settings_can_read_local_dotenv_without_printing_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "QWEN_API_KEY=sk-local-secret-value",
                        "QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "QWEN_MODEL=qwen-flash",
                    ]
                ),
                encoding="utf-8",
            )

            values = _read_local_dotenv(str(env_path))

            self.assertEqual(values["QWEN_MODEL"], "qwen-flash")
            self.assertEqual(mask_api_key(values["QWEN_API_KEY"]), "sk-l...alue")

    def test_qwen_review_prompt_is_evidence_only(self) -> None:
        self.assertIn("不允许使用模型自身知识", QWEN_REVIEW_SYSTEM_PROMPT)
        self.assertIn("不允许自己查最新资料", QWEN_REVIEW_SYSTEM_PROMPT)
        self.assertIn("只能根据 extractedText", QWEN_REVIEW_SYSTEM_PROMPT)

    def test_qwen_review_cost_guard_excludes_calculated_and_terminal_items(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            calculated = _insert_review_item(
                store,
                metric_key="fcfMargin",
                item_type="extracted_value",
                source_type="CALCULATED",
                resolution_status="calculated",
            )
            approved = _insert_review_item(store, metric_key="rpoGrowth", item_type="extracted_value")
            store.update_review_status(int(approved["id"]), "approved")
            pending = _insert_review_item(store, metric_key="subscriptionRevenueGrowth", item_type="extracted_value")

            candidates = qwen_review_candidates(store.list_items())
            ids = {int(row["id"]) for row in candidates}

            self.assertIn(int(pending["id"]), ids)
            self.assertNotIn(int(calculated["id"]), ids)
            self.assertNotIn(int(approved["id"]), ids)

    def test_qwen_missing_kpi_is_not_sent_to_evidence_validation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            item = _insert_review_item(store, item_type="missing_kpi", affects="Quality")
            client = _FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.99))
            service = QwenReviewService(queue_store=store, ai_store=AIReviewStore(store.path), client=client)

            run = service.review_rows([item])
            eligible, reason = qwen_review_eligibility(item)

            self.assertFalse(eligible)
            self.assertEqual(reason, "unsupported_item_type")
            self.assertEqual(run.reviewed, 0)
            self.assertEqual(client.calls, 0)

    def test_qwen_extracted_value_without_text_is_not_called(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            store.upsert_item(
                {
                    "symbol": "NOW",
                    "metricKey": "subscriptionRevenueGrowth",
                    "displayName": "subscriptionRevenueGrowth",
                    "itemType": "extracted_value",
                    "value": 20,
                    "unit": "percent",
                    "period": "Q1 2026",
                    "sourceType": "IR_RELEASE",
                    "sourceUrl": "https://example.com/source",
                    "sourceDocumentTitle": "Earnings Release",
                    "extractedText": "",
                    "evidenceText": "",
                    "confidence": "medium",
                    "affects": "Quality",
                    "reviewStatus": "pending_review",
                    "resolutionStatus": "available",
                }
            )
            row = store.list_items(symbol="NOW")[0]
            client = _FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.99))
            service = QwenReviewService(queue_store=store, ai_store=AIReviewStore(store.path), client=client)

            run = service.review_rows([row])
            eligible, reason = qwen_review_eligibility(row)

            self.assertFalse(eligible)
            self.assertIn(reason, {"missing_evidence_text", "unsupported_item_type", "status_not_pending_review"})
            self.assertEqual(run.reviewed, 0)
            self.assertEqual(client.calls, 0)

    def test_qwen_period_uses_metric_period_not_filing_date(self) -> None:
        row = {
            "symbol": "NOW",
            "metricKey": "cRPO growth",
            "displayName": "cRPO growth",
            "value": 25,
            "unit": "percent",
            "period": "2026-01-28",
            "sourceType": "IR_RELEASE",
            "sourceDocumentTitle": "NOW Q4 2025 Earnings Release",
            "extractedText": "In Q4 2025, cRPO growth was 25% year-over-year.",
            "confidence": "medium",
            "affects": "Quality",
            "itemType": "extracted_value",
            "reviewStatus": "pending_review",
            "resolutionStatus": "available",
        }

        periods = normalize_metric_period(row)
        payload = build_qwen_review_input(row)
        guarded = validate_qwen_review_result(
            {
                **_qwen_review_result("recommend_approve", period_match="mismatch", confidence=0.95),
                "evidenceQuote": "In Q4 2025, cRPO growth was 25% year-over-year.",
            }
        )

        self.assertEqual(periods.sourcePublishedDate, "2026-01-28")
        self.assertEqual(periods.metricPeriod, "2025 Q4")
        self.assertEqual(payload["periodDisplay"], "2025 Q4")
        self.assertEqual(payload["deterministicPrecheck"], "exact")
        self.assertEqual(enforce_qwen_evidence_only(row, guarded)["periodMatch"], "exact")

    def test_metric_percent_values_are_normalized_for_qwen(self) -> None:
        self.assertEqual(normalize_metric_value("25%", "percent").displayValue, "25.0%")
        self.assertEqual(normalize_metric_value(25.0, "percent").displayValue, "25.0%")
        self.assertEqual(normalize_metric_value(0.25, "percent").displayValue, "25.0%")

    def test_deterministic_precheck_exact_can_machine_verify(self) -> None:
        row = {
            "metricKey": "rpoGrowth",
            "displayName": "RPO growth",
            "value": 25,
            "unit": "percent",
            "metricPeriod": "2025 Q4",
            "evidenceWindow": "RPO growth was 25% in Q4 2025.",
        }

        self.assertEqual(deterministic_precheck(row), "exact")

    def test_qwen_non_json_output_becomes_human_review(self) -> None:
        result = parse_qwen_review_json("not json")

        self.assertEqual(result["aiDecision"], "needs_human_review")
        self.assertEqual(result["evidenceMatch"], "no_evidence")
        self.assertIn("json_parse_failed", result["warnings"])

    def test_qwen_json_repair_once_handles_code_fence(self) -> None:
        result = parse_qwen_review_json("```json\n" + json.dumps(_qwen_review_result("not_enough_evidence")) + "\n```")

        self.assertEqual(result["aiDecision"], "not_enough_evidence")

    def test_qwen_mismatch_cannot_auto_confirm(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _qwen_review_result("recommend_reject", evidence_match="mismatch", confidence=0.99)

            action = apply_qwen_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "ai_recommend_reject")
            self.assertEqual(updated["reviewStatus"], "pending_review")

    def test_qwen_qualitative_risk_is_not_sent_to_evidence_validation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "qwen.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="qualitative_risk", affects="Risk")
            client = _FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.99))
            service = QwenReviewService(queue_store=store, ai_store=ai_store, client=client)

            run = service.review_rows([item])
            updated = store.list_items(symbol="NOW")[0]
            latest = ai_store.latest_for_item(int(item["id"]))

            self.assertEqual(run.reviewed, 0)
            self.assertEqual(client.calls, 0)
            self.assertEqual(updated["reviewStatus"], "pending_review")
            self.assertIsNone(latest)

    def test_qwen_exact_match_can_auto_approve_extracted_value(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "qwen.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            client = _FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.95))
            service = QwenReviewService(queue_store=store, ai_store=ai_store, client=client)

            run = service.review_rows([item])
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(run.auto_approved, 1)
            self.assertEqual(updated["reviewStatus"], "approved")

    def test_qwen_auto_approval_records_triage_and_audit_log(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "qwen.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            client = _FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.95))
            service = QwenReviewService(queue_store=store, ai_store=ai_store, client=client)

            service.review_rows([item])
            updated = store.list_items(symbol="NOW")[0]
            latest = ai_store.latest_for_item(int(item["id"]))
            logs = ai_store.list_audit_logs(int(item["id"]))

            self.assertEqual(updated["reviewStatus"], "approved")
            self.assertEqual(updated["aiTriageStatus"], "auto_approved_by_ai")
            self.assertEqual(updated["approvedBy"], "ai")
            self.assertEqual(latest["aiTriageStatus"], "auto_approved_by_ai")
            self.assertEqual(logs[0]["action"], "auto_approved_by_ai")

    def test_qwen_transcript_source_cannot_auto_approve(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality", source_type="FMP_TRANSCRIPT")
            result = _qwen_review_result("recommend_approve", confidence=0.99)

            action = apply_qwen_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "ai_needs_human_review")
            self.assertEqual(updated["reviewStatus"], "pending_review")

    def test_qwen_recommend_correct_creates_candidate_without_overwrite(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality", value=20)
            result = _qwen_review_result("recommend_correct", corrected_value=22, confidence=0.93)

            action = apply_qwen_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "ai_recommend_correct")
            self.assertEqual(updated["reviewStatus"], "pending_review")
            self.assertEqual(updated["value"], 20)
            self.assertIn("correctedValue", updated["correctionCandidate"])

    def test_qwen_not_enough_evidence_keeps_item_out_of_scoring(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _qwen_review_result("not_enough_evidence", evidence_match="no_evidence", confidence=0.2)

            action = apply_qwen_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "ai_not_enough_evidence")
            self.assertEqual(updated["reviewStatus"], "pending_review")

    def test_qwen_accept_ai_correction_writes_audit_and_manual_status(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "qwen.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality", value=20)
            result = _qwen_review_result("recommend_correct", corrected_value=22, confidence=0.93)

            action = apply_qwen_review_result(item, result, store)
            ai_result_id = ai_store.save_result(item, {**result, "hallucinationRisk": False}, "qwen:test", "hash-correct", action)
            old, new = store.accept_ai_correction(int(item["id"]), result, ai_result_id)
            ai_store.log_audit(
                int(item["id"]),
                "accept_ai_correction",
                old.get("reviewStatus") if old else None,
                new.get("reviewStatus") if new else None,
                old.get("value") if old else None,
                new.get("value") if new else None,
                "local_user",
                ai_result_id,
                "test",
            )

            updated = store.list_items(symbol="NOW")[0]
            self.assertEqual(updated["reviewStatus"], "manually_corrected")
            self.assertEqual(updated["sourceType"], "AI_ASSISTED_MANUAL_CORRECTION")
            self.assertEqual(updated["value"], 22)
            self.assertEqual(ai_store.list_audit_logs(int(item["id"]))[0]["action"], "accept_ai_correction")

    def test_qwen_accept_ai_reject_writes_audit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "qwen.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _qwen_review_result("recommend_reject", evidence_match="mismatch", confidence=0.99)
            action = apply_qwen_review_result(item, result, store)
            ai_result_id = ai_store.save_result(item, {**result, "hallucinationRisk": False}, "qwen:test", "hash-reject", action)

            old, new = store.accept_ai_reject(int(item["id"]), ai_result_id, "test")
            ai_store.log_audit(
                int(item["id"]),
                "accept_ai_reject",
                old.get("reviewStatus") if old else None,
                new.get("reviewStatus") if new else None,
                old.get("value") if old else None,
                new.get("value") if new else None,
                "user_after_ai_recommendation",
                ai_result_id,
                "test",
            )

            updated = store.list_items(symbol="NOW")[0]
            self.assertEqual(updated["reviewStatus"], "rejected")
            self.assertEqual(updated["rejectedBy"], "user_after_ai_recommendation")
            self.assertEqual(ai_store.list_audit_logs(int(item["id"]))[0]["action"], "accept_ai_reject")

    def test_qwen_batch_accept_only_allows_auto_approved_items(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "qwen.sqlite")
            auto_item = _insert_review_item(store, metric_key="subscriptionRevenueGrowth", item_type="extracted_value")
            reject_item = _insert_review_item(store, metric_key="rpoGrowth", item_type="extracted_value")
            store.set_ai_triage(int(auto_item["id"]), "auto_approved_by_ai")
            store.set_ai_triage(int(reject_item["id"]), "ai_recommend_reject")

            store.batch_accept_ai_auto_approved([int(auto_item["id"]), int(reject_item["id"])])
            rows = {row["metricKey"]: row for row in store.list_items()}

            self.assertEqual(rows["subscriptionRevenueGrowth"]["reviewStatus"], "approved")
            self.assertEqual(rows["rpoGrowth"]["reviewStatus"], "pending_review")

    def test_ai_abnormal_metrics_cap_data_confidence_below_high(self) -> None:
        enriched = enrich_data_confidence(
            {
                "ticker": "NOW",
                "modelType": "SAAS_SOFTWARE",
                "forward_revenue_growth": 0.2,
                "operating_margin": 0.2,
                "fcf_margin": 0.3,
                "manualSbcRatio": 0.1,
                "manualNetDebtToAdjustedEbitda": 0.5,
                "interest_coverage": 10,
                "manualSubscriptionRevenueGrowth": 0.2,
                "manualRpoGrowth": 0.2,
                "manualNonGaapOperatingMargin": 0.3,
                "manualNetRetention": 1.1,
                "manualLargeCustomerGrowth": 0.2,
                "peg_ratio": 1.5,
                "criticalAiAbnormalMetrics": ["订阅收入增速"],
            }
        )

        self.assertEqual(enriched["dataConfidence"], "medium")

    def test_qwen_input_hash_prevents_repeat_calls(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "qwen.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            client = _FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.95))
            service = QwenReviewService(queue_store=store, ai_store=ai_store, client=client)

            first = service.review_rows([item])
            second = service.review_rows([item])

            self.assertEqual(first.reviewed, 1)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(client.calls, 1)

    def test_qwen_review_ui_uses_qwen_specific_controls(self) -> None:
        source = inspect.getsource(manual_review)

        self.assertIn("仅运行 Qwen 证据复核", source)
        self.assertIn("一键自动处理当前筛选结果", source)
        self.assertNotIn("AI模式", source)
        self.assertIn("provider=qwen", source)

    def test_stock_manual_overrides_support_power_company_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache = FundamentalCache(Path(tmpdir) / "cache.sqlite")
            self.assertEqual(cache.get_manual_power_overrides("VST"), {"modelType": "POWER_GENERATION"})

            cache.set_manual_power_overrides(
                "VST",
                manual_adjusted_ebitda=5_500_000_000,
                manual_adjusted_fcf_before_growth=4_000_000_000,
                manual_net_debt_to_adjusted_ebitda=3.4,
                manual_hedge_coverage_current_year=0.80,
                manual_hedge_coverage_next_year=0.65,
                manual_buyback_amount=2_000_000_000,
                manual_share_count_reduction=0.04,
                manual_narrative_notes="Power demand exposure under review.",
            )
            overrides = cache.get_manual_power_overrides("VST")
            self.assertEqual(overrides["manualAdjustedEbitda"], 5_500_000_000)
            self.assertEqual(overrides["manualAdjustedFcfBeforeGrowth"], 4_000_000_000)
            self.assertEqual(overrides["manualNetDebtToAdjustedEbitda"], 3.4)
            self.assertEqual(overrides["manualHedgeCoverageCurrentYear"], 0.80)
            self.assertEqual(overrides["manualHedgeCoverageNextYear"], 0.65)
            self.assertEqual(overrides["manualBuybackAmount"], 2_000_000_000)
            self.assertEqual(overrides["manualShareCountReduction"], 0.04)
            self.assertEqual(overrides["manualNarrativeNotes"], "Power demand exposure under review.")

            cache.set_manual_overrides(
                "PLD",
                modelType="REIT_REAL_ESTATE",
                manualAffo=4_000_000_000,
                manualAffoGrowth=0.04,
                manualOccupancy=0.96,
            )
            reit_overrides = cache.get_manual_overrides("PLD")
            self.assertEqual(reit_overrides["modelType"], "REIT_REAL_ESTATE")
            self.assertEqual(reit_overrides["manualAffo"], 4_000_000_000)
            self.assertEqual(reit_overrides["manualOccupancy"], 0.96)


class FormattingTests(unittest.TestCase):
    def test_missing_financial_values_display_as_na(self) -> None:
        self.assertEqual(format_currency(None), "N/A")
        self.assertEqual(format_large_number(float("nan")), "N/A")
        self.assertEqual(format_percent(None), "N/A")
        self.assertEqual(format_multiple(float("nan")), "N/A")


class DashboardLayoutTests(unittest.TestCase):
    def _dashboard_score(self, **overrides):
        defaults = {
            "risk_flags": [],
            "trading_signals": [],
            "scoring_model": "GENERIC",
            "quality_rating": "A",
            "entry_rating": "A",
            "risk_rating": "低",
            "valuation_status": "击球区附近",
            "action": "可小仓分批",
            "max_suggested_position_percent": 5,
            "max_portfolio_weight_percent": 15,
            "current_add_limit_percent": 5,
            "data_confidence": "high",
            "proxy_confidence": "high",
            "data_insufficient": False,
            "data_quality_pct": 100,
            "missing_data": [],
            "missing_industry_metrics": [],
            "proxy_metrics_used": [],
            "missing_metric_impacts": [],
            "metric_resolution_statuses": [],
            "human_readable_summary": {},
            "active_risk_drivers": [],
            "missing_data_explanation": [],
            "rating_cap": None,
            "key_positives": [],
            "key_risks": [],
            "total_score": 80,
            "value_zone": "击球区附近",
            "rating": "A",
            "overheat_score": 0,
            "overheat_status": "正常",
            "overheat_action": "正常评估",
            "overheat_recommendation": "",
            "overheat_reasons": [],
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_default_dashboard_columns_are_decision_allowlist(self) -> None:
        labels = [column["label"] for column in DASHBOARD_COLUMNS]
        self.assertEqual(
            labels,
            [
                "代码",
                "现价",
                "市值",
                "质量",
                "买点",
                "风险",
                "估值状态",
                "操作建议",
                "当前新增",
                "数据状态",
                "操作",
            ],
        )

        hidden_default_columns = {
            "52周高点",
            "52周低点",
            "RSI14",
            "EMA20",
            "EMA50",
            "EMA200",
            "20日涨幅",
            "TTM市盈率",
            "预期市盈率",
            "市销率",
            "EV/销售额",
            "P/FCF",
            "FCF收益率",
            "收入增速",
            "经营利润率",
            "ROIC",
            "净债务/EBITDA",
            "流动比率",
            "数据完整度",
            "距高点回撤",
        }
        self.assertTrue(hidden_default_columns.isdisjoint(labels))

    def test_dashboard_loading_uses_productized_shell_not_streamlit_spinner(self) -> None:
        source = inspect.getsource(__import__("ui.dashboard", fromlist=[""]))

        self.assertIn("show_spinner=False", source)
        self.assertNotIn("st.spinner", source)
        self.assertIn("terminal-loading-shell", _loading_shell_html("读取本地缓存", "准备评分"))
        self.assertIn("terminal-refresh-card", _refresh_progress_html("更新数据源", "正在更新 NOW", 1, 4, "NOW"))

    def test_dashboard_header_uses_clean_command_bar(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        source = inspect.getsource(dashboard_module._render_dashboard_header)

        self.assertNotIn("dashboard_refresh_symbol_choice", source)
        self.assertNotIn("更新单只", source)
        self.assertIn("重新评分", source)
        self.assertIn("更新观察池", source)
        self.assertIn("更多 ▾", source)
        self.assertIn("视图设置", source)
        self.assertIn("dashboard_density", source)
        self.assertNotIn("dashboard_density", source.split('with st.popover("更多 ▾"', 1)[0])
        self.assertIn("强制刷新 FMP 缓存", source)
        self.assertIn("运行缺失数据补全", source)
        self.assertIn("重置本地缓存", source)
        self.assertNotIn("更新全部观察池", source)

    def test_dashboard_single_stock_refresh_does_not_clear_full_cache(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        row_action_source = inspect.getsource(dashboard_module._render_row_action_menu)
        render_source = inspect.getsource(dashboard_module.render)

        self.assertIn("dashboard_force_fmp_refresh_symbol", row_action_source)
        self.assertNotIn("st.cache_data.clear()", row_action_source)
        self.assertIn("_refresh_single_dashboard_row", render_source)
        self.assertIn("_store_session_dashboard_table", inspect.getsource(dashboard_module._refresh_single_dashboard_row))

    def test_dashboard_single_refresh_replaces_only_matching_row(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        table = pd.DataFrame(
            [
                {"symbol": "NOW", "price": "$100.00", "qualityRating": "B"},
                {"symbol": "MSFT", "price": "$400.00", "qualityRating": "A"},
            ]
        )

        updated = dashboard_module._replace_dashboard_row(
            table,
            {"symbol": "NOW", "price": "$101.00", "qualityRating": "B+"},
        )

        self.assertEqual(len(updated), 2)
        self.assertEqual(updated.loc[updated["symbol"] == "NOW", "price"].iloc[0], "$101.00")
        self.assertEqual(updated.loc[updated["symbol"] == "MSFT", "price"].iloc[0], "$400.00")

    def test_dashboard_single_refresh_preserves_list_and_dict_fields(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        table = pd.DataFrame(
            [
                {"symbol": "NOW", "price": "$100.00", "overheatReasons": ["旧原因"]},
                {"symbol": "MSFT", "price": "$400.00", "overheatReasons": ["保持"]},
            ]
        )
        refreshed_payload = {
            "symbol": "NOW",
            "price": "$101.00",
            "overheatReasons": ["RSI14 高于 70", "20日涨幅偏热"],
            "humanReadableSummary": {"entry": "只观察"},
        }

        updated = dashboard_module._replace_dashboard_row(table, refreshed_payload)
        now_row = updated.loc[updated["symbol"] == "NOW"].iloc[0]
        msft_row = updated.loc[updated["symbol"] == "MSFT"].iloc[0]

        self.assertEqual(now_row["overheatReasons"], ["RSI14 高于 70", "20日涨幅偏热"])
        self.assertEqual(now_row["humanReadableSummary"], {"entry": "只观察"})
        self.assertEqual(msft_row["overheatReasons"], ["保持"])

    def test_dashboard_row_expands_final_decision_fields_without_dropping_legacy_action(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        row = dashboard_module._build_dashboard_row(
            "VST",
            {
                "ticker": "VST",
                "current_price": 100,
                "price_to_fcf": 18,
                "free_cash_flow_yield": 0.06,
                "market_cap": 100_000_000_000,
                "free_cash_flow": 6_000_000_000,
            },
            {"price": 100},
            self._dashboard_score(
                action="可小仓分批",
                valuation_status="只观察",
                entry_rating="C - 只观察",
                current_add_limit_percent=5,
                max_portfolio_weight_percent=20,
            ),
            {"pct": 100, "missing": []},
        )

        self.assertEqual(row["action"], "可小仓分批")
        self.assertEqual(row["finalAction"], "只观察")
        self.assertEqual(row["decisionLane"], "wait")
        self.assertEqual(row["displayCategory"], "等回踩")
        self.assertFalse(row["isActionable"])
        self.assertEqual(row["currentAddLimitPercent"], 0)
        self.assertEqual(row["currentAddLimit"], "不建议新增")

    def test_dashboard_actionable_rows_prefer_final_decision(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        table = pd.DataFrame(
            [
                {
                    "symbol": "OLD",
                    "action": "可小仓分批",
                    "finalAction": "只观察",
                    "decisionLane": "wait",
                    "isActionable": False,
                    "dataConfidence": "high",
                    "totalScore": 90,
                },
                {
                    "symbol": "BUY",
                    "action": "只观察",
                    "finalAction": "可小仓分批",
                    "decisionLane": "actionable",
                    "isActionable": True,
                    "dataConfidence": "high",
                    "totalScore": 80,
                },
            ]
        )

        rows = dashboard_module._actionable_rows(table)

        self.assertEqual([row["symbol"] for row in rows], ["BUY"])

    def test_dashboard_near_buy_zone_keeps_wait_lane_rows_when_valuation_is_near(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        table = pd.DataFrame(
            [
                {
                    "symbol": "NEAR",
                    "finalAction": "等回踩",
                    "decisionLane": "wait",
                    "isActionable": False,
                    "valuationStatus": "击球区附近",
                    "totalScore": 80,
                }
            ]
        )

        groups = dashboard_module._summary_lane_groups(table)
        near_symbols = [row["symbol"] for key, _title, _subtitle, rows, _color in groups if key == "nearBuyZone" for row in rows]
        wait_symbols = [row["symbol"] for key, _title, _subtitle, rows, _color in groups if key == "waitOrReview" for row in rows]

        self.assertEqual(near_symbols, ["NEAR"])
        self.assertEqual(wait_symbols, [])

    def test_dashboard_action_cell_prefers_final_action_and_final_add_limit(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        html = dashboard_module._decision_table_cell_html(
            pd.Series(
                {
                    "action": "可小仓分批",
                    "finalAction": "只观察",
                    "valuationStatus": "只观察",
                    "maxSuggestedPosition": "≤5%",
                    "currentAddLimit": "不建议新增",
                }
            ),
            {"key": "actionSummary"},
            "VST",
        )

        self.assertIn("只观察", html)
        self.assertIn("不建议新增", html)
        self.assertNotIn("可小仓", html)

    def test_stock_detail_drawer_has_fixed_close_control(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        drawer_source = inspect.getsource(dashboard_module._drawer_html)
        styles_source = inspect.getsource(dashboard_module._render_dashboard_styles)

        self.assertIn("关闭右侧详情面板", drawer_source)
        self.assertIn("drawer-close-link", drawer_source)
        self.assertIn("data-dashboard-drawer-close", drawer_source)
        self.assertNotIn("closeDrawer=1", drawer_source)
        self.assertIn(".drawer-close-link", styles_source)
        self.assertIn("position: fixed", styles_source)
        self.assertIn("z-index: 2147483001", styles_source)
        self.assertNotIn("dashboard_close_drawer", drawer_source)
        self.assertNotIn(".st-key-dashboard_close_drawer", styles_source)
        self.assertNotIn("body:has(.st-key-dashboard_close_drawer button:active) .stock-drawer", styles_source)
        self.assertNotIn("body:has(.st-key-dashboard_close_drawer button[disabled])", styles_source)
        self.assertNotIn("body:has(.st-key-dashboard_close_drawer button:disabled)", styles_source)

    def test_drawer_actions_do_not_run_backend_tasks_from_dashboard(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        drawer_source = inspect.getsource(dashboard_module._render_stock_detail_drawer)
        render_source = inspect.getsource(dashboard_module.render)
        actions_source = inspect.getsource(dashboard_module._drawer_review_action_bar_html)

        self.assertNotIn("dashboard_pending_data_fill", drawer_source)
        self.assertNotIn("_run_pending_data_fill", render_source)
        self.assertNotIn("drawerAction", actions_source)
        self.assertNotIn("st.rerun()", drawer_source)
        self.assertNotIn('target="_blank"', actions_source)
        self.assertNotIn("?page=detail", actions_source)
        self.assertIn("data-dashboard-drawer-message", actions_source)
        self.assertIn("data-dashboard-drawer-action-note", actions_source)

    def test_dashboard_drawer_open_close_is_client_side(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        render_source = inspect.getsource(dashboard_module.render)
        payload_source = inspect.getsource(dashboard_module._render_client_stock_detail_drawers)
        row_action_source = inspect.getsource(dashboard_module._render_row_action_menu)
        open_helper_source = inspect.getsource(dashboard_module._drawer_open_menu_html)

        self.assertIn("_render_client_stock_detail_drawers(table)", render_source)
        self.assertNotIn("_render_stock_detail_drawer(table)", render_source)
        self.assertIn("components.html", payload_source)
        self.assertIn("__dashboardDrawerPayload", payload_source)
        self.assertIn("event.preventDefault()", payload_source)
        self.assertIn("_drawer_open_menu_html", row_action_source)
        self.assertIn("data-dashboard-drawer-open", open_helper_source)
        self.assertIn("data-dashboard-drawer-focus", open_helper_source)
        self.assertNotIn("dashboard_drawer_symbol", row_action_source)
        self.assertNotIn("pending_app_page", row_action_source)
        self.assertNotIn('st.query_params["page"]', row_action_source)

    def test_fixed_sidebar_uses_dom_injection_not_markdown_html_block(self) -> None:
        source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("components.html", source)
        self.assertIn("zhx-fixed-sidebar-root", source)
        self.assertNotIn('st.markdown("\\n".join(sidebar_html)', source)

    def test_decision_lanes_use_compact_grid_and_truncated_reason(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        styles_source = inspect.getsource(dashboard_module._render_dashboard_styles)

        self.assertIn("grid-template-columns: 52px auto auto minmax(0, 1fr)", styles_source)
        self.assertIn("height: 32px", styles_source)
        self.assertIn("text-overflow: ellipsis", styles_source)
        self.assertIn("white-space: nowrap", styles_source)
        self.assertIn("text-decoration: none !important", styles_source)
        self.assertNotIn("grid-template-areas", styles_source)

    def test_decision_lane_uses_short_reason_with_drawer_link(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        row = pd.Series(
            {
                "symbol": "NVDA",
                "valuationStatus": "合理偏贵",
                "action": "只观察",
                "overheatReasons": ["今日下跌只是短期冷却，不等于进入击球区"],
                "overheatScore": 65,
            }
        )

        html = dashboard_module._lane_item_html(row)

        self.assertIn('class="lane-item"', html)
        self.assertIn('href="#"', html)
        self.assertIn('data-dashboard-drawer-open="NVDA"', html)
        self.assertNotIn("?page=dashboard&drawer=NVDA", html)
        self.assertIn("短期冷却，未到买点", html)
        self.assertIn("今日下跌只是短期冷却，不等于进入击球区", html)
        self.assertNotIn("text-decoration", html)

    def test_decision_lane_shortens_rsi_reason(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        row = pd.Series({"overheatReasons": ["RSI14 高于 70"], "overheatScore": 70})

        self.assertEqual(dashboard_module._lane_reason(row), "RSI仍偏热")

    def test_lane_footer_uses_local_focus_without_page_navigation(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        html = dashboard_module._lane_more_html("nearBuyZone", 7)

        self.assertIn("还有 7 只 · 查看全部", html)
        self.assertNotIn("href=", html)
        self.assertNotIn("lane=nearBuyZone", html)
        self.assertNotIn("#watchlist-table", html)
        self.assertIn("class=\"lane-more\"", html)

    def test_active_lane_filter_can_filter_watchlist_table(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        table = pd.DataFrame(
            [
                {"symbol": "NOW", "valuationStatus": "击球区附近", "action": "只观察", "totalScore": 70},
                {"symbol": "MSFT", "valuationStatus": "合理偏贵", "action": "只观察", "totalScore": 90},
                {"symbol": "NVDA", "valuationStatus": "极贵", "action": "禁止追高", "totalScore": 30, "overheatScore": 80},
            ]
        )

        dashboard_module.st.session_state[dashboard_module.LANE_FILTER_SESSION_KEY] = "nearBuyZone"
        try:
            filtered = dashboard_module._filtered_table_for_active_lane(table)
        finally:
            dashboard_module.st.session_state.pop(dashboard_module.LANE_FILTER_SESSION_KEY, None)

        self.assertEqual(filtered["symbol"].tolist(), ["NOW"])

    def test_summary_sections_cap_lanes_at_four_rows(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        source = inspect.getsource(dashboard_module._render_summary_sections)

        self.assertIn("rows[:4]", source)
        self.assertIn("_render_lane_more_button", source)

    def test_metric_resolution_groups_collapse_derived_and_qualitative_into_low_priority(self) -> None:
        groups = _metric_resolution_groups(
            [
                {"displayName": "Segment strength", "resolutionStatus": "derived_score"},
                {"displayName": "regulatory risk", "resolutionStatus": "semi_auto_low_confidence"},
            ]
        )

        self.assertIn("低优先级 / 仅解释项", groups)
        self.assertEqual(len(groups["低优先级 / 仅解释项"]), 2)

    def test_ui_metric_labels_hide_backend_enums(self) -> None:
        self.assertEqual(model_type_label("MEGA_CAP_PLATFORM"), "平台型科技巨头")
        self.assertEqual(resolution_status_label("manual_override_required"), "建议人工复核")
        self.assertEqual(metric_label("Segment strength"), "分部业务强度")
        self.assertEqual(metric_label("Buyback discipline"), "回购纪律")
        self.assertEqual(metric_label("EMA20"), "EMA20")
        self.assertEqual(metric_label("EMA50"), "EMA50")
        self.assertEqual(metric_label("EMA200"), "EMA200")
        self.assertEqual(metric_label("RSI14"), "RSI14")
        self.assertEqual(metric_label("RSI14 高于 70"), "RSI14 高于 70")
        self.assertEqual(metric_label("dilution risk"), "稀释风险")
        self.assertEqual(metric_label("acquisition integration risk"), "并购整合风险")
        self.assertEqual(metric_label("unknownBackendMetric"), "unknown Backend Metric")
        self.assertEqual(metric_label("unknownBackendMetric", debug=True), "未映射字段：unknownBackendMetric")
        self.assertIn("unknownBackendMetric", unmapped_metric_labels())

    def test_metric_label_map_covers_common_technical_and_financial_fields(self) -> None:
        self.assertEqual(metric_label("EMA20"), "EMA20")
        self.assertEqual(metric_label("EMA50"), "EMA50")
        self.assertEqual(metric_label("EMA200"), "EMA200")
        self.assertEqual(metric_label("RSI14"), "RSI14")
        self.assertEqual(metric_label("rsi14"), "RSI14")
        self.assertEqual(metric_label("drawdownFrom52WeekHigh"), "距52周高点回撤")
        self.assertEqual(metric_label("return20d"), "20日涨幅")
        self.assertEqual(metric_label("return60d"), "60日涨幅")
        self.assertEqual(metric_label("currentVolume / avgVolume20d - 1"), "成交量趋势")
        self.assertEqual(metric_label("currentPrice / closePrice20TradingDaysAgo - 1"), "20日涨幅")
        self.assertEqual(metric_label("currentPrice / closePrice60TradingDaysAgo - 1"), "60日涨幅")
        self.assertEqual(metric_label("currentPrice / fiftyTwoWeekHigh - 1"), "距52周高点回撤")
        self.assertEqual(metric_label("sbcToRevenue"), "股权激励/收入")
        self.assertEqual(metric_label("stockBasedCompensation / revenue"), "股权激励/收入")
        self.assertEqual(metric_label("operatingCashFlowMargin"), "经营现金流利润率")
        self.assertNotEqual(metric_label("operatingCashFlowMargin"), "FCF利润率")
        self.assertEqual(metric_label("nonGaapFcfMargin"), "Non-GAAP FCF利润率")
        self.assertEqual(metric_label("debtMaturityPressure"), "债务到期压力")

    def test_metric_label_map_covers_industry_kpis(self) -> None:
        self.assertEqual(metric_label("cRpoGrowth"), "cRPO增速")
        self.assertEqual(metric_label("cRpoGrowthReported"), "cRPO增速（reported YoY）")
        self.assertEqual(metric_label("rpoGrowthConstantCurrency"), "RPO增速（constant currency）")
        self.assertEqual(metric_label("subscriptionRevenueGrowth"), "订阅收入增速")
        self.assertEqual(metric_label("NRR"), "净留存率")
        self.assertEqual(metric_label("unit growth"), "客户/单位增长")
        self.assertEqual(metric_label("seatCompressionRisk"), "席位压缩风险")
        self.assertEqual(metric_label("aiCapexOverbuildRisk"), "AI资本开支过剩风险")
        self.assertEqual(metric_label("merchantPowerExposure"), "市场化电价敞口")
        self.assertEqual(metric_label("pipelineStrength"), "管线强度")

    def test_metric_label_hides_internal_debug_fields_outside_debug_mode(self) -> None:
        for field in ("evidenceHash", "extractionRule", "rawMetricKey", "sourceType", "reviewStatus", "inputHash", "promptVersion", "accessionNumber"):
            with self.subTest(field=field):
                self.assertTrue(is_internal_metric_field(field))
                self.assertEqual(metric_label(field), "")
                self.assertEqual(metric_label(field, debug=True), f"未映射字段：{field}")

    def test_drawer_resolution_uses_chinese_labels_and_statuses(self) -> None:
        row = pd.Series(
            {
                "metricResolutionStatus": [
                    {"displayName": "FCF Margin", "metricKey": "fcfMargin", "resolutionStatus": "calculated"},
                    {"displayName": "Segment strength", "metricKey": "segmentStrength", "resolutionStatus": "derived_score"},
                    {"displayName": "Buyback discipline", "metricKey": "buybackDiscipline", "resolutionStatus": "derived_score"},
                    {
                        "displayName": "Historical valuation percentile",
                        "metricKey": "historicalValuationPercentile",
                        "resolutionStatus": "derived_score",
                    },
                    {"displayName": "net retention rate", "metricKey": "netRetentionRate", "resolutionStatus": "manual_override_required"},
                ]
            }
        )

        html = _drawer_resolution_html(row)

        self.assertIn("FCF利润率", html)
        self.assertIn("已计算", html)
        self.assertIn("分部业务强度", html)
        self.assertIn("回购纪律", html)
        self.assertIn("历史估值分位", html)
        self.assertIn("建议人工复核", html)
        self.assertNotIn("manual_override_required", html)
        self.assertNotIn("Segment strength", html)
        self.assertNotIn("Buyback discipline", html)
        self.assertNotIn("未映射字段：EMA20", html)

    def test_drawer_data_status_uses_decision_sections_and_recommended_actions(self) -> None:
        row = pd.Series(
            {
                "metricResolutionStatus": [
                    {
                        "displayName": "subscription revenue growth",
                        "metricKey": "subscriptionRevenueGrowth",
                        "resolutionStatus": "requires_ir_scrape",
                        "metricType": "DISCLOSURE_KPI",
                        "affects": ["Quality"],
                        "priority": "high",
                    },
                    {
                        "displayName": "SBC / revenue",
                        "metricKey": "sbcToRevenue",
                        "resolutionStatus": "missing_inputs",
                        "metricType": "CALCULATED_METRIC",
                        "affects": ["Quality", "Risk"],
                        "priority": "high",
                    },
                    {
                        "displayName": "FCF Margin",
                        "metricKey": "fcfMargin",
                        "resolutionStatus": "calculated",
                        "metricType": "CALCULATED_METRIC",
                        "value": 0.332,
                        "confidence": "high",
                        "explanation": "基于 FMP cash flow 和收入计算：freeCashFlow / revenue。",
                        "priority": "high",
                    },
                    {
                        "displayName": "EMA200",
                        "metricKey": "ema200",
                        "resolutionStatus": "calculated",
                        "metricType": "CALCULATED_METRIC",
                        "value": 133.06,
                        "confidence": "high",
                    },
                    {
                        "displayName": "regulatory risk",
                        "metricKey": "regulatoryRisk",
                        "resolutionStatus": "semi_auto_low_confidence",
                        "metricType": "QUALITATIVE_RISK_FACTOR",
                        "priority": "low",
                    },
                ]
            }
        )

        html = _drawer_resolution_html(row)

        self.assertIn("关键待补齐", html)
        self.assertIn("可自动补齐", html)
        self.assertIn("已计算摘要", html)
        self.assertIn("低优先级 / 仅解释项", html)
        self.assertIn("抓取IR财报新闻稿 / 8-K Exhibit 99.1", html)
        self.assertIn("FCF利润率", html)
        self.assertIn("EMA200", html)
        self.assertNotIn("subscriptionRevenueGrowth", html)

    def test_drawer_shows_implied_fcf_margin_when_market_derived(self) -> None:
        row = pd.Series(
            {
                "metricResolutionStatus": [
                    {
                        "displayName": "Implied FCF Margin",
                        "metricKey": "fcfMargin",
                        "resolutionStatus": "derived_score",
                        "metricType": "DERIVED_SCORING_FACTOR",
                        "value": 0.315,
                        "confidence": "medium",
                        "explanation": "基于 FCF收益率 × 市销率推导，置信度低于财报直接计算值，暂不参与公司质量评分。",
                        "priority": "medium",
                    }
                ]
            }
        )

        html = _drawer_resolution_html(row)

        self.assertIn("估算FCF利润率", html)
        self.assertIn("规则推导", html)
        self.assertIn("暂不参与公司质量评分", html)
        self.assertNotIn("market-derived", html)
        self.assertNotIn("quality score", html)

    def test_resolution_grouping_keeps_calculated_and_derived_out_of_manual_bucket(self) -> None:
        groups = _metric_resolution_groups(
            [
                {"displayName": "FCF Margin", "resolutionStatus": "calculated"},
                {"displayName": "Net Cash / Balance Sheet", "resolutionStatus": "derived_score"},
                {"displayName": "Historical valuation percentile", "resolutionStatus": "derived_score"},
                {"displayName": "interest coverage", "resolutionStatus": "missing_inputs"},
            ]
        )

        self.assertIn("已计算摘要", groups)
        self.assertIn("可自动补齐", groups)
        self.assertIn("低优先级 / 仅解释项", groups)
        self.assertNotIn("公司未披露 / 需人工补充", groups)
        self.assertNotIn("公司未披露 / 建议人工复核", groups)

    def test_resolution_value_text_translates_status_and_confidence(self) -> None:
        text = _resolution_value_text({"metricKey": "fcfMargin", "resolutionStatus": "calculated", "confidence": "high", "value": 0.332})
        self.assertIn("已计算", text)
        self.assertIn("置信度 高", text)
        self.assertNotIn("calculated", text)


class TechnicalIndicatorTests(unittest.TestCase):
    def test_rsi14_is_bounded_and_high_for_uptrend(self) -> None:
        prices = pd.Series(range(1, 40), dtype=float)
        rsi = calculate_rsi14(prices)
        self.assertGreater(rsi.iloc[-1], 90)
        self.assertLessEqual(rsi.dropna().max(), 100)

    def test_ema_functions_return_expected_columns(self) -> None:
        prices = pd.Series(range(1, 260), dtype=float)
        self.assertAlmostEqual(calculate_ema20(prices).iloc[-1], prices.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1])
        self.assertAlmostEqual(calculate_ema50(prices).iloc[-1], prices.ewm(span=50, adjust=False, min_periods=50).mean().iloc[-1])
        self.assertAlmostEqual(calculate_ema200(prices).iloc[-1], prices.ewm(span=200, adjust=False, min_periods=200).mean().iloc[-1])

    def test_drawdown_from_52_week_high(self) -> None:
        closes = pd.Series([100.0] * 251 + [90.0])
        highs = pd.Series([100.0] * 251 + [120.0])
        drawdown = calculate_drawdown_from_52_week_high(closes, highs)
        self.assertAlmostEqual(drawdown.iloc[-1], -25.0)

    def test_technical_score(self) -> None:
        score = calculate_technical_score({"price": 120, "ema50": 110, "ema200": 100, "rsi14": 55})
        self.assertEqual(score, 10.0)

    def test_20_day_gain(self) -> None:
        prices = pd.Series([100.0] * 20 + [125.0])
        gain = calculate_gain_over_trading_days(prices, days=20)
        self.assertEqual(gain.iloc[-1], 25.0)

    def test_latest_technicals_include_overheat_inputs(self) -> None:
        prices = pd.Series([100.0] * 260 + [130.0])
        history = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=len(prices)),
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": 1_000_000,
            }
        )
        snapshot = latest_technical_snapshot(add_technical_indicators(history))
        self.assertIn("gain_60d_pct", snapshot)
        self.assertIn("daily_return_pct", snapshot)
        self.assertIn("pct_above_ema20", snapshot)
        self.assertIn("pct_above_ema50", snapshot)


class ScoringTests(unittest.TestCase):
    def _neutral_overheat(self) -> OverheatResult:
        return OverheatResult(score=0, status="", action="", recommendation="", reasons=[])

    def test_valuation_score_penalizes_expensive_multiples(self) -> None:
        cheap = calculate_valuation_score({"forward_pe": 20, "price_to_sales": 5}, {"drawdown_from_high_pct": -20})
        expensive = calculate_valuation_score({"forward_pe": 80, "price_to_sales": 25}, {"drawdown_from_high_pct": 0})
        self.assertGreater(cheap, expensive)

    def test_total_score_returns_rating_and_components(self) -> None:
        snapshot = {
            "free_cash_flow": 1_000_000_000,
            "operating_margin": 0.22,
            "profit_margin": 0.18,
            "return_on_equity": 0.18,
            "revenue_growth": 0.12,
            "earnings_growth": 0.10,
            "forward_pe": 24,
            "trailing_pe": 28,
            "price_to_sales": 7,
            "debt_to_equity": 50,
            "total_debt": 100,
            "total_cash": 150,
        }
        technicals = {"price": 100, "ema50": 95, "ema200": 90, "rsi14": 52, "drawdown_from_high_pct": -15}
        result = calculate_total_score(snapshot, technicals)
        self.assertGreater(result.total_score, 70)
        self.assertIn(result.rating[0], {"A", "B"})

    def test_vst_uses_power_company_scoring_instead_of_generic_saas_rules(self) -> None:
        snapshot = {
            "ticker": "VST",
            "sector": "Utilities",
            "industry": "Independent Power Producers",
            "market_cap": 40_000_000_000,
            "enterprise_value": 55_000_000_000,
            "ebitda": 5_500_000_000,
            "adjustedEbitdaGrowth": 0.14,
            "free_cash_flow": 4_000_000_000,
            "net_debt_to_ebitda": 3.4,
            "current_ratio": 1.1,
            "revenue_growth": -0.18,
            "return_on_invested_capital": 0.02,
        }
        technicals = {
            "price": 145,
            "ema50": 150,
            "ema200": 120,
            "rsi14": 45,
            "drawdown_from_high_pct": -30,
            "pct_above_ema200": 20,
        }

        result = calculate_total_score(snapshot, technicals)
        data_quality = {"pct": 100, "missing": []}
        high_flags = sum(1 for flag in result.risk_flags if flag.severity == "high")
        medium_flags = sum(1 for flag in result.risk_flags if flag.severity == "medium")

        self.assertEqual(result.scoring_model, "POWER_GENERATION")
        self.assertGreaterEqual(result.quality_score, 65)
        self.assertGreaterEqual(result.entry_score, 75)
        self.assertEqual(high_flags, 0)
        self.assertTrue(any(flag.label == "杠杆中高" for flag in result.risk_flags))
        self.assertEqual(result.risk_rating, "中高")
        self.assertEqual(_risk_rating(result.risk_flags, high_flags, medium_flags, data_quality), "中高")
        self.assertEqual(result.valuation_status, "回撤后有吸引力")
        self.assertEqual(_valuation_status(result.value_zone, data_quality), "回撤后有吸引力")
        self.assertEqual(result.action, "等回踩")
        self.assertEqual(_action_recommendation(result, data_quality, "", high_flags, ""), "等回踩")
        self.assertEqual(result.dataConfidence, "medium")
        self.assertEqual(result.proxyConfidence, "medium")
        self.assertIn("adjusted EBITDA", result.missingIndustryMetrics)
        self.assertIn("EBITDA", result.proxyMetricsUsed)
        self.assertEqual(result.maxSuggestedPositionPercent, 0)

    def test_vst_special_case_does_not_override_observe_valuation(self) -> None:
        context = ScoreContext(
            snapshot={"ticker": "VST"},
            technicals={"drawdown_from_high_pct": -30},
            model_type="POWER_GENERATION",
        )

        action = _final_action(
            quality=65,
            entry=45,
            risk=60,
            valuation_status="只观察",
            context=context,
            data_insufficient=False,
            overheat=self._neutral_overheat(),
        )

        self.assertEqual(action, "等回踩")

    def test_c_grade_entry_blocks_buy_actions_even_with_favorable_labels(self) -> None:
        action = _guard_action_conflicts(
            action="可小仓分批",
            valuation_status="击球区附近",
            risk=20,
            entry=45,
        )

        self.assertEqual(action, "只观察")

    def test_non_buy_valuation_statuses_block_buy_actions(self) -> None:
        context = ScoreContext(
            snapshot={"ticker": "TEST"},
            technicals={"drawdown_from_high_pct": -30},
            model_type="GENERIC",
        )

        for valuation_status in ["只观察", "偏贵", "极贵"]:
            with self.subTest(valuation_status=valuation_status):
                action = _final_action(
                    quality=80,
                    entry=80,
                    risk=20,
                    valuation_status=valuation_status,
                    context=context,
                    data_insufficient=False,
                    overheat=self._neutral_overheat(),
                )

                self.assertNotIn(action, {"可小仓分批", "可正常分批"})

    def test_medium_high_risk_does_not_emit_normal_batch_action(self) -> None:
        context = ScoreContext(
            snapshot={"ticker": "TEST"},
            technicals={"drawdown_from_high_pct": -30},
            model_type="GENERIC",
        )

        action = _final_action(
            quality=80,
            entry=80,
            risk=60,
            valuation_status="击球区附近",
            context=context,
            data_insufficient=False,
            overheat=self._neutral_overheat(),
        )

        self.assertNotEqual(action, "可正常分批")
        self.assertNotIn(action, {"可小仓分批", "可正常分批"})

    def test_near_buy_zone_entry_does_not_emit_exact_buy_action(self) -> None:
        context = ScoreContext(
            snapshot={"ticker": "TEST"},
            technicals={"drawdown_from_high_pct": -25},
            model_type="GENERIC",
        )

        action = _final_action(
            quality=80,
            entry=70,
            risk=40,
            valuation_status="击球区附近",
            context=context,
            data_insufficient=False,
            overheat=self._neutral_overheat(),
        )

        self.assertEqual(action, "等回踩")

    def test_high_quality_large_drawdown_does_not_bypass_observe_valuation(self) -> None:
        context = ScoreContext(
            snapshot={"ticker": "VST"},
            technicals={"drawdown_from_high_pct": -40},
            model_type="POWER_GENERATION",
        )

        action = _final_action(
            quality=90,
            entry=45,
            risk=40,
            valuation_status="只观察",
            context=context,
            data_insufficient=False,
            overheat=self._neutral_overheat(),
        )

        self.assertNotIn(action, {"可小仓分批", "可正常分批"})

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

        self.assertEqual(decision.finalAction, "只观察")
        self.assertFalse(decision.isActionable)
        self.assertEqual(decision.currentAddLimitPercent, 0)
        self.assertIn("valuation_status", decision.blockReasons)

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

        self.assertEqual(bundle.finalAction, buy_action)
        self.assertTrue(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 5)
        self.assertEqual(bundle.maxPortfolioWeightPercent, 15)

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

        self.assertTrue(bundle.isActionable)
        self.assertEqual(bundle.currentAddLimitPercent, 6)
        self.assertEqual(bundle.maxPortfolioWeightPercent, 20)

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

        self.assertTrue(bundle.isActionable)
        self.assertGreater(bundle.currentAddLimitPercent, 0)
        self.assertNotIn("buy_zone", bundle.blockReasons)

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
                "finalAction",
                "decisionLane",
                "displayCategory",
                "isActionable",
                "currentAddLimitPercent",
                "maxPortfolioWeightPercent",
                "blockReasons",
                "reviewReasons",
                "dataConfidence",
            },
        )

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

    def test_power_company_classification_includes_core_utility_symbols_and_industries(self) -> None:
        for symbol in ["VST", "CEG", "TLN", "NRG", "DUK", "SO", "NEE"]:
            self.assertTrue(is_power_company({"ticker": symbol}))
        self.assertTrue(is_power_company({"sector": "Utilities", "industry": "Electric Utilities"}))
        self.assertTrue(is_power_company({"industry": "Power Generation"}))

    def test_sector_specific_classifier_routes_core_models(self) -> None:
        self.assertEqual(classifyStockModel({"ticker": "VST"}), "POWER_GENERATION")
        self.assertEqual(classifyStockModel({"ticker": "NOW"}), "SAAS_SOFTWARE")
        self.assertEqual(classifyStockModel({"ticker": "ADBE"}), "SAAS_SOFTWARE")
        self.assertEqual(classifyStockModel({"ticker": "PLTR"}), "SAAS_SOFTWARE")
        self.assertEqual(classifyStockModel({"ticker": "NVDA"}), "SEMICONDUCTOR")
        self.assertEqual(classifyStockModel({"ticker": "AVGO"}), "SEMICONDUCTOR")
        self.assertEqual(classifyStockModel({"ticker": "COIN"}), "CRYPTO_FINANCIAL_INFRA")
        self.assertEqual(classifyStockModel({"ticker": "HOOD"}), "CRYPTO_FINANCIAL_INFRA")

    def test_saas_market_derived_fcf_margin_is_tagged_but_not_scored(self) -> None:
        snapshot = {
            "ticker": "NOW",
            "sector": "Technology",
            "industry": "Software - Application",
            "revenue_growth": 0.20,
            "gross_margin": 0.76,
            "operating_margin": 0.18,
            "return_on_invested_capital": 0.10,
            "free_cash_flow_yield": 0.045,
            "price_to_sales": 7.4,
            "price_to_fcf": 22,
            "forward_pe": 32,
            "total_debt": 100,
            "total_cash": 300,
        }

        metric = fcf_margin_metric(snapshot)
        score = fcf_margin_score(ScoreContext(snapshot=snapshot, technicals={}, model_type="SAAS_SOFTWARE"))

        self.assertEqual(metric.sourceType, "derivedFromMarket")
        self.assertAlmostEqual(metric.value, 0.333, places=3)
        self.assertIsNone(score)

    def test_saas_calculated_fcf_margin_can_participate_in_quality(self) -> None:
        snapshot = {
            "ticker": "CRM",
            "sector": "Technology",
            "industry": "Software - Application",
            "free_cash_flow": 3_000,
            "total_revenue": 10_000,
        }

        metric = fcf_margin_metric(snapshot)
        score = fcf_margin_score(ScoreContext(snapshot=snapshot, technicals={}, model_type="SAAS_SOFTWARE"))

        self.assertEqual(metric.sourceType, "calculated")
        self.assertAlmostEqual(metric.value, 0.30)
        self.assertIsNotNone(score)

    def test_calculated_fcf_margin_is_not_reported_as_missing(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.22,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 3_320,
                "total_revenue": 10_000,
                "price_to_sales": 7,
                "price_to_fcf": 22.2,
                "free_cash_flow_yield": 0.045,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -40,
                "gain_20d_pct": 5,
                "gain_60d_pct": -10,
                "fifty_two_week_low": 70,
            },
        )

        self.assertNotIn("calculated FCF Margin", result.missingIndustryMetrics)
        self.assertNotIn("FCF Margin reported/calculated", result.missingIndustryMetrics)
        fcf_row = _metric_resolution_by_key(result, "fcfMargin")
        self.assertEqual(fcf_row["resolutionStatus"], "calculated")
        self.assertAlmostEqual(float(fcf_row["value"]), 0.332)

    def test_saas_resolution_statuses_classify_non_fmp_fields(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.22,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 3_320,
                "total_revenue": 10_000,
                "price_to_sales": 7,
                "price_to_fcf": 22.2,
                "free_cash_flow_yield": 0.045,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -40,
                "gain_20d_pct": 5,
                "gain_60d_pct": -10,
                "fifty_two_week_low": 70,
            },
        )

        self.assertEqual(_metric_resolution_by_key(result, "subscriptionRevenueGrowth")["resolutionStatus"], "requires_ir_scrape")
        self.assertEqual(_metric_resolution_by_key(result, "nonGaapOperatingMargin")["resolutionStatus"], "requires_ir_scrape")
        self.assertEqual(_metric_resolution_by_key(result, "rpoGrowth")["resolutionStatus"], "requires_ir_scrape")
        self.assertIn(
            _metric_resolution_by_key(result, "netRetentionRate")["resolutionStatus"],
            {"company_not_disclosed", "manual_override_required"},
        )
        self.assertEqual(_metric_resolution_by_key(result, "peg")["resolutionStatus"], "requires_analyst_estimates")
        self.assertEqual(_metric_resolution_by_key(result, "peg")["metricType"], "ANALYST_ESTIMATE_METRIC")
        self.assertEqual(_metric_resolution_by_key(result, "peg")["affects"], ["Entry"])
        self.assertEqual(_metric_resolution_by_key(result, "forwardRevenueMultiple")["resolutionStatus"], "requires_analyst_estimates")
        self.assertEqual(_metric_resolution_by_key(result, "ema200")["resolutionStatus"], "calculated")
        self.assertEqual(_metric_resolution_by_key(result, "ema200")["metricType"], "CALCULATED_METRIC")

    def test_missing_resolution_routes_saas_kpis_and_estimates(self) -> None:
        result = calculate_total_score(_missing_resolution_saas_snapshot(), _missing_resolution_technicals())

        for metric_key in ("subscriptionRevenueGrowth", "largeCustomerGrowth", "cRpoGrowth", "rpoGrowth"):
            row = _metric_resolution_by_key(result, metric_key)
            self.assertEqual(row["missingResolutionRoute"], "ir_or_sec_extract")
            self.assertFalse(row["defaultReviewQueue"])

        retention = _metric_resolution_by_key(result, "netRetentionRate")
        self.assertIn(retention["missingResolutionRoute"], {"ir_or_sec_extract", "company_not_disclosed"})
        self.assertFalse(retention["defaultReviewQueue"])

        for metric_key in ("peg", "forwardRevenueMultiple"):
            row = _metric_resolution_by_key(result, metric_key)
            self.assertEqual(row["missingResolutionRoute"], "analyst_estimates_required")
            self.assertEqual(row["affects"], ["Entry"])
            self.assertFalse(row["defaultReviewQueue"])

        summary = result.missingDataSummary
        self.assertGreaterEqual(summary["autoFillableCount"], 4)
        self.assertGreaterEqual(summary["estimatesRequiredCount"], 2)
        self.assertGreaterEqual(summary["companyNotDisclosedCount"], 1)
        self.assertEqual(summary["humanReviewRequiredCount"], 0)

    def test_calculable_sbc_and_leverage_metrics_are_not_missing(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "CRM",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.22,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 3_000,
                "total_revenue": 10_000,
                "stock_based_compensation": 900,
                "total_debt": 1_000,
                "total_cash": 4_000,
                "ebitda": 2_000,
                "ebit": 2_200,
                "interest_expense": 100,
                "price_to_sales": 7,
                "price_to_fcf": 22,
                "free_cash_flow_yield": 0.045,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -25,
                "gain_20d_pct": 2,
                "gain_60d_pct": -8,
                "fifty_two_week_low": 70,
            },
        )

        self.assertNotIn("SBC / revenue", result.missingIndustryMetrics)
        self.assertEqual(_metric_resolution_by_key(result, "sbcToRevenue")["resolutionStatus"], "calculated")
        self.assertEqual(_metric_resolution_by_key(result, "netDebtToEbitda")["resolutionStatus"], "calculated")
        self.assertEqual(_metric_resolution_by_key(result, "interestCoverage")["resolutionStatus"], "calculated")

    def test_auto_calculable_missing_resolution_routes_use_structured_inputs(self) -> None:
        result = calculate_total_score(_missing_resolution_saas_snapshot(stock_based_compensation=900), _missing_resolution_technicals())

        for metric_key in ("sbcToRevenue", "netDebtToEbitda", "interestCoverage", "fcfMargin"):
            row = _metric_resolution_by_key(result, metric_key)
            self.assertEqual(row["resolutionStatus"], "calculated")
            self.assertEqual(row["missingResolutionRoute"], "auto_calculate")
            self.assertNotEqual(row["resolutionStatus"], "manual_override_required")

    def test_saas_common_symbols_use_new_framework_without_market_derived_quality_boost(self) -> None:
        symbols = ["NOW", "CRM", "ADBE", "SNOW", "DDOG", "PLTR", "ORCL"]
        for symbol in symbols:
            with self.subTest(symbol=symbol):
                result = calculate_total_score(
                    {
                        "ticker": symbol,
                        "sector": "Technology",
                        "industry": "Software - Application",
                        "revenue_growth": 0.19,
                        "gross_margin": 0.76,
                        "operating_margin": 0.14,
                        "return_on_invested_capital": 0.10,
                        "free_cash_flow_yield": 0.04,
                        "price_to_sales": 8.5,
                        "enterprise_to_revenue": 8.8,
                        "price_to_fcf": 25,
                        "forward_pe": 35,
                        "current_ratio": 1.2,
                        "total_debt": 100,
                        "total_cash": 300,
                    },
                    {
                        "price": 90,
                        "ema20": 95,
                        "ema50": 100,
                        "ema200": 110,
                        "rsi14": 48,
                        "drawdown_from_high_pct": -42,
                        "gain_20d_pct": -2,
                        "fifty_two_week_low": 70,
                    },
                )

                self.assertEqual(result.scoring_model, "SAAS_SOFTWARE")
                self.assertFalse(result.data_insufficient)
                self.assertLess(result.quality_score, 75)
                self.assertGreaterEqual(result.risk_score, 26)

    def test_saas_peg_missing_does_not_affect_quality_rating(self) -> None:
        snapshot = {
            "ticker": "NOW",
            "sector": "Technology",
            "industry": "Software - Application",
            "revenue_growth": 0.25,
            "gross_margin": 0.82,
            "operating_margin": 0.28,
            "return_on_invested_capital": 0.18,
            "free_cash_flow": 3_500,
            "total_revenue": 10_000,
            "price_to_sales": 8,
            "enterprise_to_revenue": 8.2,
            "price_to_fcf": 25,
            "ev_to_fcf": 25,
            "free_cash_flow_yield": 0.04,
            "manualSubscriptionRevenueGrowth": 0.24,
            "manualNonGaapOperatingMargin": 0.34,
            "manualNetRetention": 1.22,
            "manualRpoGrowth": 0.20,
            "manualSbcRatio": 0.08,
        }
        technicals = {
            "price": 100,
            "ema20": 98,
            "ema50": 96,
            "ema200": 90,
            "rsi14": 52,
            "drawdown_from_high_pct": -18,
            "gain_20d_pct": 1,
            "fifty_two_week_low": 70,
        }

        without_peg = calculate_total_score(snapshot, technicals)
        with_peg = calculate_total_score({**snapshot, "peg": 1.4}, technicals)

        self.assertEqual(without_peg.quality_score, with_peg.quality_score)
        self.assertEqual(without_peg.qualityRating, with_peg.qualityRating)
        peg_rows = [row for row in without_peg.missingMetricImpact if row["metric"] == "PEG"]
        self.assertEqual(peg_rows[0]["impactCategory"], "VALUATION_ONLY")
        self.assertEqual(peg_rows[0]["affects"], "Entry")
        self.assertEqual(_metric_resolution_by_key(without_peg, "peg")["missingResolutionRoute"], "analyst_estimates_required")

    def test_saas_forward_revenue_multiple_missing_does_not_cause_data_insufficient(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "CRM",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.21,
                "gross_margin": 0.78,
                "operating_margin": 0.24,
                "free_cash_flow": 4_000,
                "total_revenue": 12_000,
                "price_to_sales": 7.5,
                "price_to_fcf": 24,
                "free_cash_flow_yield": 0.042,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -22,
                "gain_20d_pct": -2,
                "fifty_two_week_low": 68,
            },
        )

        self.assertFalse(result.data_insufficient)
        rows = [row for row in result.missingMetricImpact if row["metric"] == "forward revenue multiple"]
        self.assertEqual(rows[0]["impactCategory"], "VALUATION_ONLY")
        self.assertNotEqual(result.qualityRating, "数据不足")

        self.assertEqual(
            _metric_resolution_by_key(result, "forwardRevenueMultiple")["missingResolutionRoute"],
            "analyst_estimates_required",
        )

    def test_saas_net_retention_missing_only_lowers_confidence(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.30,
                "gross_margin": 0.84,
                "operating_margin": 0.34,
                "return_on_invested_capital": 0.21,
                "free_cash_flow": 3_800,
                "total_revenue": 10_000,
                "price_to_sales": 8,
                "enterprise_to_revenue": 8,
                "price_to_fcf": 24,
                "ev_to_fcf": 24,
                "free_cash_flow_yield": 0.042,
                "manualSubscriptionRevenueGrowth": 0.28,
                "manualNonGaapOperatingMargin": 0.36,
                "manualRpoGrowth": 0.24,
                "manualSbcRatio": 0.08,
                "net_debt_to_ebitda": -1,
                "total_cash": 5_000,
                "total_debt": 1_000,
                "current_ratio": 1.4,
                "manualDebtMaturityPressure": 10,
                "interest_coverage": 20,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -18,
                "gain_20d_pct": -1,
                "fifty_two_week_low": 70,
            },
        )

        self.assertFalse(result.data_insufficient)
        self.assertEqual(result.dataConfidence, "medium")
        self.assertEqual(result.ratingCap, "A")
        self.assertGreaterEqual(result.quality_score, 75)
        self.assertLessEqual(result.quality_score, 84)
        rows = [row for row in result.missingMetricImpact if row["metric"] == "net retention rate"]
        self.assertEqual(rows[0]["impactCategory"], "CRITICAL_QUALITY")

    def test_debt_maturity_pressure_low_materiality_is_low_priority_archive(self) -> None:
        result = calculate_total_score(
            _missing_resolution_saas_snapshot(total_debt=1_000, total_cash=5_000, ebitda=2_000, market_cap=100_000),
            _missing_resolution_technicals(),
        )

        row = _metric_resolution_by_key(result, "debtMaturityPressure")
        self.assertEqual(row["missingResolutionRoute"], "low_priority_archive")
        self.assertFalse(row["defaultReviewQueue"])
        self.assertEqual(result.missingDataSummary["lowPriorityArchivedCount"], 1)
        self.assertEqual(result.missingDataSummary["humanReviewRequiredCount"], 0)

    def test_debt_maturity_pressure_high_leverage_requires_human_review(self) -> None:
        result = calculate_total_score(
            _missing_resolution_saas_snapshot(
                total_debt=8_000,
                total_cash=1_000,
                ebitda=2_000,
                interest_expense=900,
                market_cap=20_000,
            ),
            _missing_resolution_technicals(),
        )

        row = _metric_resolution_by_key(result, "debtMaturityPressure")
        self.assertEqual(row["missingResolutionRoute"], "human_review_required")
        self.assertTrue(row["defaultReviewQueue"])
        self.assertIn("debt maturity pressure", result.missingDataSummary["keyBlockingMetrics"])

    def test_missing_resolution_routes_do_not_enter_default_review_queue_noise(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review.sqlite"
            disclosure_store = DisclosureStore(db_path)
            queue_store = ReviewQueueStore(db_path, disclosure_store=disclosure_store)
            fundamental_cache = FundamentalCache(db_path)
            fundamental_cache.set_snapshot(
                "NOW",
                _missing_resolution_saas_snapshot(total_debt=1_000, total_cash=5_000, ebitda=2_000, market_cap=100_000),
            )
            builder = ReviewQueueBuilder(
                queue_store=queue_store,
                disclosure_store=disclosure_store,
                fundamental_cache=fundamental_cache,
            )

            builder.build_review_queue_for_symbol("NOW")
            metric_keys = {row["metricKey"] for row in queue_store.list_items("NOW")}

            self.assertNotIn("peg", metric_keys)
            self.assertNotIn("forwardRevenueMultiple", metric_keys)
            self.assertNotIn("netRetentionRate", metric_keys)
            self.assertNotIn("debtMaturityPressure", metric_keys)

    def test_saas_ema200_missing_is_not_fundamental_missing_data(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "ADBE",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.18,
                "gross_margin": 0.86,
                "operating_margin": 0.35,
                "free_cash_flow": 6_000,
                "total_revenue": 18_000,
                "price_to_sales": 7,
                "price_to_fcf": 22,
                "free_cash_flow_yield": 0.045,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "rsi14": 52,
                "drawdown_from_high_pct": -20,
                "gain_20d_pct": 1,
                "fifty_two_week_low": 70,
            },
        )

        self.assertFalse(any("EMA200" in item for item in result.missing_data))
        self.assertTrue(
            any(row["affects"] == "Technical" and "EMA200" in row["metric"] for row in result.missingMetricImpact)
        )

    def test_fcf_margin_direct_calculation_not_missing_or_derived(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.20,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 3_000,
                "total_revenue": 10_000,
                "price_to_sales": 7,
                "price_to_fcf": 24,
                "free_cash_flow_yield": 0.04,
            },
            {"price": 100, "ema20": 98, "ema50": 96, "ema200": 94, "rsi14": 52, "drawdown_from_high_pct": -20},
        )

        fcf = _metric_resolution_by_key(result, "fcfMargin")
        self.assertEqual(fcf["resolutionStatus"], "calculated")
        self.assertEqual(fcf["displayName"], "FCF Margin")
        self.assertNotIn("FCF Margin", result.missing_data)

    def test_fcf_margin_market_derived_is_implied_and_not_quality_input(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.20,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "price_to_sales": 8,
                "price_to_fcf": 24,
                "free_cash_flow_yield": 0.04,
            },
            {"price": 100, "ema20": 98, "ema50": 96, "ema200": 94, "rsi14": 52, "drawdown_from_high_pct": -20},
        )

        fcf = _metric_resolution_by_key(result, "fcfMargin")
        self.assertEqual(fcf["resolutionStatus"], "derived_score")
        self.assertEqual(fcf["displayName"], "Implied FCF Margin")
        self.assertNotIn("Quality", fcf["affects"])
        self.assertIn("FCF利润率为市场数据推导值", _translate_factor("FCF Margin is market-derived and excluded from quality score"))

    def test_negative_fcf_risk_driver_only_when_triggered(self) -> None:
        positive = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.20,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 1_000,
                "total_revenue": 10_000,
                "price_to_sales": 7,
            },
            {"price": 100, "ema200": 90, "drawdown_from_high_pct": -20},
        )
        negative = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.20,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": -500,
                "total_revenue": 10_000,
                "price_to_sales": 7,
            },
            {"price": 100, "ema200": 90, "drawdown_from_high_pct": -20},
        )

        self.assertNotIn("自由现金流为负", positive.activeRiskDrivers)
        self.assertIn("自由现金流为负", negative.activeRiskDrivers)

    def test_cash_and_volume_resolution_are_not_fundamental_manual_noise(self) -> None:
        cash_result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.20,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 1_000,
                "total_revenue": 10_000,
                "total_debt": 2_000,
                "cashAndCashEquivalents": 5_000,
                "ebitda": 2_500,
                "price_to_sales": 7,
            },
            {"price": 100, "ema20": 98, "ema50": 96, "ema200": 94, "rsi14": 52, "drawdown_from_high_pct": -20},
        )
        self.assertNotIn("cash and equivalents", cash_result.missing_data)

        volume_row = _metric_resolution_by_key(cash_result, "volumeTrend")
        self.assertEqual(volume_row["metricType"], "CALCULATED_METRIC")
        self.assertEqual(volume_row["affects"], ["Technical"])
        self.assertFalse(any(row["metric"] == "volume trend" and row["affects"] != "Technical" for row in cash_result.missingMetricImpact))

    def test_saas_foundation_financials_are_scoring_usable_despite_auxiliary_missing(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "SNOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.24,
                "gross_margin": 0.72,
                "operating_margin": 0.12,
                "free_cash_flow": 1_200,
                "total_revenue": 5_000,
                "price_to_sales": 9,
                "ev_to_fcf": 30,
                "free_cash_flow_yield": 0.033,
            },
            {
                "price": 100,
                "ema20": 99,
                "ema50": 97,
                "ema200": 95,
                "rsi14": 50,
                "drawdown_from_high_pct": -28,
                "gain_20d_pct": -2,
                "fifty_two_week_low": 65,
            },
        )

        self.assertFalse(result.data_insufficient)
        self.assertNotEqual(result.action, "数据不足，需复核")
        self.assertNotEqual(result.qualityRating, "数据不足")

    def test_empty_proxy_metrics_make_proxy_confidence_not_applicable(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.24,
                "gross_margin": 0.82,
                "operating_margin": 0.30,
                "return_on_invested_capital": 0.18,
                "free_cash_flow": 3_500,
                "total_revenue": 10_000,
                "price_to_sales": 7.5,
                "enterprise_to_revenue": 7.8,
                "price_to_fcf": 24,
                "ev_to_fcf": 24,
                "free_cash_flow_yield": 0.04,
                "manualSubscriptionRevenueGrowth": 0.24,
                "manualNonGaapOperatingMargin": 0.34,
                "manualNetRetention": 1.22,
                "manualRpoGrowth": 0.20,
                "manualSbcRatio": 0.08,
                "net_debt_to_ebitda": -1,
                "total_cash": 4_000,
                "total_debt": 1_000,
                "current_ratio": 1.4,
                "interest_coverage": 10,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -20,
                "gain_20d_pct": -1,
                "fifty_two_week_low": 70,
            },
        )

        self.assertEqual(result.proxyMetricsUsed, [])
        self.assertEqual(result.proxyConfidence, "不适用")

    def test_high_impact_missing_metrics_populate_missing_industry_metrics(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.22,
                "gross_margin": 0.80,
                "operating_margin": 0.25,
                "free_cash_flow": 2_500,
                "total_revenue": 10_000,
                "price_to_sales": 7,
                "price_to_fcf": 25,
                "free_cash_flow_yield": 0.04,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 50,
                "drawdown_from_high_pct": -20,
                "gain_20d_pct": -1,
                "fifty_two_week_low": 70,
            },
        )

        self.assertIn("subscription revenue growth", result.missingIndustryMetrics)
        self.assertIn("non-GAAP operating margin", result.missingIndustryMetrics)
        self.assertIn("net retention rate", result.missingIndustryMetrics)
        self.assertNotEqual(result.dataConfidence, "high")

    def test_deep_drawdown_is_entry_positive_not_primary_negative(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.22,
                "gross_margin": 0.78,
                "operating_margin": 0.22,
                "free_cash_flow": 3_000,
                "total_revenue": 10_000,
                "price_to_sales": 7,
                "price_to_fcf": 24,
                "free_cash_flow_yield": 0.04,
            },
            {
                "price": 90,
                "ema20": 96,
                "ema50": 102,
                "ema200": 112,
                "rsi14": 50,
                "drawdown_from_high_pct": -45,
                "gain_20d_pct": -3,
                "fifty_two_week_low": 72,
            },
        )

        self.assertNotIn("drawdown > 40%", result.keyNegativeDrivers)
        self.assertIn("距高点回撤较深", result.keyPositiveDrivers)
        self.assertIn("股价仍低于EMA200，趋势尚未完全修复", result.keyNegativeDrivers)

    def test_low_data_confidence_downgrades_buy_actions(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.28,
                "gross_margin": 0.84,
                "operating_margin": 0.32,
                "free_cash_flow": 4_000,
                "total_revenue": 10_000,
                "price_to_sales": 6,
                "price_to_fcf": 18,
                "free_cash_flow_yield": 0.055,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 92,
                "rsi14": 48,
                "drawdown_from_high_pct": -35,
                "gain_20d_pct": -4,
                "fifty_two_week_low": 70,
            },
        )

        self.assertEqual(result.dataConfidence, "low")
        self.assertNotIn(result.action, {"可小仓分批", "可正常分批"})
        self.assertNotIn("可小仓", result.action)
        self.assertEqual(result.maxSuggestedPositionPercent, 0)
        self.assertEqual(result.currentAddLimitPercent, 0)

    def test_observe_actions_do_not_keep_current_add_limit(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "ORCL",
                "sector": "Technology",
                "industry": "Software - Infrastructure",
                "revenue_growth": 0.04,
                "gross_margin": 0.70,
                "operating_margin": 0.24,
                "free_cash_flow": 12_000,
                "total_revenue": 55_000,
                "total_debt": 95_000,
                "total_cash": 8_000,
                "price_to_sales": 12,
                "price_to_fcf": 45,
            },
            {
                "price": 100,
                "ema20": 105,
                "ema50": 110,
                "ema200": 120,
                "rsi14": 48,
                "drawdown_from_high_pct": -18,
                "gain_20d_pct": 1,
            },
        )

        self.assertIn(result.action, {"只观察", "等回踩"})
        self.assertEqual(result.maxSuggestedPositionPercent, 0)
        self.assertEqual(result.currentAddLimitPercent, 0)

    def test_crypto_medium_high_risk_does_not_emit_exact_buy_action(self) -> None:
        cases = [
            (
                "COIN",
                {
                    "ticker": "COIN",
                    "sector": "Financial Services",
                    "industry": "Capital Markets",
                    "revenue_growth": 0.18,
                    "operating_margin": 0.22,
                    "free_cash_flow": 2_000_000_000,
                    "total_revenue": 6_000_000_000,
                    "total_cash": 7_000_000_000,
                    "total_debt": 3_000_000_000,
                    "price_to_sales": 8,
                },
            ),
            (
                "HOOD",
                {
                    "ticker": "HOOD",
                    "sector": "Financial Services",
                    "industry": "Brokerage",
                    "revenue_growth": 0.22,
                    "operating_margin": 0.16,
                    "free_cash_flow": 1_200_000_000,
                    "total_revenue": 3_500_000_000,
                    "total_cash": 4_000_000_000,
                    "total_debt": 1_000_000_000,
                    "price_to_sales": 10,
                },
            ),
        ]
        technicals = {
            "price": 100,
            "ema20": 98,
            "ema50": 96,
            "ema200": 90,
            "rsi14": 48,
            "drawdown_from_high_pct": -35,
            "gain_20d_pct": -3,
        }

        for symbol, snapshot in cases:
            with self.subTest(symbol=symbol):
                result = calculate_total_score(snapshot, technicals)

                self.assertEqual(result.riskRating, "中高")
                self.assertNotIn(result.action, {"可小仓分批", "可正常分批"})

    def test_score_explanation_panel_is_not_debug_table(self) -> None:
        source = inspect.getsource(_render_score_explanation)

        self.assertNotIn("st.dataframe", source)
        self.assertIn("数据可信度", source)
        self.assertIn("公司质量解释", source)
        self.assertIn("买点解释", source)
        self.assertIn("风险解释", source)
        self.assertIn("数据补全状态", inspect.getsource(_render_metric_resolution_groups))

    def test_scoring_output_includes_position_limit_and_proxy_metadata(self) -> None:
        coin = calculate_total_score(
            {
                "ticker": "COIN",
                "sector": "Financial Services",
                "industry": "Capital Markets",
                "revenue_growth": 0.18,
                "operating_margin": 0.18,
                "free_cash_flow": 2_000_000_000,
                "total_revenue": 6_000_000_000,
                "total_cash": 7_000_000_000,
                "total_debt": 3_000_000_000,
                "price_to_sales": 8,
            },
            {
                "price": 100,
                "ema20": 96,
                "ema50": 94,
                "ema200": 90,
                "rsi14": 48,
                "drawdown_from_high_pct": -35,
                "gain_20d_pct": -3,
            },
        )

        self.assertEqual(coin.modelType, "CRYPTO_FINANCIAL_INFRA")
        self.assertEqual(coin.proxyConfidence, "medium")
        self.assertIn("crypto revenue sensitivity", coin.missingIndustryMetrics)
        self.assertIn("symbol risk proxy", coin.proxyMetricsUsed)
        self.assertLessEqual(coin.maxSuggestedPositionPercent, 5)

        nvo = calculate_total_score(
            {
                "ticker": "NVO",
                "sector": "Healthcare",
                "industry": "Drug Manufacturers",
                "revenue_growth": 0.16,
                "operating_margin": 0.42,
                "free_cash_flow": 9_000_000_000,
                "total_revenue": 38_000_000_000,
                "total_cash": 5_000_000_000,
                "total_debt": 10_000_000_000,
                "forward_pe": 22,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 95,
                "ema200": 90,
                "rsi14": 50,
                "drawdown_from_high_pct": -25,
            },
        )

        self.assertEqual(nvo.qualityRating, "A - 高质量")
        self.assertIn("GLP-1 competition", nvo.keyNegativeDrivers)
        self.assertIn("US pricing pressure", nvo.keyNegativeDrivers)
        self.assertLessEqual(nvo.maxSuggestedPositionPercent, 15)

    def test_mega_cap_derived_factors_are_not_manual_required_missing(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "MSFT",
                "sector": "Technology",
                "industry": "Software - Infrastructure",
                "revenue_growth": 0.12,
                "operating_margin": 0.42,
                "free_cash_flow": 50_000,
                "total_revenue": 100_000,
                "return_on_invested_capital": 0.25,
                "total_cash": 100_000,
                "total_debt": 40_000,
                "forward_pe": 30,
                "price_to_fcf": 28,
                "free_cash_flow_yield": 0.035,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 95,
                "ema200": 90,
                "rsi14": 50,
                "drawdown_from_high_pct": -18,
                "gain_20d_pct": 2,
            },
        )

        self.assertEqual(result.modelType, "MEGA_CAP_PLATFORM")
        fcf = _metric_resolution_by_key(result, "fcfMargin")
        self.assertEqual(fcf["resolutionStatus"], "calculated")

        net_cash = _metric_resolution_by_display(result, "Net Cash / Balance Sheet")
        self.assertIn(net_cash["resolutionStatus"], {"calculated", "derived_score"})
        self.assertNotEqual(net_cash["resolutionStatus"], "manual_override_required")

        historical = _metric_resolution_by_display(result, "Historical valuation percentile")
        self.assertIn(historical["resolutionStatus"], {"calculated", "derived_score"})
        self.assertNotEqual(historical["resolutionStatus"], "manual_override_required")

        for name in ("Segment strength", "Buyback discipline", "Capex concern discount", "AI capex overbuild risk"):
            row = _metric_resolution_by_display(result, name)
            self.assertEqual(row["metricType"], "DERIVED_SCORING_FACTOR")
            self.assertEqual(row["resolutionStatus"], "derived_score")
            self.assertNotEqual(row["recommendedAction"], "需人工补充")

    def test_msft_splits_portfolio_weight_from_current_add_limit(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "MSFT",
                "sector": "Technology",
                "industry": "Software - Infrastructure",
                "revenue_growth": 0.12,
                "operating_margin": 0.42,
                "free_cash_flow": 50_000,
                "total_revenue": 100_000,
                "return_on_invested_capital": 0.25,
                "total_cash": 100_000,
                "total_debt": 40_000,
                "forward_pe": 34,
                "price_to_fcf": 32,
                "price_to_sales": 10,
                "free_cash_flow_yield": 0.031,
            },
            {
                "price": 100,
                "ema20": 102,
                "ema50": 104,
                "ema200": 110,
                "rsi14": 48,
                "drawdown_from_high_pct": -18,
                "gain_20d_pct": 2,
            },
        )

        self.assertEqual(result.modelType, "MEGA_CAP_PLATFORM")
        self.assertTrue(result.qualityRating.startswith(("A+", "A")))
        self.assertEqual(result.riskRating, "低")
        self.assertGreater(result.maxPortfolioWeightPercent, 5)
        self.assertLessEqual(result.currentAddLimitPercent, 5)
        self.assertEqual(result.maxSuggestedPositionPercent, result.currentAddLimitPercent)

    def test_drawer_shows_decision_summary_and_position_guidance(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        result = calculate_total_score(
            {
                "ticker": "MSFT",
                "sector": "Technology",
                "industry": "Software - Infrastructure",
                "revenue_growth": 0.12,
                "operating_margin": 0.42,
                "free_cash_flow": 50_000,
                "total_revenue": 100_000,
                "return_on_invested_capital": 0.25,
                "total_cash": 100_000,
                "total_debt": 40_000,
                "forward_pe": 34,
                "price_to_fcf": 32,
                "price_to_sales": 10,
                "free_cash_flow_yield": 0.031,
            },
            {
                "price": 100,
                "ema20": 102,
                "ema50": 104,
                "ema200": 110,
                "rsi14": 48,
                "drawdown_from_high_pct": -18,
                "gain_20d_pct": 2,
            },
        )
        row = pd.Series(
            dashboard_module._build_dashboard_row(
                "MSFT",
                {
                    "ticker": "MSFT",
                    "company_name": "Microsoft Corporation",
                    "sector": "Technology",
                    "industry": "Software - Infrastructure",
                    "revenue_growth": 0.12,
                    "operating_margin": 0.42,
                    "free_cash_flow": 50_000,
                    "total_revenue": 100_000,
                    "return_on_invested_capital": 0.25,
                    "total_cash": 100_000,
                    "total_debt": 40_000,
                    "forward_pe": 34,
                    "price_to_fcf": 32,
                    "price_to_sales": 10,
                    "free_cash_flow_yield": 0.031,
                    "market_cap": 3_000_000_000_000,
                },
                {
                    "price": 100,
                    "ema20": 102,
                    "ema50": 104,
                    "ema200": 110,
                    "rsi14": 48,
                    "drawdown_from_high_pct": -18,
                    "gain_20d_pct": 2,
                },
                result,
                {"pct": 100, "missing": []},
            )
        )
        html = dashboard_module._drawer_html(row)

        self.assertIn("当前结论", html)
        self.assertIn("当前新增建议", html)
        self.assertIn("组合仓位上限", html)
        self.assertIn("15%-20%", html)
        self.assertIn("只观察不是因为公司质量差", html)
        self.assertIn("风险评级低代表公司基本面风险较低", html)
        self.assertIn("行业专属指标", html)
        self.assertIn("Azure / Cloud 增速", html)
        self.assertIn("AI资本开支压力", html)
        self.assertIn("查看复核项", html)
        self.assertIn("同步复核队列", html)
        self.assertIn("自动补全数据", html)
        self.assertNotIn('target="_blank"', html)
        self.assertNotIn("?page=detail", html)
        self.assertIn("data-dashboard-drawer-message", html)
        self.assertIn("data-dashboard-drawer-action-note", html)
        self.assertNotIn("drawerAction", html)
        self.assertNotIn("drawer-action-bar", html)
        self.assertNotIn("估算FCF利润率", html)

    def test_drawer_review_summary_hides_zero_statuses_by_default(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        row = pd.Series(
            {
                "symbol": "MSFT",
                "reviewQueueSummary": {
                    "total": 3,
                    "pending_review": 0,
                    "needs_data": 2,
                    "derived_low_confidence": 0,
                    "qualitative_risk": 1,
                    "approved": 0,
                    "rejected": 0,
                    "auto_approved_by_ai": 0,
                    "ai_recommend_correct": 0,
                    "ai_recommend_reject": 0,
                    "ai_not_enough_evidence": 0,
                    "ai_needs_human_review": 0,
                },
                "criticalPendingReviewMetrics": [],
            }
        )
        html = dashboard_module._drawer_review_summary_html(row)

        visible = html.split("展开全部状态", 1)[0]
        self.assertIn("需要补齐", visible)
        self.assertIn("定性风险", visible)
        self.assertNotIn("已确认", visible)
        self.assertNotIn("AI自动确认", visible)
        self.assertIn("查看复核项", html)

    def test_direct_fcf_margin_preferred_over_market_derived_value(self) -> None:
        metric = fcf_margin_metric(
            {
                "free_cash_flow": 50_000,
                "total_revenue": 100_000,
                "fcf_margin": 0.2,
                "fcf_margin_sourceType": "derivedFromMarket",
                "free_cash_flow_yield": 0.03,
                "price_to_sales": 8,
            }
        )

        self.assertEqual(metric.sourceType, "calculated")
        self.assertAlmostEqual(metric.value or 0, 0.5)

    def test_drawer_action_buttons_are_not_fixed_floating_controls(self) -> None:
        dashboard_module = __import__("ui.dashboard", fromlist=[""])
        styles_source = inspect.getsource(dashboard_module._render_dashboard_styles)

        self.assertNotIn(".drawer-action-bar", styles_source)
        self.assertNotIn("position: sticky;\n            bottom: -1.15rem", styles_source)
        self.assertIn(".drawer-review-actions", styles_source)

    def test_global_metric_resolution_taxonomy_handles_industry_specific_factors(self) -> None:
        vst = calculate_total_score(
            {
                "ticker": "VST",
                "sector": "Utilities",
                "industry": "Power Generation",
                "market_cap": 40_000_000_000,
                "enterprise_value": 55_000_000_000,
                "ebitda": 5_500_000_000,
                "free_cash_flow": 4_000_000_000,
                "net_debt_to_ebitda": 3.4,
                "enterprise_to_ebitda": 10,
                "current_ratio": 1.1,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 95,
                "ema200": 90,
                "rsi14": 50,
                "drawdown_from_high_pct": -30,
                "gain_20d_pct": 0,
            },
        )
        self.assertFalse(vst.data_insufficient)
        self.assertEqual(_metric_resolution_by_display(vst, "adjusted EBITDA")["resolutionStatus"], "requires_ir_scrape")
        self.assertEqual(_metric_resolution_by_display(vst, "power demand exposure")["resolutionStatus"], "derived_score")
        self.assertEqual(_metric_resolution_by_display(vst, "generation asset quality")["metricType"], "DERIVED_SCORING_FACTOR")

        coin = calculate_total_score(
            {
                "ticker": "COIN",
                "sector": "Financial Services",
                "industry": "Capital Markets",
                "revenue_growth": 0.18,
                "operating_margin": 0.18,
                "free_cash_flow": 2_000_000_000,
                "total_revenue": 6_000_000_000,
                "total_cash": 7_000_000_000,
                "total_debt": 3_000_000_000,
                "price_to_sales": 8,
            },
            {
                "price": 100,
                "ema20": 96,
                "ema50": 94,
                "ema200": 90,
                "rsi14": 48,
                "drawdown_from_high_pct": -35,
                "gain_20d_pct": -3,
            },
        )
        regulatory = _metric_resolution_by_display(coin, "regulatory risk")
        self.assertEqual(regulatory["metricType"], "QUALITATIVE_RISK_FACTOR")
        self.assertEqual(regulatory["resolutionStatus"], "semi_auto_low_confidence")
        self.assertFalse(regulatory["isBlocking"])

        nvo = calculate_total_score(
            {
                "ticker": "NVO",
                "sector": "Healthcare",
                "industry": "Drug Manufacturers",
                "revenue_growth": 0.16,
                "operating_margin": 0.42,
                "free_cash_flow": 9_000_000_000,
                "total_revenue": 38_000_000_000,
                "total_cash": 5_000_000_000,
                "total_debt": 10_000_000_000,
                "forward_pe": 22,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 95,
                "ema200": 90,
                "rsi14": 50,
                "drawdown_from_high_pct": -25,
            },
        )
        self.assertNotEqual(nvo.dataConfidence, "low")
        self.assertEqual(_metric_resolution_by_display(nvo, "pipeline risk")["resolutionStatus"], "semi_auto_low_confidence")
        self.assertEqual(_metric_resolution_by_display(nvo, "patent cliff risk")["metricType"], "QUALITATIVE_RISK_FACTOR")

    def test_bank_and_reit_resolution_do_not_misuse_generic_metrics(self) -> None:
        bank = calculate_total_score(
            {
                "ticker": "JPM",
                "sector": "Financial Services",
                "industry": "Banks",
                "return_on_equity": 0.14,
                "return_on_assets": 0.012,
                "price_to_book": 1.4,
                "forward_pe": 11,
            },
            {"price": 100, "ema20": 99, "ema50": 98, "ema200": 96, "rsi14": 50, "drawdown_from_high_pct": -12},
        )
        ev_fcf = _metric_resolution_by_key(bank, "evToFcf")
        self.assertEqual(ev_fcf["metricType"], "NOT_APPLICABLE")
        self.assertEqual(ev_fcf["resolutionStatus"], "not_applicable")

        reit = calculate_total_score(
            {
                "ticker": "PLD",
                "sector": "Real Estate",
                "industry": "REIT - Industrial",
                "net_debt_to_ebitda": 5.0,
                "price_to_book": 1.8,
                "forward_pe": 25,
            },
            {"price": 100, "ema20": 99, "ema50": 98, "ema200": 96, "rsi14": 50, "drawdown_from_high_pct": -12},
        )
        affo = _metric_resolution_by_key(reit, "affo")
        self.assertEqual(affo["metricType"], "DISCLOSURE_KPI")
        self.assertIn(affo["resolutionStatus"], {"requires_ir_scrape", "manual_override_required"})
        self.assertEqual(_metric_resolution_by_key(reit, "ordinaryPe")["resolutionStatus"], "not_applicable")

    def test_ceg_missing_hedge_coverage_does_not_become_d_quality(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "CEG",
                "sector": "Utilities",
                "industry": "Power Generation",
                "market_cap": 100_000_000_000,
                "enterprise_value": 115_000_000_000,
                "ebitda": 8_500_000_000,
                "free_cash_flow": 4_500_000_000,
                "net_debt_to_ebitda": 2.8,
                "current_ratio": 1.1,
                "enterprise_to_ebitda": 13.5,
            },
            {
                "price": 100,
                "ema20": 96,
                "ema50": 95,
                "ema200": 90,
                "rsi14": 62,
                "drawdown_from_high_pct": -8,
            },
        )

        self.assertEqual(result.modelType, "POWER_GENERATION")
        self.assertFalse(result.data_insufficient)
        self.assertNotIn("D", result.qualityRating)
        self.assertGreaterEqual(result.quality_score, 65)
        self.assertIn("hedge coverage", result.missingIndustryMetrics)
        self.assertEqual(result.dataConfidence, "medium")

    def test_hood_quality_label_avoids_stable_wording(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "HOOD",
                "sector": "Financial Services",
                "industry": "Brokerage",
                "revenue_growth": 0.22,
                "operating_margin": 0.16,
                "free_cash_flow": 1_200_000_000,
                "total_revenue": 3_500_000_000,
                "total_cash": 4_000_000_000,
                "total_debt": 1_000_000_000,
                "price_to_sales": 10,
            },
            {
                "price": 100,
                "ema20": 98,
                "ema50": 96,
                "ema200": 90,
                "rsi14": 50,
                "drawdown_from_high_pct": -30,
            },
        )

        self.assertEqual(result.modelType, "CRYPTO_FINANCIAL_INFRA")
        self.assertNotIn("稳健", result.qualityRating)
        self.assertTrue("成长较强" in result.qualityRating or "高弹性" in result.qualityRating)
        self.assertEqual(result.proxyConfidence, "medium")

    def test_high_risk_never_shows_normal_batch_or_buy_zone(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NBIS",
                "sector": "Technology",
                "industry": "AI infrastructure",
                "revenue_growth": 0.80,
                "gross_margin": 0.50,
                "free_cash_flow": -1_000_000_000,
                "total_revenue": 1_500_000_000,
                "total_debt": 6_000_000_000,
                "total_cash": 100_000_000,
                "price_to_sales": 30,
                "enterprise_to_revenue": 35,
            },
            {
                "price": 100,
                "ema20": 96,
                "ema50": 94,
                "ema200": 80,
                "rsi14": 50,
                "drawdown_from_high_pct": -45,
            },
        )

        self.assertEqual(result.riskRating, "高")
        self.assertNotEqual(result.action, "可正常分批")
        self.assertNotIn("击球区", result.valuationStatus)
        self.assertLessEqual(result.maxSuggestedPositionPercent, 5)

    def test_small_batch_action_caps_position_at_five_percent(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "VST",
                "sector": "Utilities",
                "industry": "Independent Power Producers",
                "market_cap": 40_000_000_000,
                "enterprise_value": 55_000_000_000,
                "ebitda": 5_500_000_000,
                "free_cash_flow": 4_000_000_000,
                "net_debt_to_ebitda": 2.5,
                "current_ratio": 1.1,
            },
            {
                "price": 145,
                "ema50": 150,
                "ema200": 120,
                "rsi14": 45,
                "drawdown_from_high_pct": -30,
            },
        )

        self.assertEqual(result.riskRating, "中")
        self.assertEqual(result.action, "可小仓分批")
        self.assertGreater(result.maxSuggestedPositionPercent, 0)
        self.assertLessEqual(result.maxSuggestedPositionPercent, 5)

    def test_dashboard_core_summary_data_is_enough_for_common_models(self) -> None:
        cases = [
            ("ADBE", "Technology", "Software - Application"),
            ("CRM", "Technology", "Software - Application"),
            ("ORCL", "Technology", "Software - Infrastructure"),
            ("MSFT", "Technology", "Software - Infrastructure"),
            ("ANET", "Technology", "Communication Equipment"),
            ("PLTR", "Technology", "Software - Application"),
            ("MRVL", "Technology", "Semiconductors"),
        ]
        for symbol, sector, industry in cases:
            with self.subTest(symbol=symbol):
                result = calculate_total_score(
                    {
                        "ticker": symbol,
                        "sector": sector,
                        "industry": industry,
                        "revenue_growth": 0.16,
                        "gross_margin": 0.70,
                        "operating_margin": 0.24,
                        "return_on_invested_capital": 0.14,
                        "free_cash_flow": 3_000,
                        "total_revenue": 10_000,
                        "free_cash_flow_yield": 0.035,
                        "price_to_sales": 8,
                        "enterprise_to_revenue": 8.2,
                        "price_to_fcf": 28,
                        "enterprise_to_ebitda": 22,
                        "forward_pe": 30,
                        "current_ratio": 1.3,
                        "total_debt": 100,
                        "total_cash": 250,
                    },
                    {
                        "price": 100,
                        "ema20": 98,
                        "ema50": 96,
                        "ema200": 92,
                        "rsi14": 52,
                        "drawdown_from_high_pct": -20,
                        "gain_20d_pct": 2,
                        "fifty_two_week_low": 75,
                    },
                )

                self.assertFalse(result.data_insufficient)
                self.assertNotEqual(result.action, "数据不足，需复核")

    def test_stock_action_plan_persists_buy_zone_and_position_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = StockPlanStore(Path(tmpdir) / "plans.sqlite")
            saved = store.save_plan(
                "NOW",
                {
                    "target_position_pct": "8",
                    "planned_position_pct": "3",
                    "first_buy_price": "420",
                    "second_buy_price": "390",
                    "third_buy_price": "",
                    "no_chase_above": "520",
                    "fair_value_low": "430",
                    "fair_value_high": "470",
                    "tranche_buy_low": "390",
                    "tranche_buy_high": "430",
                    "heavy_buy_below": "360",
                    "invalidation_condition": "增长明显失速",
                    "earnings_review_points": "RPO / margin",
                    "notes": "只小仓",
                },
            )
            loaded = store.get_plan("NOW")

            self.assertEqual(saved["target_position_pct"], 8)
            self.assertEqual(loaded["tranche_buy_high"], 430)
            self.assertEqual(loaded["heavy_buy_below"], 360)
            self.assertEqual(loaded["invalidation_condition"], "增长明显失速")

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

    def test_trade_journal_store_supports_option_and_skip_actions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            option_entry = store.save_entry(
                "hood",
                {
                    "trade_date": "2026-05-26",
                    "action_type": "sell_put",
                    "premium": "1.2",
                    "strike_price": "25",
                    "expiry_date": "2026-06-19",
                },
            )
            skip_entry = store.save_entry("hood", {"trade_date": "2026-05-27", "action_type": "skip"})

            self.assertEqual(option_entry["symbol"], "HOOD")
            self.assertEqual(option_entry["premium"], 1.2)
            self.assertEqual(option_entry["strike_price"], 25)
            self.assertEqual(skip_entry["action_type"], "skip")

    def test_trade_journal_store_lists_all_entries_and_symbols(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

            store.save_entry("hood", {"trade_date": "2026-05-26", "action_type": "buy"})
            store.save_entry("now", {"trade_date": "2026-05-27", "action_type": "sell"})

            self.assertEqual([entry["symbol"] for entry in store.list_entries()], ["NOW", "HOOD"])
            self.assertEqual(store.list_symbols(), ["HOOD", "NOW"])

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

    def test_portfolio_tables_do_not_replace_stock_action_plans(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "portfolio.sqlite"
            plan_store = StockPlanStore(db_path)
            position_store = PortfolioPositionStore(db_path)

            plan_store.save_plan("NOW", {"notes": "research memo", "first_buy_price": 420})
            position_store.save_position("NOW", {"quantity": 3, "average_cost": 500, "notes": "actual holding"})

            plan = plan_store.get_plan("NOW")
            position = position_store.get_position("NOW")
            self.assertEqual(plan["notes"], "research memo")
            self.assertEqual(plan["first_buy_price"], 420)
            self.assertEqual(position["notes"], "actual holding")
            self.assertEqual(position["quantity"], 3)

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
            PortfolioSettingsStore(db_path).save_settings({"total_portfolio_value": 10000})
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

    def test_buy_zone_engine_generates_system_zone_without_manual_override(self) -> None:
        zone = generate_buy_zone(
            "NOW",
            {
                "price": 100,
                "price_to_fcf": 24,
                "free_cash_flow_yield": 0.042,
                "price_to_sales": 7.5,
                "revenue_growth": 0.18,
                "free_cash_flow": 3_200,
                "total_revenue": 10_000,
                "drawdown_from_high_pct": -35,
                "rsi14": 55,
            },
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )

        self.assertIsNotNone(zone.noChaseAbove)
        self.assertIsNotNone(zone.trancheBuyHigh)
        self.assertIn(zone.currentZone, {"fair_observation", "tranche_buy", "heavy_buy", "below_heavy_buy", "no_chase"})
        self.assertFalse(has_buy_zone_override({}))

    def test_buy_zone_validation_rejects_missing_or_invalid_price(self) -> None:
        zone = generate_buy_zone("ZERO", {"current_price": 0, "price_to_fcf": 20, "free_cash_flow_yield": 0.05}, None, "SAAS_SOFTWARE")

        self.assertEqual(zone.currentZone, "data_insufficient")
        self.assertEqual(zone.confidence, "low")
        self.assertFalse(zone.isValid)
        self.assertIsNone(zone.nextTriggerPrice)
        self.assertIn("当前价格缺失或无效", zone.warnings)

    def test_buy_zone_validation_rejects_non_monotonic_zone(self) -> None:
        zone = validate_buy_zone_estimate(
            BuyZoneEstimate("BAD", "GENERIC", 100, 130, 90, 120, 80, 95, 70, "fair_observation", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )

        self.assertEqual(zone.currentZone, "invalid_zone")
        self.assertEqual(zone.confidence, "low")
        self.assertFalse(zone.isValid)
        self.assertIsNone(zone.nextTriggerPrice)
        self.assertIn("买区区间顺序异常", zone.validationErrors)

    def test_buy_zone_extreme_price_distance_caps_confidence(self) -> None:
        extreme_no_chase = validate_buy_zone_estimate(
            BuyZoneEstimate("HOT", "GENERIC", 100, 260, 115, 120, 90, 100, 70, "fair_observation", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )
        extreme_heavy = validate_buy_zone_estimate(
            BuyZoneEstimate("LOW", "GENERIC", 100, 130, 115, 120, 90, 100, 20, "fair_observation", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )

        self.assertNotEqual(extreme_no_chase.confidence, "high")
        self.assertEqual(extreme_no_chase.currentZone, "invalid_zone")
        self.assertNotEqual(extreme_heavy.confidence, "high")
        self.assertIn("重仓区与当前价偏离过大", extreme_heavy.warnings)

    def test_buy_zone_next_trigger_matches_current_zone(self) -> None:
        fair = validate_buy_zone_estimate(
            BuyZoneEstimate("FAIR", "GENERIC", 110, 130, 105, 120, 90, 100, 70, "fair_observation", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )
        tranche = validate_buy_zone_estimate(
            BuyZoneEstimate("BATCH", "GENERIC", 95, 130, 105, 120, 90, 100, 70, "tranche_buy", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )
        no_chase = validate_buy_zone_estimate(
            BuyZoneEstimate("HOT", "GENERIC", 140, 130, 105, 120, 90, 100, 70, "no_chase", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )

        self.assertEqual(fair.currentZone, "fair_observation")
        self.assertEqual(fair.nextTriggerPrice, 100)
        self.assertEqual(tranche.currentZone, "tranche_buy")
        self.assertIsNone(tranche.nextTriggerPrice)
        self.assertLessEqual(tranche.currentPrice, 100)
        self.assertEqual(no_chase.currentZone, "no_chase")
        self.assertNotIn("可分批", no_chase.action)

    def test_buy_zone_confidence_downgrades_on_low_quality_inputs(self) -> None:
        low_data = generate_buy_zone(
            "LOWDATA",
            {"price": 100, "price_to_fcf": 24, "free_cash_flow_yield": 0.042, "price_to_sales": 7.5, "dataConfidence": "low"},
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )
        implied = generate_buy_zone(
            "IMPLIED",
            {"price": 100, "price_to_fcf": 24, "free_cash_flow_yield": 0.042, "price_to_sales": 7.5, "usedInputs": ["impliedFcfMargin"], "impliedFcfMarginAsPrimaryInput": True},
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )
        pending = generate_buy_zone(
            "PENDING",
            {
                "price": 100,
                "price_to_fcf": 24,
                "free_cash_flow_yield": 0.042,
                "price_to_sales": 7.5,
                "metric_sources": {"price_to_fcf": {"reviewStatus": "pending_review", "sourceType": "IR_RELEASE"}},
            },
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )

        self.assertEqual(low_data.confidence, "low")
        self.assertNotEqual(implied.confidence, "high")
        self.assertNotEqual(pending.confidence, "high")

    def test_saas_buy_zone_uses_cash_flow_and_sales_multiples(self) -> None:
        zone = generate_buy_zone(
            "ADBE",
            {
                "price": 250,
                "price_to_fcf": 22,
                "free_cash_flow_yield": 0.046,
                "price_to_sales": 7,
            },
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )

        self.assertIn("P/FCF", zone.inputsUsed)
        self.assertIn("FCF收益率", zone.inputsUsed)
        self.assertIn("P/S", zone.inputsUsed)
        self.assertEqual(zone.method, "blended")

    def test_buy_zone_normalizes_bad_drawdown_percentages(self) -> None:
        self.assertAlmostEqual(normalize_percent_metric(-0.517), -0.517)
        self.assertAlmostEqual(normalize_percent_metric(-51.7), -0.517)
        self.assertIsNone(normalize_percent_metric(-5170))

        zone = generate_buy_zone(
            "NVDA",
            {"price": 100, "drawdown_from_high_pct": -5170, "price_to_sales": 16},
            {"scoring_model": "SEMICONDUCTOR"},
            "SEMICONDUCTOR",
        )
        self.assertIn("距高点回撤百分比异常，已排除。", zone.warnings)

    def test_direct_fcf_margin_has_priority_over_implied_margin(self) -> None:
        direct, source, formula = direct_fcf_margin(
            {
                "free_cash_flow": 33,
                "total_revenue": 100,
                "free_cash_flow_yield": 0.045,
                "price_to_sales": 8,
            }
        )

        self.assertAlmostEqual(direct, 0.33)
        self.assertEqual(source, "calculated")
        self.assertEqual(formula, "free_cash_flow / revenue")

    def test_unreliable_text_extracted_fcf_margin_does_not_enter_buy_zone(self) -> None:
        direct, source, note = direct_fcf_margin(
            {
                "fcf_margin": 0.4,
                "metric_sources": {"fcf_margin": {"sourceType": "SEC_8K"}},
            }
        )

        self.assertIsNone(direct)
        self.assertEqual(source, "needs_review")
        self.assertIn("不参与买区引擎", note)

    def test_position_plan_autofills_buy_prices_and_splits_position_concepts(self) -> None:
        zone = generate_buy_zone(
            "MSFT",
            {
                "price": 420,
                "price_to_fcf": 28,
                "free_cash_flow_yield": 0.036,
                "price_to_sales": 11,
            },
            {"scoring_model": "MEGA_CAP_PLATFORM"},
            "MEGA_CAP_PLATFORM",
        )
        suggestion = generate_position_plan(
            "MSFT",
            zone,
            {"quality_rating": "A- 高质量", "entry_rating": "B - 等回踩", "risk_rating": "低", "action": "只观察"},
        )

        if suggestion.firstBuyPrice is not None:
            self.assertLessEqual(suggestion.firstBuyPrice, zone.currentPrice)
        self.assertEqual(suggestion.thirdBuyPrice, zone.heavyBuyBelow)
        self.assertGreater(suggestion.maxPortfolioWeightPercent, suggestion.currentAddLimitPercent)
        self.assertGreaterEqual(suggestion.maxPortfolioWeightPercent, 15)
        self.assertLessEqual(suggestion.currentAddLimitPercent, 5)

    def test_position_plan_never_waits_for_higher_price_inside_buy_zone(self) -> None:
        zone = validate_buy_zone_estimate(
            BuyZoneEstimate("BATCH", "GENERIC", 95, 130, 105, 120, 90, 100, 70, "tranche_buy", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )
        suggestion = generate_position_plan("BATCH", zone, {"risk_rating": "低", "entry_rating": "B"})

        self.assertEqual(zone.currentZone, "tranche_buy")
        self.assertIsNone(zone.nextTriggerPrice)
        self.assertLessEqual(suggestion.firstBuyPrice, zone.currentPrice)
        self.assertEqual(suggestion.firstBuyLabel, "已进入可分批区")

    def test_position_plan_blocks_high_risk_and_invalid_zone_adds(self) -> None:
        valid_zone = validate_buy_zone_estimate(
            BuyZoneEstimate("RISK", "GENERIC", 95, 130, 105, 120, 90, 100, 70, "tranche_buy", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )
        invalid_zone = validate_buy_zone_estimate(
            BuyZoneEstimate("BAD", "GENERIC", 100, 130, 90, 120, 80, 95, 70, "fair_observation", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )

        high_risk = generate_position_plan("RISK", valid_zone, {"risk_rating": "高", "entry_rating": "A"})
        invalid = generate_position_plan("BAD", invalid_zone, {"risk_rating": "低", "entry_rating": "A"})
        self.assertEqual(high_risk.currentAddLimitPercent, 0)
        self.assertEqual(invalid.currentAddLimitPercent, 0)

    def test_position_plan_requires_exact_buy_action_for_current_add(self) -> None:
        zone = validate_buy_zone_estimate(
            BuyZoneEstimate("OBS", "GENERIC", 95, 130, 105, 120, 90, 100, 70, "tranche_buy", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )

        observe = generate_position_plan("OBS", zone, {"risk_rating": "低", "entry_rating": "A", "action": "只观察"})
        wait = generate_position_plan("WAIT", zone, {"risk_rating": "低", "entry_rating": "A", "action": "等回踩"})
        exact_buy = generate_position_plan("BUY", zone, {"risk_rating": "低", "entry_rating": "A", "action": "可小仓分批"})

        self.assertEqual(observe.currentAddLimitPercent, 0)
        self.assertEqual(wait.currentAddLimitPercent, 0)
        self.assertGreater(exact_buy.currentAddLimitPercent, 0)

    def test_position_plan_blocks_low_confidence_current_add(self) -> None:
        zone = validate_buy_zone_estimate(
            BuyZoneEstimate("LOW", "GENERIC", 95, 130, 105, 120, 90, 100, 70, "tranche_buy", "high", "blended", ["P/FCF", "P/S"], [], [], "now"),
            {},
            None,
        )

        suggestion = generate_position_plan(
            "LOW",
            zone,
            {"risk_rating": "低", "entry_rating": "A", "action": "可小仓分批", "data_confidence": "low"},
        )

        self.assertEqual(suggestion.currentAddLimitPercent, 0)

    def test_buy_zone_short_action_prioritizes_no_chase_over_buy_wording(self) -> None:
        label = buy_zone._action_short_text(
            {
                "currentZone": "no_chase",
                "currentPrice": 100,
                "action": "可小仓分批",
                "dataConfidence": "medium",
                "confidence": "high",
                "isValid": True,
            }
        )

        self.assertEqual(label, "不新增")

    def test_manual_buy_zone_override_takes_priority_and_can_be_cleared(self) -> None:
        system = generate_buy_zone(
            "NOW",
            {"price": 100, "price_to_fcf": 24, "free_cash_flow_yield": 0.04, "price_to_sales": 7},
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )
        manual_plan = {
            "no_chase_above": 150,
            "fair_value_low": 110,
            "fair_value_high": 130,
            "tranche_buy_low": 90,
            "tranche_buy_high": 105,
            "heavy_buy_below": 80,
        }
        active = buy_zone_with_manual_override(system, manual_plan)
        cleared = clear_buy_zone_override_values(manual_plan)

        self.assertTrue(has_buy_zone_override(manual_plan))
        self.assertEqual(active.noChaseAbove, 150)
        self.assertFalse(has_buy_zone_override(cleared))

    def test_invalid_manual_buy_zone_override_is_marked_low_confidence(self) -> None:
        system = generate_buy_zone(
            "NOW",
            {"price": 100, "price_to_fcf": 24, "free_cash_flow_yield": 0.04, "price_to_sales": 7},
            {"scoring_model": "SAAS_SOFTWARE"},
            "SAAS_SOFTWARE",
        )
        manual_plan = {
            "no_chase_above": 150,
            "fair_value_high": 110,
            "fair_value_low": 130,
            "tranche_buy_high": 105,
            "tranche_buy_low": 90,
            "heavy_buy_below": 80,
        }
        active = buy_zone_with_manual_override(system, manual_plan)

        self.assertEqual(active.currentZone, "invalid_manual_override")
        self.assertEqual(active.confidence, "low")
        self.assertFalse(active.isValid)

    def test_stock_plan_store_can_restore_system_buy_zone(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = StockPlanStore(Path(tmpdir) / "plans.sqlite")
            store.save_plan("NOW", {"no_chase_above": "150", "tranche_buy_high": "100", "notes": "manual"})

            restored = store.clear_buy_zone_override("NOW")

            self.assertIsNone(restored["no_chase_above"])
            self.assertIsNone(restored["tranche_buy_high"])
            self.assertEqual(restored["notes"], "manual")

    def test_stock_detail_buy_zone_no_longer_defaults_to_blank_notice(self) -> None:
        source = inspect.getsource(stock_detail._render_buy_zone)

        self.assertNotIn('st.info("尚未设置，需要人工配置。")', source)
        self.assertIn("系统建议击球区", source)

    def test_stock_detail_prefers_final_decision_for_action_and_position(self) -> None:
        final_decision = SimpleNamespace(
            finalAction="只观察",
            currentAddLimitPercent=0,
            maxPortfolioWeightPercent=12,
        )
        score = SimpleNamespace(
            action="可小仓分批",
            current_add_limit_percent=5,
            max_suggested_position_percent=5,
            max_portfolio_weight_percent=20,
            scoring_model="GENERIC",
            risk_rating="中",
        )
        plan = SimpleNamespace(currentAddLimitPercent=8, maxPortfolioWeightPercent=25)
        zone = BuyZoneEstimate(
            "DETAIL",
            "GENERIC",
            110,
            130,
            105,
            120,
            90,
            100,
            70,
            "fair_observation",
            "high",
            "blended",
            [],
            [],
            [],
            "now",
        )

        self.assertEqual(stock_detail._final_action_text(score, final_decision), "只观察")
        self.assertEqual(stock_detail._final_current_add(score, final_decision, plan), 0)
        self.assertEqual(stock_detail._final_max_position(score, final_decision, plan), 12)
        self.assertIn("只观察", stock_detail._decision_summary_text(score, zone, final_decision))
        self.assertNotIn("可小仓分批", stock_detail._decision_summary_text(score, zone, final_decision))

    def test_sec_saas_supplement_extracts_sbc_and_rpo_growth(self) -> None:
        companyfacts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {"USD": [{"val": 10_000, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "OperatingIncomeLoss": {
                        "units": {"USD": [{"val": 2_000, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "NetIncomeLoss": {
                        "units": {"USD": [{"val": 1_500, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "NetCashProvidedByUsedInOperatingActivities": {
                        "units": {"USD": [{"val": 3_000, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "PaymentsToAcquirePropertyPlantAndEquipment": {
                        "units": {"USD": [{"val": 500, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "ShareBasedCompensation": {
                        "units": {"USD": [{"val": 1_100, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "WeightedAverageNumberOfDilutedSharesOutstanding": {
                        "units": {"shares": [{"val": 250, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "LongTermDebtNoncurrent": {
                        "units": {"USD": [{"val": 2_500, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]}
                    },
                    "CashAndCashEquivalentsAtCarryingValue": {
                        "units": {"USD": [{"val": 1_800, "end": "2025-12-31", "fy": 2025, "fp": "Q4"}]}
                    },
                    "RemainingPerformanceObligation": {
                        "units": {
                            "USD": [
                                {"val": 1_200, "end": "2025-12-31", "fy": 2025, "fp": "Q4"},
                                {"val": 1_000, "end": "2024-12-31", "fy": 2024, "fp": "Q4"},
                            ]
                        }
                    },
                    "DeferredRevenueCurrent": {
                        "units": {
                            "USD": [
                                {"val": 550, "end": "2025-12-31", "fy": 2025, "fp": "Q4"},
                                {"val": 500, "end": "2024-12-31", "fy": 2024, "fp": "Q4"},
                            ]
                        }
                    },
                }
            }
        }

        supplement = extract_sec_saas_metrics(companyfacts)

        self.assertEqual(supplement["total_revenue"], 10_000)
        self.assertEqual(supplement["operating_income"], 2_000)
        self.assertEqual(supplement["operating_cash_flow"], 3_000)
        self.assertEqual(supplement["capital_expenditures"], 500)
        self.assertEqual(supplement["free_cash_flow"], 2_500)
        self.assertAlmostEqual(supplement["operating_margin"], 0.20)
        self.assertAlmostEqual(supplement["fcf_margin"], 0.25)
        self.assertEqual(supplement["stock_based_compensation"], 1_100)
        self.assertAlmostEqual(supplement["sbc_ratio"], 0.11)
        self.assertEqual(supplement["diluted_shares"], 250)
        self.assertEqual(supplement["total_debt"], 2_500)
        self.assertEqual(supplement["total_cash"], 1_800)
        self.assertAlmostEqual(supplement["rpo_growth"], 0.20)
        self.assertAlmostEqual(supplement["deferred_revenue_growth"], 0.10)
        self.assertEqual(supplement["metric_sources"]["total_revenue"]["sourceType"], "reported_sec")
        self.assertEqual(supplement["metric_sources"]["fcf_margin"]["sourceType"], "calculated")
        self.assertNotIn("manualRpoGrowth", supplement)

    def test_ir_kpi_mapping_and_parser_keep_company_specific_large_customer_labels(self) -> None:
        self.assertEqual(kpi_mapping_for_ticker("NOW")["large_customer_growth"].label, "customers over $1M / $5M ACV")
        self.assertEqual(kpi_mapping_for_ticker("DDOG")["large_customer_growth"].label, "customers over $100k ARR")
        parsed = parse_ir_kpi_text(
            "NOW",
            "Subscription revenue grew 22%. cRPO growth was 18%. Non-GAAP operating margin was 31%. "
            "Customers over $1M ACV increased 28%.",
        )

        self.assertAlmostEqual(parsed["subscription_revenue_growth"], 0.22)
        self.assertAlmostEqual(parsed["crpo_growth"], 0.18)
        self.assertAlmostEqual(parsed["non_gaap_operating_margin"], 0.31)
        self.assertAlmostEqual(parsed["large_customer_growth"], 0.28)
        self.assertEqual(parsed["metric_sources"]["non_gaap_operating_margin"]["sourceType"], "non_gaap_reported")

    def test_disclosure_text_extractor_keeps_source_snippet(self) -> None:
        definition = metric_definition_by_key("cRpoGrowth")
        self.assertIsNotNone(definition)

        extracted = extractMetricFromText(
            "Current remaining performance obligations were $11.3 billion, representing 21% year-over-year growth.",
            definition,
            confidence="medium",
        )

        self.assertIsNotNone(extracted)
        self.assertAlmostEqual(extracted.value, 0.21)
        self.assertEqual(extracted.unit, "percent")
        self.assertIn("Current remaining performance obligations", extracted.extracted_text)

    def test_crpo_ratio_text_is_not_extracted_as_growth(self) -> None:
        definition = metric_definition_by_key("cRpoGrowth")
        self.assertIsNotNone(definition)

        extracted = extractMetricFromText(
            "Current Remaining Performance Obligations were 67 percent of remaining performance obligations.",
            definition,
            confidence="medium",
        )

        self.assertIsNone(extracted)

    def test_crpo_yoy_growth_text_is_extracted(self) -> None:
        definition = metric_definition_by_key("cRpoGrowth")
        self.assertIsNotNone(definition)

        extracted = extractMetricFromText(
            "Current remaining performance obligations grew 25% year-over-year in Q4 2025.",
            definition,
            confidence="medium",
        )

        self.assertIsNotNone(extracted)
        self.assertAlmostEqual(extracted.value, 0.25)

    def test_non_gaap_net_income_is_not_operating_margin(self) -> None:
        definition = metric_definition_by_key("nonGaapOperatingMargin")
        self.assertIsNotNone(definition)

        extracted = extractMetricFromText(
            "Non-GAAP net income was $2.0 billion and the effective tax rate was 18%.",
            definition,
            confidence="medium",
        )

        self.assertIsNone(extracted)

    def test_generic_cash_flow_text_is_not_fcf_margin(self) -> None:
        definition = metric_definition_by_key("fcfMargin")
        self.assertIsNotNone(definition)

        extracted = extractMetricFromText(
            "Free cash flow was $1.4 billion and revenue grew 12%.",
            definition,
            confidence="medium",
        )

        self.assertIsNone(extracted)

    def test_disclosure_store_prioritizes_official_release_over_transcript(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            store.save_metric(
                symbol="NOW",
                metric_key="nonGaapOperatingMargin",
                value=0.29,
                unit="percent",
                period="2026 Q1",
                source_type="FMP_TRANSCRIPT",
                source_url="https://example.com/transcript",
                source_document_title="Transcript",
                extracted_text="non-GAAP operating margin was 29%",
                confidence="low",
            )
            store.save_metric(
                symbol="NOW",
                metric_key="nonGaapOperatingMargin",
                value=0.31,
                unit="percent",
                period="2026 Q1",
                source_type="IR_RELEASE",
                source_url="https://example.com/release",
                source_document_title="Earnings release",
                extracted_text="non-GAAP operating margin was 31%",
                confidence="medium",
                review_status="approved",
            )

            supplement = store.metric_supplement("NOW")

            self.assertAlmostEqual(supplement["non_gaap_operating_margin"], 0.31)
            self.assertEqual(supplement["metric_sources"]["non_gaap_operating_margin"]["sourceType"], "non_gaap_reported")
            self.assertEqual(supplement["metric_sources"]["non_gaap_operating_margin"]["confidence"], "medium")
            self.assertEqual(supplement["disclosureMetrics"][0]["sourceType"], "IR_RELEASE")

    def test_calculated_metrics_cover_saas_missing_fields(self) -> None:
        metrics = {
            metric.metricKey: metric
            for metric in calculate_metrics(
                {
                    "ticker": "NOW",
                    "stock_based_compensation": 900,
                    "total_revenue": 10_000,
                    "total_debt": 4_000,
                    "total_cash": 1_500,
                    "ebitda": 2_500,
                    "ebit": 2_000,
                    "interest_expense": 250,
                    "free_cash_flow": 2_800,
                    "current_price": 90,
                    "fifty_two_week_high": 120,
                },
                technicals={"gain_20d_pct": 5, "ema20": 88, "ema50": 85, "ema200": 80, "rsi14": 52},
            )
        }

        self.assertAlmostEqual(metrics["sbcToRevenue"].value, 0.09)
        self.assertAlmostEqual(metrics["netDebt"].value, 2_500)
        self.assertAlmostEqual(metrics["netDebtToEbitda"].value, 1.0)
        self.assertAlmostEqual(metrics["interestCoverage"].value, 8.0)
        self.assertAlmostEqual(metrics["fcfMargin"].value, 0.28)
        self.assertAlmostEqual(metrics["drawdownFrom52WeekHigh"].value, -25.0)
        self.assertEqual(metrics["ema200"].sourceType, "CALCULATED")

    def test_calculated_metric_zero_denominator_returns_reason(self) -> None:
        metrics = {metric.metricKey: metric for metric in calculate_metrics({"free_cash_flow": 100, "total_revenue": 0})}

        self.assertIsNone(metrics["fcfMargin"].value)
        self.assertEqual(metrics["fcfMargin"].reason, "denominator is zero")

    def test_metric_source_map_marks_ir_only_saas_kpis(self) -> None:
        source = metric_source_definition("subscriptionRevenueGrowth")

        self.assertIsNotNone(source)
        self.assertFalse(source.canCalculate)
        self.assertIn("IR_RELEASE", source.preferredSources)
        self.assertEqual(source.missingImpact, "CRITICAL_QUALITY")

    def test_sec_client_exposes_user_agent_and_rate_limit(self) -> None:
        client = SECClient()

        self.assertTrue(client.user_agent)
        self.assertEqual(client.max_requests_per_second, SEC_MAX_REQUESTS_PER_SECOND)
        self.assertEqual(SEC_MAX_REQUESTS_PER_SECOND, 5)

    def test_disclosure_store_saves_source_confidence_and_missing_resolution(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            store.save_metric(
                symbol="NOW",
                metric_key="sbcToRevenue",
                value=0.09,
                unit="percent",
                period="latest",
                source_type="CALCULATED",
                source_url=None,
                source_document_title="Calculated from structured FMP / price data",
                extracted_text="stockBasedCompensation / revenue",
                confidence="high",
            )
            store.save_resolution(
                "NOW",
                "subscriptionRevenueGrowth",
                "manual_override_required",
                "IR_RELEASE, SEC_8K_EXHIBIT_99_1",
                "Automatic sources did not return Subscription revenue growth.",
                "manual_override_required",
            )

            supplement = store.metric_supplement("NOW", scoring_only=False)

            self.assertAlmostEqual(supplement["sbc_ratio"], 0.09)
            self.assertEqual(supplement["metric_sources"]["sbc_ratio"]["sourceType"], "calculated")
            self.assertEqual(supplement["metric_sources"]["sbc_ratio"]["confidence"], "high")
            self.assertEqual(supplement["missingMetricResolutions"][0]["status"], "manual_override_required")

    def test_review_approved_disclosure_value_can_enter_scoring(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            store.save_metric(
                symbol="NOW",
                metric_key="subscriptionRevenueGrowth",
                value=0.22,
                unit="percent",
                period="2026 Q1",
                source_type="IR_RELEASE",
                source_url="https://example.com/release",
                source_document_title="Release",
                extracted_text="subscription revenue grew 22%",
                confidence="high",
            )
            metric_id = store.get_metrics("NOW")[0]["id"]
            store.update_review_status(metric_id, "approved")

            supplement = store.metric_supplement("NOW")

            self.assertAlmostEqual(supplement["subscription_revenue_growth"], 0.22)
            self.assertEqual(supplement["metric_sources"]["subscription_revenue_growth"]["reviewStatus"], "approved")

    def test_review_rejected_disclosure_value_never_enters_scoring(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            store.save_metric(
                symbol="NOW",
                metric_key="subscriptionRevenueGrowth",
                value=0.22,
                unit="percent",
                period="2026 Q1",
                source_type="IR_RELEASE",
                source_url="https://example.com/release",
                source_document_title="Release",
                extracted_text="subscription revenue grew 22%",
                confidence="high",
            )
            metric_id = store.get_metrics("NOW")[0]["id"]
            store.update_review_status(metric_id, "rejected")

            supplement = store.metric_supplement("NOW", scoring_only=False)

            self.assertNotIn("subscription_revenue_growth", supplement)
            self.assertEqual(supplement["disclosureMetrics"][0]["reviewStatus"], "rejected")

    def test_review_manual_correction_takes_priority_over_auto_value(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            store.save_metric(
                symbol="NOW",
                metric_key="nonGaapOperatingMargin",
                value=0.29,
                unit="percent",
                period="2026 Q1",
                source_type="IR_RELEASE",
                source_url="https://example.com/release",
                source_document_title="Release",
                extracted_text="non-GAAP operating margin was 29%",
                confidence="high",
                review_status="approved",
            )
            store.save_metric(
                symbol="NOW",
                metric_key="nonGaapOperatingMargin",
                value=0.31,
                unit="percent",
                period="2026 Q1",
                source_type="IR_PRESENTATION",
                source_url="https://example.com/deck",
                source_document_title="Presentation",
                extracted_text="non-GAAP operating margin was 31%",
                confidence="medium",
            )
            metric_id = [row for row in store.get_metrics("NOW") if row["sourceType"] == "IR_PRESENTATION"][0]["id"]
            store.correct_metric(metric_id, 0.34, "percent", "2026 Q1", "Matched the earnings table.")

            supplement = store.metric_supplement("NOW")

            self.assertAlmostEqual(supplement["non_gaap_operating_margin"], 0.34)
            self.assertEqual(supplement["metric_sources"]["non_gaap_operating_margin"]["reviewStatus"], "manually_corrected")
            self.assertEqual(supplement["metric_sources"]["non_gaap_operating_margin"]["confidence"], "high")

    def test_review_pending_low_and_medium_values_are_explanation_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            store.save_metric(
                symbol="NOW",
                metric_key="netRetentionRate",
                value=1.18,
                unit="percent",
                period="2026 Q1",
                source_type="FMP_TRANSCRIPT",
                source_url="https://example.com/transcript",
                source_document_title="Transcript",
                extracted_text="net retention was 118%",
                confidence="low",
            )
            store.save_metric(
                symbol="NOW",
                metric_key="largeCustomerGrowth",
                value=0.20,
                unit="percent",
                period="2026 Q1",
                source_type="IR_PRESENTATION",
                source_url="https://example.com/deck",
                source_document_title="Presentation",
                extracted_text="large customers grew 20%",
                confidence="medium",
            )

            supplement = store.metric_supplement("NOW", scoring_only=False)

            self.assertNotIn("net_retention_rate", supplement)
            self.assertNotIn("large_customer_growth", supplement)
            self.assertEqual({row["reviewStatus"] for row in supplement["disclosureMetrics"]}, {"pending_review"})

    def test_scoring_input_gate_allows_only_trusted_statuses(self) -> None:
        allowed = [
            {"reviewStatus": "approved", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "manually_corrected", "sourceType": "MANUAL_CORRECTION"},
            {"reviewStatus": "auto_approved_by_ai", "sourceType": "SEC_8K"},
            {"sourceType": "CALCULATED"},
            {"sourceType": "FMP"},
            {"sourceType": "MANUAL", "reviewStatus": "approved", "reviewedBy": "local_user"},
        ]
        forbidden = [
            {"reviewStatus": "pending_review", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "needs_evidence", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "needs_data", "sourceType": "metric_resolution"},
            {"reviewStatus": "rejected", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "auto_archived", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "duplicate_archived", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "invalid_review_item", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "not_enough_evidence", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "stale", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "undone", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "approved", "freshnessStatus": "historical_value", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "approved", "itemType": "evidence_missing_extracted_value", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "approved", "aiTriageStatus": "extraction_rejected_by_rule", "sourceType": "IR_RELEASE"},
            {"reviewStatus": "approved", "resolutionStatus": "company_not_disclosed", "sourceType": "metric_resolution"},
            {"reviewStatus": "approved", "resolutionStatus": "manual_override_required", "sourceType": "metric_resolution"},
            {"reviewStatus": "approved", "resolutionStatus": "semi_auto_low_confidence", "sourceType": "metric_resolution"},
            {"reviewStatus": "approved", "resolutionStatus": "low_confidence_derived", "sourceType": "metric_resolution"},
            {"sourceType": "MANUAL", "reviewStatus": "approved", "reviewedBy": "ai"},
        ]

        self.assertTrue(all(canMetricEnterScoring(row) for row in allowed))
        self.assertFalse(any(canMetricEnterScoring(row) for row in forbidden))
        self.assertTrue(canMetricEnterScoring({"resolutionStatus": "low_confidence_derived", "scoring_allowed": True}))

    def test_scoring_only_metric_supplement_filters_forbidden_review_states(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            statuses = [
                "pending_review",
                "rejected",
                "stale",
                "undone",
                "manually_corrected",
                "auto_approved_by_ai",
                "approved",
            ]
            for index, status in enumerate(statuses, start=1):
                store.save_metric(
                    symbol="NOW",
                    metric_key="subscriptionRevenueGrowth",
                    value=index / 100,
                    unit="percent",
                    period=f"2026 Q{index}",
                    source_type="IR_RELEASE",
                    source_url=f"https://example.com/{index}",
                    source_document_title="Release",
                    extracted_text=f"subscription revenue grew {index}%",
                    confidence="high",
                    review_status=status,
                )
            with store.connect() as conn:
                conn.execute("UPDATE disclosure_metric_values SET freshnessStatus = 'active_current'")

            supplement = store.metric_supplement("NOW", scoring_only=True)
            payload_statuses = {row["reviewStatus"] for row in supplement["disclosureMetrics"]}

            self.assertLessEqual(payload_statuses, {"approved", "manually_corrected", "auto_approved_by_ai"})
            self.assertIn("subscription_revenue_growth", supplement)

    def test_provider_uses_scoring_only_disclosure_supplement(self) -> None:
        class _Disclosure:
            def __init__(self) -> None:
                self.calls: list[bool] = []

            def metric_supplement(self, ticker: str, scoring_only: bool = True) -> dict:
                self.calls.append(scoring_only)
                if scoring_only:
                    return {"disclosureMetrics": []}
                return {
                    "subscription_revenue_growth": 0.99,
                    "metric_sources": {
                        "subscription_revenue_growth": {
                            "sourceType": "reported_ir",
                            "reviewStatus": "pending_review",
                        }
                    },
                }

        class _Fundamentals:
            def get_manual_overrides(self, ticker: str) -> dict:
                return {}

        class _Supplement:
            def get_supplement(self, *args, **kwargs) -> dict:
                return {}

        disclosure = _Disclosure()
        provider = FMPProvider(
            api_key="test",
            fundamental_cache=_Fundamentals(),
            sec_supplement=_Supplement(),
            ir_kpi_client=_Supplement(),
            disclosure_store=disclosure,
        )

        snapshot = provider._with_supplements("NOW", {"ticker": "NOW", "symbol": "NOW", "modelType": "SAAS_SOFTWARE"})

        self.assertEqual(disclosure.calls, [True])
        self.assertNotIn("subscription_revenue_growth", snapshot)

    def test_sector_model_blocks_raw_pending_review_metric_sources(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "symbol": "NOW",
                "modelType": "SAAS_SOFTWARE",
                "subscription_revenue_growth": 0.40,
                "metric_sources": {
                    "subscription_revenue_growth": {
                        "sourceType": "reported_ir",
                        "reviewStatus": "pending_review",
                    }
                },
            },
            {},
        )
        resolution = _metric_resolution_by_key(result, "subscriptionRevenueGrowth")

        self.assertEqual(resolution["resolutionStatus"], "requires_ir_scrape")
        self.assertFalse(
            metric_participates_in_score(
                {
                    "subscription_revenue_growth": 0.40,
                    "metric_sources": {
                        "subscription_revenue_growth": {
                            "sourceType": "reported_ir",
                            "reviewStatus": "pending_review",
                        }
                    },
                },
                "subscription_revenue_growth",
            )
        )

    def test_ai_or_autopilot_manual_source_cannot_bypass_scoring_gate(self) -> None:
        self.assertFalse(
            canMetricEnterScoring(
                {
                    "sourceType": "MANUAL",
                    "reviewStatus": "approved",
                    "reviewedBy": "autopilot",
                }
            )
        )
        self.assertTrue(
            canMetricEnterScoring(
                {
                    "sourceType": "MANUAL",
                    "reviewStatus": "approved",
                    "reviewedBy": "local_user",
                }
            )
        )

    def test_critical_pending_review_caps_data_confidence_below_high(self) -> None:
        enriched = enrich_data_confidence(
            {
                "ticker": "NOW",
                "modelType": "SAAS_SOFTWARE",
                "revenue_growth": 0.20,
                "operating_margin": 0.25,
                "fcf_margin": 0.30,
                "sbc_ratio": 0.08,
                "net_debt_to_ebitda": 1.0,
                "interest_coverage": 8.0,
                "subscription_revenue_growth": 0.21,
                "rpo_growth": 0.18,
                "non_gaap_operating_margin": 0.31,
                "net_retention_rate": 1.10,
                "large_customer_growth": 0.20,
                "peg_ratio": 1.6,
                "criticalPendingReviewMetrics": ["Subscription revenue growth"],
            }
        )

        self.assertEqual(enriched["dataConfidence"], "medium")
        self.assertIn("Subscription revenue growth", enriched["pendingReviewCriticalMetrics"])

    def test_manual_review_page_exposes_review_statuses_and_actions(self) -> None:
        source = inspect.getsource(manual_review)

        for status in ("pending_review", "needs_data", "approved", "rejected", "manually_corrected"):
            self.assertIn(status, source)
        self.assertIn("一键自动处理当前筛选结果", source)
        self.assertIn("仅同步复核队列", source)
        self.assertIn("仅运行数据补全", source)
        self.assertIn("确认", source)
        self.assertIn("驳回", source)
        self.assertIn("保存修正", source)
        self.assertIn("仅运行 Qwen 证据复核", source)
        self.assertIn("未配置 Qwen 复核，仍可手动复核。", source)

    def test_manual_review_client_filter_enforces_selected_symbol(self) -> None:
        rows = [
            {"symbol": "NOW", "metricKey": "peg", "sourceType": "IR_RELEASE", "confidence": "medium", "affects": "Entry"},
            {"symbol": "AVGO", "metricKey": "peg", "sourceType": "IR_RELEASE", "confidence": "medium", "affects": "Entry"},
            {"symbol": "AVGO", "metricKey": "rpoGrowth", "sourceType": "SEC_8K", "confidence": "low", "affects": "Quality"},
        ]

        filtered = manual_review._client_filter_review_rows(
            rows,
            {
                "symbol": "AVGO",
                "metric_key": None,
                "item_type": None,
                "source_type": "IR_RELEASE",
                "confidence": "medium",
                "review_status": None,
                "model_type": None,
                "affects_scoring": True,
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["symbol"], "AVGO")
        self.assertEqual(filtered[0]["metricKey"], "peg")

    def test_manual_review_tabs_read_radio_widget_state(self) -> None:
        source = inspect.getsource(manual_review._apply_ai_filters)

        self.assertIn("review-active-tab-radio", source)
        self.assertIn('st.session_state["review-active-tab"] = tab', source)

    def test_missing_kpi_uses_planner_not_evidence_validator(self) -> None:
        row = {"id": 1, "itemType": "missing_kpi", "affects": "Quality", "resolutionStatus": "requires_ir_scrape"}

        decision = classify_review_item(row, "assisted")

        self.assertEqual(decision["automationDecision"], "needs_more_source")
        self.assertIn("KPI", decision["explanationZh"])

    def test_qualitative_risk_uses_classifier_not_evidence_validator(self) -> None:
        row = {"id": 1, "itemType": "qualitative_risk", "affects": "Risk", "metricKey": "regulatoryRisk"}

        decision = classify_review_item(row, "assisted")

        self.assertEqual(decision["automationDecision"], "needs_human_review")
        self.assertIn("定性风险", decision["explanationZh"])

    def test_derived_low_confidence_can_auto_archive_when_explanation_only(self) -> None:
        row = {"id": 1, "itemType": "derived_low_confidence", "affects": "ExplanationOnly"}

        decision = classify_review_item(row, "assisted")

        self.assertEqual(decision["automationDecision"], "auto_archive")

    def test_low_impact_confidence_only_item_auto_archives(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "auto.sqlite")
            item = _insert_review_item(
                store,
                metric_key="forwardRevenueMultiple",
                item_type="analyst_estimate_needed",
                affects="Entry",
                value=None,
                source_type="",
                resolution_status="requires_analyst_estimates",
            )
            decision = classify_review_item(item, "assisted")
            action = apply_automation_result(item, decision, store, AIReviewStore(store.path))
            updated = store.get_item(int(item["id"]))

            self.assertEqual(action, "ai_auto_archived")
            self.assertEqual(updated["reviewStatus"], "auto_archived")
            self.assertEqual(updated["aiTriageStatus"], "ai_auto_archived")

    def test_extracted_value_exact_match_still_uses_qwen_auto_confirm(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "auto.sqlite")
            item = _insert_review_item(store)
            service = ReviewAutomationService(
                queue_store=store,
                ai_store=AIReviewStore(store.path),
                qwen_service=QwenReviewService(
                    queue_store=store,
                    ai_store=AIReviewStore(store.path),
                    client=_FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.95)),
                ),
            )

            result = service.automate_rows([item], mode="assisted")
            updated = store.get_item(int(item["id"]))

            self.assertEqual(result.auto_approved, 1)
            self.assertEqual(updated["reviewStatus"], "approved")
            self.assertEqual(updated["aiTriageStatus"], "auto_approved_by_ai")

    def test_auto_archived_items_are_counted_as_automated_not_core_approved(self) -> None:
        rows = [
            {"reviewStatus": "auto_archived", "aiTriageStatus": "ai_auto_archived"},
            {"reviewStatus": "pending_review", "aiTriageStatus": ""},
        ]

        stats = automation_effectiveness(rows)

        self.assertEqual(stats["autoHandled"], 1)
        self.assertEqual(stats["humanRemaining"], 1)

    def test_automation_operation_log_records_empty_eligible_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "auto.sqlite")
            service = ReviewAutomationService(queue_store=store, ai_store=AIReviewStore(store.path))

            result = service.automate_rows([], selected_filters={"symbol": "AVGO"})
            log = store.latest_operation_log()

            self.assertEqual(result.eligible, 0)
            self.assertEqual(log["actionName"], "ai_automation")
            self.assertEqual(log["eligibleItemCount"], 0)
            self.assertEqual(log["selectedFilters"]["symbol"], "AVGO")

    def test_review_autopilot_button_path_runs_orchestrator(self) -> None:
        source = inspect.getsource(manual_review._render_sync_controls)

        self.assertIn("一键自动处理当前筛选结果", source)
        self.assertIn("run_review_autopilot", source)
        self.assertNotIn("radio(\"AI模式\"", source)

    def test_needs_data_requires_ir_scrape_triggers_disclosure_pipeline(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="subscriptionRevenueGrowth",
                item_type="missing_kpi",
                affects="Quality",
                value=None,
                source_type="metric_resolution",
                resolution_status="requires_ir_scrape",
            )
            store.update_review_status(int(item["id"]), "needs_data")
            pipeline = _FakeDisclosurePipeline()
            builder = _FakeQueueBuilder()
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=pipeline, builder=builder)

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            updated = store.get_item(int(item["id"]))

            self.assertEqual(pipeline.calls[0]["symbol"], "NOW")
            self.assertEqual(updated["autoFillType"], "ir_release")
            self.assertEqual(updated["autoFillStatus"], "success")
            self.assertGreaterEqual(result.autoFillSucceeded, 1)

    def test_missing_inputs_technical_field_gets_technical_autofill_type(self) -> None:
        row = {
            "metricKey": "EMA200",
            "resolutionStatus": "missing_inputs",
            "reviewStatus": "needs_data",
            "recommendedAction": "technical",
        }

        capability = auto_fill_capability(row)

        self.assertTrue(capability.canAutoFill)
        self.assertEqual(capability.autoFillType, "technical_indicator")

    def test_requires_analyst_estimates_is_not_auto_fillable_without_source(self) -> None:
        old = os.environ.pop("FMP_API_KEY", None)
        try:
            capability = auto_fill_capability({"resolutionStatus": "requires_analyst_estimates"})
        finally:
            if old is not None:
                os.environ["FMP_API_KEY"] = old

        self.assertFalse(capability.canAutoFill)
        self.assertEqual(capability.autoFillType, "not_auto_fillable")

    def test_company_not_disclosed_does_not_retry_autofill_forever(self) -> None:
        capability = auto_fill_capability({"resolutionStatus": "company_not_disclosed"})

        self.assertFalse(capability.canAutoFill)
        self.assertIn("不能无限重试", capability.reason)

    def test_review_autopilot_rebuilds_queue_after_autofill_and_logs_actions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="rpoGrowth",
                item_type="missing_kpi",
                affects="Quality",
                value=None,
                source_type="metric_resolution",
                resolution_status="requires_ir_scrape",
            )
            store.update_review_status(int(item["id"]), "needs_data")
            builder = _FakeQueueBuilder()
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_FakeDisclosurePipeline(), builder=builder)

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            logs = store.list_automation_logs(result.runId)

            self.assertGreaterEqual(len(builder.calls), 2)
            self.assertTrue(any(log["action"] == "auto_fill_success" for log in logs))
            self.assertTrue(any(log["action"] == "auto_archive" for log in logs))

    def test_autopilot_converts_successful_autofill_to_qwen_eligible_item(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="subscriptionRevenueGrowth",
                item_type="missing_kpi",
                affects="Quality",
                value=None,
                source_type="metric_resolution",
                resolution_status="requires_ir_scrape",
            )
            store.update_review_status(int(item["id"]), "needs_data")
            qwen_service = QwenReviewService(
                queue_store=store,
                ai_store=AIReviewStore(store.path),
                client=_FakeQwenClient(_qwen_review_result("recommend_approve", confidence=0.95)),
            )
            automation = ReviewAutomationService(
                queue_store=store,
                ai_store=AIReviewStore(store.path),
                qwen_service=qwen_service,
            )
            autopilot = ReviewAutopilot(
                queue_store=store,
                disclosure_pipeline=_FakeDisclosurePipelineWithExtracted(),
                builder=_FakeQueueBuilder(),
                automation_service=automation,
            )

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            extracted_rows = [row for row in store.list_items("NOW") if row["itemType"] == "extracted_value"]

            self.assertGreaterEqual(result.qwenEligibleCount, 1)
            self.assertGreaterEqual(result.qwenReviewedCount, 1)
            self.assertEqual(extracted_rows[0]["qwenEligible"], 1)
            self.assertEqual(extracted_rows[0]["reviewStatus"], "approved")

    def test_pipeline_skipped_for_this_model_is_unsupported_not_failed(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="adjustedEbitda",
                item_type="missing_kpi",
                affects="Quality",
                value=None,
                source_type="metric_resolution",
                resolution_status="requires_ir_scrape",
            )
            store.update_review_status(int(item["id"]), "needs_data")
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_SkippedDisclosurePipeline(), builder=_FakeQueueBuilder())

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            updated = store.get_item(int(item["id"]))

            self.assertEqual(result.failedCount, 0)
            self.assertEqual(result.unsupportedCount, 1)
            self.assertEqual(updated["autoFillStatus"], "not_available")
            self.assertIn("暂不支持", updated["autoFillError"])

    def test_power_generation_unsupported_adjusted_metrics_use_proxy_not_human_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="adjustedEbitda",
                item_type="missing_kpi",
                affects="Quality",
                value=None,
                source_type="metric_resolution",
                resolution_status="requires_ir_scrape",
                model_type="POWER_GENERATION",
            )
            store.update_review_status(int(item["id"]), "needs_data")
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_SkippedDisclosurePipeline(), builder=_FakeQueueBuilder())

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            updated = store.get_item(int(item["id"]))

            self.assertEqual(result.failedCount, 0)
            self.assertEqual(result.needsHumanCount, 0)
            self.assertEqual(updated["reviewStatus"], "auto_archived")
            self.assertEqual(updated["aiTriageStatus"], "ai_auto_archived")

    def test_autopilot_summary_counts_skipped_as_scanned_minus_processable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            pending = _insert_review_item(store, metric_key="rpoGrowth", item_type="missing_kpi", value=None, resolution_status="requires_ir_scrape")
            approved = _insert_review_item(store, metric_key="netRetentionRate", item_type="missing_kpi", value=None, resolution_status="requires_ir_scrape")
            store.update_review_status(int(pending["id"]), "needs_data")
            store.update_review_status(int(approved["id"]), "approved")
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_SkippedDisclosurePipeline(), builder=_FakeQueueBuilder())

            result = autopilot.run_review_autopilot({"symbol": "NOW"})

            self.assertEqual(result.skippedCount, result.scannedCount - result.processableCount)
            self.assertGreaterEqual(result.skippedCount, 1)

    def test_needs_human_count_excludes_auto_archived_and_auto_approved(self) -> None:
        rows = [
            {"reviewStatus": "auto_archived", "aiTriageStatus": "ai_auto_archived", "affects": "Quality"},
            {"reviewStatus": "approved", "aiTriageStatus": "auto_approved_by_ai", "affects": "Quality"},
            {"reviewStatus": "pending_review", "aiTriageStatus": "ai_recommend_reject", "affects": "Quality"},
        ]

        self.assertEqual(_human_remaining(rows), 1)

    def test_analyst_estimate_only_auto_archives_not_human_queue(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="peg",
                item_type="analyst_estimate_needed",
                affects="Entry",
                value=None,
                source_type="metric_resolution",
                resolution_status="requires_analyst_estimates",
            )
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_FakeDisclosurePipeline(), builder=_FakeQueueBuilder())

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            updated = store.get_item(int(item["id"]))

            self.assertEqual(updated["reviewStatus"], "auto_archived")
            self.assertEqual(result.needsHumanCount, 0)

    def test_qwen_eligible_failure_reason_is_recorded(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(store, extracted_text="")
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_FakeDisclosurePipeline(), builder=_FakeQueueBuilder())

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            updated = store.get_item(int(item["id"]))

            self.assertEqual(result.qwenEligibleCount, 0)
            self.assertEqual(updated["qwenEligible"], 0)
            self.assertIn(updated["qwenIneligibleReason"], {"missing_extracted_text", "missing_evidence_text"})

    def test_system_reason_does_not_become_evidence_text(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            store.upsert_item(
                {
                    "symbol": "ADBE",
                    "metricKey": "debtMaturityPressure",
                    "displayName": "债务到期压力",
                    "itemType": "missing_kpi",
                    "sourceType": "metric_resolution",
                    "sourceUrl": None,
                    "extractedText": "债务到期压力需要查看 10-K / 10-Q 债务到期表",
                    "systemReason": "债务到期压力需要查看 10-K / 10-Q 债务到期表",
                    "confidence": "low",
                    "affects": "Risk",
                    "reviewStatus": "needs_data",
                    "resolutionStatus": "requires_sec_filing",
                    "modelType": "SAAS_SOFTWARE",
                    "explanation": "债务到期压力需要查看 10-K / 10-Q 债务到期表",
                }
            )
            row = store.list_items("ADBE")[0]

            eligible, reason = qwen_review_eligibility(row)

            self.assertEqual(row["evidenceText"], "")
            self.assertEqual(row["extractedText"], "")
            self.assertIn("债务到期压力", row["systemReason"])
            self.assertFalse(eligible)
            self.assertEqual(reason, "unsupported_item_type")

    def test_adbe_crpo_ratio_extraction_is_rule_rejected(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review.sqlite"
            disclosure_store = DisclosureStore(db_path)
            queue_store = ReviewQueueStore(db_path, disclosure_store=disclosure_store)
            disclosure_store.save_metric(
                symbol="ADBE",
                metric_key="cRpoGrowth",
                value=0.67,
                unit="percent",
                period="2025 Q4",
                source_type="IR_RELEASE",
                source_url="https://example.com/adbe-release",
                source_document_title="ADBE earnings release",
                extracted_text="Current Remaining Performance Obligations were 67 percent of remaining performance obligations.",
                confidence="medium",
            )
            builder = ReviewQueueBuilder(
                queue_store=queue_store,
                disclosure_store=disclosure_store,
                fundamental_cache=FundamentalCache(db_path),
            )

            builder.build_review_queue_for_symbol("ADBE")
            rows = [
                row for row in queue_store.list_items("ADBE")
                if row["metricKey"] == "cRpoGrowth" and row["itemType"] == "extracted_value"
            ]

            self.assertEqual(rows[0]["reviewStatus"], "auto_archived")
            self.assertEqual(rows[0]["aiTriageStatus"], "extraction_rejected_by_rule")
            self.assertEqual(_human_remaining(rows), 0)

    def test_duplicate_review_items_are_archived(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            base = {
                "symbol": "NOW",
                "metricKey": "subscriptionRevenueGrowth",
                "displayName": "subscriptionRevenueGrowth",
                "itemType": "extracted_value",
                "value": 20,
                "unit": "percent",
                "period": "Q1 2026",
                "sourceType": "IR_RELEASE",
                "sourceUrl": "https://example.com/release",
                "sourceDocumentTitle": "Earnings release",
                "extractedText": "subscription revenue grew 20% in Q1 2026.",
                "evidenceText": "subscription revenue grew 20% in Q1 2026.",
                "confidence": "medium",
                "affects": "Quality",
                "reviewStatus": "pending_review",
                "resolutionStatus": "available",
                "sourceKind": "disclosure_metric_values",
                "modelType": "SAAS_SOFTWARE",
            }
            store.upsert_item({**base, "sourceMetricId": 1})
            store.upsert_item({**base, "sourceMetricId": 2})

            archived = store.archive_duplicate_items(["NOW"])
            statuses = [row["reviewStatus"] for row in store.list_items("NOW")]

            self.assertEqual(archived, 1)
            self.assertIn("duplicate_archived", statuses)
            duplicate_rows = [row for row in store.list_items("NOW") if row["reviewStatus"] == "duplicate_archived"]
            self.assertEqual(_human_remaining(duplicate_rows), 0)

    def test_net_cash_company_debt_maturity_pressure_is_low_materiality(self) -> None:
        self.assertTrue(
            _debt_maturity_low_materiality(
                {
                    "net_debt": -1_000,
                    "total_debt": 2_000,
                    "market_cap": 100_000,
                    "interest_coverage": 15,
                }
            )
        )

    def test_autopilot_ui_separates_current_run_from_cumulative_status(self) -> None:
        source = inspect.getsource(manual_review._render_last_autopilot_result) + inspect.getsource(manual_review._render_summary)

        self.assertIn("本次自动处理", source)
        self.assertIn("复核状态总览", source)
        self.assertIn("暂不支持", source)

    def test_autopilot_undo_restores_auto_archived_items(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "autopilot.sqlite")
            item = _insert_review_item(
                store,
                metric_key="forwardRevenueMultiple",
                item_type="analyst_estimate_needed",
                affects="Entry",
                value=None,
                source_type="",
                resolution_status="requires_analyst_estimates",
            )
            autopilot = ReviewAutopilot(queue_store=store, disclosure_pipeline=_FakeDisclosurePipeline(), builder=_FakeQueueBuilder())

            result = autopilot.run_review_autopilot({"symbol": "NOW"})
            archived = store.get_item(int(item["id"]))
            restored_count = store.undo_automation_run(result.runId)
            restored = store.get_item(int(item["id"]))

            self.assertEqual(archived["reviewStatus"], "auto_archived")
            self.assertEqual(restored_count, 1)
            self.assertEqual(restored["reviewStatus"], "pending_review")

    def test_review_queue_builder_covers_watchlist_model_gaps(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review.sqlite"
            disclosure_store = DisclosureStore(db_path)
            queue_store = ReviewQueueStore(db_path, disclosure_store=disclosure_store)
            fundamental_cache = FundamentalCache(db_path)
            for symbol, snapshot in _review_queue_snapshots().items():
                fundamental_cache.set_snapshot(symbol, snapshot)

            builder = ReviewQueueBuilder(
                queue_store=queue_store,
                disclosure_store=disclosure_store,
                fundamental_cache=fundamental_cache,
            )
            result = builder.build_review_queue_for_watchlist(["NOW", "MSFT", "VST", "COIN", "JPM"])
            rows = queue_store.list_items()
            symbols = {row["symbol"] for row in rows}

            self.assertGreater(result.total, 0)
            self.assertGreater(len(symbols), 1)
            self.assertIn("MSFT", symbols)
            self.assertIn("VST", symbols)
            self.assertIn("COIN", symbols)

            msft_items = [row for row in rows if row["symbol"] == "MSFT"]
            self.assertTrue(any(row["itemType"] == "derived_low_confidence" for row in msft_items))
            self.assertTrue(any(row["metricKey"] == "segmentStrength" for row in msft_items))

            vst_adjusted = [
                row for row in rows
                if row["symbol"] == "VST" and row["metricKey"] == "adjustedEbitda"
            ]
            self.assertEqual(vst_adjusted[0]["itemType"], "missing_kpi")
            self.assertEqual(vst_adjusted[0]["reviewStatus"], "needs_data")

            coin_risk = [
                row for row in rows
                if row["symbol"] == "COIN" and row["metricKey"] == "regulatoryRisk"
            ]
            self.assertEqual(coin_risk[0]["itemType"], "qualitative_risk")
            self.assertEqual(coin_risk[0]["reviewStatus"], "pending_review")

            metric_keys = {(row["symbol"], row["metricKey"]) for row in rows}
            self.assertNotIn(("NOW", "fcfMargin"), metric_keys)
            self.assertNotIn(("JPM", "evToFcf"), metric_keys)

    def test_review_queue_preserves_terminal_review_decisions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review.sqlite"
            disclosure_store = DisclosureStore(db_path)
            queue_store = ReviewQueueStore(db_path, disclosure_store=disclosure_store)
            fundamental_cache = FundamentalCache(db_path)
            fundamental_cache.set_snapshot("VST", _review_queue_snapshots()["VST"])
            builder = ReviewQueueBuilder(
                queue_store=queue_store,
                disclosure_store=disclosure_store,
                fundamental_cache=fundamental_cache,
            )

            builder.build_review_queue_for_symbol("VST")
            item = [
                row for row in queue_store.list_items("VST")
                if row["metricKey"] == "adjustedEbitda"
            ][0]
            queue_store.update_review_status(int(item["id"]), "rejected")
            builder.build_review_queue_for_symbol("VST")

            rows = [
                row for row in queue_store.list_items("VST")
                if row["metricKey"] == "adjustedEbitda"
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reviewStatus"], "rejected")

            approved_item = [
                row for row in queue_store.list_items("VST")
                if row["metricKey"] == "hedgeCoverage"
            ][0]
            queue_store.update_review_status(int(approved_item["id"]), "approved")
            builder.build_review_queue_for_symbol("VST")
            hedge_rows = [
                row for row in queue_store.list_items("VST")
                if row["metricKey"] == "hedgeCoverage"
            ]
            self.assertEqual(len(hedge_rows), 1)
            self.assertEqual(hedge_rows[0]["reviewStatus"], "approved")

    def test_review_queue_sync_does_not_trigger_sec_or_ir_fetch(self) -> None:
        source = inspect.getsource(ReviewQueueBuilder)

        self.assertNotIn("DisclosurePipeline", source)
        self.assertNotIn("SECClient", source)
        self.assertNotIn("get_market_data_provider", source)

    def test_ai_review_json_output_must_match_schema(self) -> None:
        valid = _ai_review_result("recommend_approve")
        self.assertEqual(validate_ai_review_result(valid)["aiDecision"], "recommend_approve")
        with self.assertRaises(ValueError):
            validate_ai_review_result({**valid, "extra": "not allowed"})
        self.assertIn("hallucinationRisk", validate_ai_review_result(valid))

    def test_ai_review_prompt_forbids_world_knowledge_and_fact_retrieval(self) -> None:
        from data.ai_review_assistant import SYSTEM_PROMPT

        self.assertIn("You must not use your own world knowledge", SYSTEM_PROMPT)
        self.assertIn("Only evaluate the provided evidence text", SYSTEM_PROMPT)
        self.assertIn("不允许补充外部事实", SYSTEM_PROMPT)

    def test_ai_review_mismatch_cannot_auto_confirm(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _ai_review_result("recommend_reject", evidence_match="mismatch", confidence=0.98)

            action = apply_ai_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "suggested_reject")
            self.assertEqual(updated["reviewStatus"], "pending_review")

    def test_ai_review_hallucinated_evidence_forces_human_review(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _ai_review_result("recommend_approve", confidence=0.99)
            result["evidenceQuote"] = "This quote is not in the supplied evidence text."

            guarded = enforce_evidence_only_result(item, result)
            action = apply_ai_review_result(item, guarded, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertTrue(guarded["hallucinationRisk"])
            self.assertEqual(guarded["aiDecision"], "needs_human_review")
            self.assertIn("hallucination_risk", guarded["warnings"])
            self.assertEqual(action, "needs_human_review")
            self.assertEqual(updated["reviewStatus"], "pending_review")

    def test_ai_review_declared_hallucination_risk_forces_human_review(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _ai_review_result("recommend_approve", confidence=0.99)
            result["hallucinationRisk"] = True

            guarded = enforce_evidence_only_result(item, result)

            self.assertEqual(guarded["aiDecision"], "needs_human_review")
            self.assertIn("hallucination_risk", guarded["warnings"])

    def test_qualitative_risk_cannot_be_auto_approved_by_ai(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            item = _insert_review_item(store, item_type="qualitative_risk", affects="Risk")
            result = _ai_review_result("recommend_approve", confidence=0.99)

            action = apply_ai_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "needs_human_review")
            self.assertEqual(updated["reviewStatus"], "pending_review")

    def test_exact_low_risk_extracted_value_can_be_auto_approved_by_ai(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            result = _ai_review_result("recommend_approve", confidence=0.95)

            action = apply_ai_review_result(item, result, store)
            updated = store.list_items(symbol="NOW")[0]

            self.assertEqual(action, "auto_approved_by_ai")
            self.assertEqual(updated["reviewStatus"], "approved")

    def test_ai_review_correction_is_candidate_not_overwrite(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ai.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality", value=20)
            client = _FakeAIClient(_ai_review_result("recommend_correct", corrected_value=22, confidence=0.92))
            assistant = AIReviewAssistant(queue_store=store, ai_store=ai_store, client=client)

            run = assistant.review_rows([item])
            updated = store.list_items(symbol="NOW")[0]
            latest = ai_store.latest_for_item(int(item["id"]))

            self.assertEqual(run.reviewed, 1)
            self.assertEqual(updated["value"], 20)
            self.assertEqual(updated["reviewStatus"], "pending_review")
            self.assertEqual(latest["appliedAction"], "manually_correct_candidate")
            self.assertEqual(latest["correctedValue"], 22)

    def test_ai_review_without_api_key_is_safe(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            item = _insert_review_item(store)
            assistant = AIReviewAssistant(queue_store=store, client=_UnconfiguredAIClient())

            result = assistant.review_rows([item])

            self.assertTrue(result.not_configured)
            self.assertEqual(result.reviewed, 0)

    def test_ai_review_does_not_repeat_same_input(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ai.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store, item_type="extracted_value", affects="Quality")
            client = _FakeAIClient(_ai_review_result("recommend_approve", confidence=0.95))
            assistant = AIReviewAssistant(queue_store=store, ai_store=ai_store, client=client)

            first = assistant.review_rows([item])
            second = assistant.review_rows([item])

            self.assertEqual(first.reviewed, 1)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(client.calls, 1)

    def test_ai_review_cost_guard_excludes_calculated_and_terminal_items(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "ai.sqlite")
            calculated = _insert_review_item(
                store,
                metric_key="fcfMargin",
                item_type="extracted_value",
                source_type="CALCULATED",
                resolution_status="calculated",
            )
            approved = _insert_review_item(store, metric_key="rpoGrowth", item_type="extracted_value")
            store.update_review_status(int(approved["id"]), "approved")
            pending = _insert_review_item(store, metric_key="subscriptionRevenueGrowth", item_type="extracted_value")

            candidates = ai_review_candidates(store.list_items())

            self.assertIn(int(pending["id"]), {int(row["id"]) for row in candidates})
            self.assertNotIn(int(calculated["id"]), {int(row["id"]) for row in candidates})
            self.assertNotIn(int(approved["id"]), {int(row["id"]) for row in candidates})

    def test_ai_review_batch_functions_create_local_batch_record(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ai.sqlite"
            store = ReviewQueueStore(db_path)
            ai_store = AIReviewStore(db_path)
            item = _insert_review_item(store)
            assistant = AIReviewAssistant(queue_store=store, ai_store=ai_store, client=_UnconfiguredAIClient())

            batch = assistant.create_ai_review_batch([int(item["id"])])
            status = assistant.check_ai_review_batch_status(int(batch["id"]))

            self.assertEqual(batch["status"], "queued")
            self.assertEqual(status["reviewItemIds"], [int(item["id"])])

    def test_qwen_review_client_uses_dashscope_json_schema_payload(self) -> None:
        client = QwenReviewClient(api_key="test-key", model="qwen-plus", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        body = client.response_body_for_batch({"symbol": "NOW", "metricKey": "subscriptionRevenueGrowth"})

        self.assertEqual(client.model, "qwen-plus")
        self.assertIn("/compatible-mode/v1", client.base_url)
        self.assertEqual(body["model"], "qwen-plus")
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_ai_review_provider_env_can_select_qwen(self) -> None:
        old_provider = os.environ.get("AI_REVIEW_PROVIDER")
        old_key = os.environ.get("DASHSCOPE_API_KEY")
        try:
            os.environ["AI_REVIEW_PROVIDER"] = "qwen"
            os.environ["DASHSCOPE_API_KEY"] = "test-key"
            client = create_review_client()
            self.assertIsInstance(client, QwenReviewClient)
            self.assertTrue(client.configured)
        finally:
            if old_provider is None:
                os.environ.pop("AI_REVIEW_PROVIDER", None)
            else:
                os.environ["AI_REVIEW_PROVIDER"] = old_provider
            if old_key is None:
                os.environ.pop("DASHSCOPE_API_KEY", None)
            else:
                os.environ["DASHSCOPE_API_KEY"] = old_key

    def test_disclosure_pipeline_calculates_metrics_and_marks_ir_misses_manual_override(self) -> None:
        class OfflineSECClient:
            user_agent = "test"
            max_requests_per_second = 5

            def cik_for_ticker(self, symbol: str, force_refresh: bool = False):
                return None

        class OfflineDisclosurePipeline(DisclosurePipeline):
            def _load_ir_pages(self, symbol, definitions, result, force_refresh):
                self._log(result, symbol, "IR_RELEASE", None, "skipped", "offline test")

            def _load_fmp_transcript(self, symbol, definitions, result):
                self._log(result, symbol, "FMP_TRANSCRIPT", None, "skipped", "offline test")

        with TemporaryDirectory() as tmpdir:
            store = DisclosureStore(Path(tmpdir) / "disclosure.sqlite")
            pipeline = OfflineDisclosurePipeline(store=store, sec_client=OfflineSECClient())

            result = pipeline.run(
                "NOW",
                model_type="SAAS_SOFTWARE",
                current_snapshot={
                    "ticker": "NOW",
                    "stock_based_compensation": 900,
                    "total_revenue": 10_000,
                    "total_debt": 4_000,
                    "total_cash": 1_500,
                    "ebitda": 2_500,
                    "ebit": 2_000,
                    "interest_expense": 250,
                    "free_cash_flow": 2_800,
                    "current_price": 90,
                    "fifty_two_week_high": 120,
                },
                current_technicals={"ema20": 88, "ema50": 85, "ema200": 80, "rsi14": 52},
            )

            saved_keys = {item["metricKey"] for item in result["saved"]}
            self.assertIn("sbcToRevenue", saved_keys)
            self.assertIn("netDebtToEbitda", saved_keys)
            resolutions = {item["metricKey"]: item for item in result["resolutions"]}
            self.assertEqual(resolutions["subscriptionRevenueGrowth"]["status"], "manual_override_required")

    def test_data_confidence_distinguishes_not_disclosed_ir_and_estimate_needs(self) -> None:
        enriched = enrich_data_confidence(
            {
                "ticker": "ADBE",
                "modelType": "SAAS_SOFTWARE",
                "revenue_growth": 0.10,
                "operating_margin": 0.30,
                "free_cash_flow": 1_000,
                "total_revenue": 5_000,
                "sbc_ratio": 0.08,
                "net_debt_to_ebitda": -1.0,
                "interest_coverage": 10.0,
                "manualNonGaapOperatingMargin": 0.35,
                "metric_statuses": {"peg_ratio": {"status": "requires_estimates"}},
            }
        )

        self.assertEqual(enriched["dataConfidence"], "medium")
        self.assertIn("large customer growth", enriched["notDisclosedMetrics"])
        self.assertIn("subscription revenue growth", enriched["requiresIrScrapeMetrics"])
        self.assertIn("PEG", enriched["requiresEstimatesMetrics"])

    def test_manual_override_persists_saas_supplement_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache = FundamentalCache(Path(tmpdir) / "fundamentals.sqlite")
            cache.set_manual_overrides(
                "NOW",
                manualSubscriptionRevenueGrowth=0.18,
                manualNonGaapOperatingMargin=0.32,
                manualNetRetention=1.22,
                manualRpoGrowth=0.16,
                manualLargeCustomerGrowth=0.24,
                manualSbcRatio=0.08,
            )

            overrides = cache.get_manual_overrides("NOW")

            self.assertEqual(overrides["manualSubscriptionRevenueGrowth"], 0.18)
            self.assertEqual(overrides["manualNonGaapOperatingMargin"], 0.32)
            self.assertEqual(overrides["manualNetRetention"], 1.22)
            self.assertEqual(overrides["manualRpoGrowth"], 0.16)
            self.assertEqual(overrides["manualLargeCustomerGrowth"], 0.24)
            self.assertEqual(overrides["manualSbcRatio"], 0.08)

    def test_saas_high_quality_deep_drawdown_does_not_become_low_risk(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "NOW",
                "sector": "Technology",
                "industry": "Software - Application",
                "revenue_growth": 0.22,
                "gross_margin": 0.76,
                "operating_margin": 0.20,
                "return_on_invested_capital": 0.10,
                "free_cash_flow": 3_200,
                "total_revenue": 10_000,
                "free_cash_flow_yield": 0.045,
                "price_to_sales": 7.4,
                "price_to_fcf": 22,
                "forward_pe": 32,
                "sbc_ratio": 0.11,
                "manualNetRetention": 1.12,
                "manualRpoGrowth": 0.14,
                "total_debt": 100,
                "total_cash": 300,
                "current_ratio": 1.4,
            },
            {
                "price": 90,
                "ema20": 96,
                "ema50": 102,
                "ema200": 112,
                "rsi14": 50,
                "drawdown_from_high_pct": -42,
                "gain_20d_pct": -3,
                "fifty_two_week_low": 72,
            },
        )

        self.assertEqual(result.scoring_model, "SAAS_SOFTWARE")
        self.assertFalse(result.data_insufficient)
        self.assertGreaterEqual(result.quality_score, 75)
        self.assertGreaterEqual(result.entry_score, 65)
        self.assertGreaterEqual(result.risk_score, 26)
        self.assertNotEqual(result.risk_rating, "低")

    def test_specialized_models_use_public_metric_proxies_instead_of_manual_required_data(self) -> None:
        cases = [
            (
                "VST",
                {
                    "ticker": "VST",
                    "sector": "Utilities",
                    "industry": "Independent Power Producers",
                    "market_cap": 50_000_000_000,
                    "enterprise_value": 70_000_000_000,
                    "ebitda": 6_500_000_000,
                    "free_cash_flow": 3_500_000_000,
                    "net_debt_to_ebitda": 3.4,
                    "free_cash_flow_growth": 0.03,
                    "current_ratio": 1.0,
                },
            ),
            (
                "CEG",
                {
                    "ticker": "CEG",
                    "sector": "Utilities",
                    "industry": "Power Generation",
                    "market_cap": 100_000_000_000,
                    "enterprise_value": 115_000_000_000,
                    "ebitda": 8_500_000_000,
                    "free_cash_flow": 4_500_000_000,
                    "net_debt_to_ebitda": 2.2,
                    "free_cash_flow_growth": 0.02,
                    "current_ratio": 1.1,
                },
            ),
            (
                "COIN",
                {
                    "ticker": "COIN",
                    "sector": "Financial Services",
                    "industry": "Capital Markets",
                    "revenue_growth": 0.18,
                    "operating_margin": 0.22,
                    "free_cash_flow": 2_000_000_000,
                    "total_revenue": 6_000_000_000,
                    "total_cash": 7_000_000_000,
                    "total_debt": 3_000_000_000,
                    "price_to_sales": 8,
                },
            ),
            (
                "HOOD",
                {
                    "ticker": "HOOD",
                    "sector": "Financial Services",
                    "industry": "Brokerage",
                    "revenue_growth": 0.22,
                    "operating_margin": 0.16,
                    "free_cash_flow": 1_200_000_000,
                    "total_revenue": 3_500_000_000,
                    "total_cash": 4_000_000_000,
                    "total_debt": 1_000_000_000,
                    "price_to_sales": 10,
                },
            ),
            (
                "NVO",
                {
                    "ticker": "NVO",
                    "sector": "Healthcare",
                    "industry": "Drug Manufacturers",
                    "revenue_growth": 0.16,
                    "operating_margin": 0.42,
                    "free_cash_flow": 9_000_000_000,
                    "total_revenue": 38_000_000_000,
                    "total_cash": 5_000_000_000,
                    "total_debt": 10_000_000_000,
                    "forward_pe": 22,
                },
            ),
        ]
        technicals = {
            "price": 100,
            "rsi14": 48,
            "drawdown_from_high_pct": -30,
            "gain_20d_pct": -4,
            "ema20": 102,
            "ema50": 105,
            "ema200": 95,
            "fifty_two_week_low": 70,
        }

        for symbol, snapshot in cases:
            with self.subTest(symbol=symbol):
                result = calculate_total_score(snapshot, technicals)

                self.assertFalse(result.data_insufficient)
                self.assertNotEqual(result.quality_rating, "数据不足")
                self.assertNotEqual(result.action, "数据不足，需复核")

    def test_industry_model_missing_key_data_is_not_scored_as_d(self) -> None:
        result = calculate_total_score(
            {"ticker": "PLD", "sector": "Real Estate", "industry": "REIT"},
            {"price": 100, "rsi14": 50, "drawdown_from_high_pct": -10},
        )
        self.assertEqual(result.scoring_model, "REIT_REAL_ESTATE")
        self.assertEqual(result.action, "数据不足，需复核")
        self.assertEqual(result.quality_rating, "数据不足")

    def test_high_risk_action_does_not_conflict_with_buy_zone(self) -> None:
        result = calculate_total_score(
            {
                "ticker": "CRWV",
                "sector": "Technology",
                "industry": "AI Infrastructure",
                "revenue_growth": 0.80,
                "gross_margin": 0.20,
                "free_cash_flow": -2_000_000_000,
                "total_revenue": 1_000_000_000,
                "total_debt": 6_000_000_000,
                "total_cash": 500_000_000,
                "enterprise_to_revenue": 30,
            },
            {"price": 120, "rsi14": 75, "drawdown_from_high_pct": -2, "gain_20d_pct": 30, "pct_above_ema200": 45},
        )
        self.assertNotEqual(result.action, "可正常分批")
        self.assertNotIn("Buy Zone", result.valuation_status)
        self.assertNotIn("Buy Zone", result.action)

    def test_valuation_score_normalizes_to_100_point_scale(self) -> None:
        self.assertEqual(normalize_valuation_score(10), 40)
        self.assertEqual(normalize_valuation_score(25), 100)

    def test_anti_fomo_signal_triggers_on_overbought_momentum(self) -> None:
        signals = build_trading_signals(
            {
                "price": 130,
                "ema50": 120,
                "ema200": 100,
                "rsi14": 72,
                "drawdown_from_high_pct": -2,
                "pct_above_ema200": 30,
                "gain_20d_pct": 22,
            },
            valuation_score=8,
            technical_score=8,
            risk_flags=[],
        )
        anti_fomo = [signal for signal in signals if signal.kind == "anti_fomo"]
        self.assertEqual(anti_fomo[0].message, ANTI_FOMO_MESSAGE)
        self.assertGreaterEqual(len(anti_fomo[0].reasons), 4)

    def test_overheat_score_does_not_clear_just_because_today_is_down(self) -> None:
        result = calculate_overheat_score(
            {
                "price_to_sales": 28,
                "forward_pe": 58,
                "free_cash_flow_yield": 0.012,
            },
            {
                "rsi14": 66,
                "drawdown_from_high_pct": -4,
                "gain_20d_pct": 18,
                "gain_60d_pct": 42,
                "daily_return_pct": -3,
                "pct_above_ema20": 7,
                "pct_above_ema50": 14,
            },
            valuation_status="偏贵",
            model_type="SEMICONDUCTOR",
            quality_rating="A - 高质量",
        )

        self.assertGreaterEqual(result.score, 60)
        self.assertEqual(result.status, "偏热")
        self.assertEqual(result.action, "只观察")
        self.assertTrue(any("今日下跌" in reason for reason in result.reasons))

    def test_overheat_score_downgrades_when_setup_cools(self) -> None:
        result = calculate_overheat_score(
            {"price_to_sales": 12, "forward_pe": 32, "free_cash_flow_yield": 0.035},
            {
                "rsi14": 55,
                "drawdown_from_high_pct": -16,
                "gain_20d_pct": 3,
                "gain_60d_pct": 12,
                "daily_return_pct": -2,
                "pct_above_ema20": -1,
                "pct_above_ema50": 4,
            },
            valuation_status="合理偏便宜",
            model_type="SEMICONDUCTOR",
            quality_rating="A - 高质量",
        )

        self.assertLess(result.score, 40)
        self.assertIn(result.status, {"回调较充分", "非过热"})

    def test_left_side_opportunity_requires_all_conditions_and_no_major_flags(self) -> None:
        signals = build_trading_signals(
            {
                "price": 98,
                "ema200": 100,
                "rsi14": 35,
                "drawdown_from_high_pct": -30,
            },
            valuation_score=18,
            technical_score=2,
            risk_flags=[],
        )
        opportunity = [signal for signal in signals if signal.kind == "left_side_opportunity"]
        self.assertEqual(opportunity[0].message, LEFT_SIDE_OPPORTUNITY_MESSAGE)

        blocked = build_trading_signals(
            {
                "price": 98,
                "ema200": 100,
                "rsi14": 35,
                "drawdown_from_high_pct": -30,
            },
            valuation_score=18,
            technical_score=2,
            risk_flags=[RiskFlag("High debt", "high", "Debt is elevated.")],
        )
        self.assertFalse([signal for signal in blocked if signal.kind == "left_side_opportunity"])


class ReviewQueueEvidenceGateTests(unittest.TestCase):
    def test_extracted_value_without_evidence_moves_to_needs_evidence(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            store.upsert_item(
                {
                    "symbol": "ADBE",
                    "metricKey": "rpoGrowth",
                    "displayName": "RPO增速",
                    "itemType": "extracted_value",
                    "value": 13,
                    "unit": "percent",
                    "period": "Q4 2025",
                    "sourceType": "SEC_8K",
                    "sourceUrl": None,
                    "sourceDocumentTitle": None,
                    "extractedText": "",
                    "evidenceText": "",
                    "confidence": "medium",
                    "affects": "Quality",
                    "reviewStatus": "pending_review",
                    "resolutionStatus": "available",
                }
            )
            row = store.list_items(symbol="ADBE")[0]

            self.assertEqual(row["itemType"], "evidence_missing_extracted_value")
            self.assertEqual(row["reviewStatus"], "needs_evidence")
            eligible, _reason = qwen_review_eligibility(row)
            self.assertFalse(eligible)

    def test_needs_evidence_backfill_success_returns_to_pending_review(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "release.txt"
            source.write_text("In Q4 2025, cRPO grew 25% year-over-year.", encoding="utf-8")
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            store.upsert_item(
                {
                    "symbol": "CRM",
                    "metricKey": "cRpoGrowth",
                    "displayName": "cRPO增速",
                    "itemType": "extracted_value",
                    "value": 25,
                    "unit": "percent",
                    "period": "Q4 2025",
                    "sourceType": "IR_RELEASE",
                    "sourceUrl": source.as_uri(),
                    "sourceDocumentTitle": "CRM Earnings Release",
                    "extractedText": "",
                    "evidenceText": "",
                    "confidence": "medium",
                    "affects": "Quality",
                    "reviewStatus": "pending_review",
                    "resolutionStatus": "available",
                }
            )
            dirty = store.list_items(symbol="CRM")[0]
            self.assertEqual(dirty["reviewStatus"], "needs_evidence")

            outcome = backfill_evidence_for_review_item(int(dirty["id"]), store)
            row = store.get_item(int(dirty["id"]))

            self.assertEqual(outcome["status"], "backfilled")
            self.assertEqual(row["itemType"], "extracted_value")
            self.assertEqual(row["reviewStatus"], "pending_review")
            self.assertTrue(row["evidenceHash"])

    def test_cleanup_moves_system_reason_out_of_evidence_text(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            store.upsert_item(
                {
                    "symbol": "ADBE",
                    "metricKey": "debtMaturityPressure",
                    "displayName": "债务到期压力",
                    "itemType": "extracted_value",
                    "value": 1,
                    "unit": "count",
                    "period": "Q4 2025",
                    "sourceType": "SEC_10K",
                    "sourceUrl": "https://example.com/10k",
                    "sourceDocumentTitle": "10-K",
                    "extractedText": "债务到期压力需要查看10-K/10-Q债务到期表",
                    "evidenceText": "债务到期压力需要查看10-K/10-Q债务到期表",
                    "confidence": "low",
                    "affects": "ConfidenceOnly",
                    "reviewStatus": "pending_review",
                    "resolutionStatus": "available",
                }
            )
            store.cleanup_stale_review_items(["ADBE"])
            row = store.list_items(symbol="ADBE")[0]

            self.assertEqual(row["evidenceText"], "")
            self.assertIn(row["reviewStatus"], {"needs_evidence", "auto_archived"})
            self.assertTrue(row["systemReason"])

    def test_manual_review_page_has_needs_evidence_tab_and_disables_confirm(self) -> None:
        source = Path("ui/manual_review.py").read_text(encoding="utf-8")

        self.assertIn("需要补证据", source)
        self.assertIn('"needs_evidence"', source)
        self.assertIn('"重新抓证据"', source)
        self.assertIn('"evidenceBackfilled"', source)
        self.assertIn('summary.get("needs_evidence"', source)
        self.assertIn('"evidence_missing_extracted_value"', source)


class BuyZoneTests(unittest.TestCase):
    def test_eps_multiple_buy_zone_price_ladder(self) -> None:
        ladder = calculate_buy_zone_ladder(
            BuyZoneInputs(
                current_price=180,
                target_position_size=10_000,
                valuation_method="EPS multiple",
                forward_eps=8,
                target_pe=25,
            )
        )
        self.assertEqual(ladder["fair_value_price"], 200)
        self.assertEqual(ladder["starter_position_price"], 190)
        self.assertEqual(ladder["normal_buy_zone_price"], 170)
        self.assertEqual(ladder["heavy_buy_zone_price"], 150)
        self.assertEqual(ladder["panic_buy_zone_price"], 130)
        self.assertGreater(ladder["weighted_average_cost"], 0)
        self.assertGreater(ladder["total_shares"], 0)

    def test_custom_margin_of_safety_lowers_each_buy_price(self) -> None:
        ladder = calculate_buy_zone_ladder(
            BuyZoneInputs(
                current_price=180,
                target_position_size=10_000,
                valuation_method="EPS multiple",
                margin_of_safety_pct=10,
                forward_eps=8,
                target_pe=25,
            )
        )
        self.assertEqual(ladder["fair_value_price"], 200)
        self.assertEqual(ladder["margin_adjusted_fair_value"], 180)
        self.assertEqual(ladder["starter_position_price"], 171)
        self.assertEqual(ladder["normal_buy_zone_price"], 153)

    def test_fcf_multiple_fair_value(self) -> None:
        fair_value = calculate_fair_value_per_share(
            BuyZoneInputs(
                current_price=90,
                target_position_size=5_000,
                valuation_method="FCF multiple",
                forward_fcf=10_000_000_000,
                target_fcf_multiple=20,
                shares_outstanding=1_000_000_000,
            )
        )
        self.assertEqual(fair_value, 200)

    def test_revenue_multiple_fair_value(self) -> None:
        fair_value = calculate_fair_value_per_share(
            BuyZoneInputs(
                current_price=90,
                target_position_size=5_000,
                valuation_method="Revenue multiple",
                forward_revenue=20_000_000_000,
                target_ev_sales=8,
                net_debt=10_000_000_000,
                shares_outstanding=1_000_000_000,
            )
        )
        self.assertEqual(fair_value, 150)


class BuyZonePlanPageTests(unittest.TestCase):
    def _buy_zone_score(self, **overrides) -> SimpleNamespace:
        values = {
            "action": sorted(BUY_ACTIONS)[0],
            "quality_rating": "A",
            "entry_rating": "A",
            "risk_rating": "low",
            "valuation_status": "",
            "data_confidence": "high",
            "scoring_model": "GENERIC",
            "current_add_limit_percent": 5,
            "max_portfolio_weight_percent": 20,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _buy_zone_estimate(
        self,
        symbol: str = "TST",
        current_zone: str = "tranche_buy",
        current_price: float = 95,
        next_trigger_price: float | None = None,
    ) -> BuyZoneEstimate:
        return BuyZoneEstimate(
            symbol=symbol,
            modelType="GENERIC",
            currentPrice=current_price,
            noChaseAbove=130,
            fairValueLow=105,
            fairValueHigh=120,
            trancheBuyLow=90,
            trancheBuyHigh=100,
            heavyBuyBelow=70,
            currentZone=current_zone,
            confidence="high",
            method="blended",
            inputsUsed=[],
            keyReasons=[],
            warnings=[],
            createdAt="now",
            nextTriggerPrice=next_trigger_price,
            isValid=True,
            validationErrors=[],
        )

    def test_buy_zone_page_is_system_plan_center_not_default_eps_calculator(self) -> None:
        from ui import buy_zone as buy_zone_page

        source = inspect.getsource(buy_zone_page.render)
        self.assertIn("买区计划", source)
        self.assertIn("系统根据评分、估值、风险和技术位置自动生成买区", source)
        self.assertIn("_load_buy_zone_rows", source)
        self.assertNotIn("买区计算器", source)

    def test_buy_zone_page_uses_engines_and_drawer(self) -> None:
        from ui import buy_zone as buy_zone_page

        source = inspect.getsource(buy_zone_page)
        self.assertIn("generate_buy_zone", source)
        self.assertIn("generate_position_plan", source)
        self.assertIn("data-buy-zone-drawer-open", source)
        self.assertIn("BuyZoneDrawer", source)
        self.assertIn("高级估值沙盒", source)

    def test_zero_price_is_not_used_for_valid_buy_zone(self) -> None:
        zone = generate_buy_zone("ZERO", {"current_price": 0, "price_to_fcf": 20, "free_cash_flow_yield": 0.05}, None, "SAAS_SOFTWARE")
        self.assertEqual(zone.currentZone, "data_insufficient")
        self.assertIn("缺少当前价格", " ".join(zone.keyReasons))

    def test_buy_zone_page_displays_missing_price_instead_of_zero(self) -> None:
        from ui import buy_zone as buy_zone_page

        self.assertEqual(buy_zone_page._money(0), "价格缺失")
        self.assertEqual(buy_zone_page._money(None), "价格缺失")

    def test_buy_zone_page_prefers_final_action_for_display(self) -> None:
        from ui import buy_zone as buy_zone_page

        legacy_buy_action = sorted(BUY_ACTIONS)[0]
        final_non_buy_action = sorted(NON_BUY_VALUATION_STATUSES)[0]
        row = {
            "action": legacy_buy_action,
            "finalAction": final_non_buy_action,
            "currentZone": "tranche_buy",
            "currentPrice": 95,
            "currentAddLimitPercent": 0,
            "confidence": "high",
            "dataConfidence": "high",
            "isValid": True,
        }
        fallback_row = dict(row)
        fallback_row.pop("finalAction")
        fallback_row.pop("currentAddLimitPercent")

        self.assertEqual(buy_zone_page._row_action(row), final_non_buy_action)
        self.assertEqual(buy_zone_page._row_action(fallback_row), legacy_buy_action)
        self.assertNotEqual(buy_zone_page._action_short_text(row), buy_zone_page._action_short_text(fallback_row))

    def test_buy_zone_page_shows_zero_when_final_decision_blocks_add(self) -> None:
        from ui import buy_zone as buy_zone_page

        score = self._buy_zone_score(valuation_status=sorted(NON_BUY_VALUATION_STATUSES)[0])
        zone = self._buy_zone_estimate()
        plan = generate_position_plan("ZERO", zone, score)

        row = buy_zone_page._row_from_outputs("ZERO", {}, {}, score, zone, plan, "system_generated", False)

        self.assertEqual(row["action"], score.action)
        self.assertNotEqual(row["finalAction"], row["action"])
        self.assertFalse(row["isActionable"])
        self.assertEqual(row["currentAddLimitPercent"], 0)
        self.assertEqual(buy_zone_page._current_add_text(row)[0], "0%")

    def test_buy_zone_manual_override_rederives_final_decision(self) -> None:
        from ui import buy_zone as buy_zone_page

        score = self._buy_zone_score()
        zone = self._buy_zone_estimate(symbol="MAN")
        plan = generate_position_plan("MAN", zone, score)
        row = buy_zone_page._row_from_outputs("MAN", {}, {}, score, zone, plan, "system_generated", False)
        manual_plan = {
            "no_chase_above": 90,
            "fair_value_low": 70,
            "fair_value_high": 80,
            "tranche_buy_low": 55,
            "tranche_buy_high": 65,
            "heavy_buy_below": 45,
        }

        updated = buy_zone_page._apply_manual_plan(row, manual_plan)

        self.assertTrue(row["isActionable"])
        self.assertEqual(updated["currentZone"], "no_chase")
        self.assertEqual(updated["decisionLane"], "blocked")
        self.assertFalse(updated["isActionable"])
        self.assertEqual(updated["currentAddLimitPercent"], 0)
        self.assertNotEqual(updated["finalAction"], row["finalAction"])

    def test_buy_zone_near_trigger_logic_survives_final_decision_wait_lane(self) -> None:
        from ui import buy_zone as buy_zone_page

        result = buy_zone_page.resolve_buy_zone_display_category(
            {
                "currentZone": "fair_observation",
                "currentPrice": 100,
                "nextTriggerPrice": 90,
                "finalAction": "wait",
                "decisionLane": "wait",
                "isActionable": False,
                "currentAddLimitPercent": 0,
                "confidence": "high",
                "dataConfidence": "high",
                "isValid": True,
            }
        )

        self.assertTrue(result["priorityEligible"])
        self.assertEqual(result["triggerTone"], "near")
        self.assertIn("10.0", str(result["triggerPrimary"]))


def _ai_review_result(
    decision: str,
    evidence_match: str = "exact_match",
    period_match: str = "exact",
    unit_match: str = "exact",
    risk_level: str = "low",
    confidence: float = 0.9,
    corrected_value: float | None = None,
) -> dict:
    return {
        "aiDecision": decision,
        "correctedValue": corrected_value,
        "correctedUnit": "percent" if corrected_value is not None else None,
        "correctedPeriod": "Q1 2026" if corrected_value is not None else None,
        "confidenceScore": confidence,
        "evidenceMatch": evidence_match,
        "periodMatch": period_match,
        "unitMatch": unit_match,
        "riskLevel": risk_level,
        "hallucinationRisk": False,
        "explanationZh": "原文证据足以支持判断。",
        "evidenceQuote": "subscription revenue grew 20%",
        "warnings": [],
    }


def _insert_review_item(
    store: ReviewQueueStore,
    metric_key: str = "subscriptionRevenueGrowth",
    item_type: str = "extracted_value",
    affects: str = "Quality",
    value: float = 20,
    source_type: str = "IR_RELEASE",
    resolution_status: str = "available",
    extracted_text: str = "subscription revenue grew 20% in Q1 2026.",
    model_type: str = "SAAS_SOFTWARE",
) -> dict:
    store.upsert_item(
        {
            "symbol": "NOW",
            "metricKey": metric_key,
            "displayName": metric_key,
            "itemType": item_type,
            "value": value,
            "unit": "percent",
            "period": "Q1 2026",
            "sourceType": source_type,
            "sourceUrl": "https://example.com/source" if source_type in {"IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION", "FMP_TRANSCRIPT"} else None,
            "sourceDocumentTitle": "Earnings Release",
            "extractedText": extracted_text,
            "evidenceText": extracted_text,
            "confidence": "medium",
            "affects": affects,
            "reviewStatus": "pending_review",
            "recommendedAction": "AI预审",
            "resolutionStatus": resolution_status,
            "sourceKind": "metric_resolution",
            "modelType": model_type,
            "explanation": "test row",
        }
    )
    return store.list_items(symbol="NOW", metric_key=metric_key)[0]


class _FakeDisclosurePipeline:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, symbol: str, **kwargs) -> dict:
        self.calls.append({"symbol": symbol, **kwargs})
        return {
            "symbol": symbol,
            "modelType": kwargs.get("model_type"),
            "saved": [{"metricKey": "subscriptionRevenueGrowth", "value": 0.2}],
            "logs": [{"status": "available", "sourceType": "IR_RELEASE"}],
            "missing": [],
            "notDisclosed": [],
            "resolutions": [],
        }


class _FakeDisclosurePipelineWithExtracted(_FakeDisclosurePipeline):
    def run(self, symbol: str, **kwargs) -> dict:
        self.calls.append({"symbol": symbol, **kwargs})
        return {
            "symbol": symbol,
            "modelType": kwargs.get("model_type"),
            "saved": [
                {
                    "metricKey": "subscriptionRevenueGrowth",
                    "value": 20,
                    "unit": "percent",
                    "period": "Q1 2026",
                    "sourceType": "IR_RELEASE",
                    "sourceUrl": "https://example.com/ir",
                    "sourceDocumentTitle": "Earnings Release",
                    "extractedText": "subscription revenue grew 20% in Q1 2026.",
                    "confidence": "medium",
                }
            ],
            "logs": [{"status": "available", "sourceType": "IR_RELEASE"}],
            "missing": [],
            "notDisclosed": [],
            "resolutions": [],
        }


class _SkippedDisclosurePipeline(_FakeDisclosurePipeline):
    def run(self, symbol: str, **kwargs) -> dict:
        self.calls.append({"symbol": symbol, **kwargs})
        return {
            "symbol": symbol,
            "modelType": kwargs.get("model_type"),
            "saved": [],
            "logs": [{"status": "skipped", "sourceType": "PIPELINE", "errorMessage": "pipeline skipped for this model"}],
            "missing": [],
            "notDisclosed": [],
            "resolutions": [],
        }


class _FakeQueueBuilder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def build_review_queue_for_watchlist(self, symbols) -> object:
        normalized = [str(symbol).upper() for symbol in symbols]
        self.calls.append(normalized)
        return type(
            "FakeQueueBuildResult",
            (),
            {
                "symbols": normalized,
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "total": 0,
                "item_type_counts": {},
            },
        )()


class _FakeAIClient:
    configured = True
    model = "test-model"
    batch_model = "test-batch-model"

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    def review_item(self, payload: dict) -> dict:
        self.calls += 1
        return self.result


class _UnconfiguredAIClient:
    configured = False
    model = "test-model"
    batch_model = "test-batch-model"

    def review_item(self, payload: dict) -> dict:
        raise AssertionError("OpenAI client should not be called when unconfigured")


def _qwen_review_result(
    decision: str,
    evidence_match: str = "exact_match",
    period_match: str = "exact",
    unit_match: str = "exact",
    risk_level: str = "low",
    confidence: float = 0.9,
    corrected_value: float | None = None,
) -> dict:
    result = {
        "aiDecision": decision,
        "correctedValue": corrected_value,
        "correctedUnit": "percent" if corrected_value is not None else None,
        "correctedPeriod": "Q1 2026" if corrected_value is not None else None,
        "confidenceScore": confidence,
        "evidenceMatch": evidence_match,
        "periodMatch": period_match,
        "unitMatch": unit_match,
        "riskLevel": risk_level,
        "explanationZh": "原文证据足以支持判断。",
        "evidenceQuote": "subscription revenue grew 20% in Q1 2026",
        "warnings": [],
    }
    return validate_qwen_review_result(result)


class _FakeQwenClient:
    configured = True
    model = "qwen-flash"

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    def chat_completion(self, messages, response_format=None, model=None):
        self.calls += 1
        return {"choices": [{"message": {"content": json.dumps(self.result, ensure_ascii=False)}}]}


class ManualReviewActionSemanticsTests(unittest.TestCase):
    def test_needs_evidence_does_not_show_confirm_action(self) -> None:
        from ui.manual_review import _review_primary_action

        action = _review_primary_action(
            {
                "itemType": "evidence_missing_extracted_value",
                "reviewStatus": "needs_evidence",
                "value": 13,
                "unit": "percent",
            }
        )
        self.assertEqual(action["key"], "backfill_evidence")
        self.assertEqual(action["label"], "重新抓证据")

    def test_missing_kpi_uses_auto_fill_or_unavailable_action(self) -> None:
        from ui.manual_review import _review_primary_action

        auto_fill_action = _review_primary_action(
            {
                "itemType": "missing_kpi",
                "reviewStatus": "needs_data",
                "canAutoFill": True,
                "resolutionStatus": "requires_ir_scrape",
            }
        )
        unavailable_action = _review_primary_action(
            {
                "itemType": "missing_kpi",
                "reviewStatus": "needs_data",
                "canAutoFill": False,
                "resolutionStatus": "company_not_disclosed",
            }
        )
        self.assertEqual(auto_fill_action["label"], "自动补齐")
        self.assertEqual(unavailable_action["label"], "标记无法获取")

    def test_qualitative_risk_uses_reviewed_not_confirm(self) -> None:
        from ui.manual_review import _review_primary_action

        action = _review_primary_action({"itemType": "qualitative_risk", "reviewStatus": "pending_review"})
        self.assertEqual(action["key"], "mark_reviewed")
        self.assertEqual(action["label"], "标记已复核")

    def test_extracted_value_requires_complete_evidence_to_confirm(self) -> None:
        from ui.manual_review import _has_complete_extracted_value_evidence, _review_primary_action

        complete = {
            "itemType": "extracted_value",
            "reviewStatus": "pending_review",
            "value": 20,
            "normalizedValue": 20,
            "unit": "percent",
            "sourceType": "IR_RELEASE",
            "sourceUrl": "https://example.com/release",
            "sourceDocumentTitle": "Earnings Release",
            "evidenceText": "subscription revenue grew 20% in Q1 2026.",
            "evidenceHash": "abc123",
            "metricPeriod": "Q1 2026",
        }
        incomplete = {**complete, "evidenceText": "", "evidenceHash": ""}
        self.assertTrue(_has_complete_extracted_value_evidence(complete))
        self.assertEqual(_review_primary_action(complete)["label"], "确认数据")
        self.assertFalse(_has_complete_extracted_value_evidence(incomplete))
        self.assertEqual(_review_primary_action(incomplete)["label"], "重新抓证据")

    def test_auto_archived_items_are_restore_only_when_visible(self) -> None:
        from ui.manual_review import _review_primary_action

        action = _review_primary_action({"itemType": "missing_kpi", "reviewStatus": "auto_archived"})
        self.assertEqual(action["label"], "已归档")
        self.assertEqual(action["key"], "noop_archived")

    def test_review_status_scoring_eligibility_is_strict(self) -> None:
        from data.disclosure_store import _eligible_for_scoring

        self.assertTrue(_eligible_for_scoring({"reviewStatus": "approved"}))
        self.assertTrue(_eligible_for_scoring({"reviewStatus": "manually_corrected"}))
        self.assertFalse(_eligible_for_scoring({"reviewStatus": "auto_archived"}))
        self.assertFalse(_eligible_for_scoring({"reviewStatus": "pending_review"}))
        self.assertFalse(_eligible_for_scoring({"reviewStatus": "rejected"}))


class ReviewUndoVersioningTests(unittest.TestCase):
    def test_approved_value_can_be_undone_and_marks_score_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ReviewQueueStore(Path(tmp) / "cache.db")
            item = _insert_review_item(store)
            item_id = int(item["id"])

            store.update_review_status(item_id, "approved")
            approved = store.get_item(item_id)
            self.assertEqual(approved["reviewStatus"], "approved")
            self.assertTrue(canMetricEnterScoring(approved))
            active_versions = [row for row in store.list_metric_versions(item_id) if row["isActive"]]
            self.assertEqual(len(active_versions), 1)
            self.assertEqual(active_versions[0]["reviewStatus"], "approved")
            self.assertEqual(store.get_score_status("NOW")["scoreStatus"], "stale")

            store.undo_review_status(item_id, "pending_review", "mistaken confirm")
            undone = store.get_item(item_id)
            self.assertEqual(undone["reviewStatus"], "pending_review")
            self.assertFalse(canMetricEnterScoring(undone))
            self.assertFalse([row for row in store.list_metric_versions(item_id) if row["isActive"]])
            self.assertEqual(store.get_score_status("NOW")["scoreStatus"], "stale")
            self.assertIn("undo_approve", store.get_score_status("NOW")["staleReason"])
            actions = [row["action"] for row in store.list_review_audit_logs(item_id)]
            self.assertIn("approve", actions)
            self.assertIn("undo_approve", actions)

    def test_auto_approved_value_can_be_undone_and_blocked_from_scoring(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ReviewQueueStore(Path(tmp) / "cache.db")
            item = _insert_review_item(store)
            item_id = int(item["id"])

            store.apply_ai_auto_approval(item_id, explanation_zh="validated evidence")
            approved = store.get_item(item_id)
            self.assertEqual(approved["reviewStatus"], "approved")
            self.assertEqual(approved["aiTriageStatus"], "auto_approved_by_ai")
            self.assertTrue(canMetricEnterScoring(approved))

            store.undo_review_status(item_id, "pending_review", "bad auto approval")
            undone = store.get_item(item_id)
            self.assertEqual(undone["reviewStatus"], "pending_review")
            self.assertIsNone(undone["aiTriageStatus"])
            self.assertFalse(canMetricEnterScoring(undone))
            self.assertEqual(store.get_score_status("NOW")["scoreStatus"], "stale")
            self.assertIn("undo_auto_approve", store.get_score_status("NOW")["staleReason"])
            actions = [row["action"] for row in store.list_review_audit_logs(item_id)]
            self.assertIn("auto_approve", actions)
            self.assertIn("undo_auto_approve", actions)

    def test_manually_corrected_version_supersedes_approved_version(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ReviewQueueStore(Path(tmp) / "cache.db")
            item = _insert_review_item(store)
            item_id = int(item["id"])
            store.update_review_status(item_id, "approved")
            store.update_review_status(item_id, "manually_corrected", "manual edit")

            versions = store.list_metric_versions(item_id)
            active_versions = [row for row in versions if row["isActive"]]
            self.assertEqual(len(active_versions), 1)
            self.assertEqual(active_versions[0]["reviewStatus"], "manually_corrected")
            self.assertTrue(any(row["reviewStatus"] == "approved" and not row["isActive"] for row in versions))

    def test_undo_manual_correction_restores_previous_confirmed_value(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ReviewQueueStore(Path(tmp) / "cache.db")
            item = _insert_review_item(store, value=20)
            item_id = int(item["id"])
            store.update_review_status(item_id, "approved")
            store.correct_item(item_id, 25, "percent", "Q1 2026", "manual edit")
            corrected = store.get_item(item_id)
            self.assertEqual(corrected["reviewStatus"], "manually_corrected")
            self.assertEqual(corrected["value"], 25)
            self.assertTrue(canMetricEnterScoring(corrected))

            store.undo_review_status(item_id, "pending_review", "undo manual edit")
            restored = store.get_item(item_id)
            self.assertEqual(restored["reviewStatus"], "approved")
            self.assertEqual(restored["value"], 20)
            self.assertNotEqual(restored["value"], corrected["value"])
            self.assertTrue(canMetricEnterScoring(restored))
            active_versions = [row for row in store.list_metric_versions(item_id) if row["isActive"]]
            self.assertEqual(len(active_versions), 1)
            self.assertEqual(active_versions[0]["reviewStatus"], "approved")
            self.assertEqual(active_versions[0]["value"], 20)
            self.assertIn("undo_manual_correct", store.get_score_status("NOW")["staleReason"])

    def test_rejected_and_archived_versions_do_not_remain_active(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ReviewQueueStore(Path(tmp) / "cache.db")
            item = _insert_review_item(store)
            item_id = int(item["id"])
            store.update_review_status(item_id, "approved")
            store.update_review_status(item_id, "rejected")
            rejected = store.get_item(item_id)
            self.assertEqual(rejected["reviewStatus"], "rejected")
            self.assertFalse(canMetricEnterScoring(rejected))
            self.assertFalse([row for row in store.list_metric_versions(item_id) if row["isActive"]])
            store.undo_review_status(item_id, "approved", "undo rejected")
            pending = store.get_item(item_id)
            self.assertEqual(pending["reviewStatus"], "pending_review")
            self.assertFalse(canMetricEnterScoring(pending))

            second = _insert_review_item(store, metric_key="rpoGrowth", value=13)
            second_id = int(second["id"])
            store.auto_archive_item(second_id, "low priority")
            archived = store.get_item(second_id)
            self.assertEqual(archived["reviewStatus"], "auto_archived")
            self.assertFalse(canMetricEnterScoring(archived))
            self.assertFalse([row for row in store.list_metric_versions(second_id) if row["isActive"]])
            store.undo_review_status(second_id, "needs_data", "restore archived")
            needs_data = store.get_item(second_id)
            self.assertEqual(needs_data["reviewStatus"], "needs_data")
            self.assertFalse(canMetricEnterScoring(needs_data))
            actions = [row["action"] for row in store.list_review_audit_logs(second_id)]
            self.assertIn("undo_archive", actions)

    def test_recent_confirmed_lists_approved_and_corrected_items(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ReviewQueueStore(Path(tmp) / "cache.db")
            approved = _insert_review_item(store, metric_key="subscriptionRevenueGrowth")
            corrected = _insert_review_item(store, metric_key="rpoGrowth", value=13)
            auto_approved = _insert_review_item(store, metric_key="netRetentionRate", value=115)
            store.update_review_status(int(approved["id"]), "approved")
            store.update_review_status(int(corrected["id"]), "manually_corrected")
            store.apply_ai_auto_approval(int(auto_approved["id"]), explanation_zh="validated evidence")
            rows = store.list_recent_confirmed_items()
            metrics = {row["metricKey"] for row in rows}
            self.assertIn("subscriptionRevenueGrowth", metrics)
            self.assertIn("rpoGrowth", metrics)
            self.assertIn("netRetentionRate", metrics)
            self.assertTrue(all(row["canEnterScoring"] for row in rows))
            self.assertTrue(all(row["reviewItemId"] for row in rows))

    def test_high_impact_confirm_requires_guard(self) -> None:
        from ui.manual_review import _requires_high_impact_confirmation

        self.assertTrue(_requires_high_impact_confirmation({"metricKey": "cRpoGrowth", "affects": "Quality"}))
        self.assertTrue(_requires_high_impact_confirmation({"metricKey": "otherMetric", "affects": "Action"}))
        self.assertFalse(_requires_high_impact_confirmation({"metricKey": "lowPriorityNote", "affects": "ExplanationOnly"}))


class MetricVariantFreshnessTests(unittest.TestCase):
    def test_servicenow_crpo_reported_and_constant_currency_split(self) -> None:
        text = "Q4 2025 cRPO was $12.85 billion, representing 25% year-over-year growth and 21% in constant currency."
        metrics = {metric.metric_key: metric for metric in extract_saas_metric_variants(text)}
        self.assertAlmostEqual(metrics["cRpoGrowthReported"].value, 0.25)
        self.assertEqual(metrics["cRpoGrowthReported"].metric_variant, "cRpoGrowthReported")
        self.assertEqual(metrics["cRpoGrowthReported"].target_basis, "reported_yoy")
        self.assertAlmostEqual(metrics["cRpoGrowthConstantCurrency"].value, 0.21)
        self.assertEqual(metrics["cRpoGrowthConstantCurrency"].target_basis, "constant_currency_yoy")

    def test_servicenow_rpo_reported_and_constant_currency_split(self) -> None:
        text = "RPO was $28.2 billion, representing 26.5% year-over-year growth, 22.5% in constant currency."
        metrics = {metric.metric_key: metric for metric in extract_saas_metric_variants(text)}
        self.assertAlmostEqual(metrics["rpoGrowthReported"].value, 0.265)
        self.assertAlmostEqual(metrics["rpoGrowthConstantCurrency"].value, 0.225)

    def test_latest_metric_variant_is_active_and_old_period_is_historical(self) -> None:
        with TemporaryDirectory() as tmp:
            store = DisclosureStore(Path(tmp) / "cache.db")
            evidence_q4 = "Q4 2025 cRPO was $12.85 billion, representing 25% year-over-year growth and 21% in constant currency."
            evidence_q1 = "Q1 2026 cRPO was $13.4 billion, representing 22.5% year-over-year growth."
            store.save_metric(
                "NOW",
                "cRpoGrowthReported",
                0.25,
                "percent",
                "2025 Q4",
                "SEC_8K",
                "https://example.com/q4",
                "NOW Q4 2025 release",
                evidence_q4,
                "high",
                review_status="approved",
                metric_variant="cRpoGrowthReported",
                target_basis="reported_yoy",
            )
            store.save_metric(
                "NOW",
                "cRpoGrowthReported",
                0.225,
                "percent",
                "2026 Q1",
                "SEC_8K",
                "https://example.com/q1",
                "NOW Q1 2026 release",
                evidence_q1,
                "high",
                review_status="approved",
                metric_variant="cRpoGrowthReported",
                target_basis="reported_yoy",
            )
            rows = {(row["period"], row["metricKey"]): row for row in store.get_metrics("NOW")}
            self.assertEqual(rows[("2026 Q1", "cRpoGrowthReported")]["freshnessStatus"], "active_current")
            self.assertEqual(rows[("2025 Q4", "cRpoGrowthReported")]["freshnessStatus"], "historical_value")
            scoring_rows = store.best_metrics("NOW", scoring_only=True)
            self.assertAlmostEqual(scoring_rows["cRpoGrowthReported"]["value"], 0.225)

    def test_fcf_margin_taxonomy_separates_operating_cash_flow_and_nongaap_fcf(self) -> None:
        ocf = extract_saas_metric_variants("GAAP net cash provided by operating activities as % of total revenues 44.5%.")
        ocf_keys = {metric.metric_key for metric in ocf}
        self.assertIn("operatingCashFlowMargin", ocf_keys)
        self.assertNotIn("nonGaapFcfMargin", ocf_keys)

        fcf = extract_saas_metric_variants("Non-GAAP free cash flow margin 44%.")
        fcf_keys = {metric.metric_key for metric in fcf}
        self.assertIn("nonGaapFcfMargin", fcf_keys)

    def test_qwen_input_includes_variant_basis_and_deterministic_exact(self) -> None:
        row = {
            "symbol": "NOW",
            "metricKey": "cRpoGrowthReported",
            "metricVariant": "cRpoGrowthReported",
            "displayName": "cRPO growth reported",
            "value": 0.25,
            "unit": "percent",
            "period": "2025 Q4",
            "sourceType": "SEC_8K",
            "sourceUrl": "https://example.com/q4",
            "sourceDocumentTitle": "NOW Q4 2025 release",
            "evidenceText": "Q4 2025 cRPO was $12.85 billion, representing 25% year-over-year growth and 21% in constant currency.",
            "confidence": "high",
            "affects": "Quality",
            "itemType": "extracted_value",
            "reviewStatus": "pending_review",
            "targetBasis": "reported_yoy",
            "freshnessStatus": "active_current",
        }
        payload = build_qwen_review_input(row)
        self.assertEqual(payload["metricVariant"], "cRpoGrowthReported")
        self.assertEqual(payload["targetBasis"], "reported_yoy")
        self.assertEqual(payload["targetValue"], 25.0)
        self.assertEqual(payload["deterministicPrecheck"], "exact")

    def test_qwen_partial_match_can_be_machine_verified_when_deterministic_exact(self) -> None:
        row = {
            "symbol": "NOW",
            "metricKey": "cRpoGrowthReported",
            "metricVariant": "cRpoGrowthReported",
            "displayName": "cRPO growth reported",
            "value": 0.25,
            "unit": "percent",
            "period": "2025 Q4",
            "sourceType": "SEC_8K",
            "sourceUrl": "https://example.com/q4",
            "sourceDocumentTitle": "NOW Q4 2025 release",
            "evidenceText": "Q4 2025 cRPO was $12.85 billion, representing 25% year-over-year growth and 21% in constant currency.",
            "confidence": "high",
            "affects": "Quality",
            "itemType": "extracted_value",
            "reviewStatus": "pending_review",
            "targetBasis": "reported_yoy",
            "freshnessStatus": "active_current",
        }
        result = enforce_qwen_evidence_only(
            row,
            {
                "aiDecision": "recommend_approve",
                "correctedValue": None,
                "correctedUnit": None,
                "correctedPeriod": None,
                "confidenceScore": 0.8,
                "evidenceMatch": "partial_match",
                "periodMatch": "exact",
                "unitMatch": "exact",
                "riskLevel": "low",
                "explanationZh": "多个口径在同一句内。",
                "evidenceQuote": "representing 25% year-over-year growth",
                "warnings": [],
            },
        )
        self.assertEqual(result["evidenceMatch"], "exact_match")
        self.assertIn("qwen_partial_but_deterministic_exact", result["warnings"])

    def test_historical_review_item_does_not_show_confirm_as_primary(self) -> None:
        from ui.manual_review import _review_primary_action

        action = _review_primary_action(
            {
                "itemType": "extracted_value",
                "reviewStatus": "pending_review",
                "freshnessStatus": "historical_value",
                "value": 0.25,
                "unit": "percent",
                "sourceType": "SEC_8K",
                "sourceUrl": "https://example.com/q4",
                "sourceDocumentTitle": "NOW Q4 2025 release",
                "evidenceText": "Q4 2025 cRPO was $12.85 billion, representing 25% year-over-year growth.",
                "metricPeriod": "2025 Q4",
            }
        )
        self.assertEqual(action["key"], "keep_historical")


if __name__ == "__main__":
    unittest.main()
