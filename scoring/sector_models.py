from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from data.calculated_metrics import apply_calculated_metrics_to_snapshot
from scoring.metric_sources import fcf_margin_metric, metric_participates_in_score
from scoring.overheat import OverheatResult, calculate_overheat_score
from scoring.risk_flags import RiskFlag


ModelType = str
MetricType = str
ResolutionStatus = str


symbolModelOverrides: dict[str, ModelType] = {
    # SaaS / Software
    "NOW": "SAAS_SOFTWARE",
    "ADBE": "SAAS_SOFTWARE",
    "CRM": "SAAS_SOFTWARE",
    "SNOW": "SAAS_SOFTWARE",
    "DDOG": "SAAS_SOFTWARE",
    "MDB": "SAAS_SOFTWARE",
    "NET": "SAAS_SOFTWARE",
    "CRWD": "SAAS_SOFTWARE",
    "PLTR": "SAAS_SOFTWARE",
    "ORCL": "SAAS_SOFTWARE",
    # Mega-cap platforms
    "MSFT": "MEGA_CAP_PLATFORM",
    "GOOGL": "MEGA_CAP_PLATFORM",
    "GOOG": "MEGA_CAP_PLATFORM",
    "META": "MEGA_CAP_PLATFORM",
    "AMZN": "MEGA_CAP_PLATFORM",
    # Semiconductors
    "NVDA": "SEMICONDUCTOR",
    "AVGO": "SEMICONDUCTOR",
    "AMD": "SEMICONDUCTOR",
    "MRVL": "SEMICONDUCTOR",
    "MU": "SEMICONDUCTOR_CYCLICAL",
    "COHR": "SEMICONDUCTOR_CYCLICAL",
    "WDC": "SEMICONDUCTOR_CYCLICAL",
    "STX": "SEMICONDUCTOR_CYCLICAL",
    # Networking hardware
    "ANET": "NETWORKING_HARDWARE",
    # AI infrastructure / neo-cloud
    "CRWV": "AI_INFRA_HIGH_RISK",
    "NBIS": "AI_INFRA_HIGH_RISK",
    "IREN": "AI_INFRA_HIGH_RISK",
    # Power / independent power producers
    "VST": "POWER_GENERATION",
    "CEG": "POWER_GENERATION",
    "TLN": "POWER_GENERATION",
    "NRG": "POWER_GENERATION",
    # Regulated utilities
    "NEE": "REGULATED_UTILITIES",
    "DUK": "REGULATED_UTILITIES",
    "SO": "REGULATED_UTILITIES",
    "AEP": "REGULATED_UTILITIES",
    # Medical devices
    "ISRG": "MEDICAL_DEVICE",
    "SYK": "MEDICAL_DEVICE",
    "BSX": "MEDICAL_DEVICE",
    "ABT": "MEDICAL_DEVICE",
    "MDT": "MEDICAL_DEVICE",
    # Pharma / biotech
    "NVO": "PHARMA",
    "LLY": "PHARMA",
    "MRK": "PHARMA",
    "PFE": "PHARMA",
    "ABBV": "PHARMA",
    # Crypto / brokerage
    "COIN": "CRYPTO_FINANCIAL_INFRA",
    "HOOD": "CRYPTO_FINANCIAL_INFRA",
    "MSTR": "CRYPTO_FINANCIAL_INFRA",
    # Banks / financials
    "JPM": "BANK_FINANCIAL",
    "BAC": "BANK_FINANCIAL",
    "C": "BANK_FINANCIAL",
    "GS": "BANK_FINANCIAL",
    "MS": "BANK_FINANCIAL",
    "SCHW": "BANK_FINANCIAL",
    # REITs
    "PLD": "REIT_REAL_ESTATE",
    "AMT": "REIT_REAL_ESTATE",
    "EQIX": "REIT_REAL_ESTATE",
    "O": "REIT_REAL_ESTATE",
    "PSA": "REIT_REAL_ESTATE",
    # Auto / consumer hardware
    "TSLA": "AUTO_HARDWARE",
    "RIVN": "AUTO_HARDWARE",
    "LCID": "AUTO_HARDWARE",
    # Consumer internet / ecommerce
    "CPNG": "CONSUMER_INTERNET_ECOMMERCE",
    "MELI": "CONSUMER_INTERNET_ECOMMERCE",
    "SE": "CONSUMER_INTERNET_ECOMMERCE",
    "PDD": "CONSUMER_INTERNET_ECOMMERCE",
    # Industrial capex
    "ETN": "INDUSTRIAL_CAPEX",
    "VRT": "INDUSTRIAL_CAPEX",
    "PWR": "INDUSTRIAL_CAPEX",
    "CAT": "INDUSTRIAL_CAPEX",
    "DE": "INDUSTRIAL_CAPEX",
    "HON": "INDUSTRIAL_CAPEX",
    "GE": "INDUSTRIAL_CAPEX",
    # Energy commodity
    "XOM": "ENERGY_COMMODITY",
    "CVX": "ENERGY_COMMODITY",
    "OXY": "ENERGY_COMMODITY",
    "SLB": "ENERGY_COMMODITY",
    "EOG": "ENERGY_COMMODITY",
}


INDUSTRY_KEYWORDS: list[tuple[ModelType, tuple[str, ...]]] = [
    ("SAAS_SOFTWARE", ("software", "application software", "infrastructure software", "cloud software", "saas")),
    ("SEMICONDUCTOR_CYCLICAL", ("memory", "storage", "optical", "disk drive")),
    ("SEMICONDUCTOR", ("semiconductor", "chip", "integrated circuit")),
    ("AI_INFRA_HIGH_RISK", ("data center", "ai infrastructure", "bitcoin mining", "high performance computing")),
    ("POWER_GENERATION", ("independent power", "power generation", "merchant power", "energy infrastructure")),
    ("REGULATED_UTILITIES", ("electric utilities", "electric utility", "regulated electric", "multi-utilities", "utilities regulated")),
    ("MEDICAL_DEVICE", ("medical device", "medical instruments", "surgical", "health care equipment")),
    ("PHARMA", ("pharmaceutical", "biotechnology", "drug manufacturers", "therapeutics")),
    ("CRYPTO_FINANCIAL_INFRA", ("capital markets", "brokerage", "crypto", "financial data")),
    ("BANK_FINANCIAL", ("bank", "banks", "diversified banks", "investment banking", "asset management")),
    ("REIT_REAL_ESTATE", ("reit", "real estate investment trust", "real estate services")),
    ("AUTO_HARDWARE", ("auto manufacturers", "automobiles", "consumer electronics", "hardware")),
    ("CONSUMER_INTERNET_ECOMMERCE", ("internet retail", "e-commerce", "marketplace", "online retail")),
    ("INDUSTRIAL_CAPEX", ("electrical equipment", "industrial", "machinery", "engineering", "construction")),
    ("ENERGY_COMMODITY", ("oil", "gas", "exploration", "drilling", "energy equipment")),
]


SECTOR_KEYWORDS: list[tuple[ModelType, tuple[str, ...]]] = [
    ("REGULATED_UTILITIES", ("utilities",)),
    ("BANK_FINANCIAL", ("financial services", "financial")),
    ("REIT_REAL_ESTATE", ("real estate",)),
    ("PHARMA", ("healthcare", "health care")),
    ("INDUSTRIAL_CAPEX", ("industrials",)),
    ("ENERGY_COMMODITY", ("energy",)),
    ("CONSUMER_INTERNET_ECOMMERCE", ("consumer cyclical", "communication services")),
]


@dataclass(frozen=True)
class Factor:
    name: str
    weight: float
    scorer: Callable[["ScoreContext"], float | None]


@dataclass(frozen=True)
class ModelProfile:
    model_type: ModelType
    quality: tuple[Factor, ...]
    entry: tuple[Factor, ...]
    risk: tuple[Factor, ...]
    required_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    data_threshold: float = 45.0


@dataclass(frozen=True)
class AxisScore:
    score: float
    available_weight: float
    total_weight: float
    missing: list[str]
    positives: list[str]
    risks: list[str]

    @property
    def availability_pct(self) -> float:
        if self.total_weight <= 0:
            return 0.0
        return self.available_weight / self.total_weight * 100


@dataclass(frozen=True)
class SectorScore:
    model_type: ModelType
    quality_score: float
    entry_score: float
    risk_score: float
    quality_rating: str
    entry_rating: str
    risk_rating: str
    valuation_status: str
    action: str
    value_zone: str
    key_positives: list[str]
    key_risks: list[str]
    missing_data: list[str]
    risk_flags: list[RiskFlag]
    data_quality_pct: float
    data_insufficient: bool
    overheat_score: float
    overheat_status: str
    overheat_action: str
    overheat_recommendation: str
    overheat_reasons: list[str]
    valuation_module_score: float = 0.0
    quality_module_score: float = 0.0
    balance_sheet_module_score: float = 0.0
    technical_module_score: float = 0.0
    investment_risk_score: float = 0.0
    fcf_margin_source_type: str = "missing"
    fcf_margin_note: str = ""
    max_suggested_position_percent: float = 0.0
    max_portfolio_weight_percent: float = 0.0
    current_add_limit_percent: float = 0.0
    data_confidence: str = "high"
    proxy_confidence: str = "high"
    missing_industry_metrics: list[str] | None = None
    proxy_metrics_used: list[str] | None = None
    missing_metric_impacts: list[dict[str, str]] | None = None
    missing_data_explanation: list[str] | None = None
    rating_cap: str | None = None
    metric_resolution_statuses: list[dict[str, object]] | None = None
    missing_data_summary: dict[str, object] | None = None
    human_readable_summary: dict[str, str] | None = None
    active_risk_drivers: list[str] | None = None

    @property
    def modelType(self) -> str:
        return self.model_type

    @property
    def qualityRating(self) -> str:
        return self.quality_rating

    @property
    def entryRating(self) -> str:
        return self.entry_rating

    @property
    def riskRating(self) -> str:
        return self.risk_rating

    @property
    def valuationStatus(self) -> str:
        return self.valuation_status

    @property
    def maxSuggestedPositionPercent(self) -> float:
        return self.max_suggested_position_percent

    @property
    def maxPortfolioWeightPercent(self) -> float:
        return self.max_portfolio_weight_percent

    @property
    def currentAddLimitPercent(self) -> float:
        return self.current_add_limit_percent

    @property
    def dataConfidence(self) -> str:
        return self.data_confidence

    @property
    def proxyConfidence(self) -> str:
        return self.proxy_confidence

    @property
    def keyPositiveDrivers(self) -> list[str]:
        return self.key_positives

    @property
    def keyNegativeDrivers(self) -> list[str]:
        return self.key_risks

    @property
    def missingIndustryMetrics(self) -> list[str]:
        return self.missing_industry_metrics or []

    @property
    def proxyMetricsUsed(self) -> list[str]:
        return self.proxy_metrics_used or []

    @property
    def missingMetricImpact(self) -> list[dict[str, str]]:
        return self.missing_metric_impacts or []

    @property
    def missingDataExplanation(self) -> list[str]:
        return self.missing_data_explanation or []

    @property
    def ratingCap(self) -> str | None:
        return self.rating_cap

    @property
    def metricResolutionStatus(self) -> list[dict[str, object]]:
        return self.metric_resolution_statuses or []

    @property
    def missingDataSummary(self) -> dict[str, object]:
        return self.missing_data_summary or {}

    @property
    def humanReadableSummary(self) -> dict[str, str]:
        return self.human_readable_summary or {}

    @property
    def activeRiskDrivers(self) -> list[str]:
        return self.active_risk_drivers or []


@dataclass(frozen=True)
class ProxyAssessment:
    data_confidence: str
    proxy_confidence: str
    missing_industry_metrics: list[str]
    proxy_metrics_used: list[str]


@dataclass(frozen=True)
class MetricResolution:
    metricKey: str
    displayName: str
    metricType: MetricType
    resolutionStatus: ResolutionStatus
    value: float | None = None
    unit: str | None = None
    sourceType: str = "missing"
    confidence: str = "low"
    affects: list[str] | None = None
    isBlocking: bool = False
    ratingCapImpact: str = "none"
    explanation: str = ""
    recommendedAction: str = ""
    sourceMetricsUsed: list[str] | None = None
    priority: str = "low"
    missingResolutionRoute: str = ""
    defaultReviewQueue: bool = False
    reviewPriority: str = "low"

    def to_dict(self) -> dict[str, object]:
        return {
            "metricKey": self.metricKey,
            "displayName": self.displayName,
            "metricType": self.metricType,
            "resolutionStatus": self.resolutionStatus,
            "value": self.value,
            "unit": self.unit,
            "sourceType": self.sourceType,
            "confidence": self.confidence,
            "affects": self.affects or [],
            "isBlocking": self.isBlocking,
            "ratingCapImpact": self.ratingCapImpact,
            "explanation": self.explanation,
            "recommendedAction": self.recommendedAction,
            "sourceMetricsUsed": self.sourceMetricsUsed or [],
            "priority": self.priority,
            "missingResolutionRoute": self.missingResolutionRoute,
            "defaultReviewQueue": self.defaultReviewQueue,
            "reviewPriority": self.reviewPriority,
        }


@dataclass(frozen=True)
class ScoreContext:
    snapshot: dict
    technicals: dict
    model_type: ModelType


def classifyStockModel(stock: dict) -> ModelType:
    symbol = str(stock.get("symbol") or stock.get("ticker") or "").upper()
    manual_model = stock.get("modelType") or stock.get("model_type")
    if manual_model:
        return str(manual_model).upper()
    if symbol in symbolModelOverrides:
        return symbolModelOverrides[symbol]

    industry = _normalize(stock.get("industry"))
    for model_type, keywords in INDUSTRY_KEYWORDS:
        if any(keyword in industry for keyword in keywords):
            return model_type

    sector = _normalize(stock.get("sector"))
    for model_type, keywords in SECTOR_KEYWORDS:
        if any(keyword in sector for keyword in keywords):
            return model_type

    return "GENERIC"


def classify_stock_model(stock: dict) -> ModelType:
    return classifyStockModel(stock)


def score_stock_by_model(snapshot: dict, technicals: dict) -> SectorScore:
    snapshot = apply_calculated_metrics_to_snapshot(snapshot)
    model_type = classifyStockModel(snapshot)
    profile = MODEL_PROFILES.get(model_type, MODEL_PROFILES["GENERIC"])
    context = ScoreContext(snapshot=snapshot, technicals=technicals, model_type=model_type)

    if model_type == "SAAS_SOFTWARE":
        return _score_saas_software(context, profile)

    quality = _score_axis(profile.quality, context, risk_axis=False)
    entry = _score_axis(profile.entry, context, risk_axis=False)
    risk = _score_axis(profile.risk, context, risk_axis=True)
    required_missing = _missing_required_groups(context, profile.required_groups)
    raw_missing = _dedupe([*quality.missing, *entry.missing, *risk.missing, *required_missing])
    raw_missing = _filter_inactive_trigger_missing(raw_missing, context)
    missing_metric_impacts = _missing_metric_impacts(raw_missing, context.model_type)
    metric_resolution_statuses = _metric_resolution_statuses(context, missing_metric_impacts)
    missing = _fundamental_missing_data(raw_missing, missing_metric_impacts, metric_resolution_statuses)
    data_quality_pct = _core_data_quality_pct(quality, entry, risk)
    data_insufficient = _is_data_insufficient(profile, quality, entry, required_missing, data_quality_pct)

    quality_score = _apply_special_quality_rules(model_type, quality.score, context, data_insufficient)
    quality_score = _apply_missing_rating_cap(quality_score, missing_metric_impacts)
    entry_score = _apply_special_entry_rules(model_type, entry.score, risk.score, context)
    risk_score = _apply_special_risk_rules(model_type, risk.score, context)
    rating_cap = _rating_cap_from_missing_impacts(missing_metric_impacts)
    quality_rating = _quality_entry_rating(quality_score, "quality", data_insufficient, model_type)
    entry_rating = _quality_entry_rating(entry_score, "entry", data_insufficient)
    risk_rating = _risk_rating(risk_score, data_insufficient)
    valuation_status = _valuation_status(model_type, entry_score, risk_score, context, data_insufficient)
    overheat = calculate_overheat_score(
        snapshot,
        technicals,
        valuation_status=valuation_status,
        model_type=model_type,
        quality_rating=quality_rating,
    )
    flags = _risk_flags(model_type, risk_score, context, risk.risks)
    positives = _dedupe([*quality.positives, *entry.positives])
    key_risks = _dedupe([*risk.risks, *quality.risks, *entry.risks])
    proxy_assessment = _proxy_assessment(
        context,
        data_quality_pct,
        data_insufficient,
        missing_metric_impacts,
        metric_resolution_statuses,
    )
    quality_score, risk_score = _apply_confidence_score_caps(quality_score, risk_score, proxy_assessment.data_confidence)
    quality_rating = _quality_entry_rating(quality_score, "quality", data_insufficient, model_type)
    risk_rating = _risk_rating(risk_score, data_insufficient)
    action = _final_action(quality_score, entry_score, risk_score, valuation_status, context, data_insufficient, overheat)
    action = _apply_confidence_action(action, proxy_assessment.data_confidence)
    key_risks = _postprocess_key_risks(model_type, key_risks, proxy_assessment)
    max_portfolio_weight = _max_portfolio_weight_percent(quality_score, risk_score, data_insufficient, proxy_assessment.data_confidence)
    current_add_limit = _max_suggested_position_percent(quality_score, risk_score, action, data_insufficient)
    current_add_limit = _apply_missing_position_adjustment(current_add_limit, missing_metric_impacts)
    current_add_limit = _apply_confidence_position_cap(current_add_limit, proxy_assessment.data_confidence, action)
    human_summary = _human_readable_summary(context, quality_rating, entry_rating, risk_rating, valuation_status, action)
    active_risks = _active_risk_drivers(context)
    missing_data_summary = _missing_data_summary(metric_resolution_statuses)

    return SectorScore(
        model_type=model_type,
        quality_score=round(quality_score, 1),
        entry_score=round(entry_score, 1),
        risk_score=round(risk_score, 1),
        quality_rating=quality_rating,
        entry_rating=entry_rating,
        risk_rating=risk_rating,
        valuation_status=valuation_status,
        action=action,
        value_zone=valuation_status,
        key_positives=positives[:6],
        key_risks=key_risks[:8],
        missing_data=missing,
        risk_flags=flags,
        data_quality_pct=data_quality_pct,
        data_insufficient=data_insufficient,
        overheat_score=overheat.score,
        overheat_status=overheat.status,
        overheat_action=overheat.action,
        overheat_recommendation=overheat.recommendation,
        overheat_reasons=overheat.reasons,
        max_suggested_position_percent=current_add_limit,
        max_portfolio_weight_percent=max_portfolio_weight,
        current_add_limit_percent=current_add_limit,
        data_confidence=proxy_assessment.data_confidence,
        proxy_confidence=proxy_assessment.proxy_confidence,
        missing_industry_metrics=proxy_assessment.missing_industry_metrics,
        proxy_metrics_used=proxy_assessment.proxy_metrics_used,
        missing_metric_impacts=missing_metric_impacts,
        missing_data_explanation=_missing_data_explanations(missing_metric_impacts),
        rating_cap=rating_cap,
        metric_resolution_statuses=metric_resolution_statuses,
        missing_data_summary=missing_data_summary,
        human_readable_summary=human_summary,
        active_risk_drivers=active_risks,
    )


