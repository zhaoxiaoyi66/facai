from __future__ import annotations

import re
from html import escape

import streamlit as st

from ai.qwen_review_service import (
    QWEN_NOT_SUITABLE_REASON,
    QwenReviewService,
    qwen_review_candidates,
    qwen_review_efficiency_stats,
    qwen_review_eligibility,
)
from ai.review_automation import ReviewAutomationService, automation_effectiveness
from data.ai_review_assistant import AIReviewStore
from data.evidence_backfill import backfill_evidence_for_review_item
from data.review_center_auto_archive import auto_archive_low_priority_review_items
from data.review_center_view_model import build_review_center_view_model
from data.review_queue_builder import ReviewQueueBuilder, ReviewQueueStore
from formatting import format_compact_number, format_large_number
from review_autopilot import ReviewAutopilot, auto_fill_capability
from scoring.sector_models import classifyStockModel
from settings import load_watchlist
from ui.metric_labels import action_label, confidence_label, metric_label, model_type_label, source_type_label


STATUS_LABELS = {
    "pending_review": "待确认",
    "needs_data": "需要补齐",
    "approved": "已确认",
    "rejected": "已驳回",
    "manually_corrected": "手动修正",
    "stale": "已过期",
    "auto_archived": "AI自动归档",
    "duplicate_archived": "重复项归档",
}
STATUS_TONES = {
    "pending_review": "yellow",
    "needs_data": "orange",
    "approved": "green",
    "rejected": "red",
    "manually_corrected": "blue",
    "stale": "gray",
    "auto_archived": "gray",
    "duplicate_archived": "gray",
}
ITEM_TYPE_LABELS = {
    "extracted_value": "自动抽取值",
    "missing_kpi": "缺失关键KPI",
    "derived_low_confidence": "低置信度规则推导",
    "qualitative_risk": "定性风险复核",
    "analyst_estimate_needed": "需要分析师预期",
    "manual_override_needed": "建议人工补充",
}
ITEM_TYPE_TONES = {
    "extracted_value": "blue",
    "missing_kpi": "yellow",
    "derived_low_confidence": "gray",
    "qualitative_risk": "orange",
    "analyst_estimate_needed": "gray",
    "manual_override_needed": "yellow",
}
AI_DECISION_LABELS = {
    "recommend_approve": "AI建议确认",
    "recommend_reject": "AI建议驳回",
    "recommend_correct": "AI建议修正",
    "needs_human_review": "AI要求人工复核",
    "needs_more_source": "需要更多来源",
    "not_enough_evidence": "证据不足",
}
AI_DECISION_TONES = {
    "recommend_approve": "green",
    "recommend_reject": "red",
    "recommend_correct": "orange",
    "needs_human_review": "yellow",
    "needs_more_source": "gray",
    "not_enough_evidence": "gray",
}
AI_TRIAGE_LABELS = {
    "auto_approved_by_ai": "AI自动确认",
    "ai_recommend_approve": "AI建议确认",
    "ai_recommend_correct": "AI建议修正",
    "ai_recommend_reject": "AI建议驳回",
    "ai_needs_human_review": "AI要求人工判断",
    "ai_not_enough_evidence": "证据不足",
    "ai_invalid_output": "AI输出无效",
    "ai_skipped": "已跳过",
    "ai_auto_archived": "AI自动归档",
    "extraction_rejected_by_rule": "规则未通过图谱",
}
AI_TRIAGE_TONES = {
    "auto_approved_by_ai": "green",
    "ai_recommend_approve": "blue",
    "ai_recommend_correct": "orange",
    "ai_recommend_reject": "red",
    "ai_needs_human_review": "yellow",
    "ai_not_enough_evidence": "gray",
    "ai_invalid_output": "red",
    "ai_skipped": "gray",
    "ai_auto_archived": "gray",
    "extraction_rejected_by_rule": "gray",
}
AI_MATCH_LABELS = {
    "exact_match": "完全匹配",
    "partial_match": "部分匹配",
    "mismatch": "不匹配",
    "no_evidence": "无证据",
    "exact": "明确",
    "ambiguous": "不明确",
}

STATUS_LABELS.update({
    "needs_evidence": "需要补证据",
    "invalid_review_item": "无效复核项",
})
STATUS_TONES.update({
    "needs_evidence": "orange",
    "invalid_review_item": "gray",
})
ITEM_TYPE_LABELS.update({
    "evidence_missing_extracted_value": "缺证据抽取值",
})
ITEM_TYPE_TONES.update({
    "evidence_missing_extracted_value": "orange",
})
AI_TRIAGE_LABELS.update({
    "needs_more_source": "需要补证据",
    "ready_for_qwen": "待Qwen复核",
})
AI_TRIAGE_TONES.update({
    "needs_more_source": "orange",
    "ready_for_qwen": "blue",
})

RECENT_CONFIRMED_TAB = "最近确认"
CONFIRM_NOTICE_KEY = "review-last-confirmed-item"
IMPACT_DETAIL_KEY = "review-impact-detail-item"


