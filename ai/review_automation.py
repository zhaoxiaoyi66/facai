from __future__ import annotations

from dataclasses import dataclass

from ai.qwen_review_service import QwenReviewRunResult, QwenReviewService, qwen_review_eligibility
from data.ai_review_assistant import AIReviewStore
from data.review_queue_builder import ReviewQueueStore


AUTOMATION_MODES = {"conservative", "assisted", "autopilot", "autopilot_with_guardrails"}
AUTOMATION_DECISIONS = {
    "auto_approve",
    "auto_archive",
    "ai_recommend_approve",
    "ai_recommend_correct",
    "ai_recommend_reject",
    "needs_human_review",
    "needs_more_source",
    "needs_estimates",
    "proxy_available",
    "low_priority_ignore",
    "not_material",
}


@dataclass(frozen=True)
class AutomationRunResult:
    eligible: int
    processed: int
    skipped: int
    failed: int
    auto_approved: int = 0
    auto_archived: int = 0
    needs_human: int = 0
    qwen_reviewed: int = 0
    errors: list[str] | None = None
    message: str = ""


class ReviewAutomationService:
    def __init__(
        self,
        queue_store: ReviewQueueStore | None = None,
        ai_store: AIReviewStore | None = None,
        qwen_service: QwenReviewService | None = None,
    ) -> None:
        self.queue_store = queue_store or ReviewQueueStore()
        self.ai_store = ai_store or AIReviewStore(self.queue_store.path)
        self.qwen_service = qwen_service or QwenReviewService(queue_store=self.queue_store, ai_store=self.ai_store)

    def automate_rows(self, rows: list[dict], mode: str = "autopilot_with_guardrails", selected_filters: dict | None = None) -> AutomationRunResult:
        mode = normalize_automation_mode(mode)
        started = _now_for_log()
        eligible_rows = [row for row in rows if _is_automation_candidate(row)]
        if not eligible_rows:
            result = AutomationRunResult(0, 0, len(rows), 0, message="当前筛选结果中没有符合条件的项目。")
            self.queue_store.log_operation("ai_automation", selected_filters, 0, 0, len(rows), 0, [], started)
            return result

        qwen_rows = [row for row in eligible_rows if qwen_review_eligibility(row)[0]]
        local_rows = [row for row in eligible_rows if row not in qwen_rows]
        qwen_result = self.qwen_service.review_rows(qwen_rows) if qwen_rows else QwenReviewRunResult(0, 0, 0, 0)

        processed = int(qwen_result.reviewed or 0)
        failed = len(qwen_result.errors or [])
        auto_archived = 0
        needs_human = int(qwen_result.needs_human or 0)
        errors = list(qwen_result.errors or [])

        for row in local_rows:
            try:
                automation = classify_review_item(row, mode)
                action = apply_automation_result(row, automation, self.queue_store, self.ai_store)
                processed += 1
                if action == "ai_auto_archived":
                    auto_archived += 1
                elif action in {"ai_needs_human_review", "ai_not_enough_evidence"}:
                    needs_human += 1
            except Exception as exc:  # pragma: no cover - defensive UI path
                failed += 1
                errors.append(f"{row.get('symbol')} {row.get('metricKey')}: {exc}")

        skipped = max(0, len(rows) - processed - failed)
        result = AutomationRunResult(
            eligible=len(eligible_rows),
            processed=processed,
            skipped=skipped,
            failed=failed,
            auto_approved=int(qwen_result.auto_approved or 0),
            auto_archived=auto_archived,
            needs_human=needs_human,
            qwen_reviewed=int(qwen_result.reviewed or 0),
            errors=errors,
            message=f"AI自动分流完成：处理 {processed} 条，自动确认 {int(qwen_result.auto_approved or 0)} 条，自动归档 {auto_archived} 条。",
        )
        self.queue_store.log_operation(
            "ai_automation",
            {**(selected_filters or {}), "mode": mode},
            len(eligible_rows),
            processed,
            skipped,
            failed,
            errors,
            started,
        )
        return result


