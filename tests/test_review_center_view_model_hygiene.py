from __future__ import annotations

from data import review_center_view_model as view_model


def test_review_center_default_summaries_are_user_facing_chinese() -> None:
    assert view_model._reason_summary({}, False, False, False) == "该复核项需要人工分流。"
    assert view_model._reason_summary({}, True, True, False) == "该项会影响评分，但缺少可验证证据。"
    assert view_model._reason_summary({}, False, False, True) == "低优先级且不参与评分，可作为归档候选。"
    assert view_model._reason_summary({}, False, False, False, risk_observation=True) == "定性风险仅作为观察项记录，不作为数据确认任务。"
    assert view_model._evidence_summary({}) == "暂无可验证证据。"

    combined = " ".join(
        [
            view_model._reason_summary({}, False, False, False),
            view_model._reason_summary({}, True, True, False),
            view_model._evidence_summary({}),
        ]
    )
    for token in ("Review queue", "Scoring-impact", "No verifiable", "archive candidate"):
        assert token not in combined


def test_review_center_duplicate_summary_is_localized() -> None:
    representative = _row({"freshnessStatus": "active_current"})
    duplicate = _row({"freshnessStatus": view_model.HISTORICAL_FRESHNESS, "missingEvidence": True})

    view_model._attach_duplicate_candidates(representative, [duplicate])

    assert representative.item["duplicateSummary"] == "1 个重复/历史候选, 1 个历史值, 1 个弱证据"
    assert "duplicate/historical" not in representative.item["duplicateSummary"]
    assert "weak-evidence" not in representative.item["duplicateSummary"]


def test_review_center_treats_localized_missing_values_as_missing() -> None:
    for value in ("待补", "暂缺", "暂无"):
        assert view_model._present_review_value(value) is False


def _row(item: dict) -> view_model._ReviewCenterRow:
    return view_model._ReviewCenterRow(
        row=dict(item),
        item=dict(item),
        affects_scoring=False,
        has_explicit_evidence=False,
        missing_evidence=bool(item.get("missingEvidence")),
        active=True,
        handled=False,
        ai_correction=False,
        priority_score=0,
        confidence_score=0,
        source_score=0,
        has_value=False,
        risk_observation=False,
        handled_at="",
    )