def _score_saas_software(context: ScoreContext, profile: ModelProfile) -> SectorScore:
    valuation = _score_axis(SAAS_VALUATION, context, risk_axis=False)
    quality = _score_axis(SAAS_QUALITY, context, risk_axis=False)
    balance_sheet = _score_axis(SAAS_BALANCE_SHEET, context, risk_axis=False)
    technical_setup = _score_axis(SAAS_TECHNICAL, context, risk_axis=False)
    investment_risk = _score_axis(SAAS_INVESTMENT_RISK, context, risk_axis=True)

    required_missing = _missing_required_groups(context, profile.required_groups)
    raw_missing = _dedupe(
        [
            *quality.missing,
            *valuation.missing,
            *balance_sheet.missing,
            *technical_setup.missing,
            *investment_risk.missing,
            *required_missing,
        ]
    )
    raw_missing = _filter_inactive_trigger_missing(raw_missing, context)
    fcf_metric = fcf_margin_metric(context.snapshot)
    if fcf_metric.sourceType == "derivedFromMarket":
        raw_missing = _dedupe([*raw_missing, "FCF Margin reported/calculated"])
    missing_metric_impacts = _missing_metric_impacts(raw_missing, context.model_type)
    metric_resolution_statuses = _metric_resolution_statuses(context, missing_metric_impacts)
    missing = _fundamental_missing_data(raw_missing, missing_metric_impacts, metric_resolution_statuses)

    data_quality_pct = round(
        quality.availability_pct * 0.40
        + valuation.availability_pct * 0.25
        + balance_sheet.availability_pct * 0.15
        + technical_setup.availability_pct * 0.20,
        1,
    )
    data_insufficient = bool(required_missing) or data_quality_pct < profile.data_threshold
    if _has_saas_foundation_data(context):
        data_insufficient = False

    quality_score = _apply_saas_quality_constraints(quality.score, context, quality.missing, fcf_metric.sourceType)
    quality_score = _apply_missing_rating_cap(quality_score, missing_metric_impacts)
    growth_component = growth_deceleration_entry(context)
    entry_score = valuation.score * 0.55 + technical_setup.score * 0.30 + (growth_component if growth_component is not None else 50) * 0.15
    entry_score = _apply_special_entry_rules(context.model_type, entry_score, investment_risk.score, context)
    risk_score = _apply_saas_risk_constraints(investment_risk.score, context)

    if data_insufficient:
        quality_score = min(quality_score, 54)
        entry_score = min(entry_score, 54)

    rating_cap = _rating_cap_from_missing_impacts(missing_metric_impacts)
    quality_rating = _quality_entry_rating(quality_score, "quality", data_insufficient, context.model_type)
    entry_rating = _quality_entry_rating(entry_score, "entry", data_insufficient)
    risk_rating = _risk_rating(risk_score, data_insufficient)
    valuation_status = _valuation_status(context.model_type, entry_score, risk_score, context, data_insufficient)
    overheat = calculate_overheat_score(
        context.snapshot,
        context.technicals,
        valuation_status=valuation_status,
        model_type=context.model_type,
        quality_rating=quality_rating,
    )

    positives = _dedupe([*quality.positives, *valuation.positives, *balance_sheet.positives, *technical_setup.positives])
    if _number(context.technicals.get("drawdown_from_high_pct")) is not None and _number(context.technicals.get("drawdown_from_high_pct")) <= -40:
        positives = _dedupe(["距高点回撤较深", *positives])
    key_risks = _dedupe([*investment_risk.risks, *quality.risks, *valuation.risks, *technical_setup.risks])
    if fcf_metric.sourceType == "derivedFromMarket":
        key_risks = _dedupe([*key_risks, "FCF Margin is market-derived and excluded from quality score"])
    proxy_assessment = _proxy_assessment(
        context,
        data_quality_pct,
        data_insufficient,
        missing_metric_impacts,
        metric_resolution_statuses,
    )
    quality_score, risk_score = _apply_confidence_score_caps(quality_score, risk_score, proxy_assessment.data_confidence)
    quality_rating = _quality_entry_rating(quality_score, "quality", data_insufficient, context.model_type)
    risk_rating = _risk_rating(risk_score, data_insufficient)
    action = _final_action(quality_score, entry_score, risk_score, valuation_status, context, data_insufficient, overheat)
    action = _apply_confidence_action(action, proxy_assessment.data_confidence)
    key_risks = _postprocess_key_risks(context.model_type, key_risks, proxy_assessment)
    max_portfolio_weight = _max_portfolio_weight_percent(quality_score, risk_score, data_insufficient, proxy_assessment.data_confidence)
    current_add_limit = _max_suggested_position_percent(quality_score, risk_score, action, data_insufficient)
    current_add_limit = _apply_missing_position_adjustment(current_add_limit, missing_metric_impacts)
    current_add_limit = _apply_confidence_position_cap(current_add_limit, proxy_assessment.data_confidence, action)
    human_summary = _human_readable_summary(context, quality_rating, entry_rating, risk_rating, valuation_status, action)
    active_risks = _active_risk_drivers(context)
    missing_data_summary = _missing_data_summary(metric_resolution_statuses)

    return SectorScore(
        model_type=context.model_type,
        quality_score=round(_clamp(quality_score), 1),
        entry_score=round(_clamp(entry_score), 1),
        risk_score=round(_clamp(risk_score), 1),
        quality_rating=quality_rating,
        entry_rating=entry_rating,
        risk_rating=risk_rating,
        valuation_status=valuation_status,
        action=action,
        value_zone=valuation_status,
        key_positives=positives[:6],
        key_risks=key_risks[:8],
        missing_data=missing,
        risk_flags=_risk_flags(context.model_type, risk_score, context, investment_risk.risks),
        data_quality_pct=data_quality_pct,
        data_insufficient=data_insufficient,
        overheat_score=overheat.score,
        overheat_status=overheat.status,
        overheat_action=overheat.action,
        overheat_recommendation=overheat.recommendation,
        overheat_reasons=overheat.reasons,
        valuation_module_score=round(valuation.score, 1),
        quality_module_score=round(quality_score, 1),
        balance_sheet_module_score=round(balance_sheet.score, 1),
        technical_module_score=round(technical_setup.score, 1),
        investment_risk_score=round(risk_score, 1),
        fcf_margin_source_type=fcf_metric.sourceType,
        fcf_margin_note=fcf_metric.note,
        max_suggested_position_percent=current_add_limit,
        max_portfolio_weight_percent=max_portfolio_weight,
        current_add_limit_percent=current_add_limit,
        data_confidence=proxy_assessment.data_confidence,
        proxy_confidence=proxy_assessment.proxy_confidence,
        missing_industry_metrics=proxy_assessment.missing_industry_metrics,
        proxy_metrics_used=proxy_assessment.proxy_metrics_used,
        missing_metric_impacts=missing_metric_impacts,
        missing_data_explanation=_missing_data_explanations(missing_metric_impacts),
        rating_cap=rating_cap,
        metric_resolution_statuses=metric_resolution_statuses,
        missing_data_summary=missing_data_summary,
        human_readable_summary=human_summary,
        active_risk_drivers=active_risks,
    )


def _score_axis(factors: Iterable[Factor], context: ScoreContext, risk_axis: bool) -> AxisScore:
    total_weight = 0.0
    available_weight = 0.0
    weighted = 0.0
    missing: list[str] = []
    positives: list[str] = []
    risks: list[str] = []

    for factor in factors:
        total_weight += factor.weight
        value = factor.scorer(context)
        if value is None:
            missing.append(factor.name)
            continue
        value = _clamp(value)
        weighted += value * factor.weight
        available_weight += factor.weight
        if risk_axis:
            if value >= 65:
                risks.append(factor.name)
            elif value <= 25:
                positives.append(factor.name)
        else:
            if value >= 70:
                positives.append(factor.name)
            elif value <= 35:
                risks.append(factor.name)

    if available_weight <= 0:
        return AxisScore(50.0, 0.0, total_weight, missing, positives, risks)
    return AxisScore(round(weighted / available_weight, 1), available_weight, total_weight, missing, positives, risks)


def _core_data_quality_pct(quality: AxisScore, entry: AxisScore, risk: AxisScore) -> float:
    return round(
        quality.availability_pct * 0.50
        + entry.availability_pct * 0.35
        + risk.availability_pct * 0.15,
        1,
    )


def _is_data_insufficient(
    profile: ModelProfile,
    quality: AxisScore,
    entry: AxisScore,
    required_missing: list[str],
    data_quality_pct: float,
) -> bool:
    if required_missing:
        return True
    if data_quality_pct >= profile.data_threshold:
        return False
    return quality.availability_pct < profile.data_threshold or entry.availability_pct < profile.data_threshold


def _missing_metric_impacts(missing: list[str], model_type: ModelType) -> list[dict[str, str]]:
    impacts: list[dict[str, str]] = []
    for item in _dedupe(missing):
        impacts.append(_missing_metric_impact_row(item, model_type))
    return impacts


def _filter_inactive_trigger_missing(missing: list[str], context: ScoreContext) -> list[str]:
    return [item for item in missing if not _is_inactive_trigger_metric(item, context)]


def _is_inactive_trigger_metric(item: str, context: ScoreContext) -> bool:
    lowered = item.lower()
    manual_trigger_keys = {
        "dilution": ("manualDilutionRisk",),
        "acquisition integration": ("manualAcquisitionIntegrationRisk",),
        "ai disruption": ("manualAiDisruptionRisk",),
        "seat compression": ("manualAiDisruptionRisk",),
        "customer concentration": ("manualCustomerConcentration",),
    }
    if _contains_any(lowered, ("negative fcf", "free cash flow negative", "fcf negative")):
        fcf = _metric(context, "free_cash_flow")
        return fcf is None or fcf >= 0
    if lowered.strip() == "below ema200":
        price = _number(context.technicals.get("price"))
        ema200 = _number(context.technicals.get("ema200"))
        return price is None or ema200 is None or price >= ema200
    if "drawdown > 40" in lowered:
        drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
        return drawdown is None or drawdown > -40
    for pattern, keys in manual_trigger_keys.items():
        if pattern in lowered:
            value = _metric(context, *keys)
            return value is None or value < 50
    return False


def _active_risk_drivers(context: ScoreContext) -> list[str]:
    drivers: list[str] = []
    fcf = _metric(context, "free_cash_flow")
    if fcf is not None and fcf < 0:
        drivers.append("自由现金流为负")
    price = _number(context.technicals.get("price"))
    ema200 = _number(context.technicals.get("ema200"))
    if price is not None and ema200 is not None and price < ema200:
        drivers.append("股价低于EMA200")
    drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
    if drawdown is not None and drawdown <= -40:
        drivers.append("距高点回撤超过40%")
    manual_risks = [
        ("manualDilutionRisk", "稀释风险"),
        ("manualAcquisitionIntegrationRisk", "并购整合风险"),
        ("manualAiDisruptionRisk", "AI替代 / 席位压缩风险"),
        ("manualCustomerConcentration", "客户集中度风险"),
    ]
    for key, label in manual_risks:
        value = _metric(context, key)
        if value is not None and value >= 50:
            drivers.append(label)
    return _dedupe(drivers)


def _missing_metric_category(item: str, model_type: ModelType) -> str:
    return str(_metric_taxonomy(item, model_type)["impactCategory"])


def _missing_metric_impact_row(item: str, model_type: ModelType) -> dict[str, str]:
    taxonomy = _metric_taxonomy(item, model_type)
    category = str(taxonomy["impactCategory"])
    base = {
        "metric": item,
        "impactCategory": category,
        "metricType": str(taxonomy["metricType"]),
        "resolutionStatus": str(taxonomy["resolutionStatus"]),
        "confidence": str(taxonomy["confidence"]),
        "affects": str(taxonomy["affects"][0] if taxonomy["affects"] else "ConfidenceOnly"),
        "isBlocking": str(bool(taxonomy["isBlocking"])),
        "ratingCapImpact": str(taxonomy["ratingCapImpact"]),
        "priority": str(taxonomy["priority"]),
        "action": str(taxonomy["recommendedAction"]),
        "explanation": str(taxonomy["explanation"]),
    }
    if category == "CRITICAL_QUALITY":
        return {
            **base,
            "impactLevel": "high",
        }
    if category == "CRITICAL_RISK":
        return {
            **base,
            "impactLevel": "medium",
        }
    if category == "VALUATION_ONLY":
        return {
            **base,
            "impactLevel": "low",
        }
    if category == "TECHNICAL_ONLY":
        return {
            **base,
            "impactLevel": "low",
        }
    if category == "DERIVED_SCORING_FACTOR":
        return {
            **base,
            "impactLevel": "low",
        }
    if category == "QUALITATIVE_RISK_FACTOR":
        return {
            **base,
            "impactLevel": "low",
        }
    return {
        **base,
        "impactLevel": "low",
    }


def _fundamental_missing_data(
    missing: list[str],
    impacts: list[dict[str, str]],
    resolution_rows: list[dict[str, object]] | None = None,
) -> list[str]:
    technical_only = {row["metric"] for row in impacts if row.get("impactCategory") == "TECHNICAL_ONLY"}
    resolved = {
        item
        for item in missing
        if _is_metric_resolved(item, resolution_rows or [])
    }
    return [item for item in missing if item not in technical_only and item not in resolved]


def _metric_taxonomy(item: str, model_type: ModelType) -> dict[str, object]:
    lowered = item.lower()
    if _is_not_applicable_metric(lowered, model_type):
        return _taxonomy(
            "NOT_APPLICABLE",
            "not_applicable",
            "not_applicable",
            ["ExplanationOnly"],
            "NOT_APPLICABLE",
            "none",
            "该行业模型不使用这个指标作为核心评分项。",
            "无需补充",
            priority="low",
        )
    if _contains_any(lowered, _TECHNICAL_PATTERNS):
        return _taxonomy(
            "CALCULATED_METRIC",
            "missing_inputs",
            "low",
            ["Technical"],
            "TECHNICAL_ONLY",
            "none",
            "技术指标由历史价格自动计算；缺失时通常是价格历史未刷新。",
            "刷新价格历史后自动计算",
            ["price history"],
            priority="medium",
        )
    if _contains_any(lowered, _ANALYST_ESTIMATE_PATTERNS):
        return _taxonomy(
            "ANALYST_ESTIMATE_METRIC",
            "requires_analyst_estimates",
            "low",
            ["Entry"],
            "VALUATION_ONLY",
            "none",
            "需要分析师预期或远期收入/利润预测；不影响 Quality Rating。",
            "补齐 analyst estimates",
            ["FMP analyst estimates"],
            priority="low",
        )
    if "debt maturity pressure" in lowered:
        return _taxonomy(
            "DISCLOSURE_KPI",
            "requires_sec_filing",
            "low",
            ["Risk", "ConfidenceOnly"],
            "CRITICAL_RISK",
            "none",
            "债务到期压力需要查看 10-K / 10-Q 债务到期表，不能由普通估值字段直接替代。",
            "检查10-K / 10-Q债务到期表",
            ["SEC 10-K", "SEC 10-Q"],
            priority="high",
        )
    if _is_disclosure_kpi(lowered, model_type):
        return _taxonomy(
            "DISCLOSURE_KPI",
            _disclosure_status(lowered, model_type),
            "low",
            _disclosure_affects(lowered, model_type),
            "CRITICAL_QUALITY" if not _contains_any(lowered, ("risk", "coverage", "hedge", "patent", "pipeline")) else "CRITICAL_RISK",
            "cap_A" if model_type == "SAAS_SOFTWARE" else "none",
            "公司披露型行业 KPI，优先从 IR release、8-K 99.1、投资者材料或 transcript 抽取。",
            _disclosure_action(lowered),
            ["IR release", "SEC 8-K", "investor presentation"],
            priority="high",
        )
    if _contains_any(lowered, _CALCULATED_PATTERNS):
        explanation = "这是可计算指标，应先补齐原始输入，而不是直接人工录入结果。"
        action = "补齐 FMP / SEC 原始输入后自动计算"
        if "cash" in lowered:
            explanation = "现金及等价物应来自 FMP / SEC 资产负债表字段。"
            action = "刷新 FMP 资产负债表；若仍缺失再查 SEC XBRL"
        if "interest coverage" in lowered:
            explanation = "利息覆盖倍数可由 EBIT / interest expense 计算。"
            action = "补齐 EBIT 和利息费用字段"
        return _taxonomy(
            "CALCULATED_METRIC",
            "missing_inputs",
            "low",
            ["Quality", "Risk"],
            "CRITICAL_RISK" if _contains_any(lowered, ("net debt", "interest coverage", "leverage", "debt")) else "CRITICAL_QUALITY",
            "cap_A" if _contains_any(lowered, ("fcf margin", "sbc")) else "none",
            explanation,
            action,
            _source_metrics_for_calculated(lowered),
            priority="high" if _contains_any(lowered, ("fcf margin", "sbc", "net debt", "interest coverage")) else "medium",
        )
    if _is_qualitative_risk_factor(lowered, model_type):
        return _taxonomy(
            "QUALITATIVE_RISK_FACTOR",
            "semi_auto_low_confidence",
            "low",
            ["Risk", "ExplanationOnly"],
            "QUALITATIVE_RISK_FACTOR",
            "none",
            "这是定性风险项，可半自动打标签，但不能作为 blocking missing。",
            "建议复核风险叙事，不需要直接补成财务字段",
            ["filings", "news / risk tags", "symbol risk rules"],
            priority="low",
        )
    if _is_derived_scoring_factor(lowered, model_type):
        return _taxonomy(
            "DERIVED_SCORING_FACTOR",
            "derived_score",
            "medium",
            _derived_affects(lowered),
            "DERIVED_SCORING_FACTOR",
            "none",
            "这是评分因子，不是原始字段；系统会用财务、估值、价格和规则代理推导。",
            "无需人工补字段，可在复核中心调整风险标签",
            _source_metrics_for_derived(lowered, model_type),
            priority="low",
        )
    if model_type != "GENERIC":
        return _taxonomy(
            "DERIVED_SCORING_FACTOR",
            "derived_score",
            "low",
            ["ConfidenceOnly", "ExplanationOnly"],
            "DERIVED_SCORING_FACTOR",
            "none",
            "辅助评分因子暂以低置信度规则推导，不作为关键缺失项。",
            "无需人工补字段；如需提高置信度，可补充行业 KPI",
            ["public financial proxies", "valuation", "technical setup"],
            priority="low",
        )
    return _taxonomy(
        "RAW_FINANCIAL_METRIC",
        "missing",
        "low",
        ["ConfidenceOnly"],
        "SUPPLEMENTAL",
        "none",
        "基础字段缺失，只影响解释完整度。",
        "刷新 FMP / SEC 数据",
        priority="low",
    )


def _taxonomy(
    metric_type: str,
    status: str,
    confidence: str,
    affects: list[str],
    impact_category: str,
    rating_cap: str,
    explanation: str,
    action: str,
    source_metrics: list[str] | None = None,
    is_blocking: bool = False,
    priority: str = "low",
) -> dict[str, object]:
    return {
        "metricType": metric_type,
        "resolutionStatus": status,
        "confidence": confidence,
        "affects": affects,
        "impactCategory": impact_category,
        "ratingCapImpact": rating_cap,
        "explanation": explanation,
        "recommendedAction": action,
        "sourceMetricsUsed": source_metrics or [],
        "isBlocking": is_blocking,
        "priority": priority,
    }


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in value for pattern in patterns)


_TECHNICAL_PATTERNS = (
    "ema",
    "rsi",
    "drawdown",
    "20d",
    "20-day",
    "60d",
    "60-day",
    "52-week",
    "technical",
    "volume trend",
    "trend confirmation",
    "distance from 52-week low",
)

_ANALYST_ESTIMATE_PATTERNS = (
    "peg",
    "forward revenue",
    "forward pe",
    "normalized pe",
    "ntm revenue",
    "expected eps",
    "eps growth",
    "forward eps",
)

_CALCULATED_PATTERNS = (
    "fcf margin",
    "free cash flow",
    "sbc",
    "stock-based compensation",
    "net debt",
    "interest coverage",
    "operating margin",
    "gross margin",
    "roic",
    "roe",
    "roa",
    "fcf yield",
    "ev/ebitda",
    "ev / adjusted ebitda",
    "market cap / adjusted fcf",
    "p/s",
    "ev/sales",
    "ev/fcf",
    "p/fcf",
    "capex / revenue",
    "share count",
    "cash",
    "debt",
    "ebitda",
    "revenue growth",
)

_DISCLOSURE_KPI_PATTERNS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "SAAS_SOFTWARE": (
        "subscription revenue",
        "non-gaap operating",
        "rpo",
        "crpo",
        "net retention",
        "dbnrr",
        "large customer",
        "customers over",
    ),
    "MEGA_CAP_PLATFORM": (
        "cloud revenue",
        "azure growth",
        "ai arr",
        "segment revenue",
        "segment operating income",
    ),
    "POWER_GENERATION": (
        "adjusted ebitda",
        "adjusted fcf",
        "hedge coverage",
        "generation mix",
        "capacity market",
    ),
    "CRYPTO_FINANCIAL_INFRA": (
        "user assets",
        "auc",
        "aum",
        "net deposits",
        "trading volume",
        "transaction revenue",
        "interest revenue",
        "subscription revenue",
        "gold revenue",
        "stablecoin revenue",
        "normalized earnings",
        "normalized ebitda",
    ),
    "PHARMA": (
        "product revenue growth",
        "pipeline updates",
        "trial milestones",
    ),
    "REIT_REAL_ESTATE": (
        "affo",
        "noi",
        "occupancy",
        "lease duration",
    ),
    "BANK_FINANCIAL": (
        "cet1",
        "nim",
        "credit loss",
        "deposit stability",
    ),
}

