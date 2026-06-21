from __future__ import annotations

from pathlib import Path
from typing import Any

from data.disclosure_pipeline import DisclosurePipeline
from data.disclosure_store import DisclosureStore
from data.fundamentals import FundamentalCache
from data.review_queue_builder import ReviewQueueBuilder, ReviewQueueStore
from data.sec_client import SECClient


AI_CLOUD_SEC_MODELS = {
    "CRWV": "AI_CLOUD_INFRA",
    "NBIS": "AI_INFRA_HIGH_RISK",
}
SEC_TEXT_SOURCE_TYPES = {"SEC_8K", "SEC_10Q", "SEC_10K"}
REFRESH_STATUS_LABELS = {
    "success": "成功",
    "partial": "部分完成",
    "failed": "失败",
}


def refresh_ai_cloud_sec_disclosures(
    symbol: str,
    *,
    sec_client: SECClient | None = None,
    db_path: Path | None = None,
    force_refresh: bool = True,
) -> dict[str, Any]:
    normalized = str(symbol or "").strip().upper()
    result: dict[str, Any] = {
        "symbol": normalized,
        "status": "failed",
        "statusLabel": REFRESH_STATUS_LABELS["failed"],
        "modelType": None,
        "fetchedFilings": [],
        "cachedTextCount": 0,
        "extractedCandidateCount": 0,
        "missingFieldCount": 0,
        "errors": [],
    }
    if not normalized:
        result["errors"].append("缺少股票代码")
        return result

    model_type = AI_CLOUD_SEC_MODELS.get(normalized)
    if not model_type:
        result["errors"].append("仅支持 CRWV / NBIS 的 AI 云 SEC 披露刷新")
        return result
    result["modelType"] = model_type

    disclosure_store = DisclosureStore(db_path) if db_path else DisclosureStore()
    queue_store = ReviewQueueStore(db_path or disclosure_store.path, disclosure_store=disclosure_store)
    fundamental_cache = FundamentalCache(db_path or disclosure_store.path)

    pipeline_result = DisclosurePipeline(store=disclosure_store, sec_client=sec_client or SECClient()).run(
        normalized,
        model_type=model_type,
        current_snapshot=_snapshot_for_symbol(normalized, model_type),
        force_refresh=force_refresh,
    )
    queue_result = ReviewQueueBuilder(
        queue_store=queue_store,
        disclosure_store=disclosure_store,
        fundamental_cache=fundamental_cache,
    ).build_review_queue_for_symbol(normalized)

    fetched_filings = [
        {
            "sourceType": row.get("sourceType"),
            "url": row.get("url"),
            "title": row.get("errorMessage"),
        }
        for row in pipeline_result.get("logs", [])
        if row.get("status") == "fetched" and row.get("sourceType") in SEC_TEXT_SOURCE_TYPES
    ]
    errors = [_sec_log_error_message(row) for row in pipeline_result.get("logs", []) if row.get("status") == "failed"]
    extracted_count = len(
        [
            row
            for row in pipeline_result.get("saved", [])
            if row.get("sourceType") in SEC_TEXT_SOURCE_TYPES and row.get("evidenceText")
        ]
    )
    missing_count = queue_result.item_type_counts.get("missing_kpi", len(pipeline_result.get("missing", [])))

    status = _refresh_status(cached_text_count=len(fetched_filings), extracted_count=extracted_count, errors=errors)
    result.update(
        {
            "status": status,
            "statusLabel": _refresh_status_label(status),
            "fetchedFilings": fetched_filings,
            "cachedTextCount": len(fetched_filings),
            "extractedCandidateCount": extracted_count,
            "missingFieldCount": missing_count,
            "errors": errors,
        }
    )
    return result


def _snapshot_for_symbol(symbol: str, model_type: str) -> dict[str, str]:
    return {"ticker": symbol, "symbol": symbol, "modelType": model_type}


def _refresh_status(*, cached_text_count: int, extracted_count: int, errors: list[str]) -> str:
    if extracted_count > 0:
        return "success" if not errors else "partial"
    if cached_text_count > 0 or not errors:
        return "partial"
    return "failed"


def _refresh_status_label(status: str) -> str:
    return REFRESH_STATUS_LABELS.get(str(status or ""), "待复核")


def _sec_log_error_message(row: dict[str, Any]) -> str:
    source = str(row.get("sourceType") or "SEC").strip()
    detail = str(row.get("errorMessage") or row.get("url") or "").strip()
    if detail:
        return f"{source}: {detail}"
    return f"{source}: 请求失败"
