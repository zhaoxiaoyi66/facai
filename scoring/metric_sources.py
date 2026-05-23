from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MetricSourceType = Literal[
    "reported",
    "reported_sec",
    "reported_ir",
    "non_gaap_reported",
    "calculated",
    "derivedFromMarket",
    "estimated",
    "missing",
    "not_disclosed",
    "vendor_unavailable",
    "requires_ir_scrape",
    "requires_estimates",
]

NON_PARTICIPATING_SOURCES = {
    "derivedFromMarket",
    "missing",
    "not_disclosed",
    "vendor_unavailable",
    "requires_ir_scrape",
    "requires_estimates",
}


@dataclass(frozen=True)
class MetricValue:
    key: str
    value: float | None
    sourceType: MetricSourceType
    formula: str | None = None
    note: str = ""

    @property
    def participates_in_quality(self) -> bool:
        return self.sourceType not in NON_PARTICIPATING_SOURCES and self.value is not None

    @property
    def participates_in_risk_reduction(self) -> bool:
        return self.sourceType not in NON_PARTICIPATING_SOURCES and self.value is not None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "sourceType": self.sourceType,
            "formula": self.formula,
            "note": self.note,
        }


REPORTED_FIELDS = {
    "total_revenue",
    "revenue",
    "free_cash_flow",
    "operating_cash_flow",
    "net_income",
    "gross_profit",
    "operating_income",
    "ebitda",
    "total_debt",
    "total_cash",
    "cash_and_equivalents",
}

CALCULATED_FIELDS = {
    "gross_margin",
    "operating_margin",
    "profit_margin",
    "return_on_equity",
    "return_on_invested_capital",
    "current_ratio",
    "net_debt_to_ebitda",
    "price_to_sales",
    "enterprise_to_revenue",
    "price_to_fcf",
    "free_cash_flow_yield",
}


def metric_with_source(snapshot: dict, key: str, *aliases: str) -> MetricValue:
    for candidate in (key, *aliases):
        value = _number(snapshot.get(candidate))
        if value is None:
            value = _number(snapshot.get(_camel_to_snake(candidate)))
        if value is None:
            continue
        source = _explicit_source(snapshot, candidate) or _default_source(candidate)
        return MetricValue(key=key, value=value, sourceType=source)
    return MetricValue(key=key, value=None, sourceType="missing")


def metric_source_type(snapshot: dict, key: str) -> MetricSourceType | None:
    return _explicit_source(snapshot, key)


def metric_participates_in_score(snapshot: dict, key: str) -> bool:
    source = _explicit_source(snapshot, key)
    return source not in NON_PARTICIPATING_SOURCES


def fcf_margin_metric(snapshot: dict) -> MetricValue:
    direct = metric_with_source(snapshot, "direct_fcf_margin", "directFcfMargin", "fcf_margin", "free_cash_flow_margin")
    if direct.value is not None and direct.sourceType != "derivedFromMarket":
        return MetricValue(
            key="fcf_margin",
            value=direct.value,
            sourceType=direct.sourceType if direct.sourceType != "missing" else "reported",
            note=_source_note(direct.sourceType),
        )

    fcf = metric_with_source(snapshot, "free_cash_flow")
    revenue = metric_with_source(snapshot, "total_revenue", "revenue")
    ratio = _ratio(fcf.value, revenue.value)
    if ratio is not None:
        return MetricValue(
            key="fcf_margin",
            value=ratio,
            sourceType="calculated",
            formula="free_cash_flow / total_revenue",
            note="由自由现金流 / 收入计算，参与质量评分。",
        )

    implied_direct = metric_with_source(snapshot, "implied_fcf_margin", "impliedFcfMargin")
    if implied_direct.value is not None:
        return MetricValue(
            key="fcf_margin",
            value=implied_direct.value,
            sourceType="derivedFromMarket",
            formula="free_cash_flow_yield * price_to_sales",
            note="推导值：FCF yield × P/S，不参与质量评分。",
        )

    if direct.value is not None and direct.sourceType == "derivedFromMarket":
        return MetricValue(
            key="fcf_margin",
            value=direct.value,
            sourceType="derivedFromMarket",
            formula="free_cash_flow_yield * price_to_sales",
            note="推导值：FCF yield × P/S，不参与质量评分。",
        )

    fcf_yield = metric_with_source(snapshot, "free_cash_flow_yield")
    price_to_sales = metric_with_source(snapshot, "price_to_sales", "enterprise_to_revenue")
    derived = _ratio_product(fcf_yield.value, price_to_sales.value)
    if derived is not None:
        return MetricValue(
            key="fcf_margin",
            value=derived,
            sourceType="derivedFromMarket",
            formula="free_cash_flow_yield * price_to_sales",
            note="推导值：FCF yield × P/S，不参与质量评分。",
        )

    return MetricValue(key="fcf_margin", value=None, sourceType="missing", note="缺少自由现金流和收入。")


