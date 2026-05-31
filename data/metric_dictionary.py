from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDefinition:
    metric_key: str
    snapshot_key: str
    display_name: str
    aliases: tuple[str, ...]
    preferred_sources: tuple[str, ...]
    unit_hint: str = "percent"


SOURCE_PRIORITY = {
    "MANUAL_CORRECTION": 110,
    "MANUAL": 100,
    "CALCULATED": 95,
    "FMP": 90,
    "SEC_XBRL": 85,
    "IR_RELEASE": 80,
    "SEC_8K": 78,
    "IR_PRESENTATION": 70,
    "SEC_10Q": 62,
    "SEC_10K": 60,
    "FMP_TRANSCRIPT": 40,
}

CONFIDENCE_PRIORITY = {"high": 3, "medium": 2, "low": 1}


SAAS_SOFTWARE_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        metric_key="sbcToRevenue",
        snapshot_key="sbc_ratio",
        display_name="SBC / revenue",
        aliases=("stock-based compensation", "SBC", "share-based compensation"),
        preferred_sources=("CALCULATED", "SEC_XBRL", "FMP"),
    ),
    MetricDefinition(
        metric_key="netDebt",
        snapshot_key="net_debt",
        display_name="Net debt",
        aliases=("net debt",),
        preferred_sources=("CALCULATED", "FMP"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="netDebtToEbitda",
        snapshot_key="net_debt_to_ebitda",
        display_name="Net debt / EBITDA",
        aliases=("net debt to EBITDA", "net debt / EBITDA"),
        preferred_sources=("CALCULATED", "FMP"),
        unit_hint="multiple",
    ),
    MetricDefinition(
        metric_key="interestCoverage",
        snapshot_key="interest_coverage",
        display_name="Interest coverage",
        aliases=("interest coverage",),
        preferred_sources=("CALCULATED", "FMP"),
        unit_hint="multiple",
    ),
    MetricDefinition(
        metric_key="fcfMargin",
        snapshot_key="fcf_margin",
        display_name="FCF margin",
        aliases=("free cash flow margin", "FCF margin"),
        preferred_sources=("CALCULATED", "SEC_XBRL", "FMP"),
    ),
    MetricDefinition(
        metric_key="directFcfMargin",
        snapshot_key="fcf_margin",
        display_name="FCF利润率（直接计算）",
        aliases=("free cash flow margin", "FCF margin"),
        preferred_sources=("CALCULATED", "SEC_XBRL", "FMP"),
    ),
    MetricDefinition(
        metric_key="impliedFcfMargin",
        snapshot_key="implied_fcf_margin",
        display_name="估算FCF利润率",
        aliases=("implied FCF margin",),
        preferred_sources=("FMP",),
    ),
    MetricDefinition(
        metric_key="drawdownFrom52WeekHigh",
        snapshot_key="drawdown_from_high_pct",
        display_name="52-week drawdown",
        aliases=("52-week drawdown", "distance to 52-week high"),
        preferred_sources=("CALCULATED",),
    ),
    MetricDefinition(
        metric_key="return20d",
        snapshot_key="gain_20d_pct",
        display_name="20-day return",
        aliases=("20-day return",),
        preferred_sources=("CALCULATED",),
    ),
    MetricDefinition(
        metric_key="ema20",
        snapshot_key="ema20",
        display_name="EMA20",
        aliases=("EMA20",),
        preferred_sources=("CALCULATED",),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="ema50",
        snapshot_key="ema50",
        display_name="EMA50",
        aliases=("EMA50",),
        preferred_sources=("CALCULATED",),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="ema200",
        snapshot_key="ema200",
        display_name="EMA200",
        aliases=("EMA200",),
        preferred_sources=("CALCULATED",),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="rsi14",
        snapshot_key="rsi14",
        display_name="RSI14",
        aliases=("RSI14",),
        preferred_sources=("CALCULATED",),
        unit_hint="number",
    ),
    MetricDefinition(
        metric_key="subscriptionRevenueGrowth",
        snapshot_key="subscription_revenue_growth",
        display_name="订阅收入增速",
        aliases=(
            "subscription revenue was",
            "subscription revenues were",
            "subscription revenue of",
            "subscription revenues of",
            "subscription revenue growth",
            "subscription revenues increased",
            "subscription revenues grew",
            "subscription revenue year-over-year",
            "subscription revenue increased",
            "subscription revenue grew",
        ),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="subscriptionRevenueGrowthReported",
        snapshot_key="subscription_revenue_growth",
        display_name="订阅收入增速（reported YoY）",
        aliases=("subscription revenue", "subscription revenues"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="subscriptionRevenueGrowthConstantCurrency",
        snapshot_key="subscription_revenue_growth_constant_currency",
        display_name="订阅收入增速（constant currency）",
        aliases=("subscription revenue", "subscription revenues", "constant currency"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="nonGaapOperatingMargin",
        snapshot_key="non_gaap_operating_margin",
        display_name="Non-GAAP经营利润率",
        aliases=(
            "non-GAAP operating margin",
            "non GAAP operating margin",
            "non-GAAP income from operations margin",
            "non-GAAP operating income margin",
        ),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION"),
    ),
    MetricDefinition(
        metric_key="rpoGrowth",
        snapshot_key="rpo_growth",
        display_name="RPO增速",
        aliases=(
            "RPO",
            "remaining performance obligations",
            "RPO growth",
            "remaining performance obligations growth",
            "RPO grew",
            "remaining performance obligations grew",
        ),
        preferred_sources=("SEC_XBRL", "IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="rpoGrowthReported",
        snapshot_key="rpo_growth",
        display_name="RPO增速（reported YoY）",
        aliases=("RPO", "remaining performance obligations"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="rpoGrowthConstantCurrency",
        snapshot_key="rpo_growth_constant_currency",
        display_name="RPO增速（constant currency）",
        aliases=("RPO", "remaining performance obligations", "constant currency"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="cRpoGrowth",
        snapshot_key="crpo_growth",
        display_name="cRPO增速",
        aliases=(
            "cRPO",
            "current remaining performance obligations",
            "cRPO growth",
            "current remaining performance obligations growth",
            "current RPO grew",
            "current remaining performance obligations grew",
        ),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="cRpoGrowthReported",
        snapshot_key="crpo_growth",
        display_name="cRPO增速（reported YoY）",
        aliases=("cRPO", "current remaining performance obligations", "current RPO"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="cRpoGrowthConstantCurrency",
        snapshot_key="crpo_growth_constant_currency",
        display_name="cRPO增速（constant currency）",
        aliases=("cRPO", "current remaining performance obligations", "current RPO", "constant currency"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="netRetentionRate",
        snapshot_key="net_retention_rate",
        display_name="净留存率",
        aliases=(
            "net retention rate",
            "net revenue retention",
            "dollar-based net retention",
            "dollar based net retention",
            "DBNRR",
        ),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="largeCustomerGrowth",
        snapshot_key="large_customer_growth",
        display_name="大客户增长",
        aliases=(
            "customers with more than",
            "customers over",
            "customers contributing more than",
            "large customers",
            "customers with annual contract value",
            "customers with ACV",
        ),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="operatingCashFlowMargin",
        snapshot_key="operating_cash_flow_margin",
        display_name="经营现金流利润率",
        aliases=("GAAP net cash provided by operating activities", "operating cash flow margin"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION"),
    ),
    MetricDefinition(
        metric_key="nonGaapFcfMargin",
        snapshot_key="non_gaap_fcf_margin",
        display_name="Non-GAAP FCF利润率",
        aliases=("non-GAAP free cash flow margin", "non-GAAP FCF margin"),
        preferred_sources=("IR_RELEASE", "SEC_8K", "IR_PRESENTATION"),
    ),
    MetricDefinition(
        metric_key="sbcRatio",
        snapshot_key="sbc_ratio",
        display_name="SBC / revenue",
        aliases=("stock-based compensation", "SBC", "share-based compensation"),
        preferred_sources=("SEC_XBRL", "FMP"),
    ),
    MetricDefinition(
        metric_key="peg",
        snapshot_key="peg_ratio",
        display_name="PEG",
        aliases=("PEG",),
        preferred_sources=("FMP",),
        unit_hint="multiple",
    ),
    MetricDefinition(
        metric_key="forwardRevenueMultiple",
        snapshot_key="forward_revenue_multiple",
        display_name="Forward revenue multiple",
        aliases=("forward revenue multiple", "NTM revenue multiple"),
        preferred_sources=("FMP",),
        unit_hint="multiple",
    ),
)


CRYPTO_FINANCIAL_INFRA_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        metric_key="hoodAuc",
        snapshot_key="hood_auc",
        display_name="AUC",
        aliases=("AUC", "assets under custody", "assets under management", "AUM"),
        preferred_sources=("SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="hoodNetDeposits",
        snapshot_key="hood_net_deposits",
        display_name="Net deposits",
        aliases=("net deposits", "net deposit"),
        preferred_sources=("SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="hoodTransactionRevenue",
        snapshot_key="hood_transaction_revenue",
        display_name="Transaction revenue",
        aliases=("transaction revenue", "transaction revenues", "transaction-based revenue", "transaction-based revenues"),
        preferred_sources=("SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="hoodInterestRevenue",
        snapshot_key="hood_interest_revenue",
        display_name="Interest revenue",
        aliases=("interest revenue", "interest revenues", "net interest revenue", "net interest revenues"),
        preferred_sources=("SEC_10Q", "SEC_10K", "SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="hoodSubscriptionGoldRevenue",
        snapshot_key="hood_subscription_gold_revenue",
        display_name="Subscription / Gold revenue",
        aliases=(
            "subscription revenue",
            "subscription revenues",
            "subscription and services revenue",
            "subscription and services revenues",
            "Robinhood Gold revenue",
            "Gold revenue",
            "Gold revenues",
            "subscription / Gold revenue",
        ),
        preferred_sources=("SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="hoodNormalizedEarnings",
        snapshot_key="hood_normalized_earnings",
        display_name="Normalized earnings",
        aliases=(
            "normalized earnings",
            "normalized net income",
            "adjusted net income",
            "net income, adjusted",
            "net income adjusted",
            "adjusted earnings",
            "non-GAAP net income",
            "net income excluding",
        ),
        preferred_sources=("SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="hoodNormalizedEbitda",
        snapshot_key="hood_normalized_ebitda",
        display_name="Normalized EBITDA",
        aliases=("normalized EBITDA", "adjusted EBITDA"),
        preferred_sources=("SHAREHOLDER_LETTER", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"),
        unit_hint="money",
    ),
)


AI_CLOUD_INFRA_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        metric_key="aiCloudContractedBacklog",
        snapshot_key="contracted_backlog",
        display_name="Contracted backlog / RPO",
        aliases=("contracted backlog", "revenue backlog", "backlog", "remaining contracted backlog"),
        preferred_sources=("SEC_10Q", "SEC_10K", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "SHAREHOLDER_LETTER"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="aiCloudRpo",
        snapshot_key="remaining_performance_obligations",
        display_name="Remaining performance obligations",
        aliases=("remaining performance obligations", "RPO", "contracted RPO"),
        preferred_sources=("SEC_10Q", "SEC_10K", "EARNINGS_RELEASE", "IR_RELEASE", "SEC_8K", "IR_PRESENTATION", "SHAREHOLDER_LETTER"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="aiCloudGpuFleetCapacity",
        snapshot_key="gpu_fleet_capacity",
        display_name="GPU fleet / capacity",
        aliases=("GPU fleet", "GPU capacity", "installed GPUs", "contracted GPU capacity", "GPU count", "GPU accelerators"),
        preferred_sources=("IR_PRESENTATION", "EARNINGS_RELEASE", "IR_RELEASE", "SHAREHOLDER_LETTER", "SEC_10Q", "SEC_10K"),
        unit_hint="count",
    ),
    MetricDefinition(
        metric_key="aiCloudUtilization",
        snapshot_key="utilization",
        display_name="Utilization",
        aliases=("utilization", "GPU utilization", "fleet utilization", "capacity utilization"),
        preferred_sources=("IR_PRESENTATION", "EARNINGS_RELEASE", "IR_RELEASE", "SHAREHOLDER_LETTER", "FMP_TRANSCRIPT"),
    ),
    MetricDefinition(
        metric_key="aiCloudCapexCommitments",
        snapshot_key="capex_commitments",
        display_name="Capex commitments",
        aliases=("capex commitments", "purchase commitments", "remaining capex commitments", "contracted capex commitments"),
        preferred_sources=("SEC_10Q", "SEC_10K", "EARNINGS_RELEASE", "IR_RELEASE", "IR_PRESENTATION", "SHAREHOLDER_LETTER"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="aiCloudCapexIntensity",
        snapshot_key="capex_intensity",
        display_name="Capex intensity",
        aliases=("capex intensity", "capex / revenue", "capex to revenue"),
        preferred_sources=("SEC_10Q", "SEC_10K", "FMP", "EARNINGS_RELEASE"),
    ),
    MetricDefinition(
        metric_key="aiCloudNetDebt",
        snapshot_key="net_debt",
        display_name="Net debt",
        aliases=("net debt", "net indebtedness"),
        preferred_sources=("SEC_10Q", "SEC_10K", "FMP"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="aiCloudDebtMaturity",
        snapshot_key="debt_maturity",
        display_name="Debt maturity",
        aliases=("debt maturity", "debt maturities", "maturity schedule", "nearest debt maturity"),
        preferred_sources=("SEC_10Q", "SEC_10K"),
        unit_hint="text",
    ),
    MetricDefinition(
        metric_key="aiCloudInterestBurden",
        snapshot_key="interest_burden",
        display_name="Interest burden",
        aliases=("interest burden", "interest expense", "interest coverage"),
        preferred_sources=("SEC_10Q", "SEC_10K", "FMP"),
        unit_hint="money",
    ),
    MetricDefinition(
        metric_key="aiCloudCustomerConcentration",
        snapshot_key="customer_concentration",
        display_name="Customer concentration",
        aliases=("customer concentration", "largest customer", "major customer", "top customer", "top customer revenue share"),
        preferred_sources=("SEC_10K", "SEC_10Q", "EARNINGS_RELEASE", "IR_RELEASE", "SHAREHOLDER_LETTER"),
    ),
    MetricDefinition(
        metric_key="aiCloudNvidiaSupplyExposure",
        snapshot_key="nvidia_supply_exposure",
        display_name="Nvidia supply exposure",
        aliases=("Nvidia supply exposure", "GPU supplier concentration", "supplier concentration", "Nvidia purchase commitment"),
        preferred_sources=("SEC_10K", "SEC_10Q", "IR_PRESENTATION", "EARNINGS_RELEASE", "SHAREHOLDER_LETTER"),
        unit_hint="qualitative",
    ),
    MetricDefinition(
        metric_key="aiCloudHyperscalerExposure",
        snapshot_key="hyperscaler_exposure",
        display_name="Hyperscaler exposure",
        aliases=("hyperscaler exposure", "hyperscaler customer", "cloud customer", "anchor tenant"),
        preferred_sources=("SEC_10K", "SEC_10Q", "IR_PRESENTATION", "EARNINGS_RELEASE", "SHAREHOLDER_LETTER"),
        unit_hint="qualitative",
    ),
)


MODEL_METRIC_DICTIONARY = {
    "SAAS_SOFTWARE": SAAS_SOFTWARE_METRICS,
    "CRYPTO_FINANCIAL_INFRA": CRYPTO_FINANCIAL_INFRA_METRICS,
    "AI_INFRA_HIGH_RISK": AI_CLOUD_INFRA_METRICS,
    "AI_CLOUD_INFRA": AI_CLOUD_INFRA_METRICS,
}


def metric_definitions_for_model(model_type: str) -> tuple[MetricDefinition, ...]:
    return MODEL_METRIC_DICTIONARY.get(model_type, ())


def metric_definition_by_key(metric_key: str) -> MetricDefinition | None:
    for definitions in MODEL_METRIC_DICTIONARY.values():
        for definition in definitions:
            if definition.metric_key == metric_key:
                return definition
    return None
