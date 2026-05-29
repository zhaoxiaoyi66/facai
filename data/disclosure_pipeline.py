from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from data.calculated_metrics import CalculatedMetric, calculate_metrics
from data.disclosure_store import DisclosureStore
from data.extract_metric_from_text import ExtractedMetric, extractMetricFromText
from data.ir_kpi_scraper import kpi_mapping_for_ticker
from data.metric_dictionary import MetricDefinition, metric_definitions_for_model
from data.metric_source_map import metric_source_definition
from data.metric_variants import extract_saas_metric_variants
from data.sec_client import SECClient, SECFiling, _html_to_text, _links_from_html
from data.sec_supplement import SEC_COMPANYFACTS_URL, extract_sec_hood_metrics, extract_sec_saas_metrics


NOW_IR_SEED_URLS = (
    "https://investors.servicenow.com/news-events/press-releases/default.aspx",
    "https://www.servicenow.com/company/media/press-room.html",
)
HOOD_IR_SEED_URLS = (
    "https://investors.robinhood.com/newsroom/default.aspx",
    "https://investors.robinhood.com/financials/quarterly-results/default.aspx",
)
IR_LINK_KEYWORDS = (
    "financial results",
    "earnings",
    "quarter",
    "fiscal",
    "q1",
    "q2",
    "q3",
    "q4",
    "results",
)
FMP_TRANSCRIPT_ENDPOINT = "https://financialmodelingprep.com/stable/earning-call-transcript"


@dataclass(frozen=True)
class PipelineLog:
    source_type: str
    url: str | None
    status: str
    error_message: str | None = None


