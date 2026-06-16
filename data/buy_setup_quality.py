from __future__ import annotations

from typing import Any


SETUP_STATUS_TEXT = {
    "HIGH_QUALITY_SETUP": "高质量买点",
    "STARTER_REASONABLE": "试仓合理",
    "SETUP_WATCH": "观察级 Setup",
    "WEAK_SETUP": "买入质量偏弱",
    "HIGH_RISK_SETUP": "高风险 Setup",
    "DATA_INSUFFICIENT": "数据不足",
}


def setup_quality_status(setup_score: Any) -> str:
    score = _number(setup_score)
    if score is None:
        return "DATA_INSUFFICIENT"
    if score >= 80:
        return "HIGH_QUALITY_SETUP"
    if score >= 70:
        return "STARTER_REASONABLE"
    if score >= 60:
        return "SETUP_WATCH"
    if score >= 50:
        return "WEAK_SETUP"
    return "HIGH_RISK_SETUP"


def setup_quality_text(setup_score: Any) -> str:
    return SETUP_STATUS_TEXT[setup_quality_status(setup_score)]


def setup_quality_note(
    setup_score: Any,
    *,
    volume_acceptance_score: Any = None,
) -> str:
    status = setup_quality_status(setup_score)
    score = _number(setup_score)
    volume_score = _number(volume_acceptance_score)
    score_text = "暂缺" if score is None else f"{score:.1f}".rstrip("0").rstrip(".")
    prefix = f"{SETUP_STATUS_TEXT[status]}：Setup 综合 {score_text}"
    if status == "DATA_INSUFFICIENT":
        prefix = "数据不足：Setup 综合暂缺"
    if volume_score is not None and volume_score < 50:
        return f"{prefix}；量价未确认 / 承接不足，等待确认。"
    if status == "HIGH_QUALITY_SETUP":
        return f"{prefix}；仍需结合当前子区和量价确认。"
    if status == "STARTER_REASONABLE":
        return f"{prefix}；试仓质量合理，仍按量价确认复核。"
    if status == "SETUP_WATCH":
        return f"{prefix}；观察级 setup，量价未确认，不建议新增。"
    if status == "WEAK_SETUP":
        return f"{prefix}；买入质量偏弱，建议等待更清晰确认。"
    if status == "HIGH_RISK_SETUP":
        return f"{prefix}；高风险 setup，建议先复核结构和承接。"
    return f"{prefix}；等待数据补齐。"


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
