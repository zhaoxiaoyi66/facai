from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Iterable

from ai.qwen_client import EVIDENCE_ONLY_RULE, QwenClient, QwenProviderError, extract_chat_content
from data.ai_review_assistant import AIReviewStore
from data.extract_metric_from_text import validate_extracted_metric_candidate
from data.metric_variants import metric_variant_for_key, target_basis_for_metric
from data.normalize_metric_value import (
    build_evidence_window,
    deterministic_precheck,
    normalize_metric_period,
    normalize_metric_value,
)
from data.review_queue_builder import ReviewQueueStore


QWEN_REVIEW_DECISIONS = {
    "recommend_approve",
    "recommend_reject",
    "recommend_correct",
    "needs_human_review",
    "needs_more_source",
    "not_enough_evidence",
}
QWEN_EVIDENCE_MATCHES = {"exact_match", "partial_match", "mismatch", "no_evidence"}
QWEN_PERIOD_MATCHES = {"exact", "ambiguous", "mismatch"}
QWEN_UNIT_MATCHES = {"exact", "ambiguous", "mismatch"}
QWEN_RISK_LEVELS = {"low", "medium", "high"}
QWEN_REVIEW_PROMPT_VERSION = "qwen-evidence-review-v2"
DEFAULT_QWEN_REVIEW_MAX_ITEMS = 20
QWEN_REVIEWABLE_ITEM_TYPES = {"extracted_value"}
QWEN_EVIDENCE_SOURCE_TYPES = {"SEC_8K", "SEC_10Q", "SEC_10K", "IR_RELEASE", "IR_PRESENTATION", "FMP_TRANSCRIPT"}
TERMINAL_REVIEW_STATUSES = {"approved", "rejected", "manually_corrected", "auto_archived", "duplicate_archived"}
EXCLUDED_SOURCE_TYPES = {"CALCULATED", "FMP"}
QWEN_NOT_SUITABLE_REASON = "不适合AI证据复核，需人工/数据源处理"
AI_TRIAGE_STATUSES = {
    "auto_approved_by_ai",
    "ai_recommend_approve",
    "ai_recommend_correct",
    "ai_recommend_reject",
    "ai_needs_human_review",
    "ai_not_enough_evidence",
    "ai_invalid_output",
    "ai_skipped",
    "extraction_rejected_by_rule",
}


QWEN_REVIEW_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "qwen_review_evidence_validation",
        "description": "Evidence-only pre-review for a manual review queue item.",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "aiDecision": {"type": "string", "enum": sorted(QWEN_REVIEW_DECISIONS)},
                "correctedValue": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "correctedUnit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "correctedPeriod": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "confidenceScore": {"type": "number", "minimum": 0, "maximum": 1},
                "evidenceMatch": {"type": "string", "enum": sorted(QWEN_EVIDENCE_MATCHES)},
                "periodMatch": {"type": "string", "enum": sorted(QWEN_PERIOD_MATCHES)},
                "unitMatch": {"type": "string", "enum": sorted(QWEN_UNIT_MATCHES)},
                "riskLevel": {"type": "string", "enum": sorted(QWEN_RISK_LEVELS)},
                "explanationZh": {"type": "string"},
                "evidenceQuote": {"type": "string"},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "aiDecision",
                "correctedValue",
                "correctedUnit",
                "correctedPeriod",
                "confidenceScore",
                "evidenceMatch",
                "periodMatch",
                "unitMatch",
                "riskLevel",
                "explanationZh",
                "evidenceQuote",
                "warnings",
            ],
        },
        "strict": True,
    },
}