class DisclosurePipeline:
    def __init__(self, store: DisclosureStore | None = None, sec_client: SECClient | None = None) -> None:
        self.store = store or DisclosureStore()
        self.sec_client = sec_client or SECClient()

    def run(
        self,
        symbol: str,
        model_type: str = "SAAS_SOFTWARE",
        current_snapshot: dict | None = None,
        current_technicals: dict | None = None,
        price_history: pd.DataFrame | None = None,
        force_refresh: bool = False,
    ) -> dict:
        symbol = symbol.upper().strip()
        definitions = list(metric_definitions_for_model(model_type))
        result = {"symbol": symbol, "modelType": model_type, "saved": [], "logs": [], "missing": [], "notDisclosed": [], "resolutions": []}

        if model_type != "SAAS_SOFTWARE" and not (model_type == "CRYPTO_FINANCIAL_INFRA" and symbol == "HOOD"):
            self._log(result, symbol, "PIPELINE", None, "skipped", "MVP 版本先支持 SAAS_SOFTWARE")
            return result

        self.store.clear_symbol_metrics(symbol)
        self._save_fmp_structured(symbol, current_snapshot or {}, result)
        self._save_calculated_metrics(symbol, current_snapshot or {}, current_technicals or {}, price_history, result)
        cik = self._load_cik(symbol, result, force_refresh=force_refresh)
        if cik:
            self._load_sec_xbrl(symbol, cik, result, force_refresh=force_refresh)
            self._load_sec_filings(symbol, cik, definitions, result, force_refresh=force_refresh)
        self._load_ir_pages(symbol, definitions, result, force_refresh=force_refresh)
        self._load_fmp_transcript(symbol, definitions, result)

        best = self.store.best_metrics(symbol)
        mapping = kpi_mapping_for_ticker(symbol)
        for definition in definitions:
            if definition.metric_key in best or definition.metric_key in {"peg", "forwardRevenueMultiple"}:
                continue
            config = mapping.get(definition.snapshot_key)
            if config and config.status == "not_disclosed":
                result["notDisclosed"].append(definition.metric_key)
                self._save_resolution(
                    result,
                    symbol,
                    definition.metric_key,
                    "not_disclosed",
                    ", ".join(definition.preferred_sources),
                    "Company-specific KPI mapping marks this metric as not disclosed.",
                    "none",
                )
            else:
                result["missing"].append(definition.metric_key)
                self._save_resolution_for_missing(symbol, definition.metric_key, result)
        return result

    def _save_fmp_structured(self, symbol: str, snapshot: dict, result: dict) -> None:
        if not snapshot:
            self._log(result, symbol, "FMP", None, "skipped", "当前页面暂无结构化快照；仍会继续抓 SEC / IR")
            return

        self._log(result, symbol, "FMP", None, "available", "已读取当前页面的 FMP 结构化字段")
        peg = _number(snapshot.get("peg_ratio") or snapshot.get("peg"))
        if peg is not None:
            self._save(
                result,
                symbol,
                "peg",
                peg,
                "x",
                "NTM",
                "FMP",
                None,
                "FMP analyst estimates",
                "PEG from forward PE / expected EPS growth",
                "medium",
            )

        forward_revenue = _number(snapshot.get("forward_revenue_estimate"))
        enterprise_value = _number(snapshot.get("enterprise_value") or snapshot.get("market_cap"))
        if forward_revenue and forward_revenue > 0 and enterprise_value:
            self._save(
                result,
                symbol,
                "forwardRevenueMultiple",
                enterprise_value / forward_revenue,
                "x",
                "NTM",
                "FMP",
                None,
                "FMP analyst estimates",
                "enterprise value / next twelve month revenue estimate",
                "medium",
            )

    def _save_calculated_metrics(
        self,
        symbol: str,
        snapshot: dict,
        technicals: dict,
        price_history: pd.DataFrame | None,
        result: dict,
    ) -> None:
        self._log(result, symbol, "CALCULATED", None, "started", "calculate metrics from existing FMP / price data")
        saved_count = 0
        for metric in calculate_metrics(snapshot, technicals=technicals, price_history=price_history):
            if metric.value is None:
                self._save_resolution(
                    result,
                    symbol,
                    metric.metricKey,
                    "calculation_unavailable",
                    "CALCULATED",
                    metric.reason or "missing inputs",
                    _recommended_action(metric.metricKey),
                )
                continue
            self._save_calculated_metric(result, symbol, metric)
            saved_count += 1
        self._log(result, symbol, "CALCULATED", None, "available", f"{saved_count} calculated metrics saved")

    def _save_calculated_metric(self, result: dict, symbol: str, metric: CalculatedMetric) -> None:
        self._save(
            result,
            symbol,
            metric.metricKey,
            float(metric.value),
            metric.unit,
            metric.period or "latest",
            "CALCULATED",
            None,
            "Calculated from structured FMP / price data",
            metric.formula,
            metric.confidence,
        )

    def _load_cik(self, symbol: str, result: dict, force_refresh: bool) -> str | None:
        try:
            cik = self.sec_client.cik_for_ticker(symbol, force_refresh=force_refresh)
        except Exception as exc:
            self._log(result, symbol, "SEC_SUBMISSIONS", "https://www.sec.gov/files/company_tickers.json", "failed", _short_error(exc))
            return None
        if not cik:
            self._log(result, symbol, "SEC_SUBMISSIONS", "https://www.sec.gov/files/company_tickers.json", "not_found")
            return None
        self._log(result, symbol, "SEC_SUBMISSIONS", "https://www.sec.gov/files/company_tickers.json", "available", f"CIK {cik}")
        return cik

    def _load_sec_xbrl(self, symbol: str, cik: str, result: dict, force_refresh: bool) -> None:
        url = SEC_COMPANYFACTS_URL.format(cik=cik)
        try:
            companyfacts = self.sec_client.companyfacts(cik, force_refresh=force_refresh)
            supplement = extract_sec_saas_metrics(companyfacts)
            if symbol == "HOOD":
                supplement.update(extract_sec_hood_metrics(companyfacts))
        except Exception as exc:
            self._log(result, symbol, "SEC_XBRL", url, "failed", _short_error(exc))
            return

        self._log(result, symbol, "SEC_XBRL", url, "available")
        xbrl_metrics = {
            "sbcRatio": supplement.get("sbc_ratio"),
            "rpoGrowth": supplement.get("rpo_growth"),
        }
        if symbol == "HOOD":
            xbrl_metrics = {"hoodInterestRevenue": supplement.get("hood_interest_revenue")}
        for metric_key, value in xbrl_metrics.items():
            if value is None:
                continue
            unit = "usd" if metric_key.startswith("hood") else "percent"
            source_type = "SEC_10Q" if metric_key.startswith("hood") else "SEC_XBRL"
            source_title = "SEC companyfacts / 10-Q / 10-K" if metric_key.startswith("hood") else "SEC companyfacts"
            self._save(
                result,
                symbol,
                metric_key,
                float(value),
                unit,
                "latest",
                source_type,
                url,
                source_title,
                _xbrl_extracted_text(metric_key, supplement),
                "high",
            )

    def _load_sec_filings(self, symbol: str, cik: str, definitions: list[MetricDefinition], result: dict, force_refresh: bool) -> None:
        try:
            filings = self.sec_client.recent_filings(cik, forms=("8-K", "10-Q", "10-K"), limit=10, force_refresh=force_refresh)
        except Exception as exc:
            self._log(result, symbol, "SEC_SUBMISSIONS", None, "failed", _short_error(exc))
            return

        self._log(result, symbol, "SEC_SUBMISSIONS", None, "available", f"{len(filings)} recent filings")
        for filing in filings:
            if filing.form != "8-K":
                self._log(result, symbol, f"SEC_{filing.form.replace('-', '')}", filing.document_url, "skipped", "标准 10-K/10-Q 字段使用 SEC XBRL，暂不做长文 KPI 抽取")
                continue
            for url, title in self.sec_client.filing_exhibit_urls(filing, force_refresh=force_refresh):
                if url == filing.document_url:
                    self._log(result, symbol, "SEC_8K", url, "skipped", "跳过 8-K 主文，避免把 exhibit 清单或债券条款误判为经营 KPI")
                    continue
                source_type = "SEC_8K"
                try:
                    text = self.sec_client.cached_text(
                        f"sec_doc_{filing.accession_number}_{_slug(url)}",
                        url,
                        ttl_hours=24 * 7,
                        force_refresh=force_refresh,
                    )
                except Exception as exc:
                    self._log(result, symbol, source_type, url, "failed", _short_error(exc))
                    continue
                self._log(result, symbol, source_type, url, "fetched", title)
                self._extract_and_save(
                    result=result,
                    symbol=symbol,
                    definitions=definitions,
                    text=text,
                    source_type=source_type,
                    source_url=url,
                    source_document_title=title,
                    period=filing.report_date or filing.filing_date,
                    confidence="medium",
                    accession_number=filing.accession_number,
                )

    def _load_ir_pages(self, symbol: str, definitions: list[MetricDefinition], result: dict, force_refresh: bool) -> None:
        for seed_url in _ir_seed_urls(symbol):
            try:
                raw_html = self.sec_client.cached_text(
                    f"ir_seed_{symbol}_{_slug(seed_url)}",
                    seed_url,
                    ttl_hours=12,
                    force_refresh=force_refresh,
                    normalize_html=False,
                )
            except Exception as exc:
                self._log(result, symbol, "IR_RELEASE", seed_url, "failed", _short_error(exc))
                continue
            self._log(result, symbol, "IR_RELEASE", seed_url, "fetched", "IR landing page")

            landing_text = _html_to_text(raw_html)
            self._extract_and_save(
                result=result,
                symbol=symbol,
                definitions=definitions,
                text=landing_text,
                source_type="IR_RELEASE",
                source_url=seed_url,
                source_document_title="IR landing page",
                period=_period_from_text(landing_text),
                confidence="medium",
            )

            for url, title in _candidate_ir_links(raw_html, seed_url)[:6]:
                try:
                    text = self.sec_client.cached_text(
                        f"ir_doc_{symbol}_{_slug(url)}",
                        url,
                        ttl_hours=24,
                        force_refresh=force_refresh,
                    )
                except Exception as exc:
                    self._log(result, symbol, "IR_RELEASE", url, "failed", _short_error(exc))
                    continue
                self._log(result, symbol, "IR_RELEASE", url, "fetched", title)
                self._extract_and_save(
                    result=result,
                    symbol=symbol,
                    definitions=definitions,
                    text=text,
                    source_type="IR_RELEASE",
                    source_url=url,
                    source_document_title=title or "IR release",
                    period=_period_from_text(text),
                    confidence="medium",
                )

    def _load_fmp_transcript(self, symbol: str, definitions: list[MetricDefinition], result: dict) -> None:
        api_key = _get_secret("FMP_API_KEY")
        if not api_key:
            self._log(result, symbol, "FMP_TRANSCRIPT", FMP_TRANSCRIPT_ENDPOINT, "skipped", "缺少 FMP_API_KEY")
            return

        year = datetime.now(timezone.utc).year
        for quarter in range(4, 0, -1):
            params = urlencode({"symbol": symbol, "year": year, "quarter": quarter, "apikey": api_key})
            url = f"{FMP_TRANSCRIPT_ENDPOINT}?{params}"
            try:
                request = Request(url, headers={"User-Agent": "ZHX-Research/1.0"})
                with urlopen(request, timeout=10) as response:
                    payload = response.read().decode("utf-8", errors="replace")
                data = json.loads(payload)
            except Exception as exc:
                self._log(result, symbol, "FMP_TRANSCRIPT", FMP_TRANSCRIPT_ENDPOINT, "failed", _short_error(exc))
                return

            text = _transcript_text(data)
            if not text:
                continue
            self._log(result, symbol, "FMP_TRANSCRIPT", FMP_TRANSCRIPT_ENDPOINT, "fetched", f"{year} Q{quarter}")
            self._extract_and_save(
                result=result,
                symbol=symbol,
                definitions=definitions,
                text=text,
                source_type="FMP_TRANSCRIPT",
                source_url=FMP_TRANSCRIPT_ENDPOINT,
                source_document_title=f"FMP earnings transcript {year} Q{quarter}",
                period=f"{year} Q{quarter}",
                confidence="low",
            )
            return
            # Keep transcript use conservative: newest available quarter only.
        self._log(result, symbol, "FMP_TRANSCRIPT", FMP_TRANSCRIPT_ENDPOINT, "not_found", f"{year} transcripts not available")

    def _extract_and_save(
        self,
        result: dict,
        symbol: str,
        definitions: list[MetricDefinition],
        text: str,
        source_type: str,
        source_url: str | None,
        source_document_title: str | None,
        period: str | None,
        confidence: str,
        accession_number: str | None = None,
    ) -> None:
        definition_keys = {definition.metric_key for definition in definitions}
        variant_keys = {
            "cRpoGrowthReported",
            "cRpoGrowthConstantCurrency",
            "rpoGrowthReported",
            "rpoGrowthConstantCurrency",
            "subscriptionRevenueGrowthReported",
            "subscriptionRevenueGrowthConstantCurrency",
            "operatingCashFlowMargin",
            "nonGaapFcfMargin",
        }
        for extracted in extract_saas_metric_variants(text, confidence=confidence):
            if extracted.metric_key not in definition_keys:
                continue
            self._save_extracted(
                result,
                symbol,
                extracted,
                period=period,
                source_type=source_type,
                source_url=source_url,
                source_document_title=source_document_title,
                accession_number=accession_number,
            )

        for definition in definitions:
            if definition.metric_key in {"peg", "forwardRevenueMultiple", "sbcRatio"} and source_type not in {"FMP", "SEC_XBRL"}:
                continue
            if definition.metric_key in variant_keys:
                continue
            if definition.metric_key in {"cRpoGrowth", "rpoGrowth", "subscriptionRevenueGrowth", "fcfMargin"} and source_type in {
                "IR_RELEASE",
                "SEC_8K",
                "IR_PRESENTATION",
                "FMP_TRANSCRIPT",
            }:
                continue
            extracted = extractMetricFromText(text, definition, confidence=confidence)
            if not extracted:
                continue
            self._save_extracted(
                result,
                symbol,
                extracted,
                period=period,
                source_type=source_type,
                source_url=source_url,
                source_document_title=source_document_title,
                accession_number=accession_number,
            )

    def _save_extracted(
        self,
        result: dict,
        symbol: str,
        extracted: ExtractedMetric,
        period: str | None,
        source_type: str,
        source_url: str | None,
        source_document_title: str | None,
        accession_number: str | None = None,
    ) -> None:
        self._save(
            result,
            symbol,
            extracted.metric_key,
            extracted.value,
            extracted.unit,
            _metric_period_from_extraction(period, source_document_title, extracted.extracted_text),
            source_type,
            source_url,
            source_document_title,
            extracted.extracted_text,
            extracted.confidence,
            accession_number=accession_number,
            metric_variant=extracted.metric_variant,
            target_basis=extracted.target_basis,
        )

    def _save(
        self,
        result: dict,
        symbol: str,
        metric_key: str,
        value: float,
        unit: str | None,
        period: str | None,
        source_type: str,
        source_url: str | None,
        source_document_title: str | None,
        extracted_text: str | None,
        confidence: str,
        accession_number: str | None = None,
        metric_variant: str | None = None,
        target_basis: str | None = None,
    ) -> None:
        self.store.save_metric(
            symbol=symbol,
            metric_key=metric_key,
            value=value,
            unit=unit,
            period=period,
            source_type=source_type,
            source_url=source_url,
            source_document_title=source_document_title,
            extracted_text=extracted_text,
            confidence=confidence,
            accession_number=accession_number,
            metric_variant=metric_variant,
            target_basis=target_basis,
        )
        result["saved"].append(
            {
                "metricKey": metric_key,
                "metricVariant": metric_variant,
                "targetBasis": target_basis,
                "value": value,
                "unit": unit,
                "period": period,
                "sourceType": source_type,
                "sourceUrl": source_url,
                "sourceDocumentTitle": source_document_title,
                "extractedText": extracted_text,
                "evidenceText": extracted_text,
                "extractionRule": "disclosure_pipeline_extraction",
                "confidence": confidence,
            }
        )

    def _save_resolution_for_missing(self, symbol: str, metric_key: str, result: dict) -> None:
        definition = metric_source_definition(metric_key)
        if definition:
            source_tried = ", ".join(definition.preferredSources)
            recommended_action = "manual_override_required" if "MANUAL" in definition.fallbackSources else _recommended_action(metric_key)
            status = "manual_override_required" if recommended_action == "manual_override_required" else "missing"
            reason = f"Automatic sources did not return {definition.displayName}."
        else:
            source_tried = None
            recommended_action = "manual_override_required"
            status = "manual_override_required"
            reason = "Automatic sources did not return this metric."
        self._save_resolution(result, symbol, metric_key, status, source_tried, reason, recommended_action)

    def _save_resolution(
        self,
        result: dict,
        symbol: str,
        metric_key: str,
        status: str,
        source_tried: str | None,
        reason: str | None,
        recommended_action: str | None,
    ) -> None:
        self.store.save_resolution(symbol, metric_key, status, source_tried, reason, recommended_action)
        result["resolutions"].append(
            {
                "metricKey": metric_key,
                "status": status,
                "sourceTried": source_tried,
                "reason": reason,
                "recommendedAction": recommended_action,
            }
        )

    def _log(self, result: dict, symbol: str, source_type: str, url: str | None, status: str, error_message: str | None = None) -> None:
        self.store.log_fetch(symbol, source_type, url, status, error_message)
        result["logs"].append(
            {
                "sourceType": source_type,
                "url": url,
                "status": status,
                "errorMessage": error_message,
            }
        )


