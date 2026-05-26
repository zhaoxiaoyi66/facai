from __future__ import annotations

import re
from dataclasses import dataclass

from data.extract_metric_from_text import ExtractedMetric


@dataclass(frozen=True)
class MetricVariantDefinition:
    metricKey: str
    metricVariant: str
    displayName: str
    targetBasis: str


METRIC_VARIANTS: dict[str, MetricVariantDefinition] = {
    "cRpoGrowthReported": MetricVariantDefinition("cRpoGrowthReported", "cRpoGrowthReported", "cRPO增速（reported YoY）", "reported_yoy"),
    "cRpoGrowthConstantCurrency": MetricVariantDefinition(
        "cRpoGrowthConstantCurrency", "cRpoGrowthConstantCurrency", "cRPO增速（constant currency）", "constant_currency_yoy"
    ),
    "rpoGrowthReported": MetricVariantDefinition("rpoGrowthReported", "rpoGrowthReported", "RPO增速（reported YoY）", "reported_yoy"),
    "rpoGrowthConstantCurrency": MetricVariantDefinition(
        "rpoGrowthConstantCurrency", "rpoGrowthConstantCurrency", "RPO增速（constant currency）", "constant_currency_yoy"
    ),
    "subscriptionRevenueGrowthReported": MetricVariantDefinition(
        "subscriptionRevenueGrowthReported", "subscriptionRevenueGrowthReported", "订阅收入增速（reported YoY）", "reported_yoy"
    ),
    "subscriptionRevenueGrowthConstantCurrency": MetricVariantDefinition(
        "subscriptionRevenueGrowthConstantCurrency",
        "subscriptionRevenueGrowthConstantCurrency",
        "订阅收入增速（constant currency）",
        "constant_currency_yoy",
    ),
    "largeCustomerGrowth": MetricVariantDefinition("largeCustomerGrowth", "largeCustomerGrowth", "大客户增长", "customer_count_growth"),
    "nonGaapOperatingMargin": MetricVariantDefinition("nonGaapOperatingMargin", "nonGaapOperatingMargin", "Non-GAAP经营利润率", "margin"),
    "operatingCashFlowMargin": MetricVariantDefinition("operatingCashFlowMargin", "operatingCashFlowMargin", "经营现金流利润率", "margin"),
    "nonGaapFcfMargin": MetricVariantDefinition("nonGaapFcfMargin", "nonGaapFcfMargin", "Non-GAAP FCF利润率", "margin"),
    "directFcfMargin": MetricVariantDefinition("directFcfMargin", "directFcfMargin", "FCF利润率（直接计算）", "margin"),
    "impliedFcfMargin": MetricVariantDefinition("impliedFcfMargin", "impliedFcfMargin", "估算FCF利润率", "ratio"),
}


def metric_variant_definition(metric_key: str | None) -> MetricVariantDefinition | None:
    return METRIC_VARIANTS.get(str(metric_key or ""))


def metric_variant_for_key(metric_key: str | None) -> str:
    definition = metric_variant_definition(metric_key)
    return definition.metricVariant if definition else str(metric_key or "")


def target_basis_for_metric(metric_key: str | None) -> str:
    definition = metric_variant_definition(metric_key)
    if definition:
        return definition.targetBasis
    key = str(metric_key or "")
    if "Margin" in key or "margin" in key:
        return "margin"
    if "Growth" in key or "growth" in key:
        return "reported_yoy"
    return "amount"


def extract_saas_metric_variants(text: str, confidence: str = "medium") -> list[ExtractedMetric]:
    """Extract variant-aware SaaS KPI values from one source text.

    The rules intentionally favor explicit wording over broad proximity so that
    values like "cRPO were 67 percent of RPO" do not become growth metrics.
    """
    source = _normalize(text)
    if not source:
        return []
    extracted: list[ExtractedMetric] = []
    seen: set[tuple[str, float, str]] = set()

    for sentence in _sentences(source):
        for clause in _metric_clauses(sentence):
            lower = clause.lower()
            for family in _rpo_families(clause):
                family_window = _family_window(clause, family)
                reported = _reported_yoy_value(family_window)
                constant_currency = _constant_currency_value(family_window)
                if reported is not None:
                    metric_key = "cRpoGrowthReported" if family == "crpo" else "rpoGrowthReported"
                    _append_unique(extracted, seen, metric_key, reported / 100, family_window, confidence)
                if constant_currency is not None:
                    metric_key = "cRpoGrowthConstantCurrency" if family == "crpo" else "rpoGrowthConstantCurrency"
                    _append_unique(extracted, seen, metric_key, constant_currency / 100, family_window, confidence)

            if ("subscription revenue" in lower or "subscription revenues" in lower) and not _segment_subscription_clause(lower):
                reported = _reported_yoy_value(clause)
                constant_currency = _constant_currency_value(clause)
                if reported is not None:
                    _append_unique(extracted, seen, "subscriptionRevenueGrowthReported", reported / 100, clause, confidence)
                if constant_currency is not None:
                    _append_unique(extracted, seen, "subscriptionRevenueGrowthConstantCurrency", constant_currency / 100, clause, confidence)

            non_gaap_fcf = _non_gaap_fcf_margin(clause)
            if non_gaap_fcf is not None:
                _append_unique(extracted, seen, "nonGaapFcfMargin", non_gaap_fcf / 100, clause, confidence)

            operating_cash_flow_margin = _operating_cash_flow_margin(clause)
            if operating_cash_flow_margin is not None:
                _append_unique(extracted, seen, "operatingCashFlowMargin", operating_cash_flow_margin / 100, clause, confidence)

    return extracted


