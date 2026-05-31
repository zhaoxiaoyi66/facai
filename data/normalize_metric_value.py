from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


PERCENT_HINTS = (
    "growth",
    "margin",
    "yield",
    "ratio",
    "rate",
    "retention",
    "coverage",
    "sbc",
    "percent",
    "%",
)

BUSINESS_PERCENT_TOKENS = (
    "growth",
    "margin",
    "ratio",
    "yield",
    "retention",
    "coverage",
    "sbc",
    "rpo",
    "crpo",
    "deposit",
)


@dataclass(frozen=True)
class NormalizedMetricValue:
    rawValue: object
    normalizedValue: float | None
    unit: str | None
    displayValue: str
    valueScale: str


@dataclass(frozen=True)
class EvidenceWindow:
    evidenceWindow: str
    aliasesMatched: list[str]
    evidenceInsufficient: bool


@dataclass(frozen=True)
class MetricPeriod:
    sourcePublishedDate: str | None
    fiscalPeriod: str | None
    metricPeriod: str | None
    periodDisplay: str | None


def normalize_metric_value(value: object, unit: object = None, evidence_text: str = "", metric_key: str = "") -> NormalizedMetricValue:
    raw = value
    numeric = _parse_number(value)
    unit_text = str(unit or "").strip().lower()
    scale = _value_scale(unit_text, evidence_text, metric_key)
    normalized_unit: str | None = unit_text or None
    if scale == "percent":
        normalized_unit = "percent"
        if numeric is not None and abs(numeric) <= 1 and not _has_explicit_percent_marker(raw, numeric, evidence_text):
            numeric = numeric * 100
        display = "N/A" if numeric is None else f"{numeric:.1f}%"
    elif scale == "multiple":
        normalized_unit = "x"
        display = "N/A" if numeric is None else f"{numeric:.1f}x"
    elif scale == "currency":
        normalized_unit = unit_text or "currency"
        display = "N/A" if numeric is None else f"{numeric:,.1f}"
    else:
        display = "N/A" if numeric is None else f"{numeric:g}"
    return NormalizedMetricValue(raw, numeric, normalized_unit, display, scale)


def display_percent_to_scoring_ratio(value: object, unit: object = None, metric_key: str = "") -> float | None:
    """Convert review-center percent points to the ratio convention used by scoring."""
    numeric = _parse_number(value)
    if numeric is None:
        return None
    unit_text = str(unit or "").strip().lower()
    if unit_text not in {"percent", "%", "percentage", "pct"} and not is_business_percent_metric(metric_key):
        return numeric
    raw_text = str(value or "").strip().lower() if isinstance(value, str) else ""
    if "%" in raw_text or "percent" in raw_text or abs(numeric) > 1:
        return numeric / 100
    return numeric


def scoring_ratio_to_display_percent(value: object) -> float | None:
    numeric = _parse_number(value)
    if numeric is None:
        return None
    return numeric * 100


def is_business_percent_metric(metric_key: object) -> bool:
    token = _metric_token(metric_key)
    return any(hint in token for hint in BUSINESS_PERCENT_TOKENS)


def normalize_metric_period(row: dict) -> MetricPeriod:
    row_period = _clean(row.get("period"))
    source_date = _date_like(row_period)
    title_period = _period_from_text(_clean(row.get("sourceDocumentTitle")))
    text = " ".join(_clean(row.get(key)) for key in ("extractedText", "explanation") if _clean(row.get(key)))
    text_period = title_period or _period_from_text(text)
    fiscal_period = _period_from_fiscal_fields(row) or text_period
    if row_period and not source_date and _looks_like_fiscal_period(row_period):
        metric_period = row_period
    else:
        metric_period = text_period or fiscal_period
    return MetricPeriod(
        sourcePublishedDate=source_date,
        fiscalPeriod=fiscal_period,
        metricPeriod=metric_period,
        periodDisplay=metric_period or fiscal_period or row_period,
    )


