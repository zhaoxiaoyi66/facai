from __future__ import annotations

import re
from typing import Any


METRIC_DISPLAY_MAP: dict[str, str] = {
    "crpogrowthreported": "cRPO增速（reported YoY）",
    "crpogrowthconstantcurrency": "cRPO增速（constant currency）",
    "rpogrowthreported": "RPO增速（reported YoY）",
    "rpogrowthconstantcurrency": "RPO增速（constant currency）",
    "subscriptionrevenuegrowthreported": "订阅收入增速（reported YoY）",
    "subscriptionrevenuegrowthconstantcurrency": "订阅收入增速（constant currency）",
    "operatingcashflowmargin": "经营现金流利润率",
    "nongaapfcfmargin": "Non-GAAP FCF利润率",
    "directfcfmargin": "FCF利润率（直接计算）",
    "impliedfcfmargin": "估算FCF利润率",
    # Generic
    "revenue growth": "收入增速",
    "gross margin": "毛利率",
    "operating margin": "经营利润率",
    "gaap operating margin": "GAAP经营利润率",
    "non-gaap operating margin": "Non-GAAP经营利润率",
    "non gaap operating margin": "Non-GAAP经营利润率",
    "fcf margin": "FCF利润率",
    "calculated fcf margin": "FCF利润率",
    "direct fcf margin": "FCF利润率",
    "implied fcf margin": "估算FCF利润率",
    "fcf margin reported/calculated": "财报口径FCF利润率",
    "fcf yield": "FCF收益率",
    "free cash flow yield": "FCF收益率",
    "p/fcf": "市值/FCF",
    "price to fcf": "市值/FCF",
    "ev/fcf": "EV/FCF",
    "ev / fcf": "EV/FCF",
    "p/s": "市销率",
    "price to sales": "市销率",
    "ev/sales": "EV/销售额",
    "enterprise to revenue": "EV/销售额",
    "roic": "ROIC",
    "net debt / ebitda": "净债务/EBITDA",
    "net debt / adjusted ebitda": "净债务/调整后EBITDA",
    "net cash / balance sheet": "净现金/资产负债表强度",
    "balance sheet": "资产负债表强度",
    "cash and equivalents": "现金与等价物",
    "cash and cash equivalents": "现金及等价物",
    "cashandcashequivalents": "现金及等价物",
    "cash and short term investments": "现金及短期投资",
    "cashandshortterminvestments": "现金及短期投资",
    "interest coverage": "利息覆盖倍数",
    "debt maturity pressure": "债务到期压力",
    "current ratio": "流动比率",
    "ebitda": "EBITDA",
    "free cash flow": "自由现金流",
    "market cap": "市值",
    "forward pe": "远期市盈率",
    "forward pe / normalized pe": "远期/正常化市盈率",
    "normalized pe": "正常化市盈率",
    "peg": "PEG",
    "forward revenue multiple": "远期收入倍数",
    "ntm revenue estimate": "未来12个月收入预期",
    "expected eps growth": "预期EPS增速",
    "historical valuation percentile": "历史估值分位",
    "valuation overheating": "估值过热",
    "valuation extreme": "估值极端",
    "drawdown from 52-week high": "距52周高点回撤",
    "drawdown / technical setup": "回撤/技术结构",
    "52-week drawdown": "距高点回撤",
    "drawdown": "距高点回撤",
    "return20d": "20日涨幅",
    "return60d": "60日涨幅",
    "20d return": "20日涨幅",
    "60d return": "60日涨幅",
    "ema20": "EMA20",
    "ema50": "EMA50",
    "ema200": "EMA200",
    "rsi14": "RSI14",
    "rsi": "RSI14",
    "rsi / technical cooling": "RSI/技术冷却",
    "rsi / momentum cooling": "RSI/动量冷却",
    "above / below ema200": "均线趋势状态",
    "below ema200": "股价低于EMA200",
    "volume trend": "成交量趋势",
    "volumetrend": "成交量趋势",
    "trend confirmation": "趋势确认",
    "confidence only": "仅影响置信度",
    "market-derived": "基于市场数据估算",
    # SaaS
    "subscription revenue growth": "订阅收入增速",
    "rpo / crpo growth": "RPO / cRPO 增速",
    "rpo growth": "RPO增速",
    "crpo growth": "cRPO增速",
    "net retention rate": "净留存率",
    "large customer growth": "大客户增长",
    "sbc / revenue": "股权激励/收入",
    "sbc discipline": "股权激励纪律",
    "ai disruption risk": "AI替代风险",
    "ai disruption / seat compression risk": "AI替代 / 席位压缩风险",
    "seat compression risk": "席位压缩风险",
    "growth deceleration": "增长放缓",
    "growth deceleration risk": "增长放缓风险",
    "valuation vs growth": "估值与增长匹配度",
    "ai replacement narrative": "AI替代叙事",
    "competitive pressure": "竞争压力",
    "dilution risk": "稀释风险",
    "acquisition integration risk": "并购整合风险",
    "free cash flow negative": "自由现金流为负",
    "fcf negative": "自由现金流为负",
    # Mega cap
    "segment strength": "分部业务强度",
    "buyback discipline": "回购纪律",
    "capex concern discount": "Capex担忧折价",
    "ai capex overbuild risk": "AI资本开支过剩风险",
    "ai overbuild narrative": "AI过度建设叙事",
    "regulatory risk": "监管风险",
    "antitrust": "反垄断风险",
    "segment concentration": "分部集中度",
    "margin compression": "利润率压缩",
    "growth slowdown": "增长放缓",
    "cloud revenue growth": "云业务收入增速",
    "azure growth": "Azure增速",
    "segment revenue": "分部收入",
    "segment operating income": "分部经营利润",
    # Semiconductor
    "cycle position": "周期位置",
    "semiconductor cycle risk": "半导体周期风险",
    "inventory correction risk": "库存修正风险",
    "margin normalization risk": "利润率正常化风险",
    "export control risk": "出口管制风险",
    "export control / china risk": "出口管制/中国风险",
    "customer concentration": "客户集中度",
    "customer concentration risk adjustment": "客户集中度风险调整",
    "product moat / ecosystem": "产品护城河 / 生态",
    "inventory discipline": "库存纪律",
    "cycle-adjusted margin": "周期调整后利润率",
    "fcf across cycle": "跨周期FCF",
    "fcf generation": "FCF创造能力",
    "competitive position": "竞争位置",
    "revenue recovery": "收入复苏",
    "downcycle risk": "下行周期风险",
    "inventory glut": "库存过剩风险",
    "negative fcf": "自由现金流为负",
    "high leverage": "杠杆偏高",
    # Power
    "adjusted ebitda": "调整后EBITDA",
    "adjusted fcf before growth": "增长投资前调整后FCF",
    "hedge coverage": "对冲覆盖率",
    "generation mix": "发电资产结构",
    "generation asset quality": "发电资产质量",
    "power demand exposure": "电力需求敞口",
    "data center power exposure": "数据中心电力需求敞口",
    "commodity exposure": "商品价格敞口",
    "merchant power price exposure": "市场化电价敞口",
    "capacity market exposure": "容量市场敞口",
    "fcf volatility": "FCF波动性",
    # Crypto
    "revenue diversification": "收入多元化",
    "crypto cycle sensitivity": "加密周期敏感度",
    "regulatory positioning": "监管位置",
    "user asset quality": "用户资产质量",
    "user / asset base quality": "用户/资产基础质量",
    "crypto price sensitivity": "加密价格敏感度",
    "revenue cyclicality": "收入周期性",
    "product ecosystem": "产品生态",
    "platform trust": "平台信任风险",
    # Pharma
    "pipeline strength": "管线强度",
    "pipeline risk": "管线风险",
    "patent cliff risk": "专利悬崖风险",
    "patent durability": "专利耐久性",
    "pricing pressure": "定价压力",
    "product concentration": "产品集中度",
    "glp-1 competition": "GLP-1竞争",
    "regulatory pricing": "监管定价风险",
    "product revenue growth": "核心产品收入增速",
    "pipeline updates": "管线更新",
    "trial milestones": "临床里程碑",
    # Other model terms
    "affo": "AFFO",
    "noi": "NOI",
    "occupancy": "出租率",
    "lease duration": "租约期限",
    "cet1": "CET1资本充足率",
    "nim": "净息差",
    "credit loss": "信用损失",
    "deposit stability": "存款稳定性",
    "ordinary pe": "普通PE",
    "gaap profitability": "GAAP盈利能力",
    "core growth": "核心增长",
    "valuation": "估值",
    "capital return": "资本回报",
    "power model core": "电力模型核心",
    "backlog / contracted revenue": "Backlog / 已签约收入",
    "contracted backlog": "已签约订单",
    "asset quality": "资产质量",
}

