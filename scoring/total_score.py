from __future__ import annotations

from dataclasses import dataclass

from indicators.technicals import calculate_technical_score
from scoring.risk_flags import RiskFlag
from scoring.sector_models import SectorScore, score_stock_by_model
from scoring.signals import TradingSignal, build_trading_signals


@dataclass(frozen=True)
class ScoreResult:
    quality_score: float
    growth_score: float
    valuation_score: float
    technical_score: float
    balance_sheet_risk_score: float
    catalyst_score: float
    total_score: float
    rating: str
    value_zone: str
    risk_flags: list[RiskFlag]
    missing_data: list[str]
    trading_signals: list[TradingSignal]
    scoring_model: str = "GENERIC"
    entry_score: float = 0.0
    risk_score: float = 0.0
    quality_rating: str = ""
    entry_rating: str = ""
    risk_rating: str = ""
    valuation_status: str = ""
    action: str = ""
    key_positives: list[str] | None = None
    key_risks: list[str] | None = None
    data_quality_pct: float = 100.0
    data_insufficient: bool = False
    overheat_score: float = 0.0
    overheat_status: str = "非过热"
    overheat_action: str = "正常评估"
    overheat_recommendation: str = "回到行业模型正常评估"
    overheat_reasons: list[str] | None = None
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
    hard_missing_fields: list[str] | None = None
    not_disclosed_fields: list[str] | None = None
    not_applicable_fields: list[str] | None = None
    proxy_used_fields: list[str] | None = None
    confidence_penalty_reasons: list[str] | None = None
    model_fit_notes: list[str] | None = None
    missing_metric_impacts: list[dict[str, str]] | None = None
    missing_data_explanation: list[str] | None = None
    rating_cap: str | None = None
    metric_resolution_statuses: list[dict[str, object]] | None = None
    missing_data_summary: dict[str, object] | None = None
    human_readable_summary: dict[str, str] | None = None
    active_risk_drivers: list[str] | None = None

    @property
    def modelType(self) -> str:
        return self.scoring_model

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
        return self.key_positives or []

    @property
    def keyNegativeDrivers(self) -> list[str]:
        return self.key_risks or []

    @property
    def missingIndustryMetrics(self) -> list[str]:
        return self.missing_industry_metrics or []

    @property
    def proxyMetricsUsed(self) -> list[str]:
        return self.proxy_metrics_used or []

    @property
    def hardMissingFields(self) -> list[str]:
        return self.hard_missing_fields or []

    @property
    def notDisclosedFields(self) -> list[str]:
        return self.not_disclosed_fields or []

    @property
    def notApplicableFields(self) -> list[str]:
        return self.not_applicable_fields or []

    @property
    def proxyUsedFields(self) -> list[str]:
        return self.proxy_used_fields or []

    @property
    def confidencePenaltyReasons(self) -> list[str]:
        return self.confidence_penalty_reasons or []

    @property
    def modelFitNotes(self) -> list[str]:
        return self.model_fit_notes or []

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