_DERIVED_SCORING_PATTERNS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "SAAS_SOFTWARE": (
        "ai disruption",
        "seat compression",
        "sbc discipline",
        "valuation vs growth",
        "growth deceleration",
        "growth slowdown",
    ),
    "MEGA_CAP_PLATFORM": (
        "segment strength",
        "buyback discipline",
        "capex concern",
        "ai capex overbuild",
        "segment concentration",
        "buyback",
    ),
    "SEMICONDUCTOR": (
        "cycle position",
        "inventory correction",
        "margin normalization",
        "ai demand",
        "customer concentration",
        "product moat",
        "ecosystem",
        "semiconductor cycle",
    ),
    "SEMICONDUCTOR_CYCLICAL": (
        "cycle-adjusted",
        "cycle trough",
        "cycle peak",
        "inventory glut",
        "fcf inflection",
        "normalized margin",
        "inventory discipline",
        "competitive position",
        "downcycle",
    ),
    "POWER_GENERATION": (
        "power demand",
        "data center",
        "generation asset",
        "commodity",
        "merchant power",
        "regulatory / political",
        "valuation vs peers",
        "fcf volatility",
        "cash-flow visibility",
    ),
    "CRYPTO_FINANCIAL_INFRA": (
        "crypto cycle",
        "revenue diversification",
        "regulatory positioning",
        "product ecosystem",
        "asset quality",
        "customer asset",
        "revenue cyclicality",
        "concentration risk",
        "price vs crypto cycle",
    ),
    "PHARMA": (
        "product concentration",
        "pricing pressure",
        "glp-1",
        "patent durability",
        "capital allocation",
        "pipeline optionality",
    ),
    "MEDICAL_DEVICE": (
        "installed base",
        "procedure volume",
        "recurring revenue",
        "recall",
    ),
    "INDUSTRIAL_CAPEX": (
        "backlog",
        "book-to-bill",
        "order slowdown",
        "cycle peak",
        "capex demand",
        "aftermarket",
    ),
    "AUTO_HARDWARE": (
        "price war",
        "manufacturing execution",
        "margin compression",
        "inventory risk",
        "brand",
        "demand cyclicality",
    ),
}

_QUALITATIVE_RISK_PATTERNS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "SAAS_SOFTWARE": ("ai replacement", "competitive pressure", "customer concentration"),
    "MEGA_CAP_PLATFORM": ("regulatory risk", "antitrust", "ai overbuild narrative"),
    "SEMICONDUCTOR": ("export control", "china risk", "geopolitical"),
    "POWER_GENERATION": ("regulatory risk", "political risk"),
    "CRYPTO_FINANCIAL_INFRA": ("regulatory risk", "platform trust", "crypto market drawdown"),
    "PHARMA": ("pipeline risk", "patent cliff", "regulatory pricing"),
    "MEDICAL_DEVICE": ("fda risk", "product recall"),
}


def _is_disclosure_kpi(lowered: str, model_type: ModelType) -> bool:
    return _contains_any(lowered, _DISCLOSURE_KPI_PATTERNS_BY_MODEL.get(model_type, ()))


def _is_derived_scoring_factor(lowered: str, model_type: ModelType) -> bool:
    if _contains_any(lowered, _DERIVED_SCORING_PATTERNS_BY_MODEL.get(model_type, ())):
        return True
    return _contains_any(lowered, ("discipline", "position", "strength", "discount", "exposure", "quality", "ecosystem", "cycle", "pressure"))


def _is_qualitative_risk_factor(lowered: str, model_type: ModelType) -> bool:
    if _contains_any(lowered, _QUALITATIVE_RISK_PATTERNS_BY_MODEL.get(model_type, ())):
        return True
    return _contains_any(lowered, ("regulatory", "patent", "pipeline", "geopolitical", "export control", "antitrust", "fda"))


def _is_not_applicable_metric(lowered: str, model_type: ModelType) -> bool:
    if model_type == "BANK_FINANCIAL" and _contains_any(lowered, ("ev/fcf", "ev / fcf", "free cash flow yield", "p/s", "ev/sales")):
        return True
    if model_type == "REIT_REAL_ESTATE" and _contains_any(lowered, ("ordinary pe", "standard pe", "gaap pe", "ttm pe")):
        return True
    if model_type == "SAAS_SOFTWARE" and _contains_any(lowered, ("hedge coverage", "generation mix")):
        return True
    if model_type == "POWER_GENERATION" and _contains_any(lowered, ("net retention", "rpo", "crpo")):
        return True
    return False


def _disclosure_status(lowered: str, model_type: ModelType) -> str:
    if model_type == "SAAS_SOFTWARE" and "net retention" in lowered:
        return "company_not_disclosed"
    if model_type in {"REIT_REAL_ESTATE", "POWER_GENERATION", "PHARMA", "CRYPTO_FINANCIAL_INFRA", "MEGA_CAP_PLATFORM"}:
        return "requires_ir_scrape"
    if model_type == "BANK_FINANCIAL":
        return "requires_sec_filing"
    return "requires_ir_scrape"


def _disclosure_affects(lowered: str, model_type: ModelType) -> list[str]:
    if _contains_any(lowered, ("risk", "hedge", "patent", "pipeline", "credit loss", "cet1")):
        return ["Risk", "ConfidenceOnly"]
    if model_type == "REIT_REAL_ESTATE":
        return ["Quality", "Entry"]
    return ["Quality", "ConfidenceOnly"]


def _disclosure_action(lowered: str) -> str:
    if "not disclosed" in lowered:
        return "确认公司是否披露，必要时人工补充"
    return "抓取 IR release / 8-K / investor presentation"


def _derived_affects(lowered: str) -> list[str]:
    if _contains_any(lowered, ("risk", "concern", "pressure", "concentration", "overbuild", "cycle", "discount")):
        return ["Risk", "ExplanationOnly"]
    if _contains_any(lowered, ("valuation", "entry", "position")):
        return ["Entry", "ExplanationOnly"]
    return ["Quality", "ExplanationOnly"]


def _source_metrics_for_calculated(lowered: str) -> list[str]:
    if "fcf margin" in lowered:
        return ["free cash flow", "revenue"]
    if "sbc" in lowered or "stock-based compensation" in lowered:
        return ["stock-based compensation", "revenue"]
    if "net debt" in lowered:
        return ["total debt", "cash", "EBITDA"]
    if "interest coverage" in lowered:
        return ["EBIT", "interest expense"]
    if "drawdown" in lowered:
        return ["current price", "52-week high"]
    return ["FMP structured data", "SEC XBRL"]


def _source_metrics_for_derived(lowered: str, model_type: ModelType) -> list[str]:
    if model_type == "MEGA_CAP_PLATFORM":
        return ["revenue growth", "operating margin", "FCF", "capex", "share count", "valuation"]
    if model_type == "POWER_GENERATION":
        return ["EBITDA", "FCF", "net debt", "EV/EBITDA", "drawdown"]
    if model_type == "CRYPTO_FINANCIAL_INFRA":
        return ["revenue growth", "FCF", "cash/debt", "valuation", "price drawdown"]
    if model_type == "PHARMA":
        return ["revenue growth", "margin", "FCF", "valuation", "symbol risk tags"]
    if model_type.startswith("SEMICONDUCTOR"):
        return ["revenue growth", "gross margin", "inventory/capex if available", "valuation", "price momentum"]
    return ["public financial proxies", "valuation", "technical setup"]