def render() -> None:
    st.markdown(_styles(), unsafe_allow_html=True)
    store = ReviewQueueStore()
    ai_store = AIReviewStore(store.path)
    st.markdown(
        """
        <div class="review-toolbar">
          <div>
            <div class="review-kicker">系统后验验证</div>
            <h1>数据复核</h1>
            <p>验证系统信号、历史表现和数据质量。</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    initial_review_view = build_review_center_view_model(rows=store.list_items(), store=store)
    filters = _render_filters(store, initial_review_view.get("summary", {}).get("groupCounts", {}))
    base_rows = _filtered_rows(store, filters)
    review_view = build_review_center_view_model(rows=base_rows, store=store)
    _render_summary(review_view.get("summary", {}), ai_store.summary())
    _render_sync_controls(store, base_rows, filters)
    _render_last_confirm_notice(store)
    rows = _apply_ai_filters(base_rows, ai_store)
    view_rows = _review_view_rows_for_active_tab(review_view, rows)
    _render_ai_controls(store, ai_store, filters, base_rows)
    _render_rows(store, rows, ai_store, view_rows)






















def _render_recent_confirmed_rows(store: ReviewQueueStore, rows: list[dict]) -> None:
    st.markdown('<div class="review-list-title">最近确认</div>', unsafe_allow_html=True)
    st.caption("最近 7 天确认、AI自动确认和人工修正的数据。撤销后该股票评分会被标记为过期，需要重新计算。")
    if not rows:
        st.info("最近 7 天暂无已确认数据。")
        return
    for row in rows:
        _render_recent_confirmed_row(store, row)


def _render_recent_confirmed_row(store: ReviewQueueStore, row: dict) -> None:
    metric_id = int(row["id"])
    symbol = str(row.get("symbol") or "").upper()
    status = str(row.get("reviewStatus") or "")
    triage = str(row.get("aiTriageStatus") or "")
    effective_status = "auto_approved_by_ai" if triage == "auto_approved_by_ai" or status == "auto_approved_by_ai" else status
    score_status = store.get_score_status(symbol) if symbol else {}
    with st.container(border=True):
        cols = st.columns([0.95, 0.55, 1.35, 0.72, 0.44, 0.75, 0.82, 0.78, 0.82, 1.45], vertical_alignment="center")
        cols[0].caption(_short_time(row.get("confirmedAt") or row.get("approvedAt") or row.get("reviewedAt") or row.get("updatedAt")))
        cols[1].markdown(f"**{escape(symbol or '待补')}**")
        cols[2].markdown(
            f"<div class='metric-title'>{escape(metric_label(row.get('displayName') or row.get('metricKey')))}</div>"
            f"<div class='metric-sub'>{escape(str(row.get('metricKey') or ''))}</div>",
            unsafe_allow_html=True,
        )
        cols[3].markdown(f"<span class='metric-value'>{escape(_format_value(row.get('value'), row.get('unit')))}</span>", unsafe_allow_html=True)
        cols[4].caption(_review_display_text(row.get("unit")))
        cols[5].markdown(_badge(source_type_label(row.get("sourceType")), "gray"), unsafe_allow_html=True)
        cols[6].markdown(_badge(_confirmed_status_label(effective_status), _confirmed_status_tone(effective_status)), unsafe_allow_html=True)
        cols[7].markdown(_badge("参与评分" if row.get("canEnterScoring") else "不进评分", "green" if row.get("canEnterScoring") else "gray"), unsafe_allow_html=True)
        cols[8].caption(_confirmed_actor_label(row))
        with cols[9]:
            action_cols = st.columns([0.55, 0.45])
            if action_cols[0].button(_undo_confirm_label(effective_status), key=f"recent-undo-{metric_id}", width="stretch"):
                store.undo_review_status(metric_id, "pending_review", f"ui_recent_undo_{effective_status}")
                st.session_state.pop(CONFIRM_NOTICE_KEY, None)
                st.toast("已撤销确认，该股票评分已标记为过期。")
                st.rerun()
            if action_cols[1].button("影响", key=f"recent-impact-{metric_id}", width="stretch"):
                st.session_state[IMPACT_DETAIL_KEY] = metric_id if st.session_state.get(IMPACT_DETAIL_KEY) != metric_id else None
        if st.session_state.get(IMPACT_DETAIL_KEY) == metric_id:
            _render_score_impact_panel(store, row, score_status, key_prefix=f"recent-{metric_id}")
        if row.get("sourceUrl"):
            st.link_button("打开来源", str(row["sourceUrl"]), width="stretch")


def _render_score_impact_panel(store: ReviewQueueStore, row: dict, score_status: dict, key_prefix: str) -> None:
    affects = _affects_label(row.get("affects") or "ConfidenceOnly")
    run_id = score_status.get("lastScoreRunId") or "未记录"
    st.markdown(
        f"""
        <div class="review-impact-panel">
          <strong>评分影响</strong>
          <span>是否参与评分：{'是' if row.get('canEnterScoring') else '否'}</span>
          <span>影响范围：{escape(affects)}</span>
          <span>最近评分批次：{escape(str(run_id))}</span>
          <span>撤销后会将该股票评分标记为过期，需重新计算。</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    symbol = str(row.get("symbol") or "").upper()
    if symbol and st.button("重新计算该股票", key=f"{key_prefix}-recompute", width="stretch"):
        ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist([symbol])
        store.mark_score_fresh(symbol, f"manual-recompute-{int(row.get('id') or 0)}")
        st.toast(f"{symbol} 已重新计算并标记为最新。")
        st.rerun()


def _render_last_confirm_notice(store: ReviewQueueStore) -> None:
    notice = st.session_state.get(CONFIRM_NOTICE_KEY)
    if not notice:
        return
    row = notice.get("row") if isinstance(notice, dict) else {}
    if not isinstance(row, dict):
        return
    metric_id = int(row.get("id") or notice.get("id") or 0)
    if not metric_id:
        return
    symbol = str(row.get("symbol") or notice.get("symbol") or "").upper()
    metric_name = metric_label(row.get("displayName") or row.get("metricKey") or notice.get("metricName"))
    st.markdown(
        f"""
        <div class="review-confirm-notice">
          <strong>刚刚确认：{escape(symbol or '待补')} {escape(metric_name)}</strong>
          <span>该数据将参与评分，可在“最近确认”中撤销。</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns([1.0, 1.0, 4.0], vertical_alignment="center")
    if cols[0].button("撤销确认", key=f"review-last-undo-{metric_id}", width="stretch"):
        store.undo_review_status(metric_id, "pending_review", "ui_last_confirm_undo")
        st.session_state.pop(CONFIRM_NOTICE_KEY, None)
        st.toast("已撤销确认，该股票评分已标记为过期。")
        st.rerun()
    if cols[1].button("查看影响", key=f"review-last-impact-{metric_id}", width="stretch"):
        st.session_state[IMPACT_DETAIL_KEY] = metric_id if st.session_state.get(IMPACT_DETAIL_KEY) != metric_id else None
    if st.session_state.get(IMPACT_DETAIL_KEY) == metric_id:
        score_status = store.get_score_status(symbol) if symbol else {}
        _render_score_impact_panel(store, row, score_status, key_prefix=f"last-confirm-{metric_id}")


def _short_time(value: object) -> str:
    text = str(value or "待补")
    return text.replace("T", " ")[:16]


def _confirmed_status_label(status: str) -> str:
    if status == "auto_approved_by_ai":
        return "AI已确认"
    if status == "manually_corrected":
        return "已修正"
    return STATUS_LABELS.get(status, status or "未归类")


def _confirmed_status_tone(status: str) -> str:
    if status == "auto_approved_by_ai":
        return "green"
    return STATUS_TONES.get(status, "gray")


def _undo_confirm_label(status: str) -> str:
    if status == "auto_approved_by_ai":
        return "撤销AI确认"
    if status == "manually_corrected":
        return "撤销修正"
    return "撤销确认"


def _confirmed_actor_label(row: dict) -> str:
    if str(row.get("aiTriageStatus") or "") == "auto_approved_by_ai" or str(row.get("reviewStatus") or "") == "auto_approved_by_ai":
        return "Qwen / AI"
    if str(row.get("reviewStatus") or "") == "manually_corrected":
        return "人工修正"
    return str(row.get("approvedBy") or row.get("reviewedBy") or "用户")




def _render_correction_popover(store: ReviewQueueStore, row: dict, column) -> None:
    metric_id = int(row["id"])
    with column:
        with st.popover("修正", use_container_width=True):
            value = st.number_input("修正数值", value=float(row.get("value") or 0), key=f"review-correct-value-{metric_id}")
            unit = st.text_input("单位", value=str(row.get("unit") or ""), key=f"review-correct-unit-{metric_id}")
            period = st.text_input("期间", value=str(row.get("period") or ""), key=f"review-correct-period-{metric_id}")
            notes = st.text_area("修正说明", value=str(row.get("correctionNotes") or ""), key=f"review-correct-notes-{metric_id}", height=88)
            if st.button("保存修正", key=f"review-correct-save-{metric_id}", width="stretch"):
                store.correct_item(metric_id, value, unit or None, period or None, notes or None)
                st.toast("已保存手动修正")
                st.rerun()


def _model_type_for_symbol(symbol: str) -> str:
    if not symbol:
        return "GENERIC"
    return classifyStockModel({"symbol": symbol})


def _format_value(value: object, unit: object) -> str:
    if value in (None, ""):
        return "暂无"
    raw_text = str(value).strip()
    try:
        parse_text = raw_text.replace(",", "").lower().replace("percentage", "").replace("percent", "").replace("%", "").strip()
        number = float(parse_text)
    except (TypeError, ValueError):
        return str(value)

    normalized_unit = str(unit or "").strip().lower()
    if normalized_unit in {"percent", "percentage", "pct"}:
        explicit_percent = "%" in raw_text or "percent" in raw_text.lower()
        return f"{number:.1f}%" if explicit_percent or abs(number) > 1 else f"{number * 100:.1f}%"
    if normalized_unit in {"multiple", "x"}:
        return f"{number:.2f}x"
    if normalized_unit in {"money", "usd", "dollar", "currency"}:
        return format_large_number(number)
    if abs(number) >= 1_000_000:
        return format_compact_number(number)
    if number.is_integer():
        return f"{number:,.0f}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _review_display_text(value: object, fallback: str = "待补") -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "n/a", "na", "none", "null", "nan"}:
        return fallback
    return text


def _review_has_display_text(value: object) -> bool:
    return _review_display_text(value, "") != ""


def _metric_row_subtitle(row: dict) -> str:
    parts = [model_type_label(row.get("modelType"))]
    variant = row.get("metricVariant")
    if variant:
        parts.append(metric_label(variant))
    basis = _target_basis_label(row.get("targetBasis"))
    if basis:
        parts.append(basis)
    period = row.get("metricPeriod") or row.get("fiscalPeriod") or row.get("period")
    if period:
        parts.append(str(period))
    freshness = _freshness_label(row.get("freshnessStatus"))
    if freshness:
        parts.append(freshness)
    return " · ".join(part for part in parts if part)


def _target_basis_label(value: object) -> str:
    mapping = {
        "reported_yoy": "reported YoY",
        "constant_currency_yoy": "constant currency",
        "margin": "利润率口径",
        "amount": "金额口径",
        "ratio": "比例口径",
        "customer_count_growth": "客户数增速",
    }
    return mapping.get(str(value or ""), "")


def _freshness_label(value: object) -> str:
    mapping = {
        "active_current": "当前值",
        "historical_value": "历史值",
    }
    return mapping.get(str(value or ""), "")


def _affects_label(value: object) -> str:
    if not value:
        return "解释"
    mapping = {"Quality": "质量", "Entry": "买点", "Risk": "风险", "Technical": "技术", "ConfidenceOnly": "置信度", "ExplanationOnly": "解释"}
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    return " / ".join(mapping.get(part, part) for part in parts) if parts else "解释"


def _score_status_label(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "fresh": "最新",
        "stale": "需重算",
        "expired": "需重算",
        "pending": "等待重算",
    }.get(text, "最新" if not text else text)


def _detail_title(row: dict) -> str:
    if row.get("reviewStatus") == "needs_data":
        return "为什么需要补齐"
    if row.get("itemType") == "extracted_value":
        return "原文片段"
    return "复核说明"


def _confidence_tone(confidence: str) -> str:
    if confidence == "high":
        return "green"
    if confidence == "medium":
        return "yellow"
    if confidence == "low":
        return "gray"
    return "gray"


def _badge(label: str, tone: str) -> str:
    return f'<span class="review-badge tone-{escape(tone)}">{escape(label)}</span>'












def _affects_scoring(value: object) -> bool:
    parts = {part.strip() for part in str(value or "").split(",") if part.strip()}
    return bool(parts & {"Quality", "Entry", "Risk"})


def _truncate(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"






def _render_ai_action_controls(store: ReviewQueueStore, row: dict, ai_result: dict) -> None:
    metric_id = int(row["id"])
    triage_status = _ai_triage_status(row, ai_result)
    if triage_status not in {"ai_recommend_correct", "ai_recommend_reject"}:
        return
    cols = st.columns([1, 1, 3])
    ai_result_id = int(ai_result.get("id") or 0) or None
    if triage_status == "ai_recommend_correct":
        if cols[0].button("接受AI修正", key=f"review-accept-ai-correct-{metric_id}", width="stretch"):
            old, new = store.accept_ai_correction(metric_id, ai_result, ai_result_id)
            AIReviewStore(store.path).log_audit(
                metric_id,
                "accept_ai_correction",
                old.get("reviewStatus") if old else None,
                new.get("reviewStatus") if new else None,
                old.get("value") if old else None,
                new.get("value") if new else None,
                "local_user",
                ai_result_id,
                ai_result.get("explanationZh"),
            )
            st.toast("已接受AI修正，作为人工确认后的修正值")
            st.rerun()
    if triage_status == "ai_recommend_reject":
        if cols[1].button("接受驳回", key=f"review-accept-ai-reject-{metric_id}", width="stretch"):
            old, new = store.accept_ai_reject(metric_id, ai_result_id, ai_result.get("explanationZh"))
            AIReviewStore(store.path).log_audit(
                metric_id,
                "accept_ai_reject",
                old.get("reviewStatus") if old else None,
                new.get("reviewStatus") if new else None,
                old.get("value") if old else None,
                new.get("value") if new else None,
                "user_after_ai_recommendation",
                ai_result_id,
                ai_result.get("explanationZh"),
            )
            st.toast("已按AI建议驳回该数据")
            st.rerun()




def _is_ai_reject(result: dict | None) -> bool:
    return bool(result) and _ai_triage_status({}, result) == "ai_recommend_reject"


def _is_ai_correct(result: dict | None) -> bool:
    return bool(result) and _ai_triage_status({}, result) == "ai_recommend_correct"


def _is_ai_human(result: dict | None) -> bool:
    return bool(result) and _ai_triage_status({}, result) in {"ai_needs_human_review", "ai_not_enough_evidence", "ai_invalid_output"}


def _ai_sort_key(row: dict, result: dict | None) -> tuple[int, str, str]:
    triage_status = _ai_triage_status(row, result)
    rank = {
        "ai_recommend_reject": 0,
        "ai_recommend_correct": 1,
        "ai_not_enough_evidence": 2,
        "ai_needs_human_review": 3 if _affects_scoring(row.get("affects")) else 4,
        "ai_recommend_approve": 5,
        "auto_approved_by_ai": 6,
    }.get(triage_status, 7)
    return (rank, str(row.get("symbol") or ""), str(row.get("metricKey") or ""))


def _ai_triage_status(row: dict, result: dict | None) -> str:
    if row and row.get("aiTriageStatus"):
        return str(row.get("aiTriageStatus"))
    if not result:
        return ""
    if result.get("aiTriageStatus"):
        return str(result.get("aiTriageStatus"))
    action = str(result.get("appliedAction") or "")
    return {
        "auto_approved_by_ai": "auto_approved_by_ai",
        "suggested_approve": "ai_recommend_approve",
        "suggested_reject": "ai_recommend_reject",
        "manually_correct_candidate": "ai_recommend_correct",
        "needs_human_review": "ai_needs_human_review",
        "needs_more_source": "ai_needs_human_review",
        "not_enough_evidence": "ai_not_enough_evidence",
    }.get(action, "")






def _cn_item_type(value: object) -> str:
    return {
        "extracted_value": "自动抽取值",
        "missing_kpi": "缺失关键KPI",
        "derived_low_confidence": "低置信度规则推导",
        "qualitative_risk": "定性风险复核",
        "analyst_estimate_needed": "需要分析师预期",
        "manual_override_needed": "建议人工补充",
    }.get(str(value or ""), str(value or "未归类"))


def _cn_ai_triage(value: object) -> str:
    return {
        "auto_approved_by_ai": "AI自动确认",
        "ai_recommend_approve": "AI建议确认",
        "ai_recommend_correct": "AI建议修正",
        "ai_recommend_reject": "AI建议驳回",
        "ai_needs_human_review": "AI要求人工判断",
        "ai_not_enough_evidence": "证据不足",
        "ai_invalid_output": "AI输出无效",
        "ai_skipped": "已跳过",
        "ai_auto_archived": "AI自动归档",
    }.get(str(value or ""), "未预审")


def _cn_match(value: object) -> str:
    return {
        "exact_match": "精确匹配",
        "partial_match": "部分匹配",
        "mismatch": "证据不匹配",
        "no_evidence": "证据不足",
        "exact": "明确",
        "ambiguous": "不明确",
    }.get(str(value or ""), str(value or "不明确"))


def _triage_tone(value: str) -> str:
    return {
        "auto_approved_by_ai": "green",
        "ai_recommend_approve": "blue",
        "ai_recommend_correct": "orange",
        "ai_recommend_reject": "red",
        "ai_needs_human_review": "yellow",
        "ai_not_enough_evidence": "gray",
        "ai_invalid_output": "red",
        "ai_skipped": "gray",
        "ai_auto_archived": "gray",
    }.get(value, "gray")




def _render_summary(summary: dict, ai_summary: dict | None = None) -> None:
    group_counts = summary.get("groupCounts") or {}
    pending_count = int(group_counts.get("highPriorityPending", summary.get("active", 0)) or 0)
    impact_count = int(group_counts.get("scoringImpactNeedsHuman", 0) or 0)
    auto_confirm_count = int(group_counts.get("autoConfirmCandidates", 0) or 0)
    auto_archive_count = int(group_counts.get("autoArchiveCandidates", 0) or 0)
    cards = [
        ("待处理", pending_count, "yellow"),
        ("影响评分", impact_count, "red"),
        ("可自动确认", auto_confirm_count, "green"),
        ("可自动归档", auto_archive_count, "gray"),
        ("风险观察", group_counts.get("riskObservation", 0), "orange"),
        ("AI 建议", group_counts.get("aiSuggestedCorrections", 0), "orange"),
        ("证据不足", group_counts.get("insufficientEvidence", 0), "gray"),
    ]
    html = "".join(
        f'<div class="review-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
        for label, value, tone in cards
    )
    st.markdown(
        f"""
        <section class="review-overview-panel">
          <div class="review-overview-title">
            <strong>复核状态</strong>
            <span>默认优先展示会影响评分、AI 高置信建议和证据不足项；自动归档默认收起。</span>
          </div>
          <div class="review-summary-strip">{html}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_filters(store: ReviewQueueStore, group_counts: dict | None = None) -> dict:
    rows = store.list_items()
    symbols = sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")})
    metric_keys = sorted({str(row.get("metricKey") or "") for row in rows if row.get("metricKey")})
    source_types = sorted({str(row.get("sourceType") or "") for row in rows if row.get("sourceType")})
    tabs = ["待处理", "影响评分", "可自动处理", "AI 建议", "证据不足", "已处理"]
    tab_counts = _review_tab_counts(group_counts or {})
    _apply_default_review_tab(tab_counts)
    st.session_state["review-tab-counts"] = tab_counts
    st.session_state["review-active-tab"] = st.radio(
        "工作台视图",
        tabs,
        index=tabs.index(st.session_state.get("review-active-tab", "待处理"))
        if st.session_state.get("review-active-tab", "待处理") in tabs
        else 0,
        format_func=lambda tab: f"{tab} {tab_counts.get(tab, 0)}",
        horizontal=True,
        key="review-active-tab-radio",
    )
    with st.expander("精确筛选", expanded=False):
        cols = st.columns([1.0, 1.4, 1.1, 1.0, 1.0])
        symbol = cols[0].selectbox("股票", ["全部", *symbols], key="review-filter-symbol")
        metric_key = cols[1].selectbox(
            "指标",
            ["全部", *metric_keys],
            format_func=lambda value: "全部" if value == "全部" else metric_label(value),
            key="review-filter-metric",
        )
        source_type = cols[2].selectbox(
            "来源",
            ["全部", *source_types],
            format_func=lambda value: "全部" if value == "全部" else source_type_label(value),
            key="review-filter-source",
        )
        confidence = cols[3].selectbox(
            "置信度",
            ["全部", "high", "medium", "low"],
            format_func=lambda value: "全部" if value == "全部" else confidence_label(value),
            key="review-filter-confidence",
        )
        affects_scoring = cols[4].checkbox("只看影响评分", value=False, key="review-filter-affects-scoring")
    return {
        "symbol": None if symbol == "全部" else symbol,
        "metric_key": None if metric_key == "全部" else metric_key,
        "item_type": None,
        "source_type": None if source_type == "全部" else source_type,
        "confidence": None if confidence == "全部" else confidence,
        "review_status": None,
        "model_type": None,
        "affects_scoring": affects_scoring,
    }


def _filtered_rows(store: ReviewQueueStore, filters: dict) -> list[dict]:
    effective_filters = _effective_review_filters(filters)
    if _active_review_tab() == RECENT_CONFIRMED_TAB:
        try:
            rows = store.list_recent_confirmed_items(days=7)
        except Exception as exc:
            st.error(f"最近确认列表暂时不可用：{exc}")
            return []
        return _client_filter_review_rows(rows, effective_filters)

    rows = store.list_items(
        symbol=effective_filters["symbol"],
        metric_key=effective_filters["metric_key"],
        item_type=effective_filters["item_type"],
        source_type=effective_filters["source_type"],
        confidence=effective_filters["confidence"],
        review_status=effective_filters["review_status"],
        model_type=effective_filters["model_type"],
        affects_scoring=effective_filters["affects_scoring"],
    )
    return _client_filter_review_rows(rows, effective_filters)


def _effective_review_filters(filters: dict) -> dict:
    effective = dict(filters)
    widget_map = {
        "symbol": "review-filter-symbol",
        "metric_key": "review-filter-metric",
        "source_type": "review-filter-source",
        "confidence": "review-filter-confidence",
    }
    for filter_key, widget_key in widget_map.items():
        widget_value = st.session_state.get(widget_key)
        if widget_value and widget_value != "全部":
            effective[filter_key] = str(widget_value)
        elif widget_value == "全部":
            effective[filter_key] = None
    effective["affects_scoring"] = bool(st.session_state.get("review-filter-affects-scoring", effective.get("affects_scoring")))
    return effective


def _review_tab_counts(group_counts: dict) -> dict[str, int]:
    return {
        "待处理": int(group_counts.get("highPriorityPending", 0) or 0),
        "影响评分": int(group_counts.get("scoringImpactNeedsHuman", 0) or 0),
        "可自动处理": int(group_counts.get("autoConfirmCandidates", 0) or 0)
        + int(group_counts.get("autoArchiveCandidates", 0) or 0),
        "AI 建议": int(group_counts.get("aiSuggestedCorrections", 0) or 0),
        "证据不足": int(group_counts.get("insufficientEvidence", 0) or 0),
        "已处理": int(group_counts.get("recentlyHandled", 0) or 0),
    }


def _apply_default_review_tab(tab_counts: dict[str, int]) -> None:
    if st.session_state.get("review-default-tab-applied"):
        return
    current_tab = str(st.session_state.get("review-active-tab-radio") or st.session_state.get("review-active-tab") or "待处理")
    if current_tab != "待处理" or tab_counts.get("待处理", 0) > 0:
        st.session_state["review-default-tab-applied"] = True
        return
    for tab in ("影响评分", "证据不足", "AI 建议", "可自动处理", "已处理"):
        if tab_counts.get(tab, 0) > 0:
            st.session_state["review-active-tab"] = tab
            break
    st.session_state["review-default-tab-applied"] = True


def _client_filter_review_rows(rows: list[dict], filters: dict) -> list[dict]:
    filtered: list[dict] = []
    for row in rows:
        if filters.get("symbol") and str(row.get("symbol") or "").upper() != str(filters["symbol"]).upper():
            continue
        if filters.get("metric_key") and str(row.get("metricKey") or "") != str(filters["metric_key"]):
            continue
        if filters.get("item_type") and str(row.get("itemType") or "") != str(filters["item_type"]):
            continue
        if filters.get("source_type") and str(row.get("sourceType") or "") != str(filters["source_type"]):
            continue
        if filters.get("confidence") and str(row.get("confidence") or "") != str(filters["confidence"]):
            continue
        if filters.get("review_status") and str(row.get("reviewStatus") or "") != str(filters["review_status"]):
            continue
        if filters.get("model_type") and str(row.get("modelType") or "") != str(filters["model_type"]):
            continue
        if filters.get("affects_scoring"):
            affects = str(row.get("affects") or "")
            if not any(part in affects for part in ("Quality", "Entry", "Risk")):
                continue
        filtered.append(row)
    return filtered


def _active_review_tab() -> str:
    return str(st.session_state.get("review-active-tab-radio") or st.session_state.get("review-active-tab") or "待处理")


def _render_ai_controls(store: ReviewQueueStore, ai_store: AIReviewStore, filters: dict, rows: list[dict]) -> None:
    qwen_rows = [row for row in rows if qwen_review_eligibility(row)[0]]
    stats = qwen_review_efficiency_stats(qwen_rows)
    st.markdown(
        f"""
        <div class="qwen-efficiency">
          <strong>Qwen证据复核</strong>
          <span>候选 {stats['eligibleForQwenCount']} · 已预审 {stats['qwenReviewedCount']} · 自动确认 {stats['autoApprovedCount']} · 仍需人工 {stats['humanRequiredCount']} · 证据不足 {stats['notEnoughEvidenceCount']}</span>
          <small>仅统计适合 Qwen 证据复核的候选项；其他基础复核项不计入本行。</small>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _apply_ai_filters(rows: list[dict], ai_store: AIReviewStore) -> list[dict]:
    latest = ai_store.latest_for_items([int(row["id"]) for row in rows])
    tab = str(st.session_state.get("review-active-tab-radio") or _active_review_tab())
    st.session_state["review-active-tab"] = tab
    filtered: list[dict] = []
    for row in rows:
        triage = _ai_triage_status(row, latest.get(int(row["id"])))
        status = str(row.get("reviewStatus") or "")
        item_type = str(row.get("itemType") or "")
        if tab == "待处理":
            if status in {"approved", "rejected", "manually_corrected", "auto_archived"}:
                continue
            if triage in {"auto_approved_by_ai", "ai_auto_archived"} or row.get("hiddenByDefault"):
                continue
            if item_type in {"calculated", "not_applicable"}:
                continue
        elif tab == "影响评分" and not _is_high_impact_review_item(row):
            continue
        elif tab == "可自动处理" and triage not in {"auto_approved_by_ai", "ai_auto_archived", "ai_recommend_approve"} and not _truthy(row.get("canAutoFill")):
            continue
        elif tab == "AI 建议" and triage not in {"ai_recommend_correct", "ai_recommend_reject", "ai_recommend_approve", "ai_needs_human_review"}:
            continue
        elif tab == "证据不足" and triage != "ai_not_enough_evidence" and status != "needs_evidence":
            continue
        elif tab == "已处理" and status not in {"approved", "rejected", "manually_corrected", "auto_archived", "auto_approved_by_ai"} and triage not in {"auto_approved_by_ai", "ai_auto_archived"}:
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: _review_workbench_sort_key(row, latest.get(int(row["id"]))))


def _has_complete_extracted_value_evidence(row: dict) -> bool:
    if str(row.get("itemType") or "") != "extracted_value":
        return False
    value = row.get("normalizedValue")
    if value is None or str(value).strip() == "":
        value = row.get("value")
    evidence = str(row.get("evidenceText") or row.get("extractedText") or "").strip()
    period = str(row.get("metricPeriod") or row.get("fiscalPeriod") or row.get("period") or "").strip()
    required_values = [
        value,
        row.get("unit"),
        row.get("sourceType"),
        row.get("sourceUrl"),
        row.get("sourceDocumentTitle"),
        evidence,
        row.get("evidenceHash"),
        period,
    ]
    return all(item is not None and str(item).strip() != "" for item in required_values)


def _review_workbench_sort_key(row: dict, result: dict | None) -> tuple[int, str, str]:
    triage = _ai_triage_status(row, result)
    if _is_high_impact_review_item(row) and str(row.get("reviewStatus") or "") not in {"approved", "rejected", "auto_archived"}:
        rank = 0
    elif triage in {"ai_recommend_correct", "ai_recommend_reject", "ai_recommend_approve"}:
        rank = 1
    elif triage in {"ai_not_enough_evidence", "ai_needs_human_review"} or str(row.get("reviewStatus") or "") == "needs_evidence":
        rank = 2
    elif triage in {"auto_approved_by_ai", "ai_auto_archived"} or row.get("hiddenByDefault"):
        rank = 8
    else:
        rank = 4
    return (rank, str(row.get("symbol") or ""), str(row.get("metricKey") or ""))


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


HIGH_IMPACT_REVIEW_METRICS = {
    "rpoGrowth",
    "cRpoGrowth",
    "subscriptionRevenueGrowth",
    "nonGaapOperatingMargin",
    "fcfMargin",
    "directFcfMargin",
    "netRetentionRate",
    "adjustedEbitda",
    "adjustedFcfBeforeGrowth",
    "hedgeCoverage",
    "debtMaturityPressure",
    "regulatoryRisk",
    "patentCliffRisk",
    "pipelineRisk",
}


def _requires_high_impact_confirmation(row: dict) -> bool:
    metric_key = str(row.get("metricKey") or "")
    display_text = " ".join(
        str(row.get(key) or "")
        for key in ("displayName", "metricKey", "systemReason", "explanation")
    ).lower()
    compact_text = "".join(ch for ch in display_text if ch.isalnum())
    high_impact_terms = {
        "rpo",
        "crpo",
        "subscriptionrevenuegrowth",
        "nongaapoperatingmargin",
        "fcfmargin",
        "netretentionrate",
        "adjustedebitda",
        "adjustedfcf",
        "hedgecoverage",
        "debtmaturitypressure",
        "regulatoryrisk",
        "pipelinerisk",
        "patentrisk",
        "patentcliffrisk",
    }
    affects = {part.strip() for part in str(row.get("affects") or "").split(",") if part.strip()}
    return (
        metric_key in HIGH_IMPACT_REVIEW_METRICS
        or bool(affects & {"Action", "Position", "maxPosition"})
        or any(term in compact_text for term in high_impact_terms)
        or "non-gaap" in display_text
    )


def _review_primary_action(row: dict) -> dict:
    status = str(row.get("reviewStatus") or "")
    triage = str(row.get("aiTriageStatus") or "")
    item_type = str(row.get("itemType") or "")

    if status == "auto_archived" or triage == "ai_auto_archived":
        return {"key": "noop_archived", "label": "已归档"}
    if status == "manually_corrected":
        return {"key": "noop_manually_corrected", "label": "已修正"}
    if status == "rejected":
        return {"key": "noop_rejected", "label": "已驳回"}
    if status == "approved" or status == "auto_approved_by_ai" or triage == "auto_approved_by_ai":
        is_ai_confirmed = status == "auto_approved_by_ai" or triage == "auto_approved_by_ai"
        return {"key": "noop_auto_approved" if is_ai_confirmed else "noop_approved", "label": "AI已确认" if is_ai_confirmed else "已确认"}
    if str(row.get("freshnessStatus") or "") == "historical_value":
        return {"key": "keep_historical", "label": "保留为历史"}
    if _has_complete_extracted_value_evidence(row):
        return {"key": "approve", "label": "确认数据"}
    if status == "needs_evidence" or item_type in {"evidence_missing_extracted_value", "extracted_value"}:
        return {"key": "backfill_evidence", "label": "重新抓证据"}
    if item_type == "missing_kpi":
        if str(row.get("autoFillStatus") or "") == "success":
            return {"key": "noop_auto_filled", "label": "已补齐"}
        capability = auto_fill_capability(row)
        if _truthy(row.get("canAutoFill")) or capability.canAutoFill:
            return {"key": "auto_fill", "label": "自动补齐"}
        return {"key": "mark_unavailable", "label": "标记无法获取"}
    if item_type in {"derived_low_confidence", "qualitative_risk"}:
        return {"key": "mark_reviewed", "label": "标记已复核"}
    if item_type == "analyst_estimate_needed":
        return {"key": "fetch_estimates", "label": "获取预期"}
    if item_type == "manual_override_needed":
        return {"key": "manual_fill", "label": "手动补充"}
    return {"key": "archive", "label": "归档"}


def _run_review_action(store: ReviewQueueStore, row: dict, action_key: str, ai_result: dict | None = None) -> None:
    metric_id = int(row["id"])
    if action_key.startswith("noop_"):
        return
    if action_key == "approve":
        guard_key = f"review-confirm-guard-{metric_id}"
        if _requires_high_impact_confirmation(row) and not st.session_state.get(guard_key):
            st.session_state[guard_key] = True
            st.warning("该数据会影响评分。确认后将进入评分系统。确认不等于买入建议。是否继续？")
            return
        store.update_review_status(metric_id, "approved")
        st.session_state.pop(guard_key, None)
        confirmed_row = dict(row)
        confirmed_row["reviewStatus"] = "approved"
        confirmed_row["canEnterScoring"] = True
        metric_name = metric_label(row.get("displayName") or row.get("metricKey"))
        st.session_state[CONFIRM_NOTICE_KEY] = {
            "id": metric_id,
            "symbol": str(row.get("symbol") or "").upper(),
            "metricName": metric_name,
            "row": confirmed_row,
        }
        st.toast(f"已确认 {confirmed_row.get('symbol') or '待补'} {metric_name}，该数据将参与评分。")
    elif action_key == "keep_historical":
        store.auto_archive_item(metric_id, "keep_historical_value")
        st.toast("已保留为历史值")
    elif action_key == "set_current_value":
        guard_key = f"review-set-current-guard-{metric_id}"
        if not st.session_state.get(guard_key):
            st.session_state[guard_key] = True
            st.warning("这是旧期间数据。设为当前值后可能进入当前评分，请再次点击确认。")
            return
        store.mark_item_current_value(metric_id)
        st.session_state.pop(guard_key, None)
        st.toast("已设为当前值，请重新复核后再确认")
    elif action_key == "undo_archive":
        store.undo_review_status(metric_id, "pending_review", "undo_auto_archive")
        st.toast("已撤销归档，需重新计算评分")
    elif action_key == "undo_approval":
        store.undo_review_status(metric_id, "pending_review", "undo_approval")
        st.session_state.pop(CONFIRM_NOTICE_KEY, None)
        st.toast("已撤销确认，该股票评分已标记为过期。")
    elif action_key == "undo_manual_correct":
        store.undo_review_status(metric_id, "pending_review", "undo_manual_correct")
        st.session_state.pop(CONFIRM_NOTICE_KEY, None)
        st.toast("已撤销确认，该股票评分已标记为过期。")
    elif action_key == "undo_reject":
        store.undo_review_status(metric_id, "pending_review", "undo_reject")
        st.toast("已撤销驳回，重新进入待复核")
    elif action_key == "backfill_evidence":
        outcome = backfill_evidence_for_review_item(metric_id, store)
        if outcome.get("status") == "backfilled":
            st.toast("已补回原文证据，可进入复核")
        else:
            st.warning(f"暂未补到证据：{outcome.get('reason') or outcome.get('status')}")
    elif action_key in {"auto_fill", "fetch_estimates"}:
        result = ReviewAutopilot(queue_store=store).run_auto_fill_only([row])
        _show_autopilot_result(result)
        st.toast("自动补齐流程已完成")
    elif action_key == "mark_unavailable":
        store.auto_archive_item(metric_id, "标记无法获取：不再反复进入核心待处理，后续使用代理或降低置信度。")
        st.toast("已标记无法获取，并从核心待处理移除")
    elif action_key == "mark_reviewed":
        store.auto_archive_item(metric_id, "已复核：保留为解释/低权重项，不作为核心确认数据进入评分。")
        st.toast("已标记复核完成")
    elif action_key == "manual_fill":
        store.update_review_status(metric_id, "needs_data", "manual_override_requested")
        st.toast("已标记为需要手动补充")
    elif action_key == "reject":
        store.update_review_status(metric_id, "rejected")
        st.toast("已驳回，该数据不会进入评分")
    elif action_key == "archive":
        store.auto_archive_item(metric_id, "用户归档：低优先级或暂不进入核心评分。")
        st.toast("已归档")
    elif action_key == "needs_more_source":
        store.update_review_status(metric_id, "needs_data", "needs_more_source")
        st.toast("已标记为需要更多来源")
    elif action_key == "use_ttm_proxy":
        store.auto_archive_item(metric_id, "已使用 TTM / 当前代理，不再作为人工待处理项。")
        st.toast("已改用代理并归档")
    elif action_key == "lower_weight":
        store.auto_archive_item(metric_id, "已调低权重：仅保留为低权重解释项。")
        st.toast("已调低权重并归档")
    elif action_key == "risk_note":
        store.auto_archive_item(metric_id, "已加入风险备注，不作为确认数据进入评分。")
        st.toast("已加入风险备注")
    elif action_key == "accept_ai_correct" and ai_result:
        old, new = store.accept_ai_correction(metric_id, ai_result, int(ai_result.get("id") or 0) or None)
        AIReviewStore(store.path).log_audit(
            metric_id,
            "accept_ai_correction",
            old.get("reviewStatus") if old else None,
            new.get("reviewStatus") if new else None,
            old.get("value") if old else None,
            new.get("value") if new else None,
            "local_user",
            int(ai_result.get("id") or 0) or None,
            ai_result.get("explanationZh"),
        )
        st.toast("已接受AI修正，作为人工确认后的修正值")
    elif action_key == "accept_ai_reject" and ai_result:
        old, new = store.accept_ai_reject(metric_id, int(ai_result.get("id") or 0) or None, ai_result.get("explanationZh"))
        AIReviewStore(store.path).log_audit(
            metric_id,
            "accept_ai_reject",
            old.get("reviewStatus") if old else None,
            new.get("reviewStatus") if new else None,
            old.get("value") if old else None,
            new.get("value") if new else None,
            "user_after_ai_recommendation",
            int(ai_result.get("id") or 0) or None,
            ai_result.get("explanationZh"),
        )
        st.toast("已按AI建议驳回该数据")




def _compact_ai_badge(ai_result: dict | None, triage: str, eligible: bool, reason: str) -> str:
    if ai_result:
        score = float(ai_result.get("confidenceScore") or 0)
        match_text = _cn_match(ai_result.get("evidenceMatch"))
        label = f"AI：{match_text} · {score:.0%}"
        return _badge(label, _triage_tone(triage))
    if not eligible:
        label = "不适合AI证据复核" if reason not in {"missing_extracted_text", "missing_evidence_text"} else "无原文证据"
        return _badge(label, "gray")
    return _badge("可由Qwen预审", "blue")


def _recommended_review_action(row: dict, eligible: bool, reason: str) -> str:
    auto_fill_status = str(row.get("autoFillStatus") or "")
    if auto_fill_status == "success":
        fill_type = str(row.get("autoFillType") or "自动补齐")
        return f"自动补齐已完成：{fill_type} 已成功，等待复核新生成或更新后的数据项。"
    if auto_fill_status == "failed":
        return f"自动补齐失败：{row.get('autoFillError') or '未返回具体原因'}"
    if str(row.get("reviewStatus") or "") == "needs_evidence":
        return "需要补证据：该值已生成，但缺少可验证原文，需重新抓取来源后再复核。"
    if str(row.get("reviewStatus") or "") == "needs_data":
        capability = auto_fill_capability(row)
        if capability.canAutoFill:
            return f"可自动补齐：{capability.reason}"
        if str(row.get("resolutionStatus") or "") == "company_not_disclosed":
            return "公司未披露：不再无限重试自动补齐。"
        return f"需要人工补充：{capability.reason}"
    if eligible:
        return "等待确认自动抽取值"
    item_type = str(row.get("itemType") or "")
    if reason in {"missing_extracted_text", "missing_evidence_text"}:
        return "缺少原文证据，需补充来源后再交给Qwen。"
    return {
        "missing_kpi": "需要补齐：抓取IR / SEC文件或补充关键KPI。",
        "derived_low_confidence": "规则推导待复核：检查推导依据和影响范围。",
        "qualitative_risk": "定性风险需人工判断，不交给AI自动确认。",
        "analyst_estimate_needed": "需要分析师预期，不属于证据校验任务。",
        "manual_override_needed": "建议人工补充或确认公司是否披露。",
    }.get(item_type, QWEN_NOT_SUITABLE_REASON)


def _is_high_impact_review_item(row: dict) -> bool:
    affects = {part.strip() for part in str(row.get("affects") or "").split(",") if part.strip()}
    return bool(affects & {"Quality", "Entry", "Risk", "Action", "Position", "maxPosition"})


def _ai_result_html(result: dict) -> str:
    triage = _ai_triage_status({}, result)
    decision = str(result.get("aiDecision") or "")
    warnings = "；".join(str(item) for item in (result.get("warnings") or []) if item)
    corrected = ""
    if result.get("correctedValue") is not None:
        corrected = (
            f"<p><strong>建议修正</strong>：{escape(str(result.get('correctedValue')))} "
            f"{escape(str(result.get('correctedUnit') or ''))} {escape(str(result.get('correctedPeriod') or ''))}</p>"
        )
    return (
        "<div class='ai-review-panel'>"
        f"{_badge(_cn_ai_triage(triage), _triage_tone(triage))}"
        f"<strong>AI置信度 {float(result.get('confidenceScore') or 0):.0%}</strong>"
        f"<span>证据：{escape(_cn_match(result.get('evidenceMatch')))}</span>"
        f"<span>期间：{escape(_cn_match(result.get('periodMatch')))}</span>"
        f"<span>单位：{escape(_cn_match(result.get('unitMatch')))}</span>"
        f"{corrected}"
        f"<p><strong>中文解释</strong>：{escape(str(result.get('explanationZh') or ''))}</p>"
        f"<blockquote>{escape(str(result.get('evidenceQuote') or ''))}</blockquote>"
        f"<p><strong>模型</strong>：provider=qwen · model={escape(str(result.get('model') or 'qwen'))}</p>"
        f"<em>{escape(warnings)}</em>"
        "</div>"
    )


def _default_action_rows(rows: list[dict], ai_store: AIReviewStore) -> list[dict]:
    latest = ai_store.latest_for_items([int(row["id"]) for row in rows])
    return _apply_ai_filters(rows, ai_store) if latest else [
        row
        for row in rows
        if str(row.get("reviewStatus") or "") not in {"approved", "rejected", "manually_corrected", "auto_archived"}
        and str(row.get("aiTriageStatus") or "") not in {"auto_approved_by_ai", "ai_auto_archived"}
    ]


def _show_ai_run_result(result) -> None:
    st.session_state["qwen_review_last_result"] = {
        "reviewed": int(getattr(result, "reviewed", 0) or 0),
        "skipped": int(getattr(result, "skipped", 0) or 0),
        "auto_approved": int(getattr(result, "auto_approved", 0) or 0),
        "needs_human": int(getattr(result, "needs_human", 0) or 0),
        "eligible_for_qwen": int(getattr(result, "eligible_for_qwen", 0) or 0),
        "skipped_not_suitable": int(getattr(result, "skipped_not_suitable", 0) or 0),
        "not_configured": bool(getattr(result, "not_configured", False)),
        "errors": list(getattr(result, "errors", None) or []),
    }
    if getattr(result, "not_configured", False):
        st.toast("未配置 Qwen 复核，仍可手动复核。")
    elif int(getattr(result, "eligible_for_qwen", 0) or 0) == 0:
        st.warning("当前筛选结果中没有符合 Qwen 证据复核条件的项目。")
    else:
        st.toast(f"Qwen预审完成 {result.reviewed} 条；自动确认 {result.auto_approved} 条；仍需人工 {result.needs_human} 条")


def _show_autopilot_result(result) -> None:
    st.session_state["review_autopilot_last_result"] = {
        "runId": getattr(result, "runId", ""),
        "scanned": int(getattr(result, "scannedCount", getattr(result, "scanned", 0)) or 0),
        "processable": int(getattr(result, "processableCount", 0) or 0),
        "skipped": int(getattr(result, "skippedCount", 0) or 0),
        "autoFillAttempted": int(getattr(result, "autoFillAttemptedCount", getattr(result, "autoFillAttempted", 0)) or 0),
        "autoFillSucceeded": int(getattr(result, "autoFillSuccessCount", getattr(result, "autoFillSucceeded", 0)) or 0),
        "autoFillFailed": int(getattr(result, "autoFillFailedCount", getattr(result, "autoFillFailed", 0)) or 0),
        "evidenceBackfilled": int(getattr(result, "evidenceBackfilledCount", 0) or 0),
        "unsupported": int(getattr(result, "unsupportedCount", 0) or 0),
        "qwenEligible": int(getattr(result, "qwenEligibleCount", 0) or 0),
        "qwenReviewed": int(getattr(result, "qwenReviewedCount", getattr(result, "qwenReviewed", 0)) or 0),
        "autoApproved": int(getattr(result, "autoApprovedCount", getattr(result, "autoApproved", 0)) or 0),
        "autoArchived": int(getattr(result, "autoArchivedCount", getattr(result, "autoArchived", 0)) or 0),
        "humanRemaining": int(getattr(result, "needsHumanCount", getattr(result, "humanRemaining", 0)) or 0),
        "failed": int(getattr(result, "failedCount", getattr(result, "failed", 0)) or 0),
        "automationRate": float(getattr(result, "automationRate", 0.0) or 0.0),
        "errors": list(getattr(result, "errors", None) or []),
        "unsupportedMessages": list(getattr(result, "unsupported", None) or []),
        "message": str(getattr(result, "message", "") or ""),
    }
    if int(st.session_state["review_autopilot_last_result"]["scanned"] or 0) == 0:
        st.warning("没有可处理项目：当前筛选条件下，0 条符合此操作。")
    elif int(st.session_state["review_autopilot_last_result"]["failed"] or 0):
        st.warning(f"自动处理完成，但有 {int(st.session_state['review_autopilot_last_result']['failed'] or 0)} 条真正异常。")
    else:
        st.toast("一键自动处理完成")


def _render_last_autopilot_result() -> None:
    result = st.session_state.get("review_autopilot_last_result")
    if not isinstance(result, dict):
        return
    message = (
        f"本次自动处理：扫描 {result.get('scanned', 0)} 条，可处理 {result.get('processable', 0)} 条，"
        f"自动补齐成功 {result.get('autoFillSucceeded', 0)} 条，"
        f"证据补回 {result.get('evidenceBackfilled', 0)} 条，"
        f"Qwen复核 {result.get('qwenReviewed', 0)} 条，"
        f"自动确认 {result.get('autoApproved', 0)} 条，"
        f"自动归档 {result.get('autoArchived', 0)} 条，"
        f"仍需人工 {result.get('humanRemaining', 0)} 条，"
        f"暂不支持 {result.get('unsupported', 0)} 条，"
        f"失败 {result.get('failed', 0)} 条。"
    )
    rate = float(result.get("automationRate", 0) or 0)
    subtitle = f"系统已自动处理 {rate:.0%}，剩余 {result.get('humanRemaining', 0)} 条需要人工判断。"
    if result.get("failed"):
        st.warning(message + " " + subtitle)
        with st.expander("查看本次自动处理失败原因", expanded=False):
            for error in (result.get("errors") or [])[:12]:
                st.caption(str(error))
            unsupported = result.get("unsupportedMessages") or []
            if unsupported:
                st.caption("暂不支持 / 无数据源 / 公司未披露：")
                for item in unsupported[:12]:
                    st.caption(str(item))
    elif result.get("scanned"):
        st.success(message + " " + subtitle)
        unsupported = result.get("unsupportedMessages") or []
        if unsupported:
            with st.expander("查看暂不支持 / 无数据源 / 公司未披露", expanded=False):
                for item in unsupported[:12]:
                    st.caption(str(item))
    else:
        st.info("没有可处理项目：当前筛选条件下，0 条符合此操作。")


def _render_automation_logs(store: ReviewQueueStore) -> None:
    logs = store.list_automation_logs()[:30]
    with st.expander("处理日志", expanded=False):
        if not logs:
            st.caption("暂无自动处理日志。")
            return
        for log in logs:
            st.caption(
                f"{log.get('timestamp')} · {log.get('actor')} · {log.get('action')} · "
                f"{log.get('symbol') or ''} {metric_label(log.get('metricKey') or '')} · "
                f"{log.get('oldStatus') or '-'} → {log.get('newStatus') or '-'} · {log.get('reason') or ''}"
            )


def _show_operation_result(action_name: str, eligible: int, processed: int, skipped: int, failed: int, errors: list[str] | None = None) -> None:
    st.session_state["review_operation_last_result"] = {
        "actionName": action_name,
        "eligible": int(eligible or 0),
        "processed": int(processed or 0),
        "skipped": int(skipped or 0),
        "failed": int(failed or 0),
        "errors": list(errors or []),
    }
    if not eligible:
        st.warning("没有可处理项目：当前筛选条件下，0 条符合此操作。")
    elif failed:
        st.warning(f"{action_name} 完成，但失败 {failed} 条。")
    else:
        st.toast(f"{action_name} 完成：处理 {processed} 条，跳过 {skipped} 条。")


def _render_last_qwen_result() -> None:
    automation = st.session_state.get("ai_automation_last_result")
    if isinstance(automation, dict):
        message = (
            f"AI自动分流完成：符合条件 {automation.get('eligible', 0)} 条，处理 {automation.get('processed', 0)} 条，"
            f"Qwen证据复核 {automation.get('qwen_reviewed', 0)} 条，自动确认 {automation.get('auto_approved', 0)} 条，"
            f"自动归档 {automation.get('auto_archived', 0)} 条，仍需人工 {automation.get('needs_human', 0)} 条。"
        )
        errors = automation.get("errors") or []
        if errors:
            st.warning(message + f" 失败 {len(errors)} 条。")
            with st.expander("查看AI自动分流失败原因", expanded=False):
                for error in errors[:10]:
                    st.caption(str(error))
        elif automation.get("eligible", 0):
            st.success(message)
        else:
            st.info("当前筛选结果中没有符合条件的项目。")
    result = st.session_state.get("qwen_review_last_result")
    if not isinstance(result, dict):
        return
    if result.get("not_configured"):
        st.warning("Qwen 未配置：请检查 .env 中的 QWEN_API_KEY。")
        return
    if int(result.get("eligible_for_qwen", 0) or 0) == 0:
        st.info("当前筛选结果中没有符合 Qwen 证据复核条件的项目。缺失项、定性风险和规则推导请使用“AI自动分流当前筛选结果”。")
        return
    errors = result.get("errors") or []
    message = (
        f"Qwen预审完成：处理 {result.get('reviewed', 0)} 条，跳过 {result.get('skipped', 0)} 条，"
        f"适合复核 {result.get('eligible_for_qwen', 0)} 条，不适合AI {result.get('skipped_not_suitable', 0)} 条，"
        f"AI自动确认 {result.get('auto_approved', 0)} 条，仍需人工 {result.get('needs_human', 0)} 条。"
    )
    if errors:
        st.warning(message + f" 失败 {len(errors)} 条。")
        with st.expander("查看失败原因", expanded=False):
            for error in errors[:10]:
                st.caption(str(error))
    else:
        st.success(message)


def _render_last_operation_result(store: ReviewQueueStore) -> None:
    result = st.session_state.get("review_operation_last_result") or store.latest_operation_log()
    if not isinstance(result, dict):
        return
    action = result.get("actionName", "最近操作")
    eligible = int(result.get("eligible", result.get("eligibleItemCount", 0)) or 0)
    processed = int(result.get("processed", result.get("processedCount", 0)) or 0)
    skipped = int(result.get("skipped", result.get("skippedCount", 0)) or 0)
    failed = int(result.get("failed", result.get("failedCount", 0)) or 0)
    errors = list(result.get("errors") or result.get("errorMessages") or [])
    text = f"{action}：可处理 {eligible} 条，已处理 {processed} 条，跳过 {skipped} 条，失败 {failed} 条。"
    if failed or errors:
        st.warning(text)
        with st.expander("查看最近操作失败原因", expanded=False):
            for error in errors[:10]:
                st.caption(str(error))
    else:
        st.info(text)


def _render_metric_row(store: ReviewQueueStore, row: dict, ai_result: dict | None = None) -> None:
    view_item = row.pop("__review_view_item", None)
    metric_id = int(row["id"])
    status = str(row.get("reviewStatus") or "pending_review")
    confidence = str(row.get("confidence") or "")
    eligible, reason = qwen_review_eligibility(row)
    value_text = _format_value(row.get("value"), row.get("unit"))
    suggested_text = _review_suggested_value_text(row, ai_result, view_item)
    source_meta = _review_row_source_meta(row)
    affects_text = _affects_label(row.get("affects") or "ConfidenceOnly")

    with st.container():
        st.markdown('<div class="review-row-marker"></div>', unsafe_allow_html=True)
        cols = st.columns([0.44, 1.5, 1.08, 0.64, 0.7, 0.68, 1.1, 1.68], gap="small", vertical_alignment="center")
        cols[0].markdown(f"<div class='review-symbol'>{escape(str(row.get('symbol') or ''))}</div>", unsafe_allow_html=True)
        metric_name = view_item.get("metric") if isinstance(view_item, dict) else row.get("displayName") or row.get("metricKey")
        cols[1].markdown(
            f"<div class='metric-title'>{escape(metric_label(metric_name))}</div>"
            f"<div class='metric-sub'>{escape(_metric_row_subtitle(row))}</div>",
            unsafe_allow_html=True,
        )
        cols[2].markdown(
            f"<div class='review-value-stack'><strong>{escape(value_text)}</strong><span>{escape(suggested_text)}</span></div>",
            unsafe_allow_html=True,
        )
        cols[3].markdown(_badge(source_type_label(row.get("sourceType")), "gray"), unsafe_allow_html=True)
        cols[4].markdown(_badge(confidence_label(confidence), _confidence_tone(confidence)), unsafe_allow_html=True)
        cols[5].markdown(_badge(affects_text, _affects_tone(row.get("affects"))), unsafe_allow_html=True)
        action_hint = _review_action_hint(row, ai_result, eligible, reason, view_item)
        cols[6].markdown(
            f"<div class='review-action-hint'><span>{escape(source_meta)}</span><strong>{escape(action_hint)}</strong></div>",
            unsafe_allow_html=True,
        )
        with cols[7]:
            action_cols = st.columns([1, 1, 1, 1], gap="small")
            if action_cols[0].button("确认", key=f"review-confirm-{metric_id}", disabled=status in {"approved", "auto_approved_by_ai"}, width="stretch"):
                _run_review_action(store, row, "approve", ai_result)
                st.rerun()
            if action_cols[1].button("驳回", key=f"review-reject-{metric_id}", disabled=status == "rejected", width="stretch"):
                _run_review_action(store, row, "reject", ai_result)
                st.rerun()
            if action_cols[2].button("归档", key=f"review-archive-{metric_id}", disabled=status == "auto_archived", width="stretch"):
                _run_review_action(store, row, "archive", ai_result)
                st.rerun()
            if action_cols[3].button("证据", key=f"review-toggle-evidence-{metric_id}", width="stretch"):
                st.session_state["review-selected-evidence-id"] = metric_id


def _review_row_source_meta(row: dict) -> str:
    source = source_type_label(row.get("sourceType"))
    if str(row.get("sourceType") or "").strip().lower() == "missing":
        return f"{source} · 待补证据"
    period = _review_display_text(row.get("metricPeriod") or row.get("fiscalPeriod") or row.get("period") or row.get("sourceDate") or row.get("updatedAt"))
    return f"{source} · {period}"


def _affects_tone(value: object) -> str:
    raw = str(value or "")
    if "Risk" in raw or "Technical" in raw:
        return "orange"
    if "Quality" in raw or "Entry" in raw:
        return "blue"
    return "gray"


def _review_suggested_value_text(row: dict, ai_result: dict | None, view_item: dict | None = None) -> str:
    if isinstance(view_item, dict) and view_item.get("proposedValue") not in (None, ""):
        return "建议 " + _format_value(view_item.get("proposedValue"), row.get("unit"))
    if ai_result and ai_result.get("correctedValue") is not None:
        return "建议 " + _format_value(ai_result.get("correctedValue"), ai_result.get("correctedUnit") or row.get("unit"))
    display = row.get("displayValue")
    if display and str(display) != str(row.get("value") or ""):
        return "建议 " + _format_value(display, row.get("unit"))
    return "建议 暂无"


def _review_action_hint(row: dict, ai_result: dict | None, eligible: bool, reason: str, view_item: dict | None = None) -> str:
    if isinstance(view_item, dict) and view_item.get("riskObservation"):
        return "风险观察 / 可归档" if view_item.get("canAutoArchive") else "风险观察"
    if isinstance(view_item, dict) and view_item.get("suggestedAction"):
        return _review_vm_action_label(str(view_item.get("suggestedAction")))
    triage = _ai_triage_status(row, ai_result)
    if triage == "ai_recommend_correct":
        return "建议修正后确认"
    if triage == "ai_recommend_reject":
        return "建议驳回"
    if triage == "ai_not_enough_evidence" or str(row.get("reviewStatus") or "") == "needs_evidence":
        return "先补证据"
    if _is_high_impact_review_item(row):
        return "影响评分，需人工确认"
    if triage in {"auto_approved_by_ai", "ai_recommend_approve"}:
        return "可自动确认"
    if triage == "ai_auto_archived":
        return "可自动归档"
    return _recommended_review_action(row, eligible, reason)


def _render_review_evidence_drawer(source_rows: list[dict], ai_results: dict[int, dict]) -> None:
    selected_id = st.session_state.get("review-selected-evidence-id")
    selected_entry = None
    if selected_id is not None:
        for entry in source_rows:
            raw = entry.get("raw") or {}
            if raw.get("id") is not None and int(raw["id"]) == int(selected_id):
                selected_entry = entry
                break
    if selected_entry is None:
        st.markdown(
            """
            <aside class="review-evidence-drawer empty">
              <div class="drawer-topline">证据详情</div>
              <strong>选择一条复核项</strong>
              <p>点击队列右侧的“证据”，这里会显示原文、AI 解释和建议处理方式。</p>
            </aside>
            """,
            unsafe_allow_html=True,
        )
        return

    row = dict(selected_entry.get("raw") or {})
    view_item = selected_entry.get("item")
    metric_id = int(row["id"])
    ai_result = ai_results.get(metric_id)
    eligible, reason = qwen_review_eligibility(row)
    if st.button("关闭证据", key=f"review-close-evidence-{metric_id}", width="stretch"):
        st.session_state["review-selected-evidence-id"] = None
        st.markdown(
            """
            <aside class="review-evidence-drawer empty">
              <div class="drawer-topline">证据详情</div>
              <strong>选择一条复核项</strong>
              <p>点击队列右侧的“证据”，这里会显示原文、AI 解释和建议处理方式。</p>
            </aside>
            """,
            unsafe_allow_html=True,
        )
        return
    st.markdown(_review_evidence_drawer_html(row, ai_result, eligible, reason, view_item), unsafe_allow_html=True)


def _review_human_reason_text(reason: object) -> str:
    text = str(reason or "").strip()
    if not text:
        return "暂无不足原因"
    labels = {
        "deterministic_precheck_failed": "规则预检未通过，需人工确认",
        "missing_extracted_text": "缺少可复核的抽取文本",
        "missing_evidence_text": "缺少原文证据",
        "needs_evidence": "证据不足，需补充来源",
        "requires_ir_scrape": "需从 IR / SEC 披露中补充抽取",
    }
    if text in labels:
        return labels[text]
    if "_" in text and text.isascii():
        return "证据不足，需人工确认"
    return text


def _review_system_reason_text(reason: object) -> str:
    text = str(reason or "").strip()
    if not text:
        return "暂无系统说明。"
    lowered = text.lower()
    if "remaining performance obligations is a required ai cloud infra" in lowered:
        return (
            "RPO 是 AI 云买区模型的核心经营输入，会影响置信度。"
            "revenue backlog 可能包含 RPO 以外的已承诺未来收入，不能直接当作纯 RPO 使用；"
            "需从 SEC 10-Q / 10-K 或 8-K 披露中找到明确的 remaining performance obligations，并人工确认。"
        )
    if "is a core hood brokerage/fintech operating input" in lowered and "do not substitute" in lowered:
        metric = text.split(" is a core ", 1)[0].strip() or "该字段"
        metric_label_map = {
            "auc": "AUC",
            "net deposits": "net deposits",
            "transaction revenue": "transaction revenue",
            "interest revenue": "interest revenue",
            "subscription / gold revenue": "subscription / Gold revenue",
            "normalized earnings": "normalized earnings",
            "normalized ebitda": "normalized EBITDA",
        }
        display_metric = metric_label_map.get(metric.lower(), metric)
        if metric.lower() == "normalized earnings":
            return "未在当前披露文本中找到 normalized earnings，需人工确认 non-GAAP 盈利口径。"
        return (
            f"{display_metric} 是 HOOD 券商 / 金融科技买区模型的核心经营输入，影响系统置信度；"
            "不得用 P/S、P/FCF 或 FCF yield 替代。需从 SEC / IR 披露中补充证据并人工确认。"
        )
    labels = {
        "Extracted from SEC / IR / transcript; confirmation is required before scoring.": (
            "已从 SEC / IR / 电话会文本提取，进入评分前需人工确认。"
        ),
    }
    return labels.get(text, text)


def _review_evidence_drawer_html(row: dict, ai_result: dict | None, eligible: bool, reason: str, view_item: dict | None = None) -> str:
    symbol = str(row.get("symbol") or "")
    metric_name = view_item.get("metric") if isinstance(view_item, dict) else row.get("displayName") or row.get("metricKey")
    evidence_text = str(row.get("evidenceText") or row.get("extractedText") or "").strip()
    system_reason = str(row.get("systemReason") or row.get("explanation") or "").strip()
    evidence_summary = str((view_item or {}).get("evidenceSummary") or "").strip()
    source_text = _review_source_display(row)
    ai_explanation = ""
    if ai_result:
        decision = str(ai_result.get("aiDecision") or "")
        ai_explanation = (
            f"{AI_DECISION_LABELS.get(decision, decision)}；"
            f"AI置信度 {float(ai_result.get('confidenceScore') or 0):.0%}；"
            f"{str(ai_result.get('explanationZh') or '').strip()}"
        )
    shortage = _review_human_reason_text(reason if not eligible else row.get("reviewStatus"))
    action = _review_action_hint(row, ai_result, eligible, reason, view_item)
    quote = str((ai_result or {}).get("evidenceQuote") or "").strip()
    original = evidence_text or quote or evidence_summary or "暂无原文片段。"
    system = _review_system_reason_text(system_reason or evidence_summary)
    ai_text = ai_explanation or "尚未进行 Qwen 证据复核。"
    return (
        '<aside class="review-evidence-drawer">'
        '<div class="drawer-topline">证据详情</div>'
        f'<div class="drawer-head"><strong>{escape(symbol)}</strong><span>{escape(metric_label(metric_name))}</span></div>'
        f'{_review_drawer_section("系统说明", system)}'
        f'{_review_drawer_section("原文片段", original, clipped=True)}'
        f'{_review_drawer_section("AI 解释", ai_text)}'
        f'{_review_drawer_section("证据来源", source_text)}'
        f'{_review_drawer_section("不足原因", shortage)}'
        f'{_review_drawer_section("建议处理方式", action)}'
        '</aside>'
    )


def _review_drawer_section(title: str, body: object, clipped: bool = False) -> str:
    class_name = "drawer-evidence-section clipped" if clipped else "drawer-evidence-section"
    return f'<section class="{class_name}"><h4>{escape(title)}</h4><p>{escape(str(body or "暂无"))}</p></section>'


def _review_source_display(row: dict) -> str:
    source = source_type_label(row.get("sourceType"))
    title = str(row.get("sourceDocumentTitle") or row.get("sourceTitle") or "").strip()
    url = str(row.get("sourceUrl") or "").strip()
    source_type = str(row.get("sourceType") or "").strip().upper()
    lowered = f"{title} {url}".lower()
    if source_type == "SEC_8K" and "earnings" in lowered:
        title = _earnings_release_label(url) or "earnings release"
    elif title:
        title = title.replace("8-K Exhibit 99.1", "Exhibit 99.1")
    parts = [part for part in (source, title) if _review_has_display_text(part) and part != "待补"]
    text = " / ".join(parts) if parts else "待补"
    return text if not url else f"{text} · {url}"


def _earnings_release_label(url: str) -> str:
    lowered = str(url or "").lower()
    match = re.search(r"([1-4])q(\d{2})", lowered)
    if not match:
        match = re.search(r"q([1-4])(\d{2})", lowered)
    if not match:
        return "earnings release"
    quarter = match.group(1)
    year = 2000 + int(match.group(2))
    return f"Q{quarter} {year} earnings release"


def _render_rows(store: ReviewQueueStore, rows: list[dict], ai_store: AIReviewStore | None = None, view_rows: list[dict] | None = None) -> None:
    source_rows = view_rows if view_rows is not None else [{"raw": row, "item": None} for row in rows]
    ai_results = ai_store.latest_for_items([int(row["id"]) for row in rows]) if ai_store else {}
    st.markdown(f'<div class="review-list-title">{escape(_review_list_title())}</div>', unsafe_allow_html=True)
    if not source_rows:
        _render_review_empty_state()
        return
    table_col, detail_col = st.columns([3.65, 1.15], gap="medium")
    with table_col:
        for entry in source_rows:
            raw = dict(entry.get("raw") or {})
            if not raw:
                continue
            raw["__review_view_item"] = entry.get("item")
            _render_metric_row(store, raw, ai_results.get(int(raw["id"])))
    with detail_col:
        _render_review_evidence_drawer(source_rows, ai_results)


def _review_list_title() -> str:
    return {
        "待处理": "高优先级待处理",
        "影响评分": "影响评分，需人工确认",
        "可自动处理": "可自动确认 / 归档候选",
        "AI 建议": "AI 建议修正",
        "证据不足": "证据不足，先补证据",
        "已处理": "最近已处理",
    }.get(_active_review_tab(), "复核队列")


def _render_review_empty_state() -> None:
    tab_counts = st.session_state.get("review-tab-counts") or {}
    current_tab = _active_review_tab()
    suggested = [
        f"{tab} {count}"
        for tab in ("影响评分", "证据不足", "AI 建议", "可自动处理", "已处理")
        if tab != current_tab and (count := int(tab_counts.get(tab, 0) or 0)) > 0
    ]
    suggestion_text = " / ".join(suggested) if suggested else "暂无其它非空分类"
    st.markdown(
        f"""
        <div class="review-empty-state">
          <strong>当前分类暂无复核项。</strong>
          <span>可切换到：{escape(suggestion_text)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _review_view_rows_for_active_tab(view: dict, fallback_rows: list[dict]) -> list[dict]:
    active_tab = _active_review_tab()
    group_key = {
        "待处理": "highPriorityPending",
        "影响评分": "scoringImpactNeedsHuman",
        "AI 建议": "aiSuggestedCorrections",
        "证据不足": "insufficientEvidence",
        "已处理": "recentlyHandled",
    }.get(active_tab, "highPriorityPending")
    raw_by_id = {int(row["id"]): row for row in fallback_rows if row.get("id") is not None}
    if active_tab == "可自动处理":
        entries = []
        for group in view.get("groups", []):
            if group.get("key") not in {"autoConfirmCandidates", "autoArchiveCandidates"}:
                continue
            for item in group.get("items", []):
                raw = raw_by_id.get(int(item.get("id") or 0))
                if raw and all(entry["raw"].get("id") != raw.get("id") for entry in entries):
                    entries.append({"raw": raw, "item": item})
        return entries
    for group in view.get("groups", []):
        if group.get("key") != group_key:
            continue
        entries = []
        for item in group.get("items", []):
            raw = raw_by_id.get(int(item.get("id") or 0))
            if raw:
                entries.append({"raw": raw, "item": item})
        return entries
    return [{"raw": row, "item": None} for row in fallback_rows]


def _review_vm_action_label(action: str) -> str:
    return {
        "review_completed": "已处理",
        "review_ai_correction": "建议修正后确认",
        "auto_confirm_candidate": "可自动确认",
        "auto_archive_candidate": "可自动归档",
        "manual_confirm_after_evidence_review": "补证据后确认",
        "collect_evidence": "先补证据",
        "manual_confirm": "人工确认",
        "review_later": "稍后复核",
        "no_action": "无需操作",
    }.get(action, action)


def _render_sync_controls(store: ReviewQueueStore, rows: list[dict] | None = None, filters: dict | None = None) -> None:
    rows = rows if rows is not None else store.list_items()
    with st.expander("维护操作", expanded=False):
        st.caption("这些操作只在你手动点击时执行；默认复核工作台不会自动刷新或批量修复。")
        cols = st.columns([1.1, 1.0, 1.0, 1.0], vertical_alignment="center")
        if cols[0].button("同步观察池", key="review-sync-workbench", width="stretch"):
            result = ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist(load_watchlist())
            st.session_state["review_queue_sync_result"] = result
            st.toast(f"已同步 {len(result.symbols)} 只股票，生成/更新 {result.total} 项。")
            st.rerun()
        if cols[1].button("数据补齐", key="review-autofill-workbench", width="stretch"):
            result = ReviewAutopilot(queue_store=store).run_auto_fill_only(rows)
            _show_autopilot_result(result)
            st.rerun()
        if cols[2].button("Qwen 预审", key="review-qwen-workbench", width="stretch"):
            result = QwenReviewService(queue_store=store, ai_store=AIReviewStore(store.path)).review_rows(rows)
            _show_ai_run_result(result)
            st.rerun()
        if cols[3].button("处理日志", key="review-log-workbench", width="stretch"):
            st.session_state["show_review_automation_logs"] = not st.session_state.get("show_review_automation_logs", False)
        _render_auto_archive_preview_control(store, filters)
        if st.session_state.get("show_review_automation_logs"):
            _render_automation_logs(store)


def _render_auto_archive_preview_control(store: ReviewQueueStore, filters: dict | None = None) -> None:
    preview_cols = st.columns([1.0, 3.0], vertical_alignment="center")
    if preview_cols[0].button("预览低优先级归档", key="review-auto-archive-preview", width="stretch"):
        result = auto_archive_low_priority_review_items(
            store=store,
            symbol=(filters or {}).get("symbol"),
            dry_run=True,
        )
        st.session_state["review_auto_archive_preview"] = result
    result = st.session_state.get("review_auto_archive_preview")
    if not isinstance(result, dict):
        preview_cols[1].caption("仅 dry-run 预览，不会实际归档。")
        _render_auto_archive_execution_result()
        return
    items = list(result.get("items") or [])
    count = int(result.get("eligibleCount") or 0)
    sample_html = _auto_archive_sample_html(items)
    preview_cols[1].markdown(
        (
            '<div class="review-auto-archive-preview">'
            f'<div><strong>预计归档 {count} 条，仅处理低优先级项。</strong><span>当前为预览，未实际归档。</span></div>'
            f"{sample_html}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    confirm = preview_cols[1].checkbox(
        "我确认只归档上述低优先级项",
        key="review-auto-archive-execute-confirm",
        disabled=count <= 0,
    )
    execute = preview_cols[1].button(
        "执行自动归档",
        key="review-auto-archive-execute",
        width="stretch",
        disabled=count <= 0 or not confirm,
    )
    if execute:
        try:
            archive_result = auto_archive_low_priority_review_items(
                store=store,
                symbol=(filters or {}).get("symbol"),
                dry_run=False,
            )
            st.session_state["review_auto_archive_execution_result"] = archive_result
            st.session_state["review_auto_archive_preview"] = auto_archive_low_priority_review_items(
                store=store,
                symbol=(filters or {}).get("symbol"),
                dry_run=True,
            )
            st.rerun()
        except Exception as exc:
            st.session_state["review_auto_archive_execution_result"] = {
                "archivedCount": 0,
                "items": [],
                "error": str(exc),
            }
    _render_auto_archive_execution_result()


def _render_auto_archive_execution_result() -> None:
    result = st.session_state.get("review_auto_archive_execution_result")
    if not isinstance(result, dict):
        return
    items = list(result.get("items") or [])
    archived_count = int(result.get("archivedCount") or 0)
    error = str(result.get("error") or "").strip()
    error_html = f'<div class="review-auto-archive-error">{escape(error)}</div>' if error else ""
    st.markdown(
        (
            '<div class="review-auto-archive-preview done">'
            f'<div><strong>已归档 {archived_count} 条</strong><span>执行完成后已刷新复核中心视图。</span></div>'
            f"{_auto_archive_sample_html(items)}"
            f"{error_html}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _auto_archive_sample_html(items: list[dict]) -> str:
    header = (
        '<div class="review-auto-archive-sample head">'
        "<strong>股票</strong><span>指标</span><em>归档原因</em>"
        "</div>"
    )
    sample_html = "".join(
        (
            '<div class="review-auto-archive-sample">'
            f'<strong>{escape(str(item.get("symbol") or ""))}</strong>'
            f'<span>{escape(str(item.get("metric") or item.get("metricKey") or ""))}</span>'
            f'<em>{escape(_auto_archive_reason_label(item.get("reason")))}</em>'
            "</div>"
        )
        for item in items[:5]
    )
    return f"{header}{sample_html}" if sample_html else '<div class="review-auto-archive-empty">暂无可显示样例。</div>'


def _auto_archive_reason_label(reason: object) -> str:
    text = str(reason or "").strip()
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"risk_observation", "qualitative_risk", "generic_risk", "sector_risk"}:
        return "泛风险观察"
    lower_text = text.lower()
    if normalized in {"duplicate_archived", "duplicate_candidate", "duplicate"} or "duplicate" in lower_text:
        return "重复候选"
    if normalized in {"historical_value", "stale"} or "historical" in lower_text or "stale" in lower_text:
        return "历史值"
    if normalized in {"ai_auto_archived", "low_priority_review_noise"} or "low-priority" in lower_text or "low priority" in lower_text:
        return "低优先级不影响评分"
    return text or "低优先级不影响评分"


def _styles() -> str:
    return """
    <style>
      .review-toolbar {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        margin: 0 0 12px;
      }
      .review-kicker {
        color: #2563EB;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: .08em;
        text-transform: uppercase;
      }
      .review-toolbar h1 {
        margin: 2px 0 2px;
        color: #111827;
        font-size: 28px;
        line-height: 1.15;
        letter-spacing: 0;
      }
      .review-toolbar p {
        margin: 0;
        color: #6B7280;
        font-size: 14px;
      }
      .review-command-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin: -2px 0 8px;
      }
      .review-command-head strong {
        display: block;
        color: #111827;
        font-size: 14px;
        font-weight: 800;
      }
      .review-command-head span {
        color: #6B7280;
        font-size: 12px;
      }
      .review-auto-archive-preview {
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 8px;
        background: #F9FAFB;
        font-size: 12px;
      }
      .review-auto-archive-preview.done {
        margin-top: 8px;
        border-color: #BBF7D0;
        background: #F0FDF4;
      }
      .review-auto-archive-error {
        margin-top: 6px;
        color: #B91C1C;
      }
      .review-auto-archive-preview > div:first-child {
        display: flex;
        gap: 8px;
        align-items: baseline;
        justify-content: space-between;
        margin-bottom: 5px;
      }
      .review-auto-archive-preview span,
      .review-auto-archive-empty {
        color: #6B7280;
      }
      .review-auto-archive-sample {
        display: grid;
        grid-template-columns: 52px minmax(96px, 1fr) 110px;
        gap: 6px;
        padding: 3px 0;
        border-top: 1px solid #EEF2F7;
      }
      .review-auto-archive-sample strong,
      .review-auto-archive-sample span,
      .review-auto-archive-sample em {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .review-auto-archive-sample em {
        color: #6B7280;
        font-style: normal;
      }
      .review-auto-archive-sample.head {
        border-top: 0;
        padding-top: 0;
        color: #6B7280;
        font-weight: 700;
      }
      .qwen-efficiency {
        margin: 12px 0 10px;
        color: #111827;
        font-size: 13px;
      }
      .qwen-efficiency strong {
        font-weight: 800;
      }
      .qwen-efficiency span {
        margin-left: 4px;
        color: #374151;
      }
      .qwen-efficiency small {
        display: block;
        margin-top: 2px;
        color: #8A94A6;
        font-size: 11px;
      }
      .review-overview-panel {
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 7px;
        background: #FFFFFF;
        padding: 8px;
        margin: 10px 0 8px;
      }
      .review-overview-title {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 7px;
      }
      .review-overview-title strong {
        color: #111827;
        font-size: 14px;
        font-weight: 800;
      }
      .review-overview-title span {
        color: #6B7280;
        font-size: 12px;
      }
      .review-summary-strip {
        display: grid;
        grid-template-columns: repeat(6, minmax(0, 1fr));
        gap: 0;
        margin: 0;
        border: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 6px;
        overflow: hidden;
      }
      .review-ai-summary-strip {
        display: grid;
        grid-template-columns: repeat(6, minmax(0, 1fr));
        gap: 8px;
        margin: 0;
      }
      .review-ai-summary-card {
        height: 52px;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        background: #F9FAFB;
        padding: 8px 10px;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }
      .review-ai-summary-card span {
        color: #6B7280;
        font-size: 11px;
        font-weight: 700;
      }
      .review-ai-summary-card strong {
        color: #111827;
        font-size: 17px;
        line-height: 1.1;
        font-variant-numeric: tabular-nums;
      }
      .review-summary-card {
        height: 38px;
        border: 0;
        border-right: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 0;
        background: #FBFCFE;
        padding: 5px 10px;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }
      .review-summary-card:last-child {
        border-right: 0;
      }
      .review-summary-card span {
        color: #7C8797;
        font-size: 10px;
        font-weight: 700;
      }
      .review-summary-card strong {
        color: #111827;
        font-size: 16px;
        line-height: 1.1;
        font-variant-numeric: tabular-nums;
      }
      .review-list-title {
        margin: 12px 0 8px;
        color: #111827;
        font-size: 15px;
        font-weight: 800;
      }
      .metric-title {
        color: #111827;
        font-size: 12px;
        font-weight: 800;
        line-height: 1.22;
      }
      .metric-sub,
      .source-meta {
        color: #9CA3AF;
        font-size: 10.5px;
        line-height: 1.18;
      }
      .metric-value {
        color: #111827;
        font-size: 13px;
        font-weight: 800;
        font-variant-numeric: tabular-nums;
      }
      .review-row-marker {
        height: 1px;
        border-top: 1px solid rgba(15, 23, 42, 0.06);
        margin: 0;
      }
      .review-symbol {
        color: #0F172A;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0;
        line-height: 1.1;
      }
      .review-value-stack {
        display: grid;
        gap: 0;
        min-width: 0;
      }
      .review-value-stack strong {
        color: #111827;
        font-size: 12px;
        line-height: 1.2;
        font-variant-numeric: tabular-nums;
      }
      .review-value-stack span,
      .review-action-hint {
        color: #64748B;
        font-size: 11px;
        line-height: 1.2;
      }
      .review-action-hint {
        display: grid;
        gap: 1px;
      }
      .review-action-hint span {
        color: #9CA3AF;
        font-size: 10px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .review-action-hint strong {
        color: #475569;
        font-size: 11px;
        font-weight: 700;
        line-height: 1.15;
      }
      .review-empty-state {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 7px;
        background: #FBFCFE;
        padding: 9px 10px;
        color: #475569;
        font-size: 12px;
      }
      .review-empty-state strong {
        color: #0F172A;
        font-size: 13px;
      }
      .review-empty-state span {
        color: #64748B;
        font-size: 12px;
      }
      .review-evidence-drawer {
        position: sticky;
        top: 70px;
        display: grid;
        gap: 8px;
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 8px;
        background: #FFFFFF;
        padding: 10px;
        box-shadow: 0 12px 26px rgba(15, 23, 42, 0.06);
      }
      .review-evidence-drawer.empty {
        background: #FBFCFE;
        box-shadow: none;
      }
      .review-evidence-drawer .drawer-topline {
        color: #64748B;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.04em;
      }
      .review-evidence-drawer .drawer-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 8px;
        border-bottom: 1px solid rgba(148, 163, 184, 0.14);
        padding-bottom: 7px;
      }
      .review-evidence-drawer .drawer-head strong,
      .review-evidence-drawer.empty strong {
        color: #0F172A;
        font-size: 14px;
        font-weight: 850;
      }
      .review-evidence-drawer .drawer-head span,
      .review-evidence-drawer.empty p {
        color: #64748B;
        font-size: 11px;
        line-height: 1.4;
      }
      .drawer-evidence-section {
        display: grid;
        gap: 3px;
        border: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 7px;
        background: #FBFCFE;
        padding: 7px 8px;
      }
      .drawer-evidence-section h4 {
        margin: 0;
        color: #475569;
        font-size: 10.5px;
        font-weight: 800;
      }
      .drawer-evidence-section p {
        margin: 0;
        color: #111827;
        font-size: 11.5px;
        line-height: 1.45;
        overflow-wrap: anywhere;
      }
      .drawer-evidence-section.clipped p {
        max-height: 150px;
        overflow: auto;
      }
      .review-evidence-panel {
        margin: 0;
        display: grid;
        gap: 4px;
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 7px;
        background: #FBFCFE;
        padding: 8px 10px;
        color: #475569;
        font-size: 12px;
        line-height: 1.45;
      }
      .review-evidence-panel strong {
        color: #0F172A;
        font-size: 12px;
      }
      .review-evidence-panel span {
        max-height: 92px;
        overflow: hidden;
      }
      .source-snippet {
        margin-top: 8px;
        color: #4B5563;
        font-size: 12px;
        line-height: 1.5;
        background: #F7F8FA;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 10px 12px;
      }
      .review-confirm-notice {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        margin: 8px 0 6px;
        border: 1px solid #D1FAE5;
        border-radius: 8px;
        background: #F0FDF4;
        padding: 9px 12px;
        color: #166534;
        font-size: 12px;
      }
      .review-confirm-notice strong {
        color: #14532D;
        font-size: 13px;
      }
      .review-impact-panel {
        margin: 8px 0;
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 4px 12px;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        background: #F8FAFC;
        padding: 10px 12px;
        color: #4B5563;
        font-size: 12px;
        line-height: 1.5;
      }
      .review-impact-panel strong {
        grid-column: 1 / -1;
        color: #111827;
        font-size: 12px;
      }
      .ai-review-panel {
        margin-top: 10px;
        display: flex;
        flex-wrap: wrap;
        gap: 6px 8px;
        background: #F8FAFC;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 10px 12px;
        color: #4B5563;
        font-size: 12px;
        line-height: 1.55;
      }
      .ai-review-panel strong {
        color: #111827;
        font-size: 12px;
      }
      .ai-review-panel p,
      .ai-review-panel blockquote,
      .ai-review-panel em {
        margin: 0;
        flex-basis: 100%;
      }
      .ai-review-panel blockquote {
        border-left: 3px solid #BFDBFE;
        padding-left: 8px;
        color: #6B7280;
        max-height: 72px;
        overflow: hidden;
      }
      .ai-review-panel em {
        color: #B45309;
        font-style: normal;
      }
      .review-badge {
        display: inline-flex;
        min-height: 18px;
        align-items: center;
        padding: 0 7px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 650;
        border: 1px solid transparent;
        white-space: nowrap;
        line-height: 18px;
      }
      .tone-green { background: #F0FDF4; color: #166534; border-color: #CDEFD8; }
      .tone-blue { background: #F2F6FB; color: #334155; border-color: #E4EAF1; }
      .tone-yellow { background: #FFFBEB; color: #854D0E; border-color: #F4E7B0; }
      .tone-orange { background: #FFF7ED; color: #92400E; border-color: #F7D8A9; }
      .tone-red { background: #FFF5F5; color: #991B1B; border-color: #F3D2D2; }
      .tone-gray { background: #F8FAFC; color: #475569; border-color: #E4EAF1; }
      .review-summary-card.tone-green,
      .review-summary-card.tone-blue,
      .review-summary-card.tone-yellow,
      .review-summary-card.tone-orange,
      .review-summary-card.tone-red,
      .review-summary-card.tone-gray {
        background: #FBFCFE;
        border-color: rgba(148, 163, 184, 0.16);
      }
      .review-summary-card.tone-green strong { color: #166534; }
      .review-summary-card.tone-blue strong { color: #1E3A8A; }
      .review-summary-card.tone-yellow strong { color: #854D0E; }
      .review-summary-card.tone-orange strong { color: #92400E; }
      .review-summary-card.tone-red strong { color: #991B1B; }
      .review-summary-card.tone-gray strong { color: #334155; }
      .review-summary-card.tone-green span,
      .review-summary-card.tone-blue span,
      .review-summary-card.tone-yellow span,
      .review-summary-card.tone-orange span,
      .review-summary-card.tone-red span,
      .review-summary-card.tone-gray span {
        color: #7C8797;
      }
      div[data-testid="stButton"] > button {
        min-height: 24px;
        height: 24px;
        padding: 0 7px;
        border-radius: 999px;
        border-color: rgba(148, 163, 184, 0.28);
        background: rgba(255, 255, 255, 0.72);
        color: #475569;
        font-size: 11px;
        font-weight: 700;
        line-height: 1;
        box-shadow: none;
      }
      div[data-testid="stButton"] p {
        margin: 0;
        font-size: 11px;
        line-height: 1;
        white-space: nowrap;
      }
      div[data-testid="stButton"] > button:hover {
        border-color: rgba(71, 85, 105, 0.36);
        background: #F8FAFC;
        color: #0F172A;
      }
      div[data-testid="stButton"] > button:disabled {
        opacity: 0.42;
        color: #94A3B8;
        background: #F8FAFC;
      }
      div[class*="st-key-review-confirm-"] button,
      div[class*="st-key-review-reject-"] button,
      div[class*="st-key-review-archive-"] button,
      div[class*="st-key-review-toggle-evidence-"] button {
        min-height: 20px;
        height: 20px;
        padding: 0 2px;
        border-color: transparent;
        background: transparent;
        color: #64748B;
        font-size: 11px;
        font-weight: 750;
      }
      div[class*="st-key-review-confirm-"] button:hover,
      div[class*="st-key-review-reject-"] button:hover,
      div[class*="st-key-review-archive-"] button:hover,
      div[class*="st-key-review-toggle-evidence-"] button:hover {
        border-color: rgba(148, 163, 184, 0.2);
        background: #F8FAFC;
        color: #0F172A;
      }
      div[data-testid="stExpander"] details {
        border-color: rgba(148, 163, 184, 0.16);
        border-radius: 7px;
        background: #FBFCFE;
      }
      div[data-testid="stExpander"] details summary {
        min-height: 28px;
        padding: 4px 8px;
        font-size: 12px;
        color: #475569;
      }
      @media (max-width: 1100px) {
        .review-summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .review-ai-summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .review-overview-title { display: block; }
      }
    </style>
    """