def fcf_margin_source_note(snapshot: dict) -> str:
    metric = fcf_margin_metric(snapshot)
    if metric.sourceType == "derivedFromMarket":
        return "估算FCF利润率：基于 FCF收益率 × 市销率推导，暂不参与公司质量评分"
    if metric.sourceType == "calculated":
        return "FCF利润率：由自由现金流 / 收入计算"
    if metric.sourceType == "reported_sec":
        return "SEC 标准财报字段"
    if metric.sourceType == "reported_ir":
        return "IR / 财报材料披露"
    if metric.sourceType == "non_gaap_reported":
        return "IR 披露的 non-GAAP 指标"
    if metric.sourceType == "estimated":
        return "估算值"
    if metric.sourceType == "reported":
        return "公司或数据源披露"
    return "缺失"


def _explicit_source(snapshot: dict, key: str) -> MetricSourceType | None:
    metric_sources = snapshot.get("metric_sources")
    if isinstance(metric_sources, dict):
        raw = metric_sources.get(key) or metric_sources.get(_camel_to_snake(key))
        if isinstance(raw, dict):
            raw = raw.get("sourceType") or raw.get("source_type")
        source = _normalize_source(raw)
        if source:
            return source

    for suffix in ("sourceType", "source_type"):
        source = _normalize_source(snapshot.get(f"{key}_{suffix}") or snapshot.get(f"{_camel_to_snake(key)}_{suffix}"))
        if source:
            return source
    return None


def _default_source(key: str) -> MetricSourceType:
    key = _camel_to_snake(key)
    if key.startswith("forward_") or key.endswith("_estimate") or "estimate" in key:
        return "estimated"
    if key in REPORTED_FIELDS:
        return "reported"
    if key in CALCULATED_FIELDS or key.endswith("_margin") or key.endswith("_ratio"):
        return "calculated"
    return "reported"


def _source_note(source: MetricSourceType) -> str:
    if source == "derivedFromMarket":
        return "推导值：FCF yield × P/S，不参与质量评分。"
    if source == "calculated":
        return "由原始财务数据计算。"
    if source == "reported_sec":
        return "SEC 标准财报字段。"
    if source == "reported_ir":
        return "IR / 财报材料披露。"
    if source == "non_gaap_reported":
        return "IR 披露的 non-GAAP 指标。"
    if source == "estimated":
        return "第三方预测或系统估算。"
    if source == "not_disclosed":
        return "公司未披露。"
    if source == "vendor_unavailable":
        return "当前供应商无标准字段。"
    if source == "requires_ir_scrape":
        return "需要从 IR 材料抓取。"
    if source == "requires_estimates":
        return "需要分析师预测数据。"
    if source == "reported":
        return "公司或数据源披露。"
    return "缺失。"


def _normalize_source(value) -> MetricSourceType | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized == "derived_from_market":
        normalized = "derivedFromMarket"
    allowed: set[MetricSourceType] = {
        "reported",
        "reported_sec",
        "reported_ir",
        "non_gaap_reported",
        "calculated",
        "derivedFromMarket",
        "estimated",
        "missing",
        "not_disclosed",
        "vendor_unavailable",
        "requires_ir_scrape",
        "requires_estimates",
    }
    return normalized if normalized in allowed else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _ratio_product(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left * right


def _number(value) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
