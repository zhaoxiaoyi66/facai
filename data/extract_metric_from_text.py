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
COUNT_PATTERN = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+|-?\d+(?:\.\d+)?)(?![\w.])")
HOOD_MONEY_ROW_BOUNDARY_PATTERN = re.compile(
    r"\b("
    r"transaction-based revenues?|transaction revenues?|net interest revenues?|interest revenues?|"
    r"other revenues?|total net revenues?|adjusted ebitda|normalized ebitda|"
    r"adjusted net income|non[- ]gaap net income|net income excluding|"
    r"assets under custody|total platform assets|auc|net deposits?|"
    r"subscription and services revenues?|robinhood gold subscription revenue|gold revenues?|"
    r"funded customers|adjusted operating expenses"
    r")\b",
    re.IGNORECASE,
)


def extractMetricFromText(text: str, metric_definition: MetricDefinition, confidence: str = "medium") -> ExtractedMetric | None:
    if not text:
        return None

    normalized_text = _normalize_whitespace(text)
    ai_cloud_candidate = _extract_ai_cloud_special_metric(normalized_text, metric_definition, confidence)
    if ai_cloud_candidate or _ai_cloud_special_metric_only(metric_definition.metric_key):
        return ai_cloud_candidate

    lowered = normalized_text.lower()
    candidates: list[ExtractedMetric] = []

    for alias in metric_definition.aliases:
        alias_lower = alias.lower()
        start = 0
        while True:
            index = lowered.find(alias_lower, start)
            if index < 0:
                break
            if not _alias_matches_at(lowered, alias_lower, index):
                start = index + len(alias_lower)
                continue
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
            value_window = _value_window_for_alias(post_alias_window, metric_definition.metric_key, metric_definition.unit_hint)
            value = _extract_value(value_window, metric_definition.unit_hint, scale_context)
            if not value and metric_definition.unit_hint != "money":
                value = _extract_value(window, metric_definition.unit_hint, scale_context)
            if value:
                number, unit = value
                evidence_window = _candidate_evidence_window(window, value_window, metric_definition.unit_hint)
                valid, _reason = validate_extracted_metric_candidate(metric_definition.metric_key, evidence_window, number)
                if not valid:
                    start = index + len(alias_lower)
                    continue
                candidates.append(
                    ExtractedMetric(
                        metric_key=metric_definition.metric_key,
                        value=number,
                        unit=unit,
                        extracted_text=evidence_window.strip(),
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
    ai_cloud_valid, ai_cloud_reason = _validate_ai_cloud_metric_candidate(canonical_key, text, candidate_value)
    if not ai_cloud_valid:
        return False, ai_cloud_reason
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


def _validate_ai_cloud_metric_candidate(metric_key: str, text: str, candidate_value: float | None) -> tuple[bool, str]:
    token = _normalized_metric_token(metric_key)
    if not token.startswith("aicloud"):
        return True, "valid"

    if token == "aicloudgpufleetcapacity":
        has_gpu_capacity_context = bool(
            re.search(
                r"\b(?:gpu|gpus|accelerators?)\b.{0,80}\b(?:fleet|capacity|installed|deployed|count)\b|"
                r"\b(?:fleet|capacity|installed|deployed|count)\b.{0,80}\b(?:gpu|gpus|accelerators?)\b",
                text,
            )
        )
        has_gpu_count = bool(re.search(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:gpu|gpus|gpu accelerators|accelerators)\b", text))
        if not has_gpu_capacity_context or not has_gpu_count:
            return False, "GPU capacity candidate lacks an explicit GPU count."
        if re.search(r"\b(?:mw|gw|megawatts?|gigawatts?|borrowing capacity|debt|notes?|capex|capital expenditures?)\b", text):
            return False, "GPU capacity candidate looks like power, debt, or capex rather than GPU count."

    if token == "aicloudrpo":
        if not re.search(r"\bremaining performance obligations?\b|\brpo\b", text):
            return False, "RPO candidate lacks explicit remaining performance obligations wording."
        if re.search(
            r"\brevenue backlog\b|\bcontracted backlog\b|\bbookings?\b|plus.{0,120}other amounts|"
            r"other amounts.{0,120}future periods|subject to the satisfaction of delivery and availability",
            text,
        ):
            return False, "RPO candidate is a broader backlog disclosure, not pure RPO."
        if re.search(r"\b(?:our\s+)?revenue\s+(?:was|were|of)\s+\$", text):
            return False, "RPO candidate picked up revenue, not remaining performance obligations."
        if not re.search(r"(?:remaining performance obligations?|rpo).{0,120}\$\s?\d|\$\s?\d.{0,120}(?:remaining performance obligations?|rpo)", text):
            return False, "RPO candidate lacks an explicit monetary RPO value."
        if re.search(r"\bcorporate bonds?|commercial paper|marketable securities|general corporate purposes\b", text):
            return False, "RPO candidate is from corporate finance context, not performance obligations."

    if token == "aicloudcontractedbacklog":
        if not re.search(r"\b(?:contracted backlog|revenue backlog|remaining contracted backlog|backlog)\b", text):
            return False, "Backlog candidate lacks explicit backlog wording."
        if re.search(r"\b(?:sales pipeline|opportunity pipeline|total addressable market|future demand|market opportunity)\b", text):
            return False, "Backlog candidate looks like pipeline or market opportunity rather than contracted backlog."

    if token == "aicloudcustomerconcentration":
        has_concentration_context = bool(
            re.search(
                r"\b(customer concentration|largest customer|top customer|customer [a-z]\b.{0,80}\b(?:accounted for|represented)|"
                r"(?:accounted for|represented).{0,80}\b(?:revenue|accounts receivable))\b",
                text,
            )
        )
        if not has_concentration_context:
            return False, "Customer concentration candidate lacks explicit concentration disclosure."
        if candidate_value is not None and candidate_value <= 0.1005 and re.search(r"\b(?:no other customer\b.{0,80})?(?:accounted for|representing|represented)?\s*10\s?(?:%|percent)\s+or\s+more\b", text):
            return False, "Customer concentration candidate is only the 10% disclosure threshold."
        if re.search(r"\b(customer wins?|customer demand|customer agreement|customer relationship|strategic customer)\b", text):
            return False, "Customer concentration candidate looks like customer news, not concentration disclosure."

    if token == "aicloudnvidiasupplyexposure":
        has_hard_supply_context = bool(
            re.search(r"\bnvidia\b.{0,120}\b(?:supplier|supply|purchase commitments?|vendor|gpus?)\b|\b(?:supplier|vendor|purchase commitments?|gpus?)\b.{0,120}\bnvidia\b", text)
            or re.search(r"\b(?:supplier concentration|vendor concentration)\b.{0,120}\b(?:gpus?|nvidia|purchase commitments?)\b", text)
        )
        if not has_hard_supply_context:
            return False, "Nvidia supply exposure candidate is not a hard supply dependency disclosure."

    if token == "aicloudhyperscalerexposure":
        has_contract_context = bool(
            re.search(
                r"\b(?:openai|meta|microsoft|azure|google|amazon|aws|oracle|hyperscaler)\b.{0,160}"
                r"\b(?:committed|commitment|master services agreement|order form|significant customer|top customer|customer concentration)\b|"
                r"\b(?:committed|commitment|master services agreement|order form|significant customer|top customer|customer concentration)\b.{0,160}"
                r"\b(?:openai|meta|microsoft|azure|google|amazon|aws|oracle|hyperscaler)\b",
                text,
            )
        )
        if not has_contract_context:
            return False, "Hyperscaler exposure candidate lacks customer contract or concentration context."

    return True, "valid"


def _ai_cloud_special_metric_only(metric_key: str) -> bool:
    return _normalized_metric_token(metric_key) in {
        "aicloudrpo",
        "aicloudcustomerconcentration",
        "aiclouddebtmaturity",
        "aicloudinterestburden",
        "aicloudnvidiasupplyexposure",
        "aicloudhyperscalerexposure",
    }


def _extract_ai_cloud_special_metric(text: str, metric_definition: MetricDefinition, confidence: str) -> ExtractedMetric | None:
    token = _normalized_metric_token(metric_definition.metric_key)
    if not token.startswith("aicloud"):
        return None
    if token == "aicloudrpo":
        return _extract_ai_cloud_rpo(text, metric_definition.metric_key, confidence)
    if token == "aicloudcustomerconcentration":
        return _extract_ai_cloud_customer_concentration(text, metric_definition.metric_key, confidence)
    if token == "aiclouddebtmaturity":
        return _extract_ai_cloud_debt_maturity(text, metric_definition.metric_key, confidence)
    if token == "aicloudinterestburden":
        return _extract_ai_cloud_interest_burden(text, metric_definition.metric_key, confidence)
    if token == "aicloudnvidiasupplyexposure":
        return _extract_ai_cloud_nvidia_supply_exposure(text, metric_definition.metric_key, confidence)
    if token == "aicloudhyperscalerexposure":
        return _extract_ai_cloud_hyperscaler_exposure(text, metric_definition.metric_key, confidence)
    return None


def _extract_ai_cloud_rpo(text: str, metric_key: str, confidence: str) -> ExtractedMetric | None:
    patterns = (
        r"\b(?:remaining performance obligations?|rpo)\b.{0,220}\$\s?(\d+(?:\.\d+)?)\s?(billion|million|bn|mm|m)?",
        r"\$\s?(\d+(?:\.\d+)?)\s?(billion|million|bn|mm|m)?[^.]{0,120}\b(?:unsatisfied\s+)?(?:remaining performance obligations?|rpo)\b",
    )
    candidates: list[ExtractedMetric] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            evidence = _sentence_window(text, match.start(), match.end(), before=30, after=220)
            valid, _reason = validate_extracted_metric_candidate(metric_key, evidence, None)
            if not valid:
                continue
            amount = float(match.group(1)) * _money_unit_multiplier((match.group(2) or "").lower(), evidence)
            candidates.append(ExtractedMetric(metric_key, amount, "usd", evidence, confidence))
    return _best_candidate(candidates, metric_key)


def _extract_ai_cloud_customer_concentration(text: str, metric_key: str, confidence: str) -> ExtractedMetric | None:
    table_match = re.search(
        r"(Significant Customers.{0,420}?Customer A\s+(\d+(?:\.\d+)?)\s?%.{0,120}?Customer B\s+(\d+(?:\.\d+)?)\s?%)",
        text,
        flags=re.IGNORECASE,
    )
    if table_match:
        evidence = _sentence_window(text, table_match.start(), table_match.end(), before=80, after=160)
        value = float(table_match.group(2)) / 100
        valid, _reason = validate_extracted_metric_candidate(metric_key, evidence, value)
        if valid:
            return ExtractedMetric(metric_key, value, "percent", evidence, confidence)

    patterns = (
        r"\b(?:largest customer|top customer|customer a)\b.{0,120}\b(?:accounted for|represented|recognized).{0,120}?(\d+(?:\.\d+)?)\s?%",
        r"\b(?:accounted for|represented|recognized).{0,120}?(\d+(?:\.\d+)?)\s?%.{0,120}\b(?:largest customer|top customer|customer a)\b",
        r"\btop customer revenue share\b.{0,80}?(\d+(?:\.\d+)?)\s?%",
    )
    candidates: list[ExtractedMetric] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = float(match.group(1)) / 100
            evidence = _sentence_window(text, match.start(), match.end(), before=180, after=220)
            valid, _reason = validate_extracted_metric_candidate(metric_key, evidence, value)
            if valid:
                candidates.append(ExtractedMetric(metric_key, value, "percent", evidence, confidence))
    return _best_candidate(candidates, metric_key)


def _extract_ai_cloud_debt_maturity(text: str, metric_key: str, confidence: str) -> ExtractedMetric | None:
    debt_table = re.search(r"(?:\b10\.\s*)?Debt\b.{0,200}?(?:total debt obligations are as follows|total debt obligations|debt obligations)", text, flags=re.IGNORECASE)
    if debt_table:
        evidence_offset = debt_table.start()
        search_text = text[debt_table.start() : min(len(text), debt_table.end() + 1600)]
    else:
        generic = re.search(r"\b(?:debt maturity|debt maturities|maturity schedule|nearest debt maturity)\b.{0,120}\b(20[2-9]\d)\b", text, flags=re.IGNORECASE)
        if generic:
            evidence = _sentence_window(text, generic.start(), generic.end(), before=120, after=180)
            return ExtractedMetric(metric_key, float(generic.group(1)), "year", evidence, confidence)
        due_match = re.search(r"\b(?:senior notes?|convertible notes?|term loan|facility)\b.{0,120}\bdue\s+(20[2-9]\d)\b", text, flags=re.IGNORECASE)
        if due_match:
            evidence = _sentence_window(text, due_match.start(), due_match.end(), before=120, after=180)
            return ExtractedMetric(metric_key, float(due_match.group(1)), "year", evidence, confidence)
        return None
    facility_matches = list(
        re.finditer(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+"
            r"(20[2-9]\d)\s+(?:\d{1,2}\s?%|[-—]|\$?\s?\d)",
            search_text,
            flags=re.IGNORECASE,
        )
    )
    if facility_matches:
        years = [int(match.group(1)) for match in facility_matches]
        first_year = min(years)
        first_match = next(match for match in facility_matches if int(match.group(1)) == first_year)
        evidence = _sentence_window(text, evidence_offset + first_match.start(), evidence_offset + first_match.end(), before=180, after=320)
        return ExtractedMetric(metric_key, float(first_year), "year", evidence, confidence)
    year_matches = list(re.finditer(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)?\s*(20[2-9]\d)\b", search_text, flags=re.IGNORECASE))
    years = [int(match.group(1)) for match in year_matches if _debt_maturity_year_context(search_text, match)]
    if years:
        first_year = min(years)
        first_match = next(match for match in year_matches if int(match.group(1)) == first_year)
        evidence = _sentence_window(text, evidence_offset + first_match.start(), evidence_offset + first_match.end(), before=180, after=320)
        return ExtractedMetric(metric_key, float(first_year), "year", evidence, confidence)
    return None


def _debt_maturity_year_context(text: str, match: re.Match) -> bool:
    around = text[max(0, match.start() - 120) : min(len(text), match.end() + 120)].lower()
    prefix = text[max(0, match.start() - 40) : match.start()].lower()
    if re.search(r"\b(?:march|june|september|december)\s+31,?\s*$", prefix):
        return False
    if re.search(r"\b(?:april|may|january|february|march|june|july|august|september|october|november|december)\s+\d{1,2},?\s*$", prefix):
        return False
    return bool(re.search(r"\b(debt|facility|senior notes?|convertible notes?|maturit(?:y|ies)|effective interest rates?)\b", around))


def _extract_ai_cloud_interest_burden(text: str, metric_key: str, confidence: str) -> ExtractedMetric | None:
    match = re.search(r"\binterest expense,?\s+net\b.{0,360}", text, flags=re.IGNORECASE)
    if not match:
        return None
    evidence = _sentence_window(text, match.start(), match.end(), before=520, after=180)
    if "interest expense, net" not in evidence.lower() and "interest expense net" not in evidence.lower():
        return None
    if re.search(r"\bfinance leases?\b", evidence, flags=re.IGNORECASE) and not re.search(r"\binterest expense,?\s+net\b", evidence, flags=re.IGNORECASE):
        return None
    if re.search(r"\binterest expense\b.{0,80}\b\d+(?:\.\d+)?\s?%", evidence, flags=re.IGNORECASE) and "$" not in evidence:
        return None
    if re.search(r"\binterest expense\b.{0,80}\bfinance leases?\b|\bfinance leases?\b.{0,80}\binterest expense\b", evidence, flags=re.IGNORECASE):
        return None
    post_match_text = text[match.start() : min(len(text), match.end() + 220)]
    row_amount = re.search(r"\binterest expense,?\s+net\s+\$?\s*\(?\s*(-?\d+(?:\.\d+)?)\s*\)?", post_match_text, flags=re.IGNORECASE)
    if row_amount:
        value = float(row_amount.group(1)) * _money_unit_multiplier("", evidence)
        return ExtractedMetric(metric_key, abs(value), "usd", evidence, confidence)
    explicit_interest_money = re.search(
        r"\binterest expense(?:,?\s+net)?\b[^.\n]{0,80}\$\s*\(?\s*(-?\d+(?:\.\d+)?)\s*\)?",
        evidence,
        flags=re.IGNORECASE,
    )
    if explicit_interest_money:
        value = float(explicit_interest_money.group(1)) * _money_unit_multiplier("", evidence)
        return ExtractedMetric(metric_key, abs(value), "usd", evidence, confidence)
    return None


def _extract_ai_cloud_nvidia_supply_exposure(text: str, metric_key: str, confidence: str) -> ExtractedMetric | None:
    patterns = (
        r"\ball of the GPUs used in our infrastructure today are NVIDIA GPUs\b.{0,260}",
        r"\bcustomers have contractually specified our use of NVIDIA GPUs\b.{0,260}",
        r"\bNVIDIA\b.{0,160}\b(?:purchase commitments?|supplier concentration|vendor concentration|supply dependency|GPU supplier)\b.{0,160}",
        r"\b(?:purchase commitments?|supplier concentration|vendor concentration|supply dependency|GPU supplier)\b.{0,160}\bNVIDIA\b.{0,160}",
    )
    return _extract_ai_cloud_risk_observation(text, metric_key, confidence, patterns)


def _extract_ai_cloud_hyperscaler_exposure(text: str, metric_key: str, confidence: str) -> ExtractedMetric | None:
    patterns = (
        r"\bOpenAI\b.{0,180}\b(?:committed|commitment|master services agreement|order form|significant customer)\b.{0,220}",
        r"\bMeta\b.{0,180}\b(?:committed|commitment|master services agreement|order form|significant customer)\b.{0,220}",
        r"\bMicrosoft\b.{0,180}\b(?:top customer|significant customer|customer concentration|revenue)\b.{0,220}",
        r"\b(?:hyperscalers?|AI labs)\b.{0,160}\b(?:customer wins|agreements|contracts|commitments)\b.{0,220}",
    )
    return _extract_ai_cloud_risk_observation(text, metric_key, confidence, patterns)


def _extract_ai_cloud_risk_observation(text: str, metric_key: str, confidence: str, patterns: tuple[str, ...]) -> ExtractedMetric | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        evidence = _sentence_window(text, match.start(), match.end(), before=100, after=140)
        valid, _reason = validate_extracted_metric_candidate(metric_key, evidence, 1.0)
        if valid:
            return ExtractedMetric(metric_key, 1.0, "flag", evidence, confidence)
    return None


def _extract_value(window: str, unit_hint: str, scale_context: str | None = None) -> tuple[float, str] | None:
    if unit_hint == "multiple":
        match = MULTIPLE_PATTERN.search(window)
        if match:
            return float(match.group(1)), "x"

    if unit_hint == "money":
        money_matches = list(MONEY_PATTERN.finditer(window))
        if money_matches:
            money = _select_current_quarter_money_match(money_matches, scale_context or window)
            amount = float(money.group(1))
            suffix = (money.group(2) or "").lower()
            amount *= _money_unit_multiplier(suffix, scale_context or window)
            return amount, "usd"
        table_amount = _extract_table_money_amount(window, scale_context or window)
        if table_amount is not None:
            return table_amount
        return None

    if unit_hint == "text":
        year_match = re.search(r"\b(20[2-9]\d|21\d{2})\b", window)
        if year_match:
            return float(year_match.group(1)), "year"
        return None

    if unit_hint == "count":
        number = COUNT_PATTERN.search(window)
        if number and not _number_match_is_year(window, number):
            return float(number.group(1).replace(",", "")), "count"
        return None

    percent = PERCENT_PATTERN.search(window)
    if percent:
        return float(percent.group(1)) / 100, "percent"

    if unit_hint == "multiple":
        number = NUMBER_PATTERN.search(window)
        if number:
            return float(number.group(1)), "x"

    return None


def _value_window_for_alias(post_alias_window: str, metric_key: str, unit_hint: str) -> str:
    if unit_hint != "money":
        return post_alias_window
    row = _hood_money_row_window(post_alias_window, metric_key)
    return row or post_alias_window


def _candidate_evidence_window(full_window: str, value_window: str, unit_hint: str) -> str:
    if unit_hint != "money":
        return full_window
    prefix = full_window[: max(0, full_window.find(value_window))]
    heading_match = (
        re.search(r"(Three Months Ended[^.]{0,120})", prefix, flags=re.IGNORECASE)
        or re.search(
            r"((?:reports?\s+)?(?:first|second|third|fourth)\s+quarter\s+20\d{2}[^.]{0,80})",
            prefix,
            flags=re.IGNORECASE,
        )
        or re.search(r"(CONDENSED CONSOLIDATED[^.]{0,120})", prefix, flags=re.IGNORECASE)
    )
    if heading_match:
        return f"{heading_match.group(1).strip()} {value_window.strip()}".strip()
    sentence_prefix = re.split(r"[.;•◦]", prefix)[-1].strip()
    if sentence_prefix:
        return f"{sentence_prefix} {value_window.strip()}".strip()
    return value_window.strip()


def _hood_money_row_window(post_alias_window: str, metric_key: str) -> str:
    token = _normalized_metric_token(metric_key)
    if not token.startswith("hood"):
        return post_alias_window
    text = _normalize_whitespace(post_alias_window)
    if not text:
        return text
    first_sentence = re.search(r"^(.{0,220}?\.(?:\s|$))", text)
    if first_sentence and not _looks_like_financial_table_row(first_sentence.group(1)):
        return first_sentence.group(1).strip()

    matches = list(HOOD_MONEY_ROW_BOUNDARY_PATTERN.finditer(text))
    if len(matches) > 1:
        return text[: matches[1].start()].strip()
    bullet = re.search(r"\s[•◦]\s", text[24:])
    if bullet:
        return text[: 24 + bullet.start()].strip()
    return text[:260].strip()


def _looks_like_financial_table_row(text: str) -> bool:
    return len(list(NUMBER_PATTERN.finditer(text))) >= 3 or bool(PERCENT_PATTERN.search(text))


def _select_current_quarter_money_match(matches: list[re.Match], context: str) -> re.Match:
    index = _current_quarter_amount_index(context, len(matches))
    return matches[min(index, len(matches) - 1)]


def _extract_table_money_amount(window: str, context: str) -> tuple[float, str] | None:
    amount_matches = []
    for match in NUMBER_PATTERN.finditer(window):
        if _number_match_is_percent(window, match):
            continue
        if _number_match_is_year(window, match):
            continue
        amount_matches.append(match)
    if not amount_matches:
        return None
    match = amount_matches[min(_current_quarter_amount_index(context, len(amount_matches)), len(amount_matches) - 1)]
    amount = float(match.group(1))
    amount *= _money_unit_multiplier("", context or window)
    return amount, "usd"


def _current_quarter_amount_index(context: str, amount_count: int) -> int:
    if amount_count <= 1:
        return 0
    years = _three_months_header_years(context)
    if len(years) >= 2 and years[0] < years[1]:
        return 1
    return 0


def _three_months_header_years(context: str) -> list[int]:
    match = re.search(r"three months ended.{0,160}", context, flags=re.IGNORECASE)
    if not match:
        return []
    return [int(year) for year in re.findall(r"20\d{2}", match.group(0))]


def _number_match_is_percent(text: str, match: re.Match) -> bool:
    return bool(re.match(r"\s*(?:%|percent|percentage)", text[match.end() :], flags=re.IGNORECASE))


def _number_match_is_year(text: str, match: re.Match) -> bool:
    value = match.group(1)
    if not re.fullmatch(r"20\d{2}", value):
        return False
    prefix = text[max(0, match.start() - 24) : match.start()].lower()
    return bool(re.search(r"\b(q[1-4]|quarter|ended|fiscal|fy)\b", prefix))


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

    if _has_transaction_revenue_subcomponent_context(canonical_key, text, value):
        return True

    if _looks_like_scaled_non_gaap_money_metric(canonical_key, text, value):
        return True
    if _looks_like_ambiguous_hood_non_gaap_scope(canonical_key, text, value):
        return True

    if _expected_period_scope(canonical_key) == "quarterly":
        if value is not None:
            context = _money_context_for_value(text, value)
            if _period_scope_mismatch(context, expected="quarterly"):
                return True
            return False
        if _period_scope_mismatch(text, expected="quarterly"):
            return True
        return not re.search(r"\bnet deposits?\s+(?:were|was|of)?\s*\$?\s*\d", text)

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
    if any(
        marker in token
        for marker in (
            "hoodnetdeposits",
            "hoodtransactionrevenue",
            "hoodinterestrevenue",
            "hoodsubscriptiongoldrevenue",
            "hoodnormalizedebitda",
            "hoodnormalizedearnings",
        )
    ):
        return "quarterly"
    return None


def _period_scope_mismatch(text: str, expected: str) -> bool:
    if expected != "quarterly":
        return False
    prefix = text.split("$", 1)[0]
    if re.search(r"\bnine\s+months?\s+ended\b|\bsix\s+months?\s+ended\b", prefix):
        return True
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


def _looks_like_ambiguous_hood_non_gaap_scope(metric_key: str, text: str, value: float | None) -> bool:
    token = _normalized_metric_token(metric_key)
    if not ("hoodnormalizedebitda" in token or "hoodnormalizedearnings" in token):
        return False
    return bool(
        re.search(
            r"adjusted\s+ebitda(?:\s*\(non[- ]gaap\))?.{0,80}\$\s*\d+(?:\.\d+)?.{0,80}\$\s*\d+(?:\.\d+)?.{0,80}\$\s*\d+(?:\.\d+)?.{0,120}adjusted\s+ebitda\s+margin",
            text,
        )
    )


def _has_transaction_revenue_subcomponent_context(metric_key: str, text: str, value: float | None) -> bool:
    if "hoodtransactionrevenue" not in _normalized_metric_token(metric_key):
        return False
    context = _money_context_for_value(text, value)
    return bool(re.search(r"\b(other|options|equities|cryptocurrenc(?:y|ies)|event contracts?)\s+transaction revenues?\b", context))


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


def _sentence_window(text: str, start: int, end: int, before: int = 180, after: int = 220) -> str:
    window_start = max(0, start - before)
    window_end = min(len(text), end + after)
    return _normalize_whitespace(text[window_start:window_end])


def _looks_like_cost_context(lowered_text: str, index: int) -> bool:
    prefix = lowered_text[max(0, index - 24):index]
    return "cost of" in prefix or "costs of" in prefix


def _alias_matches_at(lowered_text: str, alias_lower: str, index: int) -> bool:
    if not alias_lower.replace(" ", "").isalnum():
        return True
    if len(alias_lower) > 3:
        return True
    before = lowered_text[index - 1] if index > 0 else ""
    after_index = index + len(alias_lower)
    after = lowered_text[after_index] if after_index < len(lowered_text) else ""
    return not before.isalnum() and not after.isalnum()