QWEN_REVIEW_SYSTEM_PROMPT = f"""你是数据证据校验助手，只负责判断 review item 的 evidenceWindow 是否支持 extractedValue。
{EVIDENCE_ONLY_RULE}
You must not use your own world knowledge. Only evaluate the provided evidence text.
Use metricVariant and targetBasis when deciding which number the evidence supports. If targetBasis is reported_yoy, match the reported year-over-year value; if targetBasis is constant_currency_yoy, match the constant-currency value.
不允许使用模型自身知识。不允许自己查最新资料。你不能联网，不能根据常识补全事实。
只能根据 extractedText / evidenceWindow、sourceTitle、metricPeriod、metricKey、extractedValue 和 unit 判断。
如果 evidenceWindow 不能支持 extractedValue，返回 not_enough_evidence。
如果期间不明确，periodMatch = ambiguous；如果单位不明确，unitMatch = ambiguous。
sourcePublishedDate 是文件发布日期，不是财报期间。不要把 2026-01-28 这类 filing date 与 Q4 2025 直接判为 mismatch。
如果原文显示 Q4 2025，而 sourcePublishedDate 是 2026-01-28，这是正常情况。
如果单位是百分比，25% 表示 normalizedValue = 25.0 percent；不要把 0.25 percent 当作 0.25%。
定性风险不能 recommend_approve，只能 needs_human_review 或 needs_more_source。
不允许输出 schema 外字段。只返回 JSON，不要输出解释文字、Markdown 或代码块。"""


@dataclass(frozen=True)
class QwenReviewRunResult:
    reviewed: int
    skipped: int
    auto_approved: int
    needs_human: int
    not_configured: bool = False
    errors: list[str] | None = None
    eligible_for_qwen: int = 0
    skipped_not_suitable: int = 0
    invalid_json: int = 0
    not_enough_evidence: int = 0
    machine_verified: int = 0