def build_evidence_window(
    text: object,
    aliases: Iterable[object] | None = None,
    value_display: str | None = None,
    limit: int = 1200,
) -> EvidenceWindow:
    source = " ".join(str(text or "").split())
    alias_list = [str(alias or "").strip() for alias in (aliases or []) if str(alias or "").strip()]
    if not source:
        return EvidenceWindow("", [], True)
    lower = source.lower()
    matched = [alias for alias in alias_list if alias.lower() in lower]
    anchors = [alias.lower() for alias in matched]
    if value_display:
        anchors.extend(_value_variants(value_display))
    index = -1
    for anchor in anchors:
        if not anchor:
            continue
        index = lower.find(anchor.lower())
        if index >= 0:
            break
    if index < 0:
        number_match = re.search(r"[-+]?\d+(?:\.\d+)?\s*%?", source)
        index = number_match.start() if number_match else 0
    half = max(200, limit // 2)
    start = max(0, index - half)
    end = min(len(source), start + limit)
    if end - start < limit:
        start = max(0, end - limit)
    window = source[start:end].strip()
    return EvidenceWindow(window, matched, len(window) < 32 or not re.search(r"\d", window))


def deterministic_precheck(row: dict) -> str:
    normalized = normalize_metric_value(
        row.get("normalizedValue", row.get("value")),
        row.get("unit"),
        str(row.get("evidenceWindow") or row.get("extractedText") or ""),
        str(row.get("metricKey") or ""),
    )
    evidence = str(row.get("evidenceWindow") or row.get("extractedText") or "")
    lower = evidence.lower()
    if not evidence.strip() or normalized.normalizedValue is None:
        return "fail"
    aliases = [str(row.get("metricKey") or ""), str(row.get("displayName") or "")]
    alias_hit = any(alias and alias.lower() in lower for alias in aliases)
    value_hit = any(variant and variant.lower() in lower for variant in _value_variants(normalized.displayValue))
    basis = str(row.get("targetBasis") or "").strip().lower()
    period = row.get("metricPeriod") or row.get("periodDisplay") or row.get("period")
    period_hit = True
    if period and _looks_like_fiscal_period(str(period)):
        period_hit = _period_matches_text(str(period), evidence)
    if value_hit and period_hit and _basis_matches_text(basis, normalized.displayValue, evidence):
        return "exact"
    if alias_hit and value_hit and period_hit:
        return "exact"
    if not value_hit:
        return "fail"
    return "unknown"


def _basis_matches_text(basis: str, display_value: str, evidence: str) -> bool:
    if not basis:
        return False
    value_patterns = [re.escape(variant).replace("%", r"\s*(?:%|percent)") for variant in _value_variants(display_value)]
    value_pattern = "|".join(pattern for pattern in value_patterns if pattern)
    if not value_pattern:
        return False
    if basis == "reported_yoy":
        return bool(
            re.search(rf"(?:{value_pattern})[^.，,;]{{0,80}}(?:year[- ]over[- ]year|yoy|growth)", evidence, flags=re.IGNORECASE)
            or re.search(rf"(?:representing|grew|increased|up)[^.，,;]{{0,80}}(?:{value_pattern})", evidence, flags=re.IGNORECASE)
        )
    if basis == "constant_currency_yoy":
        return bool(
            re.search(rf"(?:{value_pattern})[^.，,;]{{0,80}}constant currency", evidence, flags=re.IGNORECASE)
            or re.search(rf"constant currency[^.，,;]{{0,80}}(?:{value_pattern})", evidence, flags=re.IGNORECASE)
        )
    if basis in {"margin", "ratio", "amount", "customer_count_growth"}:
        return bool(re.search(value_pattern, evidence, flags=re.IGNORECASE))
    return False


def period_match_from_evidence(metric_period: object, source_published_date: object, evidence_text: str) -> str:
    period = _clean(metric_period)
    if period and _period_matches_text(period, evidence_text):
        return "exact"
    if period:
        return "ambiguous"
    # Filing date is metadata, not the metric period. It should not create a mismatch by itself.
    if _date_like(_clean(source_published_date)):
        return "ambiguous"
    return "ambiguous"


def _parse_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _has_explicit_percent_marker(value: object, numeric: float | None, evidence_text: str = "") -> bool:
    raw_text = str(value or "").strip().lower() if isinstance(value, str) else ""
    if "%" in raw_text or "percent" in raw_text:
        return True
    if numeric is None or not evidence_text:
        return False
    numeric_forms = {
        f"{numeric:g}",
        f"{numeric:.1f}",
        f"{numeric:.2f}".rstrip("0").rstrip("."),
    }
    for numeric_form in {form for form in numeric_forms if form}:
        if re.search(rf"(?<![\d.]){re.escape(numeric_form)}\s*(?:%|percent|percentage)", evidence_text, flags=re.IGNORECASE):
            return True
    return False


def _metric_token(metric_key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(metric_key or "").lower())


def _value_scale(unit_text: str, evidence_text: str, metric_key: str) -> str:
    combined = f"{unit_text} {evidence_text} {metric_key}".lower()
    if unit_text in {"year", "years", "count", "number"}:
        return "count"
    if unit_text in {"percent", "%", "percentage"}:
        return "percent"
    if unit_text in {"usd", "currency", "$", "dollar", "dollars"} or "usd" in unit_text or "dollar" in unit_text:
        return "currency"
    if any(hint in combined for hint in PERCENT_HINTS):
        return "percent"
    if unit_text in {"x", "multiple", "times"} or re.search(r"\d+(?:\.\d+)?x\b", combined):
        return "multiple"
    if any(token in combined for token in ("$", "usd", "million", "billion")):
        return "currency"
    return "count"


def _value_variants(display_value: str) -> list[str]:
    text = str(display_value or "").strip()
    variants = {text, text.replace(".0%", "%")}
    number = _parse_number(text)
    if number is not None:
        variants.add(f"{number:g}")
        variants.add(f"{number:.1f}")
        if "%" in text:
            variants.add(f"{number:g}%")
            variants.add(f"{number:.1f}%")
            variants.add(f"{number / 100:g}")
    return [variant for variant in variants if variant]


def _period_from_text(text: str) -> str | None:
    patterns = [
        r"\b(Q[1-4])[\s_-]*(20\d{2})(?=\D|$)",
        r"\b(20\d{2})[\s_-]*(Q[1-4])\b",
        r"\bFY\s*(20\d{2})\b",
        r"\bfiscal\s+(20\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 2 and groups[0].upper().startswith("Q"):
            return f"{groups[1]} {groups[0].upper()}"
        if len(groups) == 2:
            return f"{groups[0]} {groups[1].upper()}"
        return f"FY{groups[0]}"
    return None


def _period_from_fiscal_fields(row: dict) -> str | None:
    year = row.get("fiscalYear")
    quarter = row.get("fiscalQuarter")
    if year and quarter:
        q = str(quarter).upper()
        if not q.startswith("Q"):
            q = f"Q{q}"
        return f"{year} {q}"
    if year:
        return f"FY{year}"
    return None


def _period_matches_text(period: str, text: str) -> bool:
    normalized_period = _normalize_period_text(period)
    normalized_text = _normalize_period_text(text)
    return normalized_period in normalized_text


def _normalize_period_text(value: str) -> str:
    text = str(value or "").upper()
    text = re.sub(r"\b(Q[1-4])[\s_-]*(20\d{2})(?=\D|$)", r"\2 \1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _looks_like_fiscal_period(value: str) -> bool:
    return bool(re.search(r"\b(Q[1-4][\s_-]*20\d{2}(?=\D|$)|20\d{2}[\s_-]*Q[1-4]|FY\s*20\d{2})\b", value, flags=re.IGNORECASE))


def _date_like(value: str) -> str | None:
    match = re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip())
    return match.group(0) if match else None


def _clean(value: object) -> str:
    return str(value or "").strip()
