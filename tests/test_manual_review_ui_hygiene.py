from __future__ import annotations

import inspect

from ui import manual_review


def test_manual_review_missing_placeholders_are_localized() -> None:
    source = inspect.getsource(manual_review)

    assert '"N/A"' not in source
    assert "'N/A'" not in source
    assert manual_review._review_display_text(None) == "待补"
    assert manual_review._review_display_text("N/A") == "待补"
    assert manual_review._review_display_text("none", "未记录") == "未记录"
    assert manual_review._review_has_display_text("N/A") is False
    assert manual_review._review_row_source_meta({}) == "待补 · 待补"
    assert manual_review._review_source_display({}) == "待补"


def test_manual_review_header_uses_chinese_kicker() -> None:
    source = inspect.getsource(manual_review.render)

    assert "DATA REVIEW" not in source
    assert "系统后验验证" in source


def test_manual_review_score_impact_copy_localizes_internal_status() -> None:
    source = inspect.getsource(manual_review._score_status_label)
    impact_source = inspect.getsource(manual_review._render_rows)

    assert manual_review._score_status_label("fresh") == "最新"
    assert manual_review._score_status_label("stale") == "需重算"
    assert manual_review._affects_label("Quality,Entry,ConfidenceOnly") == "质量 / 买点 / 置信度"
    assert manual_review._affects_label("Confidence Only,NEW_INTERNAL_SCOPE") == "置信度 / 解释"
    assert manual_review._score_status_label("NEW_SCORE_STATUS") == "最新"
    assert manual_review._confirmed_status_label("NEW_CONFIRM_STATUS") == "未归类"
    assert manual_review._auto_archive_reason_label("NEW_ARCHIVE_REASON") == "低优先级不影响评分"
    assert manual_review._review_vm_action_label("NEW_REVIEW_ACTION") == "待复核"
    assert manual_review._review_system_reason_text("NEW_SYSTEM_REASON") == "暂无系统说明。"
    assert manual_review._review_system_reason_text("人工系统说明") == "人工系统说明"
    assert "当前评分状态：{score_status.get('scoreStatus') or 'fresh'}" not in impact_source
    assert "Quality" not in source


def test_manual_review_qwen_not_configured_copy_hides_env_key_name() -> None:
    source = inspect.getsource(manual_review._render_last_qwen_result)

    assert "Qwen 未配置：请先在本地环境里配置 Qwen 接口密钥。" in source
    assert "QWEN_API_KEY" not in source
    assert ".env" not in source
