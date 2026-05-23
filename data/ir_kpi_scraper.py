from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KPIConfig:
    label: str
    status: str
    source_type: str = "reported_ir"


SAAS_KPI_MAPPING: dict[str, dict[str, KPIConfig]] = {
    "NOW": {
        "subscription_revenue_growth": KPIConfig("subscription revenue growth", "requires_ir_scrape"),
        "crpo_growth": KPIConfig("cRPO growth", "requires_ir_scrape"),
        "rpo_growth": KPIConfig("RPO growth", "requires_ir_scrape"),
        "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("net retention rate", "not_disclosed"),
        "large_customer_growth": KPIConfig("customers over $1M / $5M ACV", "requires_ir_scrape"),
    },
    "DDOG": {
        "subscription_revenue_growth": KPIConfig("subscription revenue growth", "not_disclosed"),
        "crpo_growth": KPIConfig("cRPO growth", "not_disclosed"),
        "rpo_growth": KPIConfig("RPO growth", "not_disclosed"),
        "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("net retention rate / DBNRR", "not_disclosed"),
        "large_customer_growth": KPIConfig("customers over $100k ARR", "requires_ir_scrape"),
    },
    "SNOW": {
        "subscription_revenue_growth": KPIConfig("product revenue growth", "requires_ir_scrape"),
        "crpo_growth": KPIConfig("remaining performance obligations growth", "requires_ir_scrape"),
        "rpo_growth": KPIConfig("remaining performance obligations growth", "requires_ir_scrape"),
        "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("net revenue retention rate", "requires_ir_scrape"),
        "large_customer_growth": KPIConfig("customers over $1M product revenue", "requires_ir_scrape"),
    },
    "CRWD": {
        "subscription_revenue_growth": KPIConfig("subscription revenue / ARR growth", "requires_ir_scrape"),
        "crpo_growth": KPIConfig("cRPO growth", "requires_ir_scrape"),
        "rpo_growth": KPIConfig("RPO growth", "requires_ir_scrape"),
        "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("dollar-based net retention rate", "not_disclosed"),
        "large_customer_growth": KPIConfig("ARR / module adoption / large subscription customers", "requires_ir_scrape"),
    },
    "CRM": {
        "subscription_revenue_growth": KPIConfig("subscription and support revenue growth", "requires_ir_scrape"),
        "crpo_growth": KPIConfig("cRPO growth", "requires_ir_scrape"),
        "rpo_growth": KPIConfig("RPO growth", "requires_ir_scrape"),
        "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("net retention rate", "not_disclosed"),
        "large_customer_growth": KPIConfig("large enterprise customer growth", "requires_ir_scrape"),
    },
    "ADBE": {
        "subscription_revenue_growth": KPIConfig("subscription revenue / ARR growth", "requires_ir_scrape"),
        "crpo_growth": KPIConfig("cRPO growth", "not_disclosed"),
        "rpo_growth": KPIConfig("RPO growth", "not_disclosed"),
        "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("net retention rate", "not_disclosed"),
        "large_customer_growth": KPIConfig("large customer growth", "not_disclosed"),
    },
    "PLTR": {
        "subscription_revenue_growth": KPIConfig("commercial revenue / remaining deal value growth", "requires_ir_scrape"),
        "crpo_growth": KPIConfig("cRPO growth", "not_disclosed"),
        "rpo_growth": KPIConfig("remaining deal value growth", "requires_ir_scrape"),
        "non_gaap_operating_margin": KPIConfig("adjusted operating margin", "requires_ir_scrape", "non_gaap_reported"),
        "net_retention_rate": KPIConfig("net dollar retention", "not_disclosed"),
        "large_customer_growth": KPIConfig("large customer growth", "requires_ir_scrape"),
    },
}