def classify_review_item(row: dict, mode: str = "assisted") -> dict:
    mode = normalize_automation_mode(mode)
    item_type = str(row.get("itemType") or "")
    affects = _affects(row.get("affects"))
    can_affect_score = bool(affects & {"Quality", "Entry", "Risk"})
    can_affect_action = "Action" in affects
    can_affect_position = "maxPosition" in affects or "Position" in affects

    if item_type == "extracted_value":
        eligible, reason = qwen_review_eligibility(row)
        return _automation_result(
            "needs_more_source" if not eligible else "ai_recommend_approve",
            0.55 if not eligible else 0.75,
            can_affect_score,
            can_affect_action,
            can_affect_position,
            "补充原文证据后再做 Qwen 证据校验" if not eligible else "进入 Qwen 证据校验",
            f"extracted_value 需要证据验证；当前状态：{reason}",
            reason,
        )
    if item_type == "missing_kpi":
        if _low_impact(row) or (mode == "autopilot" and not can_affect_action and not can_affect_position and "Risk" not in affects):
            return _automation_result("auto_archive", 0.78, can_affect_score, can_affect_action, can_affect_position, "已有代理或低影响，归档为低优先级", "该缺口不应阻塞评分，保留补齐建议。", "不改变核心评分")
        return _automation_result("needs_more_source", 0.7, can_affect_score, can_affect_action, can_affect_position, _source_recommendation(row), "关键 KPI 仍需要数据源补齐。", "不自动批准缺失 KPI")
    if item_type == "derived_low_confidence":
        if _explanation_only(row) or (mode in {"assisted", "autopilot"} and not can_affect_action and not can_affect_position and "Risk" not in affects):
            return _automation_result("auto_archive", 0.76, can_affect_score, can_affect_action, can_affect_position, "作为解释项低权重保留", "低置信度规则推导已降权，不需要逐条人工处理。", "不提升评级或仓位")
        return _automation_result("needs_human_review", 0.66, can_affect_score, can_affect_action, can_affect_position, "人工检查推导依据", "该推导可能影响核心评分。", "需人工判断")
    if item_type == "qualitative_risk":
        if _low_impact(row) and mode in {"assisted", "autopilot"}:
            return _automation_result("auto_archive", 0.72, can_affect_score, can_affect_action, can_affect_position, "作为观察备注归档", "定性风险为低影响备注，不自动进入核心评分。", "定性风险永不自动批准")
        return _automation_result("needs_human_review", 0.7, can_affect_score, can_affect_action, can_affect_position, "保留人工判断", "定性风险不能由 AI 自动确认。", "禁止自动批准高风险定性项")
    if item_type == "analyst_estimate_needed":
        if mode in {"assisted", "autopilot"} and affects <= {"Entry", "ConfidenceOnly"}:
            return _automation_result("auto_archive", 0.8, can_affect_score, can_affect_action, can_affect_position, "标记为买点置信度项，暂不人工逐条处理", "分析师预期缺口不影响公司质量评分。", "不改变 Action")
        return _automation_result("needs_estimates", 0.7, can_affect_score, can_affect_action, can_affect_position, "接入分析师预期数据源", "需要 estimates source。", "不使用模型猜测预期")
    if item_type == "manual_override_needed":
        if _low_impact(row) or (mode == "autopilot" and not can_affect_action and not can_affect_position):
            return _automation_result("auto_archive", 0.74, can_affect_score, can_affect_action, can_affect_position, "低优先级，可暂时忽略", "该人工补充项只影响解释完整度或低权重置信度。", "不进入核心评分")
        return _automation_result("needs_human_review", 0.68, can_affect_score, can_affect_action, can_affect_position, "人工决定是否补充", "该项可能影响核心判断。", "需人工复核")
    return _automation_result("low_priority_ignore", 0.6, can_affect_score, can_affect_action, can_affect_position, "暂不处理", "非核心复核项。", "无")


