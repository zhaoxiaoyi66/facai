from __future__ import annotations

from typing import Any


SECTOR_LOCALIZATION = {
    "technology": "科技",
    "healthcare": "医疗健康",
    "financial services": "金融服务",
    "utilities": "公用事业",
    "consumer cyclical": "可选消费",
    "communication services": "通信服务",
    "consumer defensive": "必选消费",
    "industrials": "工业",
    "energy": "能源",
    "real estate": "房地产",
    "basic materials": "基础材料",
}


INDUSTRY_LOCALIZATION = {
    "software": "软件",
    "software - application": "应用软件",
    "software - infrastructure": "软件基础设施",
    "semiconductors": "半导体",
    "communication equipment": "通信设备",
    "biotechnology": "生物科技",
    "medical - devices": "医疗器械",
    "medical devices": "医疗器械",
    "medical - instruments & supplies": "医疗器械",
    "capital markets": "资本市场",
    "financial - data & stock exchanges": "金融数据 / 交易所",
    "independent power producers": "独立电力生产商",
    "renewable utilities": "可再生公用事业",
    "power generation": "电力",
    "electric utilities": "电力",
    "internet retail": "互联网零售",
    "internet content & information": "互联网内容与信息",
    "drug manufacturers": "生物医药",
    "drug manufacturers - general": "生物医药",
    "drug manufacturers - specialty & generic": "生物医药",
    "brokerage": "券商",
    "banks": "银行",
    "specialty industrial machinery": "电气设备",
    "electrical equipment & parts": "电气设备",
    "ai infrastructure": "AI基础设施",
    "data center / ai infrastructure": "数据中心｜AI基础设施",
}


TICKER_RESEARCH_TRACKS = {
    "MSFT": "云平台｜AI软件",
    "NOW": "企业SaaS｜工作流自动化",
    "CRM": "企业SaaS｜CRM",
    "ADBE": "创意软件｜AI变现",
    "PLTR": "AI数据平台",
    "ORCL": "数据库｜云基础设施",
    "INTU": "应用软件｜税务金融软件",
    "WDAY": "企业SaaS｜人力资本管理",
    "NVDA": "AI GPU｜算力平台",
    "AVGO": "AI ASIC｜半导体",
    "MRVL": "AI ASIC｜数据中心网络",
    "MU": "存储｜HBM",
    "COHR": "光通信｜材料器件",
    "LITE": "光模块｜通信器件",
    "NOK": "光网络｜通信设备",
    "ANET": "数据中心网络｜交换机",
    "NVO": "GLP-1｜生物医药",
    "ISRG": "手术机器人｜医疗器械",
    "BSX": "医疗器械",
    "COIN": "加密交易平台",
    "HOOD": "金融科技｜券商",
    "VST": "电力｜AI电力",
    "CEG": "核电｜电力",
    "GLW": "科技",
    "ETN": "电气设备｜电网",
    "CRWV": "AI云｜算力租赁",
    "NBIS": "AI云｜算力基础设施",
}


def localize_sector(sector: object = None, industry: object = None) -> str:
    provider_sector, provider_industry = _split_provider_track(_clean(sector), _clean(industry))
    sector_label = _localized_label(SECTOR_LOCALIZATION, provider_sector)
    industry_label = _localized_label(INDUSTRY_LOCALIZATION, provider_industry)
    labels = [label for label in (sector_label, industry_label) if label]
    if len(labels) == 2 and labels[0] == labels[1]:
        labels = labels[:1]
    if labels:
        return "｜".join(labels)
    return "赛道待补"


def get_ticker_research_track(
    ticker: object,
    provider_sector: object = None,
    provider_industry: object = None,
    watchlist_metadata: dict[str, Any] | None = None,
) -> str:
    symbol = _clean(ticker).upper()
    if symbol in TICKER_RESEARCH_TRACKS:
        return TICKER_RESEARCH_TRACKS[symbol]
    metadata = watchlist_metadata or {}
    metadata_track = _clean(
        metadata.get("research_track")
        or metadata.get("researchTrack")
        or metadata.get("track")
        or metadata.get("theme")
    )
    if metadata_track and not _looks_like_raw_english(metadata_track):
        return metadata_track
    return localize_sector(provider_sector, provider_industry)


def format_company_track(company_name: object, sector: object, industry: object, ticker: object) -> tuple[str, str]:
    symbol = _clean(ticker).upper()
    company = _clean(company_name) or symbol or "—"
    track = get_ticker_research_track(symbol, sector, industry)
    return company, track or "赛道待补"


def _split_provider_track(sector: str, industry: str) -> tuple[str, str]:
    if industry:
        return sector, industry
    for separator in (" / ", "/", "｜", "|"):
        if separator in sector:
            parts = [part.strip() for part in sector.split(separator, 1)]
            return parts[0], parts[1] if len(parts) > 1 else ""
    return sector, ""


def _localized_label(mapping: dict[str, str], value: str) -> str:
    if not value:
        return ""
    normalized = _normalize(value)
    if normalized in mapping:
        return mapping[normalized]
    if _looks_like_raw_english(value):
        return ""
    return value


def _clean(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"none", "nan", "n/a", "na", "-", "—"}:
        return ""
    return " ".join(text.split())


def _normalize(value: str) -> str:
    return _clean(value).lower().replace("&amp;", "&")


def _looks_like_raw_english(value: str) -> bool:
    text = _clean(value)
    if not text:
        return False
    has_ascii_letter = any("a" <= char.lower() <= "z" for char in text)
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
    return has_ascii_letter and not has_cjk