def _metric_resolution_statuses(context: ScoreContext, impacts: list[dict[str, str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rows.append(_resolution_row_for_fcf_margin(context))
    if context.model_type == "SAAS_SOFTWARE":
        rows.extend(
            [
                _resolution_row_for_metric(
                    context,
                    metric_key="sbcToRevenue",
                    display_name="SBC / revenue",
                    keys=("manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio"),
                    default_status="requires_sec_filing",
                    explanation="可由 stock-based compensation / revenue 计算；若 FMP/SEC 无原始字段，需要 SEC companyfacts。",
                    action="抓取 SEC companyfacts 或手动补充 SBC",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="netDebtToEbitda",
                    display_name="net debt / EBITDA",
                    keys=("manualNetDebtToAdjustedEbitda", "net_debt_to_ebitda"),
                    default_status="manual_override_required",
                    explanation="可由总债务、现金和 EBITDA 计算；缺原始字段时需补充。",
                    action="补齐 total debt / cash / EBITDA",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="interestCoverage",
                    display_name="interest coverage",
                    keys=("interest_coverage", "manualInterestCoverage"),
                    default_status="manual_override_required",
                    explanation="可由 EBIT / interest expense 计算；缺利息费用时需补充。",
                    action="补齐 EBIT 和 interest expense",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="subscriptionRevenueGrowth",
                    display_name="subscription revenue growth",
                    keys=("manualSubscriptionRevenueGrowth", "subscription_revenue_growth"),
                    default_status="requires_ir_scrape",
                    explanation="FMP 通常没有标准字段，需要从 earnings release / 8-K Exhibit 99.1 抓取。",
                    action="抓取 earnings release / 8-K Exhibit 99.1",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="nonGaapOperatingMargin",
                    display_name="non-GAAP operating margin",
                    keys=("manualNonGaapOperatingMargin", "non_gaap_operating_margin"),
                    default_status="requires_ir_scrape",
                    explanation="Non-GAAP 指标通常来自 IR release 或 investor presentation。",
                    action="抓取 earnings release / investor presentation",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="rpoGrowth",
                    display_name="RPO / cRPO growth",
                    keys=("manualRpoGrowth", "manualArrGrowth", "rpo_growth", "crpo_growth"),
                    default_status="requires_ir_scrape",
                    explanation="RPO/cRPO 增速通常披露在财报新闻稿或 8-K 99.1。",
                    action="抓取 earnings release / 8-K Exhibit 99.1",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="cRpoGrowth",
                    display_name="cRPO growth",
                    keys=("manualCrpoGrowth", "manualCRPOGrowth", "cRpoGrowth", "crpo_growth"),
                    default_status="requires_ir_scrape",
                    explanation="cRPO growth is normally disclosed in the IR release, 8-K Exhibit 99.1, investor presentation, or transcript.",
                    action="Fetch IR release / 8-K Exhibit 99.1 before asking for manual review.",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="largeCustomerGrowth",
                    display_name="large customer growth",
                    keys=("manualLargeCustomerGrowth", "largeCustomerGrowth", "large_customer_growth"),
                    default_status="requires_ir_scrape",
                    explanation="Large-customer growth is a company-disclosed SaaS KPI. Try IR release, 8-K 99.1, investor presentation, or transcript first.",
                    action="Fetch IR / SEC disclosure before asking for a manual override.",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="netRetentionRate",
                    display_name="net retention rate",
                    keys=("manualNetRetention", "net_retention_rate", "dbnrr"),
                    default_status="company_not_disclosed",
                    explanation="部分成熟 SaaS 公司不再稳定披露净留存率；抓不到时不直接视为经营恶化。",
                    action="检查年报 / investor presentation / 手动补充",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="peg",
                    display_name="PEG",
                    keys=("peg_ratio", "peg"),
                    default_status="requires_analyst_estimates",
                    explanation="PEG 需要 forward PE 和预期 EPS 增速，不影响 Quality Rating。",
                    action="补齐分析师 EPS 预期",
                ),
                _resolution_row_for_metric(
                    context,
                    metric_key="forwardRevenueMultiple",
                    display_name="forward revenue multiple",
                    keys=("forward_revenue_multiple", "forward_price_to_sales"),
                    default_status="requires_analyst_estimates",
                    explanation="远期收入倍数需要 NTM revenue estimate；缺失时用 TTM P/S 或 EV/Sales 观察。",
                    action="补齐 NTM revenue estimate",
                ),
            ]
        )
    rows.extend(_model_specific_resolution_rows(context))
    rows.extend(_technical_resolution_rows(context))
    by_name = {str(row["displayName"]): row for row in rows}
    for impact in impacts:
        metric = str(impact.get("metric") or "")
        if not metric or metric in by_name:
            continue
        if _is_metric_resolved(metric, rows):
            continue
        by_name[metric] = {
            "metricKey": _metric_key_from_name(metric),
            "displayName": metric,
            "metricType": str(impact.get("metricType") or "RAW_FINANCIAL_METRIC"),
            "value": None,
            "resolutionStatus": _status_from_impact(impact),
            "sourceType": "missing",
            "confidence": str(impact.get("confidence") or "low"),
            "affects": _list_from_impact_affects(impact.get("affects")),
            "isBlocking": str(impact.get("isBlocking") or "").lower() == "true",
            "ratingCapImpact": str(impact.get("ratingCapImpact") or "none"),
            "explanation": str(impact.get("explanation") or "字段尚未补齐。"),
            "recommendedAction": str(impact.get("action") or "需人工补充"),
            "sourceMetricsUsed": _source_metrics_for_derived(metric.lower(), context.model_type),
            "priority": str(impact.get("priority") or "low"),
        }
    return _finalize_metric_resolution_rows(list(by_name.values()), context)


def _finalize_metric_resolution_rows(rows: list[dict[str, object]], context: ScoreContext) -> list[dict[str, object]]:
    return [_with_missing_resolution_route(dict(row), context) for row in rows]


def _with_missing_resolution_route(row: dict[str, object], context: ScoreContext) -> dict[str, object]:
    route = str(row.get("missingResolutionRoute") or _missing_resolution_route(row, context))
    row["missingResolutionRoute"] = route
    row["defaultReviewQueue"] = _default_review_queue(row, route, context.model_type)
    row["reviewPriority"] = _review_priority_for_route(row, route)

    if route in {"analyst_estimates_required", "company_not_disclosed", "proxy_available", "low_priority_archive"}:
        row["isBlocking"] = False
    if route == "analyst_estimates_required":
        row["affects"] = ["Entry"]
        row["priority"] = "low"
        row["ratingCapImpact"] = "none"
    if route == "low_priority_archive":
        row["priority"] = "low"
        row["ratingCapImpact"] = "none"
        row["recommendedAction"] = "Low materiality; archive by default unless leverage facts change."
    if route == "auto_calculate" and str(row.get("resolutionStatus") or "") == "missing_inputs":
        row["recommendedAction"] = row.get("recommendedAction") or "Refresh structured FMP / SEC inputs, then calculate automatically."
    return row


def _missing_resolution_route(row: dict[str, object], context: ScoreContext) -> str:
    metric_key = str(row.get("metricKey") or "")
    status = str(row.get("resolutionStatus") or "")
    metric_type = str(row.get("metricType") or "")
    source_type = str(row.get("sourceType") or "")

    if metric_key == "debtMaturityPressure":
        return _debt_maturity_resolution_route(context)
    if status in {"available", "calculated"}:
        if metric_type == "CALCULATED_METRIC" or source_type == "calculated":
            return "auto_calculate"
        return "proxy_available"
    if status == "missing_inputs":
        return "auto_calculate" if metric_type == "CALCULATED_METRIC" else "human_review_required"
    if status in {"requires_ir_scrape", "requires_sec_filing"}:
        return "ir_or_sec_extract"
    if status == "requires_analyst_estimates":
        return "analyst_estimates_required"
    if status == "company_not_disclosed":
        return "company_not_disclosed"
    if status in {"derived_score", "semi_auto_low_confidence"}:
        return "proxy_available"
    if status == "manual_override_required":
        return "human_review_required"
    if status in {"not_applicable"}:
        return "low_priority_archive"
    return "human_review_required"


def _debt_maturity_resolution_route(context: ScoreContext) -> str:
    net_debt = _snapshot_number(context.snapshot, "net_debt", "netDebt")
    net_debt_to_ebitda = _snapshot_number(context.snapshot, "net_debt_to_ebitda", "netDebtToEbitda")
    interest_coverage = _snapshot_number(context.snapshot, "interest_coverage", "interestCoverage")
    total_debt = _snapshot_number(context.snapshot, "total_debt", "totalDebt")
    market_cap = _snapshot_number(context.snapshot, "market_cap", "marketCap")
    if net_debt is not None and net_debt <= 0:
        return "low_priority_archive"
    if net_debt_to_ebitda is not None and net_debt_to_ebitda < 1:
        return "low_priority_archive"
    if total_debt is not None and market_cap is not None and market_cap > 0 and total_debt / market_cap < 0.1:
        return "low_priority_archive"
    if net_debt_to_ebitda is not None and net_debt_to_ebitda > 2:
        return "human_review_required"
    if interest_coverage is not None and interest_coverage < 3:
        return "human_review_required"
    return "ir_or_sec_extract"


def _snapshot_number(snapshot: dict, *keys: str) -> float | None:
    for key in keys:
        value = snapshot.get(key)
        if value is None:
            value = snapshot.get(_camel_to_snake(key))
        number = _number(value)
        if number is not None:
            return number
    return None


def _default_review_queue(row: dict[str, object], route: str, model_type: ModelType) -> bool:
    if route == "human_review_required":
        return True
    if route in {"analyst_estimates_required", "company_not_disclosed", "low_priority_archive", "auto_calculate"}:
        return False
    if route == "ir_or_sec_extract":
        return model_type != "SAAS_SOFTWARE"
    if route == "proxy_available":
        return model_type != "SAAS_SOFTWARE" and bool(_affects_scoring(row))
    return False


def _review_priority_for_route(row: dict[str, object], route: str) -> str:
    if route == "human_review_required":
        return "high" if str(row.get("priority") or "") == "high" else "medium"
    if route in {"auto_calculate", "ir_or_sec_extract"}:
        return "medium"
    return "low"


def _affects_scoring(row: dict[str, object]) -> bool:
    affects = row.get("affects")
    if isinstance(affects, (list, tuple, set)):
        values = {str(item) for item in affects if item}
    elif isinstance(affects, str):
        values = {item.strip() for item in affects.split(",") if item.strip()}
    else:
        values = set()
    return bool(values & {"Quality", "Entry", "Risk"})


def _missing_data_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    missing_rows = [
        row
        for row in rows
        if str(row.get("resolutionStatus") or "") not in {"available", "calculated", "not_applicable"}
    ]
    routes = [str(row.get("missingResolutionRoute") or "") for row in missing_rows]
    human_rows = [row for row in missing_rows if row.get("missingResolutionRoute") == "human_review_required"]
    key_blocking_metrics = [str(row.get("displayName") or row.get("metricKey") or "") for row in human_rows if row.get("displayName") or row.get("metricKey")]
    summary = {
        "blockingCount": len(key_blocking_metrics),
        "autoFillableCount": sum(1 for route in routes if route in {"auto_calculate", "ir_or_sec_extract", "proxy_available"}),
        "estimatesRequiredCount": routes.count("analyst_estimates_required"),
        "companyNotDisclosedCount": routes.count("company_not_disclosed"),
        "lowPriorityArchivedCount": routes.count("low_priority_archive"),
        "humanReviewRequiredCount": len(human_rows),
        "keyBlockingMetrics": key_blocking_metrics,
        "recommendedNextAction": "no_missing_data",
    }
    if human_rows:
        summary["recommendedNextAction"] = "handle_high_impact_manual_review"
    elif summary["autoFillableCount"]:
        summary["recommendedNextAction"] = "run_auto_fill_or_refresh_disclosures"
    elif summary["estimatesRequiredCount"]:
        summary["recommendedNextAction"] = "configure_analyst_estimates_for_valuation"
    elif summary["companyNotDisclosedCount"] or summary["lowPriorityArchivedCount"]:
        summary["recommendedNextAction"] = "no_user_action_required"
    return summary


def _is_metric_resolved(metric: str, resolution_rows: list[dict[str, object]]) -> bool:
    lowered = metric.lower()
    resolved_statuses = {"available", "calculated", "derived", "derived_score", "semi_auto_low_confidence", "not_applicable"}
    aliases = {
        "fcfMargin": ("fcf margin", "calculated fcf margin", "fcf margin reported/calculated"),
        "sbcToRevenue": ("sbc / revenue", "stock-based compensation", "sbc"),
        "netDebtToEbitda": ("net debt / ebitda", "net debt"),
        "interestCoverage": ("interest coverage",),
        "ema20": ("ema20",),
        "ema50": ("ema50",),
        "ema200": ("ema200", "below ema200", "above / below ema200"),
        "rsi14": ("rsi14", "rsi"),
        "drawdownFrom52WeekHigh": ("drawdown", "52-week drawdown"),
        "return20d": ("20d return", "20-day", "20日"),
        "return60d": ("60d return", "60-day", "60日"),
    }
    for row in resolution_rows:
        status = str(row.get("resolutionStatus") or "")
        if status not in resolved_statuses:
            continue
        metric_key = str(row.get("metricKey") or "")
        display_name = str(row.get("displayName") or "").lower()
        if metric_key and metric_key.lower() == lowered:
            return True
        if display_name and display_name == lowered:
            return True
        if any(token in lowered for token in aliases.get(metric_key, ())):
            return True
    return False


def _resolution_row_for_fcf_margin(context: ScoreContext) -> dict[str, object]:
    metric = fcf_margin_metric(context.snapshot)
    if metric.value is None:
        return MetricResolution(
            metricKey="fcfMargin",
            displayName="FCF margin",
            metricType="CALCULATED_METRIC",
            value=None,
            resolutionStatus="missing_inputs",
            sourceType="missing",
            confidence="low",
            affects=["Quality"],
            ratingCapImpact="cap_A",
            explanation="缺少 free cash flow 或 revenue，暂不能计算 FCF margin。",
            recommendedAction="补齐 FMP cash flow 和 revenue 后自动计算",
            sourceMetricsUsed=["free cash flow", "revenue"],
            priority="high",
        ).to_dict()
    if metric.sourceType == "derivedFromMarket":
        status = "derived_score"
        display_name = "Implied FCF Margin"
        metric_type = "DERIVED_SCORING_FACTOR"
        affects = ["Entry", "ExplanationOnly"]
        priority = "medium"
        explanation = "基于 FCF收益率 × 市销率推导，置信度低于财报直接计算值，暂不参与公司质量评分。"
    elif metric.sourceType == "calculated":
        status = "calculated"
        display_name = "FCF Margin"
        metric_type = "CALCULATED_METRIC"
        affects = ["Quality", "Entry"]
        priority = "high"
        explanation = "基于 FMP cash flow 和收入计算：freeCashFlow / revenue。"
    else:
        status = "available"
        display_name = "FCF Margin"
        metric_type = "RAW_FINANCIAL_METRIC"
        affects = ["Quality", "Entry"]
        priority = "high"
        explanation = "来自结构化财务数据或公司披露。"
    return MetricResolution(
        metricKey="fcfMargin",
        displayName=display_name,
        metricType=metric_type,
        value=metric.value,
        resolutionStatus=status,
        sourceType=metric.sourceType,
        confidence="high" if status in {"available", "calculated"} else "medium",
        affects=affects,
        explanation=explanation,
        recommendedAction="无需补充" if status != "derived_score" else "如需用于质量评分，请补齐 free cash flow 和 revenue",
        sourceMetricsUsed=["free cash flow", "revenue"] if status in {"calculated", "available"} else ["FCF yield", "P/S"],
        priority=priority,
    ).to_dict()


def _resolution_row_for_metric(
    context: ScoreContext,
    metric_key: str,
    display_name: str,
    keys: tuple[str, ...],
    default_status: str,
    explanation: str,
    action: str,
) -> dict[str, object]:
    taxonomy = _metric_taxonomy(display_name, context.model_type)
    metric_type = str(taxonomy["metricType"])
    affects = list(taxonomy["affects"]) if isinstance(taxonomy["affects"], list) else ["ConfidenceOnly"]
    rating_cap = str(taxonomy["ratingCapImpact"])
    source_metrics = list(taxonomy["sourceMetricsUsed"]) if isinstance(taxonomy["sourceMetricsUsed"], list) else []
    value = _metric(context, *keys)
    if value is not None:
        source_type = _resolution_source_type(context, keys)
        status = "calculated" if source_type == "calculated" or metric_key in {"sbcToRevenue", "netDebtToEbitda", "interestCoverage"} else "available"
        if status == "calculated":
            metric_type = "CALCULATED_METRIC"
        return MetricResolution(
            metricKey=metric_key,
            displayName=display_name,
            metricType=metric_type,
            value=value,
            resolutionStatus=status,
            sourceType=source_type,
            confidence="high",
            affects=affects,
            ratingCapImpact="none",
            explanation=_available_resolution_explanation(metric_key, explanation),
            recommendedAction="无需补充",
            sourceMetricsUsed=source_metrics,
            priority=str(taxonomy["priority"]),
        ).to_dict()
    if metric_type == "CALCULATED_METRIC" and default_status in {"manual_override_required", "missing"}:
        default_status = "missing_inputs"
        action = "补齐原始输入后自动计算"
    return MetricResolution(
        metricKey=metric_key,
        displayName=display_name,
        metricType=metric_type,
        value=None,
        resolutionStatus=default_status,
        sourceType="missing",
        confidence=str(taxonomy["confidence"]),
        affects=affects,
        ratingCapImpact=rating_cap,
        explanation=explanation,
        recommendedAction=action,
        sourceMetricsUsed=source_metrics,
        priority=str(taxonomy["priority"]),
    ).to_dict()


def _technical_resolution_rows(context: ScoreContext) -> list[dict[str, object]]:
    specs = [
        ("ema20", "EMA20", "ema20"),
        ("ema50", "EMA50", "ema50"),
        ("ema200", "EMA200", "ema200"),
        ("rsi14", "RSI14", "rsi14"),
        ("drawdownFrom52WeekHigh", "drawdown", "drawdown_from_high_pct"),
        ("return20d", "20d return", "gain_20d_pct"),
        ("return60d", "60d return", "gain_60d_pct"),
        ("volumeTrend", "volume trend", "volume_trend"),
    ]
    rows = []
    for metric_key, display_name, technical_key in specs:
        value = _number(context.technicals.get(technical_key))
        rows.append(
            {
                "metricKey": metric_key,
                "displayName": display_name,
                "metricType": "CALCULATED_METRIC",
                "value": value,
                "resolutionStatus": "calculated" if value is not None else "missing_inputs",
                "sourceType": "calculated" if value is not None else "missing",
                "confidence": "high" if value is not None else "low",
                "affects": ["Technical"],
                "isBlocking": False,
                "ratingCapImpact": "none",
                "explanation": "基于日线价格自动计算。" if value is not None else "技术指标计算任务未完成。",
                "recommendedAction": "无需补充" if value is not None else "刷新价格历史后自动计算",
                "sourceMetricsUsed": ["price history"],
                "priority": "medium" if metric_key in {"ema200", "rsi14", "drawdownFrom52WeekHigh"} else "low",
            }
        )
    return rows


def _resolution_source_type(context: ScoreContext, keys: tuple[str, ...]) -> str:
    sources = context.snapshot.get("metric_sources")
    if isinstance(sources, dict):
        for key in keys:
            raw = sources.get(key) or sources.get(_camel_to_snake(key))
            if isinstance(raw, dict) and raw.get("sourceType"):
                return str(raw["sourceType"])
    return "calculated"


def _model_specific_resolution_rows(context: ScoreContext) -> list[dict[str, object]]:
    model_type = context.model_type
    rows: list[MetricResolution] = []
    if model_type == "BANK_FINANCIAL":
        rows.append(
            MetricResolution(
                metricKey="evToFcf",
                displayName="EV/FCF",
                metricType="NOT_APPLICABLE",
                resolutionStatus="not_applicable",
                sourceType="not_applicable",
                confidence="not_applicable",
                affects=["ExplanationOnly"],
                explanation="银行不使用 EV/FCF 作为核心估值指标，应看 P/TBV、ROE、CET1 和信用质量。",
                recommendedAction="无需补充",
            )
        )
    if model_type == "REIT_REAL_ESTATE":
        rows.extend(
            [
                MetricResolution(
                    metricKey="ordinaryPe",
                    displayName="普通 PE",
                    metricType="NOT_APPLICABLE",
                    resolutionStatus="not_applicable",
                    sourceType="not_applicable",
                    confidence="not_applicable",
                    affects=["ExplanationOnly"],
                    explanation="REIT 不把普通 PE 当作核心估值，应优先看 AFFO、NOI、入住率和债务。",
                    recommendedAction="无需补充",
                ),
                _model_disclosure_metric(context, "affo", "AFFO", ("manualAffo", "affo"), "requires_ir_scrape", "AFFO 通常来自 REIT supplemental / 10-Q / IR presentation。"),
            ]
        )
    if model_type == "POWER_GENERATION":
        rows.extend(
            [
                _model_disclosure_metric(context, "adjustedEbitda", "adjusted EBITDA", ("manualAdjustedEbitda", "adjustedEbitda", "adjusted_ebitda"), "requires_ir_scrape", "Adjusted EBITDA 通常来自 earnings release / investor presentation。"),
                _model_disclosure_metric(context, "adjustedFcfBeforeGrowth", "adjusted FCF before growth", ("manualAdjustedFcfBeforeGrowth", "adjustedFcfBeforeGrowth", "adjusted_fcf_before_growth"), "requires_ir_scrape", "Adjusted FCF before growth 通常来自公司指引或 IR 材料。"),
                _model_disclosure_metric(context, "hedgeCoverage", "hedge coverage", ("manualHedgeCoverageCurrentYear", "hedgeCoverageCurrentYear", "hedge_coverage_current_year"), "requires_ir_scrape", "对冲覆盖率通常来自 IR presentation 或 10-K 风险披露。"),
                _derived_resolution("powerDemandExposure", "power demand exposure", model_type, "由数据中心电力需求、收入增长、估值和行业标签推导。"),
                _derived_resolution("generationAssetQuality", "generation asset quality", model_type, "由 EBITDA、FCF、杠杆和发电资产叙事代理推导。"),
                _derived_resolution("commodityExposure", "commodity exposure", model_type, "由 merchant power exposure、FCF 波动和能源价格敏感度代理推导。"),
            ]
        )
    if model_type == "CRYPTO_FINANCIAL_INFRA":
        rows.extend(
            [
                _derived_resolution("revenueDiversification", "revenue diversification", model_type, "由收入增速、FCF、估值和业务标签代理判断。"),
                _qualitative_resolution("regulatoryRisk", "regulatory risk", model_type, "监管风险不能作为 blocking missing，只做半自动低置信度风险标签。"),
                _derived_resolution("cryptoCycleSensitivity", "crypto cycle sensitivity", model_type, "由价格回撤、估值和 symbol risk proxy 推导。"),
            ]
        )
        if _symbol(context) == "HOOD":
            rows.extend(_hood_brokerage_buy_zone_resolution_rows(context))
    if model_type == "PHARMA":
        rows.extend(
            [
                _qualitative_resolution("pipelineRisk", "pipeline risk", model_type, "管线风险需要复核，但缺失时不应直接打成数据不足。"),
                _qualitative_resolution("patentCliffRisk", "patent cliff risk", model_type, "专利悬崖风险为定性风险标签，不作为 blocking missing。"),
                _derived_resolution("pricingPressure", "pricing pressure", model_type, "由药品组合、美国价格压力和 symbol risk proxy 半自动推导。"),
            ]
        )
    if model_type == "MEGA_CAP_PLATFORM":
        rows.extend(
            [
                _resolution_row_for_metric(
                    context,
                    metric_key="netCashBalanceSheet",
                    display_name="Net Cash / Balance Sheet",
                    keys=("net_cash", "total_cash", "cash_and_cash_equivalents", "cashAndCashEquivalents", "current_ratio"),
                    default_status="derived_score",
                    explanation="基于现金、短期投资、总债务、流动比率等判断资产负债表强度。",
                    action="无需人工补字段；必要时复核现金和总债务数据",
                ),
                _derived_resolution("segmentStrength", "Segment strength", model_type, "由收入增速、利润率、云/广告/平台业务表现代理推导。"),
                _derived_resolution("buybackDiscipline", "Buyback discipline", model_type, "由回购金额、股数变化、FCF 和净现金代理推导。"),
                _derived_resolution("historicalValuationPercentile", "Historical valuation percentile", model_type, "由历史估值区间、当前 P/FCF、P/S 和远期估值代理推导。"),
                _derived_resolution("capexConcernDiscount", "Capex concern discount", model_type, "由 capex、FCF margin、AI capex 叙事和估值代理推导。"),
                _derived_resolution("aiCapexOverbuildRisk", "AI capex overbuild risk", model_type, "半自动判断 AI capex 过热风险，不是原始字段缺失。"),
                _qualitative_resolution("regulatoryRisk", "Regulatory risk", model_type, "监管和反垄断风险为定性风险标签，不作为 blocking missing。"),
            ]
        )
    return [row.to_dict() if isinstance(row, MetricResolution) else row for row in rows]


def _model_disclosure_metric(
    context: ScoreContext,
    metric_key: str,
    display_name: str,
    keys: tuple[str, ...],
    default_status: str,
    explanation: str,
) -> MetricResolution:
    value = _metric(context, *keys)
    if value is not None:
        return MetricResolution(
            metricKey=metric_key,
            displayName=display_name,
            metricType="DISCLOSURE_KPI",
            resolutionStatus="available",
            value=value,
            sourceType=_resolution_source_type(context, keys),
            confidence="high",
            affects=["Quality", "Risk"],
            explanation="已取得公司披露的行业 KPI。",
            recommendedAction="无需补充",
            sourceMetricsUsed=["IR release", "SEC filing", "manual override"],
            priority="high",
        )
    return MetricResolution(
        metricKey=metric_key,
        displayName=display_name,
        metricType="DISCLOSURE_KPI",
        resolutionStatus=default_status,
        value=None,
        sourceType="missing",
        confidence="low",
        affects=["Quality", "ConfidenceOnly"],
        isBlocking=False,
        ratingCapImpact="none",
        explanation=explanation,
        recommendedAction="抓取 IR / 8-K / investor presentation；若公司未披露再考虑人工补充",
        sourceMetricsUsed=["IR release", "SEC 8-K", "investor presentation"],
        priority="high",
    )


def _hood_brokerage_buy_zone_resolution_rows(context: ScoreContext) -> list[MetricResolution]:
    specs = (
        (
            "hoodAuc",
            "AUC",
            ("manualHoodAuc", "hoodAuc", "hood_auc", "auc", "assets_under_custody", "assetsUnderCustody"),
            "Source priority: shareholder letter > earnings release > IR release > SEC 8-K > 10-Q > 10-K.",
            "Keywords: AUC, Assets Under Custody; unit USD; period quarter-end point-in-time.",
        ),
        (
            "hoodNetDeposits",
            "net deposits",
            ("manualHoodNetDeposits", "hoodNetDeposits", "hood_net_deposits", "net_deposits", "netDeposits"),
            "Source priority: shareholder letter > earnings release > IR release > SEC 8-K > 10-Q > 10-K.",
            "Keywords: net deposits; unit USD; period quarterly flow.",
        ),
        (
            "hoodTransactionRevenue",
            "transaction revenue",
            ("manualHoodTransactionRevenue", "hoodTransactionRevenue", "hood_transaction_revenue", "transaction_revenue", "transactionRevenue"),
            "Source priority: shareholder letter > earnings release > IR release > SEC 8-K > 10-Q > 10-K.",
            "Keywords: transaction revenue, transaction-based revenues; unit USD; period quarterly revenue.",
        ),
        (
            "hoodInterestRevenue",
            "interest revenue",
            ("manualHoodInterestRevenue", "hoodInterestRevenue", "hood_interest_revenue", "interest_revenue", "interestRevenue"),
            "Source priority: SEC companyfacts / 10-Q / 10-K > shareholder letter > earnings release > IR release > SEC 8-K.",
            "Keywords: net interest revenue, interest revenue, InterestIncomeExpenseNet; unit USD; period quarterly revenue.",
        ),
        (
            "hoodSubscriptionGoldRevenue",
            "subscription / Gold revenue",
            (
                "manualHoodSubscriptionGoldRevenue",
                "hoodSubscriptionGoldRevenue",
                "hood_subscription_gold_revenue",
                "subscription_gold_revenue",
                "subscriptionGoldRevenue",
                "gold_revenue",
                "goldRevenue",
            ),
            "Source priority: shareholder letter > earnings release > IR release > SEC 8-K > 10-Q > 10-K.",
            "Keywords: subscription and services revenue, Robinhood Gold revenue, Gold revenue; unit USD; period quarterly revenue.",
        ),
        (
            "hoodNormalizedEarnings",
            "normalized earnings",
            ("manualHoodNormalizedEarnings", "hoodNormalizedEarnings", "hood_normalized_earnings", "normalized_earnings", "normalizedEarnings"),
            "Source priority: shareholder letter > earnings release > IR release > SEC 8-K > 10-Q > 10-K.",
            "Keywords: normalized earnings, adjusted net income, non-GAAP net income; unit USD; period quarterly or TTM; review adjustment basis.",
        ),
        (
            "hoodNormalizedEbitda",
            "normalized EBITDA",
            ("manualHoodNormalizedEbitda", "hoodNormalizedEbitda", "hood_normalized_ebitda", "normalized_ebitda", "normalizedEbitda"),
            "Source priority: shareholder letter > earnings release > IR release > SEC 8-K > 10-Q > 10-K.",
            "Keywords: adjusted EBITDA, normalized EBITDA; unit USD; period quarterly or TTM; review adjustment basis.",
        ),
    )
    return [
        _hood_brokerage_buy_zone_metric(context, metric_key, display_name, keys, source_priority, extraction_hint)
        for metric_key, display_name, keys, source_priority, extraction_hint in specs
    ]


def _hood_brokerage_buy_zone_metric(
    context: ScoreContext,
    metric_key: str,
    display_name: str,
    keys: tuple[str, ...],
    source_priority: str,
    extraction_hint: str,
) -> MetricResolution:
    value = _metric(context, *keys)
    source_metrics = ["SEC 10-Q", "SEC 10-K", "shareholder letter", "earnings release", "IR release"]
    if value is not None:
        return MetricResolution(
            metricKey=metric_key,
            displayName=display_name,
            metricType="DISCLOSURE_KPI",
            resolutionStatus="available",
            value=value,
            sourceType=_resolution_source_type(context, keys),
            confidence="high",
            affects=["Entry", "ConfidenceOnly"],
            isBlocking=False,
            ratingCapImpact="none",
            explanation=f"{display_name} is available for the future HOOD brokerage/fintech buy-zone model.",
            recommendedAction="No refill needed.",
            sourceMetricsUsed=source_metrics,
            priority="high",
        )
    if metric_key == "hoodNormalizedEarnings":
        explanation = (
            "未在当前披露文本中找到 normalized earnings，需人工确认 non-GAAP 盈利口径。"
            "这是 HOOD 券商 / 金融科技买区模型的核心经营输入，影响系统置信度；"
            "不得用 P/S、P/FCF 或 FCF yield 替代。"
            "来源优先级：股东信 / earnings release / IR release / SEC 8-K / 10-Q / 10-K。"
            "抽取提示：关键词 normalized earnings、adjusted net income、non-GAAP net income；单位 USD；口径为季度或 TTM。"
        )
        recommended_action = (
            "需从 SEC / 股东信 / earnings release / 10-Q / 10-K 补充 normalized earnings 证据，"
            "并人工确认 non-GAAP 调整口径后，才能支持更精确的 HOOD 买区模型。"
        )
    else:
        explanation = (
            f"{display_name} is a core HOOD brokerage/fintech operating input for the buy-zone model and system confidence; "
            "do not substitute P/S, P/FCF, or FCF yield for it. "
            f"{source_priority} {extraction_hint}"
        )
        recommended_action = (
            f"Fetch SEC / shareholder letter / earnings release / 10-Q / 10-K evidence for {display_name} "
            "before enabling a precise HOOD buy-zone model."
        )

    return MetricResolution(
        metricKey=metric_key,
        displayName=display_name,
        metricType="DISCLOSURE_KPI",
        resolutionStatus="requires_ir_scrape",
        value=None,
        sourceType="missing",
        confidence="low",
        affects=["Entry", "ConfidenceOnly"],
        isBlocking=False,
        ratingCapImpact="none",
        explanation=explanation,
        recommendedAction=recommended_action,
        sourceMetricsUsed=source_metrics,
        priority="high",
    )


def _derived_resolution(metric_key: str, display_name: str, model_type: ModelType, explanation: str) -> MetricResolution:
    return MetricResolution(
        metricKey=metric_key,
        displayName=display_name,
        metricType="DERIVED_SCORING_FACTOR",
        resolutionStatus="derived_score",
        value=None,
        sourceType="rule_derived",
        confidence="medium",
        affects=_derived_affects(display_name.lower()),
        isBlocking=False,
        ratingCapImpact="none",
        explanation=explanation,
        recommendedAction="无需人工补字段；如需提高置信度，可在复核中心补充行业标签",
        sourceMetricsUsed=_source_metrics_for_derived(display_name.lower(), model_type),
        priority="low",
    )


def _qualitative_resolution(metric_key: str, display_name: str, model_type: ModelType, explanation: str) -> MetricResolution:
    return MetricResolution(
        metricKey=metric_key,
        displayName=display_name,
        metricType="QUALITATIVE_RISK_FACTOR",
        resolutionStatus="semi_auto_low_confidence",
        value=None,
        sourceType="semi_auto_risk_tag",
        confidence="low",
        affects=["Risk", "ExplanationOnly"],
        isBlocking=False,
        ratingCapImpact="none",
        explanation=explanation,
        recommendedAction="建议复核风险叙事，不需要作为财务缺失项补录",
        sourceMetricsUsed=["filings", "IR risk disclosure", "symbol risk rules"],
        priority="low",
    )


def _available_resolution_explanation(metric_key: str, fallback: str) -> str:
    if metric_key == "sbcToRevenue":
        return "已由 stockBasedCompensation / revenue 计算。"
    if metric_key == "netDebtToEbitda":
        return "已由净债务 / EBITDA 计算或由结构化数据提供。"
    if metric_key == "interestCoverage":
        return "已由 EBIT / interest expense 计算或由结构化数据提供。"
    return fallback


def _status_from_impact(impact: dict[str, str]) -> str:
    return str(impact.get("resolutionStatus") or "missing")


def _list_from_impact_affects(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return ["ConfidenceOnly"]


def _metric_key_from_name(name: str) -> str:
    return "".join(part.capitalize() if index else part for index, part in enumerate(name.replace("/", " ").replace("-", " ").split()))


def _rating_cap_from_missing_impacts(impacts: list[dict[str, str]]) -> str | None:
    if any(row.get("ratingCapImpact") == "cap_A" for row in impacts):
        return "A"
    return None


def _apply_missing_rating_cap(score: float, impacts: list[dict[str, str]]) -> float:
    if _rating_cap_from_missing_impacts(impacts) == "A":
        return min(score, 84.0)
    return score


def _apply_missing_position_adjustment(max_position: float, impacts: list[dict[str, str]]) -> float:
    if max_position <= 5:
        return max_position
    if any(row.get("impactCategory") == "CRITICAL_RISK" for row in impacts):
        return max(0.0, max_position - 2.5)
    return max_position


def _apply_confidence_action(action: str, data_confidence: str) -> str:
    if data_confidence != "low":
        return action
    if action in {"可小仓分批", "可正常分批"}:
        return "待复核，暂不新增"
    if action in {"回撤买点", "击球区附近"}:
        return "数据待复核"
    return action


def _apply_confidence_score_caps(quality: float, risk: float, data_confidence: str) -> tuple[float, float]:
    if data_confidence != "low":
        return quality, risk
    return min(quality, 74.0), max(risk, 26.0)


def _apply_confidence_position_cap(max_position: float, data_confidence: str, action: str) -> float:
    if data_confidence == "low":
        return 0.0
    return max_position


def _max_portfolio_weight_percent(quality: float, risk: float, data_insufficient: bool, data_confidence: str = "high") -> float:
    if data_insufficient:
        return 0.0
    if risk > 70:
        weight = 5.0
    elif risk > 50:
        weight = 10.0
    elif risk > 25:
        weight = 15.0
    else:
        weight = 20.0

    if quality < 55:
        weight = min(weight, 5.0)
    elif quality < 65:
        weight = min(weight, 10.0)
    elif quality < 75:
        weight = min(weight, 15.0)

    if data_confidence == "low":
        weight = min(weight, 5.0)
    elif data_confidence == "medium":
        weight = min(weight, 15.0)
    return round(weight, 1)


def _missing_data_explanations(impacts: list[dict[str, str]]) -> list[str]:
    explanations = []
    for row in impacts:
        explanations.append(f"{row['metric']}: {row['explanation']}")
    return explanations


def _human_readable_summary(
    context: ScoreContext,
    quality_rating: str,
    entry_rating: str,
    risk_rating: str,
    valuation_status: str,
    action: str,
) -> dict[str, str]:
    ps = _metric(context, "price_to_sales")
    ev_sales = _metric(context, "enterprise_to_revenue")
    pe = _metric(context, "trailing_pe", "pe_ratio")
    p_fcf = _metric(context, "price_to_fcf", "ev_to_fcf")
    fcf_yield = _metric(context, "free_cash_flow_yield")
    drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
    gain_20d = _number(context.technicals.get("gain_20d_pct"))
    ema200_gap = _number(context.technicals.get("pct_above_ema200"))

    valuation_parts = []
    if pe is not None:
        valuation_parts.append(f"GAAP PE 约 {_summary_multiple(pe)}")
    if ps is not None:
        valuation_parts.append(f"P/S 约 {_summary_multiple(ps)}")
    elif ev_sales is not None:
        valuation_parts.append(f"EV/Sales 约 {_summary_multiple(ev_sales)}")
    if p_fcf is not None:
        valuation_parts.append(f"P/FCF 约 {_summary_multiple(p_fcf)}")
    if fcf_yield is not None:
        valuation_parts.append(f"FCF yield 约 {_summary_pct(fcf_yield)}")
    valuation = "，".join(valuation_parts) or "估值数据仍需补齐"
    valuation += f"，当前判断为{valuation_status}。"

    if ema200_gap is not None and ema200_gap < 0:
        technical = "股价仍低于 EMA200，中期趋势未完全修复"
    elif ema200_gap is not None:
        technical = "股价位于 EMA200 上方，中期趋势相对稳定"
    else:
        technical = "EMA200 尚未计算，技术趋势需要刷新价格历史后复核"
    if gain_20d is not None and gain_20d > 15:
        technical += "；20日涨幅仍高，说明短线已经反弹过一波，不能追高。"
    elif drawdown is not None and drawdown <= -30:
        technical += "；距高点回撤较深，买点需要结合趋势修复确认。"
    else:
        technical += "。"

    entry = f"买点评级为{entry_rating}，操作建议为{action}。"
    if drawdown is not None and drawdown <= -30:
        entry += "当前回撤较深，估值接近可观察区。"
    if context.model_type == "SAAS_SOFTWARE":
        entry += "关键 SaaS 经营指标待复核前，暂不提高仓位。"

    risk = f"风险等级为{risk_rating}。"
    if context.model_type == "SAAS_SOFTWARE":
        risk += "主要风险来自增长放缓、SaaS 关键经营指标缺失、AI 替代叙事，以及股价趋势尚未完全修复。"
    elif context.model_type == "POWER_GENERATION":
        risk += "主要风险来自杠杆、商品电价波动、对冲覆盖率和监管变化。"
    elif context.model_type == "CRYPTO_FINANCIAL_INFRA":
        risk += "主要风险来自加密周期、监管变化、交易量波动和估值弹性。"
    elif context.model_type == "PHARMA":
        risk += "主要风险来自竞争格局、美国定价压力、pipeline 和专利风险。"
    else:
        risk += "主要风险需要结合估值、负债、现金流和技术趋势复核。"

    return {
        "valuation": valuation,
        "technical": technical,
        "entry": entry,
        "risk": risk,
        "quality": f"质量评级为{quality_rating}，需结合数据置信度一起看。",
    }


def _summary_multiple(value: float) -> str:
    return f"{value:.1f}x"


def _summary_pct(value: float) -> str:
    pct = value * 100 if abs(value) <= 1 else value
    return f"{pct:.1f}%"


def _has_saas_foundation_data(context: ScoreContext) -> bool:
    has_core_quality = all(
        (
            _has_metric(context, "forward_revenue_growth", "revenue_growth"),
            _has_metric(context, "gross_margin"),
            _has_metric(context, "operating_margin"),
            fcf_margin_metric(context.snapshot).value is not None,
        )
    )
    has_core_valuation = _has_metric(context, "price_to_sales") and (
        _has_metric(context, "price_to_fcf", "ev_to_fcf") or _has_metric(context, "free_cash_flow_yield")
    )
    return bool(has_core_quality and has_core_valuation)


def _confidence_with_missing_impacts(base: str, impacts: list[dict[str, str]]) -> str:
    if any(row.get("impactCategory") == "CRITICAL_QUALITY" for row in impacts):
        return _downgrade_confidence(base)
    return base


def _downgrade_confidence(confidence: str) -> str:
    if confidence == "high":
        return "medium"
    if confidence == "medium":
        return "low"
    return confidence


def _quality_entry_rating(score: float, axis: str, data_insufficient: bool = False, model_type: ModelType | None = None) -> str:
    if data_insufficient:
        return "数据不足"
    letter = _rating_letter(score, axis)

    if axis == "entry":
        suffix = "回撤买点" if score >= 75 else "击球区附近" if score >= 65 else "等回踩" if score >= 55 else "只观察" if score >= 40 else "偏贵"
    else:
        suffix = _quality_suffix(score, model_type)
    return f"{letter} - {suffix}"


def _quality_suffix(score: float, model_type: ModelType | None = None) -> str:
    if score >= 75:
        return "高质量"
    if model_type == "CRYPTO_FINANCIAL_INFRA":
        if score >= 65:
            return "成长较强"
        if score >= 55:
            return "高弹性"
        return "周期观察"
    if model_type in {"POWER_GENERATION", "PHARMA", "SEMICONDUCTOR", "INDUSTRIAL_CAPEX"}:
        if score >= 65:
            return "质量较强"
        if score >= 55:
            return "基本面可用"
    return "稳健" if score >= 55 else "可观察" if score >= 40 else "偏弱"


def _rating_letter(score: float, axis: str) -> str:
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B+"
    if score >= 60:
        return "B"
    if axis == "quality" and score >= 55:
        return "B-"
    if axis == "quality" and score >= 50:
        return "C+"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _risk_rating(score: float, data_insufficient: bool = False) -> str:
    if data_insufficient:
        return "数据不足"
    if score <= 25:
        return "低"
    if score <= 50:
        return "中"
    if score <= 70:
        return "中高"
    return "高"


BUY_ACTIONS = {"可小仓分批", "可正常分批"}
NON_BUY_VALUATION_STATUSES = {"只观察", "偏贵", "极贵"}
MIN_BUY_ENTRY_SCORE = 55.0
MIN_EXACT_BUY_ENTRY_SCORE = 75.0


def _final_action(
    quality: float,
    entry: float,
    risk: float,
    valuation_status: str,
    context: ScoreContext,
    data_insufficient: bool,
    overheat: OverheatResult,
) -> str:
    if data_insufficient:
        return "数据不足，需复核"
    if quality < 40 and risk > 70:
        return "剔除"
    if risk > 70:
        return "财报后复核"
    if valuation_status in {"极贵", "禁止追高"}:
        return "禁止追高"

    base_action = _base_action(quality, entry, risk)
    if (
        context.model_type == "POWER_GENERATION"
        and _symbol(context) == "VST"
        and quality >= 60
        and entry >= MIN_BUY_ENTRY_SCORE
        and risk <= 50
        and valuation_status not in NON_BUY_VALUATION_STATUSES
    ):
        drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
        if drawdown is not None and drawdown <= -25:
            base_action = "可小仓分批"
    if context.model_type == "SAAS_SOFTWARE":
        below_200 = below_ema200_risk(context)
        if risk > 50 and below_200 is not None and below_200 >= 55 and base_action in {"可小仓分批", "可正常分批"}:
            base_action = "只观察"
        elif risk > 25 and base_action == "可正常分批":
            base_action = "可小仓分批"
    if overheat.score >= 80:
        return "禁止追高"
    if overheat.score >= 60 and base_action in {"可小仓分批", "可正常分批", "等回踩"}:
        return "只观察"
    if overheat.score >= 40 and base_action in {"可小仓分批", "可正常分批"}:
        return "等回踩"
    if overheat.score >= 20 and base_action == "可正常分批":
        base_action = "可小仓分批"
    return _guard_action_conflicts(base_action, valuation_status, risk, entry)


def _guard_action_conflicts(action: str, valuation_status: str, risk: float, entry: float) -> str:
    if valuation_status in NON_BUY_VALUATION_STATUSES and action in BUY_ACTIONS:
        return "只观察" if valuation_status == "只观察" else "等回踩"
    if entry < MIN_BUY_ENTRY_SCORE and action in BUY_ACTIONS:
        return "只观察"
    if entry < MIN_EXACT_BUY_ENTRY_SCORE and action in BUY_ACTIONS:
        return "等回踩"
    if risk > 50 and action in BUY_ACTIONS:
        return "等回踩"
    return action


def _base_action(quality: float, entry: float, risk: float) -> str:
    if quality >= 65 and entry >= 65:
        return "可小仓分批" if risk > 50 else "可正常分批"
    if quality >= 65 and entry < 55:
        return "等回踩"
    if entry >= 65 and risk <= 70:
        return "可小仓分批"
    if entry < 40 and risk >= 55:
        return "禁止追高"
    if quality < 40:
        return "只观察"
    return "只观察"


def _max_suggested_position_percent(quality: float, risk: float, action: str, data_insufficient: bool) -> float:
    if data_insufficient or "复核" in str(action) or action in {"剔除", "禁止追高", "只观察", "等回踩"}:
        return 0.0
    if risk > 70:
        max_position = 5.0
    elif risk > 50:
        max_position = 10.0
    elif risk > 25:
        max_position = 15.0
    else:
        max_position = 20.0

    if quality < 55:
        max_position = min(max_position, 5.0)
    if action == "可小仓分批":
        max_position = min(max_position, 5.0)
    elif action == "可正常分批":
        if risk <= 50 and quality >= 65:
            max_position = min(max_position, 15.0)
        else:
            max_position = min(max_position, 5.0)
    elif action == "财报后复核":
        max_position = min(max_position, 3.0)
    elif action in {"只观察", "等回踩"}:
        max_position = min(max_position, 5.0)
    return round(max_position, 1)


def _valuation_status(
    model_type: ModelType,
    entry: float,
    risk: float,
    context: ScoreContext,
    data_insufficient: bool,
) -> str:
    if data_insufficient:
        return "数据不足"
    if risk > 70:
        return "高风险，需复核"
    if entry >= 75:
        if model_type == "POWER_GENERATION":
            return "回撤后有吸引力"
        return "回撤买点" if _number(context.technicals.get("drawdown_from_high_pct")) is not None else "击球区附近"
    if entry >= 65:
        return "击球区附近"
    if entry >= 55:
        return "合理偏便宜"
    if _valuation_extreme_score(context) >= 75 or risk > 80:
        return "极贵"
    if entry < 40:
        return "偏贵"
    return "只观察"


def _apply_special_quality_rules(model_type: ModelType, score: float, context: ScoreContext, data_insufficient: bool) -> float:
    if data_insufficient:
        return score

    if model_type == "POWER_GENERATION":
        adjusted_ebitda_growth = _metric(context, "adjustedEbitdaGrowth", "adjusted_ebitda_growth")
        adjusted_fcf = _adjusted_fcf(context)
        leverage = _net_debt_to_adjusted_ebitda(context)
        if (
            (adjusted_ebitda_growth is None or adjusted_ebitda_growth >= 0)
            and (adjusted_fcf is None or adjusted_fcf > 0)
            and (leverage is None or leverage <= 4)
        ):
            score = max(score, 60)
        symbol = _symbol(context)
        adjusted_ebitda = _adjusted_ebitda(context)
        if symbol == "CEG" and adjusted_ebitda is not None and adjusted_ebitda > 0 and (leverage is None or leverage <= 4):
            score = max(score, 68)
        if symbol in {"VST", "TLN"} and adjusted_fcf is not None and adjusted_fcf > 0 and (leverage is None or leverage <= 4):
            score = max(score, 65)

    if model_type == "SAAS_SOFTWARE":
        fcf_margin = _operating_fcf_margin(context)
        growth = _revenue_growth(context)
        if fcf_margin is not None and fcf_margin > 0.25 and growth is not None and growth > 0.15:
            score = max(score, 65)

    if model_type in {"MEGA_CAP_PLATFORM", "MEDICAL_DEVICE", "PHARMA"}:
        if score >= 60 and _valuation_extreme_score(context) < 80:
            score = max(score, 60)
        if model_type == "PHARMA" and _symbol(context) in {"NVO", "LLY"}:
            score = max(score, 75)

    if model_type == "CRYPTO_FINANCIAL_INFRA" and _symbol(context) == "COIN":
        score = min(score, 59)

    return _clamp(score)


def _apply_saas_quality_constraints(score: float, context: ScoreContext, missing: list[str], fcf_source_type: str) -> float:
    sbc_ratio = _metric(context, "manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio")
    operating_margin = _metric(context, "operating_margin")
    has_retention_or_rpo = any(
        _metric(context, key) is not None
        for key in ("manualNetRetention", "manualRpoGrowth", "manualArrGrowth", "rpo_growth", "crpo_growth", "manualSubscriptionRevenueGrowth")
    )

    if sbc_ratio is not None and sbc_ratio > 0.10:
        score = min(score, 84)
    if operating_margin is not None and operating_margin < 0.15:
        score = min(score, 74)
    if fcf_source_type == "derivedFromMarket":
        score = min(score, 84)
    if not has_retention_or_rpo:
        score = min(score, 84)
    if "calculated FCF Margin" in missing and operating_margin is not None and operating_margin < 0.15:
        score = min(score, 74)
    return _clamp(score)


def _apply_special_entry_rules(model_type: ModelType, score: float, risk: float, context: ScoreContext) -> float:
    if model_type == "SAAS_SOFTWARE":
        ps = _metric(context, "price_to_sales", "enterprise_to_revenue")
        fcf_yield = _metric(context, "free_cash_flow_yield")
        growth = _revenue_growth(context)
        price_to_fcf = _metric(context, "price_to_fcf", "ev_to_fcf")
        if ps is not None and ps > 20 and (fcf_yield is None or fcf_yield < 0.02):
            score = min(score, 45)
        if growth is not None and growth < 0.20 and ps is not None and ps > 8:
            score = min(score, 64)
        if (
            growth is not None
            and 0.15 <= growth <= 0.25
            and ps is not None
            and 7 <= ps <= 9
            and price_to_fcf is not None
            and 20 <= price_to_fcf <= 30
        ):
            score = min(score, 69)

    if model_type == "SEMICONDUCTOR":
        ps = _metric(context, "price_to_sales", "enterprise_to_revenue")
        forward_pe = _metric(context, "forward_pe")
        gain_20d = _number(context.technicals.get("gain_20d_pct"))
        if ps is not None and ps > 25 and forward_pe is not None and forward_pe > 55 and gain_20d is not None and gain_20d > 15:
            score = min(score, 50)

    if model_type in {"AI_INFRA_HIGH_RISK", "SEMICONDUCTOR_CYCLICAL"}:
        fcf = _metric(context, "free_cash_flow", "adjustedFcfBeforeGrowth", "manualAdjustedFcfBeforeGrowth")
        if fcf is not None and fcf < 0:
            score = min(score, 64)

    if risk > 80:
        score = min(score, 64)
    return _clamp(score)


def _apply_saas_risk_constraints(score: float, context: ScoreContext) -> float:
    sbc_ratio = _metric(context, "manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio")
    drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
    below_200 = below_ema200_risk(context)
    ai_disruption = _manual_risk_level(context, "manualAiDisruptionRisk")
    fcf = _metric(context, "free_cash_flow")
    leverage = _net_debt_to_adjusted_ebitda(context)

    if sbc_ratio is not None and sbc_ratio > 0.15:
        score = max(score, 26)
    if below_200 is not None and below_200 >= 55:
        score = max(score, 26)
    if drawdown is not None and drawdown <= -40:
        score = max(score, 26)
    if fcf is not None and fcf < 0:
        score = max(score, 60)
    if leverage is not None and leverage > 3:
        score = max(score, 60)
    if (
        below_200 is not None
        and below_200 >= 55
        and drawdown is not None
        and drawdown <= -40
        and sbc_ratio is not None
        and sbc_ratio > 0.10
        and ai_disruption is not None
        and ai_disruption >= 60
    ):
        score = max(score, 51)
    return _clamp(score)


def _apply_special_risk_rules(model_type: ModelType, score: float, context: ScoreContext) -> float:
    if model_type == "POWER_GENERATION":
        leverage = _net_debt_to_adjusted_ebitda(context)
        adjusted_fcf = _adjusted_fcf(context)
        current_ratio = _metric(context, "current_ratio", "currentRatio")
        valuation_extreme = _valuation_extreme_score(context)
        if leverage is not None and 3 <= leverage <= 4:
            score = max(score, 55)
            score = min(score, 70)
        if (
            (leverage is not None and leverage > 4)
            or (adjusted_fcf is not None and adjusted_fcf < 0)
            or (current_ratio is not None and current_ratio < 0.8)
            or valuation_extreme >= 85
        ):
            score = max(score, 75)

    if model_type == "AI_INFRA_HIGH_RISK":
        fcf = _metric(context, "free_cash_flow")
        leverage = _net_debt_to_adjusted_ebitda(context)
        if (fcf is not None and fcf < 0) and (leverage is None or leverage > 2.5):
            score = max(score, 75)

    if model_type == "CRYPTO_FINANCIAL_INFRA":
        score = max(score, 51)

    return _clamp(score)


def _risk_flags(model_type: ModelType, risk_score: float, context: ScoreContext, risk_reasons: list[str]) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    if model_type == "POWER_GENERATION":
        leverage = _net_debt_to_adjusted_ebitda(context)
        adjusted_fcf = _adjusted_fcf(context)
        if leverage is not None:
            if leverage > 4:
                flags.append(RiskFlag("杠杆高", "high", "净债务/调整后EBITDA 高于 4.0x。"))
            elif leverage >= 3:
                flags.append(RiskFlag("杠杆中高", "medium", "净债务/调整后EBITDA 在 3.0x 到 4.0x 区间。"))
        if adjusted_fcf is not None and adjusted_fcf < 0:
            flags.append(RiskFlag("调整后FCF为负", "high", "调整后 growth capex 前自由现金流为负。"))

    severity = "high" if risk_score > 70 else "medium" if risk_score > 50 else "info"
    if risk_score > 50:
        label = "高风险" if severity == "high" else "风险中高"
        flags.append(RiskFlag(label, severity, "；".join(risk_reasons[:4]) or f"{model_type} 模型风险分较高。"))
    return flags


def _missing_required_groups(context: ScoreContext, groups: tuple[tuple[str, tuple[str, ...]], ...]) -> list[str]:
    missing: list[str] = []
    for label, keys in groups:
        if _metric(context, *keys) is None:
            missing.append(label)
    return missing


def _metric(context: ScoreContext, *keys: str) -> float | None:
    for key in keys:
        if not metric_participates_in_score(context.snapshot, key):
            continue
        value = context.snapshot.get(key)
        if value is None:
            value = context.snapshot.get(_camel_to_snake(key))
        number = _number(value)
        if number is not None:
            return number
    return None


def _has_metric(context: ScoreContext, *keys: str) -> bool:
    return _metric(context, *keys) is not None


def _has_direct_metric(context: ScoreContext, *keys: str) -> bool:
    for key in keys:
        value = context.snapshot.get(key)
        if value is None:
            value = context.snapshot.get(_camel_to_snake(key))
        if _number(value) is not None:
            return True
    return False


def _proxy_assessment(
    context: ScoreContext,
    data_quality_pct: float,
    data_insufficient: bool,
    missing_metric_impacts: list[dict[str, str]] | None = None,
    metric_resolution_statuses: list[dict[str, object]] | None = None,
) -> ProxyAssessment:
    snapshot_confidence = context.snapshot.get("dataConfidence")
    model_type = context.model_type
    missing: list[str] = []
    proxies: list[str] = []

    if model_type == "SAAS_SOFTWARE":
        base_confidence = str(snapshot_confidence) if snapshot_confidence in {"high", "medium", "low"} else _confidence_from_pct(data_quality_pct, data_insufficient)
        unresolved_impacts = [
            row
            for row in (missing_metric_impacts or [])
            if not _is_metric_resolved(str(row.get("metric") or ""), metric_resolution_statuses or [])
        ]
        industry_missing = _industry_missing_from_impacts(unresolved_impacts, metric_resolution_statuses or [])
        return ProxyAssessment(_confidence_with_missing_impacts(base_confidence, unresolved_impacts), "不适用", industry_missing, [])

    if model_type == "POWER_GENERATION":
        if not _has_direct_metric(context, "manualAdjustedEbitda", "adjustedEbitda", "adjusted_ebitda"):
            missing.append("adjusted EBITDA")
            if _has_metric(context, "ebitda"):
                proxies.append("EBITDA")
        if not _has_direct_metric(context, "manualAdjustedFcfBeforeGrowth", "adjustedFcfBeforeGrowth", "adjusted_fcf_before_growth"):
            missing.append("adjusted FCF before growth")
            if _has_metric(context, "free_cash_flow"):
                proxies.append("FCF")
        if not _has_direct_metric(context, "manualHedgeCoverageCurrentYear", "hedgeCoverageCurrentYear", "hedge_coverage_current_year"):
            missing.append("hedge coverage")
        if not _has_direct_metric(context, "generationMix", "manualGenerationAssetQuality"):
            missing.append("generation mix")
        if _has_metric(context, "net_debt_to_ebitda", "total_debt", "netDebtToAdjustedEbitda"):
            proxies.append("net debt")
        if _has_metric(context, "enterprise_value", "market_cap", "price_to_fcf", "enterprise_to_ebitda"):
            proxies.append("valuation")
        return _confidence_from_proxy_state(data_quality_pct, data_insufficient, missing, proxies)

    if model_type == "CRYPTO_FINANCIAL_INFRA":
        symbol = _symbol(context)
        if symbol in {"COIN", "HOOD", "MSTR"}:
            missing.extend(
                [
                    "revenue diversification by segment",
                    "user / asset base quality",
                    "crypto revenue sensitivity",
                    "regulatory exposure",
                ]
            )
            proxies.extend(["revenue growth", "FCF", "balance sheet", "drawdown", "valuation", "symbol risk proxy"])
            return _confidence_from_proxy_state(data_quality_pct, data_insufficient, missing, proxies)

    if model_type == "PHARMA":
        symbol = _symbol(context)
        if symbol in {"NVO", "LLY"}:
            missing.extend(["GLP-1 competition", "US pricing pressure", "pipeline / patent risk"])
            proxies.extend(["revenue growth", "operating margin", "FCF", "forward PE", "symbol pipeline proxy"])
            return _confidence_from_proxy_state(data_quality_pct, data_insufficient, missing, proxies)

    if model_type in {"AI_INFRA_HIGH_RISK", "REGULATED_UTILITIES", "MEDICAL_DEVICE", "BANK_FINANCIAL", "REIT_REAL_ESTATE"}:
        if data_quality_pct < 75:
            return _confidence_from_proxy_state(data_quality_pct, data_insufficient, [], ["public financial proxies"])

    data_confidence = str(snapshot_confidence) if snapshot_confidence in {"high", "medium", "low"} else _confidence_from_pct(data_quality_pct, data_insufficient)
    return ProxyAssessment(data_confidence, "不适用", [], [])


def _industry_missing_from_impacts(
    impacts: list[dict[str, str]],
    resolution_rows: list[dict[str, object]] | None = None,
) -> list[str]:
    return _dedupe(
        [
            str(row.get("metric"))
            for row in impacts
            if row.get("impactCategory") == "CRITICAL_QUALITY" and row.get("metric")
            and not _is_metric_resolved(str(row.get("metric")), resolution_rows or [])
        ]
    )


def _confidence_from_proxy_state(
    data_quality_pct: float,
    data_insufficient: bool,
    missing: list[str],
    proxies: list[str],
) -> ProxyAssessment:
    missing = _dedupe(missing)
    proxies = _dedupe([item for item in proxies if item])
    if data_insufficient:
        data_confidence = "low"
    elif missing:
        data_confidence = "medium" if len(proxies) >= 2 and data_quality_pct >= 45 else "low"
    else:
        data_confidence = _confidence_from_pct(data_quality_pct, False)

    if not proxies:
        proxy_confidence = "不适用"
    elif not missing:
        proxy_confidence = "high"
    elif len(proxies) >= 3:
        proxy_confidence = "medium"
    else:
        proxy_confidence = "low"
    return ProxyAssessment(data_confidence, proxy_confidence, missing, proxies)


def _confidence_from_pct(data_quality_pct: float, data_insufficient: bool) -> str:
    if data_insufficient or data_quality_pct < 45:
        return "low"
    if data_quality_pct < 75:
        return "medium"
    return "high"


def _postprocess_key_risks(model_type: ModelType, key_risks: list[str], proxy_assessment: ProxyAssessment) -> list[str]:
    risks = [risk for risk in key_risks if risk != "drawdown > 40%"]
    if "below EMA200" in risks:
        risks = [risk for risk in risks if risk != "below EMA200"]
        risks.append("股价仍低于EMA200，趋势尚未完全修复")
    if model_type == "PHARMA":
        risks.extend(["GLP-1 competition", "US pricing pressure", "pipeline / patent risk"])
    if model_type == "CRYPTO_FINANCIAL_INFRA":
        risks.extend(["crypto cycle sensitivity", "regulatory exposure"])
    if proxy_assessment.missing_industry_metrics:
        risks.append("行业专属 KPI 未完全披露，当前使用代理指标")
    return _dedupe(risks)[:8]


def _manual_or_metric(context: ScoreContext, manual_key: str, *keys: str) -> float | None:
    return _metric(context, manual_key, _camel_to_snake(manual_key), *keys)


def _revenue_growth(context: ScoreContext) -> float | None:
    return _metric(context, "forward_revenue_growth", "revenue_growth", "manualRevenueGrowth")


def _fcf_margin(context: ScoreContext, include_market_derived: bool = True) -> float | None:
    metric = fcf_margin_metric(context.snapshot)
    if metric.sourceType == "derivedFromMarket" and not include_market_derived:
        return None
    return metric.value


def _operating_fcf_margin(context: ScoreContext) -> float | None:
    return _fcf_margin(context, include_market_derived=False)


def _adjusted_ebitda(context: ScoreContext) -> float | None:
    return _manual_or_metric(context, "manualAdjustedEbitda", "adjustedEbitda", "ebitda")


def _adjusted_fcf(context: ScoreContext) -> float | None:
    return _manual_or_metric(context, "manualAdjustedFcfBeforeGrowth", "adjustedFcfBeforeGrowth", "free_cash_flow")


def _adjusted_fcf_yield(context: ScoreContext) -> float | None:
    return _ratio(_adjusted_fcf(context), _metric(context, "market_cap", "marketCap"))


def _net_debt_to_adjusted_ebitda(context: ScoreContext) -> float | None:
    manual = _manual_or_metric(context, "manualNetDebtToAdjustedEbitda", "netDebtToAdjustedEbitda")
    if manual is not None:
        return manual
    existing = _metric(context, "net_debt_to_ebitda")
    if existing is not None:
        return existing
    total_debt = _metric(context, "total_debt", "totalDebt")
    total_cash = _metric(context, "total_cash", "totalCash")
    ebitda = _adjusted_ebitda(context)
    if total_debt is None or ebitda in {None, 0}:
        return None
    return (total_debt - (total_cash or 0)) / ebitda


def _ev_to_adjusted_ebitda(context: ScoreContext) -> float | None:
    direct = _metric(context, "enterpriseValueToAdjustedEbitda", "enterprise_value_to_adjusted_ebitda", "enterprise_to_ebitda")
    if direct is not None:
        return direct
    return _ratio(_metric(context, "enterprise_value", "enterpriseValue"), _adjusted_ebitda(context))


def _market_cap_to_adjusted_fcf(context: ScoreContext) -> float | None:
    return _ratio(_metric(context, "market_cap", "marketCap"), _adjusted_fcf(context))


def _valuation_extreme_score(context: ScoreContext) -> float:
    checks = [
        _risk_lower_is_better(_metric(context, "forward_pe", "trailing_pe"), low=25, high=70),
        _risk_lower_is_better(_metric(context, "price_to_sales", "enterprise_to_revenue"), low=8, high=25),
        _risk_lower_is_better(_metric(context, "price_to_fcf"), low=25, high=70),
    ]
    available = [value for value in checks if value is not None]
    return sum(available) / len(available) if available else 45.0


def _anti_fomo(context: ScoreContext) -> bool:
    overheat = calculate_overheat_score(
        context.snapshot,
        context.technicals,
        valuation_status="",
        model_type=context.model_type,
        quality_rating="",
    )
    return overheat.score >= 60


def higher_metric(*keys: str, good: float, weak: float) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        return _score_higher_is_better(_metric(context, *keys), weak=weak, good=good)

    return scorer


def lower_metric(*keys: str, good: float, weak: float) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        return _score_lower_is_better(_metric(context, *keys), good=good, weak=weak)

    return scorer


def risk_high_metric(*keys: str, low: float, high: float) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        return _risk_lower_is_better(_metric(context, *keys), low=low, high=high)

    return scorer


def risk_low_metric(*keys: str, good: float, weak: float) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        value = _score_higher_is_better(_metric(context, *keys), weak=weak, good=good)
        return None if value is None else 100 - value

    return scorer


def bool_risk(*keys: str) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        value = _metric(context, *keys)
        if value is None:
            return None
        return 80 if value > 0 else 20

    return scorer


def fcf_margin_score(context: ScoreContext) -> float | None:
    return _score_higher_is_better(_operating_fcf_margin(context), weak=0.02, good=0.25)


def fcf_margin_risk(context: ScoreContext) -> float | None:
    value = _operating_fcf_margin(context)
    if value is None:
        return None
    if value < 0:
        return 90
    return 100 - _score_higher_is_better(value, weak=0.02, good=0.25)


def negative_fcf_risk(context: ScoreContext) -> float | None:
    fcf = _metric(context, "free_cash_flow")
    if fcf is None:
        return None
    if fcf < 0:
        return 90
    return 15


def net_cash_safety_score(context: ScoreContext) -> float | None:
    total_debt = _metric(context, "total_debt", "totalDebt")
    total_cash = _metric(
        context,
        "total_cash",
        "totalCash",
        "cashAndCashEquivalents",
        "cash_and_cash_equivalents",
        "cashAndShortTermInvestments",
        "cash_and_short_term_investments",
        "cashAndCashEquivalentsAtCarryingValue",
        "cashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    )
    if total_debt is None and total_cash is None:
        return None
    if total_debt is None or total_debt <= 0:
        return 90
    if total_cash is None:
        return 45
    return _score_higher_is_better(total_cash / total_debt, weak=0.3, good=1.2)


def leverage_quality_score(context: ScoreContext) -> float | None:
    leverage = _net_debt_to_adjusted_ebitda(context)
    if leverage is None:
        return net_cash_safety_score(context)
    return _score_lower_is_better(leverage, good=1.5, weak=4.0)


def leverage_risk_score(context: ScoreContext) -> float | None:
    leverage = _net_debt_to_adjusted_ebitda(context)
    if leverage is None:
        return None
    return _risk_lower_is_better(leverage, low=2.0, high=4.5)


def adjusted_ebitda_score(context: ScoreContext) -> float | None:
    ebitda = _adjusted_ebitda(context)
    growth = _metric(context, "adjustedEbitdaGrowth", "manualAdjustedEbitdaGrowth")
    if ebitda is None:
        return fcf_or_profitability_score(context) or 55
    base = 65 if ebitda > 0 else 20
    if growth is not None:
        base = (base + _score_higher_is_better(growth, weak=-0.05, good=0.15)) / 2
    return base


def adjusted_fcf_score(context: ScoreContext) -> float | None:
    fcf = _adjusted_fcf(context)
    if fcf is None:
        return fcf_or_profitability_score(context) or 55
    if fcf <= 0:
        return 15
    fcf_yield = _adjusted_fcf_yield(context)
    if fcf_yield is None:
        return 70
    return _score_higher_is_better(fcf_yield, weak=0.03, good=0.10)


def adjusted_fcf_entry(context: ScoreContext) -> float | None:
    fcf_yield = _adjusted_fcf_yield(context)
    if fcf_yield is not None:
        return _score_higher_is_better(fcf_yield, weak=0.03, good=0.10)
    multiple = _market_cap_to_adjusted_fcf(context)
    return _score_lower_is_better(multiple, good=8, weak=22) or fcf_yield_entry(context) or 50


def ev_adjusted_ebitda_entry(context: ScoreContext) -> float | None:
    return _score_lower_is_better(_ev_to_adjusted_ebitda(context), good=7, weak=15) or lower_metric(
        "enterprise_to_revenue",
        "price_to_sales",
        good=3,
        weak=10,
    )(context) or 50


def power_generation_asset_quality_score(context: ScoreContext) -> float | None:
    manual = _score_binary_manual_positive(context, "generationMix", "manualGenerationAssetQuality")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol == "CEG":
        return 82
    if symbol in {"VST", "TLN"}:
        return 70
    if symbol == "NRG":
        return 62
    return 58 if context.model_type == "POWER_GENERATION" else None


def power_hedge_or_cashflow_visibility_score(context: ScoreContext) -> float | None:
    manual = _score_higher_is_better(
        _metric(context, "manualHedgeCoverageCurrentYear", "hedgeCoverageCurrentYear"),
        weak=0.25,
        good=0.75,
    )
    if manual is not None:
        return manual
    fcf = _adjusted_fcf(context)
    leverage = _net_debt_to_adjusted_ebitda(context)
    if fcf is not None:
        base = 62 if fcf > 0 else 25
        if leverage is not None and leverage <= 4:
            base += 8
        return _clamp(base)
    return 52


def power_buyback_or_capital_allocation_score(context: ScoreContext) -> float | None:
    score = _score_higher_is_better(_metric(context, "manualBuybackAmount", "buybackAmount"), weak=0, good=1_000_000_000)
    if score is not None:
        return score
    fcf_yield = _adjusted_fcf_yield(context)
    if fcf_yield is not None:
        return _score_higher_is_better(fcf_yield, weak=0.03, good=0.10)
    return 55


def power_demand_exposure_score(context: ScoreContext) -> float | None:
    score = _score_higher_is_better(
        _metric(context, "dataCenterPowerDemandExposure", "manualDataCenterPowerDemandExposure"),
        weak=0,
        good=0.5,
    )
    if score is not None:
        return score
    symbol = _symbol(context)
    if symbol in {"VST", "CEG", "TLN"}:
        return 70
    return 55


def power_merchant_price_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "commodityPriceExposure", "manualCommodityPriceExposure")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol in {"VST", "TLN", "NRG"}:
        return 58
    if symbol == "CEG":
        return 45
    return 55


def power_regulatory_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "regulatoryRisk", "manualRegulatoryRisk")
    if manual is not None:
        return manual
    return 45 if _symbol(context) in {"CEG", "VST"} else 50


def liquidity_risk_or_neutral_score(context: ScoreContext) -> float | None:
    score = risk_low_metric("current_ratio", "currentRatio", good=1.2, weak=0.8)(context)
    if score is not None:
        return score
    return 45


def fcf_volatility_or_neutral_score(context: ScoreContext) -> float | None:
    score = risk_low_metric("free_cash_flow_growth", good=0.10, weak=-0.10)(context)
    if score is not None:
        return score
    return 45


def power_peer_valuation_score(context: ScoreContext) -> float | None:
    peer = _score_lower_is_better(_metric(context, "manualPeerValuationPercentile"), good=0.35, weak=0.85)
    if peer is not None:
        return peer
    return ev_adjusted_ebitda_entry(context) or adjusted_fcf_entry(context)


def pharma_pipeline_strength_score(context: ScoreContext) -> float | None:
    manual = risk_low_metric("manualPipelineRisk", good=20, weak=80)(context)
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol in {"NVO", "LLY"}:
        return 82
    if symbol in {"MRK", "ABBV"}:
        return 66
    if symbol == "PFE":
        return 55
    return 60 if context.model_type == "PHARMA" else None


def pharma_patent_durability_score(context: ScoreContext) -> float | None:
    manual = risk_low_metric("manualPatentCliffRisk", good=20, weak=80)(context)
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol in {"NVO", "LLY"}:
        return 72
    if symbol in {"ABBV"}:
        return 50
    return 60 if context.model_type == "PHARMA" else None


def pharma_pipeline_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualPipelineRisk")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol in {"NVO", "LLY"}:
        return 35
    if symbol in {"PFE", "ABBV"}:
        return 55
    return 45


def pharma_regulatory_pricing_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "regulatoryRisk", "manualPricingRisk")
    if manual is not None:
        return manual
    return 45 if _symbol(context) in {"NVO", "LLY"} else 52


def pharma_product_concentration_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualProductConcentration")
    if manual is not None:
        return manual
    return 62 if _symbol(context) in {"NVO", "LLY"} else 50


def pharma_patent_cliff_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualPatentCliffRisk")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol in {"NVO", "LLY"}:
        return 40
    if symbol == "ABBV":
        return 65
    return 50


def fcf_or_profitability_score(context: ScoreContext) -> float | None:
    fcf_score = fcf_margin_score(context)
    if fcf_score is not None:
        return fcf_score
    return higher_metric("operating_margin", "profit_margin", good=0.25, weak=0.04)(context)


def balance_sheet_or_neutral_score(context: ScoreContext) -> float | None:
    score = net_cash_safety_score(context)
    if score is not None:
        return score
    leverage = _net_debt_to_adjusted_ebitda(context)
    if leverage is not None:
        leverage_score = _score_lower_is_better(leverage, good=1.5, weak=4.0)
        if leverage_score is not None:
            return leverage_score
    current_ratio = _metric(context, "current_ratio", "currentRatio")
    if current_ratio is not None:
        return _score_higher_is_better(current_ratio, weak=0.8, good=1.5)
    return 55


def balance_sheet_risk_or_neutral_score(context: ScoreContext) -> float | None:
    risk = leverage_risk_score(context)
    if risk is not None:
        return risk
    current_ratio = _metric(context, "current_ratio", "currentRatio")
    if current_ratio is not None:
        return _risk_lower_is_better(1 / current_ratio if current_ratio else None, low=0.65, high=1.25)
    return 45


def capital_allocation_or_neutral_score(context: ScoreContext) -> float | None:
    score = _score_binary_manual_positive(context, "manualBuybackAmount", "manualCapitalAllocation", "buybackAmount")
    if score is not None:
        return score
    return 55


def forward_pe_or_neutral_entry(context: ScoreContext) -> float | None:
    return lower_metric("forward_pe", "trailing_pe", good=22, weak=55)(context) or 50


def crypto_revenue_diversification_score(context: ScoreContext) -> float | None:
    manual = _score_binary_manual_positive(context, "manualRevenueDiversification")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol == "HOOD":
        return 72
    if symbol == "COIN":
        return 58
    if symbol == "MSTR":
        return 25
    return 55


def crypto_user_asset_quality_score(context: ScoreContext) -> float | None:
    manual = _score_binary_manual_positive(context, "manualUserAssetBaseQuality")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol in {"COIN", "HOOD"}:
        return 68
    return 50


def crypto_product_ecosystem_score(context: ScoreContext) -> float | None:
    manual = _score_binary_manual_positive(context, "manualProductEcosystem")
    if manual is not None:
        return manual
    return 68 if _symbol(context) in {"COIN", "HOOD"} else 50


def crypto_cycle_setup_score(context: ScoreContext) -> float | None:
    manual = _score_binary_manual_positive(context, "manualCryptoCycleSetup")
    if manual is not None:
        return manual
    return drawdown_entry_score(context)


def crypto_price_sensitivity_risk(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualCryptoPriceSensitivity")
    if manual is not None:
        return manual
    symbol = _symbol(context)
    if symbol == "MSTR":
        return 88
    if symbol == "COIN":
        return 70
    if symbol == "HOOD":
        return 55
    return 65


def crypto_regulatory_risk_score(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "regulatoryRisk", "manualRegulatoryRisk")
    if manual is not None:
        return manual
    return 68 if _symbol(context) == "COIN" else 58


def crypto_revenue_cyclicality_risk(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualRevenueCyclicality")
    if manual is not None:
        return manual
    return 70 if _symbol(context) == "COIN" else 55


def crypto_customer_asset_risk(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualCustomerAssetRisk")
    if manual is not None:
        return manual
    return 55 if _symbol(context) in {"COIN", "HOOD"} else 65


def crypto_concentration_risk(context: ScoreContext) -> float | None:
    manual = _manual_risk_level(context, "manualConcentrationRisk")
    if manual is not None:
        return manual
    return 58 if _symbol(context) == "COIN" else 45


def drawdown_entry_score(context: ScoreContext) -> float | None:
    drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
    if drawdown is None:
        return None
    if drawdown <= -30:
        return 92
    if drawdown <= -20:
        return 80
    if drawdown <= -10:
        return 66
    if drawdown <= -5:
        return 52
    return 25


def technical_cooling_score(context: ScoreContext) -> float | None:
    rsi = _number(context.technicals.get("rsi14"))
    if rsi is None:
        return None
    if 35 <= rsi <= 55:
        return 85
    if 25 <= rsi < 35 or 55 < rsi <= 65:
        return 65
    if rsi > 75:
        return 20
    return 45


def ema_position_score(ema_key: str) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        price = _number(context.technicals.get("price"))
        ema = _number(context.technicals.get(ema_key))
        if price is None or ema in {None, 0}:
            return None
        pct = (price - ema) / ema * 100
        if pct >= 5:
            return 85
        if pct >= 0:
            return 70
        if pct >= -5:
            return 48
        if pct >= -12:
            return 30
        return 15

    return scorer


def below_ema200_risk(context: ScoreContext) -> float | None:
    price = _number(context.technicals.get("price"))
    ema200 = _number(context.technicals.get("ema200"))
    if price is None or ema200 in {None, 0}:
        return None
    if price < ema200:
        pct = (ema200 - price) / ema200
        return 55 if pct < 0.08 else 72
    return 15


def deep_drawdown_risk(context: ScoreContext) -> float | None:
    drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
    if drawdown is None:
        return None
    if drawdown <= -50:
        return 80
    if drawdown <= -40:
        return 65
    if drawdown <= -25:
        return 45
    return 18


def distance_from_low_score(context: ScoreContext) -> float | None:
    price = _number(context.technicals.get("price"))
    low = _number(context.technicals.get("fifty_two_week_low"))
    if price is None or low in {None, 0}:
        return None
    pct = (price - low) / low * 100
    if pct >= 40:
        return 80
    if pct >= 20:
        return 65
    if pct >= 5:
        return 45
    return 25


def volume_trend_score(context: ScoreContext) -> float | None:
    return higher_metric("volume_trend", "manualVolumeTrend", good=0.15, weak=-0.15)(context)


def trend_confirmation_score(context: ScoreContext) -> float | None:
    ema20 = ema_position_score("ema20")(context)
    ema50 = ema_position_score("ema50")(context)
    ema200 = ema_position_score("ema200")(context)
    values = [value for value in (ema20, ema50, ema200) if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def interest_coverage_score(context: ScoreContext) -> float | None:
    return _score_higher_is_better(_metric(context, "interest_coverage", "manualInterestCoverage"), weak=2.0, good=8.0)


def debt_maturity_pressure_score(context: ScoreContext) -> float | None:
    return risk_low_metric("manualDebtMaturityPressure", "debt_maturity_pressure", good=20, weak=80)(context)


def valuation_extreme_risk(context: ScoreContext) -> float | None:
    return _valuation_extreme_score(context)


def growth_deceleration_risk(context: ScoreContext) -> float | None:
    explicit = _metric(context, "revenue_growth_slowing")
    if explicit is not None:
        return 80 if explicit > 0 else 20
    growth = _revenue_growth(context)
    if growth is None:
        return None
    if growth < 0:
        return 80
    if growth < 0.05:
        return 60
    if growth > 0.20:
        return 20
    return 40


def growth_deceleration_entry(context: ScoreContext) -> float | None:
    risk = growth_deceleration_risk(context)
    return None if risk is None else 100 - risk


def fcf_yield_entry(context: ScoreContext) -> float | None:
    return _score_higher_is_better(_metric(context, "free_cash_flow_yield"), weak=0.015, good=0.06)


def ps_vs_growth_entry(context: ScoreContext) -> float | None:
    ps = _metric(context, "price_to_sales", "enterprise_to_revenue")
    growth = _revenue_growth(context)
    if ps is None or growth is None:
        return None
    growth_pct = max(growth * 100, 1)
    ratio = ps / growth_pct
    return _score_lower_is_better(ratio, good=0.35, weak=1.25)


def cycle_heat_penalty_entry(context: ScoreContext) -> float | None:
    gain = _number(context.technicals.get("gain_20d_pct"))
    drawdown = _number(context.technicals.get("drawdown_from_high_pct"))
    if gain is None and drawdown is None:
        return None
    score = 70
    if gain is not None and gain > 20:
        score -= 35
    elif gain is not None and gain > 10:
        score -= 15
    if drawdown is not None and drawdown > -5:
        score -= 20
    elif drawdown is not None and drawdown <= -20:
        score += 15
    return _clamp(score)


def _score_higher_is_better(value: float | None, weak: float, good: float) -> float | None:
    if value is None:
        return None
    if value <= weak:
        return 20 if value >= 0 else 5
    if value >= good:
        return 92
    return 20 + (value - weak) / (good - weak) * 72


def _score_lower_is_better(value: float | None, good: float, weak: float) -> float | None:
    if value is None:
        return None
    if value <= 0:
        return None
    if value <= good:
        return 90
    if value >= weak:
        return 20
    return 90 - (value - good) / (weak - good) * 70


def _risk_lower_is_better(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    if value <= low:
        return 15
    if value >= high:
        return 90
    return 15 + (value - low) / (high - low) * 75


def _score_binary_manual_positive(context: ScoreContext, *keys: str) -> float | None:
    value = _metric(context, *keys)
    if value is None:
        return None
    return 80 if value > 0 else 35


def _manual_risk_level(context: ScoreContext, *keys: str) -> float | None:
    for key in keys:
        raw = context.snapshot.get(key) or context.snapshot.get(_camel_to_snake(key))
        if raw is None:
            continue
        if isinstance(raw, str):
            text = _normalize(raw)
            if text in {"high", "高", "高风险"}:
                return 85
            if text in {"medium", "中", "中高", "中高风险"}:
                return 58
            if text in {"low", "低", "低风险"}:
                return 20
        number = _number(raw)
        if number is not None:
            return _clamp(number)
    return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _normalize(value: object) -> str:
    return str(value or "").strip().lower()


def _symbol(context: ScoreContext) -> str:
    return str(context.snapshot.get("ticker") or context.snapshot.get("symbol") or "").upper()


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _f(name: str, weight: float, scorer: Callable[[ScoreContext], float | None]) -> Factor:
    return Factor(name, weight, scorer)


def _manual_positive_factor(*keys: str) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        return _score_binary_manual_positive(context, *keys)

    return scorer


def _manual_risk_factor(*keys: str) -> Callable[[ScoreContext], float | None]:
    def scorer(context: ScoreContext) -> float | None:
        return _manual_risk_level(context, *keys)

    return scorer


SAAS_VALUATION = (
    _f("P/S", 16, lower_metric("price_to_sales", good=5, weak=16)),
    _f("EV/Sales", 14, lower_metric("enterprise_to_revenue", "price_to_sales", good=5, weak=16)),
    _f("P/FCF", 18, lower_metric("price_to_fcf", good=22, weak=65)),
    _f("EV/FCF", 14, lower_metric("ev_to_fcf", "price_to_fcf", good=22, weak=65)),
    _f("FCF Yield", 16, fcf_yield_entry),
    _f("PEG", 10, lower_metric("peg_ratio", "peg", good=1.2, weak=3.0)),
    _f("forward revenue multiple", 12, lower_metric("forward_revenue_multiple", "forward_price_to_sales", good=5, weak=16)),
)

SAAS_QUALITY = (
    _f("revenue growth", 14, higher_metric("forward_revenue_growth", "revenue_growth", good=0.20, weak=0.0)),
    _f("subscription revenue growth", 8, higher_metric("manualSubscriptionRevenueGrowth", "subscription_revenue_growth", good=0.20, weak=0.0)),
    _f("gross margin", 13, higher_metric("gross_margin", good=0.75, weak=0.45)),
    _f("GAAP operating margin", 13, higher_metric("operating_margin", good=0.22, weak=0.02)),
    _f("non-GAAP operating margin", 8, higher_metric("manualNonGaapOperatingMargin", "non_gaap_operating_margin", good=0.30, weak=0.08)),
    _f("calculated FCF Margin", 14, fcf_margin_score),
    _f("ROIC", 10, higher_metric("return_on_invested_capital", "return_on_equity", good=0.18, weak=0.04)),
    _f("net retention rate", 8, higher_metric("manualNetRetention", "net_retention_rate", "dbnrr", good=1.20, weak=1.00)),
    _f("RPO / cRPO growth", 8, higher_metric("manualArrGrowth", "manualRpoGrowth", "rpo_growth", "crpo_growth", good=0.18, weak=0.02)),
    _f("large customer growth", 4, higher_metric("manualLargeCustomerGrowth", "large_customer_growth", good=0.20, weak=0.0)),
)

SAAS_BALANCE_SHEET = (
    _f("net debt / EBITDA", 25, leverage_quality_score),
    _f("cash and equivalents", 25, net_cash_safety_score),
    _f("current ratio", 20, higher_metric("current_ratio", good=1.5, weak=0.8)),
    _f("debt maturity pressure", 15, debt_maturity_pressure_score),
    _f("interest coverage", 15, interest_coverage_score),
)

SAAS_TECHNICAL = (
    _f("above / below EMA20", 15, ema_position_score("ema20")),
    _f("above / below EMA50", 15, ema_position_score("ema50")),
    _f("above / below EMA200", 20, ema_position_score("ema200")),
    _f("RSI", 15, technical_cooling_score),
    _f("52-week drawdown", 15, drawdown_entry_score),
    _f("distance from 52-week low", 10, distance_from_low_score),
    _f("volume trend", 5, volume_trend_score),
    _f("trend confirmation", 5, trend_confirmation_score),
)

SAAS_INVESTMENT_RISK = (
    _f("valuation risk", 15, valuation_extreme_risk),
    _f("growth deceleration risk", 12, growth_deceleration_risk),
    _f("SBC / revenue", 10, risk_high_metric("manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio", low=0.06, high=0.18)),
    _f("dilution risk", 8, _manual_risk_factor("manualDilutionRisk")),
    _f("GAAP profitability weakness", 10, risk_low_metric("operating_margin", good=0.22, weak=0.02)),
    _f("below EMA200", 10, below_ema200_risk),
    _f("drawdown > 40%", 8, deep_drawdown_risk),
    _f("acquisition integration risk", 6, _manual_risk_factor("manualAcquisitionIntegrationRisk")),
    _f("AI disruption / seat compression risk", 7, _manual_risk_factor("manualAiDisruptionRisk")),
    _f("customer concentration", 5, _manual_risk_factor("manualCustomerConcentration")),
    _f("negative FCF", 5, negative_fcf_risk),
    _f("high leverage", 4, leverage_risk_score),
)

SAAS_ENTRY = (
    _f("EV/FCF", 25, lower_metric("price_to_fcf", "ev_to_fcf", good=22, weak=65)),
    _f("P/S relative to revenue growth", 20, ps_vs_growth_entry),
    _f("FCF Yield", 15, fcf_yield_entry),
    _f("Drawdown from 52-week high", 15, drawdown_entry_score),
    _f("RSI / technical cooling", 10, technical_cooling_score),
    _f("Growth deceleration penalty", 15, growth_deceleration_entry),
)

SAAS_RISK = SAAS_INVESTMENT_RISK


MODEL_PROFILES: dict[ModelType, ModelProfile] = {
    "SAAS_SOFTWARE": ModelProfile(
        "SAAS_SOFTWARE",
        quality=SAAS_QUALITY,
        entry=SAAS_ENTRY,
        risk=SAAS_RISK,
        required_groups=(
            ("收入增速", ("forward_revenue_growth", "revenue_growth")),
        ),
        data_threshold=40,
    ),
    "MEGA_CAP_PLATFORM": ModelProfile(
        "MEGA_CAP_PLATFORM",
        quality=(
            _f("Revenue Growth", 15, higher_metric("forward_revenue_growth", "revenue_growth", good=0.12, weak=0.00)),
            _f("Operating Margin", 20, higher_metric("operating_margin", good=0.28, weak=0.08)),
            _f("FCF Margin", 20, fcf_margin_score),
            _f("ROIC", 15, higher_metric("return_on_invested_capital", "return_on_equity", good=0.20, weak=0.06)),
            _f("Net Cash / Balance Sheet", 10, net_cash_safety_score),
            _f("Segment strength", 10, _manual_positive_factor("manualSegmentStrength")),
            _f("Buyback discipline", 10, higher_metric("manualBuybackAmount", "buybackAmount", good=10_000_000_000, weak=0)),
        ),
        entry=(
            _f("Forward PE / normalized PE", 20, lower_metric("forward_pe", "trailing_pe", good=22, weak=45)),
            _f("EV/FCF", 25, lower_metric("price_to_fcf", "ev_to_fcf", good=24, weak=55)),
            _f("FCF Yield", 15, fcf_yield_entry),
            _f("Historical valuation percentile", 15, risk_low_metric("valuation_percentile", good=0.35, weak=0.85)),
            _f("Drawdown / technical setup", 15, drawdown_entry_score),
            _f("Capex concern discount", 10, risk_low_metric("manualCapexConcern", good=20, weak=80)),
        ),
        risk=(
            _f("AI capex overbuild risk", 20, _manual_risk_factor("manualCapexConcern")),
            _f("Margin compression", 20, risk_low_metric("operating_margin", good=0.28, weak=0.08)),
            _f("Regulatory risk", 15, _manual_risk_factor("regulatoryRisk", "manualRegulatoryRisk")),
            _f("Valuation overheating", 20, valuation_extreme_risk),
            _f("Growth slowdown", 15, growth_deceleration_risk),
            _f("Segment concentration", 10, _manual_risk_factor("manualSegmentConcentrationRisk")),
        ),
    ),
    "SEMICONDUCTOR": ModelProfile(
        "SEMICONDUCTOR",
        quality=(
            _f("Revenue Growth", 20, higher_metric("forward_revenue_growth", "revenue_growth", good=0.25, weak=-0.05)),
            _f("Gross Margin", 20, higher_metric("gross_margin", good=0.65, weak=0.35)),
            _f("Operating Margin", 15, higher_metric("operating_margin", good=0.28, weak=0.05)),
            _f("FCF Margin", 15, fcf_margin_score),
            _f("Product moat / ecosystem", 10, _manual_positive_factor("manualProductMoat")),
            _f("Customer concentration risk adjustment", 10, risk_low_metric("manualCustomerConcentration", good=20, weak=80)),
            _f("Balance Sheet", 10, net_cash_safety_score),
        ),
        entry=(
            _f("Forward PE", 20, lower_metric("forward_pe", good=25, weak=65)),
            _f("EV/EBITDA", 15, lower_metric("enterprise_to_ebitda", good=14, weak=35)),
            _f("P/S relative to growth", 20, ps_vs_growth_entry),
            _f("FCF Yield", 15, fcf_yield_entry),
            _f("Drawdown from high", 10, drawdown_entry_score),
            _f("RSI / momentum cooling", 10, technical_cooling_score),
            _f("Cycle position", 10, cycle_heat_penalty_entry),
        ),
        risk=(
            _f("Semiconductor cycle risk", 20, _manual_risk_factor("manualSemiconductorCycleRisk")),
            _f("Inventory correction risk", 15, _manual_risk_factor("manualInventoryRisk")),
            _f("Customer concentration", 15, _manual_risk_factor("manualCustomerConcentration")),
            _f("Export control / China risk", 15, _manual_risk_factor("manualExportControlRisk")),
            _f("Valuation extreme", 20, valuation_extreme_risk),
            _f("Margin normalization risk", 15, risk_low_metric("gross_margin", good=0.60, weak=0.35)),
        ),
    ),
    "SEMICONDUCTOR_CYCLICAL": ModelProfile(
        "SEMICONDUCTOR_CYCLICAL",
        quality=(
            _f("Cycle-adjusted margin", 20, higher_metric("manualCycleAdjustedMargin", "operating_margin", good=0.20, weak=-0.05)),
            _f("Balance Sheet", 20, net_cash_safety_score),
            _f("FCF across cycle", 20, fcf_margin_score),
            _f("Competitive position", 15, _manual_positive_factor("manualCompetitivePosition")),
            _f("Revenue recovery", 15, higher_metric("forward_revenue_growth", "revenue_growth", good=0.20, weak=-0.10)),
            _f("Inventory discipline", 10, risk_low_metric("manualInventoryRisk", good=20, weak=80)),
        ),
        entry=(
            _f("Price relative to cycle trough", 20, drawdown_entry_score),
            _f("EV/EBITDA normalized", 20, lower_metric("enterprise_to_ebitda", good=8, weak=18)),
            _f("P/B or replacement value", 15, lower_metric("price_to_book", good=1.3, weak=4)),
            _f("FCF inflection", 15, higher_metric("free_cash_flow_growth", good=0.15, weak=-0.05)),
            _f("Drawdown / technical setup", 15, drawdown_entry_score),
            _f("Cycle heat penalty", 15, cycle_heat_penalty_entry),
        ),
        risk=(
            _f("Downcycle risk", 25, _manual_risk_factor("manualDowncycleRisk")),
            _f("Inventory glut", 20, _manual_risk_factor("manualInventoryRisk")),
            _f("Negative FCF", 20, fcf_margin_risk),
            _f("High leverage", 15, leverage_risk_score),
            _f("Valuation at cycle peak", 20, valuation_extreme_risk),
        ),
    ),
    "AI_INFRA_HIGH_RISK": ModelProfile(
        "AI_INFRA_HIGH_RISK",
        quality=(
            _f("Revenue Growth", 20, higher_metric("forward_revenue_growth", "revenue_growth", good=0.40, weak=0.05)),
            _f("Gross Margin / unit economics", 15, higher_metric("gross_margin", good=0.45, weak=0.10)),
            _f("Contracted backlog", 15, higher_metric("manualBacklogGrowth", good=0.30, weak=0.0)),
            _f("Customer quality", 10, _manual_positive_factor("manualCustomerQuality")),
            _f("Asset quality", 10, _manual_positive_factor("manualAssetQuality")),
            _f("FCF trajectory", 15, fcf_margin_score),
            _f("Balance Sheet", 15, balance_sheet_or_neutral_score),
        ),
        entry=(
            _f("EV/Sales vs growth", 20, ps_vs_growth_entry),
            _f("EV/EBITDA if positive", 15, lower_metric("enterprise_to_ebitda", good=12, weak=35)),
            _f("Price vs 52-week high", 15, drawdown_entry_score),
            _f("FCF inflection probability", 20, higher_metric("free_cash_flow_growth", good=0.20, weak=-0.10)),
            _f("Dilution-adjusted valuation", 15, risk_low_metric("manualDilutionRisk", good=20, weak=80)),
            _f("Technical cooling", 15, technical_cooling_score),
        ),
        risk=(
            _f("Negative FCF", 20, fcf_margin_risk),
            _f("High leverage", 20, leverage_risk_score),
            _f("Customer concentration", 15, _manual_risk_factor("manualCustomerConcentration")),
            _f("Dilution risk", 15, _manual_risk_factor("manualDilutionRisk")),
            _f("Execution risk", 15, _manual_risk_factor("manualExecutionRisk")),
            _f("Valuation extreme", 15, valuation_extreme_risk),
        ),
        data_threshold=40,
    ),
    "POWER_GENERATION": ModelProfile(
        "POWER_GENERATION",
        quality=(
            _f("Adjusted EBITDA level and growth", 20, adjusted_ebitda_score),
            _f("Adjusted FCF before growth", 20, adjusted_fcf_score),
            _f("Generation asset quality", 15, power_generation_asset_quality_score),
            _f("Hedge / cash-flow visibility", 15, power_hedge_or_cashflow_visibility_score),
            _f("Balance Sheet / leverage", 15, balance_sheet_or_neutral_score),
            _f("Buyback and capital allocation", 10, power_buyback_or_capital_allocation_score),
            _f("Data center / power demand exposure", 5, power_demand_exposure_score),
        ),
        entry=(
            _f("Market Cap / Adjusted FCF before growth", 25, adjusted_fcf_entry),
            _f("EV / Adjusted EBITDA", 20, ev_adjusted_ebitda_entry),
            _f("FCF Yield", 20, adjusted_fcf_entry),
            _f("Drawdown from 52-week high", 15, drawdown_entry_score),
            _f("Technical setup", 10, technical_cooling_score),
            _f("Valuation vs peers", 10, power_peer_valuation_score),
        ),
        risk=(
            _f("Net Debt / Adjusted EBITDA", 25, balance_sheet_risk_or_neutral_score),
            _f("Merchant power price exposure", 20, power_merchant_price_risk_score),
            _f("Regulatory / political risk", 15, power_regulatory_risk_score),
            _f("Commodity / fuel exposure", 15, power_merchant_price_risk_score),
            _f("FCF volatility", 15, fcf_volatility_or_neutral_score),
            _f("Liquidity", 10, liquidity_risk_or_neutral_score),
        ),
        data_threshold=28,
    ),
    "REGULATED_UTILITIES": ModelProfile(
        "REGULATED_UTILITIES",
        quality=(
            _f("Regulated earnings stability", 25, _manual_positive_factor("manualRegulatedEarningsStability")),
            _f("Dividend coverage", 15, _manual_positive_factor("manualDividendCoverage")),
            _f("Rate base growth", 15, higher_metric("manualRateBaseGrowth", "earnings_growth", good=0.07, weak=0.0)),
            _f("Balance Sheet", 20, leverage_quality_score),
            _f("FCF after dividends", 10, fcf_margin_score),
            _f("Regulatory environment", 15, risk_low_metric("regulatoryRisk", "manualRegulatoryRisk", good=20, weak=80)),
        ),
        entry=(
            _f("Forward PE", 20, lower_metric("forward_pe", good=16, weak=28)),
            _f("Dividend yield vs history", 20, _manual_positive_factor("manualDividendYieldVsHistory")),
            _f("Yield spread vs 10Y Treasury", 20, _manual_positive_factor("manualYieldSpread")),
            _f("P/B", 10, lower_metric("price_to_book", good=1.5, weak=3.0)),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Interest rate sensitivity", 15, risk_low_metric("manualInterestRateSensitivity", good=20, weak=80)),
        ),
        risk=(
            _f("High debt", 25, leverage_risk_score),
            _f("Dividend coverage weakness", 20, _manual_risk_factor("manualDividendCoverageWeakness")),
            _f("Rate case / regulatory risk", 20, _manual_risk_factor("regulatoryRisk", "manualRegulatoryRisk")),
            _f("Rising rates risk", 20, _manual_risk_factor("manualInterestRateSensitivity")),
            _f("Capex funding pressure", 15, _manual_risk_factor("manualCapexFundingPressure")),
        ),
        required_groups=(("监管资产/股息覆盖需人工补充", ("manualRegulatedEarningsStability", "manualDividendCoverage")),),
    ),
    "MEDICAL_DEVICE": ModelProfile(
        "MEDICAL_DEVICE",
        quality=(
            _f("Revenue Growth", 20, higher_metric("forward_revenue_growth", "revenue_growth", good=0.12, weak=0.0)),
            _f("Gross Margin", 15, higher_metric("gross_margin", good=0.65, weak=0.35)),
            _f("Operating Margin", 15, higher_metric("operating_margin", good=0.22, weak=0.04)),
            _f("FCF Margin", 15, fcf_margin_score),
            _f("Installed base / recurring revenue", 15, _manual_positive_factor("manualInstalledBase", "manualRecurringRevenue")),
            _f("ROIC", 10, higher_metric("return_on_invested_capital", good=0.15, weak=0.04)),
            _f("Balance Sheet", 10, net_cash_safety_score),
        ),
        entry=(
            _f("Forward PE", 20, lower_metric("forward_pe", good=28, weak=60)),
            _f("EV/FCF", 25, lower_metric("price_to_fcf", "ev_to_fcf", good=25, weak=60)),
            _f("PEG / growth-adjusted valuation", 20, ps_vs_growth_entry),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Technical setup", 10, technical_cooling_score),
            _f("Historical valuation percentile", 10, risk_low_metric("valuation_percentile", good=0.35, weak=0.85)),
        ),
        risk=(
            _f("Procedure volume slowdown", 20, _manual_risk_factor("manualProcedureVolumeSlowdown")),
            _f("Product recall / FDA risk", 20, _manual_risk_factor("manualFdaRisk")),
            _f("Valuation extreme", 20, valuation_extreme_risk),
            _f("Margin pressure", 15, risk_low_metric("operating_margin", good=0.22, weak=0.04)),
            _f("Competition", 15, _manual_risk_factor("manualCompetitionRisk")),
            _f("Balance Sheet", 10, leverage_risk_score),
        ),
    ),
    "PHARMA": ModelProfile(
        "PHARMA",
        quality=(
            _f("Core product revenue growth", 20, higher_metric("forward_revenue_growth", "revenue_growth", good=0.12, weak=-0.02)),
            _f("Operating Margin", 15, higher_metric("operating_margin", good=0.28, weak=0.08)),
            _f("FCF / profitability", 15, fcf_or_profitability_score),
            _f("Pipeline strength", 20, pharma_pipeline_strength_score),
            _f("Patent durability", 15, pharma_patent_durability_score),
            _f("Balance Sheet", 10, balance_sheet_or_neutral_score),
            _f("Capital allocation", 5, capital_allocation_or_neutral_score),
        ),
        entry=(
            _f("Forward PE", 25, forward_pe_or_neutral_entry),
            _f("EV/FCF", 20, lower_metric("price_to_fcf", "ev_to_fcf", good=22, weak=55)),
            _f("PEG", 15, ps_vs_growth_entry),
            _f("FCF Yield", 15, fcf_yield_entry),
            _f("Drawdown", 10, drawdown_entry_score),
            _f("Pipeline optionality", 15, pharma_pipeline_strength_score),
        ),
        risk=(
            _f("Patent cliff", 25, pharma_patent_cliff_risk_score),
            _f("Pipeline failure risk", 20, pharma_pipeline_risk_score),
            _f("Regulatory / pricing risk", 20, pharma_regulatory_pricing_risk_score),
            _f("Product concentration", 15, pharma_product_concentration_risk_score),
            _f("Valuation extreme", 10, valuation_extreme_risk),
            _f("Leverage", 10, balance_sheet_risk_or_neutral_score),
        ),
        data_threshold=28,
    ),
    "CRYPTO_FINANCIAL_INFRA": ModelProfile(
        "CRYPTO_FINANCIAL_INFRA",
        quality=(
            _f("Revenue diversification", 20, crypto_revenue_diversification_score),
            _f("FCF generation", 20, fcf_or_profitability_score),
            _f("User / asset base quality", 15, crypto_user_asset_quality_score),
            _f("Balance Sheet", 15, balance_sheet_or_neutral_score),
            _f("Regulatory positioning", 15, lambda context: 100 - crypto_regulatory_risk_score(context)),
            _f("Product ecosystem", 15, crypto_product_ecosystem_score),
        ),
        entry=(
            _f("Normalized PE / EV/EBITDA", 20, lower_metric("forward_pe", "enterprise_to_ebitda", good=18, weak=45)),
            _f("EV/Sales vs cycle", 15, lower_metric("enterprise_to_revenue", "price_to_sales", good=4, weak=14)),
            _f("FCF Yield", 20, fcf_yield_entry),
            _f("Price vs crypto cycle", 15, crypto_cycle_setup_score),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Technical setup", 15, technical_cooling_score),
        ),
        risk=(
            _f("Crypto price sensitivity", 25, crypto_price_sensitivity_risk),
            _f("Regulatory risk", 25, crypto_regulatory_risk_score),
            _f("Revenue cyclicality", 20, crypto_revenue_cyclicality_risk),
            _f("Customer asset risk", 10, crypto_customer_asset_risk),
            _f("Valuation extreme", 10, valuation_extreme_risk),
            _f("Concentration risk", 10, crypto_concentration_risk),
        ),
        data_threshold=28,
    ),
    "BANK_FINANCIAL": ModelProfile(
        "BANK_FINANCIAL",
        quality=(
            _f("ROE", 20, higher_metric("return_on_equity", good=0.14, weak=0.06)),
            _f("ROA", 10, higher_metric("return_on_assets", good=0.012, weak=0.003)),
            _f("Net Interest Margin", 15, higher_metric("manualNim", good=0.035, weak=0.015)),
            _f("CET1 / capital ratio", 20, higher_metric("manualCet1Ratio", good=0.12, weak=0.08)),
            _f("Credit quality", 20, risk_low_metric("manualCreditLossRatio", good=0.003, weak=0.02)),
            _f("Deposit stability", 15, _manual_positive_factor("manualDepositStability")),
        ),
        entry=(
            _f("P/TBV", 30, lower_metric("manualPriceToTangibleBook", "price_to_book", good=1.0, weak=2.4)),
            _f("P/E normalized", 20, lower_metric("forward_pe", "trailing_pe", good=10, weak=18)),
            _f("Dividend yield and coverage", 10, _manual_positive_factor("manualDividendCoverage")),
            _f("ROE vs P/B relationship", 20, _manual_positive_factor("manualRoeVsPb")),
            _f("Drawdown", 10, drawdown_entry_score),
            _f("Rate cycle setup", 10, _manual_positive_factor("manualRateCycleSetup")),
        ),
        risk=(
            _f("Credit loss ratio", 25, risk_high_metric("manualCreditLossRatio", low=0.005, high=0.03)),
            _f("Deposit outflow risk", 20, _manual_risk_factor("manualDepositOutflowRisk")),
            _f("CRE / loan concentration", 15, _manual_risk_factor("manualLoanConcentrationRisk")),
            _f("Duration risk", 15, _manual_risk_factor("manualDurationRisk")),
            _f("Capital ratio weakness", 15, risk_low_metric("manualCet1Ratio", good=0.12, weak=0.08)),
            _f("Regulatory risk", 10, _manual_risk_factor("regulatoryRisk", "manualRegulatoryRisk")),
        ),
        required_groups=(
            ("CET1 资本率", ("manualCet1Ratio",)),
            ("NIM", ("manualNim",)),
            ("信用损失率", ("manualCreditLossRatio",)),
        ),
    ),
    "REIT_REAL_ESTATE": ModelProfile(
        "REIT_REAL_ESTATE",
        quality=(
            _f("AFFO growth", 20, higher_metric("manualAffoGrowth", good=0.06, weak=-0.02)),
            _f("Occupancy", 15, higher_metric("manualOccupancy", good=0.95, weak=0.86)),
            _f("Same-store NOI growth", 15, higher_metric("manualSameStoreNoiGrowth", good=0.04, weak=-0.02)),
            _f("Lease duration / tenant quality", 15, _manual_positive_factor("manualTenantQuality")),
            _f("Balance Sheet", 20, leverage_quality_score),
            _f("Dividend coverage", 15, _manual_positive_factor("manualDividendCoverage")),
        ),
        entry=(
            _f("P/AFFO", 25, lower_metric("manualPriceToAffo", good=15, weak=28)),
            _f("Dividend yield vs history", 20, _manual_positive_factor("manualDividendYieldVsHistory")),
            _f("NAV discount / premium", 20, _manual_positive_factor("manualNavDiscount")),
            _f("Spread vs Treasury", 15, _manual_positive_factor("manualYieldSpread")),
            _f("Drawdown", 10, drawdown_entry_score),
            _f("Debt maturity wall", 10, risk_low_metric("manualDebtMaturityWallRisk", good=20, weak=80)),
        ),
        risk=(
            _f("Net Debt / EBITDA", 25, leverage_risk_score),
            _f("Refinancing risk", 20, _manual_risk_factor("manualRefinancingRisk")),
            _f("Occupancy decline", 15, risk_low_metric("manualOccupancy", good=0.95, weak=0.86)),
            _f("Dividend coverage risk", 15, _manual_risk_factor("manualDividendCoverageWeakness")),
            _f("Rate sensitivity", 15, _manual_risk_factor("manualInterestRateSensitivity")),
            _f("Tenant concentration", 10, _manual_risk_factor("manualTenantConcentration")),
        ),
        required_groups=(("AFFO", ("manualAffo",)), ("入住率", ("manualOccupancy",))),
    ),
    "AUTO_HARDWARE": ModelProfile(
        "AUTO_HARDWARE",
        quality=(
            _f("Unit growth", 15, higher_metric("manualUnitGrowth", "revenue_growth", good=0.20, weak=-0.05)),
            _f("Gross Margin", 20, higher_metric("gross_margin", good=0.25, weak=0.08)),
            _f("Operating Margin", 15, higher_metric("operating_margin", good=0.12, weak=-0.05)),
            _f("FCF", 20, fcf_margin_score),
            _f("Balance Sheet", 15, net_cash_safety_score),
            _f("Brand / ecosystem", 10, _manual_positive_factor("manualBrandEcosystem")),
            _f("Manufacturing execution", 5, _manual_positive_factor("manualManufacturingExecution")),
        ),
        entry=(
            _f("Forward PE if profitable", 20, lower_metric("forward_pe", good=25, weak=70)),
            _f("EV/Sales", 15, lower_metric("enterprise_to_revenue", "price_to_sales", good=2, weak=10)),
            _f("EV/EBITDA", 15, lower_metric("enterprise_to_ebitda", good=12, weak=35)),
            _f("FCF Yield", 20, fcf_yield_entry),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Cycle / price war adjustment", 15, risk_low_metric("manualPriceWarRisk", good=20, weak=80)),
        ),
        risk=(
            _f("Price war risk", 25, _manual_risk_factor("manualPriceWarRisk")),
            _f("Margin compression", 20, risk_low_metric("gross_margin", good=0.25, weak=0.08)),
            _f("Inventory risk", 15, _manual_risk_factor("manualInventoryRisk")),
            _f("Capex burden", 15, _manual_risk_factor("manualCapexBurden")),
            _f("Demand cyclicality", 15, _manual_risk_factor("manualDemandCyclicality")),
            _f("Valuation extreme", 10, valuation_extreme_risk),
        ),
    ),
    "CONSUMER_INTERNET_ECOMMERCE": ModelProfile(
        "CONSUMER_INTERNET_ECOMMERCE",
        quality=(
            _f("Revenue / GMV growth", 15, higher_metric("forward_revenue_growth", "revenue_growth", "manualGmvGrowth", good=0.18, weak=0.0)),
            _f("Gross Margin", 10, higher_metric("gross_margin", good=0.45, weak=0.15)),
            _f("Operating Margin", 20, higher_metric("operating_margin", good=0.15, weak=-0.03)),
            _f("FCF Margin", 20, fcf_margin_score),
            _f("Take rate / monetization", 15, _manual_positive_factor("manualTakeRate")),
            _f("Logistics efficiency", 10, _manual_positive_factor("manualLogisticsEfficiency")),
            _f("Balance Sheet", 10, net_cash_safety_score),
        ),
        entry=(
            _f("EV/FCF", 25, lower_metric("price_to_fcf", "ev_to_fcf", good=22, weak=60)),
            _f("P/S relative to growth", 20, ps_vs_growth_entry),
            _f("Forward PE", 15, lower_metric("forward_pe", good=25, weak=60)),
            _f("FCF Yield", 15, fcf_yield_entry),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Competitive discount", 10, risk_low_metric("manualCompetitionRisk", good=20, weak=80)),
        ),
        risk=(
            _f("Margin pressure", 20, risk_low_metric("operating_margin", good=0.15, weak=-0.03)),
            _f("Competition / subsidy war", 20, _manual_risk_factor("manualCompetitionRisk")),
            _f("Regulatory risk", 15, _manual_risk_factor("regulatoryRisk", "manualRegulatoryRisk")),
            _f("FCF deterioration", 20, fcf_margin_risk),
            _f("Valuation extreme", 15, valuation_extreme_risk),
            _f("FX / geopolitical risk", 10, _manual_risk_factor("manualGeopoliticalRisk")),
        ),
    ),
    "INDUSTRIAL_CAPEX": ModelProfile(
        "INDUSTRIAL_CAPEX",
        quality=(
            _f("Revenue Growth", 15, higher_metric("forward_revenue_growth", "revenue_growth", good=0.12, weak=-0.02)),
            _f("Backlog growth", 20, higher_metric("manualBacklogGrowth", good=0.15, weak=0.0)),
            _f("Operating Margin", 15, higher_metric("operating_margin", good=0.18, weak=0.05)),
            _f("FCF conversion", 20, fcf_margin_score),
            _f("ROIC", 15, higher_metric("return_on_invested_capital", good=0.15, weak=0.04)),
            _f("Balance Sheet", 10, net_cash_safety_score),
            _f("Aftermarket / recurring revenue", 5, _manual_positive_factor("manualRecurringRevenue")),
        ),
        entry=(
            _f("Forward PE", 20, lower_metric("forward_pe", good=22, weak=42)),
            _f("EV/EBITDA", 20, lower_metric("enterprise_to_ebitda", good=12, weak=24)),
            _f("EV/FCF", 20, lower_metric("price_to_fcf", "ev_to_fcf", good=22, weak=50)),
            _f("Backlog-adjusted growth", 15, higher_metric("manualBacklogGrowth", good=0.15, weak=0.0)),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Cycle heat penalty", 10, cycle_heat_penalty_entry),
        ),
        risk=(
            _f("Order slowdown", 25, _manual_risk_factor("manualOrderSlowdownRisk")),
            _f("Margin compression", 20, risk_low_metric("operating_margin", good=0.18, weak=0.05)),
            _f("Supply chain / working capital", 15, _manual_risk_factor("manualWorkingCapitalRisk")),
            _f("Valuation extreme", 20, valuation_extreme_risk),
            _f("Cycle peak risk", 15, _manual_risk_factor("manualCyclePeakRisk")),
            _f("Leverage", 5, leverage_risk_score),
        ),
    ),
    "ENERGY_COMMODITY": ModelProfile(
        "ENERGY_COMMODITY",
        quality=(
            _f("FCF at current commodity price", 25, fcf_margin_score),
            _f("Balance Sheet", 20, leverage_quality_score),
            _f("Production quality / reserves", 15, _manual_positive_factor("manualProductionQuality")),
            _f("Capital discipline", 15, _manual_positive_factor("manualCapitalDiscipline")),
            _f("Cost position", 15, _manual_positive_factor("manualCostPosition")),
            _f("Shareholder return", 10, _manual_positive_factor("manualShareholderReturn")),
        ),
        entry=(
            _f("EV/EBITDA", 25, lower_metric("enterprise_to_ebitda", good=5, weak=12)),
            _f("FCF Yield", 25, fcf_yield_entry),
            _f("P/B or NAV discount", 15, lower_metric("price_to_book", good=1.2, weak=3.0)),
            _f("Commodity cycle position", 20, _manual_positive_factor("manualCommodityCyclePosition")),
            _f("Drawdown", 15, drawdown_entry_score),
        ),
        risk=(
            _f("Commodity price exposure", 30, _manual_risk_factor("commodityPriceExposure", "manualCommodityPriceExposure")),
            _f("Leverage", 20, leverage_risk_score),
            _f("Capex inflation", 15, _manual_risk_factor("manualCapexInflationRisk")),
            _f("Political / ESG risk", 15, _manual_risk_factor("manualPoliticalRisk")),
            _f("Reserve decline", 10, _manual_risk_factor("manualReserveDeclineRisk")),
            _f("Valuation at cycle peak", 10, valuation_extreme_risk),
        ),
    ),
    "GENERIC": ModelProfile(
        "GENERIC",
        quality=(
            _f("Free Cash Flow", 25, fcf_margin_score),
            _f("Operating Margin", 20, higher_metric("operating_margin", good=0.20, weak=0.03)),
            _f("Revenue Growth", 20, higher_metric("forward_revenue_growth", "revenue_growth", good=0.15, weak=0.0)),
            _f("ROIC / ROE", 20, higher_metric("return_on_invested_capital", "return_on_equity", good=0.15, weak=0.04)),
            _f("Balance Sheet", 15, net_cash_safety_score),
        ),
        entry=(
            _f("Forward PE", 20, lower_metric("forward_pe", "trailing_pe", good=22, weak=55)),
            _f("P/S relative to growth", 20, ps_vs_growth_entry),
            _f("EV/FCF", 20, lower_metric("price_to_fcf", "ev_to_fcf", good=22, weak=60)),
            _f("FCF Yield", 15, fcf_yield_entry),
            _f("Drawdown", 15, drawdown_entry_score),
            _f("Technical cooling", 10, technical_cooling_score),
        ),
        risk=(
            _f("Negative FCF / FCF pressure", 20, fcf_margin_risk),
            _f("High leverage", 20, leverage_risk_score),
            _f("Valuation extreme", 25, valuation_extreme_risk),
            _f("Growth slowdown", 20, growth_deceleration_risk),
            _f("Technical overheat", 15, lambda context: 85 if _anti_fomo(context) else 20),
        ),
        data_threshold=35,
    ),
}