METRIC_DISPLAY_MAP.update(
    {
        # Common camelCase / formula aliases
        "revenuegrowth": "收入增速",
        "grossmargin": "毛利率",
        "operatingmargin": "经营利润率",
        "gaapoperatingmargin": "GAAP经营利润率",
        "nongaapoperatingmargin": "Non-GAAP经营利润率",
        "fcfmargin": "FCF利润率",
        "directfcfmargin": "FCF利润率",
        "direct fcf margin": "FCF利润率",
        "impliedfcfmargin": "估算FCF利润率",
        "nongaapfcfmargin": "Non-GAAP FCF利润率",
        "operatingcashflowmargin": "经营现金流利润率",
        "operating cash flow margin": "经营现金流利润率",
        "fcfyield": "FCF收益率",
        "netdebt": "净债务",
        "net debt": "净债务",
        "netdebttoebitda": "净债务/EBITDA",
        "net debt to ebitda": "净债务/EBITDA",
        "interestcoverage": "利息覆盖倍数",
        "debtmaturitypressure": "债务到期压力",
        "sbc to revenue": "股权激励/收入",
        "sbctorevenue": "股权激励/收入",
        "stockbasedcompensation / revenue": "股权激励/收入",
        "stock based compensation / revenue": "股权激励/收入",
        "stock based compensation revenue": "股权激励/收入",
        "drawdownfrom52weekhigh": "距52周高点回撤",
        "drawdown from52 week high": "距52周高点回撤",
        "drawdown from 52 week high": "距52周高点回撤",
        "currentvolume / avgvolume20d 1": "成交量趋势",
        "current volume / avg volume20d 1": "成交量趋势",
        "currentprice / closeprice20tradingdaysago 1": "20日涨幅",
        "current price / close price20 trading days ago 1": "20日涨幅",
        "currentprice / closeprice60tradingdaysago 1": "60日涨幅",
        "current price / close price60 trading days ago 1": "60日涨幅",
        "currentprice / fiftytwoweekhigh 1": "距52周高点回撤",
        "current price / fifty two week high 1": "距52周高点回撤",
        # SaaS / software aliases
        "crpogrowth": "cRPO增速",
        "c rpo growth": "cRPO增速",
        "crpogrowthreported": "cRPO增速（reported YoY）",
        "c rpo growth reported": "cRPO增速（reported YoY）",
        "crpogrowthconstantcurrency": "cRPO增速（constant currency）",
        "c rpo growth constant currency": "cRPO增速（constant currency）",
        "rpogrowth": "RPO增速",
        "rpogrowthreported": "RPO增速（reported YoY）",
        "rpogrowthconstantcurrency": "RPO增速（constant currency）",
        "subscriptionrevenuegrowth": "订阅收入增速",
        "subscriptionrevenuegrowthreported": "订阅收入增速（reported YoY）",
        "subscriptionrevenuegrowthconstantcurrency": "订阅收入增速（constant currency）",
        "netretentionrate": "净留存率",
        "nrr": "净留存率",
        "largecustomergrowth": "大客户增长",
        "unit growth": "客户/单位增长",
        "unitgrowth": "客户/单位增长",
        "seatcompressionrisk": "席位压缩风险",
        # Platform / semis / power / crypto / pharma camelCase aliases
        "segmentstrength": "分部业务强度",
        "buybackdiscipline": "回购纪律",
        "historicalvaluationpercentile": "历史估值分位",
        "capexconcerndiscount": "Capex担忧折价",
        "aicapexoverbuildrisk": "AI资本开支过剩风险",
        "regulatoryrisk": "监管风险",
        "segmentconcentration": "分部集中度",
        "cycleposition": "周期位置",
        "inventorycorrectionrisk": "库存修正风险",
        "marginnormalizationrisk": "利润率正常化风险",
        "exportcontrolrisk": "出口管制风险",
        "customerconcentration": "客户集中度",
        "adjustedebitda": "调整后EBITDA",
        "adjustedfcfbeforegrowth": "增长投资前调整后FCF",
        "hedgecoverage": "对冲覆盖率",
        "generationmix": "发电资产结构",
        "merchantpowerexposure": "市场化电价敞口",
        "revenuediversification": "收入多元化",
        "cryptocyclesensitivity": "加密周期敏感度",
        "regulatorypositioning": "监管位置",
        "userassetquality": "用户资产质量",
        "pipelinestrength": "管线强度",
        "patentcliffrisk": "专利悬崖风险",
        "pricingpressure": "定价压力",
        "productconcentration": "产品集中度",
        "missing power generation core inputs": "缺少电力模型核心输入",
        "missing networking hardware growth or margin": "缺少网络硬件模型所需的增长或利润率输入",
        "networking hardware sales multiple overextended": "销售倍数偏高，优先不追高",
        "networking hardware risk inputs missing: customer concentration / cloud capex risk": "缺客户集中度 / 云资本开支风险输入，置信度受限",
        "missing crypto ev sales anchor": "缺少 EV/Sales 主估值锚",
        "missing crypto operating inputs": "缺少交易收入、订阅收入、盈利和周期拆分",
        "missing crypto core inputs for heavy buy": "缺少核心经营输入，不输出深度折价区",
        "crypto financial infra high beta sales multiple": "高 beta 且销售倍数偏高，优先不追高",
        "crypto financial infra operating mix missing": "缺少加密金融经营拆分，置信度受限",
        "ai cloud infra high ev sales capex debt": "EV/Sales、资本开支和债务压力同时偏高",
        "ai cloud infra customer concentration missing": "缺客户集中度输入，置信度受限",
        "ai cloud infra debt maturity unclear": "债务到期结构不清楚，置信度受限",
        "ai cloud infra operating inputs incomplete": "缺利用率或资本开支承诺输入",
        "ai cloud infra fcf burn or capex intensity blocks heavy buy": "FCF 为负或资本开支强度过高，不输出深度折价区",
    }
)

