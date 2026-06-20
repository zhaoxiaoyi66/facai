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
    assert "当前评分状态：{score_status.get('scoreStatus') or 'fresh'}" not in impact_source
    assert "Quality" not in source
