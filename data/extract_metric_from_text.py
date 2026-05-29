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
            scale_context_start = max(0, index - 800)
            scale_context_end = min(len(normalized_text), index + len(alias) + 260)
            scale_context = normalized_text[scale_context_start:scale_context_end]
            value = _extract_value(post_alias_window, metric_definition.unit_hint, scale_context) or _extract_value(
                window,
                metric_definition.unit_hint,
                scale_context,
            )
            if value:
                number, unit = value
                valid, _reason = validate_extracted_metric_candidate(metric_definition.metric_key, window, number)
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

    return _best_candidate(candidates, metric_definition.metric_key)


def validate_extracted_metric_candidate(metric_key: str, evidence_text: str, candidate_value: float | None = None) -> tuple[bool, str]:
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
    if metric_value_scope_mismatch(canonical_key, text, candidate_value):
        return False, "金额单位、期间或公司级口径不匹配，不能作为主候选。"
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


def _extract_value(window: str, unit_hint: str, scale_context: str | None = None) -> tuple[float, str] | None:
    if unit_hint == "multiple":
        match = MULTIPLE_PATTERN.search(window)
        if match:
            return float(match.group(1)), "x"

    if unit_hint == "money":
        money = MONEY_PATTERN.search(window)
        if money:
            amount = float(money.group(1))
            suffix = (money.group(2) or "").lower()
            amount *= _money_unit_multiplier(suffix, scale_context or window)
            return amount, "usd"

    percent = PERCENT_PATTERN.search(window)
    if percent:
        return float(percent.group(1)) / 100, "percent"

    if unit_hint == "multiple":
        number = NUMBER_PATTERN.search(window)
        if number:
            return float(number.group(1)), "x"

    return None


def _best_candidate(candidates: list[ExtractedMetric], metric_key: str | None = None) -> ExtractedMetric | None:
    if not candidates:
        return None
    if _expected_period_scope(metric_key) == "quarterly":
        scoped = [
            candidate
            for candidate in candidates
            if not metric_value_scope_mismatch(metric_key, candidate.extracted_text, candidate.value)
        ]
        if scoped:
            candidates = scoped
    return max(candidates, key=lambda item: (len(item.extracted_text), abs(item.value)))


def metric_value_scope_mismatch(metric_key: str, evidence_text: str, value: float | None = None) -> bool:
    text = _normalize_whitespace(evidence_text).lower()
    canonical_key = _canonical_metric_key(metric_key)

    if _requires_company_level_asset_scope(canonical_key):
        if _has_sub_business_asset_context(text):
            return True
        return not _has_company_level_asset_context(text)

    if _expected_period_scope(canonical_key) == "quarterly":
        if value is not None:
            context = _money_context_for_value(text, value)
            if _period_scope_mismatch(context, expected="quarterly"):
                return True
            return False
        if _period_scope_mismatch(text, expected="quarterly"):
            return True
        return not re.search(r"\bnet deposits?\s+(?:were|was|of)?\s*\$?\s*\d", text)

    if _looks_like_scaled_non_gaap_money_metric(canonical_key, text, value):
        return True

    return False


def money_metric_scope_mismatch(metric_key: str, evidence_text: str, value: float | None = None) -> bool:
    return metric_value_scope_mismatch(metric_key, evidence_text, value)


def _requires_company_level_asset_scope(metric_key: str) -> bool:
    token = _normalized_metric_token(metric_key)
    return "auc" in token or "assetsundercustody" in token


def _has_sub_business_asset_context(text: str) -> bool:
    return bool(
        re.search(
            r"\b(robinhood strategies|robinhood retirement|retirement auc|segment|business line|sub[- ]business|assets under management|aum)\b",
            text,
        )
    )


def _has_company_level_asset_context(text: str) -> bool:
    return bool(re.search(r"\b(total\s+auc|auc|assets under custody|platform auc|company[- ]level|consolidated)\b", text))


def _expected_period_scope(metric_key: str) -> str | None:
    token = _normalized_metric_token(metric_key)
    if "netdeposits" in token or "netdeposit" in token:
        return "quarterly"
    return None


def _period_scope_mismatch(text: str, expected: str) -> bool:
    if expected != "quarterly":
        return False
    prefix = text.split("$", 1)[0]
    if re.search(r"\b(ttm|ltm|trailing|last|past|over)\b.{0,80}\b(twelve|12)\s+months?\b", prefix):
        return True
    if re.search(r"\b(full year|fy|year ended|in\s+20\d{2})\b", prefix) and not re.search(r"\bquarter\b|\bq[1-4]\b", text):
        return True
    return False


def _looks_like_scaled_non_gaap_money_metric(metric_key: str, text: str, value: float | None) -> bool:
    if value is None:
        return False
    token = _normalized_metric_token(metric_key)
    if not ("ebitda" in token or "earnings" in token or "netincome" in token):
        return False
    return 0 < abs(value) < 1_000_000 and bool(
        re.search(r"\b(adjusted|normalized|non[- ]gaap).{0,60}(ebitda|earnings|net income)\b", text)
    )


def _normalized_metric_token(metric_key: str) -> str:
    return "".join(ch for ch in str(metric_key or "").lower() if ch.isalnum())


def _money_unit_multiplier(suffix: str, context: str) -> float:
    if suffix in {"billion", "bn"}:
        return 1_000_000_000
    if suffix in {"million", "mm", "m"}:
        return 1_000_000
    lowered = context.lower()
    if re.search(r"(?:\(|\b)in\s+(?:u\.?s\.?\s+dollars?\s+)?billions?\b|dollars?\s+in\s+billions?\b", lowered):
        return 1_000_000_000
    if re.search(r"(?:\(|\b)in\s+(?:u\.?s\.?\s+dollars?\s+)?millions?\b|dollars?\s+in\s+millions?\b", lowered):
        return 1_000_000
    return 1


def _money_context_for_value(text: str, value: float | None) -> str:
    if value is None:
        return text
    previous_money_end = 0
    for match in MONEY_PATTERN.finditer(text):
        amount = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        context_start = max(previous_money_end, match.start() - 120)
        context_end = min(len(text), match.end() + 120)
        scaled = amount * _money_unit_multiplier(suffix, text[context_start:context_end])
        if _values_close(scaled, value):
            return text[context_start:min(len(text), match.end() + 160)]
        previous_money_end = match.end()
    return text


def _values_close(left: float, right: float) -> bool:
    if right == 0:
        return abs(left) < 1e-9
    return abs(left - right) / max(abs(right), 1) < 0.005


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _looks_like_cost_context(lowered_text: str, index: int) -> bool:
    prefix = lowered_text[max(0, index - 24):index]
    return "cost of" in prefix or "costs of" in prefix