def apply_automation_result(row: dict, automation: dict, queue_store: ReviewQueueStore, ai_store: AIReviewStore | None = None) -> str:
    item_id = int(row["id"])
    decision = str(automation.get("automationDecision") or "")
    if decision in {"auto_archive", "low_priority_ignore", "not_material", "proxy_available"}:
        old, new = queue_store.auto_archive_item(item_id, str(automation.get("explanationZh") or automation.get("recommendedNextStep") or "AI自动归档"))
        if ai_store:
            ai_store.log_audit(
                item_id,
                "ai_auto_archived",
                old.get("reviewStatus") if old else None,
                new.get("reviewStatus") if new else None,
                old.get("value") if old else None,
                new.get("value") if new else None,
                "ai_automation",
                None,
                automation.get("explanationZh"),
            )
        return "ai_auto_archived"
    triage = _triage_for_decision(decision)
    queue_store.set_ai_triage(
        item_id,
        triage,
        explanation_zh=str(automation.get("explanationZh") or ""),
        evidence_quote=str(automation.get("evidenceOrReason") or ""),
    )
    return triage


def automation_effectiveness(rows: list[dict]) -> dict:
    total = len(rows)
    auto_done = sum(1 for row in rows if str(row.get("aiTriageStatus") or "") in {"auto_approved_by_ai", "ai_auto_archived"})
    human = sum(
        1
        for row in rows
        if str(row.get("reviewStatus") or "") not in {"approved", "rejected", "manually_corrected", "auto_archived"}
        and str(row.get("aiTriageStatus") or "") not in {"auto_approved_by_ai", "ai_auto_archived"}
    )
    return {
        "total": total,
        "autoHandled": auto_done,
        "humanRemaining": human,
        "automationRate": 0 if total == 0 else auto_done / total,
    }


def normalize_automation_mode(mode: str | None) -> str:
    normalized = str(mode or "autopilot_with_guardrails").strip().lower()
    if normalized == "autopilot_with_guardrails":
        return "autopilot"
    return normalized if normalized in AUTOMATION_MODES else "autopilot"


def _automation_result(decision: str, confidence: float, can_score: bool, can_action: bool, can_position: bool, next_step: str, explanation: str, guardrail: str) -> dict:
    if decision not in AUTOMATION_DECISIONS:
        decision = "needs_human_review"
    return {
        "automationDecision": decision,
        "automationConfidence": max(0.0, min(1.0, float(confidence))),
        "canAffectScore": bool(can_score),
        "canAffectAction": bool(can_action),
        "canAffectPositionSize": bool(can_position),
        "recommendedNextStep": next_step,
        "explanationZh": explanation,
        "evidenceOrReason": explanation,
        "riskGuardrail": guardrail,
    }


def _is_automation_candidate(row: dict) -> bool:
    return str(row.get("reviewStatus") or "") not in {"approved", "rejected", "manually_corrected", "auto_archived"} and str(row.get("resolutionStatus") or "") not in {"calculated", "not_applicable"}


def _triage_for_decision(decision: str) -> str:
    return {
        "ai_recommend_approve": "ai_recommend_approve",
        "ai_recommend_correct": "ai_recommend_correct",
        "ai_recommend_reject": "ai_recommend_reject",
        "needs_more_source": "ai_not_enough_evidence",
        "needs_estimates": "ai_needs_human_review",
        "needs_human_review": "ai_needs_human_review",
    }.get(decision, "ai_needs_human_review")


def _low_impact(row: dict) -> bool:
    affects = _affects(row.get("affects"))
    return not affects or affects <= {"ConfidenceOnly", "ExplanationOnly", "Entry"}


def _explanation_only(row: dict) -> bool:
    affects = _affects(row.get("affects"))
    return not affects or affects <= {"ConfidenceOnly", "ExplanationOnly"}


def _affects(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item}
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _source_recommendation(row: dict) -> str:
    status = str(row.get("resolutionStatus") or "")
    if status == "requires_analyst_estimates":
        return "接入分析师预期数据源"
    if status == "requires_sec_filing":
        return "抓取 SEC 10-K / 10-Q"
    return "抓取 IR / 8-K 或使用已标注代理指标"


def _now_for_log() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