INTERNAL_DEBUG_FIELDS = {
    "evidencehash",
    "evidence hash",
    "extractionrule",
    "extraction rule",
    "rawmetrickey",
    "raw metric key",
    "sourcetype",
    "source type",
    "source type raw enum",
    "resolutionstatus",
    "resolution status",
    "resolution status raw enum",
    "aitriagestatus",
    "ai triage status",
    "ai triage status raw enum",
    "reviewstatus",
    "review status",
    "review status raw enum",
    "formula debug string",
    "inputhash",
    "input hash",
    "promptversion",
    "prompt version",
    "provider/model debug",
    "provider model debug",
    "accessionnumber",
    "accession number",
    "internal cache key",
}

_UNMAPPED_METRIC_LABELS: set[str] = set()

RESOLUTION_STATUS_DISPLAY_MAP: dict[str, str] = {
    "available": "已可用",
    "calculated": "已计算",
    "derived": "规则推导",
    "derived_score": "规则推导",
    "requires_ir_scrape": "需抓取IR / 8-K",
    "requires_sec_filing": "需抓取SEC文件",
    "requires_analyst_estimates": "需分析师预期",
    "requires_estimates": "需分析师预期",
    "company_not_disclosed": "公司未披露",
    "not_disclosed": "公司未披露",
    "vendor_unavailable": "当前数据源没有",
    "manual_override_required": "建议人工复核",
    "semi_auto_low_confidence": "半自动低置信度，建议复核",
    "missing_inputs": "缺少计算输入",
    "not_applicable": "不适用",
    "missing": "暂缺",
    "estimated": "估算值",
}

