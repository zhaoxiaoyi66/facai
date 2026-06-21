from __future__ import annotations

from typing import Any

from data.extract_metric_from_text import metric_value_scope_mismatch


STRUCTURED_DEBT_MATURITY_METRICS = {"aiCloudDebtMaturity"}
STRUCTURED_DEBT_MATURITY_TEXT = "债务到期结构，需人工整理"
UNCLEAR_NET_DEBT_TEXT = "金额单位不清，需补充证据"
MONEY_SCOPE_MISMATCH_TEXT = "金额单位或口径不匹配，需降级归档，不能作为主候选。"
GENERIC_RISK_KEYWORDS = (
    "customerconcentration",
    "customerconcentrationrisk",
    "客户集中",
    "semiconductorcyclerisk",
    "半导体周期",
    "exportcontrolrisk",
    "chinaregulatoryrisk",
    "regulatoryrisk",
    "出口管制",
    "中国风险",
    "inventorycorrectionrisk",
    "库存修正",
    "cryptocyclesensitivity",
    "cryptoexposurerisk",
    "cryptoassetvolatility",
)
RISK_OBSERVATION_ITEM_TYPES = {"qualitative_risk", "generic_risk", "sector_risk"}


def is_money_scope_mismatch_candidate(row: dict[str, Any]) -> bool:
    if str(row.get("itemType") or "").strip() != "extracted_value":
        return False
    metric_key = str(row.get("metricKey") or "")
    evidence = str(row.get("evidenceText") or row.get("extractedText") or "")
    value = _number(row.get("value"))
    return metric_value_scope_mismatch(metric_key, evidence, value)


def is_risk_observation_item(row: dict[str, Any], has_value: bool) -> bool:
    item_type = _normalized_token(row.get("itemType"))
    metric_key = _normalized_token(row.get("metricKey"))
    display_name = _normalized_token(row.get("displayName"))
    if (
        str(row.get("metricKey") or "") == "aiCloudCustomerConcentration"
        and str(row.get("itemType") or "") == "extracted_value"
        and has_value
    ):
        return False
    if str(row.get("metricKey") or "") in STRUCTURED_DEBT_MATURITY_METRICS:
        return True
    if item_type in RISK_OBSERVATION_ITEM_TYPES:
        return True
    if not has_value and ("risk" in metric_key or "risk" in display_name or "风险" in metric_key or "风险" in display_name):
        return True
    return any(keyword in metric_key or keyword in display_name for keyword in GENERIC_RISK_KEYWORDS)


def is_generic_sector_risk(row: dict[str, Any]) -> bool:
    metric_key = _normalized_token(row.get("metricKey"))
    display_name = _normalized_token(row.get("displayName"))
    combined = f"{metric_key} {display_name}"
    return any(keyword in combined for keyword in GENERIC_RISK_KEYWORDS) or str(row.get("sourceType") or "").strip().lower() in {"missing", "system", "model"}


def current_value_override(row: dict[str, Any]) -> str | None:
    if str(row.get("metricKey") or "") in STRUCTURED_DEBT_MATURITY_METRICS:
        return STRUCTURED_DEBT_MATURITY_TEXT
    if str(row.get("metricKey") or "") == "aiCloudNetDebt" and net_debt_value_lacks_scale(row):
        return UNCLEAR_NET_DEBT_TEXT
    return None


def net_debt_value_lacks_scale(row: dict[str, Any]) -> bool:
    value = None
    for key in ("displayValue", "normalizedValue", "value"):
        if row.get(key) not in (None, ""):
            value = row.get(key)
            break
    number = _number(value)
    if number is None:
        return True
    evidence = _clean_text(row.get("evidenceText") or row.get("extractedText") or "").lower()
    has_scale = any(token in evidence for token in ("million", "millions", "billion", "billions", " in millions", " in billions"))
    return abs(number) < 1_000_000 and not has_scale


def is_current_revenue_backlog_candidate(row: dict[str, Any], active_statuses: set[str]) -> bool:
    value = _number(row.get("normalizedValue") or row.get("value"))
    evidence = _clean_text(row.get("evidenceText") or row.get("extractedText") or "").lower()
    return (
        str(row.get("metricKey") or "") == "aiCloudContractedBacklog"
        and str(row.get("itemType") or "") == "extracted_value"
        and str(row.get("reviewStatus") or "") in active_statuses
        and (
            str(row.get("freshnessStatus") or "active_current") == "active_current"
            or (value is not None and value >= 90_000_000_000 and "nearly $100 billion" in evidence)
        )
        and has_review_value(row)
    )


def has_review_value(row: dict[str, Any]) -> bool:
    return any(_present_review_value(row.get(key)) for key in ("displayValue", "normalizedValue", "value"))


def risk_canonical_metric(row: dict[str, Any]) -> str | None:
    item_type = str(row.get("itemType") or "").strip().lower()
    metric_key_token = _normalized_token(row.get("metricKey"))
    display_token = _normalized_token(row.get("displayName"))
    if item_type not in RISK_OBSERVATION_ITEM_TYPES and "risk" not in metric_key_token and "risk" not in display_token and "风险" not in display_token:
        return None
    combined = f"{metric_key_token} {display_token}"
    if "customerconcentration" in combined or "客户集中" in combined:
        return "customerConcentrationRisk"
    if "semiconductorcycle" in combined or "半导体周期" in combined:
        return "semiconductorCycleRisk"
    if "exportcontrol" in combined or "chinarisk" in combined or "chinaregulatory" in combined or "出口管制" in combined or "中国风险" in combined:
        return "exportControlChinaRisk"
    if "inventorycorrection" in combined or "inventoryrisk" in combined or "库存修正" in combined:
        return "inventoryCorrectionRisk"
    metric_key = str(row.get("metricKey") or "").strip()
    return metric_key or str(row.get("displayName") or "qualitativeRisk")


def _present_review_value(value: object) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, str) and value.strip().lower() in {"n/a", "na", "none", "null", "-", "--", "待补", "暂缺", "暂无"}:
        return False
    return True


def _normalized_token(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
