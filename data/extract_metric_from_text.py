from __future__ import annotations

import re
from dataclasses import dataclass

from data.metric_dictionary import MetricDefinition


@dataclass(frozen=True)
class ExtractedMetric:
    metric_key: str
    value: float
    unit: str
    extracted_text: str
    confidence: str
    metric_variant: str | None = None
    target_basis: str | None = None


PERCENT_PATTERN = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s?(?:%|percent|percentage points?)", re.IGNORECASE)
MULTIPLE_PATTERN = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s?x\b", re.IGNORECASE)
MONEY_PATTERN = re.compile(r"\$\s?(-?\d+(?:\.\d+)?)\s?(billion|million|bn|mm|m)?", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])")


def extractMetricFromText(text: str, metric_definition: MetricDefinition, confidence: str = "medium") -> ExtractedMetric | None:
    if not text:
        return None

    normalized_text = _normalize_whitespace(text)
    lowered = normalized_text.lower()
    candidates: list[ExtractedMetric] = []

    for alias in metric_definition.aliases:
        alias_lower = alias.lower()
        start = 0
        while True:
            index = lowered.find(alias_lower, start)
            if index < 0:
                break
            if _looks_like_cost_context(lowered, index):
                start = index + len(alias_lower)
                continue
            window_start = max(0, index - 200)
            window_end = min(len(normalized_text), index + len(alias) + 200)
            window = normalized_text[window_start:window_end]
            post_alias_window = normalized_text[index:window_end]
            value = _extract_value(post_alias_window, metric_definition.unit_hint) or _extract_value(window, metric_definition.unit_hint)
            if value:
                number, unit = value
                valid, _reason = validate_extracted_metric_candidate(metric_definition.metric_key, window)
                if not valid:
                    start = index + len(alias_lower)
                    continue
                candidates.append(
                    ExtractedMetric(
                        metric_key=metric_definition.metric_key,
                        value=number,
                        unit=unit,
                        extracted_text=window.strip(),
                        confidence=confidence,
                    )
                )
            start = index + len(alias_lower)

    return _best_candidate(candidates)


def validate_extracted_metric_candidate(metric_key: str, evidence_text: str) -> tuple[bool, str]:
    """Reject obvious metric mapping mistakes before they enter the review queue."""
    text = _normalize_whitespace(evidence_text).lower()
    canonical_key = _canonical_metric_key(metric_key)
    if canonical_key in {"cRpoGrowth", "rpoGrowth"}:
        if metric_key.endswith("ConstantCurrency") and "constant currency" not in text:
            return False, "缺少 constant currency 口径"
        if metric_key.endswith("Reported") and re.search(r"\d+(?:\.\d+)?\s?(?:%|percent)[^.]{0,40}constant currency", text, flags=re.IGNORECASE):
            reported_match = re.search(
                r"\d+(?:\.\d+)?\s?(?:%|percent)\s*(?:year[- ]over[- ]year|yoy|growth)",
                text,
                flags=re.IGNORECASE,
            )
            if not reported_match:
                return False, "该数字更像 constant currency，不是 reported YoY"
        has_growth_context = bool(
            re.search(
                r"\b(grew|growth|increased|increase|up|year[- ]over[- ]year|yoy|representing)\b",
                text,
                flags=re.IGNORECASE,
            )
        ) and bool(re.search(r"\b(growth|grew|increased|up|year[- ]over[- ]year|yoy)\b", text, flags=re.IGNORECASE))
        looks_like_ratio = bool(
            re.search(
                r"\b(were|was|represented|represents|is|are)\s+\d+(?:\.\d+)?\s?(?:%|percent)\b",
                text,
                flags=re.IGNORECASE,
            )
        ) and not has_growth_context
        if looks_like_ratio:
            return False, "该数字表示占比，不是增速"
        if not has_growth_context:
            return False, "缺少 growth / grew / year-over-year 等增速语境"
    if canonical_key == "nonGaapOperatingMargin":
        if not re.search(r"non[- ]gaap.{0,80}operating.{0,40}margin|operating income as a percentage of revenue", text, flags=re.IGNORECASE):
            return False, "原文没有明确 non-GAAP operating margin"
        if re.search(r"non[- ]gaap net income|tax rate|amortization", text, flags=re.IGNORECASE):
            return False, "该文本是 non-GAAP 净利润/税率/摊销，不是经营利润率"
    if canonical_key == "nonGaapFcfMargin":
        if not re.search(r"\b(non[- ]gaap free cash flow margin|non[- ]gaap fcf margin)\b", text, flags=re.IGNORECASE):
            return False, "原文没有明确 non-GAAP free cash flow margin"
    if canonical_key == "operatingCashFlowMargin":
        if not re.search(
            r"\b(net cash provided by operating activities|operating cash flow).{0,90}(?:%|percent|as a percentage of total revenues)",
            text,
            flags=re.IGNORECASE,
        ):
            return False, "原文没有明确 operating cash flow margin"
    if canonical_key == "fcfMargin":
        if not re.search(r"\b(free cash flow margin|fcf margin)\b", text, flags=re.IGNORECASE):
            return False, "原文没有明确 free cash flow margin"
    return True, "valid"


def _canonical_metric_key(metric_key: str) -> str:
    mapping = {
        "cRpoGrowthReported": "cRpoGrowth",
        "cRpoGrowthConstantCurrency": "cRpoGrowth",
        "rpoGrowthReported": "rpoGrowth",
        "rpoGrowthConstantCurrency": "rpoGrowth",
        "subscriptionRevenueGrowthReported": "subscriptionRevenueGrowth",
        "subscriptionRevenueGrowthConstantCurrency": "subscriptionRevenueGrowth",
        "nonGaapFcfMargin": "nonGaapFcfMargin",
        "operatingCashFlowMargin": "operatingCashFlowMargin",
        "directFcfMargin": "fcfMargin",
        "impliedFcfMargin": "fcfMargin",
    }
    return mapping.get(str(metric_key), str(metric_key))


def _extract_value(window: str, unit_hint: str) -> tuple[float, str] | None:
    if unit_hint == "multiple":
        match = MULTIPLE_PATTERN.search(window)
        if match:
            return float(match.group(1)), "x"

    percent = PERCENT_PATTERN.search(window)
    if percent:
        return float(percent.group(1)) / 100, "percent"

    if unit_hint == "money":
        money = MONEY_PATTERN.search(window)
        if money:
            amount = float(money.group(1))
            suffix = (money.group(2) or "").lower()
            if suffix in {"billion", "bn"}:
                amount *= 1_000_000_000
            elif suffix in {"million", "mm", "m"}:
                amount *= 1_000_000
            return amount, "usd"

    if unit_hint == "multiple":
        number = NUMBER_PATTERN.search(window)
        if number:
            return float(number.group(1)), "x"

    return None


def _best_candidate(candidates: list[ExtractedMetric]) -> ExtractedMetric | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: (len(item.extracted_text), abs(item.value)))


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _looks_like_cost_context(lowered_text: str, index: int) -> bool:
    prefix = lowered_text[max(0, index - 24):index]
    return "cost of" in prefix or "costs of" in prefix