class QwenReviewService:
    def __init__(
        self,
        queue_store: ReviewQueueStore | None = None,
        ai_store: AIReviewStore | None = None,
        client: QwenClient | None = None,
        max_items: int | None = None,
    ) -> None:
        self.queue_store = queue_store or ReviewQueueStore()
        self.ai_store = ai_store or AIReviewStore(self.queue_store.path)
        self.client = client or QwenClient()
        self.max_items = max_items or int(os.getenv("AI_REVIEW_MAX_ITEMS") or DEFAULT_QWEN_REVIEW_MAX_ITEMS)

    @property
    def configured(self) -> bool:
        return self.client.configured

    def review_rows(self, rows: list[dict], max_items: int | None = None) -> QwenReviewRunResult:
        limit = min(max_items or self.max_items, self.max_items, DEFAULT_QWEN_REVIEW_MAX_ITEMS)
        stats = qwen_review_efficiency_stats(rows)
        candidates = qwen_review_candidates(rows)[:limit]
        if not self.configured:
            return QwenReviewRunResult(
                0,
                len(candidates),
                0,
                0,
                not_configured=True,
                errors=[],
                eligible_for_qwen=stats["eligibleForQwenCount"],
                skipped_not_suitable=stats["skippedAsNotSuitableCount"],
                machine_verified=stats["machineVerifiedCount"],
            )

        reviewed = 0
        skipped = 0
        auto_approved = 0
        needs_human = 0
        errors: list[str] = []
        invalid_json = stats["invalidJsonCount"]
        not_enough_evidence = stats["notEnoughEvidenceCount"]
        for row in candidates:
            payload = build_qwen_review_input(row)
            input_hash = qwen_review_input_hash(payload)
            if self.ai_store.has_same_input_result(int(row["id"]), input_hash):
                skipped += 1
                continue
            try:
                result = self.review_item(row)
                applied_action = triage_qwen_review_result(row, result)
                ai_result_id = self.ai_store.save_result(
                    row,
                    _result_for_ai_store(result),
                    f"qwen:{self.client.model}",
                    input_hash,
                    applied_action,
                )
                applied_action = apply_qwen_review_result(
                    row,
                    result,
                    self.queue_store,
                    ai_store=self.ai_store,
                    ai_review_result_id=ai_result_id,
                    triage_status=applied_action,
                )
                reviewed += 1
                if applied_action == "auto_approved_by_ai":
                    auto_approved += 1
                if applied_action in {
                    "ai_needs_human_review",
                    "ai_recommend_reject",
                    "ai_recommend_correct",
                    "ai_not_enough_evidence",
                    "ai_invalid_output",
                }:
                    needs_human += 1
                if applied_action == "ai_not_enough_evidence":
                    not_enough_evidence += 1
                if applied_action == "ai_invalid_output":
                    invalid_json += 1
            except Exception as exc:  # pragma: no cover - defensive UI path
                errors.append(f"{row.get('symbol')} {row.get('metricKey')}: {exc}")
        return QwenReviewRunResult(
            reviewed,
            skipped,
            auto_approved,
            needs_human,
            errors=errors,
            eligible_for_qwen=stats["eligibleForQwenCount"],
            skipped_not_suitable=stats["skippedAsNotSuitableCount"],
            invalid_json=invalid_json,
            not_enough_evidence=not_enough_evidence,
            machine_verified=stats["machineVerifiedCount"],
        )

    def review_item_ids(self, review_item_ids: Iterable[int]) -> QwenReviewRunResult:
        ids = {int(item_id) for item_id in review_item_ids}
        rows = [row for row in self.queue_store.list_items() if int(row["id"]) in ids]
        return self.review_rows(rows)

    def review_item(self, row: dict) -> dict:
        payload = build_qwen_review_input(row)
        messages = [
            {"role": "system", "content": QWEN_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
        ]
        try:
            raw = self.client.chat_completion(messages, response_format=QWEN_REVIEW_RESPONSE_SCHEMA, model=self.client.model)
            text = extract_chat_content(raw)
        except QwenProviderError:
            raise
        result = parse_qwen_review_json(text)
        return enforce_qwen_evidence_only(row, result)


def build_qwen_review_input(row: dict) -> dict:
    metric_key = row.get("metricKey")
    display_name = row.get("displayName") or metric_key
    metric_variant = row.get("metricVariant") or metric_variant_for_key(str(metric_key or ""))
    target_basis = row.get("targetBasis") or target_basis_for_metric(metric_variant or metric_key)
    raw_text = row.get("evidenceText")
    if raw_text is None:
        raw_text = row.get("extractedText") or ""
    normalized = normalize_metric_value(row.get("value"), row.get("unit"), str(raw_text), str(metric_key or ""))
    periods = normalize_metric_period(row)
    evidence = build_evidence_window(
        raw_text,
        aliases=[metric_key, display_name],
        value_display=normalized.displayValue,
    )
    precheck_row = {
        **row,
        "normalizedValue": normalized.normalizedValue,
        "unit": normalized.unit,
        "displayValue": normalized.displayValue,
        "evidenceWindow": evidence.evidenceWindow,
        "metricPeriod": periods.metricPeriod,
        "periodDisplay": periods.periodDisplay,
        "metricVariant": metric_variant,
        "targetBasis": target_basis,
    }
    return {
        "symbol": row.get("symbol"),
        "metricKey": metric_key,
        "metricVariant": metric_variant,
        "metricDisplayName": display_name,
        "targetValue": normalized.normalizedValue,
        "targetUnit": normalized.unit,
        "targetPeriod": periods.metricPeriod or periods.periodDisplay,
        "targetBasis": target_basis,
        "extractedValue": row.get("value"),
        "rawValue": normalized.rawValue,
        "normalizedValue": normalized.normalizedValue,
        "extractedValueDisplay": normalized.displayValue,
        "unit": normalized.unit,
        "valueScale": normalized.valueScale,
        "sourcePublishedDate": periods.sourcePublishedDate,
        "fiscalPeriod": periods.fiscalPeriod,
        "metricPeriod": periods.metricPeriod,
        "periodDisplay": periods.periodDisplay,
        "freshnessStatus": row.get("freshnessStatus") or "active_current",
        "sourceType": row.get("sourceType"),
        "sourceTitle": row.get("sourceDocumentTitle"),
        "evidenceWindow": evidence.evidenceWindow,
        "aliasesMatched": evidence.aliasesMatched,
        "evidenceInsufficient": evidence.evidenceInsufficient,
        "deterministicPrecheck": deterministic_precheck(precheck_row),
        "confidence": row.get("confidence"),
        "affects": sorted(_affects(row.get("affects"))),
        "itemType": row.get("itemType"),
    }


def qwen_review_input_hash(payload: dict) -> str:
    stable = json.dumps(
        {
            "symbol": payload.get("symbol"),
            "metricKey": payload.get("metricKey"),
            "metricVariant": payload.get("metricVariant"),
            "targetBasis": payload.get("targetBasis"),
            "targetPeriod": payload.get("targetPeriod"),
            "freshnessStatus": payload.get("freshnessStatus"),
            "normalizedValue": payload.get("normalizedValue"),
            "unit": payload.get("unit"),
            "metricPeriod": payload.get("metricPeriod"),
            "evidenceWindow": payload.get("evidenceWindow"),
            "itemType": payload.get("itemType"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def qwen_review_candidates(rows: list[dict]) -> list[dict]:
    return [row for row in rows if _is_qwen_review_candidate(row)][:DEFAULT_QWEN_REVIEW_MAX_ITEMS]


def qwen_review_eligibility(row: dict) -> tuple[bool, str]:
    if str(row.get("reviewStatus") or "") in TERMINAL_REVIEW_STATUSES:
        return False, "already_reviewed"
    if str(row.get("freshnessStatus") or "active_current") == "historical_value":
        return False, "historical_value"
    if str(row.get("reviewStatus") or "") == "needs_evidence":
        return False, "missing_evidence_text"
    if str(row.get("aiTriageStatus") or "") == "extraction_rejected_by_rule":
        return False, "rejected_extraction_candidate"
    if str(row.get("itemType") or "") not in QWEN_REVIEWABLE_ITEM_TYPES:
        return False, "unsupported_item_type"
    if str(row.get("reviewStatus") or "") != "pending_review":
        return False, "status_not_pending_review"
    if row.get("value") is None:
        return False, "missing_value"
    source_type = str(row.get("sourceType") or "")
    if source_type in EXCLUDED_SOURCE_TYPES or source_type not in QWEN_EVIDENCE_SOURCE_TYPES:
        return False, "unsupported_source_type"
    if not str(row.get("sourceUrl") or "").strip():
        return False, "missing_source_url"
    if str(row.get("resolutionStatus") or "") in {"calculated", "not_applicable"}:
        return False, "resolution_not_reviewable"
    evidence_text = row.get("evidenceText")
    if evidence_text is None:
        evidence_text = row.get("extractedText") or ""
    if not str(evidence_text or "").strip():
        return False, "missing_evidence_text"
    valid, reason = validate_extracted_metric_candidate(str(row.get("metricKey") or ""), str(evidence_text or ""))
    if not valid:
        return False, f"metric_validator_failed:{reason}"
    payload = build_qwen_review_input(row)
    if not payload.get("metricVariant"):
        return False, "missing_metric_variant"
    if not payload.get("targetBasis"):
        return False, "missing_target_basis"
    if payload.get("normalizedValue") is None:
        return False, "missing_value"
    if not payload.get("unit"):
        return False, "missing_unit"
    if not payload.get("metricPeriod") and not payload.get("periodDisplay"):
        return False, "missing_period"
    if not payload.get("evidenceWindow") or payload.get("evidenceInsufficient"):
        return False, "evidence_insufficient"
    if payload.get("deterministicPrecheck") == "fail":
        return False, "deterministic_precheck_failed"
    return True, "eligible"


def qwen_review_efficiency_stats(rows: list[dict]) -> dict:
    stats = {
        "eligibleForQwenCount": 0,
        "qwenReviewedCount": 0,
        "autoApprovedCount": 0,
        "humanRequiredCount": 0,
        "skippedAsNotSuitableCount": 0,
        "invalidJsonCount": 0,
        "notEnoughEvidenceCount": 0,
        "machineVerifiedCount": 0,
    }
    for row in rows:
        eligible, _reason = qwen_review_eligibility(row)
        triage = str(row.get("aiTriageStatus") or "")
        if eligible:
            stats["eligibleForQwenCount"] += 1
            if build_qwen_review_input(row).get("deterministicPrecheck") == "exact":
                stats["machineVerifiedCount"] += 1
        else:
            stats["skippedAsNotSuitableCount"] += 1
        if triage:
            stats["qwenReviewedCount"] += 1
        if triage == "auto_approved_by_ai":
            stats["autoApprovedCount"] += 1
        if triage in {"ai_recommend_correct", "ai_recommend_reject", "ai_needs_human_review", "ai_not_enough_evidence", "ai_invalid_output"}:
            stats["humanRequiredCount"] += 1
        if triage == "ai_invalid_output":
            stats["invalidJsonCount"] += 1
        if triage == "ai_not_enough_evidence":
            stats["notEnoughEvidenceCount"] += 1
    return stats


def _is_qwen_review_candidate(row: dict) -> bool:
    eligible, _reason = qwen_review_eligibility(row)
    return eligible


def parse_qwen_review_json(text: str) -> dict:
    try:
        return validate_qwen_review_result(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        repaired = _repair_json_once(text)
        if repaired is None:
            return _fallback_human_review("json_parse_failed")
        try:
            return validate_qwen_review_result(json.loads(repaired))
        except (json.JSONDecodeError, ValueError):
            return _fallback_human_review("json_parse_failed")


def validate_qwen_review_result(result: dict) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Qwen review result must be a JSON object")
    allowed = set(QWEN_REVIEW_RESPONSE_SCHEMA["json_schema"]["schema"]["properties"].keys())
    extra = set(result) - allowed
    if extra:
        raise ValueError(f"unsupported fields: {sorted(extra)}")
    missing = set(QWEN_REVIEW_RESPONSE_SCHEMA["json_schema"]["schema"]["required"]) - set(result)
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    normalized = dict(result)
    if normalized["aiDecision"] not in QWEN_REVIEW_DECISIONS:
        raise ValueError("invalid aiDecision")
    if normalized["evidenceMatch"] not in QWEN_EVIDENCE_MATCHES:
        raise ValueError("invalid evidenceMatch")
    if normalized["periodMatch"] not in QWEN_PERIOD_MATCHES:
        raise ValueError("invalid periodMatch")
    if normalized["unitMatch"] not in QWEN_UNIT_MATCHES:
        raise ValueError("invalid unitMatch")
    if normalized["riskLevel"] not in QWEN_RISK_LEVELS:
        raise ValueError("invalid riskLevel")
    score = float(normalized["confidenceScore"])
    if score < 0 or score > 1:
        raise ValueError("confidenceScore out of range")
    normalized["confidenceScore"] = score
    normalized["warnings"] = [str(item) for item in normalized.get("warnings") or []]
    for key in ("correctedValue", "correctedUnit", "correctedPeriod"):
        if normalized.get(key) == "":
            normalized[key] = None
    if normalized.get("correctedUnit") == "percent" and normalized.get("correctedValue") is not None:
        fixed = normalize_metric_value(normalized["correctedValue"], "percent", str(normalized.get("evidenceQuote") or ""))
        normalized["correctedValue"] = fixed.normalizedValue
        normalized["correctedUnit"] = fixed.unit
    return normalized


def enforce_qwen_evidence_only(row: dict, result: dict) -> dict:
    normalized = validate_qwen_review_result(result)
    item_type = str(row.get("itemType") or "")
    if item_type != "extracted_value" and normalized["aiDecision"] == "recommend_approve":
        normalized["aiDecision"] = "needs_human_review"
        normalized["warnings"].append("not_suitable_for_evidence_review")
    payload = build_qwen_review_input(row)
    if normalized["evidenceMatch"] == "partial_match" and payload.get("deterministicPrecheck") == "exact":
        normalized["evidenceMatch"] = "exact_match"
        if "qwen_partial_but_deterministic_exact" not in normalized["warnings"]:
            normalized["warnings"].append("qwen_partial_but_deterministic_exact")
    if normalized["periodMatch"] == "mismatch" and payload.get("metricPeriod"):
        period_text = str(payload.get("metricPeriod") or "")
        evidence_raw = str(payload.get("evidenceWindow") or "")
        if _period_text_in_evidence(period_text, evidence_raw):
            normalized["periodMatch"] = "exact"
    evidence_text = _normalize_text(str(payload.get("evidenceWindow") or row.get("extractedText") or row.get("explanation") or ""))
    quote = str(normalized.get("evidenceQuote") or "").strip()
    needs_quote = normalized["aiDecision"] in {"recommend_approve", "recommend_correct"} or normalized["evidenceMatch"] in {
        "exact_match",
        "partial_match",
    }
    if needs_quote and (not quote or _normalize_text(quote) not in evidence_text):
        normalized["aiDecision"] = "needs_human_review"
        normalized["evidenceMatch"] = "no_evidence" if not quote else "partial_match"
        normalized["riskLevel"] = "high"
        if "hallucination_risk" not in normalized["warnings"]:
            normalized["warnings"].append("hallucination_risk")
        if not quote:
            normalized["evidenceQuote"] = ""
    return normalized


def triage_qwen_review_result(row: dict, result: dict) -> str:
    normalized = validate_qwen_review_result(result)
    if str(row.get("reviewStatus") or "") in TERMINAL_REVIEW_STATUSES:
        return "ai_skipped"
    if "json_parse_failed" in set(normalized.get("warnings") or []):
        return "ai_invalid_output"
    if _can_qwen_auto_approve(row, normalized):
        return "auto_approved_by_ai"
    if normalized["evidenceMatch"] == "mismatch" or normalized["aiDecision"] == "recommend_reject":
        return "ai_recommend_reject"
    if normalized["aiDecision"] == "recommend_correct" and normalized.get("correctedValue") is not None:
        return "ai_recommend_correct"
    if normalized["aiDecision"] == "not_enough_evidence" or normalized["evidenceMatch"] == "no_evidence":
        return "ai_not_enough_evidence"
    if normalized["aiDecision"] in {"needs_human_review", "needs_more_source"}:
        return "ai_needs_human_review"
    if _requires_human_even_if_approve(row, normalized):
        return "ai_needs_human_review"
    return "ai_recommend_approve"


def apply_qwen_review_result(
    row: dict,
    result: dict,
    queue_store: ReviewQueueStore,
    ai_store: AIReviewStore | None = None,
    ai_review_result_id: int | None = None,
    triage_status: str | None = None,
) -> str:
    normalized = validate_qwen_review_result(result)
    item_id = int(row["id"])
    triage = triage_status or triage_qwen_review_result(row, normalized)
    correction_candidate = _correction_candidate(normalized) if triage == "ai_recommend_correct" else None
    if triage == "auto_approved_by_ai":
        old, new = queue_store.apply_ai_auto_approval(
            item_id,
            ai_review_result_id=ai_review_result_id,
            evidence_quote=normalized.get("evidenceQuote"),
            explanation_zh=normalized.get("explanationZh"),
        )
        _log_ai_audit(ai_store, item_id, "auto_approved_by_ai", old, new, "ai", ai_review_result_id, normalized.get("explanationZh"))
        return triage
    queue_store.set_ai_triage(
        item_id,
        triage,
        ai_review_result_id=ai_review_result_id,
        evidence_quote=normalized.get("evidenceQuote"),
        explanation_zh=normalized.get("explanationZh"),
        correction_candidate=correction_candidate,
    )
    return triage


def _can_qwen_auto_approve(row: dict, result: dict) -> bool:
    affects = {part.lower() for part in _affects(row.get("affects"))}
    return (
        str(row.get("itemType") or "") == "extracted_value"
        and str(row.get("reviewStatus") or "") == "pending_review"
        and str(row.get("freshnessStatus") or "active_current") != "historical_value"
        and bool(row.get("metricVariant") or metric_variant_for_key(str(row.get("metricKey") or "")))
        and bool(row.get("targetBasis") or target_basis_for_metric(row.get("metricVariant") or row.get("metricKey")))
        and str(row.get("sourceType") or "") != "FMP_TRANSCRIPT"
        and result["aiDecision"] == "recommend_approve"
        and result["confidenceScore"] >= 0.90
        and result["evidenceMatch"] == "exact_match"
        and result["periodMatch"] == "exact"
        and result["unitMatch"] == "exact"
        and result["riskLevel"] == "low"
        and "action" not in affects
        and "maxposition" not in affects
        and "risk" not in affects
    )


def _requires_human_even_if_approve(row: dict, result: dict) -> bool:
    item_type = str(row.get("itemType") or "")
    metric_key = str(row.get("metricKey") or "").lower()
    affects = {part.lower() for part in _affects(row.get("affects"))}
    blocked_metric_fragments = {
        "regulatory",
        "patent",
        "pipeline",
        "disruption",
        "concentration",
        "crypto",
        "risk",
    }
    return (
        item_type in {"qualitative_risk", "derived_low_confidence", "manual_override_needed"}
        or str(row.get("sourceType") or "") == "FMP_TRANSCRIPT"
        or "action" in affects
        or "maxposition" in affects
        or ("risk" in affects and result.get("riskLevel") != "low")
        or any(fragment in metric_key for fragment in blocked_metric_fragments)
    )


def _result_for_ai_store(result: dict) -> dict:
    return {**validate_qwen_review_result(result), "hallucinationRisk": "hallucination_risk" in result.get("warnings", [])}


def _correction_candidate(result: dict) -> dict:
    normalized = validate_qwen_review_result(result)
    return {
        "originalValue": None,
        "correctedValue": normalized.get("correctedValue"),
        "correctedUnit": normalized.get("correctedUnit"),
        "correctedPeriod": normalized.get("correctedPeriod"),
        "correctionReason": normalized.get("explanationZh"),
        "evidenceQuote": normalized.get("evidenceQuote"),
    }


def _log_ai_audit(
    ai_store: AIReviewStore | None,
    item_id: int,
    action: str,
    old: dict | None,
    new: dict | None,
    actor: str,
    ai_review_result_id: int | None,
    reason: str | None,
) -> None:
    if not ai_store:
        return
    ai_store.log_audit(
        item_id,
        action,
        str(old.get("reviewStatus")) if old else None,
        str(new.get("reviewStatus")) if new else None,
        old.get("value") if old else None,
        new.get("value") if new else None,
        actor,
        ai_review_result_id,
        reason,
    )


def _repair_json_once(text: str) -> str | None:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    return match.group(0) if match else None


def _fallback_human_review(reason: str) -> dict:
    return {
        "aiDecision": "needs_human_review",
        "correctedValue": None,
        "correctedUnit": None,
        "correctedPeriod": None,
        "confidenceScore": 0.0,
        "evidenceMatch": "no_evidence",
        "periodMatch": "ambiguous",
        "unitMatch": "ambiguous",
        "riskLevel": "high",
        "explanationZh": "Qwen 返回内容无法解析为合规 JSON，已转人工复核。",
        "evidenceQuote": "",
        "warnings": [reason],
    }


def _affects(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item}
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _period_text_in_evidence(period: str, evidence: str) -> bool:
    compact_period = str(period or "").upper().replace(" ", "")
    compact_evidence = str(evidence or "").upper().replace(" ", "")
    if compact_period and compact_period in compact_evidence:
        return True
    match = re.search(r"\b(20\d{2})\s*(Q[1-4])\b", str(period or ""), flags=re.IGNORECASE)
    if match:
        alt = f"{match.group(2)}{match.group(1)}".upper()
        return alt in compact_evidence
    match = re.search(r"\b(Q[1-4])\s*(20\d{2})\b", str(period or ""), flags=re.IGNORECASE)
    if match:
        alt = f"{match.group(2)}{match.group(1)}".upper()
        return alt in compact_evidence
    return False