def run_disclosure_pipeline(
    symbol: str,
    model_type: str = "SAAS_SOFTWARE",
    current_snapshot: dict | None = None,
    current_technicals: dict | None = None,
    price_history: pd.DataFrame | None = None,
    force_refresh: bool = False,
) -> dict:
    return DisclosurePipeline().run(
        symbol,
        model_type=model_type,
        current_snapshot=current_snapshot,
        current_technicals=current_technicals,
        price_history=price_history,
        force_refresh=force_refresh,
    )


def _candidate_ir_links(raw_html: str, base_url: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for url, title in _links_from_html(raw_html, base_url):
        haystack = f"{url} {title}".lower()
        if any(keyword in haystack for keyword in IR_LINK_KEYWORDS):
            candidates.append((url, title))
    return _dedupe_urls(candidates)


def _ir_seed_urls(symbol: str) -> tuple[str, ...]:
    if symbol.upper() == "NOW":
        return NOW_IR_SEED_URLS
    if symbol.upper() == "HOOD":
        return HOOD_IR_SEED_URLS
    return ()


def _xbrl_extracted_text(metric_key: str, supplement: dict) -> str:
    if metric_key == "sbcRatio":
        return "SBC / revenue calculated from SEC companyfacts share-based compensation and revenue."
    if metric_key == "rpoGrowth":
        return "RPO growth calculated from comparable SEC companyfacts RPO values."
    if metric_key == "hoodInterestRevenue":
        return "Interest revenue mapped from SEC companyfacts net interest income / operating interest income."
    return "SEC companyfacts structured value."


def _period_from_text(text: str) -> str | None:
    cleaned = str(text or "")
    quarter_patterns = [
        r"\bQ([1-4])[\s_-]*(20\d{2})(?=\D|$)",
        r"\b(20\d{2})[\s_-]*Q([1-4])\b",
    ]
    for pattern in quarter_patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        groups = match.groups()
        if groups[0].isdigit() and len(groups[0]) == 4:
            return f"{groups[0]} Q{groups[1]}"
        return f"{groups[1]} Q{groups[0]}"

    word_match = re.search(r"\b(first|second|third|fourth)\s+quarter[^.]{0,40}?\b(20\d{2})\b", cleaned, flags=re.IGNORECASE)
    if word_match:
        quarter = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}[word_match.group(1).lower()]
        return f"{word_match.group(2)} {quarter}"

    ended_match = re.search(
        r"\b(?:three months ended|quarter ended)[^.]{0,80}?\b"
        r"(march|june|september|december)\s+\d{1,2},\s*(20\d{2})\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if ended_match:
        quarter = {"march": "Q1", "june": "Q2", "september": "Q3", "december": "Q4"}[ended_match.group(1).lower()]
        return f"{ended_match.group(2)} {quarter}"

    fiscal_match = re.search(r"\bfiscal\s+(20\d{2})\b|\bFY\s*(20\d{2})\b", cleaned, flags=re.IGNORECASE)
    if fiscal_match:
        return f"FY{fiscal_match.group(1) or fiscal_match.group(2)}"
    return None


def _metric_period_from_extraction(default_period: str | None, source_document_title: str | None, extracted_text: str | None) -> str | None:
    return _period_from_text(str(source_document_title or "")) or _period_from_text(str(extracted_text or "")) or default_period


def _transcript_text(data) -> str:
    if isinstance(data, list):
        chunks = []
        for row in data:
            if isinstance(row, dict):
                chunks.extend(str(value) for value in row.values() if isinstance(value, str))
        return " ".join(chunks)
    if isinstance(data, dict):
        return " ".join(str(value) for value in data.values() if isinstance(value, str))
    return ""


def _number(value) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_urls(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for url, title in rows:
        if url in seen:
            continue
        seen.add(url)
        deduped.append((url, title))
    return deduped


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value)[-120:]


def _short_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return text[:240]


def _recommended_action(metric_key: str) -> str:
    definition = metric_source_definition(metric_key)
    if definition and definition.missingImpact == "TECHNICAL_ONLY":
        return "auto_calculate"
    if definition and definition.missingImpact == "VALUATION_ONLY":
        return "can_ignore"
    return "manual_override_required"


def _get_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value.strip()
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return None
    with open(env_path, "r", encoding="utf-8-sig") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            if key.strip().lstrip("\ufeff") == name:
                return raw_value.strip().strip('"').strip("'")
    return None