SOURCE_TYPE_DISPLAY_MAP: dict[str, str] = {
    "FMP": "FMP",
    "fmp": "FMP",
    "CALCULATED": "自动计算",
    "calculated": "自动计算",
    "SEC_XBRL": "SEC XBRL",
    "SEC_8K": "SEC 8-K",
    "SEC_10Q": "SEC 10-Q",
    "SEC_10K": "SEC 10-K",
    "IR_RELEASE": "IR财报新闻稿",
    "IR_PRESENTATION": "投资者演示",
    "FMP_TRANSCRIPT": "财报电话会文本",
    "MANUAL": "人工录入",
    "MANUAL_CORRECTION": "人工修正",
    "manual": "人工录入",
    "missing": "暂缺",
    "not_applicable": "不适用",
    "rule_derived": "规则推导",
    "semi_auto_risk_tag": "半自动风险标签",
    "metric_resolution": "评分缺口",
    "derivedFromMarket": "市场数据反推",
    "derived_from_market": "市场数据反推",
    "reported_sec": "SEC披露",
    "reported_ir": "IR披露",
    "non_gaap_reported": "Non-GAAP披露",
    "estimated": "估算",
}

CONFIDENCE_DISPLAY_MAP: dict[str, str] = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "not_applicable": "不适用",
    "不适用": "不适用",
}