def calculate_total_score(snapshot: dict, technicals: dict) -> ScoreResult:
    sector_score = score_stock_by_model(snapshot, technicals)
    technical = calculate_technical_score(technicals)
    signal_valuation_score = sector_score.entry_score / 4
    trading_signals = build_trading_signals(
        technicals,
        signal_valuation_score,
        technical,
        sector_score.risk_flags,
        overheat=_overheat_result_like(sector_score),
    )
    total = _total_decision_score(sector_score, technical)

    return ScoreResult(
        quality_score=sector_score.quality_score,
        growth_score=_growth_proxy(snapshot),
        valuation_score=sector_score.entry_score,
        technical_score=technical,
        balance_sheet_risk_score=max(0.0, 100.0 - sector_score.risk_score),
        catalyst_score=_catalyst_proxy(sector_score),
        total_score=total,
        rating=_overall_rating(total, sector_score),
        value_zone=sector_score.value_zone,
        risk_flags=sector_score.risk_flags,
        missing_data=sector_score.missing_data,
        trading_signals=trading_signals,
        scoring_model=sector_score.model_type,
        entry_score=sector_score.entry_score,
        risk_score=sector_score.risk_score,
        quality_rating=sector_score.quality_rating,
        entry_rating=sector_score.entry_rating,
        risk_rating=sector_score.risk_rating,
        valuation_status=sector_score.valuation_status,
        action=sector_score.action,
        key_positives=sector_score.key_positives,
        key_risks=sector_score.key_risks,
        data_quality_pct=sector_score.data_quality_pct,
        data_insufficient=sector_score.data_insufficient,
        overheat_score=sector_score.overheat_score,
        overheat_status=sector_score.overheat_status,
        overheat_action=sector_score.overheat_action,
        overheat_recommendation=sector_score.overheat_recommendation,
        overheat_reasons=sector_score.overheat_reasons,
        valuation_module_score=sector_score.valuation_module_score or sector_score.entry_score,
        quality_module_score=sector_score.quality_module_score or sector_score.quality_score,
        balance_sheet_module_score=sector_score.balance_sheet_module_score,
        technical_module_score=sector_score.technical_module_score,
        investment_risk_score=sector_score.investment_risk_score or sector_score.risk_score,
        fcf_margin_source_type=sector_score.fcf_margin_source_type,
        fcf_margin_note=sector_score.fcf_margin_note,
        max_suggested_position_percent=sector_score.max_suggested_position_percent,
        max_portfolio_weight_percent=sector_score.max_portfolio_weight_percent,
        current_add_limit_percent=sector_score.current_add_limit_percent,
        data_confidence=sector_score.data_confidence,
        proxy_confidence=sector_score.proxy_confidence,
        missing_industry_metrics=sector_score.missing_industry_metrics,
        proxy_metrics_used=sector_score.proxy_metrics_used,
        hard_missing_fields=sector_score.hard_missing_fields,
        not_disclosed_fields=sector_score.not_disclosed_fields,
        not_applicable_fields=sector_score.not_applicable_fields,
        proxy_used_fields=sector_score.proxy_used_fields,
        confidence_penalty_reasons=sector_score.confidence_penalty_reasons,
        model_fit_notes=sector_score.model_fit_notes,
        missing_metric_impacts=sector_score.missing_metric_impacts,
        missing_data_explanation=sector_score.missing_data_explanation,
        rating_cap=sector_score.rating_cap,
        metric_resolution_statuses=sector_score.metric_resolution_statuses,
        missing_data_summary=sector_score.missing_data_summary,
        human_readable_summary=sector_score.human_readable_summary,
        active_risk_drivers=sector_score.active_risk_drivers,
    )


def _total_decision_score(sector_score: SectorScore, technical_score: float) -> float:
    technical_pct = max(0.0, min(100.0, technical_score * 10))
    total = (
        sector_score.quality_score * 0.42
        + sector_score.entry_score * 0.33
        + (100 - sector_score.risk_score) * 0.20
        + technical_pct * 0.05
    )
    if sector_score.data_insufficient:
        total = min(total, 54.0)
    return round(max(0.0, min(100.0, total)), 1)


def _overall_rating(total: float, sector_score: SectorScore) -> str:
    if sector_score.data_insufficient:
        return "数据不足 - 需复核"
    if sector_score.risk_score > 70 and sector_score.quality_score < 40:
        return "D - 剔除"
    if total >= 85:
        return "A+ - 高质量"
    if total >= 75:
        return "A - 高质量"
    if total >= 65:
        return "B+ - 稳健"
    if total >= 55:
        return "B - 稳健"
    if total >= 40:
        return "C - 只观察"
    return "D - 偏弱"


def _growth_proxy(snapshot: dict) -> float:
    value = snapshot.get("forward_revenue_growth")
    if value is None:
        value = snapshot.get("revenue_growth")
    try:
        growth = float(value)
    except (TypeError, ValueError):
        return 50.0
    if growth >= 0.20:
        return 90.0
    if growth <= 0:
        return 25.0
    return round(25 + growth / 0.20 * 65, 1)


def _catalyst_proxy(sector_score: SectorScore) -> float:
    positives = len(sector_score.key_positives)
    risks = len(sector_score.key_risks)
    return round(max(0.0, min(100.0, 50 + positives * 6 - risks * 4)), 1)


def _overheat_result_like(sector_score: SectorScore):
    from scoring.overheat import OverheatResult

    return OverheatResult(
        score=sector_score.overheat_score,
        status=sector_score.overheat_status,
        action=sector_score.overheat_action,
        recommendation=sector_score.overheat_recommendation,
        reasons=sector_score.overheat_reasons,
    )