def _metric_clauses(sentence: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*(?:\u2022|;)\s*", sentence) if part.strip()]


def _rpo_families(sentence: str) -> list[str]:
    lower = sentence.lower()
    families: list[str] = []
    if "crpo" in lower or "current remaining performance obligations" in lower or "current rpo" in lower:
        families.append("crpo")
    rpo_matches = list(re.finditer(r"\brpo\b|remaining performance obligations", lower))
    for match in rpo_matches:
        prefix = lower[max(0, match.start() - 16) : match.start()]
        if "current" in prefix or "crpo" in prefix:
            continue
        families.append("rpo")
        break
    return families


def _family_window(sentence: str, family: str) -> str:
    patterns = (
        (r"crpo|current remaining performance obligations|current rpo",)
        if family == "crpo"
        else (r"\brpo\b|remaining performance obligations",)
    )
    lower = sentence.lower()
    indexes: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            if family == "rpo":
                prefix = lower[max(0, match.start() - 16) : match.start()]
                if "current" in prefix or "crpo" in prefix:
                    continue
            indexes.append(match.start())
    if not indexes:
        return sentence
    start = max(0, min(indexes) - 24)
    return sentence[start : min(len(sentence), start + 260)]


def _segment_subscription_clause(lower: str) -> bool:
    if "total subscription revenue" in lower or "total subscription revenues" in lower:
        return False
    segment_markers = (
        "business professionals",
        "consumers group",
        "consumer group",
        "creative professionals",
        "digital media",
        "digital experience",
        "customer group",
    )
    return any(marker in lower for marker in segment_markers)


def _rpo_family(sentence_lower: str) -> str | None:
    families = _rpo_families(sentence_lower)
    return families[0] if families else None


def _append_unique(
    extracted: list[ExtractedMetric],
    seen: set[tuple[str, float, str]],
    metric_key: str,
    value: float,
    sentence: str,
    confidence: str,
) -> None:
    definition = metric_variant_definition(metric_key)
    key = (metric_key, round(value, 8), sentence[:120])
    if key in seen:
        return
    seen.add(key)
    extracted.append(
        ExtractedMetric(
            metric_key=metric_key,
            value=value,
            unit="percent",
            extracted_text=sentence.strip(),
            confidence=confidence,
            metric_variant=definition.metricVariant if definition else metric_key,
            target_basis=definition.targetBasis if definition else target_basis_for_metric(metric_key),
        )
    )


def _reported_yoy_value(sentence: str) -> float | None:
    patterns = [
        r"representing\s+(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:year[- ]over[- ]year|yoy)(?:\s+growth)?",
        r"(?:grew|growth was|increased|up)\s+(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:year[- ]over[- ]year|yoy)?",
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:year[- ]over[- ]year|yoy)\s+growth",
    ]
    for pattern in patterns:
        match = re.search(pattern, sentence, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _constant_currency_value(sentence: str) -> float | None:
    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+in\s+constant\s+currency",
        r"constant\s+currency[^.]{0,40}?(\d+(?:\.\d+)?)\s*(?:%|percent)",
    ]
    for pattern in patterns:
        match = re.search(pattern, sentence, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _non_gaap_fcf_margin(sentence: str) -> float | None:
    match = re.search(
        r"\b(?:non[- ]gaap\s+)?(?:free cash flow|fcf)\s+margin(?:\s+was|\s+of)?\s+(\d+(?:\.\d+)?)\s*(?:%|percent)",
        sentence,
        flags=re.IGNORECASE,
    )
    if match and "non" in sentence[: match.start()].lower() + sentence[match.start() : match.start() + 24].lower():
        return float(match.group(1))
    return None


def _operating_cash_flow_margin(sentence: str) -> float | None:
    match = re.search(
        r"(?:net cash provided by operating activities|operating cash flow)[^.]{0,120}?(?:as\s+(?:a\s+)?%\s+of|as a percentage of|margin)[^.]{0,80}?(\d+(?:\.\d+)?)\s*(?:%|percent)",
        sentence,
        flags=re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip()