MODEL_TYPE_DISPLAY_MAP: dict[str, str] = {
    "MEGA_CAP_PLATFORM": "平台型科技巨头",
    "SAAS_SOFTWARE": "SaaS / 软件",
    "SEMICONDUCTOR": "半导体",
    "SEMICONDUCTOR_CYCLICAL": "周期半导体",
    "NETWORKING_HARDWARE": "网络硬件",
    "POWER_GENERATION": "电力生产商",
    "REGULATED_UTILITIES": "传统公用事业",
    "CRYPTO_FINANCIAL_INFRA": "加密金融基础设施",
    "BROKERAGE_FINTECH": "券商金融科技",
    "AI_CLOUD_INFRA": "AI云基础设施",
    "PHARMA": "药企",
    "MEDICAL_DEVICE": "医疗器械",
    "AI_INFRA_HIGH_RISK": "AI基础设施高风险",
    "BANK_FINANCIAL": "银行金融",
    "REIT_REAL_ESTATE": "REIT / 房地产",
    "AUTO_HARDWARE": "汽车 / 硬件",
    "CONSUMER_INTERNET_ECOMMERCE": "消费互联网 / 电商",
    "INDUSTRIAL_CAPEX": "工业资本开支",
    "ENERGY_COMMODITY": "能源商品",
    "GENERIC": "通用模型",
}

ACTION_DISPLAY_MAP: dict[str, str] = {
    "manual_override_required": "建议人工复核",
    "manual override": "人工复核",
    "requires_ir_scrape": "抓取IR / 8-K",
    "requires_sec_filing": "抓取SEC文件",
    "requires_analyst_estimates": "补齐分析师预期",
    "missing": "暂缺",
}


def metric_label(value: Any, *, debug: bool = False) -> str:
    text = _clean(value)
    if not text or text.upper() == "N/A":
        return "N/A"
    key = _normalize(text)
    snake_key = _normalize(_camel_to_words(text))
    if key in INTERNAL_DEBUG_FIELDS or snake_key in INTERNAL_DEBUG_FIELDS:
        return f"未映射字段：{text}" if debug else ""
    if key in METRIC_DISPLAY_MAP:
        return METRIC_DISPLAY_MAP[key]
    if snake_key in METRIC_DISPLAY_MAP:
        return METRIC_DISPLAY_MAP[snake_key]
    translated = _replace_known_terms(text)
    if translated != text:
        return translated
    if _has_cjk(text):
        return text
    _UNMAPPED_METRIC_LABELS.add(text)
    return f"未映射字段：{text}" if debug else _humanize_unknown_metric(text)