DEFAULT_SAAS_KPI_MAPPING = {
    "subscription_revenue_growth": KPIConfig("subscription revenue growth", "requires_ir_scrape"),
    "crpo_growth": KPIConfig("cRPO growth", "requires_ir_scrape"),
    "rpo_growth": KPIConfig("RPO growth", "requires_ir_scrape"),
    "non_gaap_operating_margin": KPIConfig("non-GAAP operating margin", "requires_ir_scrape", "non_gaap_reported"),
    "net_retention_rate": KPIConfig("net retention rate", "not_disclosed"),
    "large_customer_growth": KPIConfig("large customer growth", "requires_ir_scrape"),
}


class IRKPIClient:
    def get_supplement(self, ticker: str, force_refresh: bool = False) -> dict:
        mapping = kpi_mapping_for_ticker(ticker)
        metric_sources = {}
        metric_statuses = {}
        for key, config in mapping.items():
            metric_sources[key] = {"sourceType": config.source_type if config.status == "available" else config.status}
            metric_statuses[key] = {
                "status": config.status,
                "sourceType": config.source_type,
                "label": config.label,
            }
        return {
            "ir_kpi_status": "mapping_ready",
            "irKpiMapping": {key: config.label for key, config in mapping.items()},
            "metric_sources": metric_sources,
            "metric_statuses": metric_statuses,
        }


def kpi_mapping_for_ticker(ticker: str) -> dict[str, KPIConfig]:
    return SAAS_KPI_MAPPING.get(ticker.upper(), DEFAULT_SAAS_KPI_MAPPING)


def parse_ir_kpi_text(ticker: str, text: str) -> dict:
    mapping = kpi_mapping_for_ticker(ticker)
    parsed: dict = {"metric_sources": {}, "metric_statuses": {}}
    for key, config in mapping.items():
        value = _parse_metric_value(key, text)
        if value is None:
            parsed["metric_statuses"][key] = {
                "status": config.status,
                "sourceType": config.source_type,
                "label": config.label,
            }
            continue
        parsed[key] = value
        parsed["metric_sources"][key] = {
            "sourceType": config.source_type,
            "source": "IR text parser",
            "label": config.label,
        }
        parsed["metric_statuses"][key] = {
            "status": "available",
            "sourceType": config.source_type,
            "label": config.label,
        }
    return parsed


def _parse_metric_value(key: str, text: str) -> float | None:
    patterns = {
        "subscription_revenue_growth": (
            r"subscription(?: and support)? revenue[^.\n%]{0,80}(?:growth|grew|increased)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
            r"product revenue[^.\n%]{0,80}(?:growth|grew|increased)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
        ),
        "crpo_growth": (
            r"cRPO[^.\n%]{0,80}(?:growth|grew|increased)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
            r"current remaining performance obligations[^.\n%]{0,80}(?:growth|grew|increased)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
        ),
        "rpo_growth": (
            r"\bRPO[^.\n%]{0,80}(?:growth|grew|increased)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
            r"remaining performance obligations[^.\n%]{0,80}(?:growth|grew|increased)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
        ),
        "non_gaap_operating_margin": (
            r"non-GAAP operating margin[^.\n%]{0,80}?(\d+(?:\.\d+)?)%",
            r"adjusted operating margin[^.\n%]{0,80}?(\d+(?:\.\d+)?)%",
        ),
        "non_gaap_fcf_margin": (
            r"non-GAAP free cash flow margin[^.\n%]{0,80}?(\d+(?:\.\d+)?)%",
        ),
        "net_retention_rate": (
            r"net (?:revenue |dollar )?retention(?: rate)?[^.\n%]{0,80}?(\d+(?:\.\d+)?)%",
            r"DBNRR[^.\n%]{0,80}?(\d+(?:\.\d+)?)%",
        ),
        "large_customer_growth": (
            r"customers? (?:with|over|above)[^.\n%]{0,80}(?:grew|increased|growth)[^.\n%]{0,40}?(\d+(?:\.\d+)?)%",
        ),
    }
    for pattern in patterns.get(key, ()):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1)) / 100
    return None
