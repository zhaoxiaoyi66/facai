from __future__ import annotations

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
from data.review_queue_builder import ReviewQueueBuilder, ReviewQueueStore
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
    "extraction_rejected_by_rule": "规则拦截图谱",
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


def render() -> None:
    st.markdown(_styles(), unsafe_allow_html=True)
    store = ReviewQueueStore()
    ai_store = AIReviewStore(store.path)
    st.markdown(
        """
        <div class="review-toolbar">
          <div>
            <div class="review-kicker">Manual Review Center</div>
            <h1>数据复核中心</h1>
            <p>覆盖整个观察池：自动抽取值、缺失KPI、低置信度推导和定性风险统一复核。</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _render_summary(store.summary(), ai_store.summary())
    filters = _render_filters(store)
    base_rows = _filtered_rows(store, filters)
    _render_sync_controls(store, base_rows, filters)
    rows = _apply_ai_filters(base_rows, ai_store)
    _render_ai_controls(store, ai_store, filters, base_rows)
    _render_rows(store, rows, ai_store)


def _render_sync_controls(store: ReviewQueueStore) -> None:
    cols = st.columns([1.45, 1.0, 1.0, 1.0, 2.2], vertical_alignment="center")
    if cols[0].button("同步当前观察池复核队列", width="stretch", key="review-sync-watchlist"):
        result = ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist(load_watchlist())
        st.session_state["review_queue_sync_result"] = result
        st.toast(f"已同步 {len(result.symbols)} 只股票")
        st.rerun()
    st.session_state["review-filter-only-extracted"] = cols[1].checkbox("仅显示有值待确认", key="review-only-extracted")
    st.session_state["review-filter-only-needs-data"] = cols[2].checkbox("仅显示需要补齐", key="review-only-needs-data")
    st.session_state["review-filter-affects-scoring"] = cols[3].checkbox("仅显示影响评分", key="review-only-affects-scoring")

    result = st.session_state.get("review_queue_sync_result")
    if result:
        counts = "、".join(f"{ITEM_TYPE_LABELS.get(key, key)} {value}" for key, value in result.item_type_counts.items())
        cols[4].caption(f"上次同步：{len(result.symbols)}只股票，{result.total}项；{counts or '暂无项目'}")
    else:
        cols[4].caption("同步只读取本地缓存和评分缺口，不会批量抓 SEC / IR。")


def _render_summary(summary: dict, ai_summary: dict | None = None) -> None:
    cards = [
        ("涉及股票", summary.get("symbols", 0), "blue"),
        ("待确认数据", summary.get("pending_review", 0), "yellow"),
        ("需要补齐", summary.get("needs_data", 0), "orange"),
        ("需要补证据", summary.get("needs_evidence", 0), "orange"),
        ("低置信度推导", summary.get("derived_low_confidence", 0), "gray"),
        ("定性风险复核", summary.get("qualitative_risk", 0), "orange"),
        ("已确认", summary.get("approved", 0), "green"),
        ("已驳回", summary.get("rejected", 0), "red"),
    ]
    html = "".join(
        f'<div class="review-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
        for label, value, tone in cards
    )
    st.markdown(f'<div class="review-summary-strip">{html}</div>', unsafe_allow_html=True)
    if ai_summary:
        ai_cards = [
            ("AI已预审", ai_summary.get("total", 0), "blue"),
            ("AI建议确认", ai_summary.get("recommend_approve", 0), "green"),
            ("AI建议驳回", ai_summary.get("recommend_reject", 0), "red"),
            ("AI建议修正", ai_summary.get("recommend_correct", 0), "orange"),
            ("AI要求人工判断", ai_summary.get("needs_human_review", 0), "yellow"),
            ("证据不足", ai_summary.get("not_enough_evidence", 0), "gray"),
            ("幻觉风险", ai_summary.get("hallucination_risk", 0), "red"),
        ]
        ai_html = "".join(
            f'<div class="review-ai-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
            for label, value, tone in ai_cards
        )
        st.markdown(f'<div class="review-ai-summary-strip">{ai_html}</div>', unsafe_allow_html=True)
        triage_cards = [
            ("AI已预审", ai_summary.get("total", 0), "blue"),
            ("AI自动确认", summary.get("auto_approved_by_ai", ai_summary.get("auto_approved_by_ai", 0)), "green"),
            ("AI建议确认", summary.get("ai_recommend_approve", ai_summary.get("ai_recommend_approve", 0)), "blue"),
            ("AI建议修正", summary.get("ai_recommend_correct", ai_summary.get("ai_recommend_correct", 0)), "orange"),
            ("AI建议驳回", summary.get("ai_recommend_reject", ai_summary.get("ai_recommend_reject", 0)), "red"),
            ("证据不足", summary.get("ai_not_enough_evidence", ai_summary.get("ai_not_enough_evidence", 0)), "gray"),
            ("AI要求人工判断", summary.get("ai_needs_human_review", ai_summary.get("ai_needs_human_review", 0)), "yellow"),
        ]
        triage_html = "".join(
            f'<div class="review-ai-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
            for label, value, tone in triage_cards
        )
        st.markdown(f'<div class="review-ai-summary-strip">{triage_html}</div>', unsafe_allow_html=True)


def _render_filters(store: ReviewQueueStore) -> dict:
    rows = store.list_items()
    symbols = sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")})
    metric_keys = sorted({str(row.get("metricKey") or "") for row in rows if row.get("metricKey")})
    item_types = sorted({str(row.get("itemType") or "") for row in rows if row.get("itemType")})
    source_types = sorted({str(row.get("sourceType") or "") for row in rows if row.get("sourceType")})
    model_types = sorted({str(row.get("modelType") or _model_type_for_symbol(str(row.get("symbol") or ""))) for row in rows if row.get("symbol")})

    query_symbol = str(st.query_params.get("review_symbol") or st.query_params.get("symbol") or "").upper()
    symbol_options = ["全部", *symbols]
    symbol_index = symbol_options.index(query_symbol) if query_symbol in symbol_options else 0

    with st.container(border=True):
        cols = st.columns([0.9, 1.2, 1.15, 1.1, 0.95, 1.05, 1.1])
        symbol = cols[0].selectbox("股票", symbol_options, index=symbol_index, key="review-filter-symbol")
        metric_key = cols[1].selectbox("指标", ["全部", *metric_keys], format_func=lambda value: "全部" if value == "全部" else metric_label(value), key="review-filter-metric")
        item_type = cols[2].selectbox("类型", ["全部", *item_types], format_func=lambda value: "全部" if value == "全部" else ITEM_TYPE_LABELS.get(value, value), key="review-filter-item-type")
        source_type = cols[3].selectbox("来源", ["全部", *source_types], format_func=lambda value: "全部" if value == "全部" else source_type_label(value), key="review-filter-source")
        confidence = cols[4].selectbox("置信度", ["全部", "high", "medium", "low"], format_func=lambda value: "全部" if value == "全部" else confidence_label(value), key="review-filter-confidence")
        review_status = cols[5].selectbox("复核状态", ["全部", *STATUS_LABELS.keys()], format_func=lambda value: "全部" if value == "全部" else STATUS_LABELS.get(value, value), key="review-filter-status")
        model_type = cols[6].selectbox("模型", ["全部", *model_types], format_func=lambda value: "全部" if value == "全部" else model_type_label(value), key="review-filter-model")
    return {
        "symbol": None if symbol == "全部" else symbol,
        "metric_key": None if metric_key == "全部" else metric_key,
        "item_type": None if item_type == "全部" else item_type,
        "source_type": None if source_type == "全部" else source_type,
        "confidence": None if confidence == "全部" else confidence,
        "review_status": None if review_status == "全部" else review_status,
        "model_type": None if model_type == "全部" else model_type,
        "affects_scoring": bool(st.session_state.get("review-filter-affects-scoring")),
    }


def _filtered_rows(store: ReviewQueueStore, filters: dict) -> list[dict]:
    review_status = filters["review_status"]
    item_type = filters["item_type"]
    if st.session_state.get("review-filter-only-extracted"):
        item_type = "extracted_value"
    if st.session_state.get("review-filter-only-needs-data"):
        review_status = "needs_data"
    return store.list_items(
        symbol=filters["symbol"],
        metric_key=filters["metric_key"],
        item_type=item_type,
        source_type=filters["source_type"],
        confidence=filters["confidence"],
        review_status=review_status,
        model_type=filters["model_type"],
        affects_scoring=filters["affects_scoring"],
    )


def _render_metric_row(store: ReviewQueueStore, row: dict, ai_result: dict | None = None) -> None:
    metric_id = int(row["id"])
    status = str(row.get("reviewStatus") or "pending_review")
    confidence = str(row.get("confidence") or "")
    with st.container(border=True):
        cols = st.columns([0.6, 1.35, 1.05, 0.9, 0.9, 0.95, 1.0, 1.0, 1.7])
        cols[0].markdown(f"**{escape(str(row.get('symbol') or ''))}**")
        cols[1].markdown(
            f"<div class='metric-title'>{escape(metric_label(row.get('displayName') or row.get('metricKey') or 'N/A'))}</div>"
            f"<div class='metric-sub'>{escape(_metric_row_subtitle(row))}</div>",
            unsafe_allow_html=True,
        )
        item_type = str(row.get("itemType") or "")
        cols[2].markdown(_badge(ITEM_TYPE_LABELS.get(item_type, item_type), ITEM_TYPE_TONES.get(item_type, "gray")), unsafe_allow_html=True)
        cols[3].markdown(f"<span class='metric-value'>{escape(_format_value(row.get('value'), row.get('unit')))}</span>", unsafe_allow_html=True)
        cols[4].markdown(escape(source_type_label(row.get("sourceType") or "N/A")))
        cols[5].markdown(_badge(confidence_label(confidence or "N/A"), _confidence_tone(confidence)), unsafe_allow_html=True)
        cols[6].markdown(escape(_affects_label(row.get("affects"))))
        cols[7].markdown(_badge(STATUS_LABELS.get(status, status), STATUS_TONES.get(status, "gray")), unsafe_allow_html=True)
        with cols[8]:
            action_cols = st.columns(3)
            approve_disabled = status in {"approved", "needs_data"}
            if action_cols[0].button("确认", key=f"review-approve-v2-{metric_id}", disabled=approve_disabled, width="stretch"):
                store.update_review_status(metric_id, "approved")
                st.toast("已确认使用")
                st.rerun()
            if action_cols[1].button("驳回", key=f"review-reject-v2-{metric_id}", disabled=status == "rejected", width="stretch"):
                store.update_review_status(metric_id, "rejected")
                st.toast("已驳回")
                st.rerun()
            _render_correction_popover(store, row, action_cols[2])

        detail_cols = st.columns([3.2, 1.2])
        snippet = _truncate(str(row.get("extractedText") or row.get("explanation") or ""), 360)
        detail_cols[0].markdown(
            f"<div class='source-snippet'><b>{escape(_detail_title(row))}</b><br>{escape(snippet or '暂无说明')}</div>"
            f"<div class='source-meta'>{escape(action_label(row.get('recommendedAction') or '复核'))} · 更新 {escape(str(row.get('updatedAt') or 'N/A'))}</div>",
            unsafe_allow_html=True,
        )
        url = row.get("sourceUrl")
        if url:
            detail_cols[1].link_button("打开来源", str(url), width="stretch")
        else:
            detail_cols[1].caption("暂无来源链接")
        if ai_result:
            _render_ai_action_controls(store, row, ai_result)
            st.markdown(_ai_result_html(ai_result), unsafe_allow_html=True)


def _render_ai_controls(store: ReviewQueueStore, ai_store: AIReviewStore, filters: dict, rows: list[dict]) -> None:
    assistant = QwenReviewService(queue_store=store, ai_store=ai_store)
    candidates = qwen_review_candidates(rows)
    with st.container(border=True):
        cols = st.columns([1.25, 1.0, 1.0, 1.0, 1.45], vertical_alignment="center")
        if not assistant.configured:
            cols[4].caption("未配置 Qwen 复核，仍可手动复核。")
        else:
            cols[4].caption(f"本次最多预审 {min(len(candidates), assistant.max_items)} 条；跳过已确认、已驳回、FMP 和已计算项。")
        if cols[0].button("仅运行 Qwen 证据复核", key="qwen-review-filtered", width="stretch"):
            result = assistant.review_rows(rows)
            _show_ai_run_result(result)
            st.rerun()
        st.session_state["review-filter-ai-reject"] = cols[1].checkbox("AI建议驳回", key="review-ai-reject")
        st.session_state["review-filter-ai-correct"] = cols[2].checkbox("AI建议修正", key="review-ai-correct")
        st.session_state["review-filter-ai-human"] = cols[3].checkbox("AI要求人工判断", key="review-ai-human")


def _show_ai_run_result(result) -> None:
    st.session_state["qwen_review_last_result"] = {
        "reviewed": int(getattr(result, "reviewed", 0) or 0),
        "skipped": int(getattr(result, "skipped", 0) or 0),
        "auto_approved": int(getattr(result, "auto_approved", 0) or 0),
        "needs_human": int(getattr(result, "needs_human", 0) or 0),
        "not_configured": bool(getattr(result, "not_configured", False)),
        "errors": list(getattr(result, "errors", None) or []),
    }
    if getattr(result, "not_configured", False):
        st.toast("未配置 Qwen 复核，仍可手动复核。")
        return
    errors = getattr(result, "errors", None) or []
    if errors:
        st.toast(f"Qwen预审完成 {result.reviewed} 条，{len(errors)} 条失败")
    else:
        st.toast(f"Qwen预审完成 {result.reviewed} 条；自动确认 {result.auto_approved} 条")


def _render_last_qwen_result() -> None:
    result = st.session_state.get("qwen_review_last_result")
    if not isinstance(result, dict):
        return
    if result.get("not_configured"):
        st.warning("Qwen 未配置：请检查 .env 中的 QWEN_API_KEY。")
        return
    errors = result.get("errors") or []
    message = (
        f"Qwen预审完成：处理 {result.get('reviewed', 0)} 条，"
        f"跳过 {result.get('skipped', 0)} 条，"
        f"AI自动确认 {result.get('auto_approved', 0)} 条，"
        f"需要人工判断 {result.get('needs_human', 0)} 条。"
    )
    if errors:
        st.warning(message + f" 失败 {len(errors)} 条。")
        with st.expander("查看失败原因", expanded=False):
            for error in errors[:10]:
                st.caption(str(error))
    else:
        st.success(message)


        if not candidates:
            st.info("当前筛选结果里没有可交给 Qwen 预审的项目。请先同步复核队列，或取消过窄的筛选条件。")
        _render_last_qwen_result()


def _apply_ai_filters(rows: list[dict], ai_store: AIReviewStore) -> list[dict]:
    latest = ai_store.latest_for_items([int(row["id"]) for row in rows])
    filtered = []
    for row in rows:
        ai_result = latest.get(int(row["id"]))
        if st.session_state.get("review-filter-ai-reject") and not _is_ai_reject(ai_result):
            continue
        if st.session_state.get("review-filter-ai-correct") and not _is_ai_correct(ai_result):
            continue
        if st.session_state.get("review-filter-ai-human") and not _is_ai_human(ai_result):
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: _ai_sort_key(row, latest.get(int(row["id"]))))


def _render_rows(store: ReviewQueueStore, rows: list[dict], ai_store: AIReviewStore | None = None) -> None:
    if not rows:
        st.info("当前筛选条件下没有待展示的复核项。可以先点击“同步当前观察池复核队列”。")
        return

    ai_results = ai_store.latest_for_items([int(row["id"]) for row in rows]) if ai_store else {}
    st.markdown('<div class="review-list-title">复核队列</div>', unsafe_allow_html=True)
    for row in rows:
        _render_metric_row(store, row, ai_results.get(int(row["id"])))


def _render_metric_row(store: ReviewQueueStore, row: dict, ai_result: dict | None = None) -> None:
    metric_id = int(row["id"])
    status = str(row.get("reviewStatus") or "pending_review")
    confidence = str(row.get("confidence") or "")
    with st.container(border=True):
        cols = st.columns([0.6, 1.35, 1.05, 0.9, 0.9, 0.95, 1.0, 1.0, 1.7])
        cols[0].markdown(f"**{escape(str(row.get('symbol') or ''))}**")
        cols[1].markdown(
            f"<div class='metric-title'>{escape(metric_label(row.get('displayName') or row.get('metricKey') or 'N/A'))}</div>"
            f"<div class='metric-sub'>{escape(model_type_label(row.get('modelType')))}</div>",
            unsafe_allow_html=True,
        )
        item_type = str(row.get("itemType") or "")
        cols[2].markdown(_badge(ITEM_TYPE_LABELS.get(item_type, item_type), ITEM_TYPE_TONES.get(item_type, "gray")), unsafe_allow_html=True)
        cols[3].markdown(f"<span class='metric-value'>{escape(_format_value(row.get('value'), row.get('unit')))}</span>", unsafe_allow_html=True)
        cols[4].markdown(escape(source_type_label(row.get("sourceType") or "N/A")))
        cols[5].markdown(_badge(confidence_label(confidence or "N/A"), _confidence_tone(confidence)), unsafe_allow_html=True)
        cols[6].markdown(escape(_affects_label(row.get("affects"))))
        cols[7].markdown(_badge(STATUS_LABELS.get(status, status), STATUS_TONES.get(status, "gray")), unsafe_allow_html=True)
        with cols[8]:
            action_cols = st.columns(3)
            approve_disabled = status in {"approved", "needs_data"}
            if action_cols[0].button("确认", key=f"review-approve-{metric_id}", disabled=approve_disabled, width="stretch"):
                store.update_review_status(metric_id, "approved")
                st.toast("已确认使用")
                st.rerun()
            if action_cols[1].button("驳回", key=f"review-reject-{metric_id}", disabled=status == "rejected", width="stretch"):
                store.update_review_status(metric_id, "rejected")
                st.toast("已驳回")
                st.rerun()
            _render_correction_popover(store, row, action_cols[2])

        detail_cols = st.columns([3.2, 1.2])
        snippet = _truncate(str(row.get("extractedText") or row.get("explanation") or ""), 360)
        detail_cols[0].markdown(
            f"<div class='source-snippet'><b>{escape(_detail_title(row))}</b><br>{escape(snippet or '暂无说明')}</div>"
            f"<div class='source-meta'>{escape(action_label(row.get('recommendedAction') or '复核'))} · 更新 {escape(str(row.get('updatedAt') or 'N/A'))}</div>",
            unsafe_allow_html=True,
        )
        url = row.get("sourceUrl")
        if url:
            detail_cols[1].link_button("打开来源", str(url), width="stretch")
        else:
            detail_cols[1].caption("暂无来源链接")
        if ai_result:
            st.markdown(_ai_result_html(ai_result), unsafe_allow_html=True)


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
    if value is None:
        return "暂无"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == "percent":
        return f"{number * 100:.1f}%"
    if unit == "multiple":
        return f"{number:.2f}x"
    if unit == "money":
        return f"{number:,.0f}"
    return f"{number:.4g}"


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


def _ai_result_html(result: dict) -> str:
    decision = str(result.get("aiDecision") or "")
    tone = AI_DECISION_TONES.get(decision, "gray")
    warnings = result.get("warnings") or []
    warning_text = "；".join(str(item) for item in warnings if item)
    if result.get("hallucinationRisk") and "hallucination_risk" not in warning_text:
        warning_text = (warning_text + "；" if warning_text else "") + "hallucination_risk"
    match_text = (
        f"证据 {AI_MATCH_LABELS.get(str(result.get('evidenceMatch')), result.get('evidenceMatch'))}"
        f" · 期间 {AI_MATCH_LABELS.get(str(result.get('periodMatch')), result.get('periodMatch'))}"
        f" · 单位 {AI_MATCH_LABELS.get(str(result.get('unitMatch')), result.get('unitMatch'))}"
    )
    corrected = ""
    if result.get("correctedValue") is not None:
        corrected = f"<span>建议修正：{escape(str(result.get('correctedValue')))} {escape(str(result.get('correctedUnit') or ''))} {escape(str(result.get('correctedPeriod') or ''))}</span>"
    return (
        "<div class='ai-review-panel'>"
        f"{_badge(AI_DECISION_LABELS.get(decision, decision), tone)}"
        f"<strong>AI置信度 {float(result.get('confidenceScore') or 0):.0%}</strong>"
        f"<span>provider=qwen · model={escape(str(result.get('model') or 'qwen'))}</span>"
        f"<span>{escape(match_text)}</span>"
        f"<span>{'幻觉风险：是' if result.get('hallucinationRisk') else '幻觉风险：否'}</span>"
        f"{corrected}"
        f"<p>{escape(str(result.get('explanationZh') or ''))}</p>"
        f"<blockquote>{escape(str(result.get('evidenceQuote') or ''))}</blockquote>"
        f"<em>{escape(warning_text)}</em>"
        "</div>"
    )


def _is_ai_reject(result: dict | None) -> bool:
    if not result:
        return False
    return result.get("aiDecision") == "recommend_reject" or result.get("evidenceMatch") == "mismatch" or result.get("appliedAction") == "suggested_reject"


def _is_ai_correct(result: dict | None) -> bool:
    if not result:
        return False
    return result.get("aiDecision") == "recommend_correct" or result.get("appliedAction") == "manually_correct_candidate"


def _is_ai_human(result: dict | None) -> bool:
    if not result:
        return False
    return str(result.get("aiDecision") or "") in {"needs_human_review", "needs_more_source", "not_enough_evidence"} or str(result.get("appliedAction") or "") in {
        "needs_human_review",
        "needs_more_source",
        "not_enough_evidence",
    }


def _ai_sort_key(row: dict, result: dict | None) -> tuple[int, str, str]:
    if _is_ai_reject(result):
        rank = 0
    elif _is_ai_correct(result):
        rank = 1
    elif _is_ai_human(result) and _affects_scoring(row.get("affects")):
        rank = 2
    elif result and result.get("aiDecision") == "not_enough_evidence":
        rank = 3
    elif result and result.get("aiDecision") == "recommend_approve":
        rank = 4
    else:
        rank = 5
    return (rank, str(row.get("symbol") or ""), str(row.get("metricKey") or ""))


def _affects_scoring(value: object) -> bool:
    parts = {part.strip() for part in str(value or "").split(",") if part.strip()}
    return bool(parts & {"Quality", "Entry", "Risk"})


def _truncate(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _render_ai_controls(store: ReviewQueueStore, ai_store: AIReviewStore, filters: dict, rows: list[dict]) -> None:
    assistant = QwenReviewService(queue_store=store, ai_store=ai_store)
    candidates = qwen_review_candidates(rows)
    with st.container(border=True):
        cols = st.columns([1.25, 1.0, 1.0, 1.0, 1.0, 1.45], vertical_alignment="center")
        cols[5].caption(
            "未配置 Qwen 复核，仍可手动复核。"
            if not assistant.configured
            else f"本次最多预审 {min(len(candidates), assistant.max_items)} 条；跳过已确认、已驳回、FMP 和已计算项。"
        )
        if cols[0].button("仅运行 Qwen 证据复核", key="qwen-review-filtered-v2", width="stretch"):
            result = assistant.review_rows(rows)
            _show_ai_run_result(result)
            st.rerun()
        st.session_state["review-filter-ai-reject"] = cols[1].checkbox("只看AI建议驳回", key="review-ai-reject-v2")
        st.session_state["review-filter-ai-correct"] = cols[2].checkbox("只看AI建议修正", key="review-ai-correct-v2")
        st.session_state["review-filter-ai-not-enough"] = cols[3].checkbox("只看证据不足", key="review-ai-not-enough-v2")
        st.session_state["review-filter-ai-human"] = cols[4].checkbox("只看需要人工判断", key="review-ai-human-v2")
        batch_cols = st.columns([1.1, 1.1, 1.1, 2.2], vertical_alignment="center")
        if batch_cols[0].button("批量接受AI自动确认项", key="review-batch-auto-approve", width="stretch"):
            changes = store.batch_accept_ai_auto_approved([int(row["id"]) for row in rows])
            st.toast(f"已处理 {len(changes)} 条AI自动确认项")
            st.rerun()
        if batch_cols[1].button("批量隐藏AI自动确认项", key="review-batch-hide-auto", width="stretch"):
            count = store.hide_auto_approved_items([int(row["id"]) for row in rows])
            st.toast(f"已隐藏 {count} 条AI自动确认项")
            st.rerun()
        if batch_cols[2].button("批量进入人工复核", key="review-batch-human", width="stretch"):
            count = store.mark_ai_needs_human([int(row["id"]) for row in rows])
            st.toast(f"已标记 {count} 条为人工复核")
            st.rerun()
        st.session_state["review-filter-hide-auto-approved"] = batch_cols[3].checkbox(
            "隐藏AI自动确认项",
            value=True,
            key="review-hide-auto-approved",
        )


def _apply_ai_filters(rows: list[dict], ai_store: AIReviewStore) -> list[dict]:
    latest = ai_store.latest_for_items([int(row["id"]) for row in rows])
    filtered = []
    for row in rows:
        ai_result = latest.get(int(row["id"]))
        triage_status = _ai_triage_status(row, ai_result)
        if st.session_state.get("review-filter-hide-auto-approved") and triage_status == "auto_approved_by_ai":
            continue
        if st.session_state.get("review-filter-ai-reject") and triage_status != "ai_recommend_reject":
            continue
        if st.session_state.get("review-filter-ai-correct") and triage_status != "ai_recommend_correct":
            continue
        if st.session_state.get("review-filter-ai-not-enough") and triage_status != "ai_not_enough_evidence":
            continue
        if st.session_state.get("review-filter-ai-human") and triage_status not in {"ai_needs_human_review", "ai_not_enough_evidence", "ai_invalid_output"}:
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: _ai_sort_key(row, latest.get(int(row["id"]))))


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


def _ai_result_html(result: dict) -> str:
    decision = str(result.get("aiDecision") or "")
    triage_status = _ai_triage_status({}, result)
    tone = AI_TRIAGE_TONES.get(triage_status, AI_DECISION_TONES.get(decision, "gray"))
    warnings = result.get("warnings") or []
    warning_text = "；".join(str(item) for item in warnings if item)
    if result.get("hallucinationRisk") and "hallucination_risk" not in warning_text:
        warning_text = (warning_text + "；" if warning_text else "") + "hallucination_risk"
    match_text = (
        f"证据 {AI_MATCH_LABELS.get(str(result.get('evidenceMatch')), result.get('evidenceMatch'))}"
        f" · 期间 {AI_MATCH_LABELS.get(str(result.get('periodMatch')), result.get('periodMatch'))}"
        f" · 单位 {AI_MATCH_LABELS.get(str(result.get('unitMatch')), result.get('unitMatch'))}"
    )
    corrected = ""
    if result.get("correctedValue") is not None:
        corrected = f"<span>建议修正：{escape(str(result.get('correctedValue')))} {escape(str(result.get('correctedUnit') or ''))} {escape(str(result.get('correctedPeriod') or ''))}</span>"
    return (
        "<div class='ai-review-panel'>"
        f"{_badge(AI_TRIAGE_LABELS.get(triage_status, triage_status or 'AI预审'), tone)}"
        f"{_badge(AI_DECISION_LABELS.get(decision, decision), tone)}"
        f"<strong>AI置信度 {float(result.get('confidenceScore') or 0):.0%}</strong>"
        f"<span>provider=qwen · model={escape(str(result.get('model') or 'qwen'))}</span>"
        f"<span>{escape(match_text)}</span>"
        f"<span>{'幻觉风险：是' if result.get('hallucinationRisk') else '幻觉风险：否'}</span>"
        f"{corrected}"
        f"<p>{escape(str(result.get('explanationZh') or ''))}</p>"
        f"<blockquote>{escape(str(result.get('evidenceQuote') or ''))}</blockquote>"
        f"<em>{escape(warning_text)}</em>"
        "</div>"
    )


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


def _render_sync_controls(store: ReviewQueueStore) -> None:
    with st.container(border=True):
        st.markdown(
            """
            <div class="review-command-head">
              <div>
                <strong>复核工作台</strong>
                <span>先同步观察池缺口，再用 Qwen 做证据预审；不会批量抓 SEC / IR。</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        cols = st.columns([1.35, 0.95, 0.95, 0.95, 2.0], vertical_alignment="center")
        if cols[0].button("同步观察池复核队列", width="stretch", key="review-sync-watchlist-v2"):
            result = ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist(load_watchlist())
            st.session_state["review_queue_sync_result"] = result
            st.toast(f"已同步 {len(result.symbols)} 只股票")
            st.rerun()
        st.session_state["review-filter-only-extracted"] = cols[1].checkbox("有值待确认", key="review-only-extracted-v2")
        st.session_state["review-filter-only-needs-data"] = cols[2].checkbox("需要补齐", key="review-only-needs-data-v2")
        st.session_state["review-filter-affects-scoring"] = cols[3].checkbox("影响评分", key="review-only-affects-scoring-v2")
        result = st.session_state.get("review_queue_sync_result")
        if result:
            cols[4].caption(f"上次同步：{len(result.symbols)}只股票 · {result.total}项")
        else:
            cols[4].caption("同步只读取本地缓存和评分缺口，不会发起全市场抓取。")


def _render_summary(summary: dict, ai_summary: dict | None = None) -> None:
    primary_cards = [
        ("涉及股票", summary.get("symbols", 0), "blue"),
        ("待确认", summary.get("pending_review", 0), "yellow"),
        ("需要补齐", summary.get("needs_data", 0), "orange"),
        ("影响评分", summary.get("missing_kpi", 0) + summary.get("manual_override_needed", 0), "red"),
        ("已确认", summary.get("approved", 0), "green"),
        ("已驳回", summary.get("rejected", 0), "gray"),
    ]
    ai_summary = ai_summary or {}
    triage_cards = [
        ("AI已预审", summary.get("ai_reviewed", ai_summary.get("total", 0)), "blue"),
        ("AI自动确认", summary.get("auto_approved_by_ai", ai_summary.get("auto_approved_by_ai", 0)), "green"),
        ("建议修正", summary.get("ai_recommend_correct", ai_summary.get("ai_recommend_correct", 0)), "orange"),
        ("建议驳回", summary.get("ai_recommend_reject", ai_summary.get("ai_recommend_reject", 0)), "red"),
        ("证据不足", summary.get("ai_not_enough_evidence", ai_summary.get("ai_not_enough_evidence", 0)), "gray"),
        ("人工判断", summary.get("ai_needs_human_review", ai_summary.get("ai_needs_human_review", 0)), "yellow"),
    ]
    primary_html = "".join(
        f'<div class="review-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
        for label, value, tone in primary_cards
    )
    triage_html = "".join(
        f'<div class="review-ai-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
        for label, value, tone in triage_cards
    )
    st.markdown(
        f"""
        <section class="review-overview-panel">
          <div class="review-overview-title">
            <strong>复核状态总览</strong>
            <span>优先处理 AI 建议驳回、建议修正和证据不足项</span>
          </div>
          <div class="review-summary-strip">{primary_html}</div>
          <div class="review-ai-summary-strip">{triage_html}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _cn_status(value: object) -> str:
    return {
        "pending_review": "待确认",
        "needs_data": "需要补齐",
        "approved": "已确认",
        "rejected": "已驳回",
        "manually_corrected": "人工修正",
        "stale": "已过期",
    }.get(str(value or ""), str(value or "N/A"))


def _cn_item_type(value: object) -> str:
    return {
        "extracted_value": "自动抽取值",
        "missing_kpi": "缺失关键KPI",
        "derived_low_confidence": "低置信度规则推导",
        "qualitative_risk": "定性风险复核",
        "analyst_estimate_needed": "需要分析师预期",
        "manual_override_needed": "建议人工补充",
    }.get(str(value or ""), str(value or "N/A"))


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


def _render_sync_controls(store: ReviewQueueStore, rows: list[dict] | None = None, filters: dict | None = None) -> None:
    ai_store = AIReviewStore(store.path)
    rows = rows if rows is not None else store.list_items()
    qwen_assistant = QwenReviewService(queue_store=store, ai_store=ai_store)
    autopilot = ReviewAutopilot(queue_store=store)
    default_rows = _default_action_rows(rows, ai_store)
    with st.container(border=True):
        st.markdown(
            """
            <div class="review-command-head">
              <div>
                <strong>自动化处理中心</strong>
                <span>默认自动化优先：同步队列、补齐数据、Qwen证据复核、安全确认、低优先级归档。</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("确认 = 允许该数据进入评分。确认不等于买入建议。已确认数据可在“最近确认”中撤销。")
        cols = st.columns([1.65, 0.9, 0.95, 2.4], vertical_alignment="center")
        if cols[0].button("一键自动处理当前筛选结果", width="stretch", key="review-autopilot-current-filter"):
            with st.status("正在自动处理当前筛选结果...", expanded=True) as status:
                st.write("1. 同步复核队列")
                st.write("2. 自动补齐缺失数据")
                st.write("3. 重建复核队列")
                st.write("4. Qwen复核有证据的数据")
                st.write("5. 自动确认安全项 / 自动归档低优先级项")
                result = autopilot.run_review_autopilot(filters or {})
                status.update(label="自动处理完成", state="complete")
            _show_autopilot_result(result)
            st.rerun()
        if cols[1].button("查看处理日志", width="stretch", key="review-show-automation-log"):
            st.session_state["show_review_automation_logs"] = not st.session_state.get("show_review_automation_logs", False)
        with cols[2].popover("更多 ▾", use_container_width=True):
            if st.button("仅同步复核队列", key="review-sync-watchlist-refactor", width="stretch"):
                result = ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist(load_watchlist())
                st.session_state["review_queue_sync_result"] = result
                _show_operation_result("同步复核队列", len(load_watchlist()), result.total, result.skipped, 0, [])
                store.log_operation("sync_review_queue", filters, len(load_watchlist()), result.total, result.skipped, 0, [])
                st.toast(f"已同步 {len(result.symbols)} 只股票，生成/更新 {result.total} 项")
                st.rerun()
            if st.button("仅运行数据补全", key="review-autofill-only", width="stretch"):
                result = autopilot.run_auto_fill_only(rows)
                _show_autopilot_result(result)
                st.rerun()
            if st.button("仅运行 Qwen 证据复核", key="qwen-review-filtered-refactor", width="stretch"):
                result = qwen_assistant.review_rows(rows)
                _show_ai_run_result(result)
                st.rerun()
            if st.button("仅重新计算评分", key="review-rebuild-score-only", width="stretch"):
                result = ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist(
                    [filters["symbol"]] if filters and filters.get("symbol") else load_watchlist()
                )
                _show_operation_result("重新计算评分", len(result.symbols), result.total, result.skipped, 0, [])
                st.rerun()
            if st.button("批量接受AI自动确认项", key="review-batch-auto-refactor", width="stretch"):
                changes = store.batch_accept_ai_auto_approved([int(row["id"]) for row in rows])
                _show_operation_result("批量接受AI自动确认项", len(rows), len(changes), len(rows) - len(changes), 0, [])
                store.log_operation("batch_accept_ai_auto_approved", filters, len(rows), len(changes), len(rows) - len(changes), 0, [])
                if not changes:
                    st.warning("没有可处理项目：当前筛选条件下，0 条符合此操作。")
                else:
                    st.toast(f"已处理 {len(changes)} 条AI自动确认项")
                st.rerun()
            if st.button("批量隐藏AI自动确认项", key="review-hide-auto-refactor", width="stretch"):
                count = store.hide_auto_approved_items([int(row["id"]) for row in rows])
                _show_operation_result("批量隐藏AI自动确认项", len(rows), count, len(rows) - count, 0, [])
                store.log_operation("batch_hide_ai_auto_approved", filters, len(rows), count, len(rows) - count, 0, [])
                st.toast(f"已隐藏 {count} 条AI自动确认项" if count else "没有可隐藏的AI自动确认项")
                st.rerun()
            if st.button("批量归档低优先级项", key="review-archive-low-priority", width="stretch"):
                count = store.batch_archive_low_priority([int(row["id"]) for row in rows])
                _show_operation_result("批量归档低优先级项", len(rows), count, len(rows) - count, 0, [])
                store.log_operation("batch_archive_low_priority", filters, len(rows), count, len(rows) - count, 0, [])
                st.toast(f"已归档 {count} 条低优先级项" if count else "没有可归档的低优先级项")
                st.rerun()
            if st.button("清理已归档项", key="review-clean-archived", width="stretch"):
                count = store.hide_auto_approved_items([int(row["id"]) for row in rows])
                _show_operation_result("清理已归档项", len(rows), count, len(rows) - count, 0, [])
                st.rerun()
            if st.button("批量进入人工复核", key="review-human-batch-refactor", width="stretch"):
                count = store.mark_ai_needs_human([int(row["id"]) for row in default_rows])
                _show_operation_result("批量进入人工复核", len(default_rows), count, len(default_rows) - count, 0, [])
                store.log_operation("batch_mark_human_review", filters, len(default_rows), count, len(default_rows) - count, 0, [])
                st.toast(f"已标记 {count} 条为人工复核" if count else "没有可标记的项目")
                st.rerun()
        stats = qwen_review_efficiency_stats(rows)
        effectiveness = automation_effectiveness(rows)
        cols[3].caption(
            f"当前筛选 {effectiveness['total']} 条；适合证据复核 {stats['eligibleForQwenCount']} 条；"
            f"AI已自动处理 {effectiveness['automationRate']:.0%}，剩余 {effectiveness['humanRemaining']} 条需要人工判断。"
        )
        _render_last_autopilot_result()
        _render_last_qwen_result()
        _render_last_operation_result(store)
        if st.session_state.get("show_review_automation_logs"):
            _render_automation_logs(store)


def _render_summary(summary: dict, ai_summary: dict | None = None) -> None:
    cards = [
        ("涉及股票", summary.get("symbols", 0), "blue"),
        ("待确认", summary.get("pending_review", 0), "yellow"),
        ("需要补齐", summary.get("needs_data", 0), "orange"),
        ("建议修正", summary.get("ai_recommend_correct", 0), "orange"),
        ("建议驳回", summary.get("ai_recommend_reject", 0), "red"),
        ("自动归档", summary.get("ai_auto_archived", 0), "gray"),
        ("自动确认", summary.get("auto_approved_by_ai", 0), "green"),
    ]
    html = "".join(
        f'<div class="review-summary-card tone-{tone}"><span>{escape(label)}</span><strong>{int(value or 0)}</strong></div>'
        for label, value, tone in cards
    )
    st.markdown(
        f"""
        <section class="review-overview-panel">
          <div class="review-overview-title">
            <strong>复核状态总览</strong>
            <span>默认只展示需要你处理的异常项，AI自动确认项已隐藏。</span>
          </div>
          <div class="review-summary-strip">{html}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_filters(store: ReviewQueueStore) -> dict:
    rows = store.list_items()
    symbols = sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")})
    metric_keys = sorted({str(row.get("metricKey") or "") for row in rows if row.get("metricKey")})
    source_types = sorted({str(row.get("sourceType") or "") for row in rows if row.get("sourceType")})
    tabs = ["待我处理", "最近确认", "需要补证据", "自动补齐失败", "AI建议修正", "AI建议驳回", "证据不足", "自动归档", "自动确认", "全部"]
    st.session_state["review-active-tab"] = st.radio(
        "复核视图",
        tabs,
        index=tabs.index(st.session_state.get("review-active-tab", "待我处理"))
        if st.session_state.get("review-active-tab", "待我处理") in tabs
        else 0,
        horizontal=True,
        key="review-active-tab-radio",
    )
    with st.expander("筛选", expanded=False):
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


def _render_ai_controls(store: ReviewQueueStore, ai_store: AIReviewStore, filters: dict, rows: list[dict]) -> None:
    stats = qwen_review_efficiency_stats(rows)
    st.markdown(
        f"""
        <div class="qwen-efficiency">
          <strong>Qwen有效率</strong>
          <span>适合复核 {stats['eligibleForQwenCount']} · 已预审 {stats['qwenReviewedCount']} · 自动确认 {stats['autoApprovedCount']} · 仍需人工 {stats['humanRequiredCount']} · 不适合AI {stats['skippedAsNotSuitableCount']} · 证据不足 {stats['notEnoughEvidenceCount']}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _apply_ai_filters(rows: list[dict], ai_store: AIReviewStore) -> list[dict]:
    latest = ai_store.latest_for_items([int(row["id"]) for row in rows])
    tab = st.session_state.get("review-active-tab-radio") or st.session_state.get("review-active-tab", "待我处理")
    st.session_state["review-active-tab"] = tab
    filtered: list[dict] = []
    for row in rows:
        triage = _ai_triage_status(row, latest.get(int(row["id"])))
        status = str(row.get("reviewStatus") or "")
        item_type = str(row.get("itemType") or "")
        if tab == "待我处理":
            if status in {"approved", "rejected", "manually_corrected", "auto_archived"}:
                continue
            if triage in {"auto_approved_by_ai", "ai_auto_archived"} or row.get("hiddenByDefault"):
                continue
            if status == "needs_evidence" and not _is_high_impact_review_item(row):
                continue
            if item_type == "extracted_value" and not triage:
                continue
            if item_type in {"calculated", "not_applicable"}:
                continue
        elif tab == "需要补证据" and status != "needs_evidence":
            continue
        elif tab == "自动补齐失败" and str(row.get("autoFillStatus") or "") != "failed":
            continue
        elif tab == "AI建议修正" and triage != "ai_recommend_correct":
            continue
        elif tab == "AI建议驳回" and triage != "ai_recommend_reject":
            continue
        elif tab == "证据不足" and triage != "ai_not_enough_evidence":
            continue
        elif tab == "自动归档" and triage != "ai_auto_archived":
            continue
        elif tab == "自动确认" and triage != "auto_approved_by_ai":
            continue
        elif tab == "最近确认" and status not in {"approved", "manually_corrected"} and triage != "auto_approved_by_ai":
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: _ai_sort_key(row, latest.get(int(row["id"]))))


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
    affects = {part.strip() for part in str(row.get("affects") or "").split(",") if part.strip()}
    return metric_key in HIGH_IMPACT_REVIEW_METRICS or bool(affects & {"Action", "Position", "maxPosition"})


def _review_primary_action(row: dict) -> dict:
    status = str(row.get("reviewStatus") or "")
    triage = str(row.get("aiTriageStatus") or "")
    item_type = str(row.get("itemType") or "")

    if status == "auto_archived" or triage == "ai_auto_archived":
        return {"key": "undo_archive", "label": "撤销归档"}
    if status == "manually_corrected":
        return {"key": "undo_manual_correct", "label": "撤销人工修正"}
    if status == "rejected":
        return {"key": "undo_reject", "label": "撤销驳回"}
    if status == "approved" or triage == "auto_approved_by_ai":
        label = "撤销AI自动确认" if triage == "auto_approved_by_ai" else "撤销确认"
        return {"key": "undo_approval", "label": label}
    if str(row.get("freshnessStatus") or "") == "historical_value":
        return {"key": "keep_historical", "label": "保留为历史"}
    if _has_complete_extracted_value_evidence(row):
        return {"key": "approve", "label": "确认数据"}
    if status == "needs_evidence" or item_type in {"evidence_missing_extracted_value", "extracted_value"}:
        return {"key": "backfill_evidence", "label": "重新抓证据"}
    if item_type == "missing_kpi":
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
    if action_key == "approve":
        guard_key = f"review-confirm-guard-{metric_id}"
        if _requires_high_impact_confirmation(row) and not st.session_state.get(guard_key):
            st.session_state[guard_key] = True
            st.warning("该数据会影响评分。确认后将进入评分系统。请再次点击“确认数据”继续。")
            return
        store.update_review_status(metric_id, "approved")
        st.session_state.pop(guard_key, None)
        st.toast("数据已确认，可进入评分")
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
        st.toast("已撤销确认，需重新计算评分")
    elif action_key == "undo_manual_correct":
        store.undo_review_status(metric_id, "pending_review", "undo_manual_correct")
        st.toast("已撤销人工修正，需重新计算评分")
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


def _render_metric_row(store: ReviewQueueStore, row: dict, ai_result: dict | None = None) -> None:
    metric_id = int(row["id"])
    status = str(row.get("reviewStatus") or "pending_review")
    confidence = str(row.get("confidence") or "")
    triage = _ai_triage_status(row, ai_result)
    eligible, reason = qwen_review_eligibility(row)
    value_text = _format_value(row.get("value"), row.get("unit"))
    with st.container(border=True):
        cols = st.columns([0.58, 1.45, 0.85, 0.86, 0.82, 0.86, 1.25, 1.0], vertical_alignment="center")
        cols[0].markdown(f"**{escape(str(row.get('symbol') or ''))}**")
        cols[1].markdown(
            f"<div class='metric-title'>{escape(metric_label(row.get('displayName') or row.get('metricKey') or 'N/A'))}</div>"
            f"<div class='metric-sub'>{escape(model_type_label(row.get('modelType')))}</div>",
            unsafe_allow_html=True,
        )
        cols[2].markdown(f"<span class='metric-value'>{escape(value_text)}</span>", unsafe_allow_html=True)
        cols[3].markdown(_badge(_cn_item_type(row.get("itemType")), ITEM_TYPE_TONES.get(str(row.get("itemType") or ""), "gray")), unsafe_allow_html=True)
        cols[4].markdown(_badge(source_type_label(row.get("sourceType") or "N/A"), "gray"), unsafe_allow_html=True)
        cols[5].markdown(_badge(confidence_label(confidence or "N/A"), _confidence_tone(confidence)), unsafe_allow_html=True)
        cols[6].markdown(_compact_ai_badge(ai_result, triage, eligible, reason), unsafe_allow_html=True)
        with cols[7]:
            action_cols = st.columns([0.62, 0.38])
            primary_action = _review_primary_action(row)
            if action_cols[0].button(primary_action["label"], key=f"review-primary-{primary_action['key']}-{metric_id}", width="stretch"):
                _run_review_action(store, row, primary_action["key"], ai_result)
                st.rerun()
            with action_cols[1].popover("操作 ▾", use_container_width=True):
                item_type = str(row.get("itemType") or "")
                if str(row.get("freshnessStatus") or "") == "historical_value":
                    if st.button("设为当前值", key=f"review-set-current-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "set_current_value", ai_result)
                        st.rerun()
                    if st.button("归档历史值", key=f"review-archive-history-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "keep_historical", ai_result)
                        st.rerun()
                if status in {"approved", "manually_corrected"} or triage == "auto_approved_by_ai":
                    st.caption("撤销后如何处理这条数据？")
                    if st.button("回到待复核", key=f"review-undo-pending-{metric_id}", width="stretch"):
                        store.undo_review_status(metric_id, "pending_review", "undo_to_pending_review")
                        st.toast("已撤销，回到待复核，需重新计算评分")
                        st.rerun()
                    if st.button("标记为需要补证据", key=f"review-undo-evidence-{metric_id}", width="stretch"):
                        store.undo_review_status(metric_id, "needs_evidence", "undo_to_needs_evidence")
                        st.toast("已撤销，并标记为需要补证据")
                        st.rerun()
                    if st.button("直接驳回", key=f"review-undo-reject-{metric_id}", width="stretch"):
                        store.update_review_status(metric_id, "rejected", "undo_then_reject")
                        st.toast("已撤销确认并驳回")
                        st.rerun()
                    if st.button("手动修正", key=f"review-undo-correct-{metric_id}", width="stretch"):
                        store.undo_review_status(metric_id, "pending_review", "undo_for_manual_correction")
                        _run_review_action(store, row, "manual_fill", ai_result)
                        st.rerun()
                elif _has_complete_extracted_value_evidence(row):
                    if st.button("驳回", key=f"review-reject-menu-{metric_id}", disabled=status == "rejected", width="stretch"):
                        _run_review_action(store, row, "reject", ai_result)
                        st.rerun()
                    if st.button("修正", key=f"review-correct-menu-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "needs_more_source", ai_result)
                        st.rerun()
                elif status == "needs_evidence" or item_type == "evidence_missing_extracted_value":
                    if st.button("标记无法获取", key=f"review-unavailable-evidence-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "mark_unavailable", ai_result)
                        st.rerun()
                    if st.button("手动确认来源", key=f"review-manual-source-{metric_id}", width="stretch"):
                        store.update_review_status(metric_id, "pending_review", "manual_source_confirmed")
                        st.toast("已转入待确认，请谨慎复核")
                        st.rerun()
                    if st.button("归档", key=f"review-archive-needs-evidence-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                elif item_type == "missing_kpi":
                    if st.button("手动补充", key=f"review-manual-fill-missing-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "manual_fill", ai_result)
                        st.rerun()
                    if st.button("归档", key=f"review-archive-missing-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                elif item_type == "derived_low_confidence":
                    if st.button("归档", key=f"review-archive-derived-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                    if st.button("调低权重", key=f"review-lower-weight-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "lower_weight", ai_result)
                        st.rerun()
                elif item_type == "qualitative_risk":
                    if st.button("归档", key=f"review-archive-risk-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                    if st.button("加入风险备注", key=f"review-risk-note-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "risk_note", ai_result)
                        st.rerun()
                elif item_type == "analyst_estimate_needed":
                    if st.button("使用TTM代理", key=f"review-ttm-proxy-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "use_ttm_proxy", ai_result)
                        st.rerun()
                    if st.button("归档", key=f"review-archive-estimate-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                elif item_type == "manual_override_needed":
                    if st.button("标记无法获取", key=f"review-unavailable-manual-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "mark_unavailable", ai_result)
                        st.rerun()
                    if st.button("归档", key=f"review-archive-manual-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                else:
                    if st.button("归档", key=f"review-archive-other-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "archive", ai_result)
                        st.rerun()
                if status in {"approved", "manually_corrected"} or triage == "auto_approved_by_ai":
                    with st.expander("查看评分影响", expanded=False):
                        affects = str(row.get("affects") or "ConfidenceOnly")
                        score_status = store.get_score_status(str(row.get("symbol") or ""))
                        st.caption(
                            f"该指标属于 {affects} 输入。撤销后将从评分输入中排除，并把 {row.get('symbol')} 标记为评分过期。"
                        )
                        st.caption(f"当前评分状态：{score_status.get('scoreStatus') or 'fresh'}")
                        if st.button("重新计算该股票", key=f"review-recompute-symbol-{metric_id}", width="stretch"):
                            symbol = str(row.get("symbol") or "").upper()
                            if symbol:
                                ReviewQueueBuilder(queue_store=store).build_review_queue_for_watchlist([symbol])
                                store.mark_score_fresh(symbol, f"manual-recompute-{metric_id}")
                                st.toast(f"{symbol} 已重新计算并标记为最新")
                                st.rerun()
                if ai_result and triage == "ai_recommend_correct":
                    if st.button("接受AI修正", key=f"review-accept-correct-menu-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "accept_ai_correct", ai_result)
                        st.rerun()
                if ai_result and triage == "ai_recommend_reject":
                    if st.button("接受AI驳回", key=f"review-accept-reject-menu-{metric_id}", width="stretch"):
                        _run_review_action(store, row, "accept_ai_reject", ai_result)
                        st.rerun()
                if row.get("sourceUrl"):
                    st.link_button("打开来源", str(row.get("sourceUrl")), width="stretch")
        st.caption(_recommended_review_action(row, eligible, reason))
        with st.expander("展开证据与AI解释", expanded=False):
            evidence_text = str(row.get("evidenceText") or "").strip()
            system_reason = str(row.get("systemReason") or row.get("explanation") or "").strip()
            if row.get("aiTriageStatus") == "extraction_rejected_by_rule":
                st.markdown(f"**规则已拦截**：{escape(system_reason or '该抽取候选未通过指标校验。')}")
            elif evidence_text:
                st.markdown(f"**原文片段**：{escape(evidence_text)}")
            else:
                st.markdown(f"**系统说明**：{escape(system_reason or '暂无真实原文证据。')}")
            if ai_result:
                st.markdown(_ai_result_html(ai_result), unsafe_allow_html=True)
            else:
                st.caption(QWEN_NOT_SUITABLE_REASON if not eligible else "尚未进行Qwen证据复核")


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


def _show_automation_result(result) -> None:
    st.session_state["ai_automation_last_result"] = {
        "eligible": int(getattr(result, "eligible", 0) or 0),
        "processed": int(getattr(result, "processed", 0) or 0),
        "skipped": int(getattr(result, "skipped", 0) or 0),
        "failed": int(getattr(result, "failed", 0) or 0),
        "auto_approved": int(getattr(result, "auto_approved", 0) or 0),
        "auto_archived": int(getattr(result, "auto_archived", 0) or 0),
        "needs_human": int(getattr(result, "needs_human", 0) or 0),
        "qwen_reviewed": int(getattr(result, "qwen_reviewed", 0) or 0),
        "errors": list(getattr(result, "errors", None) or []),
        "message": str(getattr(result, "message", "") or ""),
    }
    if not getattr(result, "eligible", 0):
        st.warning("当前筛选结果中没有符合条件的项目。")
    elif getattr(result, "failed", 0):
        st.warning(f"AI自动分流完成，但有 {result.failed} 条失败。")
    else:
        st.toast(f"AI自动分流完成：处理 {result.processed} 条，自动归档 {result.auto_archived} 条")


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
    with st.expander("处理日志", expanded=True):
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
      .review-overview-panel {
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        background: #FFFFFF;
        padding: 12px;
        margin: 12px 0;
      }
      .review-overview-title {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 10px;
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
        gap: 8px;
        margin: 0 0 8px;
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
        height: 58px;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        background: #FFFFFF;
        padding: 9px 12px;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }
      .review-summary-card span {
        color: #6B7280;
        font-size: 12px;
        font-weight: 700;
      }
      .review-summary-card strong {
        color: #111827;
        font-size: 19px;
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
        font-size: 13px;
        font-weight: 800;
      }
      .metric-sub,
      .source-meta {
        color: #9CA3AF;
        font-size: 12px;
      }
      .metric-value {
        color: #111827;
        font-size: 13px;
        font-weight: 800;
        font-variant-numeric: tabular-nums;
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
        min-height: 24px;
        align-items: center;
        padding: 0 8px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 700;
        border: 1px solid transparent;
        white-space: nowrap;
      }
      .tone-green { background: #EAF8F0; color: #166534; border-color: #BBF7D0; }
      .tone-blue { background: #EFF6FF; color: #1D4ED8; border-color: #BFDBFE; }
      .tone-yellow { background: #FEFCE8; color: #854D0E; border-color: #FDE68A; }
      .tone-orange { background: #FFF7ED; color: #C2410C; border-color: #FDBA74; }
      .tone-red { background: #FEF2F2; color: #B91C1C; border-color: #FECACA; }
      .tone-gray { background: #F3F4F6; color: #4B5563; border-color: #E5E7EB; }
      @media (max-width: 1100px) {
        .review-summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .review-ai-summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .review-overview-title { display: block; }
      }
    </style>
    """
