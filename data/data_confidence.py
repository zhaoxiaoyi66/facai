from __future__ import annotations

from data.ir_kpi_scraper import kpi_mapping_for_ticker


SAAS_CRITICAL_METRICS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("revenue growth", ("forward_revenue_growth", "revenue_growth"), "missing"),
    ("GAAP operating margin", ("operating_margin",), "missing"),
    ("FCF margin", ("fcf_margin", "free_cash_flow"), "missing"),
    ("SBC / revenue", ("manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio"), "vendor_unavailable"),
    ("net debt / EBITDA", ("manualNetDebtToAdjustedEbitda", "net_debt_to_ebitda"), "vendor_unavailable"),
    ("interest coverage", ("interest_coverage",), "vendor_unavailable"),
    ("subscription revenue growth", ("manualSubscriptionRevenueGrowth", "subscription_revenue_growth"), "requires_ir_scrape"),
    ("RPO / cRPO growth", ("manualRpoGrowth", "rpo_growth", "crpo_growth"), "requires_ir_scrape"),
    ("non-GAAP operating margin", ("manualNonGaapOperatingMargin", "non_gaap_operating_margin"), "requires_ir_scrape"),
    ("net retention rate", ("manualNetRetention", "net_retention_rate"), "not_disclosed"),
    ("large customer growth", ("manualLargeCustomerGrowth", "large_customer_growth"), "requires_ir_scrape"),
    ("PEG", ("peg_ratio", "peg"), "requires_estimates"),
)


def enrich_data_confidence(snapshot: dict) -> dict:
    enriched = dict(snapshot)
    ticker = str(enriched.get("ticker") or enriched.get("symbol") or "").upper()
    model_type = str(enriched.get("modelType") or enriched.get("model_type") or "").upper()
    if model_type != "SAAS_SOFTWARE" and ticker not in {"NOW", "ADBE", "CRM", "SNOW", "DDOG", "MDB", "NET", "CRWD", "PLTR", "ORCL"}:
        return enriched

    available: list[str] = []
    missing: list[str] = []
    not_disclosed: list[str] = []
    estimated: list[str] = []
    vendor_unavailable: list[str] = []
    requires_ir_scrape: list[str] = []
    requires_estimates: list[str] = []

    metric_sources = enriched.get("metric_sources") if isinstance(enriched.get("metric_sources"), dict) else {}
    metric_statuses = enriched.get("metric_statuses") if isinstance(enriched.get("metric_statuses"), dict) else {}
    mapping = kpi_mapping_for_ticker(ticker)

    for label, keys, fallback_status in SAAS_CRITICAL_METRICS:
        key = _first_available_key(enriched, keys)
        if key:
            available.append(label)
            source_type = _source_type(metric_sources, key, enriched.get(f"{key}_sourceType"))
            if source_type == "estimated":
                estimated.append(label)
            continue

        status = _metric_status(metric_statuses, keys)
        if status is None:
            status = _mapping_status(mapping, keys) or fallback_status

        if status == "not_disclosed":
            not_disclosed.append(label)
        elif status == "vendor_unavailable":
            vendor_unavailable.append(label)
        elif status == "requires_ir_scrape":
            requires_ir_scrape.append(label)
        elif status == "requires_estimates":
            requires_estimates.append(label)
        else:
            missing.append(label)

    missing_critical = [*missing, *vendor_unavailable, *requires_ir_scrape, *requires_estimates]
    total = len(SAAS_CRITICAL_METRICS)
    available_weight = len(available)
    confidence_pct = available_weight / total if total else 1
    if confidence_pct >= 0.75:
        data_confidence = "high"
    elif confidence_pct >= 0.45:
        data_confidence = "medium"
    else:
        data_confidence = "low"
    pending_review_critical = _list_value(enriched.get("criticalPendingReviewMetrics"))
    if pending_review_critical and data_confidence == "high":
        data_confidence = "medium"
    ai_abnormal_critical = _list_value(enriched.get("criticalAiAbnormalMetrics"))
    if ai_abnormal_critical and data_confidence == "high":
        data_confidence = "medium"

    enriched.update(
        {
            "availableCriticalMetrics": available,
            "missingCriticalMetrics": missing_critical,
            "notDisclosedMetrics": not_disclosed,
            "estimatedMetrics": estimated,
            "vendorUnavailableMetrics": vendor_unavailable,
            "requiresIrScrapeMetrics": requires_ir_scrape,
            "requiresEstimatesMetrics": requires_estimates,
            "pendingReviewCriticalMetrics": pending_review_critical,
            "criticalAiAbnormalMetrics": ai_abnormal_critical,
            "dataConfidence": data_confidence,
            "dataConfidencePct": round(confidence_pct * 100, 1),
        }
    )
    return enriched


def _first_available_key(snapshot: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if snapshot.get(key) is not None:
            return key
    return None


def _source_type(metric_sources: dict, key: str, direct: object = None) -> str | None:
    if isinstance(direct, str):
        return direct
    raw = metric_sources.get(key)
    if isinstance(raw, dict):
        return raw.get("sourceType") or raw.get("source_type")
    if isinstance(raw, str):
        return raw
    return None


def _metric_status(metric_statuses: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        raw = metric_statuses.get(key)
        if isinstance(raw, dict) and raw.get("status"):
            return str(raw["status"])
        if isinstance(raw, str):
            return raw
    return None


def _mapping_status(mapping: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key.startswith("manual"):
            continue
        config = mapping.get(key)
        if config:
            return config.status
    return None


def _list_value(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return []
