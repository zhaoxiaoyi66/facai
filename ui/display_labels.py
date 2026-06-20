from __future__ import annotations

from typing import Any


DISPLAY_LABELS: dict[str, str] = {
    "AI Stock Radar": "研报中心",
    "AI Stock Radar Research": "研报中心",
    "Radar": "研报中心",
    "Radar 买区": "研报中心",
    "buy zone": "买区",
    "discipline review": "交易复盘",
    "trade review": "交易复盘",
    "candidate": "自动匹配",
    "confirmed": "人工锁定",
    "auto usable": "自动可用",
    "anchor_source": "锚点来源",
    "FINAL": "已固定锚点",
    "PROVISIONAL": "临时锚点",
    "None": "缺少数据",
    "DATA_INSUFFICIENT": "数据不足",
    "DATA_MISSING": "数据不足",
    "risk_note": "备注",
    "mapping_confidence": "映射可信度",
    "TRADINGVIEW_WEBHOOK_SAMPLE": "TradingView Webhook 样本",
    "ALPACA_BOATS_SAMPLE": "Alpaca BOATS 样本",
    "OFFICIAL_BROKER_1M": "券商夜盘 1m 样本",
    "FALLBACK_REGULAR_CLOSE": "常规收盘回退，仅观察",
    "MANUAL_CSV": "CSV 手动导入",
}


def display_label(value: Any, default: str | None = None) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return default or "缺少数据"
    return DISPLAY_LABELS.get(text, DISPLAY_LABELS.get(text.lower(), default or text))


def replace_display_terms(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "缺少数据"
    result = text
    for raw, label in sorted(DISPLAY_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(raw, label)
    return result
