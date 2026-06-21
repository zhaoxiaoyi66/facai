from __future__ import annotations

from data.review_candidate_quality import current_value_override
from data.review_candidate_quality import has_review_value
from data.review_candidate_quality import is_current_revenue_backlog_candidate
from data.review_candidate_quality import is_risk_observation_item


ACTIVE_STATUSES = {"pending_review", "needs_data", "needs_evidence", "stale"}


def test_structured_debt_maturity_is_not_a_plain_numeric_confirmation() -> None:
    row = {"metricKey": "aiCloudDebtMaturity", "value": 2026, "itemType": "extracted_value"}

    assert current_value_override(row) == "债务到期结构，需人工整理"
    assert is_risk_observation_item(row, has_value=True)


def test_unclear_net_debt_scale_requires_more_evidence() -> None:
    row = {
        "metricKey": "aiCloudNetDebt",
        "value": 69,
        "itemType": "extracted_value",
        "evidenceText": "Net Debt Market Capitalization (As of 4/7/2026) 51,051 $69,539 22.5x Enterprise Value.",
    }

    assert current_value_override(row) == "金额单位不清，需补充证据"


def test_customer_concentration_numeric_candidate_is_not_generic_risk_observation() -> None:
    row = {
        "metricKey": "aiCloudCustomerConcentration",
        "itemType": "extracted_value",
        "value": 0.45,
        "displayName": "Customer concentration",
    }

    assert not is_risk_observation_item(row, has_value=True)


def test_current_revenue_backlog_is_kept_as_main_candidate_without_becoming_rpo() -> None:
    row = {
        "metricKey": "aiCloudContractedBacklog",
        "itemType": "extracted_value",
        "reviewStatus": "pending_review",
        "freshnessStatus": "historical_value",
        "value": 100_000_000_000,
        "evidenceText": "The company had nearly $100 billion of revenue backlog.",
    }

    assert is_current_revenue_backlog_candidate(row, ACTIVE_STATUSES)


def test_localized_missing_display_value_is_not_review_value() -> None:
    assert not has_review_value({"displayValue": "待补"})
    assert not has_review_value({"displayValue": "暂缺"})
    assert not has_review_value({"displayValue": "暂无"})