def resolution_status_label(value: Any) -> str:
    text = _clean(value)
    if not text or text.upper() == "N/A":
        return "暂缺"
    return RESOLUTION_STATUS_DISPLAY_MAP.get(text, RESOLUTION_STATUS_DISPLAY_MAP.get(text.lower(), f"未映射状态：{text}"))


def source_type_label(value: Any) -> str:
    text = _clean(value)
    if not text or text.upper() == "N/A":
        return "N/A"
    return SOURCE_TYPE_DISPLAY_MAP.get(text, SOURCE_TYPE_DISPLAY_MAP.get(text.lower(), f"未映射来源：{text}"))


def confidence_label(value: Any) -> str:
    text = _clean(value)
    if not text or text.upper() == "N/A":
        return "N/A"
    return CONFIDENCE_DISPLAY_MAP.get(text, CONFIDENCE_DISPLAY_MAP.get(text.lower(), f"未映射置信度：{text}"))


def model_type_label(value: Any) -> str:
    text = _clean(value)
    if not text or text.upper() == "N/A":
        return "N/A"
    return MODEL_TYPE_DISPLAY_MAP.get(text, MODEL_TYPE_DISPLAY_MAP.get(text.upper(), f"未映射模型：{text}"))


def action_label(value: Any) -> str:
    text = _clean(value)
    if not text or text.upper() == "N/A":
        return "N/A"
    if text in ACTION_DISPLAY_MAP:
        return ACTION_DISPLAY_MAP[text]
    lowered = text.lower()
    if lowered in ACTION_DISPLAY_MAP:
        return ACTION_DISPLAY_MAP[lowered]
    translated = _replace_known_terms(text)
    if translated != text:
        return translated
    if not _has_cjk(text):
        _UNMAPPED_METRIC_LABELS.add(text)
        return _humanize_unknown_metric(text)
    return text


def metric_list_label(values: Any, limit: int | None = None) -> list[str]:
    if isinstance(values, (list, tuple, set)):
        items = [label for item in values if item for label in [metric_label(item)] if label]
    elif values:
        label = metric_label(values)
        items = [label] if label else []
    else:
        items = []
    return items[:limit] if limit else items


def unmapped_metric_labels() -> list[str]:
    return sorted(_UNMAPPED_METRIC_LABELS)


def is_internal_metric_field(value: Any) -> bool:
    text = _clean(value)
    if not text:
        return False
    key = _normalize(text)
    snake_key = _normalize(_camel_to_words(text))
    return key in INTERNAL_DEBUG_FIELDS or snake_key in INTERNAL_DEBUG_FIELDS


def _replace_known_terms(text: str) -> str:
    translated = text
    for raw, label in sorted(METRIC_DISPLAY_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        translated = re.sub(re.escape(raw), label, translated, flags=re.IGNORECASE)
    return _dedupe_indicator_labels(translated)


def _dedupe_indicator_labels(text: str) -> str:
    normalized = text
    normalized = re.sub(r"RSI14(?:14)+", "RSI14", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"EMA20(?:20)+", "EMA20", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"EMA50(?:50)+", "EMA50", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"EMA200(?:200)+", "EMA200", normalized, flags=re.IGNORECASE)
    return normalized


def _humanize_unknown_metric(text: str) -> str:
    cleaned = _camel_to_words(text).replace("_", " ").replace("-", " ")
    return " ".join(cleaned.split()) or text


def _normalize(value: str) -> str:
    return " ".join(value.replace("_", " ").replace("-", " ").strip().lower().split())


def _camel_to_words(value: str) -> str:
    value = value.replace("_", " ")
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value)


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
